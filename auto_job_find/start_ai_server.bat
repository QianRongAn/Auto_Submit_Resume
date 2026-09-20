@echo off
chcp 65001 >nul
title 本地话术服务（BOSS AI 助手后端）
cd /d "%~dp0"

set PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe

if not exist "%PY%" (
  echo [错误] 找不到 Python 环境：%PY%
  echo 请确认虚拟环境还在，或改用其它 python 解释器。
  pause
  exit /b 1
)

echo ============================================================
echo   本地话术服务 —— 油猴脚本 boss_ai_helper.user.js 的后端
echo   只监听 127.0.0.1，不碰浏览器、不碰你的 BOSS 账号
echo ============================================================
echo.
echo   启动后请保持这个窗口开着。
echo   健康检查：浏览器打开 http://127.0.0.1:8765/health
echo   停止服务：直接关掉这个窗口，或按 Ctrl+C
echo.

REM 先检查端口是否已被占用（避免重复启动导致闪退）
"%PY%" -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',8765))!=0 else 1)" 2>nul
if %errorlevel%==1 (
  echo.
  echo [提示] 本地服务似乎已经在运行（端口 8765 已被占用）。
  echo   请直接打开浏览器访问：http://127.0.0.1:8765/health  查看状态。
  echo   无需重复启动。如需重启，请先关掉旧的服务窗口，或结束 python 进程。
  echo.
  pause
  exit /b 0
)

"%PY%" serve_letter.py
echo.
echo 服务已停止。
pause
