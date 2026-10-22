"""取数口子。产品里唯一的实现是 ChSource（PB-D17）；测试替身住在 tests/。

★ 做成协议是为了让接口层**不知道**数据从哪来 —— 测试换替身时改的只有装配
  那一处（`api/common/source_factory.source_dep`）。
"""
from __future__ import annotations

import datetime as dt
from typing import NamedTuple, Protocol


class InTransit(NamedTuple):
    period: str     # "YYYY-MM"
    units: int
    ref: str        # 可回查的单号


class FbaInbound(NamedTuple):
    """已发往 FBA、还没签收完的一个货件里这个 msku 的剩余件数（spec 2026-10-14-forecast-fba-inbound §3）。"""
    shipment_id: str
    status: str
    units: int
    deliver_from: dt.date | None
    deliver_to: dt.date | None      # ★ 到货日取它（OP-19 裁定 a：宁晚不早）


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

    def fc_transfers(self, seller_sku: str, sid: str) -> int:
        """调仓中预留（spec 2026-10-14 · OP-19 c：进期初）。缺行 = 0。"""

    def fba_inbound_as_of(self) -> dt.date | None:
        """货件实际用的快照日（10-15 裁定：当天没采到时用前一天，界面写明）。"""

    def fba_in_transit(self, sku: str) -> list[tuple[str, str, FbaInbound]] | None:
        """货号级 FBA 在途（全部店铺）：`(sid, msku, 货件)`；None = 当天没有货件快照（spec 2026-10-15）。"""

    def fba_inbound(self, seller_sku: str, sid: str) -> list[FbaInbound] | None:
        """已发出未签收的 FBA 货件（spec 2026-10-14）。★ None = 当天没有货件快照：未知，不当 0。"""

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
