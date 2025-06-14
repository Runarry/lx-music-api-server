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

async def url(source, songId, quality, query={}):
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

    if source == "kg":
        songId = songId.lower()

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
                    asyncio.create_task(_download_audio_to_cache(result["url"], cache_filepath))
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
        logger.info(f"获取{source}_{songId}_{quality}失败，原因：" + e.args[0])
        return {
            "code": 2,
            "msg": e.args[0],
            "data": None,
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

async def _download_audio_to_cache(url: str, filepath: str):
    """后台下载音频文件到本地缓存，不抛出异常。"""
    if os.path.exists(filepath):
        return
    try:
        async with variable.aioSession.get(url, timeout=120) as resp:
            if resp.status == 200:
                with open(filepath, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 64):
                        f.write(chunk)
                logger.info(f"音频缓存完成: {filepath}")
            else:
                logger.warning(f"下载音频失败({resp.status}): {url}")
    except Exception:
        logger.warning(f"下载音频异常: {url}\n" + traceback.format_exc())

# Helper to build cache file path based on naming rule
def _find_cached_file(source: str, song_id: str, quality: str):
    pattern = f"{source}_{song_id}_{quality}.*"
    files = glob.glob(os.path.join(_remote_cache_dir, pattern))
    return files[0] if files else None

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
