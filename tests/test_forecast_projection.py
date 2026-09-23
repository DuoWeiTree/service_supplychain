"""inventory_projection —— 主公式 期末 = 期初 − 期望销量 + 当期入库（14 §0）。

★ 阶段 A 的入库量只有「已确认」这一种（CH 的采购在途），
  本计划的计划采购量**不进**这条公式，而这件事必须在每一格上说出来。
"""
import itertools

import pytest

from forecast.projection import InboundSource, PeriodMismatch, inventory_projection

P = ["2026-10", "2026-11", "2026-12"]


def src(units, ref="PO-1"):
    return [InboundSource(units, "purchase_in_transit", ref)]


def test_main_formula_holds_in_every_cell():
    """★ 原型 test_forecast.js §4 第一条。"""
    rows = inventory_projection(300, {"2026-10": src(50)}, dict(zip(P, [120, 100, 80])))
    assert rows, "一行都没有 —— 下面的遍历是空转的"
    for r in rows:
        assert r["closing"] == r["opening"] - r["demand"] + r["inbound"]
    assert [r["closing"] for r in rows] == [230, 130, 50]


def test_opening_is_last_months_closing():
    """★ 原型 test_forecast.js §4 第二条（恒等式④）。"""
    rows = inventory_projection(300, {}, dict(zip(P, [120, 100, 80])))
    for prev, cur in itertools.pairwise(rows):
        assert cur["opening"] == prev["closing"]


def test_inbound_equals_the_sum_of_its_sources():
    """★ 恒等式⑥：有结论必有依据（原型 §5）。合计由本函数求和，不靠调用方自觉。"""
    rows = inventory_projection(0, {"2026-10": [InboundSource(30, "purchase_in_transit", "PO-1"),
                                                InboundSource(20, "purchase_in_transit", "PO-2")]},
                                dict(zip(P, [0, 0, 0])))
    first = rows[0]
    assert first["inbound"] == sum(s["units"] for s in first["basis"]["sources"]) == 50


def test_basis_echoes_every_source_verbatim():
    """★ 原型 test_forecast.js §5「依据里写明了用的哪条规则」。

    合计对得上还不够 —— 一个数要能回答「它是关于什么的」。
    50 件是哪两张单、各是什么性质，必须原样读得回来，否则回显的只是它自己。

    ★ 两笔的 kind 必须**不同**：全用同一个 kind 的话，「逐笔回显」和
      「写死一个常量」给出的结果一模一样，这条断言就分辨不出它们
      （变异 M11 实测能带着这条测试一起绿）。
    """
    rows = inventory_projection(
        0,
        {"2026-10": [InboundSource(30, "purchase_in_transit", "PO-1"),
                     InboundSource(20, "plan_purchase", "PLAN-1")]},
        dict(zip(P, [0, 0, 0])),
    )
    assert [(s["units"], s["kind"], s["ref"]) for s in rows[0]["basis"]["sources"]] == [
        (30, "purchase_in_transit", "PO-1"),
        (20, "plan_purchase", "PLAN-1"),
    ]
    assert rows[1]["basis"]["sources"] == [], "没有入库的月份要给空明细，不是缺这个键"


def test_every_cell_says_the_plan_purchase_is_not_counted():
    """★ 「没算进来」不能和「算进来了但是 0」长得一样（00e:43）。"""
    rows = inventory_projection(10, {}, dict(zip(P, [1, 1, 1])))
    assert all(r["basis"]["excludes_plan_purchase"] is True for r in rows)


def test_unknown_demand_propagates_forward_and_is_not_zero():
    """★ M-8：留空 = 未知，向后传染，不当 0。"""
    rows = inventory_projection(100, {}, {"2026-10": 30, "2026-11": None, "2026-12": 10})
    assert [r["closing"] for r in rows] == [70, None, None]
    assert [r["unknown"] for r in rows] == [False, True, True]
    assert rows[1]["basis"]["reason"] == "unknown_demand"
    # ★ 派生字段必须跟着未知走。closing 是 None 而 gap 是 0，等于替人回答了
    #   「缺多少」——「不知道缺多少」和「一件都不缺」处置完全不同。
    assert [r["gap"] for r in rows] == [0, None, None]
    assert [r["shortage"] for r in rows] == [False, None, None]


def test_not_applicable_is_a_third_state():
    """★ 三种状态必须两两分得开：0（真的没货）· 未知（人没填）· 不适用（该平台无 FBA）。"""
    zero = inventory_projection(0, {}, {"2026-10": 0})
    unknown = inventory_projection(100, {}, {"2026-10": None})
    na = inventory_projection(None, {}, {"2026-10": 5})
    assert zero[0]["closing"] == 0 and not zero[0]["unknown"] and not zero[0]["not_applicable"]
    assert unknown[0]["closing"] is None and unknown[0]["unknown"]
    assert na[0]["closing"] is None and na[0]["not_applicable"]
    assert na[0]["basis"]["reason"] == "not_applicable"
    assert not na[0]["unknown"], "不适用不是未知 —— 处置完全不同"
    # ★ 同理：该店铺压根没有这门生意，「缺 0 件」是个不存在的答案
    assert na[0]["gap"] is None and na[0]["shortage"] is None
    assert zero[0]["gap"] == 0 and zero[0]["shortage"] is False, "真的不缺货才是 0/False"


def test_shortage_and_gap_are_reported():
    rows = inventory_projection(50, {}, {"2026-10": 80})
    assert rows[0]["closing"] == -30 and rows[0]["shortage"] and rows[0]["gap"] == 30


def test_inbound_in_a_period_nobody_planned_is_refused():
    """★ 被丢掉的那一侧要硬失败：入库落在期望销量没有的月份上，静默忽略就是漏货。"""
    with pytest.raises(PeriodMismatch) as ei:
        inventory_projection(0, {"2027-05": src(10)}, {"2026-10": 1})
    assert "2027-05" in str(ei.value)


# ════════════════════════════════════════════════════════════════════
# ★★ 逐格对账：负责人自己的 Excel
#
# `docs/库存预测计算方式.xlsx` 是负责人手做的。判据不是「算得对」，是
# **他能不能拿自己的表逐格核对**。下面这组数照抄
# `docs/prototype/forecast_demo.py:69` 的 EXCEL 常量（该文件的存在理由就是复现它）。
#
# ★ 那个常量另有「计划采购 / 在途」两列 —— 属四段管道（阶段 B/C），
#   阶段 A 不算，所以**不抄进来**：抄了就等于假装算过。
#
# ★ 为什么剔除计划采购后还能与 EXCEL 四列逐格相同：
#   EXCEL 的「当期入库」只含**已确认在途**。阶段 A 剔除计划采购后的结果，
#   等价于 `forecast_demo.py --make-days 75` 下的结果 —— 实测该提前期下
#   demo 打印「★ 全部一致 —— 模型复现了你的表」，六列五个月全中，
#   因为计划采购一律在 2027-01 之后才到货，落不进这个窗口。
#   ★ 62 天**不够**：那一档 2027-01 会进 110 件（demo 4/5）。
#   demo 的默认 30 天对不上 4 个月 —— 错的是默认提前期，不是 demo。
#   ★★ 「75 行而 62 不行」这个阈值是**锚点相关**的：demo 从月初起算，
#      而 store.js 的 MONTH_ANCHOR_DAY = 15（月中），按月中锚 62 那档反而出窗。
#      两份原型对同一输入给不同月份 —— 阶段 A 不做管道所以不受影响，
#      但这个数别被抄进文档当成无条件成立的。
# ════════════════════════════════════════════════════════════════════

#: 月 → (期初, 期望销量, 当期入库, 期末)
EXCEL = {
    "2026-09": (500, 100, 0, 400),
    "2026-10": (400, 120, 133, 413),
    "2026-11": (413, 121, 80, 372),
    "2026-12": (372, 122, 20, 270),
    "2027-01": (270, 100, 0, 170),
}

#: ★ 统计层：合计是从这里加上来的（负责人表里缺的就是这一层）。
#  照抄 forecast_demo.py 的 OPENING / DEMAND / INBOUND_CONFIRMED。
SELLERS = ["fba_us", "fba_de", "fba_jp", "shop_na", "tt_us"]

OPENING = {"fba_us": 200, "fba_de": 90, "fba_jp": 70, "shop_na": 90, "tt_us": 50}

DEMAND = {
    "2026-09": {"fba_us": 40, "fba_de": 18, "fba_jp": 14, "shop_na": 18, "tt_us": 10},
    "2026-10": {"fba_us": 48, "fba_de": 22, "fba_jp": 17, "shop_na": 21, "tt_us": 12},
    "2026-11": {"fba_us": 48, "fba_de": 22, "fba_jp": 17, "shop_na": 22, "tt_us": 12},
    "2026-12": {"fba_us": 49, "fba_de": 22, "fba_jp": 17, "shop_na": 22, "tt_us": 12},
    "2027-01": {"fba_us": 40, "fba_de": 18, "fba_jp": 14, "shop_na": 18, "tt_us": 10},
}

INBOUND_CONFIRMED = {
    "2026-10": {"fba_us": 60, "fba_de": 30, "fba_jp": 20, "shop_na": 15, "tt_us": 8},
    "2026-11": {"fba_us": 35, "fba_de": 18, "fba_jp": 12, "shop_na": 10, "tt_us": 5},
    "2026-12": {"fba_us": 10, "fba_de": 5, "fba_jp": 3, "shop_na": 2, "tt_us": 0},
}


def test_reproduces_owner_excel_when_plan_purchases_are_excluded():
    """★ 合计口径：负责人 Excel 的四列 × 五个月，逐格相同。"""
    rows = inventory_projection(
        EXCEL["2026-09"][0],
        {m: [InboundSource(EXCEL[m][2], "purchase_in_transit", f"CH-{m}")]
         for m in EXCEL if EXCEL[m][2]},
        {m: EXCEL[m][1] for m in EXCEL},
    )
    assert len(rows) == len(EXCEL), "月份少了 —— 下面的逐格比对会漏掉没生成的那些"
    got = {r["period"]: (r["opening"], r["demand"], r["inbound"], r["closing"]) for r in rows}
    assert got == EXCEL


def test_reproduces_owner_excel_from_the_per_seller_layer():
    """★ 恒等式①：合计 ≡ Σ店铺。Excel 只有合计行，这一层是我们补的 ——
    所以必须证明补出来的那层加起来还等于他的表，而不是各算各的。"""
    per_seller = {}
    for s in SELLERS:
        per_seller[s] = inventory_projection(
            OPENING[s],
            {m: [InboundSource(INBOUND_CONFIRMED[m][s], "purchase_in_transit", f"CH-{m}-{s}")]
             for m in INBOUND_CONFIRMED},
            {m: DEMAND[m][s] for m in DEMAND},
        )

    mismatched = []
    for i, period in enumerate(sorted(EXCEL)):
        summed = tuple(sum(per_seller[s][i][k] for s in SELLERS)
                       for k in ("opening", "demand", "inbound", "closing"))
        if summed != EXCEL[period]:
            mismatched.append(f"{period}: Σ店铺 {summed} ≠ Excel {EXCEL[period]}")
    assert not mismatched, mismatched


def test_owner_excel_recon_breaks_if_a_plan_purchase_is_counted():
    """★ 证明上面两条对账有证伪力，不是碰巧绿的。

    2026-10 的计划采购 110 件若被注回入库（demo 在默认 30 天下正是把它算到了 2026-12），
    2026-12 的期末就变成 380 —— 而负责人表里是 270。
    对账能被一笔 110 件推离，说明它确实在比对这条公式，而不是在比对两个常量。
    """
    rows = inventory_projection(
        EXCEL["2026-09"][0],
        {m: [InboundSource(EXCEL[m][2], "purchase_in_transit", f"CH-{m}")]
         for m in EXCEL if EXCEL[m][2]}
        | {"2026-12": [InboundSource(EXCEL["2026-12"][2], "purchase_in_transit", "CH-2026-12"),
                       InboundSource(110, "plan_purchase", "PLAN-2026-10")]},
        {m: EXCEL[m][1] for m in EXCEL},
    )
    dec = next(r for r in rows if r["period"] == "2026-12")
    assert dec["closing"] == 380, "掺进计划采购后必须偏离 Excel —— 否则上面的对账不具证伪力"
    assert dec["closing"] != EXCEL["2026-12"][3]
    # ★ 标记不许撒谎：这一格确实把计划采购算进来了，它就不能再说自己没算
    assert dec["basis"]["excludes_plan_purchase"] is False
    assert all(r["basis"]["excludes_plan_purchase"] is True
               for r in rows if r["period"] != "2026-12"), "别的月份没掺，标记不该跟着翻"
