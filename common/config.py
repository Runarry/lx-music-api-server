# ----------------------------------------
# - mode: python -
# - author: helloplhm-qwq -
# - name: config.py -
# - project: lx-music-api-server -
# - license: MIT -
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

import ujson as json
import time
import os
import traceback
import sys
import sqlite3
import shutil
import ruamel.yaml as yaml_
from . import variable
from .log import log
from . import default_config
import threading
import redis

logger = log("config_manager")

# 创建线程本地存储对象
local_data = threading.local()
local_cache = threading.local()
local_redis = threading.local()


def get_data_connection():
    return local_data.connection


def get_cache_connection():
    return local_cache.connection


def get_redis_connection():
    return local_redis.connection


def handle_connect_db():
    try:
        local_data.connection = sqlite3.connect("./config/data.db")
        if read_config("common.cache.adapter") == "redis":
            host = read_config("common.cache.redis.host")
            port = read_config("common.cache.redis.port")
            user = read_config("common.cache.redis.user")
            password = read_config("common.cache.redis.password")
            db = read_config("common.cache.redis.db")
            client = redis.Redis(host=host, port=port, username=user, password=password, db=db)
            if not client.ping():
                raise
            local_redis.connection = client
        else:
            local_cache.connection = sqlite3.connect("./cache.db")
    except:
        logger.error("连接数据库失败")
        sys.exit(1)


class ConfigReadException(Exception):
    pass


yaml = yaml_.YAML()
default_str = default_config.default
default = yaml.load(default_str)


def handle_default_config():
    with open("./config/config.yml", "w", encoding="utf-8") as f:
        f.write(default_str)
        if not os.getenv("build"):
            logger.info(
                f"首次启动或配置文件被删除，已创建默认配置文件\n建议您到{variable.workdir + os.path.sep}config.yml修改配置后重新启动服务器"
            )
        return default


class ConfigReadException(Exception):
    pass


def load_data():
    config_data = {}
    try:
        # Connect to the database
        conn = get_data_connection()
        cursor = conn.cursor()

        # Retrieve all configuration data from the 'config' table
        cursor.execute("SELECT key, value FROM data")
        rows = cursor.fetchall()

        for row in rows:
            key, value = row
            config_data[key] = json.loads(value)

    except Exception as e:
        logger.error(f"Error loading config: {str(e)}")
        logger.error(traceback.format_exc())

    return config_data


def save_data(config_data):
    try:
        # Connect to the database
        conn = get_data_connection()
        cursor = conn.cursor()

        # Clear existing data in the 'data' table
        cursor.execute("DELETE FROM data")

        # Insert the new configuration data into the 'data' table
        for key, value in config_data.items():
            cursor.execute("INSERT INTO data (key, value) VALUES (?, ?)", (key, json.dumps(value)))

        conn.commit()

    except Exception as e:
        logger.error(f"Error saving config: {str(e)}")
        logger.error(traceback.format_exc())


def handleBuildRedisKey(module, key):
    prefix = read_config("common.cache.redis.key_prefix")
    return f"{prefix}:{module}:{key}"


def getCache(module, key):
    try:
        if read_config("common.cache.adapter") == "redis":
            redis = get_redis_connection()
            key = handleBuildRedisKey(module, key)
            result = redis.get(key)
            if result:
                cache_data = json.loads(result)
                return cache_data
        else:
            # 连接到数据库（如果数据库不存在，则会自动创建）
            conn = get_cache_connection()

            # 创建一个游标对象
            cursor = conn.cursor()

            cursor.execute("SELECT data FROM cache WHERE module=? AND key=?", (module, key))

            result = cursor.fetchone()
            if result:
                cache_data = json.loads(result[0])
                cache_data["time"] = int(cache_data["time"])
                if not cache_data["expire"]:
                    return cache_data
                if int(time.time()) < int(cache_data["time"]):
                    return cache_data
    except:
        pass
        # traceback.print_exc()
    return None


def updateCache(module, key, data, expire=None):
    try:
        if read_config("common.cache.adapter") == "redis":
            redis = get_redis_connection()
            key = handleBuildRedisKey(module, key)
            redis.set(key, json.dumps(data), ex=expire if expire and expire > 0 else None)
        else:
            # 连接到数据库（如果数据库不存在，则会自动创建）
            conn = get_cache_connection()

            # 创建一个游标对象
            cursor = conn.cursor()

            cursor.execute("SELECT data FROM cache WHERE module=? AND key=?", (module, key))
            result = cursor.fetchone()
            if result:
                cursor.execute(
                    "UPDATE cache SET data = ? WHERE module = ? AND key = ?", (json.dumps(data), module, key)
                )
            else:
                cursor.execute(
                    "INSERT INTO cache (module, key, data) VALUES (?, ?, ?)", (module, key, json.dumps(data))
                )
            conn.commit()
    except:
        logger.error("缓存写入遇到错误…")
        logger.error(traceback.format_exc())


def resetRequestTime(ip):
    variable.request_time[ip] = 0


def updateRequestTime(ip):
    variable.request_time[ip] = time.time()


def getRequestTime(ip):
    return variable.request_time.get(ip, 0)


def read_data(key):
    config = load_data()
    keys = key.split(".")
    value = config
    for k in keys:
        if k not in value and keys.index(k) != len(keys) - 1:
            value[k] = {}
        elif k not in value and keys.index(k) == len(keys) - 1:
            value = None
        value = value[k]

    return value


def write_data(key, value):
    config = load_data()

    keys = key.split(".")
    current = config
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]

    current[keys[-1]] = value

    save_data(config)


def push_to_list(key, obj):
    config = load_data()

    keys = key.split(".")
    current = config
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]

    if keys[-1] not in current:
        current[keys[-1]] = []

    current[keys[-1]].append(obj)

    save_data(config)


def write_config(key, value):
    config = None
    with open("./config/config.yml", "r", encoding="utf-8") as f:
        config = yaml_.YAML().load(f)

    keys = key.split(".")
    current = config
    current_cache = variable.config
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]
        if k not in current_cache:
            current_cache[k] = {}
        current_cache = current_cache[k]

    current[keys[-1]] = value
    # 更新配置缓存
    current_cache[keys[-1]] = value

    # 设置保留注释和空行的参数
    y = yaml_.YAML()
    y.preserve_quotes = True
    y.preserve_blank_lines = True

    # 写入配置并保留注释和空行
    with open("./config/config.yml", "w", encoding="utf-8") as f:
        y.dump(config, f)


def read_default_config(key):
    try:
        config = default
        keys = key.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict):
                if k not in value and keys.index(k) != len(keys) - 1:
                    value[k] = {}
                elif k not in value and keys.index(k) == len(keys) - 1:
                    value = None
                value = value[k]
            else:
                value = None
                break

        return value
    except:
        return None


def _read_config(key):
    try:
        config = variable.config
        keys = key.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict):
                if k not in value and keys.index(k) != len(keys) - 1:
                    value[k] = None
                elif k not in value and keys.index(k) == len(keys) - 1:
                    value = None
                value = value[k]
            else:
                value = None
                break

        return value
    except (KeyError, TypeError):
        return None


def read_config(key):
    try:
        config = variable.config
        keys = key.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict):
                if k not in value and keys.index(k) != len(keys) - 1:
                    value[k] = {}
                elif k not in value and keys.index(k) == len(keys) - 1:
                    value = None
                value = value[k]
            else:
                value = None
                break

        return value
    except:
        default_value = read_default_config(key)
        if isinstance(default_value, type(None)):
            logger.warning(f"配置文件{key}不存在")
        else:
            for i in range(len(keys)):
                tk = ".".join(keys[: (i + 1)])
                tkvalue = _read_config(tk)
                logger.debug(f"configfix: 读取配置文件{tk}的值：{tkvalue}")
                if (tkvalue is None) or (tkvalue == {}):
                    write_config(tk, read_default_config(tk))
                    logger.info(f"配置文件{tk}不存在，已创建")
                    return default_value


def write_data(key, value):
    config = load_data()

    keys = key.split(".")
    current = config
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]

    current[keys[-1]] = value

    save_data(config)


def init_config():
    if not os.path.exists("./config"):
        os.mkdir("config")
        if os.path.exists("./config.json"):
            shutil.move("config.json", "./config")
        if os.path.exists("./data.db"):
            shutil.move("./data.db", "./config")
        if os.path.exists("./config/config.json"):
            os.rename("./config/config.json", "./config/config.json.bak")
            handle_default_config()
            logger.warning("json配置文件已不再使用，已将其重命名为config.json.bak")
            logger.warning("配置文件不会自动更新（因为变化太大），请手动修改配置文件重启服务器")
            sys.exit(0)

    try:
        with open("./config/config.yml", "r", encoding="utf-8") as f:
            try:
                variable.config = yaml.load(f.read())
                if not isinstance(variable.config, dict):
                    logger.warning("配置文件并不是一个有效的字典，使用默认值")
                    variable.config = default
                    with open("./config/config.yml", "w", encoding="utf-8") as f:
                        yaml.dump(variable.config, f)
                        f.close()
            except:
                if os.path.getsize("./config/config.yml") != 0:
                    logger.error("配置文件加载失败，请检查是否遵循YAML语法规范")
                    sys.exit(1)
                else:
                    variable.config = handle_default_config()
    except FileNotFoundError:
        variable.config = handle_default_config()
    # print(variable.config)
    variable.log_length_limit = read_config("common.log_length_limit")
    variable.debug_mode = read_config("common.debug_mode")
    logger.debug("配置文件加载成功")

    # 尝试连接数据库
    handle_connect_db()

    conn = sqlite3.connect("./cache.db")

    # 创建一个游标对象
    cursor = conn.cursor()

    # 创建一个表来存储缓存数据
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS cache
(id INTEGER PRIMARY KEY AUTOINCREMENT,
module TEXT NOT NULL,
key TEXT NOT NULL,
data TEXT NOT NULL)"""
    )

    conn.close()

    conn2 = sqlite3.connect("./config/data.db")

    # 创建一个游标对象
    cursor2 = conn2.cursor()

    cursor2.execute(
        """CREATE TABLE IF NOT EXISTS data
(key TEXT PRIMARY KEY,
value TEXT)"""
    )

    conn2.close()

    logger.debug("数据库初始化成功")

    # handle data
    all_data_keys = {"banList": [], "requestTime": {}, "banListRaw": []}
    data = load_data()
    if data == {}:
        write_data("banList", [])
        write_data("requestTime", {})
        logger.info("数据库内容为空，已写入默认值")
    for k, v in all_data_keys.items():
        if k not in data:
            write_data(k, v)
            logger.info(f"数据库中不存在{k}，已创建")

    # 处理代理配置
    if read_config("common.proxy.enable"):
        if read_config("common.proxy.http_value"):
            os.environ["http_proxy"] = read_config("common.proxy.http_value")
            logger.info("HTTP协议代理地址: " + read_config("common.proxy.http_value"))
        if read_config("common.proxy.https_value"):
            os.environ["https_proxy"] = read_config("common.proxy.https_value")
            logger.info("HTTPS协议代理地址: " + read_config("common.proxy.https_value"))
        logger.info("代理功能已开启，请确保代理地址正确，否则无法连接网络")

    # cookie池
    if read_config("common.cookiepool"):
        logger.info("已启用cookie池功能，请确定配置的cookie都能正确获取链接")
        logger.info("传统的源 - 单用户cookie配置将被忽略")
        logger.info("所以即使某个源你只有一个cookie，也请填写到cookiepool对应的源中，否则将无法使用该cookie")
        variable.use_cookie_pool = True

    # Load ban list into memory
    logger.debug("Loading ban list into memory...")
    db_ban_list = read_data("banList") # This still uses the old slow method, but only once at startup.
    count = 0
    if db_ban_list:
        for b in db_ban_list:
            # Check for expiration before adding to memory
            if b.get("expire") and (time.time() > b.get("expire_time", 0)):
                count += 1
                continue
            variable.ban_list[b["ip"]] = b
            variable.ban_list_raw.add(b["ip"])
    
    if count > 0:
        logger.info(f"启动时已忽略{count}条过期封禁数据")
    return


def ban_ip(ip_addr, ban_time=-1):
    if read_config("security.banlist.enable"):
        expire_enabled = read_config("security.banlist.expire.enable")
        
        if ban_time == -1:
            ban_length = read_config("security.banlist.expire.length")
        else:
            ban_length = ban_time
        
        expire_time = time.time() + ban_length

        ban_info = {
            "ip": ip_addr,
            "expire": expire_enabled,
            "expire_time": expire_time,
        }
        variable.ban_list[ip_addr] = ban_info
        variable.ban_list_raw.add(ip_addr)
        # TODO: Add a background task to persist the ban list to the database
    else:
        if variable.banList_suggest < 10:
            variable.banList_suggest += 1
            logger.warning("黑名单功能已被关闭，我们墙裂建议你开启这个功能以防止恶意请求")


def check_ip_banned(ip_addr):
    if read_config("security.banlist.enable"):
        if ip_addr in variable.ban_list_raw:
            ban_info = variable.ban_list.get(ip_addr)
            if not ban_info:
                # This can happen if the raw list and the dict get out of sync.
                # Safely remove from raw list and return False.
                variable.ban_list_raw.discard(ip_addr)
                return False
            
            if ban_info["expire"]:
                if ban_info["expire_time"] > int(time.time()):
                    return True
                else:
                    # Expired, remove from in-memory list
                    variable.ban_list.pop(ip_addr, None)
                    variable.ban_list_raw.discard(ip_addr)
                    # TODO: Add a background task to remove the expired ban from the database
                    return False
            else:
                # No expiration, permanent ban
                return True
        return False
    else:
        if variable.banList_suggest <= 10:
            variable.banList_suggest += 1
            logger.warning("黑名单功能已被关闭，我们墙裂建议你开启这个功能以防止恶意请求")
        return False


async def persist_ban_list():
    """
    Persist the in-memory ban list to the database.
    """
    logger.debug("Persisting ban list to database...")
    # Create a copy to avoid issues with concurrent modification
    ban_list_to_persist = list(variable.ban_list.values())
    
    # We are writing the whole list, so we can use the old write_data function
    # which overwrites the key. This is simpler than handling individual deletions.
    write_data("banList", ban_list_to_persist)
    
    # Also update the raw list in the database for consistency, though it's not strictly used anymore.
    raw_list = [b['ip'] for b in ban_list_to_persist]
    write_data("banListRaw", raw_list)
    logger.debug(f"Persisted {len(ban_list_to_persist)} ban entries to database.")


init_config()
