"""grid 预测块带 FBA 货件与调仓中（spec 2026-10-14-forecast-fba-inbound §5）。直调 forecast_payload，不连库。"""
from __future__ import annotations

import datetime as dt

from api.ui.grid_forecast import forecast_payload
from api.ui.lead_time import LeadTime
from dim.source import FbaInbound

D = dt.date
K = ("M", "11072")
PERIODS = [D(2026, 10, 1), D(2026, 11, 1), D(2026, 12, 1)]


def payload_for(*, onhand, fc, fba_inbound):
    demand = [{"seller_sku": "M", "sid": "11072", "sku": "S", "period": p.strftime("%Y-%m"),
               "effective_units": u, "system_units": u, "system_basis": {"base_rate": 0.9}}
              for p, u in zip(PERIODS, (27, 26, 27), strict=True)]
    return forecast_payload(
        as_of=D(2026, 10, 14), periods=PERIODS, demand_rows=demand,
        purchase_rows=[("S", p, 0) for p in PERIODS], fba={K: True}, onhand=onhand,
        base_rate={K: 0.9}, in_transit={}, lead=LeadTime("oversea_sea", 30, 60, 15, {}),
        embargo_days=7, plan_id=1, fba_inbound=fba_inbound, fc=fc)


def at(out, period):
    return next(r for r in out["forecast"] if r["period"] == period)


def test_payload_carries_fba_arrivals_and_onhand_parts():
    out = payload_for(onhand={K: 52}, fc={K: 2},
                      fba_inbound={K: [FbaInbound("FBA1", "SHIPPED", 7, D(2026, 11, 1), D(2026, 11, 8))]})
    [a] = at(out, "2026-11")["basis"]["arrivals"]
    assert (a["kind"], a["shipment_id"], a["units"], a["date"]) == ("fba_shipment", "FBA1", 7, "2026-11-08")
    start = at(out, "2026-10")["basis"]["as_of_start"]
    assert start["onhand_as_of"] == 54
    assert start["onhand_parts"] == {"fulfillable": 52, "fc_transfers": 2}
    assert [s["pipeline"]["fba_inbound"] for s in out["forecast_sku"]] == [7, 7, 7]
    assert out["forecast_sku"][0]["pipeline"]["fba_inbound_reason"] is None


def test_unallocated_fba_entries_pass_core_key_check():
    out = payload_for(onhand={K: 52}, fc={K: 0},
                      fba_inbound={K: [FbaInbound("OLD", "SHIPPED", 1, None, D(2026, 10, 1))]})
    [u] = out["unallocated"]
    assert (u["scope"], u["reason"], u["shipment_id"], u["units"]) == ("msku", "fba_overdue", "OLD", 1)


def test_unknown_fba_inbound_is_named_on_the_sku_row():
    out = payload_for(onhand={K: 52}, fc={K: 0}, fba_inbound={K: None})
    assert at(out, "2026-10")["reason"] == "fba_inbound_unavailable"
    s = out["forecast_sku"][0]
    assert (s["pipeline"]["fba_inbound"], s["pipeline"]["fba_inbound_reason"]) == (None, "fba_inbound_unavailable")


def test_without_fba_args_nothing_changes():
    """旧调用方（不给 fba_inbound / fc）：照旧推演，FBA 在途 = 查过了没有（0），不是未知。"""
    out = forecast_payload(
        as_of=D(2026, 10, 14), periods=PERIODS,
        demand_rows=[{"seller_sku": "M", "sid": "11072", "sku": "S", "period": "2026-10", "effective_units": 27,
                      "system_units": 27, "system_basis": None}],
        purchase_rows=[], fba={K: True}, onhand={K: 52}, base_rate={}, in_transit={},
        lead=LeadTime("oversea_sea", 30, 60, 15, {}), embargo_days=7, plan_id=1)
    assert at(out, "2026-10")["basis"]["as_of_start"]["onhand_as_of"] == 52


# ── spec 2026-10-15：货号行「当月在途」= FBA 货件（Σ 全店）+ 海外仓（领星实查）──────

from forecast.month_in_transit import TransitItem


def mit_payload(fba_transit, oversea):
    demand = [{"seller_sku": "M", "sid": "11072", "sku": "S", "period": p.strftime("%Y-%m"),
               "effective_units": u, "system_units": u, "system_basis": {"base_rate": 0.9}}
              for p, u in zip(PERIODS, (27, 26, 27), strict=True)]
    return forecast_payload(
        as_of=D(2026, 10, 14), periods=PERIODS, demand_rows=demand,
        purchase_rows=[("S", p, 0) for p in PERIODS], fba={K: True}, onhand={K: 52},
        base_rate={K: 0.9}, in_transit={}, lead=LeadTime("oversea_sea", 30, 60, 15, {}),
        embargo_days=7, plan_id=1, fba_inbound={K: []}, fc={K: 0},
        fba_transit=fba_transit, oversea_transit=oversea)


def test_sku_rows_carry_month_in_transit_with_components():
    out = mit_payload(
        {"S": [("11072", "M", FbaInbound("FBA1", "SHIPPED", 7, D(2026, 11, 1), D(2026, 11, 8))),
               ("11073", "M-CA", FbaInbound("FBA2", "SHIPPED", 2, None, D(2026, 12, 3)))]},
        {"S": [TransitItem("oversea", "OWS1", 30, D(2026, 11, 5), {"from_wid": 1, "to_wid": 2, "shipped_at": "2026-09-30"})]})
    got = [(r["period"], r["month_in_transit"]["total"], r["month_in_transit"]["fba"], r["month_in_transit"]["oversea"])
           for r in out["forecast_sku"]]
    assert got == [("2026-10", 39, 9, 30), ("2026-11", 39, 9, 30), ("2026-12", 2, 2, 0)]
    fba_item = out["forecast_sku"][0]["month_in_transit"]["items"][0]
    assert {k: fba_item[k] for k in ("source", "ref", "units", "eta", "sid", "msku", "status")} == {
        "source": "fba", "ref": "FBA1", "units": 7, "eta": "2026-11-08", "sid": "11072", "msku": "M", "status": "SHIPPED"}


def test_oversea_unknown_makes_total_unknown_and_names_why():
    out = mit_payload({"S": []}, None)
    mit = out["forecast_sku"][0]["month_in_transit"]
    assert (mit["total"], mit["fba"], mit["oversea"]) == (None, 0, None)


def test_without_transit_args_month_in_transit_is_absent_not_zero():
    out = payload_for(onhand={K: 52}, fc={K: 0}, fba_inbound={K: []})
    assert out["forecast_sku"][0]["month_in_transit"] is None
