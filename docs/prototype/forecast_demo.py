#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存预测模型 —— 可运行验证
==========================

★ 这个脚本存在的唯一理由：
   负责人手做了 docs/库存预测计算方式.xlsx，我把它翻译成了数据库表与公式。
   **翻译对不对，不能靠看 DDL 说得通，要靠把他的数字喂进去、看能不能逐格复现。**
   复现不出来，就是我没理解对。

用法:  python3 forecast_demo.py
依赖:  无（Python 3 标准库 sqlite3）

★ 这里用 SQLite 只是为了「立刻能跑」。生产是 PostgreSQL（docs/03 §7），
   两者语义相同，差异列在文件末尾 DIALECT_NOTES。
"""

import json
import sqlite3
import sys
from collections import defaultdict

# ════════════════════════════════════════════════════════════════════
# 0. 参数 —— ★ 一律不许写死在算法里，全部从这里进，并冻结进 forecast_run.params
# ════════════════════════════════════════════════════════════════════

PARAMS = {
    # ★ 单位是**天**，不是月 —— 月为单位表达不了「国内调拨 3 天」，
    #   而那恰恰是切到国内环境时最常见的一档。
    "make_days": 30,     # 生产：下单 → 完工入本地仓   ★ 产品默认，用户可调
    "ship_days": 60,     # 货运：本地仓发出 → 目的仓可售 ★ 产品默认，用户可调
    "alloc_rule": "按缺口比例",   # ★ M-1
    "safety_days": 0,             # ★ M-3 安全库存，暂按 0
}

# ★ 为什么默认是 60/60 而不是负责人口头给的 30/60：
#   负责人 Excel 里「10 月的采购 110 件」出现在「12 月的在途」，
#   反推生产约 62~90 天（实测扫描：30 天只对上 1/5 个月，75 天 5/5 全中）。
#   而口头给的默认值是 30 天。★ 两个数对不上，且差了整整一个月。
#   而这个脚本的职责就是把这种对不上摆出来，不是替谁圆场。
#   跑 `python3 forecast_demo.py --make-days 30` 可以看另一组的结果。


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="库存预测模型 —— 用负责人 Excel 的数字验证")
    ap.add_argument("--make-days", type=int, default=PARAMS["make_days"], help="生产周期（天）")
    ap.add_argument("--ship-days", type=int, default=PARAMS["ship_days"], help="货运周期（天）")
    a = ap.parse_args()
    PARAMS["make_days"], PARAMS["ship_days"] = a.make_days, a.ship_days


SKU = "DCC104132"

# 5 个店铺 / 3 个平台（照负责人 Excel A3 行）
SELLERS = [
    ("fba_us", "Amazon FBA 美国", "US", True),
    ("fba_de", "Amazon FBA 德国", "DE", True),
    ("fba_jp", "Amazon FBA 日本", "JP", True),
    ("shop_na", "Shopify 北美", "US", False),
    ("tt_us", "TikTok 美国", "US", False),
]

MONTHS = ["2026-09", "2026-10", "2026-11", "2026-12",
          "2027-01", "2027-02", "2027-03", "2027-04"]

# ── 负责人 Excel 的合计数（★ 这是待复现的标准答案，不参与计算）─────────
EXCEL = {
    #  月        期初  期望销量  当期入库  期末   计划采购  在途
    "2026-09": (500, 100, 0, 400, 0, 233),
    "2026-10": (400, 120, 133, 413, 110, 100),
    "2026-11": (413, 121, 80, 372, 111, 20),
    "2026-12": (372, 122, 20, 270, 112, 110),
    "2027-01": (270, 100, 0, 170, 80, 221),
}

# ── ★ 统计层：合计是从这里加上来的（负责人表里缺的就是这一层）──────────

OPENING = {  # 2026-09 期初可售，合计 = 500
    "fba_us": 200, "fba_de": 90, "fba_jp": 70, "shop_na": 90, "tt_us": 50,
}

DEMAND = {  # 期望销量（人填）：店铺 × 月
    "2026-09": {"fba_us": 40, "fba_de": 18, "fba_jp": 14, "shop_na": 18, "tt_us": 10},
    "2026-10": {"fba_us": 48, "fba_de": 22, "fba_jp": 17, "shop_na": 21, "tt_us": 12},
    "2026-11": {"fba_us": 48, "fba_de": 22, "fba_jp": 17, "shop_na": 22, "tt_us": 12},
    "2026-12": {"fba_us": 49, "fba_de": 22, "fba_jp": 17, "shop_na": 22, "tt_us": 12},
    "2027-01": {"fba_us": 40, "fba_de": 18, "fba_jp": 14, "shop_na": 18, "tt_us": 10},
    "2027-02": {"fba_us": 40, "fba_de": 18, "fba_jp": 14, "shop_na": 18, "tt_us": 10},
    "2027-03": {"fba_us": 40, "fba_de": 18, "fba_jp": 14, "shop_na": 18, "tt_us": 10},
    "2027-04": {"fba_us": 40, "fba_de": 18, "fba_jp": 14, "shop_na": 18, "tt_us": 10},
}

# ★ 已确认到货 = 事实：已发运、领星有预计到货日、目的地已定
INBOUND_CONFIRMED = {
    "2026-10": {"fba_us": 60, "fba_de": 30, "fba_jp": 20, "shop_na": 15, "tt_us": 8},   # 133
    "2026-11": {"fba_us": 35, "fba_de": 18, "fba_jp": 12, "shop_na": 10, "tt_us": 5},   # 80
    "2026-12": {"fba_us": 10, "fba_de": 5,  "fba_jp": 3,  "shop_na": 2,  "tt_us": 0},   # 20
}

# ★ 期初货运中（已发未到）= 233，正好被上面三笔排干
SHIPPING_OPENING = 233

PURCHASE = {  # 计划采购量（人填）：★ 货号级，不带店铺
    "2026-09": 0, "2026-10": 110, "2026-11": 111, "2026-12": 112,
    "2027-01": 80, "2027-02": 0, "2027-03": 0, "2027-04": 0,
}


# ════════════════════════════════════════════════════════════════════
# 1. 表结构 —— 与 docs/03 §7 一一对应
# ════════════════════════════════════════════════════════════════════

SCHEMA = """
-- 38 段做成数据，不做成代码
CREATE TABLE pipeline_segment (
    segment  TEXT PRIMARY KEY,
    ord      INTEGER NOT NULL UNIQUE,
    label    TEXT NOT NULL,
    directed INTEGER NOT NULL          -- ★ 这一段的量带不带店铺
);

-- 35 一次推演：★ 参数必须冻结
CREATE TABLE forecast_run (
    run_id  INTEGER PRIMARY KEY,
    sku     TEXT NOT NULL,
    as_of   TEXT NOT NULL,
    params  TEXT NOT NULL             -- jsonb
);

-- 36 ★ 可售侧最细颗粒度：店铺 × msku × 月
CREATE TABLE forecast_cell (
    run_id            INTEGER NOT NULL REFERENCES forecast_run(run_id),
    seller_id         TEXT    NOT NULL,
    sku               TEXT    NOT NULL,
    period            TEXT    NOT NULL,
    opening           INTEGER NOT NULL,
    demand_expected   INTEGER,                    -- ★ NULL = 未知，不是 0
    inbound_confirmed INTEGER NOT NULL DEFAULT 0, -- ★ 事实
    inbound_planned   INTEGER NOT NULL DEFAULT 0, -- ★ 假设
    closing           INTEGER,
    unknown           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, seller_id, period),
    -- ★ 恒等式③ 做成行级约束：这一层不成立就是漏了一个流向
    CHECK ( (unknown = 1 AND closing IS NULL)
         OR closing = opening - COALESCE(demand_expected,0)
                    + inbound_confirmed + inbound_planned ),
    CHECK (inbound_confirmed >= 0 AND inbound_planned >= 0)
);

-- 37 ★ 管道四段
CREATE TABLE forecast_pipeline (
    run_id     INTEGER NOT NULL REFERENCES forecast_run(run_id),
    sku        TEXT    NOT NULL,
    segment    TEXT    NOT NULL REFERENCES pipeline_segment(segment),
    seller_id  TEXT,
    period     TEXT    NOT NULL,
    opening    INTEGER NOT NULL,
    inflow     INTEGER NOT NULL,
    outflow    INTEGER NOT NULL,
    closing    INTEGER NOT NULL,
    -- ★ SQLite 不许生成列进主键，改用唯一表达式索引（见文件末 DIALECT_NOTES 第 7 条）
    -- ★★ 「排货这条线以下没有店铺」—— 从注释变成约束，写错了插不进去
    CHECK ( (segment IN ('making','local')    AND seller_id IS NULL)
         OR (segment IN ('staged','shipping') AND seller_id IS NOT NULL) ),
    CHECK (closing = opening + inflow - outflow),
    -- ★ 管道量必须非负：「生产中 −5 件」没有物理含义，出现就是算错了
    CHECK (opening >= 0 AND inflow >= 0 AND outflow >= 0 AND closing >= 0)
);
CREATE UNIQUE INDEX ux_pipeline ON forecast_pipeline
    (run_id, sku, segment, COALESCE(seller_id,''), period);

-- 39 ★ 每一笔入库的依据
CREATE TABLE forecast_inbound_trace (
    run_id        INTEGER NOT NULL REFERENCES forecast_run(run_id),
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id     TEXT    NOT NULL,
    arrive_period TEXT    NOT NULL,
    source        TEXT    NOT NULL CHECK (source IN ('confirmed','allocated')),
    units         INTEGER NOT NULL CHECK (units > 0),
    order_period  TEXT,
    alloc_basis   TEXT
);

-- 40 ★ 回测：实际销量是事实，不挂 run_id；captured_at 进主键（滞后采集会追加）
CREATE TABLE sales_actual (
    seller_id    TEXT NOT NULL,
    period       TEXT NOT NULL,
    actual_units INTEGER NOT NULL,
    completeness REAL,
    captured_at  TEXT NOT NULL,
    PRIMARY KEY (seller_id, period, captured_at)
);
"""

# ★ 合计只由视图现算 —— 没有合计表，所以恒等式①「合计 ≡ Σ店铺」
#   不靠断言保证，靠「根本不存在第二个真相」保证
VIEWS = """
CREATE VIEW v_forecast_sheet AS
SELECT c.run_id, c.sku, c.period,
       SUM(c.opening)                                        AS 期初库存,
       SUM(c.demand_expected)                                AS 期望销量,
       MAX(c.demand_expected IS NULL)                        AS 期望销量有未填,
       SUM(c.inbound_confirmed)                              AS 入库_已确认,
       SUM(c.inbound_planned)                                AS 入库_按计划推算,
       SUM(c.inbound_confirmed) + SUM(c.inbound_planned)     AS 当期入库量,
       SUM(c.closing)                                        AS 期末库存,
       (SELECT inflow  FROM forecast_pipeline p WHERE p.run_id=c.run_id
          AND p.sku=c.sku AND p.period=c.period AND p.segment='making')   AS 计划采购量,
       (SELECT closing FROM forecast_pipeline p WHERE p.run_id=c.run_id
          AND p.sku=c.sku AND p.period=c.period AND p.segment='making')   AS 生产中,
       (SELECT closing FROM forecast_pipeline p WHERE p.run_id=c.run_id
          AND p.sku=c.sku AND p.period=c.period AND p.segment='local')    AS 本地仓在库,
       (SELECT COALESCE(SUM(closing),0) FROM forecast_pipeline p WHERE p.run_id=c.run_id
          AND p.sku=c.sku AND p.period=c.period AND p.segment='staged')   AS 排货中,
       (SELECT COALESCE(SUM(closing),0) FROM forecast_pipeline p WHERE p.run_id=c.run_id
          AND p.sku=c.sku AND p.period=c.period AND p.segment='shipping') AS 货运中
  FROM forecast_cell c
 GROUP BY c.run_id, c.sku, c.period;

-- ★ 核对视图：返回 0 行才算对。每轮推演必跑，非空整轮作废
CREATE VIEW v_forecast_check AS
-- ① 恒等式④：期初[m] ≡ 期末[m−1]（跨行，行级 CHECK 做不到）
SELECT 'cell_rollover' AS rule, c.run_id, c.seller_id AS grain, c.period,
       c.opening AS got, prev.closing AS want
  FROM forecast_cell c JOIN forecast_cell prev
    ON prev.run_id=c.run_id AND prev.seller_id=c.seller_id
   AND prev.period = (SELECT MAX(period) FROM forecast_cell x
                       WHERE x.run_id=c.run_id AND x.seller_id=c.seller_id
                         AND x.period < c.period)
 WHERE c.opening IS NOT prev.closing
UNION ALL
-- ② 管道同上
SELECT 'pipe_rollover', p.run_id, p.segment||'/'||COALESCE(p.seller_id,''), p.period,
       p.opening, prev.closing
  FROM forecast_pipeline p JOIN forecast_pipeline prev
    ON prev.run_id=p.run_id AND prev.sku=p.sku AND prev.segment=p.segment
   AND COALESCE(prev.seller_id,'')=COALESCE(p.seller_id,'')
   AND prev.period = (SELECT MAX(period) FROM forecast_pipeline x
                       WHERE x.run_id=p.run_id AND x.sku=p.sku
                         AND x.segment=p.segment AND COALESCE(x.seller_id,'')=COALESCE(p.seller_id,'')
                         AND x.period < p.period)
 WHERE p.opening IS NOT prev.closing
UNION ALL
-- ③ ★ 某段的流出 ≡ 下一段的流入。对不上就是漏了货
SELECT 'segment_chain', cur.run_id, cur.segment||'→'||sn.segment, cur.period,
       SUM(cur.outflow),
       (SELECT COALESCE(SUM(n.inflow),0) FROM forecast_pipeline n
         WHERE n.run_id=cur.run_id AND n.sku=cur.sku
           AND n.segment=sn.segment AND n.period=cur.period)
  FROM forecast_pipeline cur
  JOIN pipeline_segment sc ON sc.segment=cur.segment
  JOIN pipeline_segment sn ON sn.ord=sc.ord+1
 GROUP BY cur.run_id, cur.sku, cur.segment, sn.segment, cur.period
HAVING SUM(cur.outflow) IS NOT
       (SELECT COALESCE(SUM(n.inflow),0) FROM forecast_pipeline n
         WHERE n.run_id=cur.run_id AND n.sku=cur.sku
           AND n.segment=sn.segment AND n.period=cur.period)
UNION ALL
-- ④ ★ 末段流出 ≡ 各店铺当期入库（管道与可售的接缝）
SELECT 'pipe_to_cell', p.run_id, 'shipping→cell', p.period,
       SUM(p.outflow),
       (SELECT COALESCE(SUM(inbound_confirmed+inbound_planned),0) FROM forecast_cell c
         WHERE c.run_id=p.run_id AND c.sku=p.sku AND c.period=p.period)
  FROM forecast_pipeline p WHERE p.segment='shipping'
 GROUP BY p.run_id, p.sku, p.period
HAVING SUM(p.outflow) IS NOT
       (SELECT COALESCE(SUM(inbound_confirmed+inbound_planned),0) FROM forecast_cell c
         WHERE c.run_id=p.run_id AND c.sku=p.sku AND c.period=p.period)
UNION ALL
-- ⑤ ★ 推算入库 ≡ 其构成明细之和（有结论必有依据）
SELECT 'inbound_trace', c.run_id, c.seller_id, c.period,
       c.inbound_planned,
       (SELECT COALESCE(SUM(units),0) FROM forecast_inbound_trace t
         WHERE t.run_id=c.run_id AND t.seller_id=c.seller_id
           AND t.arrive_period=c.period AND t.source='allocated')
  FROM forecast_cell c
 WHERE c.inbound_planned IS NOT
       (SELECT COALESCE(SUM(units),0) FROM forecast_inbound_trace t
         WHERE t.run_id=c.run_id AND t.seller_id=c.seller_id
           AND t.arrive_period=c.period AND t.source='allocated');
"""


# ════════════════════════════════════════════════════════════════════
# 2. 推演
# ════════════════════════════════════════════════════════════════════

def month_after_days(period, days):
    """★ 从某月**月初**起算 n 天，落在哪个月。天算、月显示（与 store.js 同一口径）"""
    import datetime
    d = datetime.date(int(period[:4]), int(period[5:7]), 1) + datetime.timedelta(days=days)
    return "%04d-%02d" % (d.year, d.month)


def shift(period, n):
    y, m = int(period[:4]), int(period[5:7])
    t = (y * 12 + m - 1) + n
    return "%04d-%02d" % (t // 12, t % 12 + 1)


def run_forecast(db, run_id, purchase, months, params=PARAMS):
    """返回并写库。★ 所有中间量都落库，否则无从核对。

    ★ 管道按**批次**算，不按月递推：一笔采购 = 一个批次，
      下单 → 生产 make_days → 发出 → 货运 ship_days → 可售。
      这样天数改成任意值都成立，不用迁就月边界。
    """
    T_make, T_sea = params["make_days"], params["ship_days"]
    sellers = [x[0] for x in SELLERS]
    idx = {p: i for i, p in enumerate(months)}

    # ── 批次 ─────────────────────────────────────────────────────
    batches = []
    for p in months:
        u = purchase.get(p, 0)
        if u <= 0:
            continue
        batches.append({
            "order": p, "units": u,
            "made": month_after_days(p, T_make),
            "arrive": month_after_days(p, T_make + T_sea),
        })

    # ── 管道四段的逐月余额 ────────────────────────────────────────
    mk_rows, lc_rows = [], []
    for p in months:
        making = sum(b["units"] for b in batches if b["order"] <= p < b["made"])
        made = sum(b["units"] for b in batches if b["made"] == p)
        mk_in = purchase.get(p, 0)
        mk_out = made
        mk_open = making - mk_in + mk_out
        mk_rows.append((p, max(0, mk_open), mk_in, mk_out, making))
        # ★ 完工即发（推式）：本地仓当月进当月出，结余 0
        lc_rows.append((p, 0, made, made, 0))

    # ── 第一遍 · 基线 ────────────────────────────────────────────
    base, gap = {}, {}
    for s_ in sellers:
        cur = OPENING[s_]
        for p in months:
            cur = cur - DEMAND[p][s_] + INBOUND_CONFIRMED.get(p, {}).get(s_, 0)
            base[(s_, p)] = cur
            gap[(s_, p)] = max(0, -cur)

    # ── 第二遍 · 分配 ────────────────────────────────────────────
    allocated = defaultdict(int)
    traces = []
    arrive_planned = defaultdict(int)
    ship_by = defaultdict(int)     # ★ (店铺, 发出月) -> 件数：管道对账用
    for bt in sorted(batches, key=lambda x: x["arrive"]):
        if bt["arrive"] not in idx:
            continue                      # 周期外，由 beyond 另行列出
        am, pool = bt["arrive"], bt["units"]
        need = {s_: max(0, gap.get((s_, am), 0) - allocated[s_]) for s_ in sellers}
        total = sum(need.values())
        w = {s_: DEMAND[am][s_] for s_ in sellers}
        tw = sum(w.values())
        basis = {}
        if total == 0:
            # ★ 谁都不缺，但货照发 —— 全按期望销量摊。
            #   按缺口卡会让货凭空消失，第一版就是这么算错的。
            give = _spread(pool, w, tw, sellers)
            for s_ in sellers:
                basis[s_] = {"rule": "无人缺货，按期望销量比例 %d/%d" % (w[s_], tw),
                             "gap_part": 0, "weight_part": give[s_], "pool": pool}
        elif total <= pool:
            extra = _spread(pool - total, w, tw, sellers) if pool > total else \
                    {s_: 0 for s_ in sellers}
            give = {s_: need[s_] + extra[s_] for s_ in sellers}
            for s_ in sellers:
                basis[s_] = {"rule": "缺口 %d 补齐，余量按期望销量再摊 %d" % (need[s_], extra[s_]),
                             "gap_part": need[s_], "weight_part": extra[s_], "pool": pool}
        else:
            give = {s_: pool * need[s_] // total for s_ in sellers}
            rest = pool - sum(give.values())
            for s_ in sorted(sellers, key=lambda x: -need[x])[:rest]:
                give[s_] += 1
            for s_ in sellers:
                basis[s_] = {"rule": "不够分，按缺口比例 %d/%d × %d" % (need[s_], total, pool),
                             "gap_part": give[s_], "weight_part": 0, "pool": pool}
        assert sum(give.values()) == pool, \
            "★ %s 分配合计 %d ≠ 这批货 %d —— 管道漏货了" % (am, sum(give.values()), pool)
        for s_ in sellers:
            if give[s_] <= 0:
                continue
            allocated[s_] += give[s_]
            arrive_planned[(s_, am)] += give[s_]
            ship_by[(s_, bt["made"])] += give[s_]
            traces.append((run_id, s_, am, 'allocated', give[s_], bt["order"],
                           json.dumps(basis[s_], ensure_ascii=False)))

    # ── 货运中（★ 带店铺）────────────────────────────────────────
    shipping = {s_: sum(v.get(s_, 0) for v in INBOUND_CONFIRMED.values()) for s_ in sellers}
    conf_total = sum(shipping.values())
    assert conf_total == SHIPPING_OPENING, \
        "期初货运中 %d 与已确认到货合计 %d 对不上" % (SHIPPING_OPENING, conf_total)

    sh_rows, st_rows = [], []
    for p in months:
        for s_ in sellers:
            inf = ship_by.get((s_, p), 0)
            outf = INBOUND_CONFIRMED.get(p, {}).get(s_, 0) + arrive_planned.get((s_, p), 0)
            st_rows.append((p, s_, 0, inf, inf, 0))     # 排货中：当月进当月出
            op = shipping[s_]
            shipping[s_] = op + inf - outf
            sh_rows.append((p, s_, op, inf, outf, shipping[s_]))

    # ── 可售侧 ───────────────────────────────────────────────────
    cells = []
    for s_ in sellers:
        cur = OPENING[s_]
        for p in months:
            d = DEMAND[p][s_]
            ic = INBOUND_CONFIRMED.get(p, {}).get(s_, 0)
            ip = arrive_planned.get((s_, p), 0)
            op = cur
            cur = op - d + ic + ip
            cells.append((run_id, s_, SKU, p, op, d, ic, ip, cur, 0))
            if ic:
                traces.append((run_id, s_, p, 'confirmed', ic, None, None))

    db.executemany("INSERT INTO forecast_cell VALUES (?,?,?,?,?,?,?,?,?,?)", cells)
    db.executemany(
        "INSERT INTO forecast_pipeline (run_id,sku,segment,seller_id,period,opening,inflow,outflow,closing)"
        " VALUES (?,?,'making',NULL,?,?,?,?,?)",
        [(run_id, SKU, p, o, i, of, c) for (p, o, i, of, c) in mk_rows])
    db.executemany(
        "INSERT INTO forecast_pipeline (run_id,sku,segment,seller_id,period,opening,inflow,outflow,closing)"
        " VALUES (?,?,'local',NULL,?,?,?,?,?)",
        [(run_id, SKU, p, o, i, of, c) for (p, o, i, of, c) in lc_rows])
    db.executemany(
        "INSERT INTO forecast_pipeline (run_id,sku,segment,seller_id,period,opening,inflow,outflow,closing)"
        " VALUES (?,?,'staged',?,?,?,?,?,?)",
        [(run_id, SKU, s_, p, o, i, of, c) for (p, s_, o, i, of, c) in st_rows])
    db.executemany(
        "INSERT INTO forecast_pipeline (run_id,sku,segment,seller_id,period,opening,inflow,outflow,closing)"
        " VALUES (?,?,'shipping',?,?,?,?,?,?)",
        [(run_id, SKU, s_, p, o, i, of, c) for (p, s_, o, i, of, c) in sh_rows])
    db.executemany(
        "INSERT INTO forecast_inbound_trace"
        " (run_id,seller_id,arrive_period,source,units,order_period,alloc_basis)"
        " VALUES (?,?,?,?,?,?,?)", traces)
    db.commit()


def _spread(amount, w, tw, sellers):
    """按权重摊一个整数，余数给权重最大的 —— ★ 合计必须等于 amount"""
    out = {s_: amount * w[s_] // tw for s_ in sellers}
    rest = amount - sum(out.values())
    for s_ in sorted(sellers, key=lambda x: -w[x])[:rest]:
        out[s_] += 1
    return out



# ════════════════════════════════════════════════════════════════════
# 3. 打印
# ════════════════════════════════════════════════════════════════════

def table(headers, rows, title=None, note=None):
    if title:
        print("\n" + title)
    w = [len(str(h)) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            w[i] = max(w[i], len(str(c)))

    def wide(s):   # 中文占两格
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))
    w = [max(wide(h), max([wide(r[i]) for r in rows] or [0])) for i, h in enumerate(headers)]

    def pad(s, n):
        return str(s) + " " * (n - wide(s))
    print("  " + " │ ".join(pad(h, w[i]) for i, h in enumerate(headers)))
    print("  " + "─┼─".join("─" * x for x in w))
    for r in rows:
        print("  " + " │ ".join(pad(c, w[i]) for i, c in enumerate(r)))
    if note:
        print("  " + note)


def main():
    _cli()
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.executescript(VIEWS)
    db.executemany("INSERT INTO pipeline_segment VALUES (?,?,?,?)", [
        ('making',   1, '生产中',             0),
        ('local',    2, '本地仓在库(待排货)',  0),
        ('staged',   3, '排货中(已排未发)',    1),   # ★ 从这里起，货有了店铺
        ('shipping', 4, '货运中(已发未到)',    1),
    ])

    db.execute("INSERT INTO forecast_run VALUES (1,?,?,?)",
               (SKU, "2026-09-01", json.dumps(PARAMS, ensure_ascii=False)))
    run_forecast(db, 1, PURCHASE, MONTHS)

    print("=" * 78)
    print("第 1 步 · 我算出来的合计表  vs  你 Excel 里的数")
    print("=" * 78)
    rows, diffs = [], 0
    for p in MONTHS:
        r = db.execute(
            "SELECT 期初库存,期望销量,当期入库量,期末库存,计划采购量,货运中"
            " FROM v_forecast_sheet WHERE period=?", (p,)).fetchone()
        if p in EXCEL:
            e = EXCEL[p]
            ok = tuple(r) == e
            diffs += 0 if ok else 1
            mark = "✓ 一致" if ok else "✗ 不一致 期望=" + str(e)
        else:
            mark = "（Excel 未覆盖）"
        rows.append((p,) + tuple(r) + (mark,))
    table(["月", "期初库存", "期望销量", "当期入库", "期末库存", "计划采购", "货运中(在途)", "对照"],
          rows,
          note="★ 「货运中」= 你表里的「在途库存」。判据：前 5 个月必须逐格一致。")
    print("\n  " + ("★ 全部一致 —— 模型复现了你的表" if diffs == 0
                    else "✗ 有 %d 个月对不上，我的理解错了" % diffs))

    print("\n" + "=" * 78)
    print("第 2 步 · ★ 统计过程 —— 合计行的每个数是从这里加上来的")
    print("=" * 78)
    for p in ("2026-09", "2026-10"):
        rows = db.execute(
            "SELECT seller_id,opening,demand_expected,inbound_confirmed,"
            "inbound_planned,closing FROM forecast_cell"
            " WHERE period=? ORDER BY seller_id", (p,)).fetchall()
        name = {s[0]: s[1] for s in SELLERS}
        body = [(name[r[0]],) + r[1:] for r in rows]
        tot = tuple(sum(r[i] for r in rows) for i in range(1, 6))
        body.append(("★ 合计",) + tot)
        table(["店铺", "期初", "期望销量", "入库·已确认", "入库·推算", "期末"], body,
              title="  ── %s ──" % p)
    print("\n  ★ 合计不落表，只由视图现算 —— 存了合计就是第二个真相。")

    print("\n" + "=" * 78)
    print("第 3 步 · ★ 管道四段（你 Excel 预留、没填的那三行）")
    print("=" * 78)
    rows = []
    for p in MONTHS:
        r = db.execute("SELECT 计划采购量,生产中,本地仓在库,排货中,货运中"
                       " FROM v_forecast_sheet WHERE period=?", (p,)).fetchone()
        rows.append((p,) + tuple(x if x is not None else 0 for x in r)
                    + (sum(x or 0 for x in r[1:]),))
    table(["月", "计划采购量↓", "生产中", "本地仓在库", "排货中", "货运中", "管道合计"], rows,
          note="★ 左边两段无店铺归属，右边两段有 —— 分界线是「排货」这个动作。")

    print("\n" + "=" * 78)
    print("第 4 步 · ★ 自检：返回 0 行才算对")
    print("=" * 78)
    bad = db.execute("SELECT * FROM v_forecast_check").fetchall()
    if bad:
        table(["规则", "run", "粒度", "月", "实际", "应为"], bad)
        print("\n  ✗ 自检不通过 —— 整轮作废")
    else:
        print("\n  ✓ 0 行。四条恒等式全部成立：")
        print("    ① 期初[m] ≡ 期末[m−1]          （可售侧）")
        print("    ② 期初[m] ≡ 期末[m−1]          （管道各段）")
        print("    ③ 某段流出 ≡ 下一段流入         ★ 对不上就是漏了货")
        print("    ④ 货运中流出 ≡ 各店当期入库     ★ 管道与可售的接缝")
        print("    ⑤ 推算入库 ≡ 其构成明细之和     ★ 有结论必有依据")

    print("\n" + "=" * 78)
    print("第 5 步 · ★★ 你最初的问题：改计划采购量，库存到底动不动？")
    print("=" * 78)
    db2 = sqlite3.connect(":memory:")
    db2.executescript(SCHEMA); db2.executescript(VIEWS)
    db2.executemany("INSERT INTO pipeline_segment VALUES (?,?,?,?)", [
        ('making', 1, '生产中', 0), ('local', 2, '本地仓在库', 0),
        ('staged', 3, '排货中', 1), ('shipping', 4, '货运中', 1)])
    db2.execute("INSERT INTO forecast_run VALUES (1,?,?,?)",
                (SKU, "2026-09-01", json.dumps(PARAMS, ensure_ascii=False)))
    what_if = dict(PURCHASE); what_if["2026-10"] = 600      # 110 → 600
    run_forecast(db2, 1, what_if, MONTHS)

    rows = []
    for p in MONTHS:
        a = db.execute("SELECT 期末库存 FROM v_forecast_sheet WHERE period=?", (p,)).fetchone()[0]
        b = db2.execute("SELECT 期末库存 FROM v_forecast_sheet WHERE period=?", (p,)).fetchone()[0]
        d = b - a
        rows.append((p, a, b, ("+%d" % d) if d > 0 else ("%d" % d if d else "— 不变")))
    table(["月", "采购 110", "★ 采购 600", "差"], rows,
          title="  2026-10 的计划采购量：110 → 600，期末库存的变化",
          note="★ 前几个月纹丝不动 —— 不是模型没算，是 10 月下的单 %d 月才到。"
               % int(month_after_days("2026-10", PARAMS["make_days"] + PARAMS["ship_days"])[5:7]))

    print("\n  这批货到各店的分配（★ 每一笔都带依据）：")
    tr = db2.execute(
        "SELECT arrive_period,seller_id,units,order_period,alloc_basis"
        " FROM forecast_inbound_trace WHERE source='allocated'"
        "   AND order_period='2026-10' ORDER BY arrive_period,seller_id").fetchall()
    name = {s[0]: s[1] for s in SELLERS}
    table(["到货月", "店铺", "件数", "下单月", "依据"],
          [(r[0], name[r[1]], r[2], r[3], r[4]) for r in tr] or [("—", "—", "—", "—", "无")])

    print("\n" + "=" * 78)
    print("★ 如果第 1 步全部一致、第 4 步 0 行，就说明我把你的 Excel 理解对了。")
    print("  对不上的地方，直接指出来 —— 那就是我理解错的地方。")
    print("=" * 78)
    return 0 if diffs == 0 and not bad else 1


DIALECT_NOTES = """
SQLite 与 PostgreSQL 的差异（生产用 PG，见 docs/03 §7）
--------------------------------------------------------
1. 本文件用 SQLite 只为「零安装、立刻能跑」；PG 的 DDL 在 docs/03 §7。
2. PG 里 `IS DISTINCT FROM`，SQLite 写 `IS NOT`，语义相同。
3. PG 用 `FILTER (WHERE ...)` 与 `LATERAL`；SQLite 改写成相关子查询，结果一致。
4. PG 的 `bool_or()` ↔ SQLite 的 `MAX(expr)`（布尔按 0/1 存）。
5. ★ CHECK 约束两边完全一样 —— 这是最要紧的部分，不许有方言差异。
6. 生产的建表只走迁移文件 migrations/pg/NNN_*.sql，不许手工 psql 改结构。
"""

if __name__ == "__main__":
    sys.exit(main())
