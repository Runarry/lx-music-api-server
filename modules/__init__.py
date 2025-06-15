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
        ext_res = await _try_external_script(source, songId, quality)
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
    """后台下载音频文件到本地缓存，并在完成后写入元数据。"""
    if os.path.exists(filepath):
        return
    try:
        # 若全局 aioSession 不存在（如在独立脚本调用时），则临时创建一个
        _owns_session = False
        session = variable.aioSession
        if session is None:
            import aiohttp
            session = aiohttp.ClientSession(trust_env=True)
            _owns_session = True

        async with session.get(url, timeout=120) as resp:
            if resp.status == 200:
                async with aiofiles.open(filepath, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 64):
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
            else:
                logger.warning(f"下载音频失败({resp.status}): {url}")
        if _owns_session:
            await session.close()
    except Exception:
        logger.warning(f"下载音频异常: {url}\n" + traceback.format_exc())

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
    """获取 info/lyric 并缓存，同时下载封面到本地。"""
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
    except Exception:
        logger.warning("缓存 metadata 发生异常\n" + traceback.format_exc())

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

# ================= 外部 lx-music-source.js 脚本支持 =================
# 目录 ./external_scripts 用于缓存下载的脚本文件
_ext_script_dir = os.path.join(variable.workdir, 'external_scripts')
os.makedirs(_ext_script_dir, exist_ok=True)


async def _ensure_script_download(url: str, force: bool = False) -> str | None:
    """下载脚本；force=True 时即使已存在也会重新下载覆盖。返回本地路径或 None"""
    filename = utils.createMD5(url.encode()) + '.js'
    filepath = os.path.join(_ext_script_dir, filename)
    if os.path.exists(filepath) and not force:
        return filepath
    try:
        async with variable.aioSession.get(url, timeout=20) as resp:
            if resp.status == 200:
                with open(filepath, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                logger.info(f"external script downloaded: {url} -> {filepath}")
                return filepath
            else:
                logger.warning(f"download script failed({resp.status}): {url}")
    except Exception:
        logger.warning(f"download script exception: {url}\n" + traceback.format_exc())
    return None


# ========= 辅助：定位 run_external.js =========


def _locate_run_external_js() -> str | None:
    """在不同运行/打包环境下定位 run_external.js 的实际路径。"""
    # 0) external_scripts 目录（优先使用已写入的脚本）
    path = os.path.join(_ext_script_dir, 'run_external.js')

    if os.path.exists(path):
        return os.path.abspath(path)

    # 若仍未找到，尝试写入内嵌脚本
    target_path = os.path.join(_ext_script_dir, 'run_external.js')
    try:
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(_RUN_EXTERNAL_JS)
        logger.info(f'run_external.js 已写入: {target_path}')
        return target_path
    except Exception as e:
        logger.error(f'写入 run_external.js 失败: {e}')
        return None


async def _run_node_script(script_path: str, source: str, song_id: str, quality: str, info_dict: dict | None):
    """调用 node 子进程执行脚本，返回解析后的 JSON。"""
    try:
        info_json = json.dumps(info_dict or {}, ensure_ascii=False)
        runner_js = _locate_run_external_js()
        if runner_js is None:
            logger.error('run_external.js 未找到，无法执行 external script')
            return None

        cmd = [
            'node',
            runner_js,
            script_path,
            source,
            song_id,
            quality,
            info_json,
        ]
        proc = await asp.create_subprocess_exec(*cmd, stdout=asp.PIPE, stderr=asp.PIPE)
        stdout, stderr = await proc.communicate()
        if stderr:
            logger.debug(f"external script stderr: {stderr.decode(errors='ignore')}")
        if stdout:
            try:
                # 取最后一行非空文本
                lines = stdout.decode(errors='ignore').strip().split('\n')
                out_json = json.loads(lines[-1])
                return out_json
            except Exception:
                logger.debug("parse external script output failed\n" + traceback.format_exc())
        return None
    except FileNotFoundError:
        logger.error('Node.js 未安装或未在 PATH 中，无法使用 external script fallback')
    except Exception:
        logger.debug('run node script error\n' + traceback.format_exc())
    return None


async def _try_external_script(source: str, song_id: str, quality: str):
    """遍历配置的外部脚本，尝试获取播放链接，并输出详细日志。"""
    urls: list[str] = config.read_config('common.external_scripts.urls') or []
    if not urls:
        logger.info('[externalScript] external_scripts.urls 未配置，跳过')
        return None
    for url in urls:
        logger.info(f"[externalScript] 尝试脚本来源: {url}")
        local_path = await _ensure_script_download(url)
        if not local_path:
            logger.info(f"[externalScript] 下载失败/跳过: {url}")
            continue
        logger.info(f"[externalScript] 执行脚本文件: {local_path}")
        result = await _run_node_script(local_path, source, song_id, quality, {'songmid': song_id, 'hash': song_id})
        logger.info(f"[externalScript] 脚本返回: {result}")
        if result and isinstance(result, dict) and result.get('code') == 0 and result.get('data'):
            logger.info(f"[externalScript] 获取成功 --> {result['data']}")
            return {
                'url': result['data'],
                'quality': result.get('quality', quality),
            }
        else:
            logger.info('[externalScript] 未得到有效结果，继续下一个脚本')
    logger.info('[externalScript] 所有脚本尝试失败')
    return None

# 主动刷新所有脚本（启动时调用）
async def refresh_external_scripts():
    urls: list[str] = config.read_config('common.external_scripts.urls') or []
    if not urls:
        logger.info('[externalScript] 无外部脚本需要刷新')
        return
    logger.info('[externalScript] 正在刷新外部脚本...')
    for url in urls:
        await _ensure_script_download(url, force=True)
    logger.info('[externalScript] 外部脚本刷新完成')

# ========= 内嵌 run_external.js 脚本内容 =========

_RUN_EXTERNAL_JS = r"""
// Node adapter for lx-music-api-server
// Usage: node run_external.js <scriptPath> <source> <songId> <quality> <infoJson>
// Outputs single-line JSON to stdout. Example: {"code":0,"data":"https://..."}

const fs = require('fs');
const path = require('path');

if (process.argv.length < 7) {
  console.log(JSON.stringify({ code: 2, msg: 'invalid args' }));
  process.exit(0);
}

const [,, scriptPath, source, songId, quality, infoJson] = process.argv;
let musicInfo;
try {
  musicInfo = JSON.parse(infoJson || '{}');
} catch (e) {
  musicInfo = {};
}

// ------------------------------------------------------------
// 构造最小化的 LX 运行时
// ------------------------------------------------------------
const http = require('http');
const https = require('https');

function lxRequest(url, options = {}, cb) {
  try {
    const lib = url.startsWith('https') ? https : http;
    const req = lib.request(url, {
      method: options.method || 'GET',
      headers: options.headers || {},
    }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const bodyBuf = Buffer.concat(chunks);
        let body;
        try {
          body = JSON.parse(bodyBuf.toString());
        } catch {
          body = bodyBuf.toString();
        }
        cb(null, { body, statusCode: res.statusCode, headers: res.headers });
      });
    });
    req.on('error', err => cb(err));
    if (options.body) req.write(options.body);
    req.end();
  } catch (err) {
    cb(err);
  }
}

const listeners = {};
const EVENT_NAMES = {
  request: 'request',
  inited: 'inited',
  updateAlert: 'updateAlert',
};

const lx = {
  EVENT_NAMES,
  env: 'server',
  version: 'external',
  request: lxRequest,
  on: (name, cb) => { listeners[name] = cb; },
  send: async (name, payload) => {
    if (typeof listeners[name] === 'function') {
      return await listeners[name](payload);
    }
  },
  utils: {
    buffer: {
      from: (...args) => Buffer.from(...args),
      bufToString: (buf, enc) => buf.toString(enc),
    },
  },
};

globalThis.lx = lx;

// ------------------------------------------------------------
// 加载外部脚本
// ------------------------------------------------------------
try {
  require(path.resolve(scriptPath));
} catch (e) {
  console.log(JSON.stringify({ code: 2, msg: 'require script error: ' + e.message }));
  process.exit(0);
}

(async () => {
  try {
    const result = await lx.send(lx.EVENT_NAMES.request, {
      action: 'musicUrl',
      source,
      info: {
        musicInfo: Object.assign({ songmid: songId, hash: songId }, musicInfo),
        type: quality,
      },
    });
    if (!result) {
      console.log(JSON.stringify({ code: 2, msg: 'no result' }));
    } else {
      console.log(JSON.stringify({ code: 0, data: result }));
    }
  } catch (err) {
    console.log(JSON.stringify({ code: 2, msg: err.message || String(err) }));
  }
})(); 
"""
