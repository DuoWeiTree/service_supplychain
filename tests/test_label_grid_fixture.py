"""前端标签测试网格（web/src/pages/__fixtures__/labelGrid.forecast.json）由真推演产出。

★ 为什么要这条：手写的预测块曾把「10 月下单、11 月到货」写进 make 30 + ship 60 的网格 ——
  物理上不可能，而前端测试只核对透传，谁也不会失败。这里用同一组入参重跑 forecast_payload，
  与前端冻结的那份逐字相等；入参或推演一变，这条就红。

重新生成：`.venv/bin/python -m tests.test_label_grid_fixture`
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

from api.ui.grid_forecast import forecast_payload
from api.ui.lead_time import LeadTime

FROZEN = Path(__file__).resolve().parents[1] / "web" / "src" / "pages" / "__fixtures__" / "labelGrid.forecast.json"

P10, P11 = dt.date(2026, 10, 1), dt.date(2026, 11, 1)
A, B, W = ("MSKU-A", "11072"), ("MSKU-B", "11072"), ("MSKU-W", "90001")


def _row(k: tuple[str, str], period: str, eff: int, sysu: int | None) -> dict:
    return {"seller_sku": k[0], "sid": k[1], "sku": "SKU-1", "period": period,
            "effective_units": eff, "system_units": sysu}


def build() -> dict:
    """入参与 labelGrid.ts 的 demand / purchase / params 一一对应（改一边必须改另一边）。"""
    return forecast_payload(
        as_of=dt.date(2026, 9, 21), periods=[P10, P11],
        demand_rows=[_row(A, "2026-10", 100, 135), _row(A, "2026-11", 90, 130),
                     _row(B, "2026-10", 5, 10), _row(B, "2026-11", 5, 10),
                     _row(W, "2026-10", 60, None), _row(W, "2026-11", 65, None)],
        purchase_rows=[("SKU-1", P10, 200), ("SKU-1", P11, 80)],
        fba={A: True, B: True, W: False},
        onhand={A: 300, B: 13},
        base_rate={A: 4.325139122267326, B: 0.32},
        in_transit={"SKU-1": [SimpleNamespace(period="2026-10", units=50, ref="PO-2026-0918")]},
        lead=LeadTime("oversea_sea", 30, 60, 15, {}), embargo_days=7, plan_id=1)


def test_frozen_label_grid_is_what_the_forecast_produces():
    assert json.loads(FROZEN.read_text(encoding="utf-8")) == build(), (
        f"{FROZEN.name} 与真推演不一致 —— 重新生成：.venv/bin/python -m tests.test_label_grid_fixture")


if __name__ == "__main__":
    FROZEN.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {FROZEN}")
