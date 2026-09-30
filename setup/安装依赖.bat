@echo off
REM 双击：安装 / 统一 zn_sample 依赖（Windows）。须 CRLF。不要写跳转标签。
REM 依赖口径写在 install_deps.py 顶部常量里，两个平台共用同一份。
setlocal EnableExtensions
chcp 65001 >nul

REM %~dp0 自带结尾反斜杠；路径一律加引号，兼容空格、中文和换盘符。
cd /d "%~dp0.."
echo == zn_sample 依赖安装（Windows）==
echo 仓库目录：%CD%
echo 说明：本脚本不改 PATH、不装 WebDriver、不填密钥、不做任何平台写操作。
echo.

REM 找 uv：PATH 优先，其次官方安装位置；都没有就用官方脚本装到用户目录。
set "UV_EXE="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV_EXE set "UV_EXE=%%I"
if defined UV_EXE if not exist "%UV_EXE%" set "UV_EXE="
if not defined UV_EXE if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV_EXE if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"

if not defined UV_EXE (
  echo 未找到 uv，正在用官方脚本安装：https://astral.sh/uv
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
  if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.cargo\bin\uv.exe"
)

if not defined UV_EXE (
  echo.
  echo 没能装上 uv。请手动安装后重新双击本文件：
  echo   https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)
echo 使用 uv：%UV_EXE%
echo.

REM 用 uv 托管的 Python 3.12 跑安装脚本：不依赖系统 Python，也不需要预先装项目依赖。
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%UV_EXE%" run --no-project --python 3.12 python "%~dp0install_deps.py" --uv "%UV_EXE%" %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
  echo 依赖安装与验收完成。请照上面的「接下来（需要人工完成）」做一遍。
) else (
  echo 没有全部完成（代号 %CODE%）。请看上面的说明，修好后重新双击本文件。
)
pause
exit /b %CODE%
