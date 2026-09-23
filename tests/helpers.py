"""测试之间共用的造数。"""
import datetime as dt

OCT = dt.date(2026, 10, 1)


def mint(cur, actor, sku, total=100, state="已提交", birth=True):
    """铸出一条记录：行带 state 插入 + 追加铸出事件（S-2）。

    ★ birth=False 是给「插了行却不记事件」那条测试用的靶子，不是正常路径。
    """
    cur.execute("INSERT INTO plan (title, period_start, months, owner_actor, created_by)"
                " VALUES ('t', %s, 3, %s, %s) RETURNING plan_id", (OCT, actor, actor))
    plan_id = cur.fetchone()[0]
    cur.execute("INSERT INTO plan_rev (plan_id, rev, content_digest, submitted_by)"
                " VALUES (%s, 1, 'd', %s)", (plan_id, actor))
    cur.execute(
        "INSERT INTO plan_line (plan_id, rev, sku, period_start, total_units,"
        " demand_by_seller, demand_at_submit, state)"
        " VALUES (%s, 1, %s, %s, %s, '{}'::jsonb, 0, %s) RETURNING line_id",
        (plan_id, sku, OCT, total, state))
    line_id = cur.fetchone()[0]
    if birth:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '[*]', %s, %s, 'test')", (line_id, state, actor))
    return plan_id, line_id


def H(actor: str) -> dict:
    return {"x-actor": actor}


def make_ch_unreachable(monkeypatch) -> str:
    """让 `ch_client()` 去打本机一个没人听的端口，返回它的 target 串。

    ★ 不 monkeypatch `ch_client` 去抛一个手搓的异常：那验的是「假异常怎么走」，
      而 I-2 的整条教训正是**手搓的形状与真驱动抛的不是一回事**。这里让
      `clickhouse_connect` 真的去连、真的被拒，拿到真的
      `OperationalError ← MaxRetryError ← NewConnectionError ← ConnectionRefusedError`。
      loopback，不依赖内网、不依赖超时。

    ★ 两处 `clickhouse` 名字都要覆盖：`shared/ch_client.py` 与
      `jobs/refresh_dims.py` 各自 `from shared.config import clickhouse` 绑过
      一次，只盖一个会出现「日志里写着打 192.168.66.211、实际打的是别处」——
      那正是「一个数要能回答它是关于什么的」要防的形状。生产里两者同源，
      所以覆盖两个才是忠实的模拟，不是迁就实现。
    """
    from jobs import refresh_dims as rdmod
    from shared import ch_client as chmod

    def dead():
        return {"host": "127.0.0.1", "port": 9, "user": "default", "password": "",
                "database": "jxd_raw", "secure": False}

    monkeypatch.setattr(chmod, "clickhouse", dead)
    monkeypatch.setattr(rdmod, "clickhouse", dead)
    return "127.0.0.1:9/jxd_raw"


def _ok(r):
    """★ 静默丢失已在本仓踩过六次：半路一次 409/404 不许被吞掉，
    否则 prepared() 造出的是一张半填的计划，后面的断言会因不相干的原因红或绿。"""
    assert 200 <= r.status_code < 300, r.text
    return r


def prepared(client, seed, purchase=500, expected=120):
    """一张填好两种量的计划：MSKU-A（11072）· MSKU-C（11094）同属 sku_a。

    ★ 两个店铺是刻意的：单店的话 demand_by_seller 只有一把键，
      「按店冻结」这件事等于没被测到。
    """
    pid = _ok(client.post("/v1/plans", json={"title": "10 月计划", "period_start": "2026-10-01",
                                              "months": 3},
                          headers=H(seed.actor))).json()["plan_id"]
    for ms in (seed.msku_a, seed.msku_c):
        _ok(client.post(f"/v1/plans/{pid}/claims", json={"seller_sku": ms[0], "sid": ms[1]},
                        headers=H(seed.actor)))
        if expected is not None:
            _ok(client.put(f"/v1/plans/{pid}/demand/{ms[0]}/{ms[1]}/2026-10",
                           json={"expected_units": expected}, headers=H(seed.actor)))
    if purchase is not None:
        _ok(client.put(f"/v1/plans/{pid}/purchase/{seed.sku_a}/2026-10",
                       json={"planned_units": purchase}, headers=H(seed.actor)))
    return pid
