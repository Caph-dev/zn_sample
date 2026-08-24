"""物流同步各阶段的稳定错误分类。

统一使用固定异常类型，避免把不同阶段的时间耗尽都报成裸
``TimeoutExpired``，让任务报告能指出具体阶段和剩余预算。
"""
from __future__ import annotations


class SyncStageTimeout(RuntimeError):
    """物流同步某个阶段耗尽真实墙钟预算。"""


class SellerNavigationTimeout(SyncStageTimeout):
    """进入商家订单页的导航阶段超时。"""


class SellerPageReadinessTimeout(SyncStageTimeout):
    """订单页稳定就绪契约探测超时。"""


class LogisticsRequestTimeout(SyncStageTimeout):
    """单个订单的物流详情 GET 超时。"""


class ReturnToSampleTimeout(SyncStageTimeout):
    """返回样品申请页阶段超时。"""


class BatchDeadlineExceeded(SyncStageTimeout):
    """整个同步批次的总 deadline 已到。"""
