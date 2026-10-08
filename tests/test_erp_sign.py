from decimal import Decimal

from erp.sign import normalize, sign

APP = "0123456789abcdef"
V1 = {"access_token": "tok", "app_key": APP, "timestamp": 1700000000}
V2 = {**V1, "custom_order_sn": "SCM-PO-1",
      "product_list": [{"sku": "S1", "quantity_real": 30, "price": 12.5}], "remark": "中文", "qc_type": 2}


def test_matches_typescript_reference():
    """★ 期望值由兄弟仓 ecops/plugins/global-ecommerce-erp 的 crypto-js + md5 原实现算出
    （generateSign，2026-10-07）。改签名逻辑而这两条不红 = 没测到。"""
    assert sign(V1, APP) == "LG7wNUD5KdYv4wCsPB0nYyRFM/UWQeK7wYyJ07ItaG43ciLgYakkxZHNnCfqFj7U"
    assert sign(V2, APP) == "4rmcdsPlHxe3y7FAj+UixIuk3ov3RX5wf863K1r4dUA3ciLgYakkxZHNnCfqFj7U"


def test_normalize_matches_js_number_printing():
    # JS String(30.0) == "30"；Python str(30.0) == "30.0" —— 不归一就签不对
    assert normalize({"a": 30.0, "b": Decimal("12.50"), "c": [Decimal(7)]}) == {"a": 30, "b": 12.5, "c": [7]}
