"""登记表门禁 (b) —— 需要真库的那一半。

★ 终审 I-5：这一条单独成文件，是因为 README「离线可跑的那部分」把整个
`tests/test_mirror_registry.py` 列了进去，而它依赖 `wipe` → `business_db`，
`tests/conftest.py` 又自己覆盖 `JXD_SCM_CONFIG` 指向仓库根目录的
`config.toml` —— 于是在一台没有内网 PG 的机器上（新接手的人、CI），那条
「证明纯层离线可测」的命令会以一个与分层毫无关系的 `OperationalError` 变红。

★ 为什么拆文件而不是在 README 那行写 `-k "not backed_by_a_real_table"`：
`-k` 是一句会被人抄漏的话，抄漏之后命令照样「能跑」，只是又开始连库 ——
而文件边界抄不漏。门禁 (a) 与其余纯断言留在 `test_mirror_registry.py`，
那个文件现在真的离线可跑。
"""
from dim.registry import MIRRORS, gate_503_names


def test_every_non_pending_entry_is_backed_by_a_real_table(wipe):
    """★ 登记了却没有 refreshed_at 列 / 不在视图里 —— 两种都会让 503 闸形同虚设。"""
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.columns"
                    " WHERE table_schema = current_schema() AND column_name = 'refreshed_at'")
        has_refreshed_at = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT mirror FROM v_mirror_freshness")
        in_view = {r[0] for r in cur.fetchall()}
    assert in_view, "视图一行都没有 —— 下面的集合比较是空转的"
    for m in MIRRORS:
        if m.pending:
            continue
        assert callable(m.fetch), f"{m.name} 不是 pending，却没有 fetch"
        if m.staleness == "gate_503":
            assert m.name in has_refreshed_at, f"{m.name} 没有 refreshed_at 列"
    assert in_view == gate_503_names(), (
        f"只在视图里：{sorted(in_view - gate_503_names())}；"
        f"只在登记表里：{sorted(gate_503_names() - in_view)}")
