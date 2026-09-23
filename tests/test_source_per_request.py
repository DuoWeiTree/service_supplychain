"""取数源的生命周期：**每请求一个**，没有任何可变状态跨请求共享。

★ 存在的理由（终审 C-1/C-2/C-3，2026-09-23）：`api/ui/plans.py` 原先在 import
  时造一个 `ChSource` 单例，而 `grid()`/`claim()` 是 plain def 端点、由 Starlette
  放进线程池并发跑。一个单例同时造出三种故障，**三种都没有任何门禁看得见**：

  · C-1 一个 `clickhouse_connect` 客户端被线程池共用。实测 4 线程 3 败：
    `ProgrammingError: Attempt to execute concurrent queries within the same
    session. Please use a separate client instance per thread/process.`
    被分类成 kind="other"、翻成 `503 forecast_source_unavailable`
    「预测取数源连不上」—— CH 是健康的，运维却被指去查网络。
  · C-2 `history_window()` 是一个槽位，写完由调用方下一行读回。实测线程 B 问
    sid 11077（PETSFIT_EUROPE/Amazon.co.uk），窗口回来写着
    PETSFIT_NORTH_AMERICA/Amazon.com，连 zero_filled 都是 A 的。
  · C-3 `purchase_as_of` 一经解析就钉死，OQ-5 的陈旧闸从此再也不触发。实测
    `as_of()` 走到 2026-10-03 时它仍返回 2026-09-23（10 天 / 阈值 3 天），不抛。

★ 三条判据各有各的证伪路径，不许合成一条：
  · 生命周期（C-1 的根因）→ `test_每请求各造一个源`
  · 标记按键存取（C-2）→ `test_history_window_按它回答的那个键`（顺序即可证伪，
    不靠并发的运气）
  · 陈旧闸会复判（C-3）→ `test_采购陈旧在后续请求上照样触发`
  哪一条被改回去，都只有它自己那条会红。
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import threading

import pytest

from api.ui import source_factory as sf
from dim import ch_source as cs
from shared.ch_client import describe_failure


def H(a):
    return {"x-actor": a}


# ---------------------------------------------------------------------------
# 假 CH：按 SQL 文本分派到四种真实行形态。★ 不是随手编的字典 —— 每种形态的
# 列序都照 dim/ch_source.py 里那条 SQL 的 SELECT 列表抄的，错了纯变换就炸。
# ---------------------------------------------------------------------------

#: 在仓批量的一行样本（sid, seller_sku, units, raw_rows）。
_ONHAND_ROWS = [("11072", "MSKU-A", 300, 1), ("11072", "MSKU-B", 50, 1),
                ("11094", "MSKU-C", 25, 1)]
#: 月销量（month, units）—— 两个店各给一份，好让「窗口指错店」看得出来。
_SALES_ROWS = [(dt.date(2026, 6, 1), 561), (dt.date(2026, 7, 1), 254),
               (dt.date(2026, 8, 1), 318)]


class FakeCh:
    """按 SQL 文本分派。`gate` 可选，用来把并发请求钉在同一时刻上。"""

    def __init__(self, capture_days=None, purchase_captured=dt.date(2026, 9, 23), gate=None):
        self.capture_days = capture_days or [dt.date(2026, 9, 23)]
        self.purchase_captured = purchase_captured
        self.gate = gate
        self.calls: list[str] = []

    def __call__(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        one = " ".join(sql.split())
        if "GROUP BY _captured_date" in one:
            self.calls.append("capture_days")
            return [(d, 8000, 21, True) for d in self.capture_days]
        if "GROUP BY sid, seller_sku" in one:
            self.calls.append("onhand")
            return list(_ONHAND_ROWS)
        if "max(_captured_date)" in one and cs.PURCHASE_ITEMS_TABLE in one and "JOIN" not in one:
            self.calls.append("purchase_as_of")
            return [(self.purchase_captured,)]
        if "INNER JOIN o" in one:
            self.calls.append("in_transit")
            return [("DCC1800264G1", "2026-07", 40, "PO-OLD")]
        if cs.ORDERS_TABLE in one:
            self.calls.append("sales")
            if self.gate is not None:
                self.gate()
            return list(_SALES_ROWS)
        raise AssertionError(f"假 CH 认不出这条 SQL —— 形态变了就该炸，不许返回空：{one[:160]}")


def _plan(client, seed, months=3):
    return client.post("/v1/plans", json={"title": "t", "period_start": "2026-10-01",
                                          "months": months},
                       headers=H(seed.actor)).json()["plan_id"]


# ---------------------------------------------------------------------------
# C-1：生命周期
# ---------------------------------------------------------------------------

def test_every_request_is_served_by_its_own_source(client, seed, use_source):
    """★ 判据：**并发四个请求 = 四个互不相同的源真的各服务了一个响应**。

    ★ 不数「工厂被调了几次」—— 那个数由 `Depends` 的接线天然保证，哪怕端点
      转头去用一个模块级单例它也照样是 4（第一轮变异就是这么绿掉的：
      变异改了工厂，而断言看的正是工厂）。证人必须在现场：让每个源把自己的
      编号**随响应一起发出来**（`stats()` → `source_notes.dropped`），断言四份
      响应带回四个不同的编号。端点一旦改回用单例，这个数立刻变成 1。
    """
    tags = iter(range(1000))

    class Marked(cs.ChSource):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.tag = next(tags)

        def stats(self) -> dict:
            return {**super().stats(), "_served_by": self.tag}

    use_source(lambda: Marked(FakeCh(), classify_failure=describe_failure))
    pid = _plan(client, seed)
    client.post(f"/v1/plans/{pid}/claims",
                json={"seller_sku": "MSKU-A", "sid": "11072"}, headers=H(seed.actor))

    with cf.ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)),
                         range(4)))
    assert [r.status_code for r in rs] == [200, 200, 200, 200], [r.text for r in rs]
    served = [r.json()["source_notes"]["dropped"]["_served_by"] for r in rs]
    assert len(set(served)) == 4, (
        f"四个并发响应由 {len(set(served))} 个源服务（编号 {served}）—— 有源被跨请求"
        "共享了。ChSource 身上全是可变的每次调用状态（两份缓存 / stats /"
        " history_window / purchase_as_of）+ 一个 CH 客户端，共享它就是 "
        "C-1/C-2/C-3 三条一起回来")


def test_one_ch_client_is_never_shared_across_concurrent_requests(client, seed, monkeypatch,
                                                                  use_source):
    """★ C-1 本身：走**生产那条装配路径**（`make_source()` 读 `[forecast]`），
    只把建客户端那一步换成一个**会像真驱动一样拒绝并发**的替身。

    替身的拒绝判据抄的是 `clickhouse_connect` 的真行为，不是编的：同一个客户端
    实例上同时有两条查询在飞就抛。真驱动 2026-09-23 实测 4 线程 3 败。"""
    issued: list = []

    class Result:
        def __init__(self, rows):
            self.result_rows = rows

    class SessionBoundClient:
        """一个 session 上只许有一条查询在飞 —— 同 clickhouse_connect 的真限制。"""

        def __init__(self):
            self._busy = threading.Lock()
            self._fake = FakeCh()

        def query(self, sql, parameters=None):
            if not self._busy.acquire(blocking=False):
                raise RuntimeError(
                    "Attempt to execute concurrent queries within the same session. "
                    "Please use a separate client instance per thread/process.")
            try:
                threading.Event().wait(0.02)      # 把并发窗口撑开，不靠运气
                return Result(self._fake(sql, parameters))
            finally:
                self._busy.release()

    def fake_ch_client():
        c = SessionBoundClient()
        issued.append(c)
        return c

    monkeypatch.setattr(sf, "forecast", lambda: {"source": "ch"})
    monkeypatch.setattr(sf, "ch_client", fake_ch_client)
    # ★ 不换 `ch_query`：日志三问那层是生产的一部分，换掉它这条就不是端到端了。
    use_source(sf.make_source)

    pid = _plan(client, seed)
    client.post(f"/v1/plans/{pid}/claims",
                json={"seller_sku": "MSKU-A", "sid": "11072"}, headers=H(seed.actor))

    with cf.ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor)),
                         range(4)))
    bad = [(r.status_code, r.json().get("error"), r.json().get("msg"))
           for r in rs if r.status_code != 200]
    assert not bad, (
        f"并发请求被自己的客户端挡掉了：{bad} —— 这就是 C-1：CH 健康，503 却说"
        "「预测取数源连不上」，运维会去查网络")
    assert len(issued) >= 2, (
        f"四个并发请求只建了 {len(issued)} 个 CH 客户端 —— 客户端被共享了")


# ---------------------------------------------------------------------------
# C-2：口径标记按它回答的那个键存取
# ---------------------------------------------------------------------------

def test_history_window_answers_about_the_key_it_was_asked():
    """★ 顺序就能证伪，不靠并发的运气：同一个源先后问两个店，**第一个**的窗口
    必须还指着第一个店。单槽位实现下它会指着第二个店 —— 与 C-2 在并发下
    实测到的是同一个缺陷，只是把「交错」换成了「先后」。"""
    src = cs.ChSource(FakeCh(), classify_failure=describe_failure)
    src.monthly_sales_history("MSKU-A", "11072", 3)     # PETSFIT_NORTH_AMERICA / Amazon.com
    src.monthly_sales_history("MSKU-C", "11094", 3)     # A4PET_EUROPE / Amazon.co.uk

    first = src.history_window("MSKU-A", "11072")
    assert (first["store"], first["sales_channel"]) == ("PETSFIT_NORTH_AMERICA", "Amazon.com"), (
        f"问 (MSKU-A, 11072) 拿回来的窗口写着 {first.get('store')}/"
        f"{first.get('sales_channel')} —— 一个指着错店的标记比没有标记更坏："
        "它是对「这个数是关于什么的」给出的一个自信的错答案")
    second = src.history_window("MSKU-C", "11094")
    assert (second["store"], second["sales_channel"]) == ("A4PET_EUROPE", "Amazon.co.uk")
    assert src.history_window("MSKU-NEVER-ASKED", "11072") == {}, (
        "没问过的键要返回 {} —— 那是「还没问过」，不是「问了但没有答案」")


def test_concurrent_claims_each_carry_their_own_store_marker(client, seed, use_source):
    """★ 端到端：四个并发 claim 跨两个店，每份响应的 `history_window` 必须指着
    **自己那个 sid** 的店。四条查询钉在同一个 barrier 上同时返回，把交错窗口
    撑到最大。"""
    barrier = threading.Barrier(4, timeout=20)
    use_source(lambda: cs.ChSource(FakeCh(gate=barrier.wait),
                                   classify_failure=describe_failure))
    pid = _plan(client, seed)

    want = {("MSKU-A", "11072"): ("PETSFIT_NORTH_AMERICA", "Amazon.com"),
            ("MSKU-B", "11072"): ("PETSFIT_NORTH_AMERICA", "Amazon.com"),
            ("MSKU-C", "11094"): ("A4PET_EUROPE", "Amazon.co.uk"),
            ("MSKU-D", "11072"): ("PETSFIT_NORTH_AMERICA", "Amazon.com")}

    def claim(ms):
        return ms, client.post(f"/v1/plans/{pid}/claims",
                               json={"seller_sku": ms[0], "sid": ms[1]},
                               headers=H(seed.actor))

    with cf.ThreadPoolExecutor(4) as ex:
        got = list(ex.map(claim, want))
    for ms, r in got:
        assert r.status_code == 200, r.text
        w = r.json()["history_window"]
        assert (w["store"], w["sales_channel"]) == want[ms], (
            f"{ms} 的响应带回来的口径标记是 {w['store']}/{w['sales_channel']}，"
            f"应为 {want[ms]} —— 它拿到了另一个请求的标记")


# ---------------------------------------------------------------------------
# C-3：陈旧闸在后续请求上照样触发
# ---------------------------------------------------------------------------

def test_purchase_staleness_gate_fires_on_a_later_request_too(client, seed, use_source):
    """★ C-3 实测形态：第一次请求时采购表是新鲜的（闸放行并把日期钉住），随后
    采购采集停摆、fba_detail 照常往前走。**第二次请求必须 503**。

    钉死的实现下第二次仍然 200，`source_notes.purchase_as_of` 悄悄老了 10 天 ——
    而 OQ-5 点名「悄悄给昨天的数」正是它要防的那一种结局。"""
    fake = FakeCh(capture_days=[dt.date(2026, 9, 23)],
                  purchase_captured=dt.date(2026, 9, 23))
    clock = [dt.datetime(2026, 9, 23, 8, 0, tzinfo=dt.UTC)]
    # ★ 一个跨两次请求都活着的源：这正是「长命进程」的形状，也是 C-3 的现场。
    src = cs.ChSource(fake, now=lambda: clock[0], cache_ttl_s=300,
                      purchase_staleness_days=3, classify_failure=describe_failure)
    use_source(lambda: src)

    pid = _plan(client, seed)
    client.post(f"/v1/plans/{pid}/claims",
                json={"seller_sku": "MSKU-A", "sid": "11072"}, headers=H(seed.actor))
    r1 = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor))
    assert r1.status_code == 200, r1.text
    assert r1.json()["source_notes"]["purchase_as_of"] == "2026-09-23"

    # 十天过去：在仓快照照常推进，采购表停在 09-23。TTL 也过了。
    fake.capture_days = [dt.date(2026, 10, 3)]
    clock[0] = dt.datetime(2026, 10, 3, 8, 0, tzinfo=dt.UTC)

    r2 = client.get(f"/v1/plans/{pid}/grid", headers=H(seed.actor))
    assert r2.status_code == 503, (
        f"第二次请求拿到 {r2.status_code}，body={r2.text} —— 采购快照已经 10 天没动"
        "（阈值 3 天），闸必须再判一次。钉死的实现下它只在进程的第一次调用上生效")
    body = r2.json()
    assert body["error"] == "forecast_source_unusable"
    assert body["captured"] == "2026-09-23" and body["age_days"] == 10, body
    assert body["threshold_days"] == 3


def test_purchase_as_of_is_rechecked_when_the_reference_moves():
    """★ 同一条判据在 dim 层的单测形态（不经 HTTP）：参照的 `as_of()` 一往前走，
    年龄就变了，闸必须重判 —— 而不是拿第一次的结论一直用下去。"""
    fake = FakeCh(capture_days=[dt.date(2026, 9, 23)],
                  purchase_captured=dt.date(2026, 9, 23))
    clock = [dt.datetime(2026, 9, 23, 8, 0, tzinfo=dt.UTC)]
    src = cs.ChSource(fake, now=lambda: clock[0], cache_ttl_s=300,
                      purchase_staleness_days=3, classify_failure=describe_failure)
    assert src.purchase_as_of() == dt.date(2026, 9, 23)

    fake.capture_days = [dt.date(2026, 10, 3)]
    clock[0] = dt.datetime(2026, 10, 3, 8, 0, tzinfo=dt.UTC)
    with pytest.raises(cs.PurchaseTableStale) as ei:
        src.purchase_as_of()
    assert ei.value.age_days == 10 and ei.value.threshold_days == 3


def test_purchase_as_of_is_not_requeried_while_the_reference_holds():
    """★ 反面靶子：参照物没动就不许多打一次 CH —— 「每次都重查」与「按参照物
    缓存」都能让上面那条绿，两者的区别只有这条数得出来。"""
    fake = FakeCh()
    src = cs.ChSource(fake, classify_failure=describe_failure)
    src.purchase_as_of()
    src.purchase_as_of()
    src.purchase_as_of()
    assert fake.calls.count("purchase_as_of") == 1, fake.calls
