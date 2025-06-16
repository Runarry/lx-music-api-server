# ----------------------------------------
# - mode: python -
# - author: helloplhm-qwq -
# - name: lx.py -
# - project: lx-music-api-server -
# - license: MIT -
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

from . import config
from . import scheduler
from .log import log
from aiohttp.web import Response
import ujson as json
import re
import os
import sys
from common.utils import createMD5

logger = log('lx_script')

def get_resource_path(relative_path):
    """获取资源文件路径，处理PyInstaller打包后的情况"""
    try:
        # PyInstaller打包后的临时文件夹
        base_path = sys._MEIPASS
    except Exception:
        # 开发环境下的路径
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def get_script_content():
    """
    获取脚本模板内容
    优先级：
    1. 同目录下的 lx-music-source-example.js
    2. 内嵌资源文件
    """
    local_script_path = './lx-music-source-example.js'
    embedded_script_path = get_resource_path('lx-music-source-example.js')
    
    # 优先读取同目录下的模板文件
    if os.path.exists(local_script_path):
        try:
            with open(local_script_path, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.info('使用本地模板脚本文件')
                return content
        except Exception as e:
            logger.warning(f'读取本地模板脚本失败: {e}')
    
    # 如果本地文件不存在或读取失败，使用内嵌资源
    if os.path.exists(embedded_script_path):
        try:
            with open(embedded_script_path, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.info('使用内嵌模板脚本文件')
                return content
        except Exception as e:
            logger.error(f'读取内嵌模板脚本失败: {e}')
    
    logger.error('无法找到模板脚本文件')
    return None

async def get_script():
    """保持向后兼容，但现在不做任何操作"""
    logger.info('脚本模板现已使用本地文件，无需远程更新')
    pass

async def generate_script_response(request):
    if (request.query.get('key') not in config.read_config('security.key.values') and config.read_config('security.key.enable')):
        return {'code': 6, 'msg': 'key验证失败', 'data': None}, 403
    
    # 使用新的脚本内容获取方法
    script = get_script_content()
    if script is None:
        return {'code': 4, 'msg': '无法获取源脚本模板', 'data': None}, 400
    scriptLines = script.split('\n')
    newScriptLines = []
    for line in scriptLines:
        oline = line
        line = line.strip()
        if (line.startswith('const API_URL')):
            newScriptLines.append(f'''const API_URL = "{'https' if config.read_config('common.ssl_info.is_https') else 'http'}://{request.host}"''')
        elif (line.startswith('const API_KEY')):
            newScriptLines.append(f"""const API_KEY = `{request.query.get("key") if request.query.get("key") else ''''''}`""")
        elif (line.startswith("* @name")):
            newScriptLines.append(" * @name " + config.read_config("common.download_config.name"))
        elif (line.startswith("* @description")):
            newScriptLines.append(" * @description " + config.read_config("common.download_config.intro"))
        elif (line.startswith("* @author")):
            newScriptLines.append(" * @author " + config.read_config("common.download_config.author"))
        elif (line.startswith("* @version")):
            newScriptLines.append(" * @version " + str(config.read_config("common.download_config.version")))
        elif (line.startswith("const DEV_ENABLE ")):
            newScriptLines.append("const DEV_ENABLE = " + str(config.read_config("common.download_config.dev")).lower())
        elif (line.startswith("const UPDATE_ENABLE ")):
            newScriptLines.append("const UPDATE_ENABLE = " + str(config.read_config("common.download_config.update")).lower())
        else:
            newScriptLines.append(oline)
    r = '\n'.join(newScriptLines)
    
    # —— 移除模板中对 `server_` 前缀的强制要求 ——
    r = re.sub(r"if \(!musicInfo\.songmid\.startsWith\('server_'\)\) throw new Error\('[^']*'\);?", "", r)
    # 去掉对 songmid 的 replace('server_', '') 调用
    r = re.sub(r"songId\.replace\('server_', ''\)", "songId", r)
    
    # 根据 module.{source}.enable 过滤掉已禁用的平台
    full_quality_conf = config.read_config("common.download_config.quality") or {}
    # 不再过滤渠道，全部下发
    filtered_quality_conf = full_quality_conf
    r = re.sub(r'const MUSIC_QUALITY = {[^}]+}', f'const MUSIC_QUALITY = JSON.parse(\'{json.dumps(filtered_quality_conf)}\')', r)
    
    # 修复当服务器返回相对路径(/cache/xxx)时，前端拼接 API_URL
    r = r.replace('return body.data', "return body.data.startsWith('http') ? body.data : `${API_URL}${body.data}`")
    
    # 用于检查更新
    if (config.read_config("common.download_config.update")):
        md5 = createMD5(r)
        r = r.replace(r"const SCRIPT_MD5 = ''", f"const SCRIPT_MD5 = '{md5}'")
        if (request.query.get('checkUpdate')):
            if (request.query.get('checkUpdate') == md5):
                return {'code': 0, 'msg': 'success', 'data': None}, 200
            url = f"{'https' if config.read_config('common.ssl_info.is_https') else 'http'}://{request.host}/script"
            updateUrl = f"{url}{('?key=' + request.query.get('key')) if request.query.get('key') else ''}"
            updateMsg = config.read_config('common.download_config.updateMsg').format(updateUrl = updateUrl, url = url, key = request.query.get('key')).replace('\\n', '\n')
            return {'code': 0, 'msg': 'success', 'data': {'updateMsg': updateMsg, 'updateUrl': updateUrl}}, 200
    
    # —— 注入 info 参数，使客户端在请求 url 时带上歌曲信息 ——
    # 插入 infoPayload 行
    r = r.replace(
        "const songId = musicInfo.hash ?? musicInfo.songmid",
        "const songId = musicInfo.hash ?? musicInfo.songmid\n  const infoPayload = utils.buffer.bufToString(utils.buffer.from(JSON.stringify(musicInfo)), 'base64').replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/, '')"
    )
    # 替换 url，附加 info 参数
    r = r.replace(
        "`${API_URL}/url/${source}/${songId}/${quality}`",
        "`${API_URL}/url/${source}/${songId}/${quality}?info=${infoPayload}`"
    )
    
    return Response(text = r, content_type = 'text/javascript',
                    headers = {
                        'Content-Disposition': f'''attachment; filename={
                            config.read_config("common.download_config.filename")
                            if config.read_config("common.download_config.filename").endswith(".js")
                            else (config.read_config("common.download_config.filename") + ".js")}'''
                    })

# 脚本模板现已使用本地文件，无需远程更新任务
# if (config.read_config('common.allow_download_script')):
#     scheduler.append('update_script', get_script)
