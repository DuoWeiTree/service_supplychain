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
