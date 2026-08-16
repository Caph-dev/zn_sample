#!/bin/zsh
# 双击：只会出名单，不会批准、不会发私信。
ROOT="${0:A:h}"
cd "$ROOT"
exec zsh "$ROOT/scripts/run_launcher.sh" screen
