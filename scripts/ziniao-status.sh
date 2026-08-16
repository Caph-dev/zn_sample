#!/usr/bin/env zsh
# 一眼看清：当前是 GUI 还是 WebDriver
set -euo pipefail
PORT="${ZINIAO_SOCKET_PORT:-16851}"

if ! pgrep -x ziniao >/dev/null 2>&1; then
  echo "mode: OFF"
  exit 0
fi

args=$(pgrep -x ziniao | head -1 | xargs -I{} ps -p {} -ww -o args=)
pid=$(pgrep -x ziniao | head -1)

if [[ "$args" == *run_type=web_driver* ]]; then
  echo "mode: WEBDRIVER"
else
  echo "mode: GUI"
fi
echo "pid:  $pid"
echo "args: $args"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port: $PORT LISTEN"
else
  echo "port: $PORT not listen"
fi
for p in 9480 9481; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port: $p LISTEN"
  fi
done
