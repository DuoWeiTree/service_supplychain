"""★ 00 D-3 ①：兄弟仓 228 格对账门禁（台账）。必须一次绿 —— 红了就是搬错了，且能定位到格。"""
from fractions import Fraction as F

import pytest

from tests.recon_adapter import CHECKPOINTS, at, excel_formula, rows, signed_positions


@pytest.fixture(scope="module")
def positions():
    return signed_positions()


@pytest.fixture(scope="module")
def target():
    return {(r["sku"], r["node"]): excel_formula(r) for r in rows("recon_components.csv")}


@pytest.fixture(scope="module")
def cached():
    """运营表 `recon_expected.csv` 的缓存分量 —— 可能与 `target` 的自身公式不一致（陈旧格）。"""
    return {(r["sku"], r["node"], r["checkpoint"]): F(r["expected"])
            for r in rows("recon_expected.csv")}


def test_every_one_of_228_cells_matches_exactly(positions, target):
    bad = [(k, cp, float(at(positions, k, cp)), float(v))
           for k, cps in target.items() for cp, v in cps.items() if at(positions, k, cp) != v]
    assert sum(len(c) for c in target.values()) == 228 == 38 * len(CHECKPOINTS)
    assert bad == [], f"{len(bad)} 格不符：{bad[:10]}"


def test_negatives_survive(positions, target):
    """Excel 用负余额表达缺口 —— 带符号头寸必须保留负数（spec §4.5 · 14 B8）。"""
    assert any(v < 0 for cps in target.values() for v in cps.values())
    assert any(at(positions, k, cp) < 0 for k in target for cp in CHECKPOINTS)


def test_values_land_on_half_units(positions, target):
    """半月均分让检查点落在 0.5 的整数倍上。"""
    assert all((2 * at(positions, k, cp)).denominator == 1 for k in target for cp in CHECKPOINTS)


def test_workbook_has_exactly_two_stale_cells(target, cached):
    """运营表里 2 格缓存与自身公式矛盾（兄弟仓 test_workbook_has_two_stale_cells）。"""
    stale = [(k[0], cp, cached[(*k, cp)] - v) for k, cps in target.items()
             for cp, v in cps.items() if cached[(*k, cp)] != v]
    assert [(s, cp) for s, cp, _ in stale] == [("DCC1045014LG", "2026-08-31"),
                                               ("DCC1045014LG", "2026-09-15")]
    assert all(abs(d) == 1 for _, _, d in stale)


def test_model_matches_cache_wherever_cache_is_fresh(positions, target, cached):
    fresh = [(k, cp) for k, cps in target.items() for cp, v in cps.items()
             if cached[(*k, cp)] == v]
    assert len(fresh) == 226
    assert all(at(positions, k, cp) == cached[(*k, cp)] for k, cp in fresh)


def test_halfmonth_split_is_load_bearing(target):
    """换成按天均摊就必须对不上 —— 证明半月均分是被建模的约定，不是碰巧。"""
    flat = signed_positions(halfmonth=False)
    assert any(at(flat, k, cp) != v for k, cps in target.items() for cp, v in cps.items())


def test_anchor_correction_is_load_bearing(target):
    """关掉 +AN 就必须对不上 —— 证明适配器真的在复现 Excel 的锚点修正。"""
    raw = signed_positions(anchor_correction=False)
    assert any(at(raw, k, cp) != v for k, cps in target.items() for cp, v in cps.items())


def test_delivered_events_are_excluded(positions, target):
    """`<>送达` 是支点：已送达的货在在库快照里，再算一次就双重计数。

    ★ 控制者裁定：光数原始 CSV 里 status=="送达" 的行数不算数 —— 那不会经过
    recon_adapter.py 的任何一行代码。这里要实打实地把过滤开关关掉，证明
    adapter 的到货表真的因为这个过滤而变，且开着过滤时才是对得上 228 格的那份。
    """
    ev = rows("supply_events.csv")
    assert sum(1 for e in ev if e["status"] == "送达") == 4407

    with_filter = positions
    without_filter = signed_positions(exclude_delivered=False)
    assert any(at(without_filter, k, cp) != at(with_filter, k, cp)
               for k, cps in target.items() for cp in cps)
    # 关掉过滤后，已送达的货被重复计入到货，228 格里至少有一格会偏离 Excel 基准；
    # 开着过滤（生产用的那一分支）仍然逐格相等 —— 上面 test_every_one_of_228_cells_matches_exactly
    # 已经证明了这一点，这里再显式确认「关掉就错」。
    assert any(at(without_filter, k, cp) != v for k, cps in target.items() for cp, v in cps.items())
