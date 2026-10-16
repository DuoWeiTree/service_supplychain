"""10-14 真数据回归（OP-19：负责人「计划页库存 37，领星在途 31」）。行原样取自 CH，见 fixture 的 `_source`。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from dim import ch_source as cs
from forecast.plan_forecast import MskuInput, PlanForecastInput, forecast_plan

FIX = json.loads((Path(__file__).parent / "fixtures" / "fba_inbound_20261014.json").read_text("utf-8"))
K = ("DCC1042015GLZ2B", "11072")
P = [dt.date(2026, 10, 1), dt.date(2026, 11, 1), dt.date(2026, 12, 1)]
EFF = (27, 26, 27)                     # 计划 #2 的期望（10-14 grid 实测）


def parsed():
    by_key, dropped = cs.parse_fba_inbound([tuple(r) for r in FIX["inbound_rows"]])
    return by_key[(K[1], K[0])], dropped


def test_shipped_remaining_equals_amazon_inbound_shipped():
    xs, _ = parsed()
    shipped = sum(x.units for x in xs if x.status in cs.FBA_SHIPPED_ONLY)
    _fc, lx, _ = cs.parse_fba_extra([tuple(r) for r in FIX["extra_rows"]])
    assert shipped == lx[(K[1], K[0])] == 31


def test_delivery_windows_and_dropped_counts():
    xs, dropped = parsed()
    by_end: dict[str, int] = {}
    for x in xs:
        if x.status in cs.FBA_SHIPPED_ONLY:
            by_end[x.deliver_to.isoformat()] = by_end.get(x.deliver_to.isoformat(), 0) + x.units
    assert by_end == {"2026-11-08": 7, "2026-11-15": 5, "2026-11-22": 19}
    assert dropped["not_shipped"] == {"rows": 6, "units": 40}           # WORKING 5×4 + READY_TO_SHIP 20
    assert dropped["over_received"] == {"rows": 1, "units": 3}           # FBA19JCBB2DZ 申报 3 收 6（CLOSED 的不进这一类）


def test_projection_counts_shipments_and_fc_transfers():
    xs, _ = parsed()
    onhand = {(r[0], r[1]): r[2] for r in FIX["onhand_rows"]}[(K[1], K[0])]
    fc, _lx, _ = cs.parse_fba_extra([tuple(r) for r in FIX["extra_rows"]])
    m = MskuInput(K[0], K[1], "DCC1042015GL", True, onhand + fc[(K[1], K[0])], dict(zip(P, EFF)),
                  dict(zip(P, EFF)), 0.9, fba_inbound=tuple(xs),
                  onhand_parts={"fulfillable": onhand, "fc_transfers": fc[(K[1], K[0])]})
    f = forecast_plan(PlanForecastInput(as_of=dt.date(2026, 10, 14), periods=P, make_days=30, ship_days=60,
                                        mskus=[m], purchase={("DCC1042015GL", p): 0 for p in P}, ordered_open={}))
    oct_, nov, dec = f.msku[K]
    seg = oct_.as_of_start["segment"]["units"]                           # 10-15 ~ 10-31 那一段期望
    assert (onhand, fc[(K[1], K[0])]) == (52, 2)
    assert oct_.closing == 52 + 2 - seg
    assert nov.inbound == 7 + 5 + 19
    assert nov.closing == oct_.closing - 26 + 31
    assert dec.closing == nov.closing - 27
    # 窗口 10-11 已过、还差 1 件的 FBA19MSRJ7LG：点名不计（FI-Q2）
    assert [(u["shipment_id"], u["reason"], u["units"]) for u in f.unallocated] == [
        ("FBA19MSRJ7LG", "fba_overdue", 1)]
