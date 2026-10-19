"""新建 FBA 排货表时的默认分配（UX-D7 ~ D10 · spec 2026-10-16 §1.2）。纯函数，离线。"""
from rules.autofill import allocate, split

W1, W2 = 1, 2
SKU = "SKU-1"
SELLERS = {"A": {"has_fba": True}, "B": {"has_fba": True}, "N": {"has_fba": False}}
BRIDGE = {("MA", "A"): SKU, ("MA2", "A"): SKU, ("MB", "B"): SKU, ("MN", "N"): SKU, ("MA-FBM", "A"): SKU,
          ("OTHER", "A"): "SKU-2"}


def line(lid, pending, dbs, period="2026-10-01", sku=SKU):
    return {"line_id": lid, "sku": sku, "period": period, "pending": pending, "demand_by_seller": dbs}


def store(units, *mskus):
    """mskus: (seller_sku, units) …"""
    return {"units": units, "basis": "human", "mskus": [{"seller_sku": m, "units": u, "basis": "human"} for m, u in mskus]}


def run(lines, buckets, committed=None):
    return allocate(lines, SELLERS, BRIDGE, buckets, committed or {})


def cells(r):
    return sorted((c["line_id"], c["seller_sku"], c["sid"], c["from_wid"], c["units"]) for c in r["filled"])


def reasons(r):
    return sorted((s["line_id"], s.get("sid") or "", s.get("seller_sku") or "", s["reason"]) for s in r["skipped"])


def test_split_largest_remainder_sums_exactly():
    assert split(100, {"A": 1, "B": 1, "C": 1}) == {"A": 34, "B": 33, "C": 33}
    assert split(7, {"A": 541, "B": 100}) == {"A": 6, "B": 1}
    assert sum(split(99, {"A": 3, "B": 5, "C": 11}).values()) == 99


def test_single_store_gets_all_of_still_needed_capped_by_available():
    r = run([line(1, 100, {"A": store(541, ("MA", 541))})], {(SKU, W1): {"shared": 80, "A": 5}})
    assert cells(r) == [(1, "MA", "A", W1, 85)]
    assert reasons(r) == [(1, "", "", "stock_short")]
    short = r["skipped"][0]
    assert (short["wanted"], short["filled"]) == (100, 85)


def test_split_by_store_expected_ratio():
    """UX-D8：还差按各店期望比例拆到店。"""
    r = run([line(1, 90, {"A": store(200, ("MA", 200)), "B": store(100, ("MB", 100))})],
            {(SKU, W1): {"shared": 1000}})
    assert cells(r) == [(1, "MA", "A", W1, 60), (1, "MB", "B", W1, 30)]
    assert r["skipped"] == []


def test_store_with_unknown_expected_is_named_not_given_a_share():
    """空 ≠ 0：两家店里一家期望为空 → 不分给它，点名；另一家拿全部。"""
    r = run([line(1, 50, {"A": store(None, ("MA", None)), "B": store(10, ("MB", 10))})],
            {(SKU, W1): {"shared": 1000}})
    assert cells(r) == [(1, "MB", "B", W1, 50)]
    assert reasons(r) == [(1, "A", "", "expected_unknown")]


def test_single_store_with_unknown_expected_still_gets_all():
    """只有一家店时比例与期望无关（UX-Q10）。"""
    r = run([line(1, 50, {"A": store(None, ("MA", None))})], {(SKU, W1): {"shared": 1000}})
    assert cells(r) == [(1, "MA", "A", W1, 50)]


def test_months_first_consume_stock_later_months_named():
    """UX-D9：按月份先后，库存用完为止；没填的月份写明原因。"""
    r = run([line(2, 100, {"A": store(1, ("MA", 1))}, period="2026-11-01"),
             line(1, 100, {"A": store(1, ("MA", 1))}, period="2026-10-01"),
             line(3, 100, {"A": store(1, ("MA", 1))}, period="2026-12-01")],
            {(SKU, W1): {"shared": 150}})
    assert cells(r) == [(1, "MA", "A", W1, 100), (2, "MA", "A", W1, 50)]
    assert reasons(r) == [(2, "", "", "stock_short"), (3, "", "", "no_stock")]


def test_committed_elsewhere_is_not_available():
    r = run([line(1, 100, {"A": store(1, ("MA", 1))})], {(SKU, W1): {"shared": 100}}, {(SKU, W1): {"A": 70}})
    assert cells(r) == [(1, "MA", "A", W1, 30)]


def test_other_stores_bucket_is_not_usable_and_spills_to_next_warehouse():
    """CA-D8：本店桶 + 共享桶，别店桶不可用；一个仓不够溢到下一个（可安排多的先取）。"""
    r = run([line(1, 100, {"A": store(1, ("MA", 1))})],
            {(SKU, W1): {"shared": 10, "B": 500}, (SKU, W2): {"shared": 60, "A": 5}})
    assert cells(r) == [(1, "MA", "A", W1, 10), (1, "MA", "A", W2, 65)]


def test_split_inside_store_by_claimed_msku_expected():
    r = run([line(1, 40, {"A": store(40, ("MA", 30), ("MA2", 10))})], {(SKU, W1): {"shared": 1000}})
    assert cells(r) == [(1, "MA", "A", W1, 30), (1, "MA2", "A", W1, 10)]


def test_fbm_msku_and_unclaimed_and_wrong_sku_are_not_filled():
    """只填计划认领的 msku（demand_by_seller.mskus）；-FBM 不进 FBA 表（UX-Q12）；msku 对应别的货号不填。"""
    r = run([line(1, 40, {"A": store(40, ("MA-FBM", 30), ("OTHER", 5), ("MA", 5))})], {(SKU, W1): {"shared": 1000}})
    assert cells(r) == [(1, "MA", "A", W1, 40)]
    assert reasons(r) == [(1, "A", "MA-FBM", "fbm_msku"), (1, "A", "OTHER", "msku_not_this_sku")]


def test_store_reasons_no_fba_not_in_mirror_no_claim():
    r = run([line(1, 40, {"N": store(5, ("MN", 5)), "X": store(5, ("MX", 5)), "A": store(5),
                          "B": store(5, ("MB", 5))})], {(SKU, W1): {"shared": 1000}})
    assert cells(r) == [(1, "MB", "B", W1, 40)]
    assert reasons(r) == [(1, "A", "", "no_claimed_msku"), (1, "N", "", "no_fba"),
                          (1, "X", "", "seller_not_in_mirror")]


def test_line_with_nothing_to_fill_is_named():
    r = run([line(1, 0, {"A": store(1, ("MA", 1))}), line(2, 10, {"A": store(1)})], {(SKU, W1): {"shared": 9}})
    assert r["filled"] == []
    assert reasons(r) == [(1, "", "", "satisfied"), (2, "", "", "no_eligible_store"),
                          (2, "A", "", "no_claimed_msku")]


def test_stock_shared_across_skus_is_per_sku():
    r = run([line(1, 10, {"A": store(1, ("MA", 1))}), line(2, 10, {"A": store(1, ("OTHER", 1))}, sku="SKU-2")],
            {(SKU, W1): {"shared": 5}, ("SKU-2", W1): {"shared": 7}})
    assert cells(r) == [(1, "MA", "A", W1, 5), (2, "OTHER", "A", W1, 7)]
