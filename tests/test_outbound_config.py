"""启动闸（PB-D14 / PB-D15）：下单一律打真领星 —— 凭证、代理、盐缺一个都不许起；rbac 在 M2 前拒绝。"""
import pytest

from shared import config as config_module

_FULL = {"app_id": "a", "app_secret": "SECRET-s", "proxy_host": "p", "proxy_port": 1, "confirm_salt": "SALT-x",
         "proxy_password": "PW-p"}


def _cfg(monkeypatch, erp: dict, auth: dict):
    monkeypatch.setattr(config_module, "erp", lambda: {**config_module._ERP_DEFAULTS, **erp})
    monkeypatch.setattr(config_module, "auth", lambda: {**config_module._AUTH_DEFAULTS, **auth})


def test_no_erp_mode_and_auth_defaults_to_dev():
    assert "mode" not in config_module._ERP_DEFAULTS
    assert config_module._AUTH_DEFAULTS["mode"] == "dev"


def test_full_config_with_dev_starts(monkeypatch, scm_log):
    """PB-D15：推翻 spec §12.2「live + dev 拒绝启动」—— 齐全 + dev 起得来，且首屏点名打的是谁。"""
    from api import check_outbound_config
    _cfg(monkeypatch, _FULL, {"mode": "dev"})
    check_outbound_config()
    assert "startup erp host=https://openapi.lingxing.com proxy=p:1 auth mode=dev" in scm_log.text
    assert not [v for v in ("SECRET-s", "SALT-x", "PW-p") if v in scm_log.text], "启动日志不许回显凭证"


@pytest.mark.parametrize("key", ["app_id", "app_secret", "proxy_host"])
def test_missing_credential_or_proxy_refuses_to_start(monkeypatch, key):
    from api import check_outbound_config
    _cfg(monkeypatch, {**_FULL, key: ""}, {"mode": "dev"})
    with pytest.raises(RuntimeError, match=key):
        check_outbound_config()


@pytest.mark.parametrize("salt", ["", config_module._ERP_DEFAULTS["confirm_salt"]])
def test_empty_or_default_salt_refuses_to_start(monkeypatch, salt):
    from api import check_outbound_config
    _cfg(monkeypatch, {**_FULL, "confirm_salt": salt}, {"mode": "dev"})
    with pytest.raises(RuntimeError, match="confirm_salt"):
        check_outbound_config()


def test_unconfigured_refuses_to_start(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {}, {})
    with pytest.raises(RuntimeError, match=r"app_id.*app_secret.*proxy_host.*confirm_salt"):
        check_outbound_config()


def test_rbac_refuses_to_start_until_m2(monkeypatch):
    """★ 终审 #4：rbac 只是一个配置串、鉴权链路没实现 —— 放行就会照收伪造的 x-actor。"""
    from api import check_outbound_config
    _cfg(monkeypatch, _FULL, {"mode": "rbac"})
    with pytest.raises(RuntimeError, match="RBAC not implemented until M2"):
        check_outbound_config()


def test_missing_credentials_are_named_before_rbac(monkeypatch):
    """★ 凭证检查排在 RBAC 未实现闸之前：M2 去掉那道闸之后，这条仍必须先点名缺的凭证。"""
    from api import check_outbound_config
    _cfg(monkeypatch, {}, {"mode": "rbac"})
    with pytest.raises(RuntimeError, match="app_id"):
        check_outbound_config()


def test_unknown_auth_mode_refuses_to_start(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, _FULL, {"mode": "prod"})
    with pytest.raises(RuntimeError, match="dev|rbac"):
        check_outbound_config()


def test_leftover_erp_mode_is_loud(monkeypatch, scm_log):
    """旧 config.toml 里残留的 [erp] mode 不再控制任何东西 —— 不出声就会有人以为下单不打领星。"""
    from api import check_outbound_config
    _cfg(monkeypatch, {**_FULL, "mode": "fake"}, {"mode": "dev"})
    check_outbound_config()
    assert "[erp] mode='fake' 已不再生效" in scm_log.text
