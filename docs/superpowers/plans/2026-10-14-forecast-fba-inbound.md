# 预测计入 FBA 在途货件 + 调仓中预留 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 计划页库存推演把已发出的 FBA 货件（按亚马逊送达结束日）与调仓中预留算进去，逾期 / 无送达时段的点名不计。

**Architecture:** CH 取数层（`dim/ch_source.py`）加两样批量缓存：期初拆成「可售 + 调仓中」，货件表按 as_of 快照解析成逐 msku 的 `FbaInbound`；纯推演层（`forecast/plan_forecast.py`）把它们放进**两遍**台账（基线 + 终算）；装配层（`api/ui/plans.py` → `grid_forecast.py`）只搬运；前端只展示。

**Tech Stack:** Python 3.12 · ClickHouse（只读）· pytest（`-m pure` / 集成）· React + vitest。

**Spec:** `docs/superpowers/specs/2026-10-14-forecast-fba-inbound-design.md`

## Global Constraints

- CH 绝不写；FBA 货件只读 `jxd_raw.lingxing_fba_shipment_list` / `_items`，快照日 = 预测 `as_of`，**不回退旧快照**（FI-Q1）。
- 计入状态：`SHIPPED · IN_TRANSIT · DELIVERED · CHECKED_IN · RECEIVING`；剩余 = `quantity_shipped − quantity_received`，< 0 → 0 并计数。
- 到货日 = `sta_delivery_end_date`；结束日 ≤ as_of → `fba_overdue`；无日期 → `fba_no_eta`；两者都不进台账、进 `unallocated`（FI-Q2）。
- 期初 = `afn_fulfillable_quantity` + `reserved_fc_transfers`（NULL → 0 并计数 `fc_transfers_null`）。
- 空 ≠ 0：货件快照缺当天 → 该 msku FBA 在途未知（`fba_inbound_unavailable`），不当 0。
- 日志三问：`op=ch_fba_inbound` 带 target / as_of / elapsed_ms / cause；对账不等 WARNING `op=fba_inbound_reconcile`。
- 前端不出现「lingxing」（L8）；界面只写当场的事，解释进 title / 依据面板。
- 每个新测试先看它红一次。

## Review Focus

1. 同一货件同一 msku 在一个快照日出现两行（采集重复）→ 合并计数，不得把在途翻倍。
2. msku 在仓未知（onhand None）却有 FBA 货件 → 货件进 `unallocated`（`project_msku` 的 unplaced），不丢。
3. 货件 `sid` 与计划 msku 的 sid 不同（同 msku 挂多店）→ 只归自己的店。
4. 送达结束日恰好 = as_of → 视为逾期（不进台账），= as_of+1 → 进台账。
5. 计划采购分配：FBA 到货必须已在基线里，否则采购被多分（`test_fba_inbound_shrinks_purchase_gap`）。

---

### Task 1: CH 纯解析 —— 期初拆两列 + 货件解析

**Files:**
- Modify: `dim/source.py`（加 `FbaInbound`）
- Modify: `dim/ch_source.py`（`SQL_FBA_ONHAND` 加列、`parse_onhand` 返回调仓、新增 `SQL_FBA_INBOUND` · `parse_fba_inbound`）
- Test: `tests/test_dim_ch_source_fba_inbound.py`（pure）

**Interfaces:**
- Produces: `FbaInbound(shipment_id: str, status: str, units: int, deliver_from: date|None, deliver_to: date|None)`；`parse_onhand(rows) -> (onhand: {(sid, msku): int}, fc: {(sid, msku): int}, dropped)`；`parse_fba_inbound(rows) -> ({(sid, msku): [FbaInbound]}, dropped)`；常量 `FBA_INBOUND_STATUSES`。

- [ ] **Step 1: 失败测试**

```python
import datetime as dt
import pytest
from dim.ch_source import parse_fba_inbound, parse_onhand

pytestmark = pytest.mark.pure
D = dt.date

def row(sid="11072", msku="M", sh="FBA1", st="SHIPPED", shipped=5, rec=0, f=D(2026, 11, 15), t=D(2026, 11, 22)):
    return (sid, msku, sh, st, shipped, rec, f, t)

def test_remaining_is_shipped_minus_received_per_shipment():
    got, dropped = parse_fba_inbound([row(), row(sh="FBA2", st="RECEIVING", shipped=2, rec=1)])
    assert [(x.shipment_id, x.units, x.deliver_to) for x in got[("11072", "M")]] == [
        ("FBA1", 5, D(2026, 11, 22)), ("FBA2", 1, D(2026, 11, 22))]
    assert dropped == {}

def test_over_received_is_zero_and_counted():
    got, dropped = parse_fba_inbound([row(st="RECEIVING", shipped=3, rec=6)])
    assert got == {}
    assert dropped["over_received"] == {"rows": 1, "units": 3}

def test_not_shipped_statuses_are_counted_not_placed():
    got, dropped = parse_fba_inbound([row(st="WORKING", shipped=13), row(sh="FBA9", st="READY_TO_SHIP", shipped=20),
                                      row(sh="FBAX", st="CANCELLED", shipped=4)])
    assert got == {}
    assert dropped["not_shipped"] == {"rows": 2, "units": 33}

def test_duplicate_shipment_msku_rows_merge_once_and_are_counted():
    got, dropped = parse_fba_inbound([row(), row()])
    assert [x.units for x in got[("11072", "M")]] == [5]
    assert dropped["rows_collapsed"] == 1

def test_missing_dates_kept_as_none():
    got, _ = parse_fba_inbound([row(f=None, t=None)])
    assert got[("11072", "M")][0].deliver_to is None

def test_onhand_splits_fc_transfers_and_counts_null():
    onhand, fc, dropped = parse_onhand([("11072", "M", 52, 2, 1), ("11072", "N", 7, None, 1), ("0", "Z", 9, 1, 1)])
    assert onhand == {("11072", "M"): 52, ("11072", "N"): 7}
    assert fc == {("11072", "M"): 2, ("11072", "N"): 0}
    assert dropped["fc_transfers_null"] == 1
    assert dropped["shared_pool_excluded"] == {"rows": 1, "units": 9}
```

- [ ] **Step 2:** `uv run pytest tests/test_dim_ch_source_fba_inbound.py -q` → FAIL（ImportError `parse_fba_inbound`）。

- [ ] **Step 3: 实现**

`dim/source.py` 加：

```python
@dataclass(frozen=True)
class FbaInbound:
    """已发往 FBA、还没签收完的一个货件里这个 msku 的剩余件数（spec 2026-10-14 §3）。"""
    shipment_id: str
    status: str
    units: int
    deliver_from: dt.date | None
    deliver_to: dt.date | None      # ★ 到货日取它（裁定 a：宁晚不早）
```

`dim/ch_source.py`：`SQL_FBA_ONHAND` 增列 `toInt64(sum(ifNull(reserved_fc_transfers, 0))) AS fc, countIf(reserved_fc_transfers IS NULL) AS fc_null`，列序改为 `sid, seller_sku, units, fc, raw_rows` + `fc_null`（测试行为 5 元组时 `fc_null` 取 `fc is None`）。`parse_onhand` 改签名为三元组返回：

```python
def parse_onhand(rows):
    out, fc_out = {}, {}
    dropped: dict = {"shared_pool_excluded": {"rows": 0, "units": 0}}
    for sid, seller_sku, units, fc, raw_rows in rows:
        sid, units, raw_rows = str(sid), int(units), int(raw_rows)
        if sid == SHARED_POOL_SID:
            b = dropped["shared_pool_excluded"]; b["rows"] += raw_rows; b["units"] += units
            continue
        if fc is None:
            dropped["fc_transfers_null"] = dropped.get("fc_transfers_null", 0) + 1
        if raw_rows > 1:
            dropped["rows_collapsed"] = dropped.get("rows_collapsed", 0) + raw_rows - 1
        key = (sid, seller_sku)
        if key in out:
            dropped["rows_collapsed"] = dropped.get("rows_collapsed", 0) + 1
        out[key] = out.get(key, 0) + units
        fc_out[key] = fc_out.get(key, 0) + int(fc or 0)
    return out, fc_out, dropped
```

SQL 侧用 `sum(reserved_fc_transfers)`（ClickHouse 的 sum 跳过 NULL）+ `countIf(reserved_fc_transfers IS NULL) AS fc_null`，Python 侧把 `fc_null > 0` 当作「有 NULL」：在 `_onhand()` 里把 SQL 行 `(sid, msku, units, fc, raw_rows, fc_null)` 映射成 `(sid, msku, units, None if fc_null == raw_rows else fc, raw_rows)` 再交给 `parse_onhand`。

新增：

```python
FBA_SHIPMENT_TABLE = "jxd_raw.lingxing_fba_shipment_list"
FBA_SHIPMENT_ITEMS_TABLE = "jxd_raw.lingxing_fba_shipment_list_items"
#: 裁定 b（10-14）：只算已发出的；WORKING / READY_TO_SHIP 只计数
FBA_INBOUND_STATUSES = ("SHIPPED", "IN_TRANSIT", "DELIVERED", "CHECKED_IN", "RECEIVING")
_NOT_SHIPPED = ("WORKING", "READY_TO_SHIP")

SQL_FBA_INBOUND = f"""
SELECT toString(s.sid), i.msku, s.shipment_id, s.shipment_status,
       toInt64(ifNull(i.quantity_shipped, 0)), toInt64(ifNull(i.quantity_received, 0)),
       s.sta_delivery_start_date, s.sta_delivery_end_date
  FROM {FBA_SHIPMENT_ITEMS_TABLE} i
  JOIN {FBA_SHIPMENT_TABLE} s ON s.shipment_id = i.shipment_id AND s._captured_date = i._captured_date
 WHERE i._captured_date = {{as_of:Date}}
"""

def _d(v):
    if v in (None, ""):
        return None
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])

def parse_fba_inbound(rows):
    out: dict[tuple[str, str], dict[str, FbaInbound]] = {}
    dropped: dict = {}
    def bump(k, units):
        b = dropped.setdefault(k, {"rows": 0, "units": 0}); b["rows"] += 1; b["units"] += units
    for sid, msku, sh, st, shipped, rec, f, t in rows:
        sid, shipped, rec = str(sid), int(shipped), int(rec)
        if st in _NOT_SHIPPED:
            bump("not_shipped", shipped); continue
        if st not in FBA_INBOUND_STATUSES:
            continue                                   # 已关 / 取消 / 删除：不在路上
        if rec > shipped:
            bump("over_received", shipped); continue
        if shipped - rec == 0:
            continue
        slot = out.setdefault((sid, msku), {})
        if sh in slot:
            dropped["rows_collapsed"] = dropped.get("rows_collapsed", 0) + 1; continue
        slot[sh] = FbaInbound(sh, st, shipped - rec, _d(f), _d(t))
    return {k: sorted(v.values(), key=lambda x: x.shipment_id) for k, v in out.items()}, dropped
```

调整 `_onhand()` 调用方解三元组（存 `self._fc_cache`）。

- [ ] **Step 4:** 测试 PASS；`uv run pytest -m pure -q` 全绿（`parse_onhand` 旧测试改成三元组解包）。

- [ ] **Step 5: Commit** `feat(forecast): CH 解析 FBA 在途货件与调仓中预留`

---

### Task 2: 取数源方法（ChSource + 协议 + 测试替身）

**Files:**
- Modify: `dim/source.py`（协议加 `fc_transfers` · `fba_inbound`）
- Modify: `dim/ch_source.py`（`_fba_inbound()` 批量缓存、`fba_inbound()`、`fc_transfers()`、对账日志）
- Modify: `tests/fixture_source.py`（`fba_onhand.csv` 可选列 `fc_transfers`；可选文件 `fba_inbound.csv`）
- Test: `tests/test_dim_ch_source_fba_inbound.py`（追加，用 `tests/test_dim_ch_source_forecast.py` 现有的假 `_q` 手法）

**Interfaces:**
- Consumes: Task 1 `parse_fba_inbound` / `parse_onhand`。
- Produces: `Source.fc_transfers(seller_sku, sid) -> int`；`Source.fba_inbound(seller_sku, sid) -> list[FbaInbound] | None`（None = 当天无货件快照，未知）。

- [ ] **Step 1: 失败测试**（照 `test_dim_ch_source_forecast.py` 里构造 `ChSource` 并替换 `_q` 的写法）

```python
def test_fba_inbound_none_when_no_snapshot_on_as_of(src_with_rows):
    src = src_with_rows(fba_inbound_rows=[])        # 当天 0 行
    assert src.fba_inbound("M", "11072") is None

def test_fba_inbound_cached_one_query(src_with_rows):
    src = src_with_rows(fba_inbound_rows=[("11072", "M", "FBA1", "SHIPPED", 5, 0, None, D(2026, 11, 22))])
    assert [x.units for x in src.fba_inbound("M", "11072")] == [5]
    assert src.fba_inbound("N", "11072") == []
    assert src.query_count("SQL_FBA_INBOUND") == 1

def test_reconcile_warns_when_shipped_differs(src_with_rows, caplog):
    src = src_with_rows(fba_inbound_rows=[("11072", "M", "FBA1", "SHIPPED", 5, 0, None, D(2026, 11, 22))],
                        inbound_shipped={("11072", "M"): 31})
    src.fba_inbound("M", "11072")
    assert "op=fba_inbound_reconcile" in caplog.text and "diff=26" in caplog.text
```

（`src_with_rows` 夹具写在本测试文件里：子类化 `ChSource`，`_q` 按 SQL 常量分派返回给定行，并计数。）

- [ ] **Step 2:** 运行 → FAIL（`AttributeError fba_inbound`）。

- [ ] **Step 3: 实现** `ChSource`：

```python
def _fba_inbound(self):
    as_of = self.as_of()
    if self._inbound_cache is not None and self._inbound_cache[0] == as_of:
        return self._inbound_cache[1]
    t0 = time.perf_counter()
    try:
        rows = self._q(SQL_FBA_INBOUND, {"as_of": as_of.isoformat()})
    except Exception as e:
        cause = self._classify(e)
        log.warning("op=ch_fba_inbound outcome=fail target=%s as_of=%s elapsed_ms=%d cause=%s",
                    FBA_SHIPMENT_TABLE, as_of, (time.perf_counter() - t0) * 1000, cause)
        raise ChUnavailable(target=FBA_SHIPMENT_TABLE, cause=cause) from e
    if not rows:
        # ★ FI-Q1：不回退旧快照 —— 货件状态一天就能变；未知 ≠ 0
        log.warning("op=ch_fba_inbound outcome=no_snapshot as_of=%s target=%s", as_of, FBA_SHIPMENT_TABLE)
        self._inbound_cache = (as_of, None)
        self._stats["fba_inbound"] = {"unavailable": True}
        return None
    by_key, dropped = parse_fba_inbound(rows)
    log.info("op=ch_fba_inbound outcome=ok as_of=%s keys=%d shipments=%d units=%d dropped=%s elapsed_ms=%d",
             as_of, len(by_key), len({x.shipment_id for v in by_key.values() for x in v}),
             sum(x.units for v in by_key.values() for x in v), dropped, (time.perf_counter() - t0) * 1000)
    self._reconcile(as_of, by_key)
    self._inbound_cache = (as_of, by_key)
    self._stats["fba_inbound"] = dropped
    return by_key

def fba_inbound(self, seller_sku, sid):
    by_key = self._fba_inbound()
    return None if by_key is None else list(by_key.get((str(sid), seller_sku), ()))

def fc_transfers(self, seller_sku, sid):
    self._onhand()
    return self._fc_cache[1].get((str(sid), seller_sku), 0)
```

`_reconcile`：同一 as_of 读 `fba_detail` 的 `afn_inbound_shipped_quantity`（在 `SQL_FBA_ONHAND` 里一并取出、随 `_onhand()` 缓存为 `self._shipped_cache`），对每个 key 比 Σ(SHIPPED+IN_TRANSIT 剩余)，不等打 `log.warning("op=fba_inbound_reconcile sid=%s msku=%s ours=%d lx=%d diff=%d")`；不抛。

协议 `dim/source.py` 加两个方法签名与 docstring（「None = 当天无货件快照：未知，不当 0」）。

`FixtureSource`：`fc_transfers` 读 `fba_onhand.csv` 的 `fc_transfers` 列（缺列 / 空 → 0）；`fba_inbound` 读 `fba_inbound.csv`（文件不存在 → `[]`；列 `seller_sku,sid,shipment_id,status,units,deliver_from,deliver_to`）。

- [ ] **Step 4:** PASS；`uv run pytest -q tests/test_dim_ch_source*.py tests/test_api_grid*.py` 全绿。
- [ ] **Step 5: Commit** `feat(forecast): 取数源给出 FBA 在途货件与调仓中`

---

### Task 3: 纯推演 —— FBA 到货进两遍台账

**Files:**
- Modify: `forecast/plan_forecast.py`
- Test: `tests/test_forecast_plan.py`（追加）

**Interfaces:**
- Consumes: `FbaInbound`（Task 1）。
- Produces: `MskuInput.fba_inbound: tuple[FbaInbound, ...] | None = ()`（None = 未知）；`MskuInput.onhand_parts: dict | None = None`（`{"fulfillable": int, "fc_transfers": int}`）；`SkuMonth.fba_inbound: int | None`；`Arrival.note["kind"] == "fba_shipment"`。

- [ ] **Step 1: 失败测试**

```python
from dim.source import FbaInbound

def fi(sh, units, to, frm=None, st="SHIPPED"):
    return FbaInbound(sh, st, units, frm, to)

def one(fba, onhand=52, eff=(27, 26, 27), purchase=None):
    mk = MskuInput("M", "11072", "S", True, onhand, dict(zip(P, eff)), dict(zip(P, eff)), 0.9,
                   fba_inbound=tuple(fba))
    return PlanForecastInput(as_of=D(2026, 10, 14), periods=P, make_days=30, ship_days=60, mskus=[mk],
                             purchase=purchase or {("S", p): 0 for p in P}, ordered_open={})

def test_fba_shipment_arrives_on_delivery_end_date():
    f = forecast_plan(one([fi("FBA1", 7, D(2026, 11, 8)), fi("FBA2", 24, D(2026, 11, 22))]))
    nov = f.msku[("M", "11072")][1]
    assert nov.inbound == 31
    assert [(a["shipment_id"], a["kind"], a["date"]) for a in nov.arrivals] == [
        ("FBA1", "fba_shipment", "2026-11-08"), ("FBA2", "fba_shipment", "2026-11-22")]

def test_overdue_and_no_eta_are_named_not_placed():
    f = forecast_plan(one([fi("OLD", 1, D(2026, 10, 14)), fi("NOETA", 5, None)]))
    assert all(r.inbound in (0, None) for r in f.msku[("M", "11072")])
    reasons = sorted((u["shipment_id"], u["reason"], u["units"]) for u in f.unallocated)
    assert reasons == [("NOETA", "fba_no_eta", 5), ("OLD", "fba_overdue", 1)]

def test_beyond_window_goes_to_beyond_horizon():
    f = forecast_plan(one([fi("FAR", 9, D(2027, 3, 1))]))
    assert [(b["shipment_id"], b["reason"]) for b in f.beyond_horizon] == [("FAR", "arrives_after_horizon")]

def test_unknown_fba_inbound_makes_msku_unknown_not_zero():
    f = forecast_plan(one(None))
    assert f.msku[("M", "11072")][0].state == "unknown"
    assert f.msku[("M", "11072")][0].reason == "fba_inbound_unavailable"

def test_fba_inbound_shrinks_purchase_gap():
    """★ FBA 到货必须已在基线：否则按缺口分配会把采购多分给这个 msku。"""
    other = MskuInput("O", "11073", "S", True, 0, dict(zip(P, (27, 26, 27))), dict(zip(P, (27, 26, 27))), 0.9)
    base = one([fi("FBA1", 60, D(2026, 10, 20))], purchase={("S", OCT): 0, ("S", NOV): 0, ("S", DEC): 0})
    inp = PlanForecastInput(**{**base.__dict__, "mskus": [*base.mskus, other], "make_days": 1, "ship_days": 1,
                               "purchase": {("S", OCT): 40, ("S", NOV): 0, ("S", DEC): 0}})
    f = forecast_plan(inp)
    got = {k: sum(r.inbound or 0 for r in rows) for k, rows in f.msku.items()}
    assert got[("O", "11073")] == 40          # 缺口全在没有货件的那家店
    assert got[("M", "11072")] == 60          # 只有它自己的货件

def test_sku_row_reports_fba_inbound_total():
    f = forecast_plan(one([fi("FBA1", 7, D(2026, 11, 8))]))
    assert [r.fba_inbound for r in f.sku] == [7, 7, 7]
```

（`P` 在本文件里是 OCT..DEC；as_of 10-14 落在 10 月里 —— 10 月 partial。`fba_inbound` 在货号行 = Σ 已进台账的 FBA 剩余，按全表恒定，不按月切。）

- [ ] **Step 2:** 运行 → FAIL（`TypeError: unexpected keyword fba_inbound`）。

- [ ] **Step 3: 实现**

```python
@dataclass(frozen=True)
class MskuInput:
    ...
    system_applicable: bool = True
    #: ★ spec 2026-10-14：已发出的 FBA 货件；None = 当天无货件快照（未知 ≠ 0）
    fba_inbound: tuple | None = ()
    #: 期初两部分，只为依据回显；onhand 已是两者之和
    onhand_parts: dict | None = None


def _place_fba(m: MskuInput, start: dt.date, end: dt.date):
    """→ (arrivals, beyond, unplaced)。逾期 / 无送达时段不进台账（S-34 ④ 同理，FI-Q2）。"""
    arr, beyond, unplaced = [], [], []
    for x in m.fba_inbound or ():
        base = {"sku": m.sku, "seller_sku": m.seller_sku, "sid": m.sid, "shipment_id": x.shipment_id,
                "status": x.status, "units": x.units, "order_period": None,
                "deliver_from": x.deliver_from and x.deliver_from.isoformat(),
                "deliver_to": x.deliver_to and x.deliver_to.isoformat()}
        if x.deliver_to is None:
            unplaced.append({**base, "arrive": None, "reason": "fba_no_eta"})
        elif x.deliver_to < start:
            unplaced.append({**base, "arrive": x.deliver_to.isoformat(), "reason": "fba_overdue"})
        elif x.deliver_to > end:
            beyond.append({**base, "arrive": x.deliver_to.isoformat(), "reason": "arrives_after_horizon"})
        else:
            arr.append(Arrival(x.deliver_to, x.units, {"kind": "fba_shipment", "shipment_id": x.shipment_id,
                       "status": x.status, "deliver_from": base["deliver_from"], "deliver_to": base["deliver_to"]}))
    return arr, beyond, unplaced
```

`project_msku`：`if m.fba_inbound is None and m.applicable and m.onhand is not None:` → 所有 live 月 `_blank(p, "unknown", "fba_inbound_unavailable", live[0])`（与 `unknown_onhand` 同分支写法）。

`forecast_plan`：在「第一遍 · 基线」之前

```python
fba_arr = {}
for m in inp.mskus:
    a, b, u = _place_fba(m, start, end)
    fba_arr[m.key] = a
    beyond += b
    unallocated += u
base = {m.key: project_msku(m, inp.as_of, inp.periods, fba_arr[m.key]) for m in inp.mskus}
arrivals = {m.key: list(fba_arr[m.key]) for m in inp.mskus}
```

（`final` 不变：`arrivals[m.key]` 已含 FBA + 采购分配。）`SkuMonth` 加字段 `fba_inbound: int | None = None`，构造时传 `sum(x.units for m in ms_list if m.applicable for x in fba_arr[m.key])`，任一 applicable msku `fba_inbound is None` → None。`bridge` 与 `as_of_start` 的依据里加 `"onhand_parts": m.onhand_parts`。

- [ ] **Step 4:** PASS；`uv run pytest -m pure -q` 全绿（恒等式自检照跑）。
- [ ] **Step 5: Commit** `feat(forecast): FBA 在途货件按送达结束日进台账（基线与终算两遍）`

---

### Task 4: 装配与响应（grid）

**Files:**
- Modify: `api/ui/plans.py`（`grid()` 取 `fc_transfers` / `fba_inbound`，传入）
- Modify: `api/ui/grid_forecast.py`（`forecast_payload` 新参 `fba_inbound` · `fc`；序列化）
- Modify: `tests/test_label_grid_fixture.py`（入参加空 `fba_inbound`），重生 `web/src/pages/__fixtures__/labelGrid*.json`
- Modify: `tests/fixtures/grid_response.json`（契约样例重生，`SCM_WRITE_CONTRACT=1`）
- Test: `tests/test_api_grid_forecast.py`（追加）

**Interfaces:**
- Consumes: Task 2 `Source.fba_inbound` / `fc_transfers`；Task 3 `MskuInput(fba_inbound=, onhand_parts=)`、`SkuMonth.fba_inbound`。
- Produces（响应）：`forecast[].basis.arrivals[]` FBA 条目 `{kind:"fba_shipment", date, units, shipment_id, status, deliver_from, deliver_to}`；`basis.as_of_start.onhand_parts` / `bridge.onhand_parts`；`forecast_sku[].pipeline.fba_inbound` · `fba_inbound_reason`（null 或 `"fba_inbound_unavailable"`）；`unallocated[]` FBA 条目（`scope:"msku"`）；`beyond_horizon[]` FBA 条目；`source_notes.dropped.fba_inbound`。

- [ ] **Step 1: 失败测试**（`test_api_grid_forecast.py`，用现有 `forecast_payload` 直调手法）

```python
def test_payload_carries_fba_arrivals_and_onhand_parts():
    out = payload_for(onhand={K: 52}, fc={K: 2},
                      fba_inbound={K: [FbaInbound("FBA1", "SHIPPED", 7, None, D(2026, 11, 8))]})
    f = [r for r in out["forecast"] if r["period"] == "2026-11"][0]
    assert f["basis"]["arrivals"][0]["kind"] == "fba_shipment"
    oct_ = [r for r in out["forecast"] if r["period"] == "2026-10"][0]
    assert oct_["basis"]["as_of_start"]["onhand_as_of"] == 54
    assert oct_["basis"]["as_of_start"]["onhand_parts"] == {"fulfillable": 52, "fc_transfers": 2}
    assert out["forecast_sku"][0]["pipeline"]["fba_inbound"] == 7

def test_unallocated_fba_entries_pass_core_key_check():
    out = payload_for(onhand={K: 52}, fc={K: 0},
                      fba_inbound={K: [FbaInbound("OLD", "SHIPPED", 1, None, D(2026, 10, 1))]})
    [u] = out["unallocated"]
    assert (u["scope"], u["reason"], u["shipment_id"], u["units"]) == ("msku", "fba_overdue", "OLD", 1)
```

（`payload_for` 为本文件 helper：一个 msku `K=("M","11072")`，as_of 2026-10-14，计划 10~12 月。）

- [ ] **Step 2:** FAIL（`TypeError fba_inbound`）。

- [ ] **Step 3: 实现**
  - `forecast_payload(..., fba_inbound: dict | None = None, fc: dict | None = None)`：`MskuInput(..., onhand=None if onhand.get(k) is None else onhand[k] + (fc or {}).get(k, 0), fba_inbound=_fba(k), onhand_parts=...)`，`_fba(k)`：`fba_inbound is None` → `()`；`fba_inbound.get(k, ())` 为 `None` → `None`（未知）；否则 `tuple(...)`。
  - 序列化：`pipeline.fba_inbound = s.fba_inbound`，`fba_inbound_reason = None if s.fba_inbound is not None else "fba_inbound_unavailable"`；`in_transit_excludes` 保留 `["fba_inbound"]`（已进月末）。
  - `unallocated_json` 的核心键检查对 FBA 条目成立（`order_period` 为 None 但键在）。
  - `plans.grid()`：`if fba: onhand_by_msku[k] = ...; fc_by_msku[k] = source.fc_transfers(...); inbound_by_msku[k] = source.fba_inbound(...)`，`hasattr` 守卫同 `purchase_as_of`；传入 `forecast_payload`。
  - `except (...)` 元组不变（新方法只抛 `ChUnavailable`）。
  - 重生：`.venv/bin/python -m tests.test_label_grid_fixture`；`SCM_WRITE_CONTRACT=1 uv run pytest tests/test_grid_fixture.py -q`。

- [ ] **Step 4:** `uv run pytest -q` 全绿（含集成层，`scm_test`）。
- [ ] **Step 5: Commit** `feat(grid): 预测响应带 FBA 货件到货、调仓中与未计入点名`

---

### Task 5: 前端展示

**Files:**
- Modify: `web/src/api/types.ts`（`ForecastArrival` 联合类型、`onhand_parts`、`pipeline.fba_inbound`、unallocated FBA 字段）
- Modify: `web/src/pages/ForecastBasis.tsx`（`arrivalLine` 分支 · 未计入 FBA 行）
- Modify: `web/src/pages/PlanSheet.tsx`（「现可售」→「现有」+ title 两部分；货号行在途 title）
- Modify: `web/src/pages/planGridModel.ts`（`onhandParts` 求和 helper）
- Test: `web/src/pages/PlanSheet.labels.test.tsx` · `web/src/pages/ForecastBasis.test.tsx`

**Interfaces:**
- Consumes: Task 4 响应形状（labelGrid JSON 已重生）。

- [ ] **Step 1: 失败测试**

```tsx
it('起点格写「现有」，title 拆成可售 + 调仓中', () => {
  renderSheet(withParts(LABEL_GRID_PARTIAL, { fulfillable: 290, fc_transfers: 10 }));
  const el = screen.getByTestId('onhand-11072-SKU-1-2026-10');
  expect(el.closest('span')).toHaveTextContent(/^现有/);
  expect(el.closest('[title]')).toHaveAttribute('title', expect.stringMatching(/可售 .*调仓中 .*采集于 2026-10-10/));
});
```

```tsx
it('依据面板：FBA 货件逐条「货件号 · N 件 · 送达 x~y」，逾期的单列「未计入」', async () => {
  // grid 夹具里给 MSKU-A 11 月一条 fba_shipment 到货 + unallocated 一条 fba_overdue
  ...
  expect(panel).toHaveTextContent('FBA 货件 FBA1 · 7 件 · 送达 11-01~11-08');
  expect(panel).toHaveTextContent('未计入：FBA 货件 OLD 1 件（送达时段已过未签收）');
});
```

（`withParts` / 夹具改写放在测试文件内，用 `structuredClone` 改冻结网格的 `basis`，不改 JSON 文件。）

- [ ] **Step 2:** `cd web && npx vitest run src/pages/PlanSheet.labels.test.tsx src/pages/ForecastBasis.test.tsx` → FAIL。

- [ ] **Step 3: 实现**
  - `arrivalLine(a)`：`if (a.kind === 'fba_shipment') return \`${md(a.date)} 到货 ${a.units}（FBA 货件 ${a.shipment_id} · 送达 ${md(a.deliver_from)}~${md(a.deliver_to)}，按结束日）\``。
  - 未计入：`unalloc` 里 `reason` 为 `fba_overdue` / `fba_no_eta` 的写「未计入：FBA 货件 X N 件（送达时段已过未签收 / 没有送达时段）」。
  - 「现可售」→「现有」，`onhandTitle` 改为函数 `(parts) => \`FBA 可售 ${f} + 调仓中 ${c}，采集于 ${as_of}\``；店铺行 parts = Σ msku（任一缺 → 只写合计）。
  - 货号行「在途」title：`采购管道；另有 FBA 货件 ${fba_inbound} 件已计入月末`（`fba_inbound` null → 「FBA 货件当天未采到，月末未知」）。

- [ ] **Step 4:** `npx tsc --noEmit -p . && npx vitest run` 全绿。
- [ ] **Step 5: Commit** `feat(web): 计划页显示 FBA 货件到货与「现有」拆分`

---

### Task 6: 文档 + 真数据回归

**Files:**
- Modify: `docs/08-后端接口清单.md`（grid 预测块新字段）· `docs/10-前端交互界面.md`（「现有」· 依据面板 FBA 行）· `docs/14-库存预测数学模型.md`（到货 = 采购分配 + FBA 货件；期初含调仓）· `docs/00-待裁定清单.md`（OP-14 标「已推翻」、S-34 ⑥ 加修订注、登记 FI-Q1 / FI-Q2）
- Create: `tests/fixtures/fba_inbound_20261014.json`（DCC1042015GLZ2B 那 8 个货件的真实行，人工从 CH 抄出，带出处注释）
- Test: `tests/test_forecast_fba_inbound_regression.py`（pure）

- [ ] **Step 1: 回归测试**：读 fixture → `parse_fba_inbound` → 断言 Σ SHIPPED 剩余 = 31、到货日集合 = {11-08, 11-15, 11-22}；喂 `forecast_plan`（onhand 52 + fc 2，期望 27/26/27…）→ 断言 11 月末 = 37+2−26+7+5+19… 由测试里逐项写出算式，不写死结果。
- [ ] **Step 2:** 先把 `_place_fba` 的 `<` 改成 `<=` 看它红（边界：结束日 = as_of 视为逾期），改回。
- [ ] **Step 3:** 文档改动；`cd docs/prototype && node test_doc_index.js && node test_retired_claims.js && node test_doc_flow.js` 全绿。
- [ ] **Step 4:** 临时后端（8191，scheduler 关）读计划 #2 的 grid，人工核对 DCC1042015GLZ2B：11 月到货 31、断货日推后；用完关掉。
- [ ] **Step 5: Commit** `docs+test: FBA 在途进预测 —— 文档同步与 10-14 真数据回归`
