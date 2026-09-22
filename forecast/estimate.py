"""系统预估销量：把历史月销量序列铺到计划月份上。

★ 算法照老原型 store.js `rebuildCells` 移植：序列**按位置**消费，用完沿用最后一个值
  并标 system_extrapolated。换统计口径（均值 / 同比）时换的是本文件这一个函数，
  上面那组测试的形态不变。
"""
from __future__ import annotations

import datetime as dt
from typing import NamedTuple


class MonthEstimate(NamedTuple):
    units: int
    #: ★ 外推 ≠ 预估。这个标记必须随结果一起返回，
    #  让界面不必回头读原始格子 —— 同一个数两个来源迟早分叉（14 §5）。
    system_extrapolated: bool


class InsufficientHistory(Exception):
    """一条历史都没有。★ 返回 0 会让『新品没数据』和『卖了 0 件』长得一样。"""


class HistoryGap(Exception):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"历史销量中间缺月：{missing}。缺口不是 0，不许当 0 用。")


def monthly_estimate(history: list[tuple[dt.date, int]], months: int) -> list[MonthEstimate]:
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError(f"months 必须是正整数，收到 {months!r}")
    if not history:
        raise InsufficientHistory("没有任何历史销量")

    rows = sorted(history)
    for d, u in rows:
        if d.day != 1:
            raise ValueError(f"历史的月份必须是月初，收到 {d}")
        if u < 0:
            raise ValueError(f"历史销量为负：{d} = {u}")

    # ★ 同月两行会被下面**按位置**消费成两个月，把序列整体后移一格而毫无回声 ——
    #   与缺月同一类的静默丢失，所以同样硬失败，不许合并、不许取一个。
    dupes = sorted({f"{d:%Y-%m}" for (d, _), (n, _) in zip(rows, rows[1:]) if d == n})
    if dupes:
        raise ValueError(f"历史销量同月出现多行：{dupes}。按位置消费会整体错位。")

    missing = []
    for prev, cur in zip(rows, rows[1:]):
        step = (cur[0].year - prev[0].year) * 12 + cur[0].month - prev[0].month
        for k in range(1, step):
            m = prev[0].month + k
            missing.append(f"{prev[0].year + (m - 1) // 12}-{(m - 1) % 12 + 1:02d}")
    if missing:
        raise HistoryGap(missing)

    series = [u for _, u in rows][-months:]
    out = [MonthEstimate(u, False) for u in series]
    while len(out) < months:
        out.append(MonthEstimate(series[-1], True))
    return out
