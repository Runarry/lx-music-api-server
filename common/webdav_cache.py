# ----------------------------------------
# - mode: python -
# - author: helloplhm-qwq -
# - name: webdav_cache.py -
# - project: lx-music-api-server -
# - license: MIT -
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

import os
import asyncio
import aiohttp
from urllib.parse import quote, unquote, urljoin
from xml.etree import ElementTree as ET
import collections
from . import log, config
import base64
import traceback

logger = log.log('webdav_cache')

# WebDAV 缓存索引
# 音频缓存索引: _audio_cache_index[(source, song_id)][quality] = webdav_url
_audio_cache_index = collections.defaultdict(dict)
# 本地音乐索引: _local_music_index[filename] = webdav_url
_local_music_index = {}
# 索引锁
_index_lock = asyncio.Lock()

class WebDAVClient:
    def __init__(self, config_dict):
        self.url = config_dict['url'].rstrip('/')
        self.username = config_dict.get('username', '')
        self.password = config_dict.get('password', '')
        self.paths = config_dict.get('paths', {})
        self.ssl_verify = config_dict.get('ssl_verify', True)
        self.timeout = config_dict.get('timeout', 30)
        self.direct_url = config_dict.get('direct_url', False)
        self.proxy_auth = config_dict.get('proxy_auth', True)
        
        # 基础认证头
        self.auth_header = None
        if self.username and self.password:
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            self.auth_header = f"Basic {credentials}"
    
    def get_auth_headers(self):
        """获取认证头"""
        if self.auth_header:
            return {'Authorization': self.auth_header}
        return {}
    
    async def list_directory(self, path=""):
        """列出 WebDAV 目录内容"""
        full_path = f"{self.url}/{path}".rstrip('/')
        
        headers = {
            'Depth': '1',
            'Content-Type': 'application/xml'
        }
        headers.update(self.get_auth_headers())
        
        # PROPFIND 请求体
        body = '''<?xml version="1.0" encoding="utf-8" ?>
        <D:propfind xmlns:D="DAV:">
            <D:prop>
                <D:displayname/>
                <D:getcontentlength/>
                <D:getlastmodified/>
                <D:resourcetype/>
            </D:prop>
        </D:propfind>'''
        
        # 使用全局的 session
        from . import variable
        session = variable.aioSession
        if not session:
            session = aiohttp.ClientSession()
        
        async with session.request(
            'PROPFIND',
            full_path,
            headers=headers,
            data=body,
            ssl=self.ssl_verify,
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as resp:
                if resp.status not in (207, 200):
                    raise Exception(f"WebDAV PROPFIND failed: {resp.status}")
                
                content = await resp.text()
                return self._parse_propfind_response(content, path)
    
    def _parse_propfind_response(self, xml_content, base_path):
        """解析 PROPFIND 响应"""
        files = []
        root = ET.fromstring(xml_content)
        
        # 注册 DAV 命名空间
        namespaces = {'D': 'DAV:'}
        
        for response in root.findall('.//D:response', namespaces):
            href = response.find('D:href', namespaces)
            if href is None:
                continue
            
            href_text = unquote(href.text)
            
            # 检查是否是文件
            propstat = response.find('D:propstat', namespaces)
            if propstat is None:
                continue
                
            prop = propstat.find('D:prop', namespaces)
            if prop is None:
                continue
                
            resource_type = prop.find('D:resourcetype', namespaces)
            is_collection = resource_type is not None and resource_type.find('D:collection', namespaces) is not None
            
            if not is_collection:
                # 提取文件名
                filename = os.path.basename(href_text.rstrip('/'))
                if filename and self._is_audio_or_meta_file(filename):
                    files.append(filename)
        
        return files
    
    def _is_audio_or_meta_file(self, filename):
        """检查是否是音频文件或元数据文件"""
        audio_extensions = ('.mp3', '.flac', '.m4a', '.ogg', '.wav', '.ape', '.wma')
        meta_extensions = ('.jpg', '.jpeg', '.png', '.lrc', '.json')
        filename_lower = filename.lower()
        return filename_lower.endswith(audio_extensions) or filename_lower.endswith(meta_extensions)
    
    def generate_url(self, filepath):
        """生成 WebDAV 文件访问 URL"""
        # 确保路径不以斜杠开头，避免双斜杠
        filepath = filepath.lstrip('/')
        # URL 编码文件路径
        encoded_path = quote(filepath, safe='/')
        full_url = f"{self.url}/{encoded_path}"
        
        if self.direct_url and self.username and self.password:
            # 生成包含认证信息的直接 URL
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(full_url)
            netloc = f"{quote(self.username)}:{quote(self.password)}@{parsed.netloc}"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        else:
            # 返回需要代理认证的 URL
            return full_url

async def init_webdav_index():
    """初始化 WebDAV 缓存索引"""
    if not config.read_config('common.webdav_cache.enable'):
        return
    
    logger.info("开始构建 WebDAV 缓存索引...")
    
    try:
        webdav_config = config.read_config('common.webdav_cache')
        client = WebDAVClient(webdav_config)
        
        # 清空现有索引
        async with _index_lock:
            _audio_cache_index.clear()
            _local_music_index.clear()
        
        # 扫描音频缓存目录
        audio_path = webdav_config.get('paths', {}).get('audio', '/cache_audio').lstrip('/')
        await _scan_audio_cache_directory(client, audio_path)
        
        # 扫描本地音乐目录
        local_path = webdav_config.get('paths', {}).get('local', '/audio').lstrip('/')
        await _scan_local_music_directory(client, local_path)
        
        total_files = sum(len(v) for v in _audio_cache_index.values()) + len(_local_music_index)
        logger.info(f"WebDAV 索引构建完成，共索引 {total_files} 个文件")
        logger.debug(f"音频缓存: {sum(len(v) for v in _audio_cache_index.values())} 个文件")
        logger.debug(f"本地音乐: {len(_local_music_index)} 个文件")
    except Exception as e:
        logger.error(f"构建 WebDAV 索引失败: {e}")
        logger.debug(traceback.format_exc())

async def _scan_audio_cache_directory(client, path):
    """扫描音频缓存目录"""
    try:
        files = await client.list_directory(path)
        
        for filename in files:
            # 解析文件名格式: <source>_<songId>_<quality>.<ext>
            name_no_ext, ext = os.path.splitext(filename)
            
            # 处理封面文件: <source>_<songId>_cover.jpg
            if filename.endswith('_cover.jpg'):
                parts = name_no_ext.rsplit('_cover', 1)
                if len(parts) == 2:
                    prefix = parts[0]
                    sub_parts = prefix.split('_', 1)
                    if len(sub_parts) >= 2:
                        source = sub_parts[0]
                        song_id = sub_parts[1]
                        file_path = os.path.join(path, filename).replace('\\', '/')
                        url = client.generate_url(file_path)
                        
                        async with _index_lock:
                            _audio_cache_index[(source, song_id)]['cover'] = url
                        
                        logger.debug(f"索引封面文件: {source}_{song_id}_cover -> {url}")
                continue
            
            # 处理音频文件
            parts = name_no_ext.split('_')
            if len(parts) >= 3:
                source = parts[0]
                quality = parts[-1]
                song_id = '_'.join(parts[1:-1])
                
                # 生成访问 URL
                file_path = os.path.join(path, filename).replace('\\', '/')
                url = client.generate_url(file_path)
                
                async with _index_lock:
                    _audio_cache_index[(source, song_id)][quality] = url
                
                logger.debug(f"索引音频文件: {source}_{song_id}_{quality} -> {url}")
    except Exception as e:
        logger.warning(f"扫描音频缓存目录 {path} 失败: {e}")

async def _scan_local_music_directory(client, path):
    """扫描本地音乐目录"""
    try:
        files = await client.list_directory(path)
        
        for filename in files:
            # 本地音乐文件直接使用文件名作为索引
            file_path = os.path.join(path, filename).replace('\\', '/')
            url = client.generate_url(file_path)
            
            async with _index_lock:
                _local_music_index[filename] = url
            
            logger.debug(f"索引本地音乐文件: {filename} -> {url}")
    except Exception as e:
        logger.warning(f"扫描本地音乐目录 {path} 失败: {e}")

def find_webdav_cached_file(source, song_id, quality):
    """查找 WebDAV 音频缓存文件"""
    if not config.read_config('common.webdav_cache.enable'):
        return None
    
    song_map = _audio_cache_index.get((source, song_id))
    if not song_map:
        return None
    
    # 精确匹配质量
    if quality in song_map:
        return song_map[quality]
    
    # 如果没有精确匹配，返回任意质量
    return next(iter(song_map.values()), None)

def find_webdav_local_file(filename):
    """查找 WebDAV 本地音乐文件"""
    if not config.read_config('common.webdav_cache.enable'):
        return None
    
    # 尝试多种变体
    from common.localMusic import normalize_filename
    normalized_name = normalize_filename(filename)
    
    # 精确匹配
    if filename in _local_music_index:
        return _local_music_index[filename]
    
    # 规范化匹配
    if normalized_name in _local_music_index:
        return _local_music_index[normalized_name]
    
    # 小写匹配
    filename_lower = filename.lower()
    normalized_lower = normalized_name.lower()
    
    for key, url in _local_music_index.items():
        key_lower = key.lower()
        if key_lower == filename_lower or key_lower == normalized_lower:
            return url
    
    return None

def find_webdav_cover(source, song_id):
    """查找 WebDAV 封面文件"""
    if not config.read_config('common.webdav_cache.enable'):
        return None
    
    song_map = _audio_cache_index.get((source, song_id))
    if not song_map:
        return None
    
    return song_map.get('cover')

async def refresh_webdav_index():
    """定期刷新 WebDAV 索引"""
    while True:
        try:
            interval = config.read_config('common.webdav_cache.index_refresh_interval') or 3600
            await asyncio.sleep(interval)
            
            if config.read_config('common.webdav_cache.enable'):
                logger.info("刷新 WebDAV 缓存索引...")
                await init_webdav_index()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"刷新 WebDAV 索引失败: {e}")

# 导出临时文件相关函数（供 localMusic 使用）
def find_webdav_temp_file(filename):
    """查找 WebDAV 临时文件（如 meta.json）"""
    if not config.read_config('common.webdav_cache.enable'):
        return None
    
    try:
        webdav_config = config.read_config('common.webdav_cache')
        client = WebDAVClient(webdav_config)
        temp_path = webdav_config.get('paths', {}).get('temp', '/temp').lstrip('/')
        
        file_path = os.path.join(temp_path, filename).replace('\\', '/')
        return client.generate_url(file_path)
    except Exception:
        return None

async def get_webdav_file_content(url):
    """获取 WebDAV 文件内容"""
    try:
        webdav_config = config.read_config('common.webdav_cache')
        headers = {}
        
        if webdav_config.get('username') and webdav_config.get('password'):
            credentials = base64.b64encode(
                f"{webdav_config['username']}:{webdav_config['password']}".encode()
            ).decode()
            headers['Authorization'] = f"Basic {credentials}"
        
        # 使用全局的 session
        from . import variable
        session = variable.aioSession
        if not session:
            session = aiohttp.ClientSession()
            
        async with session.get(
            url, 
            headers=headers, 
            ssl=webdav_config.get('ssl_verify', True),
            timeout=aiohttp.ClientTimeout(total=webdav_config.get('timeout', 30))
        ) as resp:
            if resp.status == 200:
                return await resp.read()
            return None
    except Exception as e:
        logger.debug(f"获取 WebDAV 文件内容失败: {e}")
        return None