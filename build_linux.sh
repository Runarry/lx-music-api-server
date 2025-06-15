#!/bin/bash
# ------------------------------------------------------------
# 打包 lx-music-api-server 为单文件可执行程序 (Linux only)
# 使用方法：在终端执行 "bash build_linux.sh [文件名]"
# 若指定文件名则最终生成 dist/<文件名>
# ------------------------------------------------------------

set -e

# 获取脚本传入的第一个参数作为自定义名称
CUSTOM_NAME_ARG=""
if [ -n "$1" ]; then
    CUSTOM_NAME_ARG="-f $1"
fi

# 安装 / 升级打包依赖
echo "Installing/upgrading dependencies..."
python3 -m pip install --upgrade --quiet pyinstaller toml

# 执行项目自带的 build 脚本（release 模式）
echo "Running build script..."
python3 build.py build release $CUSTOM_NAME_ARG

echo "Build finished."