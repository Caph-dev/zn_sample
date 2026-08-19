#!/bin/zsh
# 双击：关闭后重开当前店（带调试口），没有开着的店则打开。不会筛查、不会批准。
ROOT="${0:A:h}"
cd "$ROOT"
exec zsh "$ROOT/scripts/run_launcher.sh" prepare
