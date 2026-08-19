@echo off
REM 双击：关闭后重开当前店（带调试口），没有开着的店则打开。不会筛查、不会批准。
set "ROOT=%~dp0"
cd /d "%ROOT%"
call "%ROOT%scripts\run_launcher.bat" prepare
