"""`dim/pm_source.py` 的门禁（Task 1 Step 3~6）。

全部离线、零 I/O——`fetch_sku_category` 的两个回调在这里全部是手写的桩，
不碰 `shared/pm_client.py`/`urllib`。真打 PM 的覆盖率数字在
`tests/test_pm_live.py`。
"""
from __future__ import annotations

import pytest

from dim import pm_source as P

# ══════════════════════════════════════════════════════════════════════
#  占位图判定：按 basename，不按完整 URL（Global Constraints ★★）
# ══════════════════════════════════════════════════════════════════════

def test_placeholder_basename_matches_regardless_of_domain():
    assert P.is_placeholder("https://a/x/default.png")
    assert P.is_placeholder("https://b/y/default.png")


def test_real_image_is_not_placeholder():
    assert not P.is_placeholder("https://a/x/real.jpg")


def test_no_url_is_placeholder():
    assert P.is_placeholder(None)
    assert P.is_placeholder("")


def test_domain_migration_does_not_defeat_the_placeholder_check():
    """★ 这条就是"不按完整 URL"的证人：换了域名/路径前缀，只要文件名还是
    default.png，仍然判占位——不许因为完整 URL 变了就判成真图。"""
    old = "https://cdn-old.zestrade.com/img/default.png"
    new = "https://static.newcdn.example.com/2026/09/default.png"
    assert P.is_placeholder(old)
    assert P.is_placeholder(new)


# ══════════════════════════════════════════════════════════════════════
#  build_tree：扁平化 + 面包屑 + 三条完整性校验
# ══════════════════════════════════════════════════════════════════════

def _leaf(code, name, parent=None, level=1):
    return {"category_code": code, "category_name": name,
            "parent_category_code": parent, "level": level,
            "is_leaf": True, "sort_order": 0, "children": []}


def test_build_tree_assembles_root_to_leaf_path():
    raw = [{
        "category_code": "C1", "category_name": "宠物用品",
        "parent_category_code": None, "level": 1, "is_leaf": False,
        "sort_order": 0,
        "children": [_leaf("C1-1", "猫用品", parent="C1", level=2)],
    }]
    rows = P.build_tree(raw)
    paths = P.path_by_code(rows)
    assert paths["C1"] == "宠物用品"
    assert paths["C1-1"] == "宠物用品/猫用品"


def test_build_tree_rejects_dangling_parent():
    raw = [_leaf("C1", "猫用品", parent="NOPE", level=1)]
    with pytest.raises(P.UnknownShape, match="悬空"):
        P.build_tree(raw)


def test_build_tree_rejects_level_mismatch():
    raw = [{
        "category_code": "C1", "category_name": "宠物用品",
        "parent_category_code": None, "level": 1, "is_leaf": False,
        "sort_order": 0,
        # ★ 声明 level=1，但按 children 实际深度是 2
        "children": [_leaf("C1-1", "猫用品", parent="C1", level=1)],
    }]
    with pytest.raises(P.UnknownShape, match="level"):
        P.build_tree(raw)


def test_build_tree_rejects_cycle():
    node = _leaf("C1", "猫用品", parent=None, level=1)
    node["children"] = [node]  # 自己是自己的子节点
    with pytest.raises(P.UnknownShape, match="环"):
        P.build_tree([node])


# ══════════════════════════════════════════════════════════════════════
#  index_products：静默丢失铁律——重复 sku_code 硬失败
# ══════════════════════════════════════════════════════════════════════

def test_index_products_rejects_duplicate_sku_code():
    items = [{"sku_code": "PM-1", "category": {"code": "C1", "name": "猫用品"}},
             {"sku_code": "PM-1", "category": {"code": "C2", "name": "狗用品"}}]
    with pytest.raises(ValueError, match="PM-1"):
        P.index_products(items)


# ══════════════════════════════════════════════════════════════════════
#  两段匹配：exact 优先，base 兜底，撞键从兜底表剔除且不 raise
# ══════════════════════════════════════════════════════════════════════

def _product(sku_code, cat_code="C1", cat_name="猫用品", image="https://x/real.jpg"):
    return {"sku_code": sku_code, "category": {"code": cat_code, "name": cat_name},
            "main_image_url": image}


def test_exact_match_wins_over_base_match():
    by_sku = P.index_products([_product("A4P-1"), _product("A4P-1-RED")])
    base_index, _ = P.build_base_index(by_sku)
    pm_sku, path, item = P.match_one("A4P-1-RED", by_sku, base_index)
    assert (pm_sku, path) == ("A4P-1-RED", "direct")
    assert item["sku_code"] == "A4P-1-RED"


def test_base_match_when_exact_misses():
    """★ `build_base_index` 的 base 是 **PM 自己 sku_code** 去尾缀的结果——
    PM 的 "A4P-TOY-1-STD" 去掉自己的变体后缀 "STD" 得到 base "A4P-TOY-1"；
    我们的货号 "A4P-TOY-1-RED" 去掉自己的变体后缀 "RED" 得到同一个 base，
    两边在这个 base 上碰头，不是拿我们的 sku 直接去掉一段就查 PM 原始
    sku_code（那样 "A4P-TOY-1" 这种没有自己变体后缀的 PM 编码永远查不中）。"""
    by_sku = P.index_products([_product("A4P-TOY-1-STD")])
    base_index, _ = P.build_base_index(by_sku)
    pm_sku, path, _item = P.match_one("A4P-TOY-1-RED", by_sku, base_index)
    assert (pm_sku, path) == ("A4P-TOY-1-STD", "base")


def test_no_match_returns_all_none():
    by_sku = P.index_products([_product("A4P-TOY-1")])
    base_index, _ = P.build_base_index(by_sku)
    assert P.match_one("ZZZ-NOPE", by_sku, base_index) == (None, None, None)


def test_ambiguous_base_is_excluded_from_index_not_raised():
    """★ 一个 base 撞两个不同品类 ⇒ 从兜底索引剔除并记入 ambiguous，
    不是整份刷新报错（计划 Task 1 Step 5 明写）。"""
    by_sku = P.index_products([
        _product("A4P-1-RED", cat_code="C1"),
        _product("A4P-1-BLUE", cat_code="C2"),  # 同 base "A4P-1"，品类不同
    ])
    base_index, ambiguous = P.build_base_index(by_sku)
    assert "A4P-1" not in base_index
    assert ambiguous == {"A4P-1"}
    # 只经这个 base 才能解析的 sku：查不到，落空，不是编一个答案出来
    assert P.match_one("A4P-1-GREEN", by_sku, base_index) == (None, None, None)


def test_ambiguous_base_same_category_is_harmless():
    """品类相同的撞键无害：不管选哪个 PM 行，品类都一样。"""
    by_sku = P.index_products([
        _product("A4P-1-RED", cat_code="C1"),
        _product("A4P-1-BLUE", cat_code="C1"),
    ])
    base_index, ambiguous = P.build_base_index(by_sku)
    assert "A4P-1" in base_index
    assert ambiguous == set()


# ══════════════════════════════════════════════════════════════════════
#  fetch_sku_category：编排入口的组合行为
# ══════════════════════════════════════════════════════════════════════

_TREE = [{
    "category_code": "C1", "category_name": "宠物用品",
    "parent_category_code": None, "level": 1, "is_leaf": False, "sort_order": 0,
    "children": [_leaf("C1-1", "猫用品", parent="C1", level=2)],
}]


def test_fetch_sku_category_composes_products_and_tree(monkeypatch):
    products = [
        _product("SKU-SERIES-A", cat_code="C1-1", image="https://x/real.jpg"),  # 真图
        _product("SKU-SERIES-B", cat_code="C1-1", image="https://x/default.png"),  # 占位
    ]
    fetch_products_calls = []
    fetch_tree_calls = []

    def fetch_products():
        fetch_products_calls.append(1)
        return products

    def fetch_tree():
        fetch_tree_calls.append(1)
        return _TREE

    fetched = P.fetch_sku_category(
        ["SKU-SERIES-A", "SKU-SERIES-B", "ZZZ-NEVER-SEEN-BY-PM"],
        fetch_products, fetch_tree)

    assert len(fetch_products_calls) == 1, "只该拉一次全量，不许每个 sku 各打一次"
    assert len(fetch_tree_calls) == 1

    by_sku_row = {r[0]: r for r in fetched.rows}
    assert set(by_sku_row) == {"SKU-SERIES-A", "SKU-SERIES-B"}

    row_a = by_sku_row["SKU-SERIES-A"]
    assert row_a == ("SKU-SERIES-A", "C1-1", "宠物用品/猫用品", "SKU-SERIES-A", "direct",
                     "https://x/real.jpg", False)
    row_b = by_sku_row["SKU-SERIES-B"]
    assert row_b[6] is True, "占位图那一行 image_is_placeholder 必须是 True"

    assert fetched.dropped == 1
    assert fetched.drop_reasons["no_pm_match"] == 1
    assert fetched.source_max_captured is None


def test_fetch_sku_category_row_order_matches_columns_constant():
    """★ 落库列序必须与 `COLUMNS` 完全对齐，否则 `_upsert` 会把值拼进错列。"""
    products = [_product("SKU-A", cat_code="C1-1")]
    fetched = P.fetch_sku_category(["SKU-A"], lambda: products, lambda: _TREE)
    assert len(fetched.rows[0]) == len(P.COLUMNS)


def test_fetch_sku_category_drops_products_with_no_category():
    item = {"sku_code": "SKU-A", "category": None, "main_image_url": "https://x/real.jpg"}
    fetched = P.fetch_sku_category(["SKU-A"], lambda: [item], lambda: _TREE)
    assert fetched.rows == []
    assert fetched.drop_reasons["no_category"] == 1


def test_fetch_sku_category_orphan_category_is_visible_not_dropped():
    """★「认不出的形态必须默认可见」：品类树里查不到这个 code 时，仍然写
    这一行（图还是要能看），但要在 drop_reasons 里留下痕迹，不能既不丢弃
    又完全没有任何记录——那样下次同一个缝隙会被误读成「一直都很干净」。"""
    item = _product("SKU-A", cat_code="C-GHOST")
    fetched = P.fetch_sku_category(["SKU-A"], lambda: [item], lambda: _TREE)
    assert len(fetched.rows) == 1
    assert fetched.rows[0][2] == "猫用品"  # 兜底用了 category name
    assert fetched.drop_reasons["orphan_category"] == 1
    assert fetched.dropped == 0, "这一行没有被丢——它还是写进了 sku_category"
