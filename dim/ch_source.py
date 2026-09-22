"""真 CH 取数。★ 阶段 A 没有实现 —— 刻意抛，不给空实现。

空实现会让「该做没做」和「本来就不用做」长得一模一样（01 规则五）。
阶段 B 接上时，这三个方法各自的取数口径在 08 §2.3 与 CLAUDE.md「取数的四条铁律」里。
"""
from __future__ import annotations

import datetime as dt

from dim.source import InTransit

_MSG = ("CH 取数属阶段 B：请按 08 §2.3 实现（商品目录 LEFT JOIN 快照、"
        "msku→货号 按 as_of argMax 但 sid 不参与、日报先按 _captured_date 去重并比对覆盖面）")


class ChSource:
    def as_of(self) -> dt.date:
        raise NotImplementedError(_MSG)

    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        raise NotImplementedError(_MSG)

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        raise NotImplementedError(_MSG)

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        raise NotImplementedError(_MSG)
