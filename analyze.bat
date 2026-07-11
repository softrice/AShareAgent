@echo off
chcp 65001 >nul
setlocal
set CODE=%1
if "%CODE%"=="" set CODE=002128

REM 优先使用本仓库 .venv，其次系统 python
set ROOT=%~dp0
set PYTHON=

if exist "%ROOT%.venv\Scripts\python.exe" set PYTHON=%ROOT%.venv\Scripts\python.exe
if not defined PYTHON if exist "%ROOT%venv\Scripts\python.exe" set PYTHON=%ROOT%venv\Scripts\python.exe
if not defined PYTHON where python >nul 2>nul && set PYTHON=python
if not defined PYTHON (
  echo [ERROR] 未找到 Python。请先:
  echo   cd /d %ROOT%
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)

cd /d "%ROOT%"
echo [AShareAgent] prepare %CODE%  ^(python=%PYTHON%^)
"%PYTHON%" -m ashare_agent prepare %CODE%
if errorlevel 1 (
  echo.
  echo [ERROR] 抓取失败。若缺依赖，请执行: "%PYTHON%" -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo.
echo 数据已就绪。请用 Cursor 打开本文件夹，然后对 Agent 说：
echo   分析 %CODE%
echo.
pause
