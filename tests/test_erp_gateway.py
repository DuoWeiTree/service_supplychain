import pytest

from erp import endpoints, gateway
from erp.drivers.fake import FAKE


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


def _p():
    return {"custom_order_sn": "SCM-PO-1", "supplier_id": 8085, "purchaser_id": 111, "opt_uid": 1,
            "qc_type": 2, "wid": 1001, "product_list": [{"sku": "S1", "quantity_real": 3, "price": 1.5}]}


def test_unregistered_endpoint_refused():
    with pytest.raises(endpoints.UnregisteredEndpoint):
        gateway.call("cancel_purchase_order", {})


def test_create_is_registered_irreversible_with_lookup():
    ep = endpoints.get("create_purchase_order")
    assert (ep.side_effect, ep.idem_param, ep.lookup) == ("irreversible", "custom_order_sn", "purchase_order_list")


def test_fake_unknown_created_is_findable_by_lookup():
    FAKE.enqueue("create_purchase_order", "unknown_created")
    _, r = gateway.call("create_purchase_order", _p())
    assert r.outcome == "unknown"
    _, lst = gateway.call("purchase_order_list", {"search_field_time": "create_time"})
    assert [o["custom_order_sn"] for o in lst.body["data"]] == ["SCM-PO-1"]


def test_fake_unknown_absent_is_not_findable():
    FAKE.enqueue("create_purchase_order", "unknown_absent")
    gateway.call("create_purchase_order", _p())
    _, lst = gateway.call("purchase_order_list", {})
    assert lst.body["data"] == []


def test_redact_hides_secrets():
    assert gateway.redact({"access_token": "t", "sign": "s", "sku": "S1"}) == \
        {"access_token": "***", "sign": "***", "sku": "S1"}
