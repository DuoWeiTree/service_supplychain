"""00e §1 阶段 A 的六条校验判据，每条一个可指认的测试。

★ 判据⑥（前端 mock 与真 API 跑出同一屏）的后端一半在 tests/test_grid_fixture.py；
  前端那一半在 web/ 的计划里。这里不假装它已经全过。
"""
import psycopg2.errors
import pytest
from helpers import H, prepared

from shared.pg_client import pg_conn


def test_criterion_1_build_claim_fill_submit_mint(client, seed):
    """① 建 → 认领 → 填两种量 → 提交 → 铸出 rev，全程可复现。"""
    pid = client.post("/v1/plans", json={"title": "判据一", "period_start": "2026-10-01",
                                         "months": 3}, headers=H(seed.actor)).json()["plan_id"]
    assert client.post(f"/v1/plans/{pid}/claims",
                       json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                       headers=H(seed.actor)).status_code == 200
    assert client.put(f"/v1/plans/{pid}/demand/{seed.msku_a[0]}/{seed.msku_a[1]}/2026-10",
                      json={"expected_units": 120}, headers=H(seed.actor)).status_code == 200
    assert client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
                      json={"planned_units": 400}, headers=H(seed.actor)).status_code == 200
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert r["rev"] == 1 and r["lines"] == 1
    lines = client.get("/v1/plan-lines", params={"plan_ids": pid},
                       headers=H(seed.actor)).json()["lines"]
    assert lines[0]["total_units"] == 400 and lines[0]["demand_at_submit"] == 120
    assert lines[0]["state"] == "已提交"


def test_criterion_2_every_skipped_cell_is_listed_with_a_reason(client, seed):
    """② 提交时被跳过的格子逐条列出，不静默丢。"""
    pid = prepared(client, seed, purchase=None)
    r = client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()
    assert {s["reason"] for s in r["skipped"]} == {"zero_purchase"}
    assert len(r["skipped"]) == 3
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM plan_submit_skip WHERE plan_id = %s", (pid,))
        assert cur.fetchone()[0] == 3, "★ 返回体说了，库里也要有 —— 它是留痕不是提示"


def test_criterion_3_one_msku_cannot_belong_to_two_unordered_plans(client, seed):
    """③ 同一 msku 不能同时归两个「未下单」计划 —— 库层裁决 + 接口点名。"""
    p1 = prepared(client, seed)
    p2 = client.post("/v1/plans", json={"title": "另一张", "period_start": "2026-10-01"},
                     headers=H(seed.actor)).json()["plan_id"]
    r = client.post(f"/v1/plans/{p2}/claims",
                    json={"seller_sku": seed.msku_a[0], "sid": seed.msku_a[1]},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["claimed_by"]["plan_id"] == p1


def test_criterion_4_content_is_immutable_changes_become_a_new_rev(client, seed):
    """④ 计划单记录内容不可变：改了只能出新 rev，旧版一个字节不动。

    ★ pytest.raises 包住整个 `with pg_conn()` 块（不嵌在块内部）：块内吞掉一次
      库层异常后正常退出，会被 `pg_conn()` 自己的收口守卫当场拦下并报
      RuntimeError（见 shared/pg_client.py、tests/test_pg_client.py、
      test_ddl_003_state_machine.py 的同类写法）——那会把「触发器挡住了直接
      UPDATE」这件事测成「pg_conn 的收口守卫生效了」，证的不是这条判据。
    """
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT line_id FROM plan_line WHERE plan_id = %s", (pid,))
        line = cur.fetchone()[0]
    with pytest.raises(psycopg2.errors.RaiseException) as ei, \
            pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line SET state = '已确认' WHERE line_id = %s", (line,))
    assert "只能由事件表推进" in str(ei.value)
    client.post(f"/v1/plans/{pid}/revs/1/cancel", json={"reason": "重来"}, headers=H(seed.actor))
    client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
               json={"planned_units": 999}, headers=H(seed.actor))
    assert client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor)).json()["rev"] == 2
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT rev, total_units FROM plan_line WHERE plan_id = %s ORDER BY rev",
                    (pid,))
        assert cur.fetchall() == [(1, 500), (2, 999)]


def test_criterion_5_the_back_edge_exists_and_terminal_states_are_sealed(client, seed):
    """⑤ 阶段 A 的形态（S-11）：白名单建对 + 非法迁移被外键拒 + 终态不可离开。

    ★ 退回边的**触发源**（撤组单）属阶段 B —— 这里证的是这条边存在且走得通，
      不是「阶段 A 能自己走一遍」。两者不要混。
    """
    pid = prepared(client, seed)
    client.post(f"/v1/plans/{pid}/submit", headers=H(seed.actor))
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT line_id FROM plan_line WHERE plan_id = %s", (pid,))
        line = cur.fetchone()[0]
        # 库层直插：已提交 → 已确认 → 已提交（唯一的一条退回边）
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已提交', '已确认', %s)", (line, seed.actor))
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor)"
                    " VALUES (%s, '已确认', '已提交', %s)", (line, seed.actor))
        cur.execute("SELECT state FROM plan_line WHERE line_id = %s", (line,))
        assert cur.fetchone()[0] == "已提交"
    r = client.post(f"/v1/plan-lines/{line}/transition", json={"to_state": "已完结"},
                    headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["allowed"] == ["已确认", "已撤销"]

    # ★ 判据⑤ 的后半句「终态密封」此前一条断言都没有：上面那次 422 是
    #   illegal_transition（这条边不在白名单里），与「这条记录已经在终态、
    #   哪条边都走不了」是两件事，而两者都长成一个 422。
    cancelled = client.post(f"/v1/plan-lines/{line}/cancel", json={"reason": "封存它"},
                            headers=H(seed.actor))
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "已撤销"
    sealed = client.post(f"/v1/plan-lines/{line}/transition", json={"to_state": "已确认"},
                         headers=H(seed.actor))
    assert sealed.status_code == 422 and sealed.json()["error"] == "terminal_state"
    assert sealed.json()["state"] == "已撤销" and sealed.json()["line_id"] == line


def test_mirror_refresh_end_to_end(wipe, seed):
    """CLI 刷一轮 → 镜像有数 → 留痕可查 → 陈旧闸放行。

    ★ 用注入的假 CH 行，不连内网 —— 连 CH 的那条在
      test_jobs_refresh_dims_live.py 里，CH 不可达时**吵着跳过**。

    ★ `seed` 已种下 wid=1（kind='local'）当「阶段 A 外键地基」，而
      `R.WAREHOUSE` 第一行也是 wid=1（type=1/sub=0 → 同样是 'local'）——
      两者共享同一个 PK，所以这一轮刷新是**在原地 upsert 掉种子行**，
      不是新增第四行。真实结果是 3 行（1/2/3），不是 4 行；断言按这个
      实测行为写，而不是抄 brief 草稿里没考虑到这次 PK 碰撞的 4 行版本。
    """
    from jobs import refresh_dims as rd
    from shared.pg_client import pg_conn
    from tests.fixtures import ch_rows as R

    runs = rd.refresh_all("cli", actor=seed.actor, only="warehouse",
                          query=R.replay(R.WAREHOUSE))
    assert [r.ok for r in runs] == [True]
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT wid, kind FROM warehouse ORDER BY wid")
        assert cur.fetchall() == [(1, "local"), (2, "oversea_self"), (3, "oversea_3pl")]
        cur.execute("SELECT refreshed_at FROM v_mirror_freshness WHERE mirror = 'warehouse'")
        assert cur.fetchone()[0] is not None
        cur.execute("SELECT ok, drop_reasons FROM dim_refresh_run WHERE mirror = 'warehouse'")
        ok, reasons = cur.fetchone()
    assert ok and reasons["no_baseline"] == 1
