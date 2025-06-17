# ----------------------------------------
# - mode: python -
# - author: helloplhm-qwq -
# - name: external_script.py -
# - project: lx-music-api-server -
# - license: MIT -
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

import os
import traceback
import ujson as json
import asyncio.subprocess as asp
from common import log
from common import config
from common import utils
from common import variable

logger = log.log("external_script")

# 目录 ./external_scripts 用于缓存下载的脚本文件
_ext_script_dir = os.path.join(variable.workdir, 'external_scripts')
os.makedirs(_ext_script_dir, exist_ok=True)

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


def _locate_run_external_js() -> str | None:
    """写入内嵌的 run_external.js 脚本到 external_scripts 目录。"""
    target_path = os.path.join(_ext_script_dir, 'run_external.js')
    
    # 如果已存在，直接返回路径
    if os.path.exists(target_path):
        return os.path.abspath(target_path)
    
    # 写入内嵌脚本
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


async def try_external_script(source: str, song_id: str, quality: str):
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