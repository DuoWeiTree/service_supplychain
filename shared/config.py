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


#: ★ 设计取值。`source` 默认 "fixture" —— 默认值必须是离线跑得通的那个，
#:   否则任何一次漏配都会变成「测试去连生产 CH」。
#: ★ Fix round 1（团队负责人裁定 2026-09-23）：绝对地板从 `dim/ch_source.py`
#:   的硬编码模块常量搬到这里——常量焊在代码里会在业务量变化后腐烂成「再也
#:   不会触发」或「永远触发」，配置化才能跟着实际量级调。
#:   出处：`SQL_FBA_CAPTURE_DAYS`（`dim/ch_source.py`）对
#:   `jxd_raw.lingxing_inventory_fba_detail` 近 20 个采集日的实测（design §1.3
#:   E-1，2026-09-23）——行数 7,934~8,080，uniq(sid) 恒为 21。取区间下限。
_FORECAST_DEFAULTS = {
    "source": "fixture",
    "snapshot_lookback_days": 7,
    "snapshot_settle_minutes": 30,
    "cache_ttl_seconds": 300,
    "drop_threshold": 0.30,
    "min_rows": 7934,
    "min_distinct_sid": 21,
    "purchase_staleness_days": 3,
}


def forecast() -> dict:
    return {**_FORECAST_DEFAULTS, **_cfg().get("forecast", {})}


#: `pm_zestrade()` 要求 `[pm_zestrade]` 段齐全的四个字段。
_PM_ZESTRADE_FIELDS = ("token_url", "client_id", "client_secret", "pm_base")


def pm_zestrade() -> dict:
    """总公司 PM 产品系统（Keycloak client_credentials）接入参数。

    → ``{token_url, client_id, client_secret, pm_base}``。

    ★ 配置类错误往启动钩子放，别等第一个请求才炸（CLAUDE.md 铁律）——
    这里就是那个"启动钩子"：整段缺失、或四个字段缺任意一个，立即硬失败并
    点名段名/缺的字段，不许带着半份配置跑到第一次真正打 PM 才在 KeyError
    深处炸出来。★ **client_secret 绝不回显**——异常信息只点名"缺哪个字段"，
    不打印任何字段的值。"""
    section = _cfg().get("pm_zestrade")
    if section is None:
        raise RuntimeError(
            "config.toml 缺 [pm_zestrade] 段——PM/Keycloak 接入参数"
            f"（{', '.join(_PM_ZESTRADE_FIELDS)}）必须配好才能取数。"
            "真值找控制者，占位段见 config.example.toml")
    missing = [k for k in _PM_ZESTRADE_FIELDS if not section.get(k)]
    if missing:
        raise RuntimeError(
            f"config.toml [pm_zestrade] 缺字段 {missing}——"
            f"需要 {list(_PM_ZESTRADE_FIELDS)} 四项都非空")
    return {k: section[k] for k in _PM_ZESTRADE_FIELDS}


#: ★ 设计取值（spec §10.2）：timeout_s=30（V6）· stale_in_flight_minutes=10（V5）。
_ERP_DEFAULTS = {
    "base_url": "https://openapi.lingxing.com",
    "app_id": "",
    "app_secret": "",
    "timeout_s": 30,
    "stale_in_flight_minutes": 10,
    "confirm_salt": "dev-only-salt",
    "user_agent": "service_supplychain/0.1",
    # ★ 领星有 IP 白名单：生产与开发都必须经固定 IP 的 Squid 代理（HTTP CONNECT + Basic Auth）。
    #   出处：/home/fido/work/2026/tools/nodejs/commerce/lingxing_erp/INDEX.md:32-45
    "proxy_host": "",
    "proxy_port": 0,
    "proxy_user": "",
    "proxy_password": "",
}


def erp() -> dict:
    return {**_ERP_DEFAULTS, **_cfg().get("erp", {})}


#: ★ 生产必须 rbac（spec §12，D11）。dev = 开发身份，留痕可伪造。
_AUTH_DEFAULTS = {"mode": "dev"}


def auth() -> dict:
    return {**_AUTH_DEFAULTS, **_cfg().get("auth", {})}
