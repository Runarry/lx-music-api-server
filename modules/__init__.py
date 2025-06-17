# ----------------------------------------
# - mode: python -
# - author: helloplhm-qwq -
# - name: __init__.py -
# - project: lx-music-api-server -
# - license: MIT -
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

from common.exceptions import FailedException
from common.utils import require
from common import log
from common import config
import os
import glob
import asyncio
from common import variable
import mutagen
from mutagen.id3 import ID3, TIT2, TPE1, TALB, USLT, APIC
from mutagen.flac import FLAC, Picture
import ujson as json
import asyncio.subprocess as asp
from common import utils
import sys  # 已存在? modules顶部有traceback, time, but not sys. ensure imported
import collections
import aiofiles

# 导入外部脚本模块
from . import external_script

# 从.引入的包并没有在代码中直接使用，但是是用require在请求时进行引入的，不要动
from . import kw
from . import mg
from . import kg
from . import tx
from . import wy
import traceback
import time

logger = log.log("api_handler")

sourceExpirationTime = {
    "tx": {
        "expire": True,
        "time": 80400,  # 不知道tx为什么要取一个这么不对劲的数字当过期时长
    },
    "kg": {
        "expire": True,
        "time": 24 * 60 * 60,  # 24 hours
    },
    "kw": {"expire": True, "time": 60 * 60},  # 60 minutes
    "wy": {
        "expire": True,
        "time": 20 * 60,  # 20 minutes
    },
    "mg": {
        "expire": False,
        "time": 0,
    },
}

# 初始化远端音频缓存目录
_remote_cache_dir = config.read_config("common.remote_cache.path") or "./cache_audio"
if not os.path.exists(_remote_cache_dir):
    try:
        os.makedirs(_remote_cache_dir, exist_ok=True)
    except Exception:
        logger.error(f"无法创建远端音频缓存目录: {_remote_cache_dir}")

# ---------------- Cache index to avoid per-request disk scanning ----------------
# 结构: _cache_index[(source, song_id)][quality] = filepath
_cache_index: dict[tuple[str, str], dict[str, str]] = collections.defaultdict(dict)

def _init_cache_index():
    """扫描远端缓存目录并构建索引，在进程启动时调用一次。"""
    try:
        for fname in os.listdir(_remote_cache_dir):
            # 排除封面/其它非音频文件
            if fname.endswith('_cover.jpg') or fname.startswith('.'):
                continue
            name_no_ext, _ = os.path.splitext(fname)
            parts = name_no_ext.split('_')
            if len(parts) < 3:
                # 文件名不符合 <source>_<songId>_<quality> 规则，跳过
                continue
            source = parts[0]
            quality = parts[-1]
            # song_id 可能包含 '_'，这里重新拼接中间段
            song_id = '_'.join(parts[1:-1])
            _cache_index[(source, song_id)][quality] = os.path.join(_remote_cache_dir, fname)
    except FileNotFoundError:
        # 目录尚不存在
        pass

# 在模块导入时立即构建索引
_init_cache_index()

# 公共方法: 增量更新索引
def _update_cache_index(source: str, song_id: str, quality: str, filepath: str):
    _cache_index[(source, song_id)][quality] = filepath

# ---------------- Metadata in-flight set to avoid duplicate tasks ----------------
_inflight_meta: set[tuple[str, str]] = set()
_inflight_meta_lock = asyncio.Lock()

async def url(source, songId, quality, query={}):
    # ❗ 为保证酷狗(Kugou)源的歌曲 ID 与磁盘/缓存中的命名一致，统一转为小写。
    #   之前的实现是在本地文件检查之后才转换，导致相同歌曲无法命中缓存。
    if source == "kg":
        songId = songId.lower()

    # —— 优先处理客户端内嵌的 info / lyric 缓存 ——
    try:
        if query:
            # decode helper
            def _decode_b64url(data_str: str):
                """Return python object from base64url-encoded json string."""
                from common import utils
                # 恢复标准 base64
                padding = '=' * (-len(data_str) % 4)
                data_str_std = data_str.replace('-', '+').replace('_', '/') + padding
                raw_bytes = utils.createBase64Decode(data_str_std)
                try:
                    return json.loads(raw_bytes.decode('utf-8'))
                except Exception:
                    # 若解析失败返回 None
                    return None
            # 信息缓存
            if 'info' in query and query['info']:
                info_obj = _decode_b64url(query['info'])
                if isinstance(info_obj, dict):
                    config.updateCache(
                        'info', f"{source}_{songId}",
                        {"expire": False, "time": 0, "data": info_obj}
                    )
                    logger.debug(f"inline info cached: {source}_{songId}")
            # 歌词缓存
            if 'lyric' in query and query['lyric']:
                lyric_obj = _decode_b64url(query['lyric'])
                if lyric_obj:
                    expire_time = 86400 * 3
                    expire_at = int(time.time() + expire_time)
                    config.updateCache(
                        'lyric', f"{source}_{songId}",
                        {"expire": True, "time": expire_at, "data": lyric_obj},
                        expire_time,
                    )
                    logger.debug(f"inline lyric cached: {source}_{songId}")
    except Exception:
        logger.debug('decode inline metadata failed\n' + traceback.format_exc())

    if not quality:
        return {
            "code": 2,
            "msg": '需要参数"quality"',
            "data": None,
        }

    # —— 本地音频缓存预检查 ——
    cached_path = _find_cached_file(source, songId, quality)
    if cached_path:
        logger.debug(f"命中本地音频缓存: {cached_path}")
        # 缓存虽已命中，但仍异步确认歌词/信息/封面是否存在
        asyncio.create_task(_ensure_metadata_cached(source, songId))
        return {
            "code": 0,
            "msg": "success",
            "data": f"/cache/{os.path.basename(cached_path)}",
            "extra": {
                "cache": True,
                "quality": {
                    "target": quality,
                    "result": quality,
                },
                "localfile": True,
            },
        }

    try:
        cache = config.getCache("urls", f"{source}_{songId}_{quality}")
        if cache:
            logger.debug(f'使用缓存的{source}_{songId}_{quality}数据，URL：{cache["url"]}')
            # 缓存虽已命中，但仍异步确认歌词/信息/封面是否存在
            asyncio.create_task(_ensure_metadata_cached(source, songId))
            return {
                "code": 0,
                "msg": "success",
                "data": cache["url"],
                "extra": {
                    "cache": True,
                    "quality": {
                        "target": quality,
                        "result": quality,
                    },
                    "expire": {
                        # 在更新缓存的时候把有效期的75%作为链接可用时长，现在加回来
                        "time": (
                            int(cache["time"] + (sourceExpirationTime[source]["time"] * 0.25))
                            if cache["expire"]
                            else None
                        ),
                        "canExpire": cache["expire"],
                    },
                },
            }
    except:
        logger.error(traceback.format_exc())
    try:
        func = require("modules." + source + ".url")
    except:
        return {
            "code": 1,
            "msg": "未知的源或不支持的方法",
            "data": None,
        }
    try:
        result = await func(songId, quality)
        logger.info(f'获取{source}_{songId}_{quality}成功，URL：{result["url"]}')

        # —— 下载音频以供下次使用 ——
        try:
            if config.read_config("common.remote_cache.enable") is not False:
                # 取文件扩展名
                _ext = os.path.splitext(result["url"].split("?")[0])[1]
                if _ext == "":
                    _ext = ".mp3"
                cache_filename = f"{source}_{songId}_{result['quality']}{_ext}"
                cache_filepath = os.path.join(_remote_cache_dir, cache_filename)
                if not os.path.exists(cache_filepath):
                    asyncio.create_task(_download_audio_to_cache(result["url"], cache_filepath, source, songId))
                # 并行缓存歌曲信息/封面/歌词
                asyncio.create_task(_ensure_metadata_cached(source, songId))
        except Exception:
            logger.warning("音频缓存调度失败\n" + traceback.format_exc())

        canExpire = sourceExpirationTime[source]["expire"]
        expireTime = int(sourceExpirationTime[source]["time"] * 0.75)
        expireAt = int(time.time() + expireTime)
        config.updateCache(
            "urls",
            f"{source}_{songId}_{quality}",
            {
                "expire": canExpire,
                # 取有效期的75%作为链接可用时长
                "time": expireAt,
                "url": result["url"],
            },
            expireTime if canExpire else None,
        )
        logger.debug(f'缓存已更新：{source}_{songId}_{quality}, URL：{result["url"]}, expire: {expireTime}')

        # 缓存虽已命中，但仍异步确认歌词/信息/封面是否存在
        asyncio.create_task(_ensure_metadata_cached(source, songId))

        # 写入内存索引，供后续请求直接命中
        try:
            name_no_ext = os.path.splitext(os.path.basename(cache_filepath))[0]
            quality_inferred = name_no_ext.split('_')[-1]
            _update_cache_index(source, songId, quality_inferred, cache_filepath)
        except Exception:
            pass

        return {
            "code": 0,
            "msg": "success",
            "data": result["url"],
            "extra": {
                "cache": False,
                "quality": {
                    "target": quality,
                    "result": result["quality"],
                },
                "expire": {
                    "time": expireAt if canExpire else None,
                    "canExpire": canExpire,
                },
                "localfile": False,
            },
        }
    except FailedException as e:
        logger.info(f"获取{source}_{songId}_{quality}失败，尝试 external script，原因：" + e.args[0])

        # —— external script fallback ——
        ext_res = await external_script.try_external_script(source, songId, quality)
        if ext_res:
            logger.info(f"external script 获取成功: {ext_res['url']}")

            # 后台缓存音频
            try:
                if config.read_config('common.remote_cache.enable') is not False:
                    _ext = os.path.splitext(ext_res['url'].split('?')[0])[1] or '.mp3'
                    cache_filename = f"{source}_{songId}_{ext_res['quality']}{_ext}"
                    cache_filepath = os.path.join(_remote_cache_dir, cache_filename)
                    if not os.path.exists(cache_filepath):
                        # 对 external script 返回的音频进行同步缓存，确保函数返回前已完成保存
                        await _download_audio_to_cache(ext_res['url'], cache_filepath, source, songId)
            except Exception:
                logger.warning('音频缓存调度失败(来自 external script)\n' + traceback.format_exc())

            # 写入 URL 缓存（不过期）
            config.updateCache('urls', f"{source}_{songId}_{quality}", {'expire': False, 'time': 0, 'url': ext_res['url']})

            asyncio.create_task(_ensure_metadata_cached(source, songId))

            # 写入内存索引，供后续请求直接命中
            try:
                name_no_ext = os.path.splitext(os.path.basename(cache_filepath))[0]
                quality_inferred = name_no_ext.split('_')[-1]
                _update_cache_index(source, songId, quality_inferred, cache_filepath)
            except Exception:
                pass

            return {
                'code': 0,
                'msg': 'success',
                'data': ext_res['url'],
                'extra': {
                    'cache': False,
                    'quality': {
                        'target': quality,
                        'result': ext_res['quality'],
                    },
                    'expire': {
                        'time': None,
                        'canExpire': False,
                    },
                    'localfile': False,
                    'fallback': 'externalScript',
                },
            }

        return {
            'code': 2,
            'msg': e.args[0],
            'data': None,
        }


async def lyric(source, songId, _, query):
    cache = config.getCache("lyric", f"{source}_{songId}")
    if cache:
        return {"code": 0, "msg": "success", "data": cache["data"]}
    try:
        func = require("modules." + source + ".lyric")
    except:
        return {
            "code": 1,
            "msg": "未知的源或不支持的方法",
            "data": None,
        }
    try:
        result = await func(songId)
        expireTime = 86400 * 3
        expireAt = int(time.time() + expireTime)
        config.updateCache(
            "lyric",
            f"{source}_{songId}",
            {
                "data": result,
                "time": expireAt,  # 歌词缓存3天
                "expire": True,
            },
            expireTime,
        )
        logger.debug(f"缓存已更新：{source}_{songId}, lyric: {result}")
        return {"code": 0, "msg": "success", "data": result}
    except FailedException as e:
        return {
            "code": 2,
            "msg": e.args[0],
            "data": None,
        }


async def search(source, songid, _, query):
    try:
        func = require("modules." + source + ".search")
    except:
        return {
            "code": 1,
            "msg": "未知的源或不支持的方法",
            "data": None,
        }
    try:
        result = await func(songid, query)
        return {"code": 0, "msg": "success", "data": result}
    except FailedException as e:
        return {
            "code": 2,
            "msg": e.args[0],
            "data": None,
        }


async def other(method, source, songid, _, query):
    # info 方法支持本地缓存
    cache_key = f"{source}_{songid}"
    if method == "info":
        cache = config.getCache("info", cache_key)
        if cache:
            return {"code": 0, "msg": "success", "data": cache["data"]}

    try:
        func = require("modules." + source + "." + method)
    except:
        return {
            "code": 1,
            "msg": "未知的源或不支持的方法",
            "data": None,
        }
    try:
        result = await func(songid)
        # 若是 info，写入缓存
        if method == "info":
            config.updateCache("info", cache_key, {"expire": False, "time": 0, "data": result})
        return {"code": 0, "msg": "success", "data": result}
    except FailedException as e:
        return {
            "code": 2,
            "msg": e.args[0],
            "data": None,
        }


async def info_with_query(source, songid, _, query):
    return await other("info", source, songid, None)

async def _download_audio_to_cache(url: str, filepath: str, source: str, song_id: str):
    """后台下载音频文件到本地缓存，并在完成后写入元数据。
    增加最多 3 次重试，并捕获 ConnectionResetError 等网络异常，降低 10054 触发概率。"""

    if os.path.exists(filepath):
        return

    import aiohttp
    max_retry = 3
    for attempt in range(1, max_retry + 1):
        _owns_session = False
        session = variable.aioSession
        if session is None:
            session = aiohttp.ClientSession(trust_env=True)
            _owns_session = True

        try:
            async with session.get(url, timeout=120) as resp:
                if resp.status != 200:
                    raise aiohttp.ClientResponseError(status=resp.status, request_info=resp.request_info, history=resp.history)

                async with aiofiles.open(filepath, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        await f.write(chunk)

            logger.info(f"音频缓存完成: {filepath}")

            # 下载完成后嵌入元数据（若可用）
            try:
                info_cache = config.getCache("info", f"{source}_{song_id}")
                info_data = info_cache["data"] if info_cache else None
                lyric_cache = config.getCache("lyric", f"{source}_{song_id}")
                lyric_data = lyric_cache["data"] if lyric_cache else None
                cover_path = os.path.join(_remote_cache_dir, f"{source}_{song_id}_cover.jpg")
                _embed_metadata(filepath, info_data, cover_path if os.path.exists(cover_path) else None, lyric_data)
            except Exception:
                logger.debug("写入元数据失败\n" + traceback.format_exc())

            # 写入内存索引，供后续请求直接命中
            try:
                name_no_ext = os.path.splitext(os.path.basename(filepath))[0]
                quality_inferred = name_no_ext.split('_')[-1]
                _update_cache_index(source, song_id, quality_inferred, filepath)
            except Exception:
                pass

            # 成功即结束
            break
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionResetError) as e:
            # 删除可能写入的不完整文件
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

            logger.warning(f"下载音频失败/重试 {attempt}/{max_retry}: {e}")

            if attempt == max_retry:
                logger.error(f"下载音频放弃: {url}")
        except Exception:
            logger.warning(f"下载音频异常: {url}\n" + traceback.format_exc())
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            if attempt == max_retry:
                logger.error(f"下载音频放弃: {url}")
        finally:
            if _owns_session:
                await session.close()
            if attempt < max_retry:
                await asyncio.sleep(1)

def _embed_metadata(filepath: str, info: dict | None, cover_path: str | None, lyric_content: str | None):
    """将歌曲信息、歌词、封面写入音频文件元数据。支持 mp3 / flac。"""
    if not info:
        return
    try:
        logger.debug(f"[meta] embedding tags into {os.path.basename(filepath)}")
        if filepath.lower().endswith('.mp3'):
            try:
                audio = ID3(filepath)
            except mutagen.id3.ID3NoHeaderError:
                audio = ID3()
            # 标题、艺术家、专辑
            audio.add(TIT2(encoding=3, text=info.get('name') or info.get('name_ori', '')))
            audio.add(TPE1(encoding=3, text=info.get('singer', '')))
            audio.add(TALB(encoding=3, text=info.get('album', '')))
            # 歌词
            if lyric_content:
                audio.add(USLT(encoding=3, text=lyric_content))
            # 封面
            if cover_path and os.path.exists(cover_path):
                with open(cover_path, 'rb') as img_f:
                    audio.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_f.read()))
            audio.save(filepath)
        elif filepath.lower().endswith('.flac'):
            audio = FLAC(filepath)
            audio['title'] = info.get('name') or info.get('name_ori', '')
            audio['artist'] = info.get('singer', '')
            audio['album'] = info.get('album', '')
            # 歌词
            if lyric_content:
                audio['lyrics'] = lyric_content
            # 封面
            if cover_path and os.path.exists(cover_path):
                pic = Picture()
                with open(cover_path, 'rb') as img_f:
                    pic.data = img_f.read()
                pic.type = 3
                pic.mime = 'image/jpeg'
                audio.clear_pictures()
                audio.add_picture(pic)
            audio.save()
    except Exception:
        logger.debug("embed metadata error\n" + traceback.format_exc())

# Helper to build cache file path based on naming rule
def _find_cached_file(source: str, song_id: str, quality: str):
    """从内存索引中查找缓存文件，避免每次请求都进行磁盘 glob。"""
    song_map = _cache_index.get((source, song_id))
    if not song_map:
        return None
    # 精准匹配 quality
    if quality in song_map:
        return song_map[quality]
    # 回退：任意质量
    # 按质量名称排序可保证稳定输出，但这里简单返回第一个
    return next(iter(song_map.values()), None)

# —— 额外信息、歌词、封面缓存 ——
async def _ensure_metadata_cached(source: str, song_id: str):
    """获取 info/lyric 并缓存，同时下载封面到本地。并发去重。"""

    key = (source, song_id)
    # --- 去重: 若已有同源任务正在进行, 直接返回 ---
    async with _inflight_meta_lock:
        if key in _inflight_meta:
            return
        _inflight_meta.add(key)

    try:
        # Info cache
        info_key = f"{source}_{song_id}"
        info_cache = config.getCache("info", info_key)
        if not info_cache:
            try:
                func_info = require(f"modules.{source}.info")
                info_data = await func_info(song_id)
                # 写入缓存数据库（不过期）
                config.updateCache("info", info_key, {"expire": False, "time": 0, "data": info_data})
            except Exception:
                logger.debug(f"获取 info 失败: {source} {song_id}\n" + traceback.format_exc())
                info_data = None
        else:
            info_data = info_cache["data"]

        # Lyric cache(已有实现，但若没命中可手动触发)
        lyric_cache = config.getCache("lyric", info_key)
        if not lyric_cache:
            try:
                func_lyric = require(f"modules.{source}.lyric")
                lyric_data = await func_lyric(song_id)
                # 3 天过期与 modules.lyric 保持一致
                expire_time = 86400 * 3
                expire_at = int(time.time() + expire_time)
                config.updateCache("lyric", info_key, {"expire": True, "time": expire_at, "data": lyric_data}, expire_time)
            except Exception:
                logger.debug(f"获取 lyric 失败: {source} {song_id}\n" + traceback.format_exc())

        # 下载封面
        if info_data and info_data.get("cover"):
            cover_url = info_data["cover"]
            ext = os.path.splitext(cover_url.split("?")[0])[1] or ".jpg"
            cover_filename = f"{source}_{song_id}_cover{ext}"
            cover_path = os.path.join(_remote_cache_dir, cover_filename)
            if not os.path.exists(cover_path):
                try:
                    async with variable.aioSession.get(cover_url) as resp:
                        if resp.status == 200:
                            with open(cover_path, "wb") as f:
                                async for chunk in resp.content.iter_chunked(8192):
                                    f.write(chunk)
                            logger.info(f"封面缓存完成: {cover_path}")
                            # 把cover地址替换为本地路径并重新写入缓存
                            info_data["cover"] = f"/cache/{cover_filename}"
                            config.updateCache("info", info_key, {"expire": False, "time": 0, "data": info_data})
                except Exception:
                    logger.debug(f"下载封面失败: {cover_url}\n" + traceback.format_exc())

        # —— 尝试把元数据写入已存在的缓存音频 ——
        try:
            for file_path in glob.glob(os.path.join(_remote_cache_dir, f"{source}_{song_id}_*.*")):
                if file_path.endswith('_cover.jpg'):
                    continue
                info_cache = config.getCache("info", f"{source}_{song_id}")
                info_data = info_cache["data"] if info_cache else None
                lyric_cache = config.getCache("lyric", f"{source}_{song_id}")
                lyric_data = lyric_cache["data"] if lyric_cache else None
                cover_file = os.path.join(_remote_cache_dir, f"{source}_{song_id}_cover.jpg")
                if not info_data:
                    logger.debug(f"[meta] info still missing for {source}_{song_id}")
                    continue
                _embed_metadata(file_path, info_data, cover_file if os.path.exists(cover_file) else None, lyric_data)
        except Exception:
            logger.debug("embed metadata post-process error\n" + traceback.format_exc())
    except Exception:
        logger.warning("缓存 metadata 发生异常\n" + traceback.format_exc())
    finally:
        # 任务结束, 移除标记
        async with _inflight_meta_lock:
            _inflight_meta.discard(key)

# ================= 外部 lx-music-source.js 脚本支持 =================
# 目录 ./external_scripts 用于缓存下载的脚本文件
_ext_script_dir = os.path.join(variable.workdir, 'external_scripts')
os.makedirs(_ext_script_dir, exist_ok=True)

# 这些函数已移动到 external_script.py 模块中
# 保留目录创建代码以确保向后兼容


# 外部脚本相关函数已移动到 external_script.py 模块中


# 外部脚本相关函数已移动到 external_script.py 模块中

# 外部脚本相关内容已移动到 external_script.py 模块中
