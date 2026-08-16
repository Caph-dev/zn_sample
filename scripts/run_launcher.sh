#!/bin/zsh
# 由仓库根目录的 .command 调用。也可：zsh scripts/run_launcher.sh screen|pipeline|tracking
set -u

SCRIPT_DIR="${0:A:h}"
ROOT="${SCRIPT_DIR:h}"
cd "$ROOT"

MODE="${1:-}"
case "$MODE" in
  screen|pipeline|tracking) ;;
  *)
    print -- "请双击仓库里的「1-只出名单」「2-筛查批准写飞书发私信」或「3-获取物流信息写飞书发单号」。"
    read -r '?按回车关闭'
    exit 2
    ;;
esac

pick_python() {
  local c
  for c in python3 python; do
    if (( $+commands[$c] )); then
      print -- "${commands[$c]}"
      return 0
    fi
  done
  for c in \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3
  do
    if [[ -x $c ]]; then
      print -- "$c"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  print -- "找不到 Python 3。请按 README 安装后，关掉这个窗口再双击一次。"
  read -r '?按回车关闭'
  exit 1
}

if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  print -- "Python 版本太旧（需要 3.9 或更高）。请按 README 重装后再双击。"
  read -r '?按回车关闭'
  exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
"$PY" "$ROOT/scripts/launch_sample.py" "$MODE"
CODE=$?

print -- ""
if (( CODE == 0 )); then
  print -- "可以关掉这个窗口。"
else
  print -- "没有跑完（代号 ${CODE}）。请看上面的说明。"
fi
read -r '?按回车关闭'
exit "$CODE"
