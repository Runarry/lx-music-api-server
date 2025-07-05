# ----------------------------------------
# - mode: python -
# - author: helloplhm-qwq -
# - name: localMusic.py -
# - project: lx-music-api-server -
# - license: MIT -
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

import platform
import subprocess
import sys
from PIL import Image
import aiohttp
from common.utils import createFileMD5, createMD5, timeLengthFormat
from . import log, config
import ujson as json
import traceback
import mutagen
import os

logger = log.log('local_music_handler')

audios = []
map = {}
AUDIO_PATH = config.read_config("common.local_music.audio_path")
TEMP_PATH = config.read_config("common.local_music.temp_path")
FFMPEG_PATH = None

def convertCover(input_bytes):
    if (input_bytes.startswith(b'\xff\xd8\xff\xe0')): # jpg object do not need convert
        return input_bytes
    temp = TEMP_PATH + '/' + createMD5(input_bytes) + '.img'
    with open(temp, 'wb') as f:
        f.write(input_bytes)
        f.close()
    img = Image.open(temp)
    img = img.convert('RGB')
    with open(temp + 'crt', 'wb') as f:
        img.save(f, format='JPEG')
        f.close()
    data = None
    with open(temp + 'crt', 'rb') as f:
        data = f.read()
        f.close()
    try:
        os.remove(temp)
    except:
        pass
    try:
        os.remove(temp + 'crt')
    except:
        pass
    return data

def check_ffmpeg():
    logger.info('正在检查ffmpeg')
    devnull = open(os.devnull, 'w')
    linux_bin_path = '/usr/bin/ffmpeg'
    environ_ffpmeg_path = os.environ.get('FFMPEG_PATH')
    if (platform.system() == 'Windows' or platform.system() == 'Cygwin'):
        if (environ_ffpmeg_path and (not environ_ffpmeg_path.endswith('.exe'))):
            environ_ffpmeg_path += '/ffmpeg.exe'
    else:
        if (environ_ffpmeg_path and os.path.isdir(environ_ffpmeg_path)):
            environ_ffpmeg_path += '/ffmpeg'

    if (environ_ffpmeg_path):
        try:
            subprocess.Popen([environ_ffpmeg_path, '-version'], stdout=devnull, stderr=devnull)
            devnull.close()
            return environ_ffpmeg_path
        except:
            pass

    if (os.path.isfile(linux_bin_path)):
        try:
            subprocess.Popen([linux_bin_path, '-version'], stdout=devnull, stderr=devnull)
            devnull.close()
            return linux_bin_path
        except:
            pass

    try: 
        subprocess.Popen(['ffmpeg', '-version'], stdout=devnull, stderr=devnull)
        return 'ffmpeg'
    except:
        logger.warning('无法找到ffmpeg，对于本地音乐的一些扩展功能无法使用，如果您不需要，请忽略本条提示')
        logger.warning('如果您已经安装，请将 FFMPEG_PATH 环境变量设置为您的ffmpeg安装路径或者将其添加到PATH中')
        return None

def getAudioCoverFromFFMpeg(path):
    if (not FFMPEG_PATH):
        return None
    cmd = [FFMPEG_PATH, '-i', path, TEMP_PATH + '/_tmp.jpg']
    popen = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stdout)
    popen.wait()
    if (os.path.exists(TEMP_PATH + '/_tmp.jpg')):
        with open(TEMP_PATH + '/_tmp.jpg', 'rb') as f:
            data = f.read()
            f.close()
        try:
            os.remove(TEMP_PATH + '/_tmp.jpg')
        except:
            pass
        return data

def readFileCheckCover(path):
    with open(path, 'rb') as f: # read the first 1MB audio
        data = f.read(1024 * 1024)
        return b'image/' in data

def checkLyricValid(lyric_content):
    if (lyric_content is None):
        return False
    if (lyric_content == ''):
        return False
    lines = lyric_content.split('\n')
    for line in lines:
        line = line.strip()
        if (line == ''):
            continue
        if (line.startswith('[')):
            continue
        if (not line.startswith('[')):
            return False
    return True

def filterLyricLine(lyric_content: str) -> str:
    lines = lyric_content.split('\n')
    completed = []
    for line in lines:
        line = line.strip()
        if (line.startswith('[')):
            completed.append(line)
        continue
    return '\n'.join(completed)

def getAudioMeta(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        audio = mutagen.File(filepath)
        if not audio:
            return None
        logger.debug(audio.items())
        if (filepath.lower().endswith('.mp3')):
            cover = audio.get('APIC:')
            if (cover):
                cover = convertCover(cover.data)
            lrc_key = None
            for k in list(audio.keys()):
                if (k.startswith('USLT')):
                    lrc_key = k
                    break
            title = audio.get('TIT2')
            artist = audio.get('TPE1')
            album = audio.get('TALB')
            if (lrc_key):
                lyric = audio.get(lrc_key)
            else:
                lyric = None
            if (title):
                title = title.text
            if (artist):
                artist = artist.text
            if (album):
                album = album.text
            if (lyric):
                lyric = [lyric.text]
            if (not lyric):
                if (os.path.isfile(os.path.splitext(filepath)[0] + '.lrc')):
                    with open(os.path.splitext(filepath)[0] + '.lrc', 'r', encoding='utf-8') as f:
                        t = f.read().replace('\ufeff', '')
                        logger.debug(t)
                        lyric = filterLyricLine(t)
                        logger.debug(lyric)
                        if (not checkLyricValid(lyric)):
                            lyric = [None]
                        else:
                            lyric = [lyric]
                        f.close()
                else:
                    lyric = [None]
        else:
            cover = audio.get('cover')
            if (cover):
                cover = convertCover(cover[0])
            else:
                if (readFileCheckCover(filepath)):
                    cover = getAudioCoverFromFFMpeg(filepath)
                else:
                    cover = None
            title = audio.get('title')
            artist = audio.get('artist')
            album = audio.get('album')
            lyric = audio.get('lyrics')
            if (not lyric):
                if (os.path.isfile(os.path.splitext(filepath)[0] + '.lrc')):
                    with open(os.path.splitext(filepath)[0] + '.lrc', 'r', encoding='utf-8') as f:
                        lyric = filterLyricLine(f.read())
                        if (not checkLyricValid(lyric)):
                            lyric = [None]
                        else:
                            lyric = [lyric]
                        f.close()
                else:
                    lyric = [None]
        return {
            "filepath": filepath,
            "title": title[0] if title else '',
            "artist": '、'.join(artist) if artist else '',
            "album": album[0] if album else '',
            "cover_path": extractCover({
                "filepath": filepath,
                "cover": cover,
            }, TEMP_PATH),
            "lyrics": lyric[0],
            'length': audio.info.length,
            'format_length': timeLengthFormat(audio.info.length),
            'md5': createFileMD5(filepath),
        }
    except:
        logger.error(f"get audio meta error: {filepath}")
        logger.error(traceback.format_exc())
        return None

def checkAudioValid(path):
    if not os.path.exists(path):
        return False
    try:
        audio = mutagen.File(path)
        if not audio:
            return False
        return True
    except:
        logger.error(f"check audio valid error: {path}")
        logger.error(traceback.format_exc())
        return False

def extractCover(audio_info, temp_path):
    if (not audio_info['cover']):
        return None
    path = os.path.join(temp_path + '/' + createMD5(audio_info['filepath']) + '_cover.jpg')
    with open(path, 'wb') as f:
        f.write(audio_info['cover'])
    return path

def findAudios(cache):

    available_exts = [
        'mp3',
        'wav',
        'flac',
        'ogg',
        'm4a',
    ]
    
    files = os.listdir(AUDIO_PATH)
    if (files == []): 
        return []
    
    audios = []
    _map = {}
    for c in cache:
        _map[c['filepath']] = c
    for file in files:
        if (not file.endswith(tuple(available_exts))):
            continue
        path = os.path.join(AUDIO_PATH, file)
        if (not checkAudioValid(path)):
            continue
        logger.info(f"found audio: {path}")
        if (not (_map.get(path) and _map[path]['md5'] == createFileMD5(path))):
            meta = getAudioMeta(path)
            audios = audios + [meta]
        else:
            audios = audios + [_map[path]]
    
    return audios

def getAudioCover(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        audio = mutagen.File(filepath)
        if not audio:
            return None
        if (filepath.lower().endswith('mp3')):
            return audio.get('APIC:').data
        else:
            if (readFileCheckCover(filepath)):
                return getAudioCoverFromFFMpeg(filepath)
            else:
                return None
        
    except:
        logger.error(f"get audio cover error: {filepath}")
        logger.error(traceback.format_exc())
        return None

def writeAudioCover(filepath):
    s = getAudioCover(filepath)
    path = os.path.join(TEMP_PATH + '/' + createMD5(filepath) + '_cover.jpg')
    with open(path, 'wb') as f:
        f.write(s)
        f.close()
    return path

def writeLocalCache(audios):
    with open(TEMP_PATH + '/meta.json', 'w', encoding='utf-8') as f:
        f.write(json.dumps({
            "file_list": os.listdir(AUDIO_PATH),
            "audios": audios
        }, ensure_ascii = False, indent = 2))
        f.close()

def dumpLocalCache():
    try:
        TEMP_PATH = config.read_config("common.local_music.temp_path")
        with open(TEMP_PATH + '/meta.json', 'r', encoding='utf-8') as f:
            d = json.loads(f.read())
        return d
    except:
        return {
            "file_list": [],
            "audios": []
        }

def initMain():
    global FFMPEG_PATH
    FFMPEG_PATH = check_ffmpeg()
    logger.debug('[initMain] 找到的ffmpeg命令: ' + str(FFMPEG_PATH))
    if (not os.path.exists(AUDIO_PATH)):
        os.mkdir(AUDIO_PATH)
        logger.info(f"[initMain] 创建本地音乐文件夹 {AUDIO_PATH}")
    if (not os.path.exists(TEMP_PATH)):
        os.mkdir(TEMP_PATH)
        logger.info(f"[initMain] 创建本地音乐临时文件夹 {TEMP_PATH}")
    global audios
    cache = dumpLocalCache()
    if (cache['file_list'] == os.listdir(AUDIO_PATH)):
        logger.debug(f"[initMain] 文件列表未变化，使用缓存数据")
        audios = cache['audios']
    else:
        logger.debug(f"[initMain] 文件列表已变化，重新扫描音频文件")
        audios = findAudios(cache['audios'])
        writeLocalCache(audios)
    
    # 清空map以防止旧数据干扰
    global map
    original_map_size = len(map) if map else 0
    logger.debug(f"[initMain] 清空map前的大小: {original_map_size}")
    map = {}
    
    # 使用规范化的文件名构建map
    normalized_count = 0
    variations_count = 0
    
    for a in audios:
        original_filename = os.path.basename(a['filepath'])
        normalized_filename = normalize_filename(original_filename)
        normalized_count += 1
        
        # 存储多个变体以提高跨平台兼容性
        # 1. 规范化后的文件名
        map[normalized_filename] = a
        
        # 2. 小写版本（解决大小写敏感问题）
        lowercase_name = normalized_filename.lower()
        if lowercase_name != normalized_filename:
            map[lowercase_name] = a
            variations_count += 1
        
        # 3. 原始文件名（未规范化）
        if original_filename != normalized_filename:
            map[original_filename] = a
            variations_count += 1
            logger.debug(f"[initMain] 文件名规范化: {original_filename} -> {normalized_filename}")
        
        # 4. 原始文件名的小写版本
        original_lowercase = original_filename.lower()
        if original_lowercase != original_filename and original_lowercase != lowercase_name:
            map[original_lowercase] = a
            variations_count += 1
    
    logger.info(f"[initMain] 初始化本地音乐成功，共 {len(audios)} 个音频文件，生成 {len(map)} 个索引项")
    logger.debug(f"[initMain] 规范化 {normalized_count} 个文件名，添加 {variations_count} 个变体索引")
    
    # 输出部分map键以便调试
    map_keys = list(map.keys())[:5] if len(map) > 5 else list(map.keys())
    logger.debug(f"[initMain] map中的前5个键: {map_keys}")
    logger.debug(f'[initMain] 本地音乐列表: {audios[:2] if len(audios) > 2 else audios}')
    logger.debug(f'[initMain] 本地音乐map样例: {dict(list(map.items())[:2]) if len(map) > 2 else map}')

async def generateAudioFileResonse(name):
    """
    生成音频文件响应
    """
    logger.debug(f"[generateAudioFileResonse] 开始处理音频文件请求: {name}")
    
    try:
        # 使用辅助函数查找文件
        audio_info = _find_in_map(name)
        
        if audio_info is None:
            logger.warning(f"未在map中找到文件: {name}")
            return {
                'code': 2,
                'msg': '未找到文件',
                'data': None
            }, 404
        
        # 检查文件是否存在
        filepath = audio_info.get('filepath')
        logger.debug(f"[generateAudioFileResonse] 获取到文件路径: {filepath}")
        
        if not filepath:
            logger.error(f"[generateAudioFileResonse] 文件路径为空")
            return {
                'code': 2,
                'msg': '文件路径无效',
                'data': None
            }, 404
        
        if not os.path.exists(filepath):
            logger.warning(f"文件不存在: {filepath}")
            return {
                'code': 2,
                'msg': '文件不存在或无法访问',
                'data': None
            }, 404
            
        # 检查文件是否可读
        if not os.access(filepath, os.R_OK):
            logger.warning(f"文件无法读取: {filepath}")
            return {
                'code': 2,
                'msg': '文件无法读取',
                'data': None
            }, 403
        
        # 返回文件响应
        logger.debug(f"[generateAudioFileResonse] 返回文件响应: {filepath}")
        return aiohttp.web.FileResponse(filepath)
    except (KeyError, TypeError) as e:
        logger.error(f"获取音频文件时出现KeyError或TypeError: {str(e)}")
        return {
            'code': 2,
            'msg': '未找到文件',
            'data': None
        }, 404
    except FileNotFoundError as e:
        logger.error(f"获取音频文件时出现FileNotFoundError: {str(e)}")
        return {
            'code': 2,
            'msg': '文件不存在',
            'data': None
        }, 404
    except PermissionError as e:
        logger.error(f"获取音频文件时出现PermissionError: {str(e)}")
        return {
            'code': 2,
            'msg': '无权限访问文件',
            'data': None
        }, 403
    except OSError as e:
        logger.error(f"获取音频文件时出现OSError: {str(e)}")
        return {
            'code': 2,
            'msg': '文件系统错误',
            'data': None
        }, 500
    except Exception as e:
        logger.error(f"获取音频文件时出现未知错误: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'code': 2,
            'msg': '服务器内部错误',
            'data': None
        }, 500

async def generateAudioCoverResonse(name):
    """根据文件名返回封面图文件流"""
    logger.debug(f"[generateAudioCoverResonse] 开始处理音频封面请求: {name}")
    
    try:
        # 使用辅助函数查找文件
        w = _find_in_map(name)
        
        # 检查是否找到文件信息
        if w is None:
            logger.warning(f"[generateAudioCoverResonse] 未在map中找到文件: {name}")
            return {
                'code': 2,
                'msg': '未找到封面',
                'data': None
            }, 404
        
        logger.debug(f"[generateAudioCoverResonse] 在map中找到文件信息: {w.get('filepath')}")
        
        # 检查音频文件是否存在
        if not os.path.exists(w['filepath']):
            logger.warning(f"[generateAudioCoverResonse] 音频文件不存在: {w['filepath']}")
            return {
                'code': 2,
                'msg': '音频文件不存在或无法访问',
                'data': None
            }, 404
        
        logger.debug(f"[generateAudioCoverResonse] 音频文件存在: {w['filepath']}")
        
        # 检查封面是否存在，不存在则尝试生成
        has_cover = w.get('cover_path') and os.path.exists(w['cover_path'])
        logger.debug(f"[generateAudioCoverResonse] 封面路径: {w.get('cover_path')}, 是否存在: {has_cover}")
        
        if not has_cover:
            logger.debug(f"[generateAudioCoverResonse] 封面不存在，尝试生成: {w['filepath']}")
            try:
                p = writeAudioCover(w['filepath'])
                logger.debug(f"[generateAudioCoverResonse] 生成封面结果: {p}")
                
                if p and os.path.exists(p):
                    logger.debug(f"[generateAudioCoverResonse] 生成音乐封面文件成功: {p}")
                    # 更新map中的封面路径
                    w['cover_path'] = p
                    logger.debug(f"[generateAudioCoverResonse] 更新map中的封面路径: {p}")
                    return aiohttp.web.FileResponse(p)
                else:
                    logger.warning(f"[generateAudioCoverResonse] 生成音乐封面文件失败: {w['filepath']}")
                    return {
                        'code': 2,
                        'msg': '无法生成封面',
                        'data': None
                    }, 404
            except Exception as e:
                logger.error(f"[generateAudioCoverResonse] 生成封面时出错: {str(e)}")
                logger.error(traceback.format_exc())
                return {
                    'code': 2,
                    'msg': '生成封面时出错',
                    'data': None
                }, 500
        
        # 检查封面文件是否可读
        if not os.access(w['cover_path'], os.R_OK):
            logger.warning(f"[generateAudioCoverResonse] 封面文件无法读取: {w['cover_path']}")
            return {
                'code': 2,
                'msg': '封面文件无法读取',
                'data': None
            }, 403
        
        # 返回封面文件响应
        logger.debug(f"[generateAudioCoverResonse] 返回封面文件响应: {w['cover_path']}")
        return aiohttp.web.FileResponse(w['cover_path'])
    except (KeyError, TypeError) as e:
        logger.error(f"[generateAudioCoverResonse] 获取封面时出现KeyError或TypeError: {str(e)}")
        import traceback
        logger.debug(f"[generateAudioCoverResonse] 错误详细信息: {traceback.format_exc()}")
        return {
            'code': 2,
            'msg': '未找到封面',
            'data': None
        }, 404
    except FileNotFoundError as e:
        logger.error(f"获取封面时出现FileNotFoundError: {str(e)}")
        return {
            'code': 2,
            'msg': '封面文件不存在',
            'data': None
        }, 404
    except PermissionError as e:
        logger.error(f"获取封面时出现PermissionError: {str(e)}")
        return {
            'code': 2,
            'msg': '无权限访问封面文件',
            'data': None
        }, 403
    except OSError as e:
        logger.error(f"获取封面时出现OSError: {str(e)}")
        return {
            'code': 2,
            'msg': '文件系统错误',
            'data': None
        }, 500
    except Exception as e:
        logger.error(f"获取封面时出现未知错误: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            'code': 2,
            'msg': '服务器内部错误',
            'data': None
        }, 500

async def generateAudioLyricResponse(name):
    """根据文件名返回歌词文本"""
    logger.debug(f"[generateAudioLyricResponse] 开始处理歌词请求: {name}")
    
    try:
        # 使用辅助函数查找文件
        w = _find_in_map(name)
        
        # 检查是否找到文件信息
        if w is None:
            logger.warning(f"[generateAudioLyricResponse] 未在map中找到文件: {name}")
            return {
                'code': 2,
                'msg': '未找到歌词',
                'data': None
            }, 404
        
        logger.debug(f"[generateAudioLyricResponse] 在map中找到文件信息: {w.get('filepath')}")
        
        # 检查音频文件是否存在
        if not os.path.exists(w['filepath']):
            logger.warning(f"[generateAudioLyricResponse] 音频文件不存在: {w['filepath']}")
            return {
                'code': 2,
                'msg': '音频文件不存在或无法访问',
                'data': None
            }, 404
        
        logger.debug(f"[generateAudioLyricResponse] 音频文件存在: {w['filepath']}")
        
        # 检查歌词是否存在
        has_lyrics = bool(w.get('lyrics'))
        logger.debug(f"[generateAudioLyricResponse] 歌词是否存在: {has_lyrics}")
        
        if not has_lyrics:
            logger.warning(f"[generateAudioLyricResponse] 歌词不存在: {name}")
            return {
                'code': 2,
                'msg': '未找到歌词',
                'data': None
            }, 404
        
        # 返回歌词文本
        lyrics_length = len(w['lyrics']) if isinstance(w['lyrics'], str) else 'non-string'
        logger.debug(f"[generateAudioLyricResponse] 返回歌词，长度: {lyrics_length}")
        return w['lyrics']
    except (KeyError, TypeError) as e:
        logger.error(f"[generateAudioLyricResponse] 获取歌词时出现KeyError或TypeError: {str(e)}")
        import traceback
        logger.debug(f"[generateAudioLyricResponse] 错误详细信息: {traceback.format_exc()}")
        return {
            'code': 2,
            'msg': '未找到歌词',
            'data': None
        }, 404
    except Exception as e:
        logger.error(f"[generateAudioLyricResponse] 获取歌词时出现未知错误: {str(e)}")
        import traceback
        logger.debug(f"[generateAudioLyricResponse] 未知错误详细信息: {traceback.format_exc()}")
        return {
            'code': 2,
            'msg': '服务器内部错误',
            'data': None
        }, 500

def checkLocalMusic(name):
    """检查指定文件名的音频、封面、歌词是否存在"""
    logger.debug(f"[checkLocalMusic] 开始检查音乐资源: {name}")
    
    try:
        # 使用辅助函数查找文件
        w = _find_in_map(name)
        
        if w is None:
            # 文件名本身未收录，则全部视为不存在
            logger.debug(f"[checkLocalMusic] 未在map中找到文件: {name}，返回全部不存在")
            return {
                'file': False,
                'cover': False,
                'lyric': False
            }
        
        logger.debug(f"[checkLocalMusic] 在map中找到文件信息: {w.get('filepath')}")
        
        # 检查文件是否存在并可读
        file_path = w.get('filepath', '')
        file_exists = file_path and os.path.exists(file_path) and os.access(file_path, os.R_OK)
        logger.debug(f"[checkLocalMusic] 音频文件路径: {file_path}, 是否存在并可读: {file_exists}")
        
        # 检查封面是否存在并可读
        cover_path = w.get('cover_path', '')
        cover_exists = cover_path and os.path.exists(cover_path) and os.access(cover_path, os.R_OK)
        logger.debug(f"[checkLocalMusic] 封面文件路径: {cover_path}, 是否存在并可读: {cover_exists}")
        
        # 检查歌词是否存在
        lyrics = w.get('lyrics')
        lyric_exists = bool(lyrics)
        logger.debug(f"[checkLocalMusic] 歌词是否存在: {lyric_exists}, 歌词长度: {len(lyrics) if lyrics and isinstance(lyrics, str) else 'N/A'}")
        
        result = {
            'file': file_exists,
            'cover': cover_exists,
            'lyric': lyric_exists
        }
        logger.debug(f"[checkLocalMusic] 检查结果: {result}")
        return result
    except Exception as e:
        logger.error(f"[checkLocalMusic] 检查音乐文件时出现错误: {str(e)}")
        import traceback
        logger.debug(f"[checkLocalMusic] 错误详细信息: {traceback.format_exc()}")
        return {
            'file': False,
            'cover': False,
            'lyric': False
        }

def normalize_filename(filename):
    """
    规范化文件名，确保在不同平台上一致处理文件名
    
    处理内容：
    1. 提取文件名（如果是路径）
    2. 处理路径分隔符（统一使用正斜杠）
    3. URL解码（处理%编码的字符）
    4. Unicode规范化（使用NFC形式，解决中文等字符在不同系统上的表示差异）
    5. 处理空白字符（统一处理空格、制表符等）
    6. 去除文件名两端的空白字符
    """
    logger.debug(f"[normalize_filename] 开始处理文件名: {filename}")
    
    # 处理路径分隔符 - 将反斜杠转换为正斜杠
    original_filename = filename
    filename = filename.replace('\\', '/')
    if original_filename != filename:
        logger.debug(f"[normalize_filename] 路径分隔符转换: {original_filename} -> {filename}")
    
    # 提取文件名（如果是路径）
    filename = os.path.basename(filename)
    if original_filename != filename:
        logger.debug(f"[normalize_filename] 提取文件名: {original_filename} -> {filename}")
    
    # URL解码（处理%编码的字符）- 多次解码以处理双重编码
    max_decode_attempts = 3
    for i in range(max_decode_attempts):
        try:
            from urllib.parse import unquote
            decoded_filename = unquote(filename, encoding='utf-8', errors='strict')
            if decoded_filename == filename:
                break
            logger.debug(f"[normalize_filename] URL解码第{i+1}次: {filename} -> {decoded_filename}")
            filename = decoded_filename
        except Exception as e:
            logger.warning(f"[normalize_filename] URL解码文件名失败(第{i+1}次): {filename}, 错误: {str(e)}")
            break
    
    # Unicode规范化（使用NFC形式）
    try:
        import unicodedata
        # 先尝试NFD再转NFC，确保一致性
        filename_nfd = unicodedata.normalize('NFD', filename)
        normalized_filename = unicodedata.normalize('NFC', filename_nfd)
        if normalized_filename != filename:
            logger.debug(f"[normalize_filename] Unicode规范化: {filename} -> {normalized_filename}")
            # 输出十六进制表示，便于调试Unicode差异
            logger.debug(f"[normalize_filename] Unicode十六进制 - 原始: {' '.join([hex(ord(c)) for c in filename[:20]])}")
            logger.debug(f"[normalize_filename] Unicode十六进制 - 规范化: {' '.join([hex(ord(c)) for c in normalized_filename[:20]])}")
        filename = normalized_filename
    except Exception as e:
        logger.warning(f"[normalize_filename] Unicode规范化文件名失败: {filename}, 错误: {str(e)}")
    
    # 处理空白字符（统一多个空格为单个空格）
    filename = ' '.join(filename.split())
    
    # 去除文件名两端的空白字符
    filename = filename.strip()
    
    # 处理Windows文件名末尾的点和空格（Windows会自动删除）
    while filename.endswith('.') or filename.endswith(' '):
        old_filename = filename
        filename = filename.rstrip('. ')
        if old_filename != filename:
            logger.debug(f"[normalize_filename] 去除末尾点和空格: {old_filename} -> {filename}")
    
    logger.debug(f"[normalize_filename] 规范化完成: {original_filename} -> {filename}")
    return filename

def _find_in_map(name):
    """
    在 map 中查找文件，尝试多种匹配策略
    返回找到的音频信息，如果未找到返回 None
    """
    if not name:
        logger.debug(f"[_find_in_map] 文件名为空")
        return None
        
    logger.debug(f"[_find_in_map] 开始查找文件: {name}")
    
    # 尝试多种变体
    variations = []
    
    # 1. 原始文件名
    variations.append(name)
    
    # 2. 规范化文件名
    normalized_name = normalize_filename(name)
    if normalized_name != name:
        variations.append(normalized_name)
    
    # 3. 小写版本
    variations.append(name.lower())
    variations.append(normalized_name.lower())
    
    # 4. 处理可能的路径分隔符问题
    if '\\' in name:
        name_forward_slash = name.replace('\\', '/')
        variations.append(os.path.basename(name_forward_slash))
        variations.append(normalize_filename(name_forward_slash))
    
    # 5. 尝试URL解码（如果看起来像编码的）
    if '%' in name:
        try:
            from urllib.parse import unquote
            decoded = unquote(name)
            variations.append(decoded)
            variations.append(normalize_filename(decoded))
        except:
            pass
    
    # 去重
    seen = set()
    unique_variations = []
    for v in variations:
        if v not in seen and v:
            seen.add(v)
            unique_variations.append(v)
    
    # 尝试所有变体
    for variant in unique_variations:
        if variant in map:
            logger.debug(f"[_find_in_map] 匹配成功 (变体: {variant}): {name}")
            return map[variant]
    
    # 如果还是没找到，尝试模糊匹配
    logger.debug(f"[_find_in_map] 精确匹配失败，尝试模糊匹配: {name}")
    
    # 计算相似度的简单函数
    def similarity(s1, s2):
        s1 = s1.lower()
        s2 = s2.lower()
        if s1 == s2:
            return 1.0
        # 检查是否一个是另一个的子串
        if s1 in s2 or s2 in s1:
            return 0.8
        # 检查去除扩展名后是否相同
        s1_base = os.path.splitext(s1)[0]
        s2_base = os.path.splitext(s2)[0]
        if s1_base == s2_base:
            return 0.9
        return 0
    
    # 查找最相似的
    best_match = None
    best_score = 0
    for key in map.keys():
        score = similarity(normalized_name, key)
        if score > best_score:
            best_score = score
            best_match = key
    
    if best_score >= 0.8:
        logger.debug(f"[_find_in_map] 模糊匹配成功 (相似度: {best_score}, 匹配: {best_match}): {name}")
        return map[best_match]
    
    # 未找到匹配
    logger.warning(f"[_find_in_map] 未找到任何匹配: {name}")
    logger.debug(f"[_find_in_map] 尝试的变体: {unique_variations}")
    
    # 输出部分map键用于调试
    map_keys = list(map.keys())[:10] if len(map) > 10 else list(map.keys())
    logger.debug(f"[_find_in_map] map中的前10个键: {map_keys}")
    
    # 输出更详细的调试信息
    if logger.isEnabledFor(10):  # DEBUG level
        for v in unique_variations[:3]:
            logger.debug(f"[_find_in_map] 变体 '{v}' 的十六进制: {' '.join([hex(ord(c)) for c in v[:20]])}")
    
    return None

def hasMusic(name):
    """
    检查音乐文件是否存在
    """
    logger.debug(f"[hasMusic] 检查音乐文件是否存在: {name}")
    
    if not name:
        logger.debug(f"[hasMusic] 文件名为空，返回False")
        return False
    
    # 使用辅助函数查找文件
    audio_info = _find_in_map(name)
    
    if audio_info is not None:
        logger.debug(f"[hasMusic] 在map中找到音乐文件: {name}")
        return True
    else:
        logger.debug(f"[hasMusic] 未找到音乐文件: {name}")
        return False