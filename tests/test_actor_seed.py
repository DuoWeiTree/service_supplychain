"""TS/DB 漂移门禁：`web/src/shell/actors.ts::ACTORS` 与 `scm.actor` 不许分叉。

背景（2026-09-23）：前端 `x-actor` 下拉硬编码三个 actor_id，`scm.actor` 却一行
都没有 —— 生产上的每一条请求都在 `api/ui/deps.py` 校验处炸
`400 unknown_actor`。迁移 `007_seed_stage_a_actors.sql` 把这三行种进库里，
本文件是防它俩以后再次分叉的门禁：以后有人往 `ACTORS` 里加第四个人却忘了
追加种子迁移，这里要点名炸出来，而不是等运营在生产上撞见 400。

★ `tests/conftest.py::wipe` 会 TRUNCATE `actor`（它在 `DATA_TABLES` 里），
  而 `business_db` 是 session 级 fixture ——迁移只在 session 开始时 apply 一次。
  一旦同一个 session 里有任何用到 `wipe`/`seed`/`client` 的测试跑在本测试前面，
  007 种的三行就已经被清空，而 007 又已经记在 `schema_migration` 里、
  `apply()` 不会重跑它。所以本测试不依赖「迁移种的行还在」这件事在测试间存活
  ——那是在断言 `wipe` 的行为，不是在断言 TS/DB 有没有漂移。
  做法：本测试自己把 `007_seed_stage_a_actors.sql` 的原文重新执行一遍
  （`ON CONFLICT DO NOTHING` 保证幂等，不受表里已有什么行影响），
  再去查库。这样断言的是「007 这份 SQL 实际插入/覆盖的 id 集合」是否等于
  TS 常量里的 id 集合 —— 与其它测试的执行顺序无关，也不需要重复一份
  手写的 INSERT 逻辑去平行验证。
"""
from __future__ import annotations

import re
from pathlib import Path

from shared.pg_client import pg_conn

ROOT = Path(__file__).resolve().parents[1]
ACTORS_TS = ROOT / "web" / "src" / "shell" / "actors.ts"
SEED_MIGRATION = ROOT / "migrations" / "pg" / "007_seed_stage_a_actors.sql"

#: 逐条解析 `{ actor_id: 'x', name: 'y', role: 'z' }` 形状的对象字面量。
#: 字段顺序、引号种类跟 `tests/test_layering_web.py` 解析前端源码的做法一样
#: 用正则而不是真跑一个 TS parser —— 这份常量是纯字面量数组，够用。
_ACTOR_ENTRY = re.compile(
    r"actor_id:\s*['\"](?P<actor_id>[^'\"]+)['\"]\s*,\s*"
    r"name:\s*['\"](?P<name>[^'\"]+)['\"]\s*,\s*"
    r"role:\s*['\"](?P<role>[^'\"]+)['\"]"
)


def parse_ts_actor_ids() -> list[str]:
    text = ACTORS_TS.read_text("utf-8")
    m = re.search(r"ACTORS[^=]*=\s*\[(?P<body>.*?)\]\s*;", text, re.DOTALL)
    assert m, (
        f"{ACTORS_TS.relative_to(ROOT)} 里没找到 `ACTORS = [...]` —— "
        "正则要跟着源码的写法改，不是这个门禁本身该放松"
    )
    ids = [mm.group("actor_id") for mm in _ACTOR_ENTRY.finditer(m.group("body"))]
    assert ids, "ACTORS 数组解析出 0 条 —— 这条门禁是空转的"
    return ids


def test_seed_migration_lists_every_ts_actor_id():
    """静态检查：007 的原文本身要覆盖 TS 常量里的每一个 actor_id。

    ★ 只读文件、不碰库 —— 先在这一层挡住最常见的疏漏（加了 TS 条目忘了加迁移
    条目），再靠下面那条打库的测试去挡「SQL 写对了字符串、但插入之后没生效」
    这类更隐蔽的分叉（比如误用了 UPDATE、或者 WHERE 条件把它筛没了）。
    """
    ts_ids = parse_ts_actor_ids()
    sql = SEED_MIGRATION.read_text("utf-8")
    missing = [aid for aid in ts_ids if f"'{aid}'" not in sql]
    assert not missing, (
        f"actors.ts 里这些 actor_id 没出现在 007 的种子 SQL 里：{missing} —— "
        "前端常量加了新人，迁移没跟上"
    )


def test_ts_actors_exist_and_are_active_after_seeding(business_db):
    """动态检查：重新执行 007 的原文后，TS 常量里的每个 actor_id 在库里
    都存在且 active=true —— 挡的是「SQL 文本里有这个 id，但实际插入失败/
    插到了别的状态」这类静态检查看不出来的分叉。
    """
    ts_ids = parse_ts_actor_ids()
    seed_sql = SEED_MIGRATION.read_text("utf-8")
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(seed_sql)
        cur.execute("SELECT actor_id, active FROM actor WHERE actor_id = ANY(%s)", (ts_ids,))
        rows = dict(cur.fetchall())

    missing = [aid for aid in ts_ids if aid not in rows]
    inactive = [aid for aid in ts_ids if rows.get(aid) is False]
    assert not missing, (
        f"actors.ts 里这些 actor_id 重跑 007 之后在库里仍然不存在：{missing}"
    )
    assert not inactive, (
        f"actors.ts 里这些 actor_id 在库里是停用状态，前端下拉会选出一个 "
        f"库里拒绝的身份：{inactive}"
    )


def test_extra_db_actors_are_not_a_drift_violation(business_db):
    """反方向刻意不校验：库里比 TS 常量多出来的 actor 是正常状态
    ——阶段 A 结束、真人账号陆续加进 `actor` 表之后，`ACTORS` 这份前端常量
    大概率不会跟着同步扩张（那时候大概率已经换成真的 `/v1/actors` 接口了）。
    这条门禁的职责只是「TS 里许诺的身份，库里必须兑现」，不是要求两边逐行相等。

    ★ 不能靠断言 `ts_ids == {硬编码的三个 id}` 来证明这一点 —— 那样写出来的
    是又一份「TS 常量恰好长这样」的检查，`ACTORS` 只要合法地加一个新人（同时
    正确追加了迁移）就会把它炸红，而炸的理由跟「反方向该不该校验」毫无关系。
    真正要证明的是：库里插入一个 TS 常量里没有的 actor 之后，上面两条门禁
    该走的查询逻辑仍然不会把它算作缺失/停用 —— 所以这里真的插一行库里独有的
    actor，再验证「TS 常量里的 id 集合」与「库里的 id 集合」不相等（证明确实
    多了一行、断言不是空转的），但这不影响 TS 常量自身那几个 id 的存在性判断。
    """
    ts_ids = parse_ts_actor_ids()
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(SEED_MIGRATION.read_text("utf-8"))
        cur.execute(
            "INSERT INTO actor (actor_id, name, active) VALUES (%s, %s, true) "
            "ON CONFLICT (actor_id) DO NOTHING",
            ("ops.db-only-real-person", "库里独有的真人"),
        )
        cur.execute("SELECT actor_id FROM actor")
        db_ids = {r[0] for r in cur.fetchall()}

    assert "ops.db-only-real-person" in db_ids, "插入本身失败了，下面的断言就是空转的"
    assert db_ids != set(ts_ids), "库里没有比 TS 常量多出任何行，这条测试没测到东西"
    missing = [aid for aid in ts_ids if aid not in db_ids]
    assert not missing, (
        f"TS 常量里的 id 在『库里比 TS 多一行』这个场景下仍然缺失：{missing} —— "
        "说明上面两条门禁的查询逻辑被『多出来的行』干扰了"
    )
