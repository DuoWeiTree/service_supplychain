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
    而那时启动日志已经刷过去了（CLAUDE.md 判据四的反面）。

    ★ 终审 C-1：这个闭包持有的客户端**不许跨请求/跨线程活着**。它是
    `make_source()` 一次调用的私有物，而 `make_source()` 现在每请求跑一次
    （`source_dep`）—— 一个进程级单例会把四个并发请求里的三个变成
    `ProgrammingError: Attempt to execute concurrent queries within the same
    session`，再被翻成 503「预测取数源连不上」去冤枉网络（实测 4 线程 3 败）。"""
    holder: list = []

    def run(sql: str, parameters: dict | None = None) -> list[tuple]:
        # ★ `parameters` 必须一路透传（终审 I-5）—— 在这一层吃掉它，SQL 里的
        #   `{name:Type}` 占位符就会以「未绑定参数」在 CH 侧炸，而那个错会被
        #   分类成 kind="other" → 503「取数源连不上」。
        if not holder:
            holder.append(ch_query(ch_client()))
        return holder[0](sql, parameters)

    return run


#: `[forecast] source` 只认这两个值。★ 认不出的值硬失败，不许回落到 fixture ——
#: 「配错了」和「就是想用 fixture」长得一样，而前者在生产上是静默降级。
KINDS = ("fixture", "ch")


def _kind(cfg: dict) -> str:
    kind = cfg.get("source", "fixture")
    if kind not in KINDS:
        # ★ 配置类错误往启动钩子放，别等第一个请求才炸（`log_source_config`）。
        raise ValueError(f"[forecast] source 只认 'fixture' 与 'ch'，配的是 {kind!r}")
    return kind


def log_source_config() -> None:
    """启动钩子里打一次取数源的生效配置，并就地校验 `source` 的取值。

    ★ 为什么不在 `make_source()` 里打：装配现在是**每请求**一次（`source_dep`），
      每请求打一行会把访问日志淹掉，而这行的用处恰恰是「启动日志第一屏可见」。
    ★ 为什么校验也在这里：配错 `source` 必须在启动时炸，不是等第一个请求。
    """
    cfg = forecast()
    kind = _kind(cfg)
    if kind == "fixture":
        log.info("op=forecast_source kind=fixture root=%s", FIXTURES)
        return
    log.info("op=forecast_source kind=ch ttl_s=%s threshold=%s lookback_days=%s "
             "settle_minutes=%s min_rows=%s min_distinct_sid=%s purchase_staleness_days=%s "
             "lifecycle=per_request",
             cfg.get("cache_ttl_seconds"), cfg.get("drop_threshold"),
             cfg.get("snapshot_lookback_days"), cfg.get("snapshot_settle_minutes"),
             cfg.get("min_rows"), cfg.get("min_distinct_sid"),
             cfg.get("purchase_staleness_days"))


def make_source() -> Source:
    """★ 每次调用都造一个**全新**的源。调用方是 `source_dep`（每请求一次）。

    ★ 终审 C-1/C-2/C-3 的同一个根因：`ChSource` 身上挂着可变的每次调用状态
      （`_as_of` / `_purchase_as_of` / 两份缓存 / `stats()` / `history_window()`）
      和一个 CH 客户端，而 `grid()`/`claim()` 是 plain def 端点、由 Starlette
      放进线程池并发跑。进程级单例于是同时造出三种故障：并发查询被驱动拒绝
      （C-1）、一个请求读到另一个请求的口径标记（C-2）、采购陈旧闸在第一次
      成功之后再也不触发（C-3）。**没有可变状态可以跨请求共享** —— 修的是
      生命周期，不是三个症状。
    ★ 代价实测（2026-09-23，见 final-fix-report）：多付一次建客户端 ≈7.9ms
      + 四条查询 ≈37.8ms/次 grid。没有因此引入跨请求缓存：那会把刚拆掉的
      共享可变状态原样装回来。
    ★ fixture 档仍然不花钱：`FixtureSource` 只存一个 Path，不建任何连接。
    """
    cfg = forecast()
    kind = _kind(cfg)
    if kind == "fixture":
        return FixtureSource(FIXTURES)
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


def source_dep() -> Source:
    """FastAPI 依赖：**每个请求一个取数源**。★ 换源仍然只改 config.toml 的
    `[forecast] source` —— 变的是生命周期，不是装配口子。

    测试要换源走 `app.dependency_overrides[source_dep]`，不再有模块级单例可以
    monkeypatch —— 那个单例正是 C-1/C-2/C-3 的根因。"""
    return make_source()
