import datetime as dt

from api.ui.purchase_options import history_query_dep
from dim.ch_source import fetch_purchase_actors
from shared.pg_client import pg_conn
from tests.helpers import make_ch_unreachable

DAY = dt.date(2026, 10, 7)


def _stub(client, rows):
    client.app.dependency_overrides[history_query_dep] = lambda: (lambda sql, params=None: rows)


def _po(seed, purchaser, opt):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
        cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                    " VALUES ('8085',%s,%s,2,%s)", (purchaser, opt, seed.actor))


def test_merges_local_and_history_with_source_and_counts(client, seed):
    _po(seed, 111, 10925905)
    _stub(client, [("111", "10925905", "陈彩凤", 135, DAY), ("185", "10925905", "陈彩凤", 8, DAY),
                   ("185", "777", "", 2, DAY)])
    b = client.get("/v1/purchase-options").json()
    assert "note" not in b
    assert b["warehouses"] == [{"wid": seed.wid, "name": "测试仓"}]
    p = {x["purchaser_id"]: x for x in b["purchasers"]}
    assert p[111] == {"purchaser_id": 111, "source": "本服务", "count": 135}
    assert p[185] == {"purchaser_id": 185, "source": "领星历史", "count": 10}
    o = {x["opt_uid"]: x for x in b["opt_uids"]}
    assert o[10925905] == {"opt_uid": 10925905, "name": "陈彩凤", "source": "本服务", "count": 143}
    assert o[777]["name"] is None and o[777]["source"] == "领星历史"
    assert b["history"] == {"available": True, "cause": None, "as_of": "2026-10-07", "dropped": 0}


def test_ch_unreachable_degrades_not_503(client, seed, monkeypatch):
    _po(seed, 111, 10925905)
    client.app.dependency_overrides.pop(history_query_dep)
    make_ch_unreachable(monkeypatch)
    r = client.get("/v1/purchase-options")
    assert r.status_code == 200
    b = r.json()
    assert b["history"]["available"] is False and b["history"]["cause"]
    assert b["history"]["as_of"] is None
    assert b["purchasers"] == [{"purchaser_id": 111, "source": "本服务", "count": None}]
    assert b["suppliers"] and b["warehouses"]


def test_empty_history_is_not_an_error(client, seed):
    got = fetch_purchase_actors(lambda sql, params=None: [])
    assert got.purchasers == [] and got.opt_uids == [] and got.as_of is None
    _stub(client, [])
    b = client.get("/v1/purchase-options").json()
    assert b["history"] == {"available": True, "cause": None, "as_of": None, "dropped": 0}


def test_default_client_never_reaches_real_ch(client, seed, monkeypatch):
    make_ch_unreachable(monkeypatch)
    h = client.get("/v1/purchase-options").json()["history"]
    assert h["available"] is True, h  # 触到真 CH 就会是 False


def test_opt_uid_with_two_names_sums_counts_keeps_top_name(client, seed):
    _stub(client, [("111", "5", "甲", 3, DAY), ("111", "5", "乙", 7, DAY), ("111", "5", "", 1, DAY)])
    o = client.get("/v1/purchase-options").json()["opt_uids"]
    assert o == [{"opt_uid": 5, "name": "乙", "source": "领星历史", "count": 11}]
