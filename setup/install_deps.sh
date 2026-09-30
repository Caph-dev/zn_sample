#!/bin/sh
# 终端入口：macOS / Linux 上安装 / 统一 zn_sample 依赖。
# Windows 双击 setup\安装依赖.bat（同一份 install_deps.py，口径一致）。
#
#   zsh setup/install_deps.sh               完整安装 + 前端构建 + 验收
#   zsh setup/install_deps.sh --no-build    跳过前端构建（产物已是最新时）
#   zsh setup/install_deps.sh --with-test   验收时额外跑 pytest
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(dirname -- "$SCRIPT_DIR")
cd "$ROOT"

echo "== zn_sample 依赖安装（$(uname -s)）=="
echo "仓库目录：$ROOT"
echo "说明：本脚本不改 PATH、不装 WebDriver、不填密钥、不做任何平台写操作。"
echo

find_uv() {
  candidate=$(command -v uv 2>/dev/null || true)
  if [ -n "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  for candidate in \
    "$HOME/.local/bin/uv" \
    /opt/homebrew/bin/uv \
    /usr/local/bin/uv \
    "$HOME/.cargo/bin/uv"
  do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

UV=$(find_uv || true)
if [ -z "$UV" ]; then
  echo "未找到 uv，正在用官方脚本安装：https://astral.sh/uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV=$(find_uv || true)
fi
if [ -z "$UV" ]; then
  echo "没能装上 uv。请手动安装后重跑本脚本："
  echo "  https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi
echo "使用 uv：$UV"
echo

# 用 uv 托管的 Python 3.12 跑安装脚本：不依赖系统 Python，也不需要预先装项目依赖。
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec "$UV" run --no-project --python 3.12 python "$SCRIPT_DIR/install_deps.py" --uv "$UV" "$@"
