"""门禁：测试套件自己用的取数源不许跟着本机 `config.toml` 走。

2026-09-28 实测的事故形状：负责人的界面要连真数据，于是 `config.toml` 的
`[forecast] source` 从缺省（=`fixture`）改成了 `"ch"`。测试配置是**从那份真实
配置生成**的（`conftest._switch_schema` 只换 schema），于是 23 条本该离线跑的测试
当场开始打生产 ClickHouse 并失败 —— 而它们一行都没改。

★ 这条门禁自己**绝不读真实的 `config.toml`**：喂合成文本、断言产出。
  不然它的红绿又跟着操作员的机器变，等于把同一个坑换了个地方
  （记忆 `a-number-needs-an-answer-to-about-what`：门禁数要可回放）。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import conftest as ct


def _parse(text: str) -> dict:
    return tomllib.loads(text)


def test_a_config_that_says_ch_comes_out_pinned_to_fixture():
    """① 键已在段里 → 必须被覆盖。这就是 09-28 那份配置的形状。"""
    src = """
[business_pg]
schema = "scm"

[forecast]
source = "ch"
snapshot_lookback_days = 7
"""
    out = _parse(ct._pin_forecast_source(src))
    assert out["forecast"]["source"] == "fixture", (
        "测试配置里的取数源仍是本机那个值 —— 离线测试会去打生产 ClickHouse")
    # ★ 数值旋钮是**故意**放过的（见 conftest 里 _FORECAST_TEST_PINS 上的注释）：
    #   它们只在 source=ch 下生效，而真正用它们的 live 测试断言的就该是本机的值。
    assert out["forecast"]["snapshot_lookback_days"] == 7, "不该顺手改动别的键"


def test_a_config_with_the_section_but_no_source_key_gets_one():
    """② 段在、缺键 → 段尾补上。老配置跟不上新键时的形状。"""
    src = """
[forecast]
cache_ttl_seconds = 300

[api]
port = 8091
"""
    out = _parse(ct._pin_forecast_source(src))
    assert out["forecast"]["source"] == "fixture"
    assert out["forecast"]["cache_ttl_seconds"] == 300
    assert out["api"]["port"] == 8091, "补键补到了别的段里"


def test_a_config_with_no_forecast_section_gets_the_whole_section():
    """③ 整段都不在 → 追加一整段。

    ★ 这正是 09-28 之前的形状：`[forecast]` 整段缺失、一切吃默认值，于是
    「测试用的是哪个源」在文件里**没有任何字面记录**。默认值恰好是 fixture
    救了它，但那是运气不是门禁 —— 默认值一改，全套测试的含义就变了。
    """
    src = """
[business_pg]
schema = "scm"
"""
    out = _parse(ct._pin_forecast_source(src))
    assert out["forecast"]["source"] == "fixture"
    assert out["business_pg"]["schema"] == "scm"


def test_the_config_the_fixture_actually_wrote_says_fixture(business_db):
    """★ 证人必须在现场。上面三条验的是那个函数；这一条验**跑测试时真正生效的
    配置**里写的是什么 —— 它读 `business_db` 落盘的那份（`JXD_SCM_CONFIG` 指着的
    那个文件），不是真实的 `config.toml`，也不是我自己再调一遍那个函数。

    ★ 自己调一遍函数再断言，等于隔着一层验上层行为：把
    `_pin_forecast_source(...)` 从生成链路里摘掉，那种写法照样全绿
    （记忆 `the-witness-must-have-been-present`）。

    ★ 交代这条的**证伪条件**：摘掉钉死动作之后，它读到的就是本机 `config.toml`
    的值 —— 所以只有在本机配置**不是** fixture 时这条才会红（2026-09-28 实测：
    配置为 `"ch"`，变异后这条红在 `+ ch`，另外三条仍绿）。若本机配置本来就是
    fixture，摘掉与不摘掉的行为确实没有差别，没得可抓也不必抓；**永久钉住这个
    行为的是上面那三条合成用例**，它们与本机配置无关。
    """
    import os

    from shared import config as config_module

    written = tomllib.loads(Path(os.environ["JXD_SCM_CONFIG"]).read_text("utf-8"))
    assert written["forecast"]["source"] == "fixture", (
        "生成链路没有钉死取数源 —— 离线测试会去打生产 ClickHouse")
    assert written["business_pg"]["schema"] == ct.TEST_SCHEMA, "schema 没换到测试库"
    assert written["freshness"]["scheduler_enabled"] is False
    assert written["freshness"]["startup_gate"] is False

    # ★ 再经一次真正的读取路径：代码读到的也必须是 fixture，而不只是文件里写着。
    assert config_module.forecast()["source"] == "fixture"


_LIVE_SRC = """
[erp]
mode = "live"
base_url = "https://x.invalid"

[auth]
mode = "rbac"
"""


def test_erp_and_auth_are_pinned_to_fake_and_dev_even_if_source_says_live():
    """★ 测试配置从真实 config.toml 生成：live 漏进来 = 测试带真凭证打领星（含不可逆建单）。"""
    out = _parse(ct._pin_erp_auth(_LIVE_SRC))
    assert out["erp"]["mode"] == "fake" and out["auth"]["mode"] == "dev"
    assert out["erp"]["base_url"] == "https://x.invalid", "不该改动别的键"


def test_erp_and_auth_pins_cover_missing_sections():
    out = _parse(ct._pin_erp_auth('[business_pg]\nschema = "scm"\n'))
    assert out["erp"]["mode"] == "fake" and out["auth"]["mode"] == "dev"


def test_the_written_config_pins_erp_and_auth(business_db):
    import os

    written = tomllib.loads(Path(os.environ["JXD_SCM_CONFIG"]).read_text("utf-8"))
    assert written["erp"]["mode"] == "fake" and written["auth"]["mode"] == "dev"
