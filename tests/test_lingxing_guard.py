"""★ 测试永远打不到真领星：两道闸，任一道单独都要能挡住。

① autouse 夹具把网关的 driver 工厂换成替身；
② 即便绕过了 ①，测试进程里实例化 LiveDriver 也直接失败。
"""
import conftest as ct
import pytest
from lingxing_double import LingxingDouble

from erp import gateway
from erp.drivers.live import LiveDriver


def test_gateway_uses_the_double(lingxing):
    assert isinstance(lingxing, LingxingDouble)
    assert gateway._driver() is lingxing


def test_live_driver_cannot_be_constructed_in_tests():
    with pytest.raises(RuntimeError, match="测试禁止实例化真领星 driver"):
        LiveDriver({"base_url": "https://lingxing.invalid"})


def test_real_driver_factory_fails_even_if_injection_is_bypassed():
    with pytest.raises(RuntimeError, match="测试禁止实例化真领星 driver"):
        ct.REAL_DRIVER_FACTORY()


def test_test_config_carries_no_real_lingxing_target(business_db):
    from shared import config as config_module
    e = config_module.erp()
    assert e["base_url"].endswith(".invalid") and e["proxy_host"].endswith(".invalid")
    assert e["app_id"] == "test-app-id" and e["app_secret"] == "test-app-secret"
