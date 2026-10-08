import pytest

from shared import config as config_module


def _cfg(monkeypatch, erp: dict, auth: dict):
    monkeypatch.setattr(config_module, "erp", lambda: {**config_module._ERP_DEFAULTS, **erp})
    monkeypatch.setattr(config_module, "auth", lambda: {**config_module._AUTH_DEFAULTS, **auth})


def test_defaults_are_fake_and_dev():
    assert config_module._ERP_DEFAULTS["mode"] == "fake"
    assert config_module._AUTH_DEFAULTS["mode"] == "dev"


def test_live_with_dev_auth_refuses_to_start(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {"mode": "live", "app_id": "a", "app_secret": "s", "confirm_salt": "x"},
         {"mode": "dev"})
    with pytest.raises(RuntimeError, match="不可逆操作必须有主"):
        check_outbound_config()


def test_live_without_credentials_refuses_to_start(monkeypatch):
    """★ 凭证检查排在 RBAC 未实现闸之前：M2 去掉那道闸之后，这条仍必须先点名缺的凭证。"""
    from api import check_outbound_config
    _cfg(monkeypatch, {"mode": "live"}, {"mode": "rbac"})
    with pytest.raises(RuntimeError, match="app_id"):
        check_outbound_config()


def test_unknown_mode_refuses_to_start(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {"mode": "prod"}, {"mode": "dev"})
    with pytest.raises(RuntimeError, match="fake|live"):
        check_outbound_config()


def test_fake_dev_starts(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {}, {})
    check_outbound_config()


def test_rbac_refuses_to_start_until_m2(monkeypatch):
    """★ 终审 #4：rbac 只是一个配置串、鉴权链路没实现 —— 放行就会在 live 下接受伪造的 x-actor。"""
    from api import check_outbound_config
    _cfg(monkeypatch, {}, {"mode": "rbac"})
    with pytest.raises(RuntimeError, match="RBAC not implemented until M2"):
        check_outbound_config()


def test_live_is_impossible_in_m1_even_fully_configured(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {"mode": "live", "app_id": "a", "app_secret": "s", "proxy_host": "p", "proxy_port": 1,
                       "confirm_salt": "x"}, {"mode": "rbac"})
    with pytest.raises(RuntimeError, match="RBAC not implemented until M2"):
        check_outbound_config()
