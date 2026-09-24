"""把兄弟仓 compat 口径的 9 个 CSV 还原成本仓台账的输入（00 D-3 ①）。

★ 只在测试里：+AN 修正（月初至锚点的实际已售）与供应事件筛选是**运营 Excel 的口径**，
  本仓生产走 spec §4.2 的 W-6（Open point OP-9）。这里复现它们只为证明
  「半月摊分 + 逐日台账」这两块生产代码与 Excel 逐格相同。
  规则逐条对照兄弟仓 recon.run / supply.live_events / supply.arrival_dates /
  demand.build_daily_demand（spread 修正）。
"""
from __future__ import annotations

import csv
import datetime as dt
from fractions import Fraction as F
from pathlib import Path

from forecast.calendar import halfmonth_daily
from forecast.ledger import run_ledger

FX = Path(__file__).parent / "fixtures" / "recon_v1"
ANCHOR = dt.date(2026, 8, 4)          # 兄弟仓 recon.ANCHOR_DEFAULT
HORIZON = 120                         # RunConfig.horizon_days
CHECKPOINTS = ("2026-08-15", "2026-08-31", "2026-09-15",
               "2026-09-30", "2026-10-15", "2026-10-31")
DELIVERED = "送达"
MAX_TIER = 2                          # Tier.MANUAL_EXCEL


def rows(name: str) -> list[dict]:
    with open(FX / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(s: str | None) -> F | None:
    s = (s or "").strip()
    return None if s == "" else F(s)


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def signed_positions(*, halfmonth: bool = True, anchor_correction: bool = True,
                      exclude_delivered: bool = True
                     ) -> dict[tuple[str, str], list[F]]:
    """(sku, node) → 锚点次日起 120 天的带符号头寸。三个开关只给「承重」测试用。"""
    dates = [ANCHOR + dt.timedelta(days=i + 1) for i in range(HORIZON)]
    oh = rows("onhand.csv")
    nodes = sorted({r["node"] for r in oh if r["node"]})
    series = sorted({(r["sku"], r["node"]) for r in oh if r["node"] in nodes})
    onhand0 = {k: F(0) for k in series}
    for r in oh:
        if r["node"] in nodes and _d(r["date"]) == ANCHOR:
            onhand0[(r["sku"], r["node"])] = _num(r["qty"])

    # ---- 到货：live_events + arrival_dates(point) ----
    p50 = {r["lane_id"]: float(r["p50_days"]) for r in rows("lanes.csv")}
    floor, horizon_end = dates[0], dates[-1]
    arr = {k: [F(0)] * HORIZON for k in series}
    for e in rows("supply_events.csv"):
        tier = e["tier"].strip()
        if tier == "" or int(float(tier)) > MAX_TIER:
            continue
        if exclude_delivered and e["status"] == DELIVERED:
            continue
        qty = _num(e["qty"])
        if qty is None or qty == 0 or e["dest_node"] not in nodes:
            continue
        eta = _d(e["eta_date"]) if e["eta_date"] else None
        if eta is not None and eta > horizon_end:
            continue
        if eta is None and e["ship_date"] and e["lane_id"] in p50:
            eta = _d(e["ship_date"]) + dt.timedelta(days=p50[e["lane_id"]])
        eta = max(eta or floor, floor)          # 缺失 → 锚点次日；逾期 → 夹到锚点次日
        col, key = (eta - dates[0]).days, (e["sku"], e["dest_node"])
        if 0 <= col < HORIZON and key in arr:
            arr[key][col] += qty

    # ---- 需求：overrides 半月摊分 + spread 修正（+AN） ----
    plan: dict[tuple[str, str], dict[dt.date, F]] = {}
    for r in rows("overrides.csv"):
        if r["kind"] == "absolute":
            plan.setdefault((r["sku"], r["node"]), {})[_d(r["period_start"]).replace(day=1)] = F(r["value"])
    realized: dict[tuple[str, str], F] = {}
    for r in rows("demand_history.csv"):
        if dt.date(2026, 8, 1) <= _d(r["date"]) <= ANCHOR:
            k = (r["sku"], r["node"])
            realized[k] = realized.get(k, F(0)) + F(r["units"])
    full = [dt.date(2026, 8, 1) + dt.timedelta(days=i)
            for i in range((horizon_end - dt.date(2026, 8, 1)).days + 1)]
    offset = (dates[0] - full[0]).days
    bucket_days = sum(1 for d in dates if d <= dt.date(2026, 8, 15))
    out = {}
    for k in series:
        months = plan.get(k)
        dem = [F(0)] * HORIZON
        if months:
            per_day: dict[dt.date, F] = {}
            for m, total in months.items():
                if halfmonth:
                    per_day.update(halfmonth_daily(total, m))
                else:                                   # 承重测试：按天均摊
                    n = len([d for d in full if d.replace(day=1) == m]) or 1
                    per_day.update({d: total / n for d in full if d.replace(day=1) == m})
            vals = [per_day.get(d, F(0)) for d in full]
            dem = vals[offset:offset + HORIZON]
            resid = sum(vals[:offset], F(0)) - realized.get(k, F(0))
            if anchor_correction and resid != 0:
                dem = [v + resid / bucket_days if i < bucket_days else v for i, v in enumerate(dem)]
                dem = [max(v, F(0)) for v in dem]
        L = run_ledger(onhand0[k], dates[0], dem, arr[k])
        out[k] = list(L.signed)
    return out


def excel_formula(r: dict) -> dict[str, F]:
    """运营表 BB..BG 的公式（兄弟仓 recon._EXCEL_FORMULAS），套它自己的缓存分量。"""
    g = lambda c: F(r[c])
    y = g("J_onhand") + g("N") + g("O") + g("P") + g("Q")
    ai = y + g("R")
    aj = ai + g("S")
    ak = aj + g("V")
    al = ak + g("W")
    am = al + g("X")
    an, aq, ar, as_ = g("AN_sold"), g("AQ_aug"), g("AR_sep"), g("AS_oct")
    return {"2026-08-15": y + an - aq / 2, "2026-08-31": ai + an - aq,
            "2026-09-15": aj + an - aq - ar / 2, "2026-09-30": ak + an - aq - ar,
            "2026-10-15": al + an - aq - ar - as_ / 2, "2026-10-31": am + an - aq - ar - as_}


def at(positions: dict, key: tuple[str, str], checkpoint: str) -> F:
    return positions[key][(dt.date.fromisoformat(checkpoint) - ANCHOR).days - 1]
