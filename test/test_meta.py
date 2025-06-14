from mutagen import File

# 缓存文件的绝对或相对路径
path = r'../cache_audio/wy_2054047081_flac.flac'

audio = File(path)
print('== Tags ==')
for k, v in audio.tags.items():
    print(k, v)