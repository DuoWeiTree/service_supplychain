"""api/pub/v1：给外部 AI 的冻结契约（IM-D7）—— 映射 · 预测预览 · 导入 · 模板 · 硬删除。"""
import io

import pytest
from helpers import H, prepared
from openpyxl import Workbook

from shared.pg_client import pg_conn
from usecase.plan_xlsx import read_first_sheet

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
AI = "ai.agent"


@pytest.fixture
def ai(seed):
    # wipe 清了 actor 表，029 种的这一行要自己补
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO actor (actor_id, name, active) VALUES (%s, 'AI 员工', true)", (AI,))
    return seed


def counts():
    with pg_conn() as c, c.cursor() as cur:
        out = {}
        for t in ("plan", "msku_claim", "plan_demand_cell"):
            cur.execute(f"SELECT count(*) FROM {t}")
            out[t] = cur.fetchone()[0]
        return out


# ---- ① mappings ------------------------------------------------------------

def test_mappings_without_conditions_is_need_query(client, seed):
    r = client.get("/pub/v1/mappings")
    assert r.status_code == 200, r.text
    assert r.json() == {"need_query": True, "matched": 0, "truncated": False, "limit": 200,
                        "dropped": 0, "items": []}


def test_mappings_by_market_returns_only_uk_msku(client, seed):
    r = client.get("/pub/v1/mappings", params={"market": "UK"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["need_query"] is False and body["matched"] == 1 and body["dropped"] == 0
    assert body["items"] == [{
        "sku": seed.sku_a, "sku_name": "猫爬架", "seller_sku": "MSKU-C", "sid": "11094",
        "seller_name": "A4Pet-BS-UK", "market": "UK", "platform": "amazon", "has_fba": True,
        "claimed_by": None}]


def test_mappings_show_the_plan_that_holds_the_msku(client, seed):
    pid = prepared(client, seed)
    r = client.get("/pub/v1/mappings", params={"sku": seed.sku_a})
    items = {(i["seller_sku"], i["sid"]): i for i in r.json()["items"]}
    assert set(items) == {seed.msku_a, seed.msku_b, seed.msku_c}
    assert items[seed.msku_a]["claimed_by"] == {"plan_id": pid, "title": "10 月计划"}
    assert items[seed.msku_b]["claimed_by"] is None


def test_mappings_by_msku_returns_that_msku_in_every_store(client, seed):
    r = client.get("/pub/v1/mappings", params={"seller_sku": "MSKU-C"})
    assert r.status_code == 200, r.text
    assert [(i["seller_sku"], i["sid"]) for i in r.json()["items"]] == [("MSKU-C", "11094")]


def test_mappings_conditions_are_anded_and_limit_truncates(client, seed):
    r = client.get("/pub/v1/mappings", params={"sku": seed.sku_a, "sid": "11072", "limit": 1})
    body = r.json()
    assert body["matched"] == 2 and body["truncated"] is True and body["limit"] == 1
    assert len(body["items"]) == 1


def test_mappings_bad_limit_and_unknown_param_are_400(client, seed):
    assert client.get("/pub/v1/mappings", params={"sku": "x", "limit": "z"}).json()["error"] == "bad_limit"
    r = client.get("/pub/v1/mappings", params={"q": "x"})
    assert r.status_code == 400 and r.json()["error"] == "unknown_query_param"


def test_mapping_partition_counts_rows_the_join_would_eat():
    from usecase.catalog_query import partition
    keep = ("S", "名", "M", "1", "店", "US", "amazon", True, None, None, True, True)
    no_sku = ("S2", None, "M2", "1", "店", "US", "amazon", True, None, None, False, True)
    no_seller = ("S", "名", "M3", "9", None, None, None, None, None, None, True, False)
    items, dropped = partition([keep, no_sku, no_seller])
    assert dropped == 2 and [i["seller_sku"] for i in items] == ["M"]


# ---- ② forecast preview ----------------------------------------------------

def test_preview_projects_without_writing(client, seed):
    before = counts()
    r = client.post("/pub/v1/forecast/preview", json={
        "period_start": "2026-10", "months": 3,
        "mskus": [{"seller_sku": "MSKU-A", "sid": "11072"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["periods"] == ["2026-10", "2026-11", "2026-12"]
    rows = [f for f in body["forecast"] if (f["seller_sku"], f["sid"]) == seed.msku_a]
    assert [f["period"] for f in rows] == body["periods"]
    assert [d["system_units"] for d in body["demand"]] == [135, 130, 135]
    assert body["source_notes"]["as_of"]
    assert counts() == before == {"plan": 0, "msku_claim": 0, "plan_demand_cell": 0}


def test_preview_skus_expand_through_the_bridge(client, seed):
    r = client.post("/pub/v1/forecast/preview",
                    json={"period_start": "2026-10", "months": 2, "skus": [seed.sku_a]})
    assert r.status_code == 200, r.text
    got = {(d["seller_sku"], d["sid"]) for d in r.json()["demand"]}
    assert got == {seed.msku_a, seed.msku_b, seed.msku_c}


def test_preview_unknown_msku_is_404_listing_each(client, seed):
    r = client.post("/pub/v1/forecast/preview", json={
        "period_start": "2026-10", "months": 1,
        "mskus": [{"seller_sku": "NOPE", "sid": "11072"}, {"seller_sku": "MSKU-A", "sid": "11072"},
                  {"seller_sku": "NOPE2", "sid": "1"}],
        "skus": ["NO-SUCH-SKU"]})
    assert r.status_code == 404 and r.json()["error"] == "unknown_msku"
    assert r.json()["mskus"] == [{"seller_sku": "NOPE", "sid": "11072"},
                                 {"seller_sku": "NOPE2", "sid": "1"}]
    assert r.json()["skus"] == ["NO-SUCH-SKU"]


def test_preview_requires_mskus_or_skus_and_a_valid_header(client, seed):
    r = client.post("/pub/v1/forecast/preview", json={"period_start": "2026-10", "months": 1})
    assert r.status_code == 400 and r.json()["error"] == "validation_error"
    r = client.post("/pub/v1/forecast/preview",
                    json={"period_start": "2026-13", "months": 1, "skus": [seed.sku_a]})
    assert r.status_code == 400 and r.json()["error"] == "bad_date"
    r = client.post("/pub/v1/forecast/preview",
                    json={"period_start": "2026-10", "months": 25, "skus": [seed.sku_a]})
    assert r.status_code == 400 and r.json()["error"] == "validation_error"
    r = client.post("/pub/v1/forecast/preview",
                    json={"period_start": "2026-10", "months": 1, "skus": [seed.sku_a], "x": 1})
    assert r.status_code == 400 and r.json()["error"] == "validation_error"


def _chained_down():
    from dim.ch_source import ChUnavailable
    try:
        raise ConnectionResetError(104, "Connection reset by peer")
    except ConnectionResetError as inner:
        raise ChUnavailable("192.0.2.1:8123", {"code": "ECONNRESET"}) from inner


def _down_source():
    from fixture_source import FIXTURES, FixtureSource

    class Down(FixtureSource):
        def as_of(self):
            _chained_down()
    return Down(FIXTURES)


def _line(scm_log, op):
    # 最后一条：timed 的通用行在前，业务层自己的失败行在后
    return [r.getMessage() for r in scm_log.records if f"op={op} " in r.getMessage()][-1]


def test_preview_source_failure_logs_the_cause_chain(client, seed, use_source, scm_log):
    """预览炸在取数源：503 之外，日志得留下底层 errno —— 光一句 ch_unavailable 分不出断连还是拒连。"""
    use_source(_down_source)
    r = client.post("/pub/v1/forecast/preview", json={
        "period_start": "2026-10", "months": 1, "mskus": [{"seller_sku": "MSKU-A", "sid": "11072"}]})
    assert r.status_code == 503, r.text
    for op in ("seed_forecast", "pub_preview"):
        assert "errno=104" in _line(scm_log, op), op


def test_projection_failure_logs_the_cause_chain(client, seed, monkeypatch, scm_log):
    import usecase.plan_projection as proj

    def boom(*a, **k):
        try:
            raise ConnectionResetError(104, "Connection reset by peer")
        except ConnectionResetError as inner:
            raise RuntimeError("forecast blew up") from inner

    monkeypatch.setattr(proj, "forecast_plan", boom)
    r = client.post("/pub/v1/forecast/preview", json={
        "period_start": "2026-10", "months": 1, "mskus": [{"seller_sku": "MSKU-A", "sid": "11072"}]})
    assert r.status_code == 500
    assert "errno=104" in _line(scm_log, "grid_forecast")


# ---- ③ import · ③' template --------------------------------------------------

def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["类型", "货号", "店铺ID", "店铺", "国家", "msku", "2026-10", "2026-11", "2026-12"])
    ws.append(["销量", "DCC1800264G1", "11072", "A4Pet-US", "US", "MSKU-A", 10, None, 0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(client, actor):
    return client.post("/pub/v1/plans/import",
                       data={"title": "AI 计划", "period_start": "2026-10", "months": "3"},
                       files={"file": ("p.xlsx", _xlsx(), XLSX)}, headers=H(actor))


def test_import_refuses_human_actor(client, ai):
    r = _import(client, ai.actor)
    assert r.status_code == 403 and r.json()["error"] == "actor_not_allowed"
    assert counts()["plan"] == 0


def test_import_as_ai_agent_is_201(client, ai):
    r = _import(client, AI)
    assert r.status_code == 201, r.text
    pid = r.json()["plan_id"]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT owner_actor, created_by FROM plan WHERE plan_id = %s", (pid,))
        assert cur.fetchone() == (AI, AI)


def test_import_source_failure_http_log_carries_the_cause_chain(client, ai, use_source, scm_log):
    use_source(_down_source)
    r = _import(client, AI)
    assert r.status_code == 503, r.text
    assert "errno=104" in _line(scm_log, "pub_plan_import")
    assert counts()["plan"] == 0


def test_import_header_errors_are_422(client, ai):
    r = client.post("/pub/v1/plans/import", data={"title": "", "period_start": "x", "months": "3"},
                    files={"file": ("p.xlsx", b"junk", XLSX)}, headers=H(AI))
    assert r.status_code == 422 and r.json()["error"] == "import_rejected"
    assert {e["code"] for e in r.json()["errors"]} == {"title_required", "bad_date"}


def test_template_downloads(client, seed):
    r = client.get("/pub/v1/plans/import/template", params={"period_start": "2026-11", "months": 2})
    assert r.status_code == 200 and r.headers["content-type"].startswith(XLSX)
    head, _ = read_first_sheet(r.content)
    assert head[-2:] == ["2026-11", "2026-12"]
    r = client.get("/pub/v1/plans/import/template", params={"period_start": "nope"})
    assert r.status_code == 400 and r.json()["error"] == "bad_date"


# ---- ④ delete ----------------------------------------------------------------

def test_delete_refuses_human_actor(client, ai):
    pid = prepared(client, ai)
    r = client.delete(f"/pub/v1/plans/{pid}", headers=H(ai.actor))
    assert r.status_code == 403 and r.json()["error"] == "actor_not_allowed"
    assert counts()["plan"] == 1


def test_delete_as_ai_agent_is_200(client, ai):
    pid = prepared(client, ai)
    r = client.delete(f"/pub/v1/plans/{pid}", headers=H(AI))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] == pid and body["title"] == "10 月计划"
    assert {(m["seller_sku"], m["sid"]) for m in body["mskus"]} == {ai.msku_a, ai.msku_c}
    assert counts() == {"plan": 0, "msku_claim": 0, "plan_demand_cell": 0}


def test_delete_submitted_is_409_and_missing_is_404(client, ai):
    pid = prepared(client, ai)
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(ai.actor)).status_code == 200
    r = client.delete(f"/pub/v1/plans/{pid}", headers=H(AI))
    assert r.status_code == 409 and r.json()["error"] == "plan_submitted"
    assert r.json()["revs"] == 1
    r = client.delete("/pub/v1/plans/999999", headers=H(AI))
    assert r.status_code == 404 and r.json()["error"] == "plan_not_found"


def test_template_rejects_bogus_actor_but_allows_none(client, seed):
    q = {"period_start": "2026-11", "months": 1}
    r = client.get("/pub/v1/plans/import/template", params=q, headers=H("nobody"))
    assert r.status_code == 400 and r.json()["error"] == "unknown_actor"
    assert client.get("/pub/v1/plans/import/template", params=q).status_code == 200


def test_import_stock_window_past_anchor_is_translated(client, ai, monkeypatch):
    import datetime as dt

    from forecast.baseline import DataWindows
    from usecase import plan_claim

    def past_anchor(as_of, embargo_days=plan_claim.EMBARGO_DAYS):
        a = as_of - dt.timedelta(days=embargo_days)
        return DataWindows(a, a - dt.timedelta(days=89), a - dt.timedelta(days=55),
                           a + dt.timedelta(days=1))

    monkeypatch.setattr(plan_claim, "data_windows", past_anchor)
    r = _import(client, AI)
    assert r.status_code == 500 and r.json()["error"] == "stock_window_past_anchor", r.text
    assert counts()["plan"] == 0
