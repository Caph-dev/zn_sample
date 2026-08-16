@echo off
REM 双击：只会出名单，不会批准、不会发私信。
set "ROOT=%~dp0"
cd /d "%ROOT%"
call "%ROOT%scripts\run_launcher.bat" screen
