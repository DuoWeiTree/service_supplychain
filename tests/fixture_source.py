"""测试专用取数替身：读 tests/fixtures/ 的 CSV 底料（PB-D17：产品只读真 CH，这个类不许回到产品包）。

conftest 的 autouse 夹具 `forecast_source` 把它装进每个测试的 `make_source`。

★ 空串读成 None，不读成 0 —— 旧项目冻结 fixture 时踩过：
  空串被读成 0 之后，「不适用」和「真的没有」再也分不开。
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from dim.source import InTransit, StockDays

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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

    def daily_sales(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                    ) -> dict[dt.date, int]:
        """★ 同一天两行（同 msku/sid/date）在这里就加总 —— baseline_rate 按天只认
        一个数，取数层悄悄留一条丢一条会比加错更难查。
        ★ 合计 0 件的日子不出行（同 ChSource.daily_sales，终审 I-3）。"""
        out: dict[dt.date, int] = {}
        for r in self._rows("daily_sales.csv"):
            if r["seller_sku"] == seller_sku and r["sid"] == sid:
                d = dt.date.fromisoformat(r["date"])
                if start <= d <= end:
                    out[d] = out.get(d, 0) + int(r["units"])
        return {d: u for d, u in out.items() if u != 0}

    def fba_stock_days(self, seller_sku: str, sid: str, start: dt.date, end: dt.date
                       ) -> StockDays:
        """★ fixture 口径：窗口里每一天都算「采到了」—— 底料里只有断货日需要点名。"""
        days = frozenset(start + dt.timedelta(days=i) for i in range((end - start).days + 1))
        out = frozenset(dt.date.fromisoformat(r["date"]) for r in self._rows("fba_stockout_days.csv")
                        if r["seller_sku"] == seller_sku and r["sid"] == sid)
        return StockDays(out & days, days, ())
