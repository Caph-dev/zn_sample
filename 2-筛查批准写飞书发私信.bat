@echo off
REM 双击：筛查、批准、写飞书、等 10 分钟补写、再发介绍私信。窗口里输入 y 继续、n 退出。
set "ROOT=%~dp0"
cd /d "%ROOT%"
call "%ROOT%scripts\run_launcher.bat" pipeline
