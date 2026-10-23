"""usecase/plan_import：先全查、后一次写；任何错整体拒绝（IM-D1 / IM-D3，spec §1 ③）。"""
import datetime as dt
import io

import pytest
from openpyxl import Workbook

from shared.pg_client import pg_conn

PERIODS = ["2026-10", "2026-11", "2026-12"]
HEAD = ["类型", "货号", "店铺ID", "店铺", "国家", "msku", *PERIODS]


def xlsx(rows, head=HEAD) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(head)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def header(title="导入计划", months=3, start=dt.date(2026, 10, 1), owner="alice"):
    from usecase.plan_import import PlanHeader
    return PlanHeader(title=title, period_start=start, months=months, owner_actor=owner)


def src():
    from fixture_source import FIXTURES, FixtureSource
    return FixtureSource(FIXTURES)


def scalar(sql, args=()):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


def rows_of(sql, args=()):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def codes(exc):
    return sorted(e.code for e in exc.value.errors)


def test_import_creates_plan_with_claims_and_human_cells(seed):
    from usecase.plan_import import import_plan

    data = xlsx([
        ["销量", seed.sku_a, "11072", "A4Pet-US", "US", "MSKU-A", 10, None, 0],
        ["销量", None, "11072", None, None, "MSKU-D", 5.0, 6, None],
        ["采购", seed.sku_a, None, None, None, None, 100, None, 50],
    ])
    out = import_plan(src(), header(), data, "alice")
    pid = out["plan_id"]
    # skus = 认领带进来的货号（sku_a、sku_b），不是表里写了采购的货号数
    assert out["mskus"] == 2 and out["skus"] == 2
    assert out["demand_cells"] == 6 and out["purchase_cells"] == 6
    assert isinstance(out["elapsed_ms"], int)

    assert rows_of("SELECT title, period_start, months, owner_actor, created_by FROM plan") == [
        ("导入计划", dt.date(2026, 10, 1), 3, "alice", "alice")]
    assert sorted(rows_of("SELECT seller_sku, sid FROM msku_claim WHERE plan_id = %s"
                          " AND released_at IS NULL", (pid,))) == [
        ("MSKU-A", "11072"), ("MSKU-D", "11072")]
    demand = rows_of("SELECT seller_sku, period_start, expected_units, system_units IS NOT NULL"
                     " FROM plan_demand_cell WHERE plan_id = %s"
                     " ORDER BY seller_sku, period_start", (pid,))
    d = dt.date
    # 空格 → NULL，不是 0；0 → 0；12.0 → 12。系统预测照种不误
    assert [(s, p, u) for s, p, u, _ in demand] == [
        ("MSKU-A", d(2026, 10, 1), 10), ("MSKU-A", d(2026, 11, 1), None),
        ("MSKU-A", d(2026, 12, 1), 0),
        ("MSKU-D", d(2026, 10, 1), 5), ("MSKU-D", d(2026, 11, 1), 6),
        ("MSKU-D", d(2026, 12, 1), None)]
    assert any(sys for *_, sys in demand)
    purchase = rows_of("SELECT sku, period_start, planned_units, updated_by FROM plan_purchase_cell"
                       " WHERE plan_id = %s ORDER BY sku, period_start", (pid,))
    assert [r for r in purchase if r[0] == seed.sku_a] == [
        (seed.sku_a, d(2026, 10, 1), 100, "alice"), (seed.sku_a, d(2026, 11, 1), None, "alice"),
        (seed.sku_a, d(2026, 12, 1), 50, "alice")]
    # sku_b 的采购格随 MSKU-D 的认领长出来，表里没写 → 全空
    assert [r[2] for r in purchase if r[0] == seed.sku_b] == [None, None, None]


def test_import_rejects_everything_and_leaves_no_plan(seed):
    from usecase.plan_import import ImportRejected, import_plan

    data = xlsx([
        ["销量", None, "11072", None, None, "NO-SUCH", 1, 1, 1],       # unknown_msku
        ["销量", None, "11072", None, "UK", "MSKU-A", 1, 1, 1],        # market_mismatch
        ["销量", None, "11072", None, None, "MSKU-D", -3, 1, 1],       # bad_units
    ])
    with pytest.raises(ImportRejected) as exc:
        import_plan(src(), header(), data, "alice")
    assert codes(exc) == ["bad_units", "market_mismatch", "unknown_msku"]
    assert scalar("SELECT count(*) FROM plan") == 0
    assert scalar("SELECT count(*) FROM msku_claim") == 0


def test_import_rejects_db_mismatches_all_at_once(seed):
    from usecase.plan_import import ImportRejected, import_plan

    data = xlsx([
        ["销量", None, "99999", None, None, "MSKU-A", 1, 1, 1],              # unknown_seller
        ["销量", None, "11072", "别的店", None, "MSKU-B", 1, 1, 1],           # seller_mismatch
        ["销量", seed.sku_b, "11094", None, None, "MSKU-C", 1, 1, 1],         # sku_mismatch
        ["采购", seed.sku_e, None, None, None, None, 1, 1, 1],               # purchase_sku_not_claimed
    ])
    with pytest.raises(ImportRejected) as exc:
        import_plan(src(), header(), data, "alice")
    assert "unknown_seller" in codes(exc)
    assert {"seller_mismatch", "sku_mismatch", "purchase_sku_not_claimed"} <= set(codes(exc))
    assert scalar("SELECT count(*) FROM plan") == 0


def test_import_rejects_claimed_elsewhere_naming_the_holder(client, seed):
    from usecase.plan_import import ImportRejected, import_plan

    r = client.post("/v1/plans", json={"title": "先来的", "period_start": "2026-10-01",
                                       "months": 3}, headers={"x-actor": "alice"})
    holder = r.json()["plan_id"]
    assert client.post(f"/v1/plans/{holder}/claims", json={"seller_sku": "MSKU-A", "sid": "11072"},
                       headers={"x-actor": "alice"}).status_code == 200

    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", 1, 1, 1]])
    with pytest.raises(ImportRejected) as exc:
        import_plan(src(), header(), data, "alice")
    [e] = exc.value.errors
    assert e.code == "claimed_elsewhere"
    assert e.extra == {"plan_id": holder, "title": "先来的"}
    assert (e.row, e.col) == (2, "F")
    assert scalar("SELECT count(*) FROM plan") == 1


def test_import_concurrent_claim_rolls_back_and_names_holder(seed):
    """查时没人占、写时被抢：裁决在唯一索引，整笔回滚，补名后同样拒绝。"""
    from fixture_source import FIXTURES, FixtureSource

    from usecase.plan_import import ImportRejected, import_plan

    class Racer(FixtureSource):
        fired = False

        def daily_sales(self, seller_sku, sid, start, end):
            if not Racer.fired:
                Racer.fired = True
                with pg_conn() as c, c.cursor() as cur:
                    cur.execute("INSERT INTO plan (title, period_start, months, owner_actor,"
                                " created_by) VALUES ('抢先', '2026-10-01', 3, 'alice', 'alice')"
                                " RETURNING plan_id")
                    pid = cur.fetchone()[0]
                    cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                                " VALUES (%s, 'MSKU-A', '11072', 'alice')", (pid,))
            return super().daily_sales(seller_sku, sid, start, end)

    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", 1, 1, 1]])
    with pytest.raises(ImportRejected) as exc:
        import_plan(Racer(FIXTURES), header(), data, "alice")
    [e] = exc.value.errors
    assert e.code == "claimed_elsewhere" and e.extra["title"] == "抢先"
    assert (e.row, e.col) == (2, "F")
    assert rows_of("SELECT title FROM plan") == [("抢先",)]


def test_import_unique_index_race_rolls_back_and_names_holder_on_new_conn(seed, monkeypatch):
    """快路径读漏了（并发下读到的是旧快照）：唯一索引裁决 → 整笔回滚 → 新连接补名。"""
    from usecase import plan_claim
    from usecase.plan_import import ImportRejected, import_plan

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                    " VALUES ('抢先', '2026-10-01', 3, 'alice', 'alice') RETURNING plan_id")
        pid = cur.fetchone()[0]
        cur.execute("INSERT INTO msku_claim (plan_id, seller_sku, sid, claimed_by)"
                    " VALUES (%s, 'MSKU-D', '11072', 'alice')", (pid,))
    # 校验阶段的读照常看见占用；只让认领里那次快路径读漏掉，逼到唯一索引那一支
    monkeypatch.setattr(plan_claim, "_active_claim", lambda *a: None)
    seen_d = []

    def holder_after_validation(cur, s, sid):
        if s == "MSKU-D" and not seen_d:
            seen_d.append(1)
            return None
        return plan_claim.claim_holder(cur, s, sid)

    monkeypatch.setattr("usecase.plan_import.claim_holder", holder_after_validation)

    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", 1, 1, 1],
                 ["销量", None, "11072", None, None, "MSKU-D", 1, 1, 1]])
    with pytest.raises(ImportRejected) as exc:
        import_plan(src(), header(), data, "alice")
    [e] = exc.value.errors
    assert e.code == "claimed_elsewhere" and (e.row, e.col) == (3, "F")
    # 补名走真查询：回滚撤掉了 search_path，只有新连接才查得到（ClaimedElsewhere docstring）
    assert e.extra == {"plan_id": pid, "title": "抢先"} and seen_d == [1]
    assert rows_of("SELECT title FROM plan") == [("抢先",)]
    assert scalar("SELECT count(*) FROM msku_claim WHERE seller_sku = 'MSKU-A'") == 0


def test_import_makes_no_source_call_while_a_connection_is_open(seed, monkeypatch):
    """取数源可以慢到 20s：写事务（或任何连接）攥着跨过它，会把同货号的认领全堵住。"""
    import contextlib

    from fixture_source import FIXTURES, FixtureSource

    from usecase import plan_import
    from usecase.plan_import import import_plan

    open_conns = [0]
    calls = []
    real = plan_import.pg_conn

    @contextlib.contextmanager
    def counted():
        with real() as c:
            open_conns[0] += 1
            try:
                yield c
            finally:
                open_conns[0] -= 1

    monkeypatch.setattr(plan_import, "pg_conn", counted)

    class Watched(FixtureSource):
        def as_of(self):
            calls.append(("as_of", open_conns[0]))
            return super().as_of()

        def daily_sales(self, *a):
            calls.append(("daily_sales", open_conns[0]))
            return super().daily_sales(*a)

        def fba_stock_days(self, *a):
            calls.append(("fba_stock_days", open_conns[0]))
            return super().fba_stock_days(*a)

    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", 1, 1, 1],
                 ["销量", None, "11072", None, None, "MSKU-D", 1, 1, 1]])
    out = import_plan(Watched(FIXTURES), header(), data, "alice")
    assert out["mskus"] == 2
    assert calls, "取数源一次都没被调 —— 这条测试没测到东西"
    assert [c for c in calls if c[1]] == [], calls


def test_import_rolls_back_when_source_is_down(seed):
    from fixture_source import FIXTURES, FixtureSource

    from dim.ch_source import ChUnavailable
    from usecase.plan_import import import_plan

    class Down(FixtureSource):
        def as_of(self):
            raise ChUnavailable("192.0.2.1:8123", {"code": "ECONNREFUSED"})

    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", 1, 1, 1]])
    with pytest.raises(ChUnavailable):
        import_plan(Down(FIXTURES), header(), data, "alice")
    assert scalar("SELECT count(*) FROM plan") == 0
    assert scalar("SELECT count(*) FROM plan_demand_cell") == 0


def test_import_purchase_sku_resolved_via_bridge(seed):
    from usecase.plan_import import import_plan

    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", None, None, None],
                 ["采购", seed.sku_a, None, None, None, None, 7, None, None]])
    out = import_plan(src(), header(), data, "alice")
    assert scalar("SELECT planned_units FROM plan_purchase_cell WHERE plan_id = %s AND sku = %s"
                  " AND period_start = '2026-10-01'", (out["plan_id"], seed.sku_a)) == 7


@pytest.mark.parametrize("kw, code", [
    ({"title": "  "}, "title_required"),
    ({"months": 3.5}, "validation_error"),
    ({"months": 0}, "bad_months"),
    ({"months": 25}, "bad_months"),
    ({"start": dt.date(2026, 10, 2)}, "bad_period_start"),
])
def test_import_header_errors_reject_with_row_zero(seed, kw, code):
    from usecase.plan_import import ImportRejected, import_plan

    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", 1, 1, 1]])
    with pytest.raises(ImportRejected) as exc:
        import_plan(src(), header(**kw), data, "alice")
    assert [(e.row, e.code) for e in exc.value.errors] == [(0, code)]
    assert scalar("SELECT count(*) FROM plan") == 0


def test_import_file_too_large_and_not_xlsx(seed, monkeypatch):
    from usecase import plan_import
    from usecase.plan_import import ImportRejected, import_plan

    with pytest.raises(ImportRejected) as exc:
        import_plan(src(), header(), b"not a zip at all", "alice")
    [e] = exc.value.errors
    assert e.code == "not_xlsx" and e.message == "不是有效的 xlsx 文件"

    monkeypatch.setattr(plan_import, "MAX_BYTES", 10)
    with pytest.raises(ImportRejected) as exc:
        import_plan(src(), header(), xlsx([]), "alice")
    assert codes(exc) == ["file_too_large"]
    assert scalar("SELECT count(*) FROM plan") == 0


def test_parse_header_ok_and_text_months():
    from usecase.plan_import import parse_header
    h, errs = parse_header("t", "2026-11", "3", "alice")
    assert errs == [] and h.period_start == dt.date(2026, 11, 1) and h.months == 3
    h, errs = parse_header("t", "2026-11", 5, "alice")
    assert errs == [] and h.months == 5


def test_parse_header_lists_every_header_error_at_once():
    from usecase.plan_import import parse_header
    h, errs = parse_header(" ", "2026-13", "²", "alice")
    assert h is None
    assert sorted(e.code for e in errs) == ["bad_date", "title_required", "validation_error"]
    assert all(e.row == 0 for e in errs)
    _, errs = parse_header("t", "2026-11", "25", "alice")
    assert [e.code for e in errs] == ["bad_months"]
    _, errs = parse_header("t", "2026-11", "3.5", "alice")
    assert [e.code for e in errs] == ["validation_error"]


def test_plan_periods_ym_crosses_year():
    from usecase.plan_import import plan_periods_ym
    assert plan_periods_ym(dt.date(2026, 11, 1), 3) == ["2026-11", "2026-12", "2027-01"]


def _chained(msg):
    try:
        raise ConnectionResetError(104, "Connection reset by peer")
    except ConnectionResetError as inner:
        raise RuntimeError(msg) from inner


@pytest.mark.parametrize("target,stage", [("seed_rows", "seed"), ("claim_msku", "write")])
def test_import_failure_log_carries_cause_chain(seed, scm_log, monkeypatch, target, stage):
    """取预测 / 写入炸了：日志得带底层原因（errno 在 __cause__ 上），光类名加消息定位不了。"""
    import usecase.plan_import as mod

    def boom(*a, **k):
        _chained("upstream broke")

    monkeypatch.setattr(mod, target, boom)
    data = xlsx([["销量", None, "11072", None, None, "MSKU-A", 1, 1, 1]])
    with pytest.raises(RuntimeError):
        mod.import_plan(src(), header(), data, "alice")
    line = next(r.getMessage() for r in scm_log.records if f"stage={stage}" in r.getMessage())
    assert "errno=104" in line and "Connection reset by peer" in line, line
    assert scalar("SELECT count(*) FROM plan") == 0


def test_unknown_msku_message_tells_the_human_what_to_do(seed):
    """IM-D3「人去维护产品」：报错得说出去哪维护、维护完要等什么，而不是一个内部表名。"""
    from usecase.plan_import import ImportRejected, import_plan
    data = xlsx([["销量", None, "11072", None, None, "NO-SUCH-MSKU", 1, 1, 1]])
    with pytest.raises(ImportRejected) as ei:
        import_plan(src(), header(), data, "alice")
    (err,) = ei.value.errors
    assert err.code == "unknown_msku"
    assert "桥表" not in err.message and "领星" in err.message and "同步" in err.message
