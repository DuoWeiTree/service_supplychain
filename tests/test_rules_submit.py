"""提交闸：铸出哪些、跳过哪些、冻结成什么。

★ 判据② 要证的不只是「有留痕」，还要证「该跳的都跳了、不该跳的没跳」。
"""
import pytest

from rules.digest import content_digest
from rules.effective import effective_demand
from rules.submit import DemandCell, PurchaseCell, select_submittable

SKU = "DCC1800264G1"
OCT, NOV = "2026-10", "2026-11"
A, B = ("MSKU-A", "11072"), ("MSKU-C", "11094")


def dcell(m, period, system, expected):
    return DemandCell(m[0], m[1], SKU, period, system, expected)


def test_effective_value_keeps_both_the_number_and_where_it_came_from():
    """★ S-15：生效值取 COALESCE(人填, 系统)，但 basis 必须记下来 ——
    不记的话，冻结之后再也分不清这 300 件是人写的还是采用了预估。"""
    assert effective_demand(120, 130) == (130, "human")
    assert effective_demand(120, None) == (120, "system")
    assert effective_demand(None, None) == (None, "unknown")
    assert effective_demand(None, 0) == (0, "human"), "★ 人填 0 是明确表态，不是未填"


def test_mints_one_line_per_purchase_cell_with_units():
    lines, skipped = select_submittable(
        [PurchaseCell(SKU, OCT, 500)],
        [dcell(A, OCT, 100, 120), dcell(B, OCT, 40, None)],
        claimed={A, B})
    assert skipped == []
    assert len(lines) == 1
    m = lines[0]
    assert (m.sku, m.period, m.total_units) == (SKU, OCT, 500)
    assert m.demand_by_seller["11072"]["units"] == 120
    assert m.demand_by_seller["11072"]["basis"] == "human"
    assert m.demand_by_seller["11094"]["units"] == 40
    assert m.demand_by_seller["11094"]["basis"] == "system"
    assert m.demand_at_submit == 160


def test_zero_or_empty_purchase_is_skipped_with_its_reason():
    """★ 坑①：总量 0 的记录 0 = 0 恒真，一诞生就会自动跳到「已确认」。"""
    lines, skipped = select_submittable(
        [PurchaseCell(SKU, OCT, 0), PurchaseCell(SKU, NOV, None)],
        [dcell(A, OCT, 100, 120), dcell(A, NOV, 100, 120)], claimed={A})
    assert lines == []
    assert [(s.period, s.reason) for s in skipped] == [
        (OCT, "zero_purchase"), (NOV, "zero_purchase")]


def test_purchase_cell_without_any_claimed_msku_is_skipped():
    """释放了认领之后，货号级的采购格子还在 —— 它铸不出记录，必须点名。"""
    lines, skipped = select_submittable(
        [PurchaseCell(SKU, OCT, 500)], [], claimed=set())
    assert lines == []
    assert [(s.period, s.reason) for s in skipped] == [(OCT, "no_claimed_msku")]


def test_structural_reason_wins_over_zero():
    """两个理由同时成立时取哪个是定死的：没有落点是结构问题，先报它。
    不定死，同一份数据两次提交会给出两种理由，而 skipped[] 是要给人看的。"""
    _, skipped = select_submittable([PurchaseCell(SKU, OCT, 0)], [], claimed=set())
    assert skipped[0].reason == "no_claimed_msku"


def test_all_unknown_demand_is_frozen_as_null_not_zero():
    """★ 空 ≠ 0：一个都没填时，冻结值必须是 null + unknown，而不是 0。"""
    lines, _ = select_submittable(
        [PurchaseCell(SKU, OCT, 500)], [dcell(A, OCT, None, None)], claimed={A})
    entry = lines[0].demand_by_seller["11072"]
    assert entry["units"] is None and entry["basis"] == "unknown"
    assert lines[0].demand_at_submit == 0, "合计只加得起来的那部分"
    assert entry["mskus"] == [{"seller_sku": "MSKU-A", "units": None, "basis": "unknown"}]


def test_a_sellers_basis_is_the_weakest_of_its_mskus():
    """同一店铺下一个 msku 人填、一个没填 —— basis 取最弱的那档。
    取最强的那档，会把「有一半是猜的」说成「人填的」。"""
    lines, _ = select_submittable(
        [PurchaseCell(SKU, OCT, 500)],
        [dcell(A, OCT, 10, 20), dcell(("MSKU-B", "11072"), OCT, None, None)],
        claimed={A, ("MSKU-B", "11072")})
    entry = lines[0].demand_by_seller["11072"]
    assert entry["basis"] == "unknown" and entry["units"] == 20


def test_demand_of_an_unclaimed_msku_is_refused_not_silently_dropped():
    """★ 丢东西必须有声：格子里有个 msku 不在认领集合里 —— 硬失败并点名。"""
    with pytest.raises(ValueError) as ei:
        select_submittable([PurchaseCell(SKU, OCT, 500)], [dcell(B, OCT, 40, 40)], claimed={A})
    assert "MSKU-C" in str(ei.value)


def test_digest_is_stable_under_reordering_and_moves_when_a_value_changes():
    """★ S-13：sha256(规范化 JSON：purchase 行 + demand 行含 basis，键排序，NULL 保留为 null)。"""
    p = [PurchaseCell(SKU, OCT, 500), PurchaseCell(SKU, NOV, None)]
    d = [dcell(A, OCT, 100, 120), dcell(B, OCT, 40, None)]
    base = content_digest(p, d)
    assert base == content_digest(list(reversed(p)), list(reversed(d)))
    assert len(base) == 64
    assert base != content_digest(p, [dcell(A, OCT, 100, 121), dcell(B, OCT, 40, None)])


def test_clearing_a_value_changes_the_digest():
    """★ NULL 保留为 null，所以「把 120 删成空」是一次真实变更 ——
    digest 不动的话，dashboard 的 changed_since_submit[] 就会漏掉这个人。"""
    p = [PurchaseCell(SKU, OCT, 500)]
    assert content_digest(p, [dcell(A, OCT, 100, 120)]) != \
           content_digest(p, [dcell(A, OCT, 100, None)])


def test_mixed_batch_partitions_purchase_cells_into_minted_and_skipped():
    """★ 铸出 + 跳过 ≡ 采购格子：单独测每种 skip 理由不够 ——
    还要证一批混着来时，没有格子被两边都算了，也没有格子两边都没算到。"""
    SKU_A, SKU_B, SKU_C, SKU_D = "SKU-A", "SKU-B", "SKU-C", "SKU-D"
    MA, MB, MC = ("MSKU-1", "11072"), ("MSKU-2", "11072"), ("MSKU-3", "11072")
    purchase_cells = [
        PurchaseCell(SKU_A, OCT, 500),   # 有认领、有数量 → 铸出
        PurchaseCell(SKU_B, OCT, 300),   # 有认领、有数量 → 铸出
        PurchaseCell(SKU_C, OCT, 0),     # 有认领、数量为 0 → zero_purchase
        PurchaseCell(SKU_D, OCT, 200),   # 无认领 → no_claimed_msku
    ]
    demand_cells = [
        DemandCell(MA[0], MA[1], SKU_A, OCT, 100, 120),
        DemandCell(MB[0], MB[1], SKU_B, OCT, 50, None),
        DemandCell(MC[0], MC[1], SKU_C, OCT, 10, 10),
    ]
    lines, skipped = select_submittable(purchase_cells, demand_cells, claimed={MA, MB, MC})

    assert len(lines) + len(skipped) == len(purchase_cells) == 4
    assert {(s.sku, s.period, s.reason) for s in skipped} == {
        (SKU_C, OCT, "zero_purchase"),
        (SKU_D, OCT, "no_claimed_msku"),
    }
    assert {(m.sku, m.period) for m in lines} == {(SKU_A, OCT), (SKU_B, OCT)}


def test_digest_distinguishes_none_from_zero_not_just_from_a_different_number():
    """★ None ≠ 0 在 digest 里要两处都证：期望销量格与采购格各自的
    「没填」vs「填了 0」都必须挪动摘要，否则两者在留痕里长得一样。"""
    p = [PurchaseCell(SKU, OCT, 500)]
    assert content_digest(p, [dcell(A, OCT, 100, None)]) != \
           content_digest(p, [dcell(A, OCT, 100, 0)])

    d = [dcell(A, OCT, 100, 120)]
    assert content_digest([PurchaseCell(SKU, OCT, None)], d) != \
           content_digest([PurchaseCell(SKU, OCT, 0)], d)
