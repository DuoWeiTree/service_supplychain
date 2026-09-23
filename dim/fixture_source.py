"""CSV 底料。阶段 A 的唯一数据源，也是前端 mock 与真 API 同源的那一份。

★ 空串读成 None，不读成 0 —— 旧项目冻结 fixture 时踩过：
  空串被读成 0 之后，「不适用」和「真的没有」再也分不开。
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from dim.source import InTransit


def _int_or_none(s: str) -> int | None:
    s = (s or "").strip()
    return int(s) if s else None


class FixtureSource:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _rows(self, name: str) -> list[dict]:
        with open(self.root / name, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def as_of(self) -> dt.date:
        return dt.date.fromisoformat((self.root / "as_of.txt").read_text("utf-8").strip())

    def monthly_sales_history(self, seller_sku: str, sid: str, months: int
                              ) -> list[tuple[dt.date, int]]:
        hit = [r for r in self._rows("sales_history.csv")
               if r["seller_sku"] == seller_sku and r["sid"] == sid]
        out = [(dt.date.fromisoformat(r["month"] + "-01"), int(r["units"])) for r in hit]
        return sorted(out)[-months:]

    def onhand_available(self, seller_sku: str, sid: str) -> int | None:
        for r in self._rows("fba_onhand.csv"):
            if r["seller_sku"] == seller_sku and r["sid"] == sid:
                return _int_or_none(r["units"])
        return None

    def purchase_in_transit(self, sku: str) -> list[InTransit]:
        return [InTransit(r["period"], int(r["units"]), r["ref"])
                for r in self._rows("purchase_in_transit.csv") if r["sku"] == sku]
