@echo off
REM 双击：获取已发货物流，写飞书并发送单号。须北京时间 16:00 之后。窗口里输入 y 继续、n 退出。
set "ROOT=%~dp0"
cd /d "%ROOT%"
call "%ROOT%scripts\run_launcher.bat" tracking
