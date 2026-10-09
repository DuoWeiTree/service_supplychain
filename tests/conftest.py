"""测试夹具。照邻居 service_social/tests/conftest.py 的形状。

★ 测试 schema 是 scm_test。禁止 scm（本服务生产）与 inv（同库另一个在跑的系统）——
  往它们任何一个里写一行都是事故，而这类事故没有任何回声。
"""
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fixture_source import FIXTURES, FixtureSource
from lingxing_double import LingxingDouble

from api.ui import source_factory
from erp import auth as erp_auth
from erp import gateway
from erp.drivers.live import LiveDriver
from erp.transport import host_safe_in_tests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_TOML = ROOT / "config.toml"

TEST_SCHEMA = "scm_test"
FORBIDDEN_SCHEMAS = {"scm", "inv"}

#: 每个测试之间要清空的表。★ plan_line_transition 不在其中 ——
#: 它是白名单（迁移灌的数据），清掉它状态机就没了。
DATA_TABLES = (
    "po_item_obs", "po_snapshot", "po_attention", "erp_call",
    "po_event", "po_source_map", "po_line", "po", "supplier",
    "dim_refresh_run",
    "plan_line_carry", "plan_line_event", "plan_submit_skip", "plan_line", "plan_rev",
    "plan_demand_cell", "plan_purchase_cell", "msku_claim", "plan",
    # ★ Task 1（sku-image 计划）新增：sku_category 引用 sku_catalog，必须排在它前面
    #   清空（TRUNCATE ... CASCADE 已经处理依赖顺序，这里的列表顺序不影响执行，
    #   只是保持「先叶子后根」的可读性）。category_refresh 无外键，随手带上。
    "sku_category", "category_refresh",
    "msku_bridge", "sku_catalog", "warehouse", "seller", "actor",
)


def assert_not_production_schema():
    from shared.pg_client import business_schema
    s = business_schema()
    assert s not in FORBIDDEN_SCHEMAS, (
        f"测试正指向 {s!r}。多半是某个夹具漏了对 business_db 的依赖，"
        "shared.config 读的是未切换的 config.toml。")


def _switch_schema(text: str) -> str:
    out, in_pg = [], False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("["):
            in_pg = s.startswith("[business_pg]")
        if in_pg and s.startswith("schema"):
            out.append(f'schema = "{TEST_SCHEMA}"\n')
            continue
        out.append(line)
    return "".join(out)


#: ★ 两个键都要在测试里默认关掉——都会让 `create_app()` 在 `_lifespan` 里碰
#: 外部系统：`scheduler_enabled` 起一个真 APScheduler（task-6-brief Step 7），
#: `startup_gate` 在镜像陈旧时抢 advisory lock、触发一次真刷新去打真 CH
#: （task-7 fix round 1 —— 没有这一条，任何一个带 `with TestClient(...)` 对着
#: 空表/陈旧镜像跑 lifespan 的测试都会在默认值下真的去连 ClickHouse）。
_FRESHNESS_TEST_PINS = ("scheduler_enabled", "startup_gate")


def _pin_section_keys(text: str, section: str, pins: dict[str, str]) -> str:
    """把 `section` 段里 `pins` 的每个键钉成给定字面量。

    三种情况都要覆盖（`config.toml` 三种都真实出现过）：① 键已在段里 → 覆盖；
    ② 段在但缺键 → 段尾补；③ **整段都不在** → 追加一整段。
    ★ 第 ③ 种就是 2026-09-28 那次的形状：`[forecast]` 整段缺失、一切吃默认值，
    于是「测试用的是什么源」这件事在文件里**根本没有字面记录**。
    """
    out, inside, seen = [], False, set()

    def flush():
        out.extend(f"{k} = {v}\n" for k, v in pins.items() if k not in seen)

    saw_section = False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("["):
            if inside:
                flush()
            inside = s.startswith(section)
            saw_section = saw_section or inside
            seen = set()
        matched = next((k for k in pins if s.startswith(k)), None)
        if inside and matched:
            out.append(f"{matched} = {pins[matched]}\n")
            seen.add(matched)
            continue
        out.append(line)
    if inside:
        flush()
    if not saw_section:
        out.append(f"\n{section}\n")
        out.extend(f"{k} = {v}\n" for k, v in pins.items())
    return "".join(out)


def _strip_forecast_source(text: str) -> str:
    """删掉 `[forecast]` 段里的 `source` 键（PB-D17：留着它任何值都拒绝启动）。

    ★ 测试配置是从本机 `config.toml` 生成的；本机那一行没删之前，不删它每个起 app
      的测试都会撞启动闸。离线测试不连 CH 靠的是 `forecast_source` autouse 夹具，
      不是这个键 —— 2026-09-28 本机写着 `"ch"` 时 23 条离线测试打了生产 CH。
    """
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("["):
            inside = s.split("#", 1)[0].strip() == "[forecast]"   # 本机段头带行尾注释
        if inside and s.split("=", 1)[0].strip() == "source" and "=" in s:
            continue
        out.append(line)
    return "".join(out)


#: ★ business_db 夹具生成的测试配置里，领星目标与凭证钉成不可达的测试值 ——
#: 只作用于经该夹具读配置的路径；不经它的（如未请求 business_db 时读到的本机 config.toml）
#: 不受此约束。真正挡住真调用的是 transport 层的测试闸（非本机 / 非 .invalid 主机直接抛）
#: 与下面的替身注入。盐非默认值：启动闸要它。
ERP_TEST_PINS = {
    "base_url": '"https://lingxing.invalid"',
    "app_id": '"test-app-id"',
    "app_secret": '"test-app-secret"',
    "confirm_salt": '"test-confirm-salt"',
    "proxy_host": '"proxy.invalid"',
    "proxy_port": "3128",
    "proxy_user": '"test"',
    "proxy_password": '"test"',
}


def _pin_erp_auth(text: str) -> str:
    text = _pin_section_keys(text, "[erp]", ERP_TEST_PINS)
    return _pin_section_keys(text, "[auth]", {"mode": '"dev"'})


#: ★ 测试永远打不到真领星（PB-D14）。conftest 一被导入就装上，早于任何测试模块与夹具：
#: ① `lingxing` autouse 夹具把网关的 driver 工厂换成替身；
#: ② 实例化 LiveDriver、取 access_token 只放行指向本机（loopback）的配置 —— 只为
#:    tests/test_erp_live_driver.py 对本机桩做端到端；
#: ③ 兜底在 erp/transport.post_json：测试进程里非本机 / 非 .invalid 主机直接抛。
LIVE_DRIVER_FORBIDDEN = "测试禁止实例化真领星 driver"
TOKEN_FORBIDDEN = "测试禁止向真领星取 access_token"
REAL_DRIVER_FACTORY = gateway._driver
_REAL_LIVE_INIT = LiveDriver.__init__
_REAL_TOKEN = erp_auth.token


def _local_only(cfg) -> bool:
    if not isinstance(cfg, dict):
        return False
    host = urlsplit(str(cfg.get("base_url") or "")).hostname
    proxy = cfg.get("proxy_host") or ""
    return _is_loopback(host) and (not proxy or _is_loopback(proxy))


def _is_loopback(host) -> bool:
    # ★ 比 transport 闸更窄：.invalid 在这里不放行 —— 真 driver 只许对着本机桩
    return host_safe_in_tests(host) and not str(host).rstrip(".").lower().endswith(".invalid")


def _guarded_live_init(self, cfg, *args, **kwargs):
    if not _local_only(cfg):
        raise RuntimeError(LIVE_DRIVER_FORBIDDEN)
    _REAL_LIVE_INIT(self, cfg, *args, **kwargs)


def _guarded_token(cfg, *args, **kwargs):
    if not _local_only(cfg):
        raise RuntimeError(TOKEN_FORBIDDEN)
    return _REAL_TOKEN(cfg, *args, **kwargs)


LiveDriver.__init__ = _guarded_live_init
erp_auth.token = _guarded_token
DOUBLE = LingxingDouble()


@pytest.fixture(autouse=True)
def lingxing(monkeypatch):
    """领星替身。每个测试一份干净的脚本与单据。"""
    DOUBLE.reset()
    monkeypatch.setattr(gateway, "_driver", lambda: DOUBLE)
    yield DOUBLE
    DOUBLE.reset()


#: ★ 测试永远不碰真 CH（PB-D17 之后产品只造 ChSource）。换的是 `make_source` 这个
#: 模块属性：`source_dep`（每请求）按名字调它，所以 `client` 夹具的 app、各测试自己
#: `create_app()` 出来的 app 一样被覆盖。要验生产装配的测试显式调 `REAL_MAKE_SOURCE`
#: 并自己把建连那一步换成桩；live 测试直接构造 `ChSource`，不经这里。
REAL_MAKE_SOURCE = source_factory.make_source


@pytest.fixture(autouse=True)
def forecast_source(monkeypatch):
    monkeypatch.setattr(source_factory, "make_source", lambda: FixtureSource(FIXTURES))


def _disable_scheduler(text: str) -> str:
    """★ 测试固定 `[freshness]` 里 `_FRESHNESS_TEST_PINS` 这几个键为 false ——
    不这样，每个 `with TestClient(create_app())` 都会在 `_lifespan` 里碰外部
    系统（起真调度器 / 抢锁打真 CH）。已有这一行就覆盖成 false，没有就在
    `[freshness]` 段尾追加一行 —— 两种情况 `config.toml` 都可能出现
    （新增键 vs 老配置未跟上）。

    ★ 只想验证「真的会触发一次刷新」的测试（tests/test_api_startup_check.py）
    必须显式 `monkeypatch` 把 `startup_gate` 改回 true —— 默认值保护的是
    其余全部测试，不是这几个专门测它的用例。
    """
    out, in_freshness, seen = [], False, set()
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("["):
            if in_freshness:
                for key in _FRESHNESS_TEST_PINS:
                    if key not in seen:
                        out.append(f"{key} = false\n")
            in_freshness = s.startswith("[freshness]")
            seen = set()
        matched = next((k for k in _FRESHNESS_TEST_PINS if s.startswith(k)), None)
        if in_freshness and matched:
            out.append(f"{matched} = false\n")
            seen.add(matched)
            continue
        out.append(line)
    if in_freshness:
        for key in _FRESHNESS_TEST_PINS:
            if key not in seen:
                out.append(f"{key} = false\n")
    return "".join(out)


@pytest.fixture(scope="session")
def business_db(tmp_path_factory):
    from shared import config as config_module

    cfg = tmp_path_factory.mktemp("jxd_scm") / "config.toml"
    text = _pin_erp_auth(_strip_forecast_source(_disable_scheduler(_switch_schema(CONFIG_TOML.read_text("utf-8")))))
    cfg.write_text(text, encoding="utf-8")
    prev = os.environ.get("JXD_SCM_CONFIG")
    os.environ["JXD_SCM_CONFIG"] = str(cfg)
    config_module.reset_cache()

    assert_not_production_schema()
    from migrations.pg.apply import apply
    apply(TEST_SCHEMA)
    try:
        yield TEST_SCHEMA
    finally:
        from shared.pg_client import pg_conn
        with pg_conn(autocommit=True) as c, c.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        if prev is None:
            os.environ.pop("JXD_SCM_CONFIG", None)
        else:
            os.environ["JXD_SCM_CONFIG"] = prev
        config_module.reset_cache()


@pytest.fixture
def wipe(business_db):
    """★ TRUNCATE 不触发 BEFORE DELETE 触发器，所以只追加表也清得掉。

    ★ DATA_TABLES 是阶段 A 终态的全表单，各表随 Task 3~5 的迁移逐个落地 ——
      过滤到「当前 schema 里已存在」的那些，否则在只应用了部分迁移的阶段，
      TRUNCATE 会先炸在还没创建的表上，而不是炸在测试真正要验的地方。
    """
    assert_not_production_schema()
    from migrations.pg.apply import applied_versions
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        existing = {r[0] for r in cur.fetchall()}
        # ★ 过滤本身会哑掉「表名拼错/改名」这类真事故：一旦阶段 A 最后一个迁移
        #   （004，落地全部表）已应用，DATA_TABLES 就该与 existing 相等 ——
        #   不相等就点名硬失败，不许再靠「还没到它的迁移」这个借口继续漏下去。
        if "004_plan_overall_state.sql" in applied_versions(business_db):
            missing = set(DATA_TABLES) - existing
            assert not missing, f"DATA_TABLES 里这些表不存在：{sorted(missing)}"
        tables = [t for t in DATA_TABLES if t in existing]
        if tables:
            cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    return business_db


@pytest.fixture
def seed(wipe):
    """维度与操作人的最小一组。

    ★ 刻意包含两种「长得像 0 其实不是」的形态：
      · actor `bob` 停用 —— 与「不存在」分得开
      · seller `WM-1` has_fba=false —— 它的 FBA 在仓是「不适用」，不是 0（02 §3.1a）

    ★ Task 10 补：`warehouse` 是 `v_mirror_freshness` 的四个镜像之一，
      之前这里没种它，第一个挂 `require_fresh_mirrors` 的路由（`/v1/plans`）
      上线前没人发现 —— `/v1/readiness` 只报告新鲜度、不拒绝服务，盖不住这个缺口。
      不种它，任何挂了这个依赖的端点在 `seed` 下永远 503。
    """
    from shared.pg_client import pg_conn
    ns = SimpleNamespace(
        actor="alice", actor_inactive="bob",
        seller_a="11072", seller_b="11094", seller_nofba="90001",
        sku_a="DCC1800264G1", sku_b="A4P-TOY-002", sku_e="A4P-TOY-003",
        msku_a=("MSKU-A", "11072"), msku_b=("MSKU-B", "11072"),
        msku_c=("MSKU-C", "11094"),          # ★ 与 msku_a 同货号、不同店 → 在途无归属那一形态
        msku_d=("MSKU-D", "11072"),          # ★ sku_b 在有 FBA 的店 → closing 有真数那一形态
        msku_nofba=("MSKU-W", "90001"),      # ★ 无 FBA → 不适用那一形态
        # ★ 09-23 真实缺陷：sku_e 独占，不与任何其它 msku 共享 (sku, sid) ——
        #   否则同组里另一个 msku 的真实在仓数字会把这个「没取到」悄悄盖住。
        #   seller_a 有 FBA，但 tests/fixtures/fba_onhand.csv 里刻意不给 MSKU-E 一行，
        #   模拟「阶段 A 冻结 fixture 里没有这个 msku」→ 未知在仓那一形态。
        msku_e=("MSKU-E", "11072"),
        wid=1,
    )
    with pg_conn() as c, c.cursor() as cur:
        cur.executemany("INSERT INTO actor (actor_id, name, active) VALUES (%s, %s, %s)",
                        [(ns.actor, "爱丽丝", True), (ns.actor_inactive, "鲍勃", False)])
        cur.executemany(
            "INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
            " VALUES (%s, %s, %s, %s, %s, now())",
            [(ns.seller_a, "A4Pet-US", "US", True, "amazon"),
             (ns.seller_b, "A4Pet-BS-UK", "UK", True, "amazon"),
             (ns.seller_nofba, "A4Pet-WM", "US", False, "walmart")])
        cur.executemany("INSERT INTO sku_catalog (sku, name, refreshed_at) VALUES (%s, %s, now())",
                        [(ns.sku_a, "猫爬架"), (ns.sku_b, "逗猫棒"), (ns.sku_e, "猫抓板")])
        cur.executemany("INSERT INTO msku_bridge VALUES (%s, %s, %s, now())",
                        [(*ns.msku_a, ns.sku_a), (*ns.msku_b, ns.sku_a),
                         (*ns.msku_c, ns.sku_a), (*ns.msku_d, ns.sku_b),
                         (*ns.msku_nofba, ns.sku_b), (*ns.msku_e, ns.sku_e)])
        cur.execute(
            "INSERT INTO warehouse (wid, name, kind, market, refreshed_at)"
            " VALUES (%s, %s, %s, %s, now())", (ns.wid, "测试仓", "local", "US"))
    return ns


@pytest.fixture
def scm_log(caplog):
    """★ 看得见 `scm.*` 日志的 caplog。

    `shared/logging.py` 刻意把 `scm` 的 `propagate` 关掉（root 上挂着什么不归
    我们管），而 pytest 的 caplog handler 装在 **root** 上 —— 于是裸用 caplog
    断言 `scm.*` 的日志，抓到的是空字符串。`tests/test_logging.py:80` 已经
    写明了这一点并用 `addHandler(caplog.handler)` 绕开，但
    `tests/test_api_startup_check.py` 的几条没跟上：它们**只在别的测试先跑过
    时才绿**（实测 `pytest tests/test_api_startup_check.py` 全绿，
    单跑那一条 `caplog.text` 为 `''` 直接红）。

    ★ 这正是本仓点名过的失败形态：断言被旁边的东西托住，看起来和真的守住了
      一模一样。日志断言不该依赖自己跑在谁后面。
    """
    logger = logging.getLogger("scm")
    logger.addHandler(caplog.handler)
    caplog.handler.setLevel(logging.INFO)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


@pytest.fixture
def client(wipe):
    from starlette.testclient import TestClient

    from api import create_app
    from api.ui.purchase_options import history_query_dep
    app = create_app()
    # 非 live 测试默认不碰真 CH；要历史数据或不可达路径的测试自行覆盖 / 移除
    app.dependency_overrides[history_query_dep] = lambda: (lambda sql, params=None: [])
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def use_source(client):
    """换掉这个 app 的取数源。

    ★ 取数源是**每请求**装配的（`api/ui/source_factory.source_dep`，终审
      C-1/C-2/C-3），所以没有模块级单例可以 `monkeypatch.setattr` —— 那个单例
      正是三个 Critical 的根因。这里接的是一个**无参工厂**，与生产同一个生命
      周期：想看「每请求各一个」就传 `lambda: Stub()`，想跨请求共用同一个实例
      （只有验「共用会坏」的用例才该这么做）就传 `lambda: the_one`。
    """
    from api.ui.source_factory import source_dep

    def _use(factory):
        client.app.dependency_overrides[source_dep] = factory
        return factory

    yield _use
    client.app.dependency_overrides.clear()


@pytest.fixture
def lead_time_reset(business_db):
    """lead_time 是配置不是数据（不在 DATA_TABLES 里被清）—— 改了就必须还原成 008 的种子，
    否则后面的测试会读到上一个测试留下的提前期。★ 整表重灌（有的测试会删行）。"""
    yield
    from shared.pg_client import pg_conn
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM lead_time")
        cur.execute("INSERT INTO lead_time (route, segment, days, valid_from) VALUES"
                    " ('oversea_sea', 'making', 30, DATE '2026-01-01'),"
                    " ('oversea_sea', 'shipping', 60, DATE '2026-01-01'),"
                    " ('air', 'shipping', 10, DATE '2026-01-01'),"
                    " ('domestic', 'shipping', 3, DATE '2026-01-01')")
