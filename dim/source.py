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


class StockDays(NamedTuple):
    """censor 用的断货日（spec §3.2 · OP-15）。★ 三个集合分开：
    「那天断货」「那天采到了、没断」「那天没采到 / 没过掉档守卫」是三件事 ——
    第三种不 censor，也不许被当成第二种。"""
    censored: frozenset[dt.date]
    captured: frozenset[dt.date]
    rejected: tuple[dict, ...]


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

    def daily_sales(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                    ) -> dict[dt.date, int]:
        """[start, end] 每天的实际销量（按 order_item 去重、排除 Cancelled/Pending）。
        ★ 每个 (msku, sid, date) 只出一个数 —— 同一天的多条底料在这里就要合计好，
        `forecast/baseline.py` 的 `baseline_rate` 按天只认一个数，不做二次去重。
        ★ 没单的日子**不出现** —— 补 0 是 baseline_rate densify 的事。"""

    def fba_stock_days(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                       ) -> StockDays:
        """[start, end] 里哪些天该 msku FBA 可售 ≤ 0（censor）。"""
