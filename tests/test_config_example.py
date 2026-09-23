"""`config.example.toml` 必须是给人抄的能用模板，不是摆设。

★ 07:22-08:26 那次事故的同一种形状换了个地方：这次不是「文档说在根目录、
  实际不在」，是「模板本身 `tomllib` 都解析不了」——`cp config.example.toml
  config.toml` 是 README 第二步，模板本身炸了，接手的人第一步就卡死。
  Task 6 review 09-22 抓到：`[freshness]` 段被写了两次（新的一段带 OQ-8/E-4
  注释、旧的一段是 Task 6 编辑时漏看了文件尾巴留下的），`coverage_drop_threshold`
  / `startup_gate` 重复声明，`tomllib.load` 直接 `TOMLDecodeError`。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from shared.config import _FORECAST_DEFAULTS, _FRESHNESS_DEFAULTS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config.example.toml"


def test_config_example_is_valid_toml():
    """★ 这条本身就是回归门禁：解析炸了，下面的键集合断言一个都跑不到。"""
    with open(EXAMPLE, "rb") as f:
        tomllib.load(f)


def test_config_example_freshness_keys_match_shared_config_defaults():
    """★ 集合相等，两个方向都要查——`shared/config.py` 多一个默认键而模板没写，
    等于模板在骗人「这就是全部可配项」；模板多一个键而 `freshness()` 不认得，
    等于抄它的人以为自己配了什么，其实读不到。"""
    with open(EXAMPLE, "rb") as f:
        cfg = tomllib.load(f)
    got = set(cfg["freshness"])
    want = set(_FRESHNESS_DEFAULTS)
    assert got == want, f"模板 [freshness] 的键与 shared.config 默认值不一致：" \
        f"模板独有 {got - want}，默认值独有 {want - got}"


def test_config_example_forecast_keys_match_shared_config_defaults():
    """★ 同上一条，换成 [forecast]（ChSource 设计 2026-09-23 Task 1 新增）。"""
    with open(EXAMPLE, "rb") as f:
        cfg = tomllib.load(f)
    got = set(cfg["forecast"])
    want = set(_FORECAST_DEFAULTS)
    assert got == want, f"模板 [forecast] 的键与 shared.config 默认值不一致：" \
        f"模板独有 {got - want}，默认值独有 {want - got}"


def test_config_example_has_the_three_connection_sections():
    with open(EXAMPLE, "rb") as f:
        cfg = tomllib.load(f)
    assert {"business_pg", "clickhouse", "api"} <= set(cfg)


#: 非测试源码。★ `shared/config.py` 自己**不算**引用 —— 它是声明的那一侧，
#: 「声明了」和「有人读」正是这条门禁要分开的两件事（`sales_months_max` 当年
#: 三处声明、零处读取，`test_config_example_*_keys_match_*` 两个方向都绿：
#: 它比的是模板的键与默认值字典的键，而两者都含着那个死键）。
_CODE_DIRS = ("api", "dim", "erp", "forecast", "jobs", "rules", "shared")
_NOT_A_REFERENCE = {ROOT / "shared" / "config.py"}


def _non_test_sources() -> list[Path]:
    out = [p for d in _CODE_DIRS for p in sorted((ROOT / d).rglob("*.py"))
           if p not in _NOT_A_REFERENCE]
    assert out, "一个源文件都没扫到 —— 这条规则是空转的"
    return out


def _unreferenced(keys) -> list[str]:
    texts = [p.read_text("utf-8") for p in _non_test_sources()]
    return sorted(k for k in keys
                  if not any(f'"{k}"' in t or f"'{k}'" in t for t in texts))


def test_every_declared_forecast_key_is_read_by_non_test_code():
    """★★ 终审 I-4：**一个不生效的旋钮比没有旋钮更坏** —— 它在 README 里像个
    承诺，改了却毫无效果，而没有任何东西会报错。

    `sales_months_max` 在 `shared/config.py` / `config.example.toml` / `README.md`
    三处声明、`grep` 全树零处读取，而上面那两条「模板键 == 默认值键」的门禁
    一个都抓不到：它们比的是两个**都含着那个死键**的集合。判据得换成「有没有
    人真的读它」。
    ★ 这也是 Task 1 复核提过的同一个缺陷（`snapshot_lookback_days` /
      `snapshot_settle_minutes` 当时被接上了，这一个没有）—— 所以这次补门禁，
      不只是修那一个键。
    """
    dead = _unreferenced(_FORECAST_DEFAULTS)
    assert not dead, (
        f"[forecast] 这些键声明了但非测试代码里没人读：{dead}。"
        "要么接上（在 api/ui/source_factory.py 里传给 ChSource），要么从"
        " shared/config.py、config.example.toml、README.md 三处一起删掉 ——"
        "留着就是在 README 里许一个不会兑现的承诺")


def test_every_declared_freshness_key_is_read_by_non_test_code():
    """★ 同一条判据换成 `[freshness]` —— 死旋钮不是 `[forecast]` 的专属毛病，
    一条只盯一个段的门禁下次会在另一个段上错过它。"""
    dead = _unreferenced(_FRESHNESS_DEFAULTS)
    assert not dead, f"[freshness] 这些键声明了但非测试代码里没人读：{dead}"
