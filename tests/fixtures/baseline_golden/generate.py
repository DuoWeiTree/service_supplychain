"""一次性：用兄弟仓 v1 `baseline_rate` 在**固定输入**上算期望值（00 D-3 ①「任意一组固定输入」）。

运行（worktree 根下）：
  /home/fido/work/2026/jxd_model_group/model_inventory_forecast/.venv/bin/python \
      tests/fixtures/baseline_golden/generate.py
★ 生成后不再改。要换基准就新建目录并写明理由（同兄弟仓 fixtures/README 的规矩）。
★ 每个 case 在两个锚点上各算一次：as_of 与 as_of − 7 —— 后者是 Task 12 embargo 之后的新基准。
"""
import csv
import datetime as dt
import random
import subprocess
import sys
from pathlib import Path

SIB = Path("/home/fido/work/2026/jxd_model_group/model_inventory_forecast")
sys.path.insert(0, str(SIB))
import pandas as pd
from models.inventory_forecast.demand import baseline_rate

OUT = Path(__file__).resolve().parent
AS_OF = dt.date(2026, 9, 21)
ANCHORS = (AS_OF, AS_OF - dt.timedelta(days=7))


def back(i):
    return AS_OF - dt.timedelta(days=i)


def named_cases():
    yield "dense_const_4_120d", {back(i): 4 for i in range(120)}, set()
    yield "const_4_56d", {back(i): 4 for i in range(56)}, set()
    yield "censor_last_14", {back(i): (0 if i < 14 else 5) for i in range(28)}, {back(i) for i in range(14)}
    yield "sparse_two_days", {back(0): 10, back(1): 10}, set()
    yield "spike", {**{back(i): 5 for i in range(28)}, back(3): 100}, set()
    yield "new_20d", {back(i): 6 for i in range(20)}, set()
    yield "zeros_only_in_window", {back(i): 0 for i in range(10)}, set()
    yield "only_older_than_window", {back(i): 3 for i in range(60, 89)}, set()
    yield "all_censored", {back(i): 2 for i in range(30)}, {back(i) for i in range(56)}
    yield "old_high_recent_low", {**{back(i): 10 for i in range(56, 90)}, **{back(i): 1 for i in range(56)}}, set()
    yield "empty", {}, set()


def random_cases(n=120, seed=20260924):
    rng = random.Random(seed)
    for k in range(n):
        days = rng.choice([10, 30, 56, 90, 150])
        lam = rng.choice([0.05, 0.3, 1, 4, 20])
        trials = int(lam * 3) + 1
        daily = {}
        for i in range(days):
            d = back(i + rng.choice([0, 0, 0, 40]))
            u = sum(1 for _ in range(trials) if rng.random() < lam / trials)
            if rng.random() < 0.05:
                u *= 10
            if u > 0 or rng.random() < 0.1:
                daily[d] = daily.get(d, 0) + u
        cen = {back(i) for i in range(60) if rng.random() < 0.1}
        yield f"rand_{k:03d}", daily, cen


def main():
    cases = list(named_cases()) + list(random_cases())
    hist, cens, exp = [], [], []
    for name, daily, cen in cases:
        hist += [(name, d.isoformat(), u) for d, u in sorted(daily.items())]
        cens += [(name, d.isoformat()) for d in sorted(cen)]
        h = pd.DataFrame([("S", "N", d, u) for d, u in daily.items()],
                         columns=["sku", "node", "date", "units"])
        c = pd.DataFrame([("S", "N", d) for d in cen], columns=["sku", "node", "date"])
        for a in ANCHORS:
            r = baseline_rate(h, a, censor=c).get(("S", "N"))
            exp.append((name, a.isoformat(), "" if r is None else repr(float(r))))
    for fname, header, rows in (("history.csv", ["case", "date", "units"], hist),
                                ("censor.csv", ["case", "date"], cens),
                                ("expected.csv", ["case", "anchor", "base_rate"], exp)):
        with open(OUT / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
    rev = subprocess.run(["git", "-C", str(SIB), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    (OUT / "README.md").write_text(
        "# baseline_rate 金标（冻结）\n\n"
        f"兄弟仓 `{SIB.name}` commit `{rev}` 的 v1 `baseline_rate` 在固定输入上的输出。\n"
        f"{len(cases)} 个 case × 锚点 {', '.join(a.isoformat() for a in ANCHORS)}。\n"
        "生成：见 generate.py 顶部。★ 不许手工编辑；要换基准就新建目录。\n", encoding="utf-8")


if __name__ == "__main__":
    main()
