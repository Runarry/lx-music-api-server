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