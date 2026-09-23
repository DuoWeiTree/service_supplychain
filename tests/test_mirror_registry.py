"""登记表是「有哪些镜像」的唯一真相。门禁 (a) 把它钉在 docs/03 §1 上。"""
import re
from pathlib import Path

from dim.registry import MIRRORS, by_name, gate_503_names, refresh_order

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "03-数据库表清单.md"

#: 03 §1 的行形如 `| 30 | \`sku_catalog\` | ① 维度 | DIM 镜像 | 刷新 | 外键完整性 |`
_ROW = re.compile(r"^\|\s*\d+\s*\|(?P<table>[^|]+)\|(?P<domain>[^|]+)\|(?P<owner>[^|]+)\|")


def _doc_rows() -> list[tuple[list[str], str]]:
    out = []
    for line in DOC.read_text("utf-8").splitlines():
        m = _ROW.match(line.strip())
        if m:
            out.append((re.findall(r"`([^`]+)`", m["table"]), m["owner"].strip()))
    return out


def test_doc_table_parser_actually_sees_the_table():
    """★ 扫 0 行的门禁永远是绿的。03:51 写的是 43 张（含 S-33 新增的 dim_refresh_run），
    少于它就是正则坏了。"""
    rows = _doc_rows()
    assert len(rows) >= 43, f"只解析到 {len(rows)} 行 —— 正则没匹配上，下面那条断言是空转的"


def test_registry_matches_the_doc_exactly():
    """★ 集合相等，不是包含：03 多一行 MIRROR → 红；登记表多一个名字 → 也红。"""
    doc_names, skipped = set(), 0
    for tables, owner in _doc_rows():
        if "MIRROR" in owner or "DIM" in owner:
            doc_names |= set(tables)
        else:
            skipped += 1          # ★ 同时统计被丢掉的那一侧
    assert skipped > 0, "一行都没被跳过 —— 归属列没解析对"
    registry_names = {m.name for m in MIRRORS} | {c for m in MIRRORS for c in m.companions}
    assert registry_names == doc_names, (
        f"只在 03 里：{sorted(doc_names - registry_names)}；"
        f"只在登记表里：{sorted(registry_names - doc_names)}")


def test_stage_a_entries_are_not_pending():
    a = {m.name for m in MIRRORS if m.stage == "A" and not m.pending}
    assert a == {"seller", "sku_catalog", "msku_bridge", "warehouse"}


def test_gate_503_is_exactly_the_four_dim_mirrors():
    assert gate_503_names() == {"seller", "sku_catalog", "msku_bridge", "warehouse"}


def test_refresh_order_puts_parents_before_children():
    """msku_bridge 的外键指着 seller 与 sku_catalog —— 先刷子表必然撞外键。"""
    order = [m.name for m in refresh_order()]
    assert order.index("seller") < order.index("msku_bridge")
    assert order.index("sku_catalog") < order.index("msku_bridge")
    assert set(order) == {m.name for m in MIRRORS if not m.pending}


def test_pending_entries_have_no_fetch():
    """反过来（非 pending 条目都必须有真的 fetch）属于 Task 2 门禁 (b)
    ——那条断言在阶段 A 四张接线（Task 4）之前应当保持诚实地红，
    这里不许用占位 callable 把它悄悄唬绿。"""
    for m in MIRRORS:
        if m.pending:
            assert m.fetch is None, f"{m.name}: pending 条目不该有 fetch"


def test_by_name_names_the_miss():
    try:
        by_name("no_such_mirror")
    except KeyError as e:
        assert "no_such_mirror" in str(e)
    else:
        raise AssertionError("查不到的名字必须硬失败，不许返回 None")
