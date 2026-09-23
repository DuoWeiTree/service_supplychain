"""取数口子。阶段 A 只有 FixtureSource 跑得起来，真 CH 实现显式未做。

★ 做成协议是为了让接口层**不知道**数据从哪来 —— 阶段 B 换成 ChSource 时
  改的只有装配那一行。
"""
from __future__ import annotations

import datetime as dt
from typing import NamedTuple, Protocol


class InTransit(NamedTuple):
    period: str     # "YYYY-MM"
    units: int
    ref: str        # 可回查的单号


class Source(Protocol):
    def as_of(self) -> dt.date:
        """这批事实是哪一天的。★ 必须跟着数一起出去（grid 的 basis.as_of）——
        一个数要能回答「它是关于什么的」，而 CH 是采集副本，不是实时领星。"""

    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        """最近若干个完整自然月的实际销量，按月升序。★ 没有行就返回空列表 —— 不补 0。"""

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        """可售在仓。★ None = 不适用（该店铺没有 FBA），与 0 是两回事。"""

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        """采购在途（CH `quantity_receive`）。★ 返回逐笔明细，合计由调用方求和。"""
