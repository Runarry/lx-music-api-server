@echo off
rem ------------------------------------------------------------
rem 打包 lx-music-api-server 为单文件可执行程序 (Windows only)
rem 使用方法：双击本脚本，或在命令行 "build_single.bat [文件名]"
rem 若指定文件名则最终生成 dist/<文件名>.exe
rem ------------------------------------------------------------

setlocal

if "%1"=="" (
    set "CUSTOM_NAME="
) else (
    set "CUSTOM_NAME=-f %1"
)

:: 安装 / 升级打包依赖
python -m pip install --upgrade --quiet pyinstaller

:: 执行项目自带的 build 脚本（release 模式）
python build.py build release %CUSTOM_NAME%

pause
endlocal 