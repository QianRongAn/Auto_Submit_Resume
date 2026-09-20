@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 手动打开一个带调试端口的 Chrome，由你自己登录 BOSS 直聘。
REM 脚本不加任何自动化参数，所以不会被识别成机器人，不会触发「安全验证」。
REM 登录后保持这个窗口开着，再去运行 run.bat。

set "PORT=9222"
set "PROF=%~dp0chrome_profile"
set "URL=https://www.zhipin.com/"

set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME%" (
    echo 没找到 chrome.exe，请手动打开 Chrome 后登录 BOSS 直聘。
    pause
    exit /b 1
)

echo 启动 Chrome（调试端口 %PORT%）...
echo 登录态会保存在：%PROF%
echo.
echo 请在打开的浏览器里：
echo   1. 若出现「安全验证」，先手动点一下
echo   2. 扫码 / 账密登录 BOSS 直聘
echo   3. 登录后保持窗口开着，再运行 run.bat
echo.

start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROF%" ^
    --no-first-run --no-default-browser-check --no-proxy-server ^
    "%URL%"

echo 已启动。等你在浏览器里登录完成后，再运行 run.bat。
pause
