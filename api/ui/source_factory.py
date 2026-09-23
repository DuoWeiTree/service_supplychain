"""装配取数源。★ 这一层允许 import shared.ch_client —— dim/ 不允许
（tests/test_layering.py:55 的 NO_CONNECTIONS），所以注入点必须在 api/。"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from dim.ch_source import ChSource
from dim.fixture_source import FixtureSource
from dim.source import Source
from shared.ch_client import ch_client, ch_query, describe_failure
from shared.config import forecast

log = logging.getLogger("scm.api")

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def lazy_ch_query() -> Callable[[str], list[tuple]]:
    """★ 第一次真查询时才建连：import 时建连会让配置错在启动前一秒才炸，
    而那时启动日志已经刷过去了（CLAUDE.md 判据四的反面）。"""
    holder: list = []

    def run(sql: str) -> list[tuple]:
        if not holder:
            holder.append(ch_query(ch_client()))
        return holder[0](sql)

    return run


def make_source() -> Source:
    cfg = forecast()
    kind = cfg.get("source", "fixture")
    if kind == "fixture":
        log.info("op=forecast_source kind=fixture root=%s", FIXTURES)
        return FixtureSource(FIXTURES)
    if kind == "ch":
        log.info("op=forecast_source kind=ch ttl_s=%s threshold=%s lookback_days=%s "
                 "settle_minutes=%s min_rows=%s min_distinct_sid=%s purchase_staleness_days=%s",
                 cfg.get("cache_ttl_seconds"), cfg.get("drop_threshold"),
                 cfg.get("snapshot_lookback_days"), cfg.get("snapshot_settle_minutes"),
                 cfg.get("min_rows"), cfg.get("min_distinct_sid"),
                 cfg.get("purchase_staleness_days"))
        # ★ 控制器裁定（Task 5 acceptance criterion #1）：classify_failure 必须
        #   显式注入 describe_failure —— 漏掉它不会报错、测试也照样绿，但生产的
        #   每一次 CH 抖动都会报 kind="unknown"，彻底废掉「超时」与「连不上」
        #   的区分。这里显式写出参数名，不依赖位置参数的巧合对齐。
        return ChSource(
            lazy_ch_query(),
            drop_threshold=float(cfg.get("drop_threshold", 0.30)),
            cache_ttl_s=int(cfg.get("cache_ttl_seconds", 300)),
            min_rows=int(cfg.get("min_rows", 7934)),
            min_distinct_sid=int(cfg.get("min_distinct_sid", 21)),
            lookback_days=int(cfg.get("snapshot_lookback_days", 7)),
            settle_minutes=int(cfg.get("snapshot_settle_minutes", 30)),
            purchase_staleness_days=int(cfg.get("purchase_staleness_days", 3)),
            classify_failure=describe_failure,
        )
    # ★ 配置类错误往启动钩子放，别等第一个请求才炸。
    raise ValueError(f"[forecast] source 只认 'fixture' 与 'ch'，配的是 {kind!r}")
