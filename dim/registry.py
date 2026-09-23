"""镜像登记表 —— 「有哪些镜像」的唯一真相（设计 §3）。

★ 这里只登记「是什么」，不登记「怎么落库」：任何写库用的 DDL/DML 关键字
  （哪怕只出现在注释字符串里）都会被 test_l7_no_writes_to_clickhouse 的
  正则扫中，判定成「dim/ 里出现了写操作」。落库的 SQL 文本放在 jobs/。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dim import ch_source


@dataclass(frozen=True)
class Mirror:
    name: str                       # = PG 表名，也是 v_mirror_freshness 的 mirror 值
    stage: str                      # A | B | C | D
    kind: str                       # refresh | append | mutable
    staleness: str                  # gate_503 | label_only | none
    key_columns: tuple[str, ...]
    columns: tuple[str, ...] = ()   # fetch 返回的列序，jobs 据它拼 upsert
    source: str | None = None       # CH / PM / 领星 源；None = 源未定（设计 §10）
    fetch: Callable | None = None   # None ⟺ pending
    coverage: tuple[str, ...] = ()  # ("rows",) | ("rows", "distinct:sid")
    depends_on: tuple[str, ...] = ()
    companions: tuple[str, ...] = ()
    pending: bool = False
    note: str = ""


#: ★ 阶段 A 四张的 fetch 由 Task 4 接上（dim/ch_source.py 的四个纯变换）。
MIRRORS: tuple[Mirror, ...] = (
    Mirror(name="seller", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("seller_id",),
           columns=("seller_id", "name", "market", "has_fba", "platform"),
           source="jxd_raw.lingxing_seller_list",
           fetch=ch_source.fetch_seller,
           coverage=("rows",),
           note="源已裁定（OQ-2，09-22）：jxd_raw.lingxing_seller_list；"
                "has_fba 派生自 lingxing_product_listing.fulfillment_channel_type='FBA'"
                "（OQ-3，同日裁定），不是源列；探针 §7.0 实测后追加裁定（控制器 09-22）："
                "该表没有 marketplace/platform 列——market 改用 country，"
                "platform 写常量 'amazon'（该表本就是领星的亚马逊店铺列表）"),
    Mirror(name="sku_catalog", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("sku",), columns=("sku", "name"),
           source="jxd_raw.lingxing_product_local_products",
           fetch=ch_source.fetch_sku_catalog,
           coverage=("rows",)),
    Mirror(name="msku_bridge", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("seller_sku", "sid"), columns=("seller_sku", "sid", "sku"),
           source="jxd_raw.lingxing_product_listing",
           fetch=ch_source.fetch_msku_bridge,
           coverage=("rows", "distinct:sid"),
           depends_on=("seller", "sku_catalog"),
           note="★ GROUP BY seller_sku, sid —— sid 绝不参与 argMax；"
                "amzn.gr.* 虚拟促销组与未绑货号的行一律丢弃并计数（控制器 09-22 追加裁定）"),
    Mirror(name="warehouse", stage="A", kind="refresh", staleness="gate_503",
           key_columns=("wid",), columns=("wid", "name", "kind", "market"),
           source="jxd_raw.lingxing_inventory_warehouses",
           fetch=ch_source.fetch_warehouse,
           coverage=("rows",)),
    Mirror(name="sku_category", stage="A", kind="refresh", staleness="label_only",
           key_columns=("sku",), companions=("category_refresh",), pending=True,
           note="A-1。migrations/pg/*.sql 里一张都没有（OQ-9），PM 接入方式未落文档"),
    Mirror(name="po_snapshot", stage="B", kind="append", staleness="none",
           key_columns=("po_no", "observed_on"), pending=True),
    Mirror(name="po_receipt", stage="B", kind="append", staleness="none",
           key_columns=("receipt_no",), pending=True),
    Mirror(name="supplier", stage="B", kind="mutable", staleness="none",
           key_columns=("supplier_id",), pending=True,
           note="源是领星 /erp/sc/data/local_inventory/supplier（08:243），不是 CH"),
    Mirror(name="ext_stage_observation", stage="C", kind="append", staleness="none",
           key_columns=("ext_ref_id", "observed_on"), pending=True),
    Mirror(name="sales_actual", stage="D", kind="append", staleness="none",
           key_columns=("sku", "sid", "period"), pending=True),
)

_BY_NAME = {m.name: m for m in MIRRORS}


def by_name(name: str) -> Mirror:
    if name not in _BY_NAME:
        raise KeyError(f"登记表里没有镜像 {name!r}；已登记的：{sorted(_BY_NAME)}")
    return _BY_NAME[name]


def gate_503_names() -> set[str]:
    return {m.name for m in MIRRORS if m.staleness == "gate_503"}


def refresh_order() -> list[Mirror]:
    """按 depends_on 拓扑排。★ 环要硬失败 —— 静默丢掉一个条目等于那张表永远不刷。"""
    todo = [m for m in MIRRORS if not m.pending]
    out: list[Mirror] = []
    done: set[str] = set()
    while todo:
        ready = [m for m in todo if set(m.depends_on) <= done]
        if not ready:
            raise ValueError(f"depends_on 成环或指向 pending 条目：{[m.name for m in todo]}")
        ready.sort(key=lambda m: m.name)
        out += ready
        done |= {m.name for m in ready}
        todo = [m for m in todo if m.name not in done]
    return out
