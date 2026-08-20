#!/bin/zsh
# 双击：筛查、批准、写飞书、等 10 分钟补写并回填订单号、再发介绍私信。窗口里输入 y 继续、n 退出。
ROOT="${0:A:h}"
cd "$ROOT"
exec zsh "$ROOT/scripts/run_launcher.sh" pipeline
