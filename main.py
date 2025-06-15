#!/usr/bin/env python3

# ----------------------------------------
# - mode: python -
# - author: helloplhm-qwq -
# - name: main.py -
# - project: lx-music-api-server -
# - license: MIT -
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

import time
import aiohttp
import asyncio
import traceback
import threading
import ujson as json
from aiohttp.web import Response, FileResponse, StreamResponse, Application
from io import TextIOWrapper
import sys
import os

if sys.version_info < (3, 6):
    print('Python版本过低，请使用Python 3.6+ ')
    sys.exit(1)

# fix: module not found: common/modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from common import utils
from common import config, localMusic
from common import lxsecurity
from common import log
from common import Httpx
from common.Httpx import checkcn_async
from common import variable
from common import scheduler
from common import lx_script
from common import gcsp
import modules

def handleResult(dic, status=200) -> Response:
    if (not isinstance(dic, dict)):
        dic = {
            'code': 0,
            'msg': 'success',
            'data': dic
        }
    return Response(body=json.dumps(dic, indent=2, ensure_ascii=False), content_type='application/json', status=status)


logger = log.log("main")
aiologger = log.log('aiohttp_web')

stopEvent = None
if sys.version_info < (3, 8):
    logger.warning('您使用的Python版本已经停止更新，不建议继续使用')
    import concurrent
    stopEvent = concurrent.futures._base.CancelledError
else:
    stopEvent = asyncio.exceptions.CancelledError


# check request info before start


async def handle_before_request(app, handler):
    async def handle_request(request):
        try:
            if config.read_config("common.reverse_proxy.allow_proxy") and request.headers.get(
                config.read_config("common.reverse_proxy.real_ip_header")):
                if not (config.read_config("common.reverse_proxy.allow_public_ip") or utils.is_local_ip(request.remote)):
                    return handleResult({"code": 1, "msg": "不允许的公网ip转发", "data": None}, 403)
                # proxy header
                request.remote_addr = request.headers.get(config.read_config("common.reverse_proxy.real_ip_header"))
            else:
                request.remote_addr = request.remote
            # check ip
            if (config.check_ip_banned(request.remote_addr)):
                return handleResult({"code": 1, "msg": "您的IP已被封禁", "data": None}, 403)
            # check global rate limit
            if (
                (time.time() - config.getRequestTime('global'))
                <
                (config.read_config("security.rate_limit.global"))
            ):
                return handleResult({"code": 5, "msg": "全局限速", "data": None}, 429)
            if (
                (time.time() - config.getRequestTime(request.remote_addr))
                <
                (config.read_config("security.rate_limit.ip"))
            ):
                return handleResult({"code": 5, "msg": "IP限速", "data": None}, 429)
            # update request time
            config.updateRequestTime('global')
            config.updateRequestTime(request.remote_addr)
            # check host
            if (config.read_config("security.allowed_host.enable")):
                if request.host.split(":")[0] not in config.read_config("security.allowed_host.list"):
                    if config.read_config("security.allowed_host.blacklist.enable"):
                        config.ban_ip(request.remote_addr, int(
                            config.read_config("security.allowed_host.blacklist.length")))
                    return handleResult({'code': 6, 'msg': '未找到您所请求的资源', 'data': None}, 404)

            resp = await handler(request)
            if (isinstance(resp, (str, list, dict))):
                resp = handleResult(resp)
            elif (isinstance(resp, tuple) and len(resp) == 2):  # flask like response
                body, status = resp
                if (isinstance(body, (str, list, dict))):
                    resp = handleResult(body, status)
                else:
                    resp = Response(
                        body=str(body), content_type='text/plain', status=status)
            elif (not isinstance(resp, (Response, FileResponse, StreamResponse))):
                resp = Response(
                    body=str(resp), content_type='text/plain', status=200)
            aiologger.info(
                f'{request.remote_addr + ("" if (request.remote == request.remote_addr) else f"|proxy@{request.remote}")} - {request.method} "{request.path}", {resp.status}')
            return resp
        except:
            logger.error(traceback.format_exc())
            return {"code": 4, "msg": "内部服务器错误", "data": None}
    return handle_request


async def main(request):
    return handleResult({"code": 0, "msg": "success", "data": None})


async def handle(request):
    method = request.match_info.get('method')
    source = request.match_info.get('source')
    songId = request.match_info.get('songId')
    quality = request.match_info.get('quality')
    if (config.read_config("security.key.enable") and request.host.split(':')[0] not in config.read_config('security.whitelist_host')):
        if (request.headers.get("X-Request-Key")) not in config.read_config("security.key.values"):
            if (config.read_config("security.key.ban")):
                config.ban_ip(request.remote_addr)
            return handleResult({"code": 1, "msg": "key验证失败", "data": None}, 403)
    if (config.read_config('security.check_lxm.enable') and request.host.split(':')[0] not in config.read_config('security.whitelist_host')):
        lxm = request.headers.get('lxm')
        if (not lxsecurity.checklxmheader(lxm, request.url)):
            if (config.read_config('security.lxm_ban.enable')):
                config.ban_ip(request.remote_addr)
        return handleResult({"code": 1, "msg": "lxm请求头验证失败", "data": None}, 403)

    try:
        query = dict(request.query)
        source_enable = config.read_config(f'module.{source}.enable')
        # 若请求的是 url 方法，但本地模块已禁用，则允许进入 modules.url，让其尝试 external script fallback
        if (not source_enable) and method != 'url':
            return handleResult({
                'code': 4,
                'msg': '此平台已停止服务',
                'data': None,
                "Your IP": request.remote_addr
            }, 404)
        if method in dir(modules):
            return handleResult(await getattr(modules, method)(source, songId, quality, query))
        else:
            return handleResult(await modules.other(method, source, songId, quality, query))
    except:
        logger.error(traceback.format_exc())
        return handleResult({'code': 4, 'msg': '内部服务器错误', 'data': None}, 500)


async def handle_404(request):
    return handleResult({'code': 6, 'msg': '未找到您所请求的资源', 'data': None}, 404)


async def handle_local(request):
    try:
        query = dict(request.query)
        data = query.get('q')
        data = utils.createBase64Decode(
            data.replace('-', '+').replace('_', '/'))
        data = json.loads(data)
        t = request.match_info.get('type')
        data['t'] = t
        # 打印前端传入的参数，便于调试
        logger.info(f"[LOCAL API] type={t}, p={data.get('p')}")
    except:
        logger.info(traceback.format_exc())
        return handleResult({'code': 6, 'msg': '请求参数有错', 'data': None}, 404)
    if (data['t'] == 'u'):
        if localMusic.hasMusic(data['p']):
            return await localMusic.generateAudioFileResonse(data['p'])
        else:
            return handleResult({'code': 6, 'msg': '未找到您所请求的资源', 'data': None}, 404)
    if (data['t'] == 'l'):
        if localMusic.hasMusic(data['p']):
            return await localMusic.generateAudioLyricResponse(data['p'])
        else:
            return handleResult({'code': 6, 'msg': '未找到您所请求的资源', 'data': None}, 404)
    if (data['t'] == 'p'):
        if localMusic.hasMusic(data['p']):
            return await localMusic.generateAudioCoverResonse(data['p'])
        else:
            return handleResult({'code': 6, 'msg': '未找到您所请求的资源', 'data': None}, 404)
    if (data['t'] == 'c'):
        if (not localMusic.hasMusic(data['p'])):
            return {
                'code': 0,
                'msg': 'success',
                'data': {
                    'file': False,
                    'cover': False,
                    'lyric': False
                }
            }
        return {
            'code': 0,
            'msg': 'success',
            'data': localMusic.checkLocalMusic(data['p'])
        }

# 音频缓存文件访问
async def handle_cache_file(request):
    filename = request.match_info.get('filename')
    cache_dir = config.read_config('common.remote_cache.path') or './cache_audio'
    path = os.path.join(cache_dir, filename)
    if os.path.exists(path):
        return FileResponse(path)
    return handleResult({'code': 6, 'msg': '未找到您所请求的资源', 'data': None}, 404)

app = Application(middlewares=[handle_before_request])
utils.setGlobal(app, "app")

# 缓存文件访问路由需要放在通配符路由之前
app.router.add_get('/', main)
app.router.add_get('/cache/{filename}', handle_cache_file)

# 动态 API 路由
app.router.add_get('/{method}/{source}/{songId}/{quality}', handle)
app.router.add_get('/{method}/{source}/{songId}', handle)

app.router.add_get('/local/{type}', handle_local)

if (config.read_config('common.allow_download_script')):
    app.router.add_get('/script', lx_script.generate_script_response)

if (config.read_config('module.gcsp.enable')):
    app.router.add_route('*', config.read_config('module.gcsp.path'), gcsp.handle_request)

# 404
app.router.add_route('*', '/{tail:.*}', handle_404)


async def run_app_host(host):
    retries = 0
    while True:
        if (retries > 4):
            logger.warning("重试次数已达上限，但仍有部分端口未能完成监听，已自动进行忽略")
            break
        try:
            ports = [int(port)
                     for port in config.read_config('common.ports')]
            ssl_ports = [int(port) for port in config.read_config(
                'common.ssl_info.ssl_ports')]
            final_ssl_ports = []
            final_ports = []
            for p in ports:
                if (p not in ssl_ports and f'{host}_{p}' not in variable.running_ports):
                    final_ports.append(p)
                else:
                    if (p not in variable.running_ports):
                        final_ssl_ports.append(p)
            # 读取证书和私钥路径
            cert_path = config.read_config('common.ssl_info.path.cert')
            privkey_path = config.read_config(
                'common.ssl_info.path.privkey')

            # 创建 HTTP AppRunner
            http_runner = aiohttp.web.AppRunner(app)
            await http_runner.setup()

            # 启动 HTTP 端口监听
            for port in final_ports:
                if (port not in variable.running_ports):
                    http_site = aiohttp.web.TCPSite(
                        http_runner, host, port)
                    await http_site.start()
                    variable.running_ports.append(f'{host}_{port}')
                    logger.info(f"""监听 -> http://{
                        host if (':' not in host)
                        else '[' + host + ']'
                    }:{port}""")

            if (config.read_config("common.ssl_info.enable") and final_ssl_ports != []):
                if (os.path.exists(cert_path) and os.path.exists(privkey_path)):
                    import ssl
                    # 创建 SSL 上下文，加载配置文件中指定的证书和私钥
                    ssl_context = ssl.create_default_context(
                        ssl.Purpose.CLIENT_AUTH)
                    ssl_context.load_cert_chain(cert_path, privkey_path)

                    # 创建 HTTPS AppRunner
                    https_runner = aiohttp.web.AppRunner(app)
                    await https_runner.setup()

                    # 启动 HTTPS 端口监听
                    for port in ssl_ports:
                        if (port not in variable.running_ports):
                            https_site = aiohttp.web.TCPSite(
                                https_runner, host, port, ssl_context=ssl_context)
                            await https_site.start()
                            variable.running_ports.append(f'{host}_{port}')
                            logger.info(f"""监听 -> https://{
                                host if (':' not in host)
                                else '[' + host + ']'
                            }:{port}""")
            logger.debug(f"HOST({host}) 已完成监听")
            break
        except OSError as e:
            if (str(e).startswith("[Errno 98]") or str(e).startswith('[Errno 10048]')):
                logger.error("端口已被占用，请检查\n" + str(e))
                logger.info('服务器将在10s后再次尝试启动...')
                await asyncio.sleep(10)
                logger.info('重新尝试启动...')
                retries += 1
            else:
                logger.error("未知错误，请检查\n" + traceback.format_exc())


async def run_app():
    for host in config.read_config('common.hosts'):
        await run_app_host(host)


async def initMain():
    await scheduler.run()
    variable.aioSession = aiohttp.ClientSession(trust_env=True)
    asyncio.create_task(checkcn_async())
    try:
        await modules.refresh_external_scripts()
    except Exception:
        logger.warning('刷新外部脚本失败\n' + traceback.format_exc())
    localMusic.initMain()
    try:
        await run_app()
        logger.info("服务器启动成功，请按下Ctrl + C停止")
        await asyncio.Event().wait()  # 等待停止事件
    except (KeyboardInterrupt, stopEvent):
        pass
    except OSError as e:
        logger.error("遇到未知错误，请查看日志")
        logger.error(traceback.format_exc())
    except:
        logger.error("遇到未知错误，请查看日志")
        logger.error(traceback.format_exc())
    finally:
        logger.info('wating for sessions to complete...')
        if variable.aioSession:
            await variable.aioSession.close()

        variable.running = False
        logger.info("Server stopped")

if __name__ == "__main__":
    def disable_quick_edit_mode():
        if sys.platform.startswith('win'):
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                h_stdin = kernel32.GetStdHandle(-10)
                mode = ctypes.c_uint32()
                kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode))
                new_mode = (mode.value & ~0x0040) | 0x0080
                kernel32.SetConsoleMode(h_stdin, new_mode)
            except Exception as e:
                logger.warning(f"禁用快速编辑模式失败: {e}")

    try:
        disable_quick_edit_mode()
        # 初始化自定义事件循环以便托盘线程可以优雅关闭服务器
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 动态获取一个用于展示的 host / port（选第一个即可）
        try:
            if sys.platform.startswith('win'):
                from tray import start_tray_background
                start_tray_background(loop)
            else:
                logger.info('检测到非 Windows 系统，已跳过托盘功能')
        except Exception as e:
            logger.warning(f"托盘启动失败: {e}")

        loop.run_until_complete(initMain())
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.critical('初始化出错，请检查日志')
        logger.critical(traceback.format_exc())
        with open('dumprecord_{}.txt'.format(int(time.time())), 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
            e = '\n\nGlobal variable object:\n\n'
            for k in dir(variable):
                e += (k + ' = ' + str(getattr(variable, k)) + '\n') if (not k.startswith('_')) else ''
            f.write(e)
            e = '\n\nsys.modules:\n\n'
            for k in sys.modules:
                e += (k + ' = ' + str(sys.modules[k]) + '\n') if (not k.startswith('_')) else ''
            f.write(e)
        logger.critical('dumprecord_{}.txt 已保存至当前目录'.format(int(time.time())))
    finally:
        for f in variable.log_files:
            if (f and isinstance(f, TextIOWrapper)):
                f.close()
