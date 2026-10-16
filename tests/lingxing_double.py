"""领星替身（仅测试）：conftest 的 autouse 夹具把网关的 driver 换成它。
脚本化结果让「结果未知」「业务拒绝」「没发出去」都能在本机复现，不打真领星。"""
from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict, deque

from erp.endpoints import Endpoint
from erp.transport import CallResult

HOST = "lingxing.invalid"
OUTCOMES = ("ok", "rejected", "rejected_auth", "not_sent", "unknown_created", "unknown_absent")


class LingxingDouble:

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self._orders: dict[str, dict] = {}       # order_sn → order
        #: 海外仓待收货备货单（`ows_in_transit_list`，spec 2026-10-15）：测试按领星原样字段塞
        self.in_transit_orders: list[dict] = []
        self._script: dict[str, deque] = defaultdict(deque)
        self._seq = 0
        self._stock: list[dict] = []             # inventoryDetails 的行（真实形状的子集）
        # channelList（10-12 实测形状的子集）：id 是字符串、enabled 0/1
        self._channels: list[dict] = [{"id": "28762", "channel_name": "美森快船", "method_name": "海卡", "enabled": 1,
                                       "provider": {"logistics_provider_name": "厦门快驿通"}, "receive_country_codes": ["US"]}]
        self.channel_calls = 0
        self._ows: dict[str, dict] = {}          # 海外仓备货单：overseas_order_no → 单
        self.ows_creates: list[dict] = []        # 每一次 createInbound 的入参（含没落单的）
        # 亚马逊 listing（10-13 实测形状的子集）：(sid, seller_sku) → fnsku（'' = listing 在但没有 FNSKU）
        self._listing: dict[tuple[str, str], str] = {}
        # FBA 发货计划（10-13 实调形状的子集，CC-D3）：批次号 → {seq, remark, sid, list[计划]}
        self._fba: dict[str, dict] = {}
        self.fba_creates: list[dict] = []
        self._fba_seq = 0
        # matchSkuList（10-12 实测形状的子集）：接了 API 的三方仓 wid → 行
        self._match: dict[int, list[dict]] = {}

    def set_match(self, wid: int, product_id: int | None, code: str, matched: bool = True) -> None:
        """三方仓产品配对。product_id=None + matched=False = 只有三方产品、没配本地产品。"""
        self._match.setdefault(wid, []).append({"wid": wid, "product_id": product_id or 0, "oversea_product_code": code,
                                                "is_matched": matched, "local_sku": None, "sid": 0})

    def enqueue(self, endpoint: str, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"替身 outcome 只能是 {OUTCOMES}，得到 {outcome!r}")
        self._script[endpoint].append(outcome)

    def mutate_item(self, order_sn: str, sku: str, quantity_real=None, price=None) -> None:
        """领星侧变更单（实测 3 张）改了落下的数 —— 喂 G1 核对。"""
        order = self._orders.get(order_sn)
        if order is None:
            raise KeyError(f"替身里没有 order_sn={order_sn!r}")
        hit = [it for it in order["item_list"] if it["sku"] == sku]
        if not hit:
            raise KeyError(f"order_sn={order_sn!r} 里没有 sku={sku!r}")
        for it in hit:
            if quantity_real is not None:
                it["quantity_real"] = quantity_real
            if price is not None:
                it["price"] = price

    def set_arrival(self, order_sn: str, sku: str, entered: int) -> None:
        """领星侧到货入库：已入库 = entered，待到货 = 实际 − entered。"""
        for it in self._orders[order_sn]["item_list"]:
            if it["sku"] == sku:
                it["quantity_entry"], it["quantity_receive"] = entered, int(it["quantity_real"]) - entered

    def set_stock(self, rows: list[tuple[int, str, str, int]]) -> None:
        """(wid, sku, seller_id, product_valid_num) —— 领星 inventoryDetails 的实时库存。"""
        self._stock = [{"wid": w, "sku": k, "seller_id": s_, "fnsku": "", "product_valid_num": v,
                        "product_lock_num": 0} for w, k, s_, v in rows]

    def _inventory(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        wids = {int(x) for x in str(p.get("wid") or "").split(",") if x}
        rows = [r for r in self._stock if (not wids or r["wid"] in wids) and str(p.get("sku") or "") in r["sku"]]
        off, n = int(p.get("offset") or 0), int(p.get("length") or 20)
        return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                          body={"code": 0, "message": "success", "total": len(rows), "data": rows[off:off + n]})

    # ── 海外仓备货单（形状照 CB-D4 实调：建成 = 40 待发货；被拒不落单；删后查不到）──
    def ship_ows(self, ows_no: str) -> None:
        """领星侧仓库已发货（50 待收货）—— 删不掉了。"""
        self._ows[ows_no]["status"] = "50"

    def fba_put(self, p: dict) -> str:
        """领星侧落一个 FBA 发货计划批次（建或「有人在领星里建了」）：建出来 status=5 待处理、lock_status=1 未锁（CC-D3）。"""
        self._fba_seq += 1
        seq = f"RP2610130{self._fba_seq:02d}"
        rows = [{"order_sn": f"R2610130{self._fba_seq:02d}{i}", "isp_id": 1000 + self._fba_seq * 10 + i, "status": 5,
                 "lock_status": 1, "msku": x["msku"], "fnsku": x["fnsku"], "wid": x.get("wid"),
                 "shipment_plan_quantity": x["shipment_plan_quantity"], "remark": "", "mws_relate": []}
                for i, x in enumerate(p["product_list"])]
        sid = str(p["product_list"][0]["sid"]) if p["product_list"] else "0"
        self._fba[seq] = {"seq": seq, "remark": p.get("remark", ""), "sid": sid, "list": rows}
        return seq

    def set_listing(self, sid: str, seller_sku: str, fnsku: str) -> None:
        self._listing[(str(sid), seller_sku)] = fnsku

    def _amazon_listing(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        sids = set(str(p.get("sid") or "").split(","))
        want = set(p.get("search_value") or [])
        rows = [{"sid": int(sid), "seller_sku": m, "fnsku": fn, "status": 1, "is_delete": 0}
                for (sid, m), fn in self._listing.items() if sid in sids and m in want]
        return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                          body={"code": 0, "message": "success", "total": len(rows), "data": rows})

    def set_fba_status(self, seq: str, status: int) -> None:
        for x in self._fba[seq]["list"]:
            x["status"] = status

    def _fba_plan(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        ok = lambda body: CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                                     body={"code": 0, "message": "success", **body})
        if ep.name == "create_fba_plan":
            self.fba_creates.append(p)
            if outcome == "rejected":
                return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="500",
                                  lx_msg="double: 店铺无此 msku", body={"code": 500, "message": "double: 店铺无此 msku"})
            seq = self.fba_put(p)
            if outcome == "unknown_absent":           # 发出去了但没收到回应，且领星里其实没建
                del self._fba[seq]
                return CallResult("unknown", HOST, ep.path, 1, cause="double: TimeoutError")
            if outcome == "unknown_created":
                return CallResult("unknown", HOST, ep.path, 1, cause="double: TimeoutError")
            return ok({"data": {"seq": seq, "order_sn": [x["order_sn"] for x in self._fba[seq]["list"]]}})
        if ep.name == "fba_plan_list":
            if not isinstance(p.get("sids"), str):     # ★ 10-13 实测：数组回 500 explode()
                return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="500",
                                  lx_msg="explode() expects parameter 2 to be string, array given", body={"code": 500})
            sids = set(p["sids"].split(","))
            rows = [dict(g, list=[dict(x) for x in g["list"]]) for g in self._fba.values()
                    if g["sid"] in sids and (not p.get("seq") or g["seq"] == p["seq"])]
            off, n = int(p.get("offset") or 0), int(p.get("length") or 20)
            return ok({"total": len(rows), "data": rows[off:off + n]})
        if ep.name in ("fba_plan_lock", "fba_plan_unlock"):
            if outcome == "rejected":
                return ok({"data": {"error": [{"orderNo": p["orderNos"][0], "msg": "double: 库存不足"}], "failNum": 1,
                                    "succNum": 0}})
            want = 2 if ep.name == "fba_plan_lock" else 1
            for g in self._fba.values():
                for x in g["list"]:
                    if x["order_sn"] in p["orderNos"]:
                        x["lock_status"] = want
            return ok({"data": {"error": [], "failNum": 0, "succNum": len(p["orderNos"])}, "total": 0})
        if ep.name == "fba_plan_update":
            if p.get("shipment_plan_quantity") == 0:
                return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="102",
                                  lx_msg="参数错误：['shipment_plan_quantity必须大于 0']", body={"code": 102})
            for g in self._fba.values():
                for x in g["list"]:
                    if x["order_sn"] == p["order_sn"] and "remark" in p:
                        x["remark"] = p["remark"]
            return ok({"data": []})
        raise NotImplementedError(ep.name)

    def set_ows_status(self, ows_no: str, status: str, sub_status: str | None = None) -> None:
        """领星侧进度推进（回执读回用）。"""
        self._ows[ows_no]["status"] = status
        if sub_status is not None:
            self._ows[ows_no]["sub_status"] = sub_status

    def _ows_put(self, p: dict) -> str:
        self._seq += 1
        no = f"OWS2610120{self._seq:02d}"
        self._ows[no] = {"overseas_order_no": no, "inbound_order_no": p["inbound_order_no"], "status": "40",
                         "s_wid": p["s_wid"], "r_wid": p["r_wid"], "products": p["product_list"]}
        return no

    def _ows_create(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        self.ows_creates.append(p)
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        if outcome == "rejected":
            return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="500",
                              lx_msg="double: 物流方式不存在", body={"code": 500, "message": "double: 物流方式不存在"})
        if p.get("r_wid") in self._match and any(not x.get("oversea_product_code") for x in p["product_list"]):
            pid = next(x["product_id"] for x in p["product_list"] if not x.get("oversea_product_code"))
            msg = f"内部错误：产品id:{pid}+店铺id:0+FNSKU:第三方sku不能为空 [请求码:DOUBLE]"     # 10-12 实调原文
            return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="500", lx_msg=msg,
                              body={"code": 500, "message": msg})
        if p.get("logistics_list_type") != 1:      # CB-D4 实调：不是新版物流信息直接 500
            return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="500",
                              lx_msg="内部错误：请使用新版费用物流轨迹(logistics_list_type必须为1)",
                              body={"code": 500, "message": "logistics_list_type必须为1"})
        if outcome == "unknown_absent":
            return CallResult("unknown", HOST, ep.path, 30000, cause="double: TimeoutError")
        no = self._ows_put(p)
        if outcome == "unknown_created":
            return CallResult("unknown", HOST, ep.path, 30000, cause="double: TimeoutError")
        return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                          body={"code": 0, "message": "success", "data": {"overseas_order_no": no}})

    def _ows_nos(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        refs = set(p.get("inbound_order_no") or [])
        data = [{"overseas_order_no": o["overseas_order_no"], "inbound_order_no": o["inbound_order_no"],
                 "create_time": "2026-10-12 16:26:45"} for o in self._ows.values() if o["inbound_order_no"] in refs]
        return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                          body={"code": 0, "message": "success", "data": data})

    def _ows_detail(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        o = self._ows.get(p.get("overseas_order_no"))
        if o is None:
            return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="500",
                              lx_msg="程序内部错误：['海外仓备货单不存在或已删除']", body={"code": 500})
        return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                          body={"code": 0, "message": "success", "data": dict(o)})

    def _ows_delete(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        for no in p.get("overseas_order_nos") or []:
            o = self._ows.get(no)
            if o is None or o["status"] not in ("10", "20", "30", "40"):
                return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="500",
                                  lx_msg="double: 当前状态不能删除", body={"code": 500})
        for no in p.get("overseas_order_nos") or []:
            del self._ows[no]
        return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="操作成功：['操作成功']",
                          body={"code": 0, "message": "操作成功", "data": None})

    def call(self, ep: Endpoint, params: dict) -> CallResult:
        with self._lock:
            outcome = self._script[ep.name].popleft() if self._script[ep.name] else "ok"
            if ep.name == "create_purchase_order":
                return self._create(ep, params, outcome)
            if ep.name == "purchase_order_list":
                return self._list(ep, params, outcome)
            if ep.name == "local_inventory_details":
                return self._inventory(ep, params, outcome)
            if ep.name == "create_ows_order":
                return self._ows_create(ep, params, outcome)
            if ep.name == "ows_order_nos":
                return self._ows_nos(ep, params, outcome)
            if ep.name == "ows_order_detail":
                return self._ows_detail(ep, params, outcome)
            if ep.name == "ows_order_delete":
                return self._ows_delete(ep, params, outcome)
            if ep.name == "amazon_listing":
                return self._amazon_listing(ep, params, outcome)
            if ep.name in ("create_fba_plan", "fba_plan_list", "fba_plan_lock", "fba_plan_unlock", "fba_plan_update"):
                return self._fba_plan(ep, params, outcome)
            if ep.name == "ows_sku_match":
                if outcome == "not_sent":
                    return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
                rows = [x for w in str(params.get("wid") or "").split(",") if w for x in self._match.get(int(w), [])]
                if params.get("is_matched") == 1:
                    rows = [x for x in rows if x["is_matched"]]
                off, n = int(params.get("offset") or 0), int(params.get("length") or 20)
                return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                                  body={"code": 0, "message": "success", "total": len(rows), "data": rows[off:off + n]})
            if ep.name == "ows_in_transit_list":
                if outcome == "not_sent":
                    return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
                size, page = int(params.get("page_size") or 20), int(params.get("page") or 1)
                rows = self.in_transit_orders[(page - 1) * size: page * size]
                return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                                  body={"code": 0, "message": "success", "total": len(self.in_transit_orders),
                                        "data": rows})
            if ep.name == "logistics_channel_list":
                self.channel_calls += 1
                if outcome == "not_sent":
                    return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
                off, n = int(params.get("offset") or 0), int(params.get("length") or 20)
                return CallResult("ok", HOST, ep.path, 1, http_status=200, lx_code="0", lx_msg="success",
                                  body={"code": 0, "message": "success", "total": len(self._channels),
                                        "data": self._channels[off:off + n]})
        raise NotImplementedError(f"替身未实现 {ep.name}")

    def _create(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        if outcome == "rejected":
            return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="1001",
                              lx_msg="double: 供应商不存在", body={"code": 1001, "message": "double: 供应商不存在"})
        if outcome == "rejected_auth":
            # 照 erp_call 1·2（2026-10-09）实收的形状：HTTP 200 + code "403"，键名是 msg
            return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="403",
                              lx_msg="授权失效，请更新授权有效期或检查权限",
                              body={"code": "403", "msg": "授权失效，请更新授权有效期或检查权限", "data": None})
        order_sn = None
        if outcome in ("ok", "unknown_created"):
            self._seq += 1
            order_sn = f"FAKE-PO{self._seq:06d}"
            self._orders[order_sn] = {
                "order_sn": order_sn, "custom_order_sn": p["custom_order_sn"],
                # 照真领星（PB-D26）：入参是系统 id sys_supplier_id / sys_wid，读回的 supplier_id / wid 就是它们
                "supplier_id": str(p["sys_supplier_id"]), "status": 2,
                "create_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # noqa: DTZ005 领星 create_time 是无时区本地时间串
                "item_list": [{"id": str(9000 + n), "sku": it["sku"], "wid": p["sys_wid"],
                               "quantity_real": it["quantity_real"], "price": it["price"], "is_delete": 0,
                               # ★ 实测形状（10-12 PO261012001）：行级 已入库 / 待到货
                               "quantity_entry": 0, "quantity_receive": it["quantity_real"]}
                              for n, it in enumerate(p["product_list"], start=1)]}
        if outcome.startswith("unknown"):
            return CallResult("unknown", HOST, ep.path, 30000, cause="double: TimeoutError: timed out (read)")
        return CallResult("ok", HOST, ep.path, 5, http_status=200, lx_code="0",
                          body={"code": 0, "data": {"order_sn": order_sn}})

    def _list(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", HOST, ep.path, 1, cause="double: ConnectionRefusedError[errno=111]")
        if outcome == "rejected":
            return CallResult("rejected", HOST, ep.path, 1, http_status=200, lx_code="1001",
                              lx_msg="double: 查询被拒", body={"code": 1001, "message": "double: 查询被拒"})
        if outcome.startswith("unknown"):
            return CallResult("unknown", HOST, ep.path, 30000, cause="double: TimeoutError: timed out (read)")
        # 照真领星（文档 + 2026-10-10 实测）：日期必填；order_sn[] / custom_order_sn[] 过滤；别的键（如 order_sns）不认
        assert p.get("start_date") and p.get("end_date"), f"purchaseOrderList 缺必填 start_date/end_date：{sorted(p)}"
        rows = list(self._orders.values())
        if p.get("order_sn"):
            rows = [r for r in rows if r["order_sn"] in set(p["order_sn"])]
        if p.get("custom_order_sn"):
            rows = [r for r in rows if r["custom_order_sn"] in set(p["custom_order_sn"])]
        return CallResult("ok", HOST, ep.path, 3, http_status=200, lx_code="0",
                          body={"code": 0, "data": rows, "total": len(rows)})

