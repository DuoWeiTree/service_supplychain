"""门禁：测试配置从本机 `config.toml` 生成，但不许把本机的取数开关带进测试进程。

2026-09-28 实测的事故形状：`config.toml` 的 `[forecast] source` 改成 `"ch"` 之后，
23 条本该离线跑的测试当场开始打生产 ClickHouse。PB-D17 之后那个键整个删了：
产品只读真 CH，留着这个键（任何值）就拒绝启动。所以测试配置生成时**删掉**它 ——
否则本机还没删那一行时，每个起 app 的测试都会撞上启动闸；挡住离线测试去连 CH 的
是 conftest 的 autouse 注入（`forecast_source` 夹具），不再是这个键的值。

★ 前三条门禁自己**绝不读真实的 `config.toml`**：喂合成文本、断言产出。
  不然它的红绿又跟着操作员的机器变（记忆 `a-number-needs-an-answer-to-about-what`）。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import conftest as ct


def _parse(text: str) -> dict:
    return tomllib.loads(text)


def test_a_leftover_source_key_is_stripped_and_other_keys_kept():
    """这就是负责人机器上 09-28 之后那份配置的形状（带行尾注释）。"""
    src = """
[business_pg]
schema = "scm"

[forecast]                     # ★ 段头带行尾注释 —— 本机 config.toml 就是这个形状
source = "ch"                  # "fixture" = 旧说明
snapshot_lookback_days = 7
source_note_is_not_the_key = 1
"""
    out = _parse(ct._strip_forecast_source(src))
    assert "source" not in out["forecast"], "测试配置里还留着 [forecast] source —— 起 app 会撞 PB-D17 启动闸"
    assert out["forecast"]["snapshot_lookback_days"] == 7, "不该顺手改动别的键"
    assert out["forecast"]["source_note_is_not_the_key"] == 1, "只删 source 这个键，不删同前缀的键"


def test_source_key_in_another_section_is_left_alone():
    src = """
[forecast]
cache_ttl_seconds = 300

[other]
source = "keep"
"""
    out = _parse(ct._strip_forecast_source(src))
    assert out["other"]["source"] == "keep"
    assert out["forecast"]["cache_ttl_seconds"] == 300


def test_a_config_with_no_forecast_section_is_unchanged():
    src = '[business_pg]\nschema = "scm"\n'
    assert ct._strip_forecast_source(src) == src


def test_the_config_the_fixture_actually_wrote_has_no_source_key(business_db):
    """★ 证人必须在现场：读 `business_db` 落盘的那份（`JXD_SCM_CONFIG` 指着的文件），
    再经一次真正的读取路径。★ 证伪条件：本机 config.toml 还写着 `[forecast] source`
    时，把 `_strip_forecast_source` 从生成链路摘掉，这条红。"""
    import os

    from shared import config as config_module

    # ★ 只取要断言的那几段：整份配置带着库密码，断言失败时 pytest 会把它整个回显出来
    written = tomllib.loads(Path(os.environ["JXD_SCM_CONFIG"]).read_text("utf-8"))
    forecast, schema, freshness = (written.get("forecast", {}), written["business_pg"]["schema"],
                                   written["freshness"])
    assert "source" not in forecast
    assert schema == ct.TEST_SCHEMA, "schema 没换到测试库"
    assert freshness["scheduler_enabled"] is False
    assert freshness["startup_gate"] is False
    assert "source" not in config_module.forecast()


_REAL_SRC = """
[erp]
base_url = "https://openapi.lingxing.com"
app_id = "REAL-ID"
app_secret = "REAL-SECRET"
confirm_salt = "REAL-SALT"
proxy_host = "203.0.113.7"
timeout_s = 7

[auth]
mode = "rbac"
"""


def _assert_pinned(out: dict) -> None:
    for k, v in ct.ERP_TEST_PINS.items():
        assert out["erp"][k] == tomllib.loads(f"x = {v}")["x"], k
    assert out["auth"]["mode"] == "dev"


def test_erp_target_and_credentials_are_pinned_even_if_source_has_real_ones():
    """★ 测试配置从真实 config.toml 生成：真领星地址与凭证不许跟进测试进程（PB-D14 第三道闸）。"""
    out = _parse(ct._pin_erp_auth(_REAL_SRC))
    _assert_pinned(out)
    assert out["erp"]["timeout_s"] == 7, "不该改动别的键"


def test_erp_and_auth_pins_cover_missing_sections():
    _assert_pinned(_parse(ct._pin_erp_auth('[business_pg]\nschema = "scm"\n')))


def test_the_written_config_pins_erp_and_auth(business_db):
    import os

    _assert_pinned(tomllib.loads(Path(os.environ["JXD_SCM_CONFIG"]).read_text("utf-8")))
