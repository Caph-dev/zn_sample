@echo off
REM 双击：启动本地网页操作台（等价于 python scripts/launch_assistant.py）。
REM 须 CRLF。不要写跳转标签。
setlocal EnableExtensions
chcp 65001 >nul

REM %~dp0 自带结尾反斜杠；路径一律加引号，兼容空格、中文和换盘符。
cd /d "%~dp0"

REM 有项目虚拟环境就用它（uv sync / python -m venv 建的都在这里）；没有再用系统 Python。
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "PY="
if exist "%VENV_PYTHON%" set "PY="%VENV_PYTHON%""

set "SEEN_PY="
if not defined PY (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "SEEN_PY=1"
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
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
%PY% "%~dp0scripts\launch_assistant.py" %*
set "CODE=%ERRORLEVEL%"

echo.
set "STOPPED="
if "%CODE%"=="0" set "STOPPED=1"
if "%CODE%"=="-1073741510" set "STOPPED=1"
if "%CODE%"=="3221225786" set "STOPPED=1"
if defined STOPPED (
  echo 操作台已停止，可以关掉这个窗口。
) else (
  echo 没有跑完（代号 %CODE%）。请看上面的说明。
)
pause
exit /b %CODE%
