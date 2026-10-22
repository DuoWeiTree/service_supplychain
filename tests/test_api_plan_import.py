"""POST /v1/plans/import · GET /v1/plans/import/template（spec §2 ui 壳）。"""
import io

from openpyxl import Workbook

from usecase.plan_xlsx import read_first_sheet

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEAD = ["类型", "货号", "店铺ID", "店铺", "国家", "msku", "2026-10", "2026-11", "2026-12"]


def xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def post(client, seed, data, actor=True, **form):
    form = {"title": "导入计划", "period_start": "2026-10", "months": "3"} | form
    h = {"x-actor": seed.actor} if actor else {}
    return client.post("/v1/plans/import", data=form,
                       files={"file": ("p.xlsx", data, XLSX)}, headers=h)


def test_success_is_201_and_grid_shows_the_imported_units(client, seed):
    r = post(client, seed, xlsx([["销量", seed.sku_a, "11072", "A4Pet-US", "US", "MSKU-A", 10, None, 0]]))
    assert r.status_code == 201, r.text
    pid = r.json()["plan_id"]
    g = client.get(f"/v1/plans/{pid}/grid", headers={"x-actor": seed.actor})
    assert g.status_code == 200
    got = {c["period"]: c["expected_units"] for c in g.json()["demand"]
           if c["seller_sku"] == "MSKU-A"}
    assert got == {"2026-10": 10, "2026-11": None, "2026-12": 0}, got   # 空 != 0


def test_rejected_is_422_with_all_errors_and_no_plan(client, seed):
    r = post(client, seed, xlsx([["乱写", seed.sku_a, "11072", None, None, "MSKU-A", 1, 1, 1],
                                 ["销量", None, "11072", None, None, "NOPE", 1, 1, 1]]),
             )
    body = r.json()
    assert r.status_code == 422 and body["error"] == "import_rejected"
    codes = {e["code"] for e in body["errors"]}
    assert "bad_type" in codes and len(body["errors"]) >= 2, body
    assert "有" in body["hint"] and "计划没建" in body["hint"]
    plans = client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"]
    assert plans == []


def test_bad_period_start_joins_the_422_list(client, seed):
    r = post(client, seed, xlsx([]), period_start="2026-13", title="")
    assert r.status_code == 422
    assert {"bad_date", "title_required"} <= {e["code"] for e in r.json()["errors"]}


def test_non_digit_months_is_reported_by_the_usecase(client, seed):
    r = post(client, seed, xlsx([]), months="abc")
    assert r.status_code == 422
    assert "validation_error" in {e["code"] for e in r.json()["errors"]}


def test_missing_actor_is_400_unknown_actor(client, seed):
    r = post(client, seed, xlsx([]), actor=False)
    assert r.status_code == 400 and r.json()["error"] == "unknown_actor"


def test_template_downloads_and_reads_back(client, seed):
    r = client.get("/v1/plans/import/template", params={"period_start": "2026-11", "months": 3})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(XLSX)
    assert 'filename="plan-import-2026-11-3m.xlsx"' in r.headers["content-disposition"]
    head, _ = read_first_sheet(r.content)
    assert head[-3:] == ["2026-11", "2026-12", "2027-01"]


def test_template_months_out_of_range_is_400(client, seed):
    r = client.get("/v1/plans/import/template", params={"period_start": "2026-11", "months": 25})
    assert r.status_code == 400 and r.json()["error"] == "validation_error"
    r = client.get("/v1/plans/import/template", params={"period_start": "2026-11", "months": "x"})
    assert r.status_code == 400 and r.json()["error"] == "validation_error"
    r = client.get("/v1/plans/import/template", params={"period_start": "nope", "months": 3})
    assert r.status_code == 400 and r.json()["error"] == "bad_date"


def test_superscript_months_is_validation_error_not_500(client, seed):
    r = client.get("/v1/plans/import/template", params={"period_start": "2026-11", "months": "\u00b2"})
    assert r.status_code == 400 and r.json()["error"] == "validation_error"
    r = post(client, seed, xlsx([]), months="\u00b2")
    assert r.status_code == 422
    assert [e["code"] for e in r.json()["errors"]] == ["validation_error"]


def test_header_errors_alone_make_the_422_and_file_is_not_judged(client, seed):
    r = post(client, seed, b"not an xlsx", title="", period_start="x", months="99")
    assert r.status_code == 422
    assert sorted(e["code"] for e in r.json()["errors"]) == ["bad_date", "bad_months", "title_required"]


def test_template_rejects_bogus_actor_but_allows_none(client, seed):
    q = {"period_start": "2026-11", "months": 3}
    r = client.get("/v1/plans/import/template", params=q, headers={"x-actor": "nobody"})
    assert r.status_code == 400 and r.json()["error"] == "unknown_actor"
    assert client.get("/v1/plans/import/template", params=q).status_code == 200


def test_template_400_lists_all_header_errors(client, seed):
    r = client.get("/v1/plans/import/template", params={"period_start": "nope", "months": 99})
    assert r.status_code == 400 and r.json()["error"] == "bad_date"
    assert {e["code"] for e in r.json()["errors"]} == {"bad_date", "bad_months"}


def test_stock_window_past_anchor_is_translated_not_bare_500(client, seed, monkeypatch, scm_log):
    import datetime as dt

    from forecast.baseline import DataWindows
    from usecase import plan_claim

    def past_anchor(as_of, embargo_days=plan_claim.EMBARGO_DAYS):
        a = as_of - dt.timedelta(days=embargo_days)
        return DataWindows(a, a - dt.timedelta(days=89), a - dt.timedelta(days=55),
                           a + dt.timedelta(days=1))

    monkeypatch.setattr(plan_claim, "data_windows", past_anchor)
    r = post(client, seed, xlsx([["销量", seed.sku_a, "11072", "A4Pet-US", "US", "MSKU-A", 10, None, 0]]))
    assert r.status_code == 500 and r.json()["error"] == "stock_window_past_anchor", r.text
    assert "op=plan_import" in scm_log.text and "outcome=StockWindowPastAnchor" in scm_log.text
    assert client.get("/v1/plans", headers={"x-actor": seed.actor}).json()["plans"] == []
