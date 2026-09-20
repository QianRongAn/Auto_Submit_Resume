@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   半自动投递：每条话术你确认后才发送
echo ============================================
echo.
echo  开始前请注意：
echo   1. 请先关闭日常 Edge 的所有窗口（脚本要用你的 Edge 登录态开一个新窗口，
echo      目录被占用会自动回退到复制版登录态，可能需要重新验证）
echo   2. 弹出的浏览器窗口里如果出现安全验证，脚本会立刻停下，
echo      你手动过完验证后重跑本脚本即可
echo   3. 每条话术都会显示在下面这个窗口里，输入 y 才发送
echo.
echo  每天上限和间隔在 .env 里改（DAILY_CAP / SEND_MIN_INTERVAL / SEND_MAX_INTERVAL）
echo.
pause

"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%~dp0semi_auto.py"
