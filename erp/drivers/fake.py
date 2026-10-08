"""fake driver：扮演领星。★ 进程内存储，重启即失（仅开发 / 测试）。
脚本化结果让「结果未知」「业务拒绝」「没发出去」都能在本机复现（spec §11 G2）。"""
from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict, deque

from erp.endpoints import Endpoint
from erp.transport import CallResult

OUTCOMES = ("ok", "rejected", "not_sent", "unknown_created", "unknown_absent")


class FakeDriver:
    mode = "fake"

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self._orders: dict[str, dict] = {}       # order_sn → order
        self._script: dict[str, deque] = defaultdict(deque)
        self._seq = 0

    def enqueue(self, endpoint: str, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"fake outcome 只能是 {OUTCOMES}，得到 {outcome!r}")
        self._script[endpoint].append(outcome)

    def mutate_item(self, order_sn: str, sku: str, quantity_real=None, price=None) -> None:
        """模拟领星侧变更单（实测 3 张）改了落下的数 —— 喂 G1 核对。"""
        for it in self._orders[order_sn]["item_list"]:
            if it["sku"] == sku:
                if quantity_real is not None:
                    it["quantity_real"] = quantity_real
                if price is not None:
                    it["price"] = price

    def call(self, ep: Endpoint, params: dict) -> CallResult:
        with self._lock:
            outcome = self._script[ep.name].popleft() if self._script[ep.name] else "ok"
            if ep.name == "create_purchase_order":
                return self._create(ep, params, outcome)
            if ep.name == "purchase_order_list":
                return self._list(ep, params, outcome)
        raise NotImplementedError(f"fake driver 未实现 {ep.name}")

    def _create(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", "fake", ep.path, 1, cause="fake: ConnectionRefusedError[errno=111]")
        if outcome == "rejected":
            return CallResult("rejected", "fake", ep.path, 1, http_status=200, lx_code="1001",
                              lx_msg="fake: 供应商不存在", body={"code": 1001, "message": "fake: 供应商不存在"})
        order_sn = None
        if outcome in ("ok", "unknown_created"):
            self._seq += 1
            order_sn = f"FAKE-PO{self._seq:06d}"
            self._orders[order_sn] = {
                "order_sn": order_sn, "custom_order_sn": p["custom_order_sn"],
                "supplier_id": str(p["supplier_id"]), "status": 2,
                "create_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # noqa: DTZ005 领星 create_time 是无时区本地时间串
                "item_list": [{"item_id": str(9000 + n), "sku": it["sku"], "wid": it.get("wid", p.get("wid")),
                               "quantity_real": it["quantity_real"], "price": it["price"], "is_delete": 0}
                              for n, it in enumerate(p["product_list"], start=1)]}
        if outcome.startswith("unknown"):
            return CallResult("unknown", "fake", ep.path, 30000, cause="fake: TimeoutError: timed out (read)")
        return CallResult("ok", "fake", ep.path, 5, http_status=200, lx_code="0",
                          body={"code": 0, "data": {"order_sn": order_sn}})

    def _list(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", "fake", ep.path, 1, cause="fake: ConnectionRefusedError[errno=111]")
        if outcome == "rejected":
            return CallResult("rejected", "fake", ep.path, 1, http_status=200, lx_code="1001",
                              lx_msg="fake: 查询被拒", body={"code": 1001, "message": "fake: 查询被拒"})
        if outcome.startswith("unknown"):
            return CallResult("unknown", "fake", ep.path, 30000, cause="fake: TimeoutError: timed out (read)")
        rows = list(self._orders.values())
        if p.get("order_sns"):
            wanted = set(str(p["order_sns"]).split(","))
            rows = [r for r in rows if r["order_sn"] in wanted]
        return CallResult("ok", "fake", ep.path, 3, http_status=200, lx_code="0",
                          body={"code": 0, "data": rows, "total": len(rows)})


FAKE = FakeDriver()
