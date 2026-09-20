@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   剪贴板管道：复制 JD -> 自动出定制话术
echo ============================================
echo.
echo  这个脚本不碰你的浏览器，不会刷新任何页面。
echo.
echo  请选择话术场景：
echo    1. BOSS直聘打招呼（中文）
echo    2. LinkedIn 求职信（英文，Easy Apply 用）
echo    3. LinkedIn 加好友备注（英文，300字符上限）
echo    4. 博士申请套磁邮件（英文）
echo.
set /p SCENE_CHOICE=输入序号后回车（直接回车=1）: 

set "SCENE=zh_job"
if "%SCENE_CHOICE%"=="2" set "SCENE=en_cover"
if "%SCENE_CHOICE%"=="3" set "SCENE=en_note"
if "%SCENE_CHOICE%"=="4" set "SCENE=en_phd"

echo.
echo  场景：%SCENE%
echo.
echo  操作方式：
echo    1. 在浏览器里点开岗位，Ctrl+A 然后 Ctrl+C 复制 JD 全文
echo    2. 等 3~5 秒，本窗口提示「话术已放回剪贴板」
echo    3. 切回页面 Ctrl+V 粘贴，然后发送/提交
echo.
echo  记录在 pipeline\outbox.md 和 pipeline\dashboard.csv
echo  按 Ctrl+C 退出
echo.
pause

"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%~dp0clip_pipeline.py" --scene %SCENE%
