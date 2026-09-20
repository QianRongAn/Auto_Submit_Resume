@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo 使用解释器：%PY%
echo.
echo 提示：若还没开浏览器，请先双击 start_browser.bat 手动打开并登录 BOSS 直聘。
echo.
"%PY%" write_response.py

echo.
echo 运行结束，日志在 logs\ 目录
pause
