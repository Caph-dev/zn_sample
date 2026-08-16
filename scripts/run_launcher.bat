@echo off
REM 须 CRLF。不要写跳转标签。
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."

set "MODE=%~1"
set "VALID="
if "%MODE%"=="screen" set "VALID=1"
if "%MODE%"=="pipeline" set "VALID=1"
if "%MODE%"=="tracking" set "VALID=1"
if not defined VALID (
  echo 请双击仓库里的「1-只出名单」「2-筛查批准写飞书发私信」或「3-获取物流信息写飞书发单号」。
  pause
  exit /b 2
)

if exist "%APPDATA%\npm\*" set "PATH=%APPDATA%\npm;%PATH%"

set "PY="
set "SEEN_PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "SEEN_PY=1"
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "SEEN_PY=1"
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  python3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "SEEN_PY=1"
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python3"
)
if not defined PY (
  if defined SEEN_PY (
    echo Python 版本太旧（需要 3.9 或更高）。请按 README 重装后再双击。
  ) else (
    echo 找不到 Python 3。请按 README 安装后，关掉这个窗口再双击一次。
  )
  pause
  exit /b 1
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
%PY% "%~dp0launch_sample.py" "%MODE%"
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" (
  echo 可以关掉这个窗口。
) else (
  echo 没有跑完（代号 %CODE%）。请看上面的说明。
)
pause
exit /b %CODE%
