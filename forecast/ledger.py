"""逐日台账（spec §4.1 · 兄弟仓 ledger.py / formulas.md §一）。纯函数。

★ 用 Fraction 而不是 float：半月摊分的每日量是分数，float 累加会让「月末 ≡ 期初 − 期望 + 入库」
  只在 1e-9 意义下成立 —— 恒等式应当逐位成立，不是「差不多」。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from fractions import Fraction

_ZERO = Fraction(0)


@dataclass(frozen=True)
class DailyLedger:
    start: dt.date
    onhand0: Fraction
    signed: tuple[Fraction | None, ...]
    available: tuple[Fraction | None, ...]
    daily_unmet: tuple[Fraction | None, ...]
    unknown_from: dt.date | None

    def _span_index(self, d: dt.date) -> int:
        """★ 校验 d 落在 [start, start + len-1] 内 —— 越界不许静默 wraparound
        （负下标）或裸抛 IndexError，必须报出日期和台账范围。"""
        i = (d - self.start).days
        if not 0 <= i < len(self.signed):
            last = self.start + dt.timedelta(days=len(self.signed) - 1)
            raise ValueError(f"{d} 不在台账 [{self.start}, {last}] 内")
        return i

    def signed_at(self, d: dt.date) -> Fraction | None:
        if d == self.start - dt.timedelta(days=1):
            return self.onhand0
        return self.signed[self._span_index(d)]

    def first_unmet(self, first: dt.date, last: dt.date) -> dt.date | None:
        i0, i1 = self._span_index(first), self._span_index(last)
        for i in range(i0, i1 + 1):
            u = self.daily_unmet[i]
            if u is not None and u > 0:
                return self.start + dt.timedelta(days=i)
        return None


def run_ledger(onhand0: int | Fraction, start: dt.date,
                demand: list[Fraction | None], arrivals: list[Fraction | None]) -> DailyLedger:
    if len(demand) != len(arrivals):
        raise ValueError(f"demand {len(demand)} 天 ≠ arrivals {len(arrivals)} 天")
    s = a = Fraction(onhand0)
    if s < 0:
        raise ValueError(f"期初可售为负：{onhand0}")
    cum = _ZERO
    signed: list[Fraction | None] = []
    avail: list[Fraction | None] = []
    unmet: list[Fraction | None] = []
    unknown_from: dt.date | None = None
    for i, (dem, arr) in enumerate(zip(demand, arrivals)):
        day = start + dt.timedelta(days=i)
        # ★ 负值校验必须在「今天把 unknown_from 定下来」的 continue 之前——
        #   否则那天（demand/arrivals 只有一个是 None）另一边的负值永远不会被查。
        if (dem is not None and dem < 0) or (arr is not None and arr < 0):
            raise ValueError(f"{day} 需求 {dem} / 到货 {arr} 为负")
        if unknown_from is None and (dem is None or arr is None):
            unknown_from = day
        if unknown_from is not None:
            signed.append(None)
            avail.append(None)
            unmet.append(None)
            continue
        short = max(_ZERO, dem - a - arr)
        a = max(_ZERO, a + arr - dem)
        s = s + arr - dem
        cum += short
        if s != a - cum:   # ★ spec §4.1 恒等式：每轮断言
            raise AssertionError(f"台账恒等式被破坏：{s} ≠ {a} − {cum}")
        signed.append(s)
        avail.append(a)
        unmet.append(short)
    return DailyLedger(start, Fraction(onhand0), tuple(signed), tuple(avail), tuple(unmet),
                        unknown_from)
