# WebDAV 缓存实现方案

## 1. 概述

本方案为 lx-music-api-server 增加 WebDAV 缓存功能，作为只读缓存层，优先级最高。WebDAV 缓存将在本地缓存之前被检查，如果命中则直接返回 WebDAV URL。

## 2. 配置设计

### 2.1 配置文件结构
在 `common/default_config.py` 中添加 WebDAV 配置：

```yaml
common:
  webdav_cache:
    enable: false                    # 是否启用 WebDAV 缓存
    url: "https://example.com/dav"   # WebDAV 服务器地址
    username: ""                     # WebDAV 用户名
    password: ""                     # WebDAV 密码
    # 目录路径配置（与本地缓存结构保持一致）
    paths:
      audio: "/cache_audio"          # 音频缓存目录（对应 remote_cache.path）
      local: "/audio"                # 本地音乐目录（对应 local_music.audio_path）
      temp: "/temp"                  # 临时文件目录（对应 local_music.temp_path）
    ssl_verify: true                 # 是否验证 SSL 证书
    timeout: 30                      # 连接超时时间（秒）
    # 缓存索引配置
    index_on_startup: true           # 启动时是否构建索引
    index_refresh_interval: 3600     # 索引刷新间隔（秒）
    # URL 生成配置
    direct_url: false                # 是否生成直接访问 URL（包含认证信息）
    proxy_auth: true                 # 是否通过服务器代理认证请求
```

## 3. 实现架构

### 3.1 新增模块：`common/webdav_cache.py`

```python
# common/webdav_cache.py
import os
import asyncio
import aiohttp
from urllib.parse import quote, unquote, urljoin
from xml.etree import ElementTree as ET
import collections
from . import log, config
import base64

logger = log.log('webdav_cache')

# WebDAV 缓存索引
# 结构: _webdav_index[(source, song_id)][quality] = webdav_url
_webdav_index = collections.defaultdict(dict)
_webdav_index_lock = asyncio.Lock()

class WebDAVClient:
    def __init__(self, config):
        self.url = config['url'].rstrip('/')
        self.username = config['username']
        self.password = config['password']
        self.path = config['path'].rstrip('/').lstrip('/')
        self.ssl_verify = config.get('ssl_verify', True)
        self.timeout = config.get('timeout', 30)
        self.direct_url = config.get('direct_url', False)
        self.proxy_auth = config.get('proxy_auth', True)
        
        # 基础认证头
        self.auth_header = None
        if self.username and self.password:
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            self.auth_header = f"Basic {credentials}"
    
    async def list_directory(self, path=""):
        """列出 WebDAV 目录内容"""
        full_path = f"{self.url}/{self.path}/{path}".rstrip('/')
        
        headers = {
            'Depth': '1',
            'Content-Type': 'application/xml'
        }
        if self.auth_header:
            headers['Authorization'] = self.auth_header
        
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
        
        async with aiohttp.ClientSession() as session:
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
                return self._parse_propfind_response(content)
    
    def _parse_propfind_response(self, xml_content):
        """解析 PROPFIND 响应"""
        files = []
        root = ET.fromstring(xml_content)
        
        for response in root.findall('.//{DAV:}response'):
            href = response.find('{DAV:}href')
            if href is None:
                continue
            
            href_text = unquote(href.text)
            
            # 检查是否是文件
            resource_type = response.find('.//{DAV:}resourcetype')
            is_collection = resource_type is not None and resource_type.find('{DAV:}collection') is not None
            
            if not is_collection:
                # 提取文件名
                filename = os.path.basename(href_text.rstrip('/'))
                if filename and self._is_audio_file(filename):
                    files.append(filename)
        
        return files
    
    def _is_audio_file(self, filename):
        """检查是否是音频文件"""
        audio_extensions = ('.mp3', '.flac', '.m4a', '.ogg', '.wav', '.ape', '.wma')
        return filename.lower().endswith(audio_extensions)
    
    def generate_url(self, filepath):
        """生成 WebDAV 文件访问 URL"""
        # URL 编码文件路径
        encoded_path = quote(filepath, safe='/')
        full_url = f"{self.url}/{self.path}/{encoded_path}".rstrip('/')
        
        if self.direct_url and self.username and self.password:
            # 生成包含认证信息的直接 URL
            # 格式: https://username:password@example.com/path/file.mp3
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(full_url)
            netloc = f"{self.username}:{self.password}@{parsed.netloc}"
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
        
        # 递归扫描目录
        await _scan_webdav_directory(client, "", 0)
        
        logger.info(f"WebDAV 索引构建完成，共索引 {sum(len(v) for v in _webdav_index.values())} 个文件")
    except Exception as e:
        logger.error(f"构建 WebDAV 索引失败: {e}")
        import traceback
        logger.debug(traceback.format_exc())

async def _scan_webdav_directory(client, path, depth):
    """递归扫描 WebDAV 目录"""
    if depth > 5:  # 限制递归深度
        return
    
    try:
        files = await client.list_directory(path)
        
        for filename in files:
            # 解析文件名格式: <source>_<songId>_<quality>.<ext>
            name_no_ext, ext = os.path.splitext(filename)
            parts = name_no_ext.split('_')
            
            if len(parts) >= 3:
                source = parts[0]
                quality = parts[-1]
                song_id = '_'.join(parts[1:-1])
                
                # 生成访问 URL
                file_path = f"{path}/{filename}".lstrip('/')
                url = client.generate_url(file_path)
                
                async with _webdav_index_lock:
                    _webdav_index[(source, song_id)][quality] = url
                
                logger.debug(f"索引 WebDAV 文件: {source}_{song_id}_{quality} -> {url}")
    except Exception as e:
        logger.warning(f"扫描 WebDAV 目录 {path} 失败: {e}")

def find_webdav_cached_file(source, song_id, quality):
    """查找 WebDAV 缓存文件"""
    if not config.read_config('common.webdav_cache.enable'):
        return None
    
    song_map = _webdav_index.get((source, song_id))
    if not song_map:
        return None
    
    # 精确匹配质量
    if quality in song_map:
        return song_map[quality]
    
    # 如果没有精确匹配，返回任意质量
    return next(iter(song_map.values()), None)

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
```

### 3.2 修改 `modules/__init__.py`

在 `url()` 函数中，在检查本地缓存之前添加 WebDAV 缓存检查：

```python
async def url(source, songId, quality, query={}):
    # ... 现有的 ID 规范化和内嵌缓存处理代码 ...
    
    # —— WebDAV 缓存预检查（最高优先级）——
    if config.read_config('common.webdav_cache.enable'):
        from common import webdav_cache
        webdav_url = webdav_cache.find_webdav_cached_file(source, songId, quality)
        if webdav_url:
            logger.debug(f"命中 WebDAV 缓存: {webdav_url}")
            
            # 如果需要代理认证，返回代理 URL
            if config.read_config('common.webdav_cache.proxy_auth') and not config.read_config('common.webdav_cache.direct_url'):
                # 返回服务器代理 URL
                proxy_url = f"/webdav/{source}/{songId}/{quality}"
                return {
                    "code": 0,
                    "msg": "success",
                    "data": proxy_url,
                    "extra": {
                        "cache": True,
                        "quality": {
                            "target": quality,
                            "result": quality,
                        },
                        "webdav": True,
                    },
                }
            else:
                # 直接返回 WebDAV URL
                return {
                    "code": 0,
                    "msg": "success",
                    "data": webdav_url,
                    "extra": {
                        "cache": True,
                        "quality": {
                            "target": quality,
                            "result": quality,
                        },
                        "webdav": True,
                    },
                }
    
    # ... 继续现有的本地缓存检查和其他逻辑 ...
```

### 3.3 修改 `main.py`

添加 WebDAV 代理路由和初始化：

```python
# 在 import 部分添加
from common import webdav_cache

# 添加 WebDAV 代理处理函数
async def handle_webdav_proxy(request):
    """代理 WebDAV 请求，添加认证头"""
    source = request.match_info.get('source')
    song_id = request.match_info.get('songId')
    quality = request.match_info.get('quality')
    
    # 查找 WebDAV URL
    webdav_url = webdav_cache.find_webdav_cached_file(source, song_id, quality)
    if not webdav_url:
        return handleResult({'code': 6, 'msg': '未找到您所请求的资源', 'data': None}, 404)
    
    # 代理请求
    webdav_config = config.read_config('common.webdav_cache')
    headers = {}
    if webdav_config.get('username') and webdav_config.get('password'):
        credentials = base64.b64encode(f"{webdav_config['username']}:{webdav_config['password']}".encode()).decode()
        headers['Authorization'] = f"Basic {credentials}"
    
    async with variable.aioSession.get(webdav_url, headers=headers, ssl=webdav_config.get('ssl_verify', True)) as resp:
        # 流式响应
        response = StreamResponse(status=resp.status, headers=resp.headers)
        await response.prepare(request)
        
        async for chunk in resp.content.iter_chunked(8192):
            await response.write(chunk)
        
        return response

# 在路由配置部分添加
app.router.add_get('/webdav/{source}/{songId}/{quality}', handle_webdav_proxy)

# 在 initMain() 函数中添加
async def initMain():
    # ... 现有代码 ...
    
    # 初始化 WebDAV 索引
    if config.read_config('common.webdav_cache.enable') and config.read_config('common.webdav_cache.index_on_startup'):
        await webdav_cache.init_webdav_index()
        # 启动定期刷新任务
        asyncio.create_task(webdav_cache.refresh_webdav_index())
    
    # ... 现有代码 ...
```

### 3.4 Local 请求的 WebDAV 支持

Local 请求的处理逻辑与普通 URL 请求有显著差异：

#### 3.4.1 前端处理流程分析

Local 源的前端处理有以下特点：

1. **二次请求机制**：
   - 首先请求 `/local/c?q={base64}` 检查资源是否存在
   - 如果存在，再请求具体资源：`/local/u`（音频）、`/local/p`（封面）、`/local/l`（歌词）

2. **文件名格式**：
   - 必须以 `server_` 前缀开始（在 lx_script.py 中已移除此限制）
   - 使用 base64url 编码传递文件名参数

3. **返回格式差异**：
   - URL 请求：返回完整 URL 或相对路径
   - Local 请求：返回带查询参数的 API 路径，如 `/local/u?q={base64}`

#### 3.4.2 WebDAV 集成方案

修改 `common/localMusic.py`：

```python
# 在文件顶部添加导入
from common import webdav_cache

# 修改 hasMusic 函数
def hasMusic(name):
    """检查音乐文件是否存在（包括 WebDAV）"""
    logger.debug(f"[hasMusic] 检查音乐文件是否存在: {name}")
    
    # 先检查 WebDAV
    if config.read_config('common.webdav_cache.enable'):
        # Local 文件在 WebDAV 中的存储格式：local_<filename>_<quality>
        # 由于 local 文件没有质量概念，可以使用特殊标记如 'default'
        webdav_url = webdav_cache.find_webdav_cached_file('local', name, 'default')
        if webdav_url:
            logger.debug(f"[hasMusic] 在 WebDAV 中找到文件: {name}")
            return True
    
    # 使用现有的本地文件查找逻辑
    audio_info = _find_in_map(name)
    if audio_info is not None:
        logger.debug(f"[hasMusic] 在本地找到音乐文件: {name}")
        return True
    
    return False

# 修改 checkLocalMusic 函数
def checkLocalMusic(name):
    """检查指定文件名的音频、封面、歌词是否存在"""
    logger.debug(f"[checkLocalMusic] 开始检查音乐资源: {name}")
    
    # 先检查 WebDAV
    if config.read_config('common.webdav_cache.enable'):
        webdav_file = webdav_cache.find_webdav_cached_file('local', name, 'default')
        webdav_cover = webdav_cache.find_webdav_cached_file('local', f"{name}_cover", 'jpg')
        webdav_lyric = webdav_cache.find_webdav_cached_file('local', f"{name}_lyric", 'lrc')
        
        if webdav_file:
            return {
                'file': bool(webdav_file),
                'cover': bool(webdav_cover),
                'lyric': bool(webdav_lyric)
            }
    
    # 继续现有的本地检查逻辑
    w = _find_in_map(name)
    # ... 现有代码 ...

# 修改音频文件响应函数
async def generateAudioFileResonse(name):
    """生成音频文件响应"""
    logger.debug(f"[generateAudioFileResonse] 开始处理音频文件请求: {name}")
    
    # 先检查 WebDAV
    if config.read_config('common.webdav_cache.enable'):
        webdav_url = webdav_cache.find_webdav_cached_file('local', name, 'default')
        if webdav_url:
            logger.debug(f"[generateAudioFileResonse] 命中 WebDAV 缓存")
            # 如果需要代理，返回代理 URL
            if config.read_config('common.webdav_cache.proxy_auth') and not config.read_config('common.webdav_cache.direct_url'):
                # 返回重定向到 WebDAV 代理
                return aiohttp.web.Response(
                    status=302,
                    headers={'Location': f'/webdav/local/{name}/default'}
                )
            else:
                # 直接重定向到 WebDAV URL
                return aiohttp.web.Response(
                    status=302,
                    headers={'Location': webdav_url}
                )
    
    # 继续现有的本地文件处理逻辑
    # ... 现有代码 ...

# 类似地修改封面和歌词响应函数
async def generateAudioCoverResonse(name):
    """根据文件名返回封面图文件流"""
    logger.debug(f"[generateAudioCoverResonse] 开始处理音频封面请求: {name}")
    
    # 先检查 WebDAV
    if config.read_config('common.webdav_cache.enable'):
        webdav_url = webdav_cache.find_webdav_cached_file('local', f"{name}_cover", 'jpg')
        if webdav_url:
            logger.debug(f"[generateAudioCoverResonse] 命中 WebDAV 缓存")
            if config.read_config('common.webdav_cache.proxy_auth') and not config.read_config('common.webdav_cache.direct_url'):
                return aiohttp.web.Response(
                    status=302,
                    headers={'Location': f'/webdav/local/{name}_cover/jpg'}
                )
            else:
                return aiohttp.web.Response(
                    status=302,
                    headers={'Location': webdav_url}
                )
    
    # 继续现有逻辑
    # ... 现有代码 ...

async def generateAudioLyricResponse(name):
    """根据文件名返回歌词文本"""
    logger.debug(f"[generateAudioLyricResponse] 开始处理歌词请求: {name}")
    
    # 先检查 WebDAV
    if config.read_config('common.webdav_cache.enable'):
        webdav_url = webdav_cache.find_webdav_cached_file('local', f"{name}_lyric", 'lrc')
        if webdav_url:
            logger.debug(f"[generateAudioLyricResponse] 命中 WebDAV 缓存")
            # 歌词需要返回文本内容，不能直接重定向
            # 需要通过代理获取内容
            webdav_config = config.read_config('common.webdav_cache')
            headers = {}
            if webdav_config.get('username') and webdav_config.get('password'):
                import base64
                credentials = base64.b64encode(f"{webdav_config['username']}:{webdav_config['password']}".encode()).decode()
                headers['Authorization'] = f"Basic {credentials}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(webdav_url, headers=headers, ssl=webdav_config.get('ssl_verify', True)) as resp:
                    if resp.status == 200:
                        lyric_content = await resp.text()
                        return lyric_content
    
    # 继续现有逻辑
    # ... 现有代码 ...
```

#### 3.4.3 WebDAV 索引的特殊处理

修改 `webdav_cache.py` 以支持 local 文件的特殊命名：

```python
async def _scan_webdav_directory(client, path, depth):
    """递归扫描 WebDAV 目录"""
    if depth > 5:  # 限制递归深度
        return
    
    try:
        files = await client.list_directory(path)
        
        for filename in files:
            # 解析文件名格式
            name_no_ext, ext = os.path.splitext(filename)
            
            # 处理标准格式: <source>_<songId>_<quality>.<ext>
            parts = name_no_ext.split('_')
            
            if len(parts) >= 3:
                source = parts[0]
                quality = parts[-1]
                song_id = '_'.join(parts[1:-1])
                
                # 生成访问 URL
                file_path = f"{path}/{filename}".lstrip('/')
                url = client.generate_url(file_path)
                
                async with _webdav_index_lock:
                    _webdav_index[(source, song_id)][quality] = url
                
                logger.debug(f"索引 WebDAV 文件: {source}_{song_id}_{quality} -> {url}")
            
            # 特殊处理 local 文件（可能没有标准格式）
            elif parts[0] == 'local' and len(parts) >= 2:
                # local 文件格式更灵活，可能是 local_filename 或其他格式
                source = 'local'
                song_id = '_'.join(parts[1:])  # 除了 'local' 前缀外的所有部分
                quality = 'default'  # local 文件没有质量概念
                
                file_path = f"{path}/{filename}".lstrip('/')
                url = client.generate_url(file_path)
                
                async with _webdav_index_lock:
                    _webdav_index[(source, song_id)][quality] = url
                
                # 检查是否是封面或歌词
                if song_id.endswith('_cover'):
                    _webdav_index[(source, song_id)]['jpg'] = url
                elif song_id.endswith('_lyric'):
                    _webdav_index[(source, song_id)]['lrc'] = url
                
                logger.debug(f"索引 Local WebDAV 文件: {source}_{song_id} -> {url}")
                
    except Exception as e:
        logger.warning(f"扫描 WebDAV 目录 {path} 失败: {e}")
```

## 4. 前端脚本兼容性

### 4.1 URL 请求的兼容性

对于普通的 URL 请求（非 local 源），现有的前端脚本模板能够正确处理 WebDAV URL：

1. `RETURN_URL_PROCESSING` 逻辑会检查返回的 URL 是否以 'http' 开头
2. 如果是完整的 HTTP/HTTPS URL（WebDAV URL），会直接使用
3. 如果是相对路径（如 `/webdav/...`），会拼接 API_URL

### 4.2 Local 请求的特殊处理

Local 请求的前端处理完全不同，它不使用 `RETURN_URL_PROCESSING`：

1. **音频 URL**：
   - 前端直接构造 `/local/u?q={base64}` 格式的 URL
   - 不经过 URL 处理逻辑，直接作为相对路径使用
   - WebDAV 集成通过 302 重定向实现，对前端透明

2. **封面 URL**：
   - 前端构造 `/local/p?q={base64}` 格式的 URL
   - 同样通过 302 重定向到 WebDAV URL

3. **歌词处理**：
   - 前端请求 `/local/l?q={base64}`，期望返回歌词文本
   - WebDAV 集成需要在服务端获取内容后返回，不能使用重定向

因此，Local 请求的 WebDAV 集成对前端完全透明，无需修改前端脚本模板。

## 5. URL 编码处理

WebDAV URL 编码需要特别注意：

1. 文件路径使用 `urllib.parse.quote()` 进行编码，保留 `/` 字符
2. 解析 WebDAV 响应时使用 `urllib.parse.unquote()` 解码
3. 中文文件名和特殊字符会被正确处理

## 6. 性能优化

1. **启动时索引构建**：避免每次请求都访问 WebDAV 服务器
2. **内存索引**：使用与本地缓存相同的索引结构，查找时间复杂度 O(1)
3. **定期刷新**：通过配置的间隔定期更新索引，确保新文件被发现
4. **并发控制**：使用锁保护索引的并发访问

## 7. 错误处理

1. WebDAV 服务器不可用时，自动降级到本地缓存
2. 认证失败时记录日志，不影响其他缓存层
3. 索引构建失败不影响服务启动

## 8. 安全考虑

1. **认证信息保护**：
   - 默认不在 URL 中包含认证信息
   - 通过服务器代理添加认证头
   - 可选的直接 URL 模式（包含认证信息）

2. **SSL 验证**：
   - 支持配置是否验证 SSL 证书
   - 适用于自签名证书的 WebDAV 服务器

## 9. 测试建议

1. 使用 Nextcloud、ownCloud 或其他 WebDAV 服务器进行测试
2. 测试各种文件名编码情况（中文、空格、特殊字符）
3. 测试认证失败和网络错误的降级处理
4. 验证索引刷新机制

## 10. 部署步骤

1. 更新配置文件，添加 WebDAV 配置
2. 创建 `common/webdav_cache.py` 文件
3. 修改 `modules/__init__.py` 和 `main.py`
4. 重启服务器，验证 WebDAV 索引构建
5. 测试音乐播放功能

## 11. WebDAV 目录结构

WebDAV 缓存采用与本地完全相同的目录结构和文件命名，便于直接迁移：

### 11.1 音频缓存目录 (`/cache_audio`)
- 存储格式：`<source>_<songId>_<quality>.<ext>`
- 封面格式：`<source>_<songId>_cover.jpg`
- 示例：
  - `kg_abc123_320k.mp3`
  - `kg_abc123_cover.jpg`

### 11.2 本地音乐目录 (`/audio`)
- 直接存储原始音频文件
- 保持原始文件名
- 示例：
  - `我的音乐.mp3`
  - `My Song.flac`

### 11.3 临时文件目录 (`/temp`)
- 存储本地音乐的元数据缓存：`meta.json`
- 存储提取的封面文件：`<md5>_cover.jpg`

这种结构允许直接将本地的 `cache_audio`、`audio` 和 `temp` 目录上传到 WebDAV 服务器，无需任何转换。

## 12. 未来扩展

1. 支持多个 WebDAV 服务器
2. 支持 WebDAV 服务器的负载均衡
3. 添加 WebDAV 文件上传功能（可选）
4. 支持 WebDAV 服务器的健康检查
5. 支持 WebDAV 目录的递归扫描和子目录组织