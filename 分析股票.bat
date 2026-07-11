@echo off
chcp 65001 >nul
set CODE=%1
if "%CODE%"=="" set CODE=002128
set PYTHON=E:\TradingAgents-CN\src\venv\Scripts\python.exe
cd /d E:\AShareAgent
echo [AShareAgent] prepare %CODE%
"%PYTHON%" -m ashare_agent prepare %CODE%
echo.
echo 数据已就绪。请打开 Cursor，打开文件夹 E:\AShareAgent，然后对 Agent 说：
echo   分析 %CODE%
echo.
pause
