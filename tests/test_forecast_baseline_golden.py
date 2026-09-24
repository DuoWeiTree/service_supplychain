"""★ 00 D-3 ①：base_rate 移植的「一次绿」门禁 —— 兄弟仓 v1 在固定输入上的输出（OP-1）。
红了就是搬错了，且能定位到 case。"""
import csv
import datetime as dt
import math
from pathlib import Path

from forecast.baseline import baseline_rate, estimate_rate

GOLD = Path(__file__).parent / "fixtures" / "baseline_golden"
AS_OF = dt.date(2026, 9, 21)
EARLIER = AS_OF - dt.timedelta(days=7)


def _rows(name):
    with open(GOLD / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load():
    expected = {(r["case"], dt.date.fromisoformat(r["anchor"])):
                (None if r["base_rate"] == "" else float(r["base_rate"]))
                for r in _rows("expected.csv")}
    cases = {c: ({}, set()) for c, _ in expected}
    for r in _rows("history.csv"):
        cases[r["case"]][0][dt.date.fromisoformat(r["date"])] = int(r["units"])
    for r in _rows("censor.csv"):
        cases[r["case"]][1].add(dt.date.fromisoformat(r["date"]))
    return cases, expected


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def test_golden_is_complete():
    cases, expected = load()
    assert len(cases) == 131 and len(expected) == 262
    assert {a for _, a in expected} == {AS_OF, EARLIER}
    assert any(not close(expected[(c, AS_OF)], expected[(c, EARLIER)]) for c in cases), \
        "两个锚点的期望值全一样 —— Task 12 的「变红」就不可能发生，这份金标测不到 embargo"


def test_port_matches_sibling_v1_at_every_anchor():
    cases, expected = load()
    bad = []
    for (case, anchor), want in sorted(expected.items()):
        r = baseline_rate(*cases[case], anchor)
        got = None if r is None else r.rate
        if not close(got, want):
            bad.append((case, anchor.isoformat(), got, want))
    assert bad == [], f"{len(bad)} 个 case 与兄弟仓不符：{bad[:10]}"


def test_production_entry_matches_sibling_v1_at_as_of():
    """生产入口（claim 调的就是它）在 v1 口径下 = 兄弟仓在 as_of 上的输出。"""
    cases, expected = load()
    bad = []
    for c in cases:
        e = estimate_rate(*cases[c], AS_OF)
        got = None if e.result is None else e.result.rate
        if not close(got, expected[(c, AS_OF)]):
            bad.append(c)
    assert bad == []
