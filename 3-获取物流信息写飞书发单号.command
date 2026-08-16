#!/bin/zsh
# 双击：获取已发货物流，写飞书并发送单号。须北京时间 16:00 之后。窗口里输入 y 继续、n 退出。
ROOT="${0:A:h}"
cd "$ROOT"
exec zsh "$ROOT/scripts/run_launcher.sh" tracking
