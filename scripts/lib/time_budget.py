"""真实墙钟 deadline 预算工具。

所有导航、稳定探测、页面 API 轮询和 sleep 都服从同一个
``time.monotonic()`` deadline，避免名义超时与实际耗时脱节。
"""
from __future__ import annotations

import time

# 允许的极小调度容差：保证 deadline 内刚好能放进一个最小 probe。
DEADLINE_SCHEDULING_SLACK = 0.05


def remaining_seconds(deadline: float) -> float:
    """距离 deadline 的剩余真实秒数，最少为 0。"""
    return max(0.0, deadline - time.monotonic())


def deadline_from_timeout(timeout: float) -> float:
    """以当前单调时钟为起点创建绝对 deadline（含极小调度容差）。"""
    return (
        time.monotonic()
        + max(0.0, float(timeout))
        + DEADLINE_SCHEDULING_SLACK
    )


def sleep_until_next_probe(
    deadline: float,
    requested_interval: float,
) -> bool:
    """休眠到下一次探测，但绝不跨过 deadline。返回是否还有剩余预算。"""
    remaining = remaining_seconds(deadline)
    if remaining <= 0:
        return False
    time.sleep(min(requested_interval, remaining))
    return True
