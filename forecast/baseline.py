"""预测销量：兄弟仓 v1 `baseline_rate` 的移植（spec §3.1 · 00 D-3 ①）。纯函数，标准库（OP-2）。

    base_rate = 近 56 天日销 → densify（从首次有销量起，没出现 = 0）→ censor（剔 FBA 断货日）
                → 截到非零日 p95 → 指数加权算术均值（半衰期 14 天）
                → 向自身近 90 天算术日均收缩，权重 n/(n+3)

★ 先搬不修：兄弟仓已知缺陷原样保留（OP-3），由金标钉住。
★ 单 msku 签名：兄弟仓按 (sku, node) 各算各的，一次只算一个 msku 与它等价 ——
  而且从构造上就不可能被邻居污染（兄弟仓 2026-08-21 修掉的那个 bug）。
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass

# ★ 全部照搬兄弟仓 models/inventory_forecast/demand.py:67-77，不另调（spec §3.1）
HALFLIFE_DAYS = 14.0
WINDOW_DAYS = 56
SHRINK_PRIOR = 3.0
TARGET_WINDOW_DAYS = 90
WINSOR_Q = 0.95
#: demand.py:266 `if len(nz) >= 5` —— 非零日少于 5 天不削峰
WINSOR_MIN_NONZERO = 5

#: spec §3.1 ② · 00 W-5：估计锚点从 as_of 前挪 7 天（整窗后移，不丢样本）。
#: ★ 设计取值 · 未实测 —— 兄弟仓 maturity.py:179 自己标的占位（成熟度曲线受 08-20 回填污染）。
EMBARGO_DAYS = 7


@dataclass(frozen=True)
class BaselineResult:
    anchor: dt.date
    rate: float
    raw_rate: float
    n_eff: float
    target_rate: float
    window_from: dt.date
    target_from: dt.date
    first_seen: dt.date | None
    kept_days: int
    censored_days: int
    winsor_cap: float | None
    #: target_rate = target_units ÷ target_days —— 依据要能说出这两个数（spec §3.3）
    target_units: int = 0
    target_days: int = TARGET_WINDOW_DAYS


@dataclass(frozen=True)
class DataWindows:
    estimation_anchor: dt.date
    sales_from: dt.date       # 90 天收缩目标窗的起点（日销要取到这里）
    censor_from: dt.date      # 56 天估计窗的起点（断货日要取到这里）
    until: dt.date


@dataclass(frozen=True)
class Estimate:
    as_of: dt.date
    embargo_days: int
    estimation_anchor: dt.date
    result: BaselineResult | None
    #: spec §3.3：由算法如实给出，调用方照写，不盖章
    extrapolated: bool


def _quantile_linear(xs: list[float], q: float) -> float:
    """= numpy.quantile 默认的 'linear'。"""
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def baseline_rate(daily: Mapping[dt.date, int], censor: Collection[dt.date], anchor: dt.date, *,
                  halflife: float = HALFLIFE_DAYS, window: int = WINDOW_DAYS,
                  prior: float = SHRINK_PRIOR, target_window: int = TARGET_WINDOW_DAYS,
                  winsor_q: float | None = WINSOR_Q) -> BaselineResult | None:
    start = anchor - dt.timedelta(days=window - 1)
    in_win = {d: u for d, u in daily.items() if start <= d <= anchor}
    if not in_win:
        return None           # 兄弟仓：窗口里一行都没有 → 结果里没有这个键
    grid = [start + dt.timedelta(days=i) for i in range(window)]
    w_all = [math.exp(-math.log(2.0) * (window - 1 - i) / halflife) for i in range(window)]
    positive = [d for d, u in in_win.items() if u > 0]
    born = min(positive) if positive else None      # ★ densify 从首次有销量起（上市前不是「卖了 0」）
    keep = [(d not in censor) and (born is None or d >= born) for d in grid]
    cap = None
    if any(keep):
        v = [float(in_win.get(d, 0)) for d, k in zip(grid, keep) if k]
        w = [x for x, k in zip(w_all, keep) if k]
        if winsor_q is not None:
            nz = [x for x in v if x > 0]            # ★ 分位只在非零日上算
            if len(nz) >= WINSOR_MIN_NONZERO:
                cap = _quantile_linear(nz, winsor_q)
                v = [min(x, cap) for x in v]
        raw = sum(a * b for a, b in zip(w, v)) / sum(w)
        n = sum(w)
    else:
        raw, n = 0.0, 0.0
    t_start = anchor - dt.timedelta(days=target_window - 1)
    tot_units = sum(u for d, u in daily.items() if t_start <= d <= anchor)
    tot = float(tot_units)
    span_days = target_window if born is None else min(target_window, (anchor - born).days + 1)
    target = tot / span_days
    rate = (n * raw + prior * target) / (n + prior) if (n + prior) else 0.0
    censored = sum(1 for d in grid if d in censor and (born is None or d >= born))
    return BaselineResult(anchor, rate, raw, n, target, start, t_start, born, sum(keep),
                          censored, cap, tot_units, span_days)


def estimation_anchor(as_of: dt.date, embargo_days: int = EMBARGO_DAYS) -> dt.date:
    """★ 两个锚点两个名字（兄弟仓 maturity.py:215）：推演从 as_of 的库存出发，
    速率用 as_of − embargo_days 那批成熟数据算。"""
    if not isinstance(embargo_days, int) or isinstance(embargo_days, bool) or embargo_days < 0:
        raise ValueError(f"embargo_days 必须是非负整数，收到 {embargo_days!r}")
    return as_of - dt.timedelta(days=embargo_days)


def data_windows(as_of: dt.date, embargo_days: int = EMBARGO_DAYS) -> DataWindows:
    a = estimation_anchor(as_of, embargo_days)
    return DataWindows(a, a - dt.timedelta(days=TARGET_WINDOW_DAYS - 1),
                       a - dt.timedelta(days=WINDOW_DAYS - 1), a)


def estimate_rate(daily: Mapping[dt.date, int], censor: Collection[dt.date], as_of: dt.date,
                  embargo_days: int = EMBARGO_DAYS) -> Estimate:
    """生产入口：速率在估计锚点上算（spec §3.1 ②）。"""
    a = estimation_anchor(as_of, embargo_days)
    result = baseline_rate(daily, censor, a)
    # ★ 一条日速率 × 该月天数对任何月份都直接成立 —— 没有按位置沿用的外推法那种
    #   「历史月份不够、照搬最后一个月」的外推步；没有 rate 就更谈不上外推。
    return Estimate(as_of, embargo_days, a, result, extrapolated=False)
