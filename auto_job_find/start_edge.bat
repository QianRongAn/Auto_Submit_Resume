@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  以调试模式重启 Edge（用于脚本接管）
echo ============================================
echo.
echo  会做两件事：
echo   1. 关闭当前所有 Edge 窗口
echo   2. 以调试端口 9222 重新打开，并自动恢复刚才的标签页
echo.
echo  你的 BOSS 直聘登录态保存在 Edge 用户数据里，不会丢失。
echo.
echo  如果有重要内容没保存，请先保存好再继续。
echo.
pause

set "EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"

echo.
echo 正在关闭 Edge...
taskkill /F /IM msedge.exe >nul 2>&1
timeout /t 4 /nobreak >nul

echo 正在以调试模式启动 Edge...
start "" "%EDGE%" --remote-debugging-port=9222 --restore-last-session

timeout /t 3 /nobreak >nul
echo.
echo 已完成。请确认：
echo   - Edge 已重新打开，刚才的标签页已恢复
echo   - BOSS 直聘页面仍处于登录状态
echo.
echo 确认后回来告诉助手，他会接管执行。
echo.
pause
