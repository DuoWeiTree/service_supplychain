"""配置读取。路径可由 JXD_SCM_CONFIG 覆盖，便于测试指向 scm_test。"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

_CFG: dict | None = None


def _cfg() -> dict:
    global _CFG
    if _CFG is None:
        default = Path(__file__).resolve().parents[1] / "config.toml"
        path = Path(os.environ.get("JXD_SCM_CONFIG", default))
        with open(path, "rb") as f:
            _CFG = tomllib.load(f)
    return _CFG


def reset_cache() -> None:
    """测试用：改了 JXD_SCM_CONFIG 之后强制重读。"""
    global _CFG
    _CFG = None


def business_pg() -> dict:
    """业务库：本仓全部业务数据，独占 schema scm（E-1）。"""
    return dict(_cfg()["business_pg"])


def clickhouse() -> dict:
    """★ 只读。绝不写任何表。"""
    return dict(_cfg()["clickhouse"])


def api() -> dict:
    return dict(_cfg().get("api", {}))


#: ★ 设计取值 · 未实测（设计 §10 OQ-6/7/10）。config.toml 缺键时用它们。
#: ★ 终审 M-6 订正：原注释写的是「缺键与『明确写了这个值』在日志里要分得开
#:   —— 见 jobs/scheduler.py 的启动行」，而那一行打的是
#:   `op=scheduler outcome=built at=06:30 tz=Asia/Shanghai`，两种来源长得一模
#:   一样。注释引用的守卫并不存在，而这种注释比没有注释更坏：下一个人会据此
#:   少查一次。要区分两者得在 `freshness()` 里记下哪些键是回落来的再打进那行
#:   日志 —— 本轮没做，所以这里如实写明它没做。
#: ★ 当前的兜底判据只有一条：`tests/test_config_example.py` 钉住
#:   `config.example.toml` 的 `[freshness]` 六键与本表双向相等，所以「模板里
#:   漏写一个键」会红；「部署现场的 config.toml 漏写」仍然只会安静回落。
_FRESHNESS_DEFAULTS = {
    "max_age_hours": 24,
    "refresh_at": "06:30",              # ★ 07:481「在 CH 采集窗口之后」，具体时刻未实测
    "refresh_timezone": "Asia/Shanghai",
    "scheduler_enabled": True,
    "coverage_drop_threshold": 0.30,    # OQ-6 裁定 09-22
    "startup_gate": True,
}


def freshness() -> dict:
    return {**_FRESHNESS_DEFAULTS, **_cfg().get("freshness", {})}
