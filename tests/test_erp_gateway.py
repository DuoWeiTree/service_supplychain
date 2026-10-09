import pytest

from erp import endpoints, gateway


def _p():
    return {"custom_order_sn": "SCM-PO-1", "supplier_id": 8085, "purchaser_id": 111, "opt_uid": 1,
            "qc_type": 2, "wid": 1001, "product_list": [{"sku": "S1", "quantity_real": 3, "price": 1.5}]}


def test_unregistered_endpoint_refused():
    with pytest.raises(endpoints.UnregisteredEndpoint):
        gateway.call("cancel_purchase_order", {})


def test_create_is_registered_irreversible_with_lookup():
    ep = endpoints.get("create_purchase_order")
    assert (ep.side_effect, ep.idem_param, ep.lookup) == ("irreversible", "custom_order_sn", "purchase_order_list")


def test_double_unknown_created_is_findable_by_lookup(lingxing):
    lingxing.enqueue("create_purchase_order", "unknown_created")
    _, r = gateway.call("create_purchase_order", _p())
    assert r.outcome == "unknown"
    _, lst = gateway.call("purchase_order_list", {"search_field_time": "create_time"})
    assert [o["custom_order_sn"] for o in lst.body["data"]] == ["SCM-PO-1"]


def test_double_unknown_absent_is_not_findable(lingxing):
    lingxing.enqueue("create_purchase_order", "unknown_absent")
    gateway.call("create_purchase_order", _p())
    _, lst = gateway.call("purchase_order_list", {})
    assert lst.body["data"] == []


def test_redact_hides_secrets():
    assert gateway.redact({"access_token": "t", "sign": "s", "sku": "S1"}) == \
        {"access_token": "***", "sign": "***", "sku": "S1"}


def test_double_list_honours_scripted_outcomes(lingxing):
    for scripted, want in [("not_sent", "not_sent"), ("rejected", "rejected"),
                           ("unknown_created", "unknown"), ("unknown_absent", "unknown")]:
        lingxing.enqueue("purchase_order_list", scripted)
        _, r = gateway.call("purchase_order_list", {})
        assert r.outcome == want, scripted
    _, r = gateway.call("purchase_order_list", {})
    assert r.outcome == "ok"


def test_unexpected_driver_exception_is_logged_then_reraised(monkeypatch, caplog):
    class Boom:
        def call(self, ep, params):
            raise RuntimeError("boom")
    monkeypatch.setattr(gateway, "_driver", lambda: Boom())
    with caplog.at_level("WARNING", logger="scm.erp"), pytest.raises(RuntimeError):
        gateway.call("create_purchase_order", _p())
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "outcome=exception" in msg and "SCM-PO-1" in msg and "RuntimeError" in msg
