# 阶段 B · M1「计划 → 订单正确」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `[erp] mode=fake` 下跑通「已提交的计划单记录 → 采购员组单 → ⛔ 下单 → 读回核对」全链，且链上每一跳都能用 `v_check_*` 视图从库里证明是对的。

**Architecture:** 新增两层 `repo/`（SQL，只收 cursor）与 `usecase/`（编排，持事务）——G-2 裁定。纯判据进 `rules/purchase.py`；出站全在 `erp/`（登记表 + 签名 + 两段式传输 + fake/live driver），`erp/` 不碰库，`erp_call` 由 `usecase/` 落。库层用 `po_event` 白名单状态机 + 约束触发器做最后一道防线，Python 侧先判、给出带字段的 409。

**Tech Stack:** Python 3.13 · FastAPI · psycopg2 · PostgreSQL（schema `scm`/`scm_test`）· `cryptography`（AES/ECB 签名）· stdlib `http.client`（两段式出站）· pytest

**Spec:** `docs/superpowers/specs/2026-10-07-purchase-order-design.md`（下称 spec）

**不在本计划（各自另起计划）：** M2 RBAC（spec §12）与首张 live 单 · M3 入库读回（CR/IB/QC、作废、`recon_run`）· M4 / 前端（spec §8、§11.5）。

## Global Constraints

- 迁移只追加新文件，已执行的不可改；迁移里**不写 `scm.` 前缀、不写 BEGIN/COMMIT**（`migrations/pg/001_foundation.sql:4-7`）。
- 每张新数据表必须加进 `tests/conftest.py::DATA_TABLES`（`tests/test_migrations.py` 核对「DATA_TABLES == 实际表」）；白名单表（`po_transition`）不加。
- 测试只用 schema `scm_test`；绝不写 `scm` / `inv`（`tests/conftest.py:3-4`）。
- 错误响应形状 `{"error","hint",…点名字段平铺}`（`api/ui/errors.py:22`）；库层拒绝按 `constraint_name` 翻译，认不出的以 500 冒出，**不兜底**。
- 每个外部调用与写操作日志回答三问：打的谁 · 多久 · 怎么失败的（带 `cause` 链）。
- 命名隔离：禁用 `inbound` · `shipment` · `shipment_plan` · `allocation`（`CLAUDE.md`）。
- `[erp] mode` 默认 `fake`；`[auth] mode` 默认 `dev`；`live` + `dev` → 启动失败（spec §12.2）。
- 「超时一律转人工，绝不自动重试」（`CLAUDE.md` 出站 #2，spec §6.2 已确认）。
- 设计取值（spec §10.2，按建议）：V1 最大余数法 · V5 in_flight 卡死 10 分钟 · V6 下单超时 30 s · V7 `custom_order_sn = 'SCM-PO-' || po_id`。
- 测试写完先弄失败一次（`CLAUDE.md`）。

## Review Focus

1. **两个采购员同时把同一条记录组进不同的单** —— 必须一个成功、一个 409 `over_total` / `over_seller_cap`，绝不能两个都成功（Task 7 并发测试）。
2. **下单请求已发出、响应丢失**（读超时 / 连接被重置）—— 必须进「待人工核实」，绝不能自动重试出第二张领星单（Task 11 `unknown_created` / `unknown_absent`）。
3. **进程在「领星成功」与「本地记账」之间被杀** —— 重启后 `erp_call` 停在 in_flight，必须被启动钩子按幂等键找回（Task 11 `test_stuck_in_flight_is_recovered_by_lookup`）。
4. **用户在待人工核实期间改草稿或再点下单** —— 必须 409 `needs_verify`，不能产生第二个 in_flight（Task 11 `test_unknown_absent_goes_to_needs_verify_and_blocks_everything`）。
5. **领星落下的数量 / 单价与我们发的不一致** —— 必须进 `v_check_po_landed` 并开一条 `landed_mismatch` 待处理，而不是「拿到单号就算对」（Task 13 `test_lingxing_side_change_is_caught`）。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `migrations/pg/010_po.sql` | 供应商镜像 · `po` / `po_line` / `po_source_map` · `po_transition` / `po_event` 状态机 · 冻结 · 计划单记录的新退回边 |
| `migrations/pg/011_erp_call_obs.sql` | `erp_call`（一次性收尾）· `po_attention` · `po_snapshot` · `po_item_obs`（只追加） |
| `migrations/pg/012_po_qty_checks.sql` | 作废感知的量视图 · 分店上限 / 总量 / 超计划量约束触发器 · `v_check_*` 一族 |
| `rules/purchase.py` | 纯判据：分店上限 · 目标状态 · 迁移路径 · preflight · 内容摘要 · confirm_token |
| `repo/__init__.py` `repo/purchase.py` | 采购单 / 记录相关 SQL（只收 cursor，不开连接） |
| `usecase/__init__.py` `usecase/line_state.py` | 记录状态再评估（`rules` + `repo`） |
| `usecase/draft_po.py` | 组单 · 改草稿 · 删草稿 |
| `usecase/place_po.py` | ⛔ 下单 · 结果未知查重 · 卡死恢复 · 落地首检 |
| `usecase/attention.py` | 待处理队列：重新核对 · 登记已有单 · 确认无此单 · 时间线 |
| `erp/endpoints.py` | 端点登记表（副作用等级 · 幂等键 · 回查） |
| `erp/sign.py` | 领星签名（移植 TS） |
| `erp/transport.py` | 两段式 HTTPS：建连失败 = `not_sent`；发出后失败 = `unknown` |
| `erp/auth.py` | 领星 access_token 缓存 |
| `erp/drivers/fake.py` `erp/drivers/live.py` | 两个 driver，同一接口 |
| `erp/gateway.py` | 唯一出站口：查登记表 → 选 driver → 返回 `CallResult` |
| `dim/ch_source.py`（改） `dim/registry.py`（改） | 供应商镜像取数（CH PO 行） |
| `api/ui/purchase_orders.py` | 采购单端点 |
| `api/ui/attention.py` `api/ui/checks.py` `api/ui/simulate.py` | 待处理 · 核对 · 模拟外部 |
| `api/ui/lines.py`（改） | 候选派生字段 · `derived_state` · trace |
| `api/ui/plans.py`（改） | 计划级采购完成度 |
| `shared/config.py`（改） `api/__init__.py`（改） | `[erp]` `[auth]` 配置与启动硬闸 |
| `tests/test_layering.py`（改） | 新层规则 |

---

### Task 1: 新层骨架、配置与启动硬闸

**Files:**
- Create: `repo/__init__.py`, `usecase/__init__.py`, `erp/drivers/__init__.py`
- Modify: `tests/test_layering.py`, `shared/config.py`, `api/__init__.py`, `config.example.toml`, `pyproject.toml`
- Test: `tests/test_layering.py`, `tests/test_outbound_config.py`

**Interfaces:**
- Produces: `shared.config.erp() -> dict`（键：`mode, base_url, app_id, app_secret, timeout_s, stale_in_flight_minutes, confirm_salt, user_agent, proxy_host, proxy_port, proxy_user, proxy_password`）· `shared.config.auth() -> dict`（键：`mode`）· `api.check_outbound_config() -> None`（不合法抛 `RuntimeError`）

- [ ] **Step 1: 写失败的分层测试**

在 `tests/test_layering.py` 把 `PACKAGES` 改为：

```python
PACKAGES = ["api", "dim", "erp", "forecast", "jobs", "migrations", "repo", "rules", "shared", "usecase"]
```

并在文件末尾追加：

```python
def test_repo_takes_cursors_only():
    """repo/ 只收 cursor：不开连接、不碰出站、不知道接口层（G-2）。"""
    forbidden = ("shared.pg_client", "psycopg2", "api", "erp", "usecase")
    bad = {str(p.relative_to(ROOT)): sorted(m for m in full_imports(p) if _hits(m, forbidden))
           for p in sources("repo")}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"repo/ 越层：{bad}"


def test_usecase_does_not_know_the_http_layer():
    bad = {str(p.relative_to(ROOT)): sorted(m for m in full_imports(p) if _hits(m, ("api", "fastapi")))
           for p in sources("usecase")}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"usecase/ 不许知道 api/：{bad}"


def test_erp_knows_no_storage():
    """L4 + L6：网关不碰库、不知道动作编排。erp_call 由 usecase/ 落。"""
    forbidden = ("shared.pg_client", "psycopg2", "api", "repo", "usecase")
    bad = {str(p.relative_to(ROOT)): sorted(m for m in full_imports(p) if _hits(m, forbidden))
           for p in sources("erp")}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"erp/ 越层：{bad}"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_layering.py -q`
Expected: FAIL —— `test_every_declared_package_exists` 报 `['repo', 'usecase']` 缺失；`sources("repo")` 断言「一个 .py 都没扫到」。

- [ ] **Step 3: 建包**

```bash
mkdir -p repo usecase erp/drivers
printf '"""采购等动作的 SQL。只收 cursor，不开连接（G-2）。"""\n' > repo/__init__.py
printf '"""动作编排：取 → 判 → 写 → 调。事务边界在这一层（G-2）。"""\n' > usecase/__init__.py
printf '"""领星 driver：fake | live，同一接口。"""\n' > erp/drivers/__init__.py
```

- [ ] **Step 4: 跑分层测试通过**

Run: `.venv/bin/pytest tests/test_layering.py -q`
Expected: PASS

- [ ] **Step 5: 写失败的配置硬闸测试** `tests/test_outbound_config.py`

```python
import pytest

from shared import config as config_module


def _cfg(monkeypatch, erp: dict, auth: dict):
    monkeypatch.setattr(config_module, "erp", lambda: {**config_module._ERP_DEFAULTS, **erp})
    monkeypatch.setattr(config_module, "auth", lambda: {**config_module._AUTH_DEFAULTS, **auth})


def test_defaults_are_fake_and_dev():
    assert config_module._ERP_DEFAULTS["mode"] == "fake"
    assert config_module._AUTH_DEFAULTS["mode"] == "dev"


def test_live_with_dev_auth_refuses_to_start(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {"mode": "live", "app_id": "a", "app_secret": "s", "confirm_salt": "x"},
         {"mode": "dev"})
    with pytest.raises(RuntimeError, match="不可逆操作必须有主"):
        check_outbound_config()


def test_live_without_credentials_refuses_to_start(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {"mode": "live"}, {"mode": "rbac"})
    with pytest.raises(RuntimeError, match="app_id"):
        check_outbound_config()


def test_unknown_mode_refuses_to_start(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {"mode": "prod"}, {"mode": "dev"})
    with pytest.raises(RuntimeError, match="fake|live"):
        check_outbound_config()


def test_fake_dev_starts(monkeypatch):
    from api import check_outbound_config
    _cfg(monkeypatch, {}, {})
    check_outbound_config()
```

- [ ] **Step 6: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_outbound_config.py -q`
Expected: FAIL —— `AttributeError: module 'shared.config' has no attribute '_ERP_DEFAULTS'`

- [ ] **Step 7: 实现配置**（追加到 `shared/config.py` 末尾）

```python
#: ★ 默认 fake：任何一次漏配都不能变成「对领星建真单」（spec §6.4）。
#: ★ 设计取值（spec §10.2）：timeout_s=30（V6）· stale_in_flight_minutes=10（V5）。
_ERP_DEFAULTS = {
    "mode": "fake",
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
```

在 `api/__init__.py` 里（`_lifespan` 之前）加：

```python
def check_outbound_config() -> None:
    """★ 配置类错误往启动钩子放（全局规范第 4 条）。故意不兜异常：配错就起不来。"""
    e, a = config_module.erp(), config_module.auth()
    if e["mode"] not in ("fake", "live"):
        raise RuntimeError(f"[erp] mode 只能是 fake|live，得到 {e['mode']!r}")
    if a["mode"] not in ("dev", "rbac"):
        raise RuntimeError(f"[auth] mode 只能是 dev|rbac，得到 {a['mode']!r}")
    if e["mode"] == "live":
        if a["mode"] == "dev":
            raise RuntimeError("[erp] mode=live 而 [auth] mode=dev：不可逆操作必须有主（spec §12.2）")
        missing = [k for k in ("app_id", "app_secret", "proxy_host") if not e[k]]   # ★ 无代理 = 被 IP 白名单拒
        if e["confirm_salt"] in ("", config_module._ERP_DEFAULTS["confirm_salt"]):
            missing.append("confirm_salt（空或仍是开发默认值）")
        if missing:
            raise RuntimeError(f"[erp] mode=live 缺配置：{missing}")
    log.warning("startup erp mode=%s host=%s auth mode=%s", e["mode"], e["base_url"], a["mode"])
```

（`api/__init__.py` 已有 `from shared import config as config_module`，见其第 73 行的用法。）

在 `_lifespan` 第一行（`source_factory.log_source_config()` 之前）调用 `check_outbound_config()`。

- [ ] **Step 8: 补配置样例与依赖**

`config.example.toml` 末尾追加：

```toml
[erp]                          # ★ 领星网关（spec §6）。默认 fake；live 需 [auth] mode = "rbac"
mode         = "fake"          # fake | live
base_url     = "https://openapi.lingxing.com"
app_id       = ""
app_secret   = ""
timeout_s    = 30              # 设计取值 V6 · 未实测
stale_in_flight_minutes = 10   # 设计取值 V5 · 未实测
confirm_salt = ""              # live 必填；H-1 confirm_token 的盐
user_agent   = "service_supplychain/0.1"
# ★ 领星 IP 白名单：必须经固定 IP 代理（凭证见 /home/fido/work/2026/tools/config/services.toml [proxies.*]）
proxy_host     = ""
proxy_port     = 0
proxy_user     = ""
proxy_password = ""

[auth]
mode = "dev"                   # dev | rbac（生产必须 rbac，spec §12）
```

`pyproject.toml` 的 `dependencies` 加 `"cryptography>=43",`，然后：

Run: `uv sync`
Expected: 安装 cryptography 成功

- [ ] **Step 9: 跑全部相关测试**

Run: `.venv/bin/pytest tests/test_outbound_config.py tests/test_layering.py tests/test_config_example.py -q`
Expected: PASS（`test_config_example.py` 若核对样例键与默认值一致而失败，按它的报错把 `[erp]` `[auth]` 两段补进它的期望表）

- [ ] **Step 10: Commit**

```bash
git add repo usecase erp/drivers tests/test_layering.py tests/test_outbound_config.py shared/config.py api/__init__.py config.example.toml pyproject.toml uv.lock
git commit -m "feat(b): repo/usecase 两层 + [erp]/[auth] 配置与 live+dev 启动硬闸"
```

---

### Task 2: 刀0 —— 裁定登记进 `00`，spec 进 `00e` 地图

**Files:**
- Modify: `docs/00-待裁定清单.md`（末尾追加一节）· `docs/00e-分阶段实施计划.md`（§5.2 地图 + §8 进度）· `docs/superpowers/specs/2026-10-07-purchase-order-design.md`（§3.3 补 `po_item_obs`）
- Test: `docs/prototype/test_doc_index.js` · `docs/prototype/test_retired_claims.js`

- [ ] **Step 1: 在 `00` 末尾追加一节**（原文照抄）

```markdown
## PB 组 · 阶段 B 采购单（负责人 2026-09-30 ~ 10-07 对话）

> 设计全文：`superpowers/specs/2026-10-07-purchase-order-design.md`。本节只登记「谁说了算」。

| # | 裁定 | 取代 / 修正 |
|---|---|---|
| PB-D1 | 直接建领星采购单 PO（`createPurchaseOrder`），不经领星采购计划 PP | — |
| PB-D2 | 采购量记到店铺；分店上限 = `total_units × 期望[s] / Σ期望`（最大余数法取整） | 补 N-1 的上限口径 |
| PB-D3 | Σ期望 = 0 或有店 basis=unknown → 该记录拒绝组单，409 `no_seller_basis` 点名 | — |
| PB-D4 | 真网关 + `[erp] mode = fake｜live`，默认 fake；第一次 live 下单由负责人亲手开、亲手点 | — |
| PB-D5 | 领星作废 → 释放额度，记录按量退回；新增退回边 已下单→已确认 / 已下单→已提交 | 解 `00e` 阶段 B 判据③ 无路径 |
| PB-D6 | 采购阶段到入库为止；收货单 CR / 入库单 IB 只读回 | — |
| PB-D7 | 可备货只算良品 | B9 |
| PB-D8 | 未结单 PO 每日用领星只读接口复核 | 改 `07` §6 |
| PB-D9 | 允许超计划量，超出记「无计划来源」并必填理由 | — |
| PB-D10 | 准备排货判据 = 首批良品入库完成 | 修正 C2 的「到货」含义；取代 `04:201` |
| PB-D11 | 生产必须登录 + RBAC；硬编码身份只限开发 | ★ 重裁 Q-5（`00d` §3 触发条件「不可逆操作无主」命中） |
| PB-D12 | 生产入口 = Keycloak + oauth2-proxy；本服务不登录，只经 cf-access-rbac `by-jwt` 判权限 | — |
| PB-D13 | 请求发出后的失败（读超时 / 连接重置）= 结果未知；超时查不到 → 待人工核实，不自动重试 | 改 `07:150-157` · `06:198` |

| # | 设计取值 · 按建议 · 未实测 | 值 |
|---|---|---|
| PB-V1 | 分店上限取整 | 最大余数法 |
| PB-V2 | 回读时刻 | 每日 19:00 |
| PB-V3 | 单头未跟上宽限 | 3 天 |
| PB-V4 | 水位不动告警 | 3 天（PO 单头实测 11 天仅 4 天有采集，会常报） |
| PB-V5 | in_flight 视为卡死 | 10 分钟 |
| PB-V6 | 下单 HTTP 超时 | 30 秒 |
| PB-V7 | `custom_order_sn` | `SCM-PO-{po_id}` |
| PB-V8 | 采购方显示名 | 人填小表 |
| PB-V10 | 候选一键组入的分店预填 | 各店剩余上限 |
| PB-V11 | 单头 / 单价预填 | 该货号最近一张领星 PO，标出处 |
| PB-V12 | ⛔ 下单前绕过 RBAC 缓存重查 | 是 |

| # | 未决 | |
|---|---|---|
| PB-U1 | 无计划来源的入库量在阶段 C 怎么排（B4 会挡住） | 阶段 C 前定 |
| PB-U2 | `make_days` 默认值与命名 | `00e` §3 |
| PB-U4 | 待处理队列要不要推送（钉钉 / 邮件） | — |
| PB-U6 | 生产部署环境能否出网 `rbac.bogoo.ai:443` | M2 前 |
| PB-U7 | `scm` 权限码录入（RBAC 维护者） | M2 前 |
```

- [ ] **Step 2: `00e` §5.2 地图加一行**（放在 `2026-09-24-plan-forecast-design.md` 那行之后）

```markdown
| `superpowers/specs/2026-10-07-purchase-order-design.md` | 阶段 B 采购单 | ★ **组单 · 分店上限 · 下单网关 · 转人工 · 入库读回与一致性 · RBAC 接入**（PB 组裁定） | 排货 / 投送 |
```

`00e` §8 进度表 B 行改为：`| B 采购单 | ☐ B1~B11（PB 组已裁定，文档同步见 M1 计划 Task 16） | ☐ M1 进行中 | ☐ |`

- [ ] **Step 3: spec §3.3 表补一行**（`po_snapshot` 行之后）

```markdown
| `po_item_obs` | 领星 PO 明细 × 观察 | `order_sn` · `lx_item_id` · `sku` · `wid` · `quantity_real` · `price` —— ★ G1「落下的 ≡ 发出的」核对的数据源（M1 计划 Task 3 补） |
```

- [ ] **Step 4: 跑文档门禁**

Run: `cd docs/prototype && node test_doc_index.js && node test_retired_claims.js && node test_doc_flow.js`
Expected: 全部 ✓

- [ ] **Step 5: Commit**

```bash
git add docs/00-待裁定清单.md docs/00e-分阶段实施计划.md docs/superpowers/specs/2026-10-07-purchase-order-design.md docs/superpowers/plans/2026-10-07-purchase-order-m1.md
git commit -m "docs(b): PB 组裁定登记进 00；spec 与 M1 计划进 00e 地图"
```

---

### Task 3: 迁移 010 —— 采购单三表、状态机、冻结

**Files:**
- Create: `migrations/pg/010_po.sql`
- Modify: `tests/conftest.py`（`DATA_TABLES` 加 `po_event, po_source_map, po_line, po, supplier`）
- Test: `tests/test_ddl_010_po.py`

**Interfaces:**
- Produces（表）：`supplier(supplier_id text PK, name, refreshed_at)` · `po(po_id, supplier_id, purchaser_id int, opt_uid bigint, qc_type smallint, wid int NULL, custom_order_sn GENERATED, order_sn, state, placed_mode, placed_at, draft_version, created_by, created_at, updated_at)` · `po_line(po_id, line_no, sku, wid, units, price, expect_arrive_date, unplanned_reason, lx_item_id)` · `po_source_map(po_id, line_no, line_id, seller_id, units)` · `po_transition` · `po_event(seq, po_id, from_state, to_state, actor, reason, src, at)`
- 约束名（Task 6 翻译用）：`po_line_one_per_sku_wid` · `po_frozen` · `po_placed_needs_order_sn` · `po_source_line_not_orderable`

- [ ] **Step 1: 写失败的 DDL 测试** `tests/test_ddl_010_po.py`

```python
import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.helpers import advance, mint


@pytest.fixture
def po_world(seed):
    """一个供应商、一个仓、一条已提交记录（seed 已灌 actor/sku/seller/warehouse）。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085', '01[吉信德]', now())")
        _, line_id = mint(cur, seed.actor, seed.sku_a, total=100)
    return line_id


def _po(cur, actor, wid):
    cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                " VALUES ('8085', 111, 10925905, 2, %s) RETURNING po_id, custom_order_sn",
                (actor,))
    return cur.fetchone()


def test_custom_order_sn_is_generated_from_po_id(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        po_id, sn = _po(cur, seed.actor, seed.wid)
    assert sn == f"SCM-PO-{po_id}"


def test_new_po_starts_as_draft_with_birth_event(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("SELECT state FROM po WHERE po_id=%s", (po_id,))
        assert cur.fetchone()[0] == "草稿"
        cur.execute("SELECT from_state, to_state FROM po_event WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("[*]", "草稿")]


def test_same_sku_same_wid_twice_in_one_po_is_rejected(po_world, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        for n in (1, 2):
            cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                        " VALUES (%s, %s, %s, %s, 10, 1)", (po_id, n, seed.sku_a, seed.wid))
    assert e.value.diag.constraint_name == "po_line_one_per_sku_wid"


def test_placing_requires_order_sn(po_world, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '草稿', '已下单', %s, 't')", (po_id, seed.actor))
    assert e.value.diag.constraint_name == "po_placed_needs_order_sn"


def test_placed_po_is_frozen(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                    " VALUES (%s, 1, %s, %s, 10, 1)", (po_id, seed.sku_a, seed.wid))
        cur.execute("UPDATE po SET order_sn='FAKE-1', placed_mode='fake', placed_at=now()"
                    " WHERE po_id=%s", (po_id,))
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s, '草稿', '已下单', %s, 't')", (po_id, seed.actor))
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_line SET units = 11 WHERE po_id=%s", (po_id,))
    assert e.value.diag.constraint_name == "po_frozen"
    # ★ 唯一例外：下单后回填领星行号
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_line SET lx_item_id='9001' WHERE po_id=%s", (po_id,))


def test_mapping_only_onto_orderable_lines(po_world, seed):
    with pg_conn() as c, c.cursor() as cur:
        _, done_line = mint(cur, seed.actor, seed.sku_a, total=5)
        advance(cur, done_line, "已撤销", actor=seed.actor)
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        po_id, _ = _po(cur, seed.actor, seed.wid)
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                    " VALUES (%s, 1, %s, %s, 5, 1)", (po_id, seed.sku_a, seed.wid))
        cur.execute("INSERT INTO po_source_map VALUES (%s, 1, %s, %s, 5)",
                    (po_id, done_line, seed.seller_a))
    assert e.value.diag.constraint_name == "po_source_line_not_orderable"


def test_plan_line_has_new_back_edges(business_db):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT from_state, to_state FROM plan_line_transition WHERE from_state='已下单'"
                    " AND kind='back' ORDER BY to_state")
        assert cur.fetchall() == [("已下单", "已提交"), ("已下单", "已确认")]
```

并在 `tests/helpers.py` 末尾加（★ `mint(state=…)` 只能铸出「已提交」—— 白名单里只有 `[*]→已提交`，要别的状态必须沿合法边走过去）：

```python
def advance(cur, line_id, *states, actor, reason="test"):
    """沿白名单边把记录推到目标状态；每一步都是一条真事件，非法边照样会撞外键。"""
    cur.execute("SELECT state FROM plan_line WHERE line_id = %s", (line_id,))
    frm = cur.fetchone()[0]
    for to in states:
        cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, reason, src)"
                    " VALUES (%s, %s, %s, %s, %s, 'test')", (line_id, frm, to, actor, reason))
        frm = to
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_ddl_010_po.py -q`
Expected: FAIL —— `relation "supplier" does not exist`

- [ ] **Step 3: 写迁移** `migrations/pg/010_po.sql`

```sql
-- 010 · 阶段 B 采购单：供应商镜像 · 采购单三表 · 白名单状态机 · 冻结 · 计划单记录退回边
-- 出处：spec 2026-10-07 §3.1 §3.2 · 03 §po（03:394-473）· 04:992 · PB-D5 · B7 · B8
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① 供应商：MIRROR。源 = CH 采购单行上的 supplier_id / supplier_name（实测 3 家，spec §2）
CREATE TABLE IF NOT EXISTS supplier (
    supplier_id  text PRIMARY KEY,
    name         text NOT NULL,
    refreshed_at timestamptz NOT NULL
);

-- ② 采购单头
CREATE TABLE IF NOT EXISTS po (
    po_id           bigserial PRIMARY KEY,
    supplier_id     text     NOT NULL REFERENCES supplier(supplier_id),
    purchaser_id    int      NOT NULL,                 -- 领星采购方（实测 111 / 185）
    opt_uid         bigint   NOT NULL,                 -- 领星采购员
    qc_type         smallint NOT NULL CONSTRAINT po_qc_type_known CHECK (qc_type IN (0,1,2)),  -- ★ B8
    wid             int      REFERENCES warehouse(wid),  -- ★ B7：单头可空，仓在行上
    -- ★ V7：幂等键由 po_id 派生，撞不上领星自填的 PO… 形态（实测 custom=order_sn 143/143）
    custom_order_sn text GENERATED ALWAYS AS ('SCM-PO-' || po_id::text) STORED UNIQUE,
    order_sn        text UNIQUE,
    state           text     NOT NULL DEFAULT '草稿',
    placed_mode     text     CONSTRAINT po_placed_mode_known CHECK (placed_mode IN ('fake','live')),
    placed_at       timestamptz,
    draft_version   int      NOT NULL DEFAULT 1,
    -- ★ 试过下单（有 erp_call）的草稿不能硬删 —— 出站证据要留着；只能「作废草稿」：
    --   清掉明细与映射（释放额度），单头留档
    discarded_at    timestamptz,
    discarded_by    text     REFERENCES actor(actor_id),
    created_by      text     NOT NULL REFERENCES actor(actor_id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ③ 明细：一单内同货号同仓只一行（N-1「下单时合并」由来源映射承载）
CREATE TABLE IF NOT EXISTS po_line (
    po_id              bigint NOT NULL REFERENCES po(po_id) ON DELETE CASCADE,
    line_no            int    NOT NULL,
    sku                text   NOT NULL REFERENCES sku_catalog(sku),
    wid                int    NOT NULL REFERENCES warehouse(wid),          -- ★ B7
    units              int    NOT NULL CONSTRAINT po_line_units_positive CHECK (units > 0),
    price              numeric(18,4) NOT NULL CONSTRAINT po_line_price_nonneg CHECK (price >= 0),
    expect_arrive_date date,
    unplanned_reason   text,                                               -- ★ PB-D9
    lx_item_id         text,                                               -- 下单后回填，回读行级键
    CONSTRAINT po_line_pkey PRIMARY KEY (po_id, line_no),
    CONSTRAINT po_line_one_per_sku_wid UNIQUE (po_id, sku, wid)
);

-- ④ 承重墙①
CREATE TABLE IF NOT EXISTS po_source_map (
    po_id     bigint NOT NULL,
    line_no   int    NOT NULL,
    line_id   bigint NOT NULL REFERENCES plan_line(line_id),
    seller_id text   NOT NULL REFERENCES seller(seller_id),
    units     int    NOT NULL CONSTRAINT po_source_units_positive CHECK (units > 0),
    CONSTRAINT po_source_map_pkey PRIMARY KEY (po_id, line_no, line_id, seller_id),
    CONSTRAINT po_source_line_fk FOREIGN KEY (po_id, line_no)
        REFERENCES po_line(po_id, line_no) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS po_source_by_line_idx   ON po_source_map (line_id);
CREATE INDEX IF NOT EXISTS po_source_by_seller_idx ON po_source_map (line_id, seller_id);

-- ⑤ 采购单本地状态机：白名单 + 事件表（同 003 的形状）
CREATE TABLE IF NOT EXISTS po_transition (
    from_state text NOT NULL,
    to_state   text NOT NULL,
    CONSTRAINT po_transition_pkey PRIMARY KEY (from_state, to_state)
);
INSERT INTO po_transition VALUES ('[*]','草稿'), ('草稿','已下单') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS po_event (
    seq        bigserial PRIMARY KEY,
    po_id      bigint NOT NULL REFERENCES po(po_id) ON DELETE CASCADE,
    from_state text   NOT NULL,
    to_state   text   NOT NULL,
    actor      text   NOT NULL,
    reason     text,
    src        text,
    at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT po_event_transition_fk FOREIGN KEY (from_state, to_state)
        REFERENCES po_transition(from_state, to_state)
);

CREATE OR REPLACE FUNCTION po_birth_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO po_event (po_id, from_state, to_state, actor, src)
    VALUES (NEW.po_id, '[*]', '草稿', NEW.created_by, 'create');
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION sync_po_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text; sn text; mode text;
BEGIN
    IF NEW.from_state = '[*]' THEN RETURN NEW; END IF;
    SELECT state, order_sn, placed_mode INTO cur, sn, mode FROM po WHERE po_id = NEW.po_id FOR UPDATE;
    IF cur IS DISTINCT FROM NEW.from_state THEN
        RAISE EXCEPTION 'po % from_state 不匹配：当前 %，事件称 %', NEW.po_id, cur, NEW.from_state;
    END IF;
    IF NEW.to_state = '已下单' AND (sn IS NULL OR mode IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_placed_needs_order_sn',
              MESSAGE = format('po %s 转已下单前必须已记 order_sn 与 placed_mode', NEW.po_id);
    END IF;
    PERFORM set_config('app.in_po_state_sync','on',true);
    UPDATE po SET state = NEW.to_state, updated_at = now() WHERE po_id = NEW.po_id;
    PERFORM set_config('app.in_po_state_sync','off',true);
    RETURN NEW;
END $$;

-- ⑥ 冻结：已下单之后，单头只许状态同步改 state；明细只许回填 lx_item_id；映射不许动
CREATE OR REPLACE FUNCTION po_guard_header() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state IS DISTINCT FROM OLD.state
       AND current_setting('app.in_po_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION '采购单状态只能由 po_event 推进';
    END IF;
    IF OLD.state = '已下单' AND current_setting('app.in_po_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_frozen',
              MESSAGE = format('采购单 %s 已下单，不可改', OLD.po_id);
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION po_guard_children() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text; pid bigint;
BEGIN
    pid := CASE WHEN TG_OP = 'DELETE' THEN OLD.po_id ELSE NEW.po_id END;
    SELECT state INTO st FROM po WHERE po_id = pid;
    IF st = '已下单' THEN
        IF TG_TABLE_NAME = 'po_line' AND TG_OP = 'UPDATE'
           AND (to_jsonb(NEW) - 'lx_item_id') = (to_jsonb(OLD) - 'lx_item_id') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_frozen',
              MESSAGE = format('采购单 %s 已下单，%s 不可 %s', pid, TG_TABLE_NAME, TG_OP);
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- ⑦ 映射只能挂在可组单的记录上（03:942）
CREATE OR REPLACE FUNCTION po_source_precondition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
    SELECT state INTO st FROM plan_line WHERE line_id = NEW.line_id;
    IF st NOT IN ('已提交','已确认') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_source_line_not_orderable',
              MESSAGE = format('记录 %s 处于 %s，只有 已提交/已确认 能组单', NEW.line_id, st);
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS t_po_birth ON po;
CREATE TRIGGER t_po_birth AFTER INSERT ON po FOR EACH ROW EXECUTE FUNCTION po_birth_event();
DROP TRIGGER IF EXISTS t_po_event_append_only ON po_event;
CREATE TRIGGER t_po_event_append_only BEFORE UPDATE ON po_event
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_po_sync_state ON po_event;
CREATE TRIGGER t_po_sync_state AFTER INSERT ON po_event FOR EACH ROW EXECUTE FUNCTION sync_po_state();
DROP TRIGGER IF EXISTS t_po_guard ON po;
CREATE TRIGGER t_po_guard BEFORE UPDATE ON po FOR EACH ROW EXECUTE FUNCTION po_guard_header();
DROP TRIGGER IF EXISTS t_po_line_guard ON po_line;
CREATE TRIGGER t_po_line_guard BEFORE INSERT OR UPDATE OR DELETE ON po_line
    FOR EACH ROW EXECUTE FUNCTION po_guard_children();
DROP TRIGGER IF EXISTS t_po_source_guard ON po_source_map;
CREATE TRIGGER t_po_source_guard BEFORE INSERT OR UPDATE OR DELETE ON po_source_map
    FOR EACH ROW EXECUTE FUNCTION po_guard_children();
DROP TRIGGER IF EXISTS t_po_source_pre ON po_source_map;
CREATE TRIGGER t_po_source_pre BEFORE INSERT ON po_source_map
    FOR EACH ROW EXECUTE FUNCTION po_source_precondition();

-- ⑧ 计划单记录：作废退回边（PB-D5）。由量判据自动落点，不需要理由
INSERT INTO plan_line_transition VALUES
 ('已下单','已确认','back',false),
 ('已下单','已提交','back',false)
ON CONFLICT DO NOTHING;
```

> 注：`po_event` 只挡 UPDATE、不挡 DELETE —— 删草稿时 `ON DELETE CASCADE` 要把它连带删掉（草稿没有对外后果）；已下单的单由 `po_guard_children` 之外的路径保护：`usecase` 不提供删已下单的入口，且 `po` 的 DELETE 在 Task 6 的 `delete_draft` 里先校验状态。

- [ ] **Step 4: `DATA_TABLES` 加新表**

在 `tests/conftest.py::DATA_TABLES` 开头加 `"po_event", "po_source_map", "po_line", "po", "supplier",`（`po_transition` 是白名单，不加）。

- [ ] **Step 5: 跑测试通过**

Run: `.venv/bin/pytest tests/test_ddl_010_po.py tests/test_migrations.py tests/test_ddl_003_state_machine.py -q`
Expected: PASS（若 `test_ddl_003` 有断言「白名单恰好 N 行」，把期望行数 +2 并在断言旁注明 PB-D5）

- [ ] **Step 6: Commit**

```bash
git add migrations/pg/010_po.sql tests/test_ddl_010_po.py tests/conftest.py tests/helpers.py tests/test_ddl_003_state_machine.py
git commit -m "feat(b): 迁移 010 —— 采购单三表、po_event 白名单状态机、下单后冻结、作废退回边"
```

---

### Task 4: 迁移 011 —— 出站留痕、待处理、领星观察

**Files:**
- Create: `migrations/pg/011_erp_call_obs.sql`
- Modify: `tests/conftest.py`（`DATA_TABLES` 加 `po_item_obs, po_snapshot, po_attention, erp_call`）
- Test: `tests/test_ddl_011_erp_call.py`

**Interfaces:**
- Produces（表）：`erp_call(call_id, endpoint, side_effect, mode, po_id, idem_key, retry_of, actor, request jsonb, outcome, http_status, lx_code, lx_msg, cause, response jsonb, host, path, elapsed_ms, started_at, finished_at)` · `po_attention(attention_id, po_id, kind, opened_at, opened_by_call, resolved_at, resolution, resolved_by, reason, detail jsonb)` · `po_snapshot(...)` · `po_item_obs(obs_id, order_sn, lx_item_id, sku, wid, quantity_real, price, source, source_captured_at, observed_at, call_id)`
- 约束名：`erp_call_one_in_flight_per_po` · `po_attention_one_open`

- [ ] **Step 1: 写失败的测试** `tests/test_ddl_011_erp_call.py`

```python
import psycopg2
import pytest

from shared.pg_client import pg_conn


@pytest.fixture
def draft(seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                    " VALUES ('8085',111,1,2,%s) RETURNING po_id", (seed.actor,))
        return cur.fetchone()[0]


def _call(cur, po_id, actor, outcome="in_flight"):
    cur.execute(
        "INSERT INTO erp_call (endpoint, side_effect, mode, po_id, idem_key, actor, request, outcome)"
        " VALUES ('create_purchase_order','irreversible','fake',%s,'SCM-PO-1',%s,'{}',%s)"
        " RETURNING call_id", (po_id, actor, outcome))
    return cur.fetchone()[0]


def test_only_one_irreversible_call_in_flight_per_po(draft, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        _call(cur, draft, seed.actor)
        _call(cur, draft, seed.actor)
    assert e.value.diag.constraint_name == "erp_call_one_in_flight_per_po"


def test_erp_call_closes_once(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
        cur.execute("UPDATE erp_call SET outcome='ok', finished_at=now() WHERE call_id=%s", (cid,))
    with pytest.raises(psycopg2.Error, match="只能收尾一次"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE erp_call SET outcome='rejected' WHERE call_id=%s", (cid,))


def test_erp_call_request_is_immutable(draft, seed):
    with pg_conn() as c, c.cursor() as cur:
        cid = _call(cur, draft, seed.actor)
    with pytest.raises(psycopg2.Error, match="只能收尾"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE erp_call SET request='{\"x\":1}' WHERE call_id=%s", (cid,))


def test_observations_are_append_only(business_db, wipe):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO po_item_obs (order_sn, lx_item_id, sku, wid, quantity_real, price,"
                    " source, source_captured_at) VALUES ('PO1','1','S',1,10,1,'sim',now())"
                    " RETURNING obs_id")
        oid = cur.fetchone()[0]
    with pytest.raises(psycopg2.Error, match="只追加"), pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE po_item_obs SET quantity_real=11 WHERE obs_id=%s", (oid,))


def test_one_open_attention_per_kind(draft, seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        for _ in (1, 2):
            cur.execute("INSERT INTO po_attention (po_id, kind) VALUES (%s,'needs_verify')", (draft,))
    assert e.value.diag.constraint_name == "po_attention_one_open"
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_ddl_011_erp_call.py -q`
Expected: FAIL —— `relation "erp_call" does not exist`

- [ ] **Step 3: 写迁移** `migrations/pg/011_erp_call_obs.sql`

```sql
-- 011 · 出站留痕 · 待处理队列 · 领星观察（只追加）
-- 出处：spec §3.3 §6 · 03:1519-1531（erp_call 原型）· CLAUDE.md 出站 #3 #5 · 铁律 8
-- ★ outcome 取值与 03 原型不同（in_flight/ok/rejected/not_sent/unknown）：
--   「没发出去」与「发出去了不知道结果」处置相反（spec §6.2），03 的 conn_failed/timeout
--   按「怎么失败」分，不按「能不能重试」分。文档同步见计划 Task 16。

CREATE TABLE IF NOT EXISTS erp_call (
    call_id     bigserial PRIMARY KEY,
    endpoint    text  NOT NULL,                  -- erp/endpoints.py 的登记名
    side_effect text  NOT NULL CONSTRAINT erp_call_side_effect_known
                      CHECK (side_effect IN ('read','reversible','irreversible')),
    mode        text  NOT NULL CONSTRAINT erp_call_mode_known CHECK (mode IN ('fake','live')),
    po_id       bigint REFERENCES po(po_id),   -- ★ 不级联：试过下单的草稿只能「作废」不能删（证据要留）
    idem_key    text,
    retry_of    bigint REFERENCES erp_call(call_id),
    actor       text  NOT NULL,
    request     jsonb NOT NULL,                  -- ★ 已脱敏（无 token / sign）
    outcome     text  NOT NULL DEFAULT 'in_flight' CONSTRAINT erp_call_outcome_known
                      CHECK (outcome IN ('in_flight','ok','rejected','not_sent','unknown')),
    http_status int,
    lx_code     text,
    lx_msg      text,
    cause       text,
    response    jsonb,
    host        text,
    path        text,
    elapsed_ms  int,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS erp_call_one_in_flight_per_po
    ON erp_call (po_id) WHERE outcome = 'in_flight' AND side_effect = 'irreversible';
CREATE INDEX IF NOT EXISTS erp_call_by_po ON erp_call (po_id, call_id);

-- ★ 一次性收尾：只能从 in_flight 改一次，且只能改结果列
CREATE OR REPLACE FUNCTION erp_call_close_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'erp_call 是出站证据，不许删除';
    END IF;
    IF OLD.outcome <> 'in_flight' THEN
        RAISE EXCEPTION 'erp_call % 只能收尾一次（已是 %）', OLD.call_id, OLD.outcome;
    END IF;
    IF (NEW.endpoint, NEW.side_effect, NEW.mode, NEW.po_id, NEW.idem_key, NEW.retry_of,
        NEW.actor, NEW.request, NEW.started_at)
       IS DISTINCT FROM
       (OLD.endpoint, OLD.side_effect, OLD.mode, OLD.po_id, OLD.idem_key, OLD.retry_of,
        OLD.actor, OLD.request, OLD.started_at) THEN
        RAISE EXCEPTION 'erp_call % 只能收尾结果列，请求侧不可改', OLD.call_id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS t_erp_call_close_once ON erp_call;
CREATE TRIGGER t_erp_call_close_once BEFORE UPDATE OR DELETE ON erp_call
    FOR EACH ROW EXECUTE FUNCTION erp_call_close_once();

-- 待处理队列（spec §6.6）：同一张单同一类最多一条未处理
CREATE TABLE IF NOT EXISTS po_attention (
    attention_id   bigserial PRIMARY KEY,
    po_id          bigint NOT NULL REFERENCES po(po_id) ON DELETE CASCADE,
    kind           text   NOT NULL CONSTRAINT po_attention_kind_known
                          CHECK (kind IN ('needs_verify','landed_mismatch')),
    opened_at      timestamptz NOT NULL DEFAULT now(),
    opened_by_call bigint REFERENCES erp_call(call_id),
    resolved_at    timestamptz,
    resolution     text CONSTRAINT po_attention_resolution_known
                        CHECK (resolution IN ('rechecked_found','found_existing',
                                              'confirmed_absent','mismatch_acknowledged')),
    resolved_by    text REFERENCES actor(actor_id),
    reason         text,
    detail         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS po_attention_one_open
    ON po_attention (po_id, kind) WHERE resolved_at IS NULL;

CREATE OR REPLACE FUNCTION po_attention_resolve_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.resolved_at IS NOT NULL THEN
        RAISE EXCEPTION '待处理 % 已处理，不可再改', OLD.attention_id;
    END IF;
    IF NEW.resolved_at IS NULL OR NEW.resolution IS NULL OR NEW.resolved_by IS NULL THEN
        RAISE EXCEPTION '处理待处理必须同时给出 resolved_at / resolution / resolved_by';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS t_po_attention_resolve_once ON po_attention;
CREATE TRIGGER t_po_attention_resolve_once BEFORE UPDATE ON po_attention
    FOR EACH ROW EXECUTE FUNCTION po_attention_resolve_once();

-- 领星观察（只追加，铁律 8）。M1 只用 po_item_obs；po_snapshot 先建出来供 M3
CREATE TABLE IF NOT EXISTS po_snapshot (
    snap_id            bigserial PRIMARY KEY,
    order_sn           text NOT NULL,
    status             int  NOT NULL,     -- 3 待提交 / 1 待下单 / 2 待到货 / 9 完成 / -1 作废 / 121 / 122
    status_shipped     int,
    quantity_real      int,
    quantity_entry     int,
    quantity_receive   int,
    source             text NOT NULL CONSTRAINT po_snapshot_source_known CHECK (source IN ('ch','api','sim')),
    source_captured_at timestamptz NOT NULL,
    observed_at        timestamptz NOT NULL DEFAULT now(),
    call_id            bigint REFERENCES erp_call(call_id)
);
CREATE INDEX IF NOT EXISTS po_snapshot_latest ON po_snapshot (order_sn, source_captured_at DESC, snap_id DESC);

CREATE TABLE IF NOT EXISTS po_item_obs (
    obs_id             bigserial PRIMARY KEY,
    order_sn           text NOT NULL,
    lx_item_id         text,
    sku                text NOT NULL,
    wid                int,
    quantity_real      int  NOT NULL,
    price              numeric(18,4),
    source             text NOT NULL CONSTRAINT po_item_obs_source_known CHECK (source IN ('ch','api','sim')),
    source_captured_at timestamptz NOT NULL,
    observed_at        timestamptz NOT NULL DEFAULT now(),
    call_id            bigint REFERENCES erp_call(call_id)
);
CREATE INDEX IF NOT EXISTS po_item_obs_latest ON po_item_obs (order_sn, source_captured_at DESC, obs_id DESC);

DROP TRIGGER IF EXISTS t_po_snapshot_append_only ON po_snapshot;
CREATE TRIGGER t_po_snapshot_append_only BEFORE UPDATE OR DELETE ON po_snapshot
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_po_item_obs_append_only ON po_item_obs;
CREATE TRIGGER t_po_item_obs_append_only BEFORE UPDATE OR DELETE ON po_item_obs
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
```

- [ ] **Step 4: `DATA_TABLES` 加 `"po_item_obs", "po_snapshot", "po_attention", "erp_call",`（放在 `"po_event"` 之前）**

> ★ `wipe` 用 `TRUNCATE … CASCADE`，`forbid_update_delete` 只挡 UPDATE/DELETE 不挡 TRUNCATE —— 若 wipe 报「只追加表不允许」，先读 `tests/conftest.py::wipe` 确认它用的是 TRUNCATE。

- [ ] **Step 5: 跑测试通过**

Run: `.venv/bin/pytest tests/test_ddl_011_erp_call.py tests/test_migrations.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add migrations/pg/011_erp_call_obs.sql tests/test_ddl_011_erp_call.py tests/conftest.py
git commit -m "feat(b): 迁移 011 —— erp_call 一次性收尾、待处理队列、领星观察只追加"
```

---

### Task 5: 迁移 012 —— 作废感知的量、分店上限、`v_check_*`

**Files:**
- Create: `migrations/pg/012_po_qty_checks.sql`
- Test: `tests/test_ddl_012_po_qty.py`

**Interfaces:**
- Produces（视图 / 函数）：`v_po_lx_status(order_sn, status, source, source_captured_at)` · `v_po_effective(po.* + is_void)` · `v_plan_line_po_qty(line_id, total_units, state, confirmed_units, ordered_units)` · `seller_caps(line_id) → (seller_id, cap)`（无依据时返回空集）· `v_po_item_latest` · `v_check_po_source` · `v_check_line_state` · `v_check_po_unplanned` · `v_check_po_landed` · `v_check_purchase(check_name, ref jsonb)`
- 约束名：`po_over_total` · `po_over_seller_cap` · `po_no_seller_basis` · `po_line_below_sources` · `po_unplanned_reason_required`

- [ ] **Step 1: 写失败的测试** `tests/test_ddl_012_po_qty.py`

```python
import json

import psycopg2
import pytest

from shared.pg_client import pg_conn
from tests.helpers import advance, mint


def _line(cur, seed, total, demand: dict):
    _, lid = mint(cur, seed.actor, seed.sku_a, total=total)
    cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                (json.dumps(demand), lid))
    return lid


def _draft(cur, seed, units):
    cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
    cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, created_by)"
                " VALUES ('8085',111,1,2,%s) RETURNING po_id", (seed.actor,))
    po_id = cur.fetchone()[0]
    cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price)"
                " VALUES (%s,1,%s,%s,%s,1)", (po_id, seed.sku_a, seed.wid, units))
    return po_id


def test_seller_caps_largest_remainder(seed):
    # 24 × 12/23 = 12.52 · 24 × 11/23 = 11.48 → 13 / 11，合计恒等于 24
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {"A": {"units": 12, "basis": "human"},
                                    "B": {"units": 11, "basis": "human"}})
        cur.execute("SELECT seller_id, cap FROM seller_caps(%s) ORDER BY 1", (lid,))
        assert cur.fetchall() == [("A", 13), ("B", 11)]


def test_seller_caps_empty_when_no_basis(seed):
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {"A": {"units": 0, "basis": "human"}})
        cur.execute("SELECT count(*) FROM seller_caps(%s)", (lid,))
        assert cur.fetchone()[0] == 0
        lid2 = _line(cur, seed, 24, {"A": {"units": 5, "basis": "unknown"}})
        cur.execute("SELECT count(*) FROM seller_caps(%s)", (lid2,))
        assert cur.fetchone()[0] == 0


def test_over_seller_cap_is_rejected_at_commit(seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 12, "basis": "human"},
                                    "OTHER": {"units": 11, "basis": "human"}})
        po_id = _draft(cur, seed, 14)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,14)", (po_id, lid, seed.seller_a))
    assert e.value.diag.constraint_name == "po_over_seller_cap"


def test_no_seller_basis_is_rejected_at_commit(seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 0, "basis": "human"}})
        po_id = _draft(cur, seed, 5)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,5)", (po_id, lid, seed.seller_a))
    assert e.value.diag.constraint_name == "po_no_seller_basis"


def test_unplanned_units_need_a_reason(seed):
    with pytest.raises(psycopg2.Error) as e, pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 10, "basis": "human"}})
        po_id = _draft(cur, seed, 30)       # 来源 24，超 6 件且没写理由
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,24)", (po_id, lid, seed.seller_a))
    assert e.value.diag.constraint_name == "po_unplanned_reason_required"


def test_void_po_releases_quantity(seed):
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 10, "basis": "human"}})
        po_id = _draft(cur, seed, 24)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,24)", (po_id, lid, seed.seller_a))
        cur.execute("UPDATE po SET order_sn='FAKE-9', placed_mode='fake', placed_at=now() WHERE po_id=%s",
                    (po_id,))
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s,'草稿','已下单',%s,'t')", (po_id, seed.actor))
        cur.execute("SELECT confirmed_units, ordered_units FROM v_plan_line_po_qty WHERE line_id=%s", (lid,))
        assert cur.fetchone() == (24, 24)
        cur.execute("INSERT INTO po_snapshot (order_sn, status, source, source_captured_at)"
                    " VALUES ('FAKE-9', -1, 'sim', now())")
        cur.execute("SELECT confirmed_units, ordered_units FROM v_plan_line_po_qty WHERE line_id=%s", (lid,))
        assert cur.fetchone() == (0, 0)


def test_check_line_state_reports_drift(seed):
    with pg_conn() as c, c.cursor() as cur:
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        advance(cur, lid, "已确认", actor=seed.actor)        # 已确认但 0 件组单
        cur.execute("SELECT line_id, state, derived FROM v_check_line_state")
        assert (lid, "已确认", "已提交") in cur.fetchall()


def test_check_po_landed_flags_quantity_drift(seed):
    with pg_conn() as c, c.cursor() as cur:
        lid = _line(cur, seed, 24, {seed.seller_a: {"units": 10, "basis": "human"}})
        po_id = _draft(cur, seed, 24)
        cur.execute("INSERT INTO po_source_map VALUES (%s,1,%s,%s,24)", (po_id, lid, seed.seller_a))
        cur.execute("UPDATE po SET order_sn='FAKE-7', placed_mode='fake', placed_at=now() WHERE po_id=%s",
                    (po_id,))
        cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                    " VALUES (%s,'草稿','已下单',%s,'t')", (po_id, seed.actor))
        cur.execute("SELECT problem FROM v_check_po_landed WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("not_observed",)]
        cur.execute("INSERT INTO po_item_obs (order_sn, sku, wid, quantity_real, price, source,"
                    " source_captured_at) VALUES ('FAKE-7', %s, %s, 20, 1, 'sim', now())",
                    (seed.sku_a, seed.wid))
        cur.execute("SELECT problem, ours_units, lx_units FROM v_check_po_landed WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("qty_or_price_differs", 24, 20)]
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_ddl_012_po_qty.py -q`
Expected: FAIL —— `function seller_caps(bigint) does not exist`

- [ ] **Step 3: 写迁移** `migrations/pg/012_po_qty_checks.sql`

```sql
-- 012 · 作废感知的量 · 分店上限 · 超计划量 · v_check_* 一族
-- 出处：spec §3.2 §3.4 §11.1 · PB-D2 PB-D3 PB-D5 PB-D9 · 03:869-913（量视图与 c_po_qty 原型）
-- ★ Python 侧（rules/purchase.py）先判并给出带字段的 409；这里是最后一道防线，
--   两边口径必须逐字一致 —— 最大余数法的并列按 seller_id 升序。

CREATE OR REPLACE VIEW v_po_lx_status AS
SELECT DISTINCT ON (order_sn) order_sn, status, source, source_captured_at
  FROM po_snapshot
 ORDER BY order_sn, source_captured_at DESC, snap_id DESC;

CREATE OR REPLACE VIEW v_po_effective AS
SELECT p.*, coalesce(s.status = -1, false) AS is_void
  FROM po p LEFT JOIN v_po_lx_status s ON s.order_sn = p.order_sn;

CREATE OR REPLACE VIEW v_plan_line_po_qty AS
SELECT l.line_id, l.total_units, l.state,
       coalesce(sum(m.units) FILTER (WHERE NOT e.is_void), 0)::int                        AS confirmed_units,
       coalesce(sum(m.units) FILTER (WHERE NOT e.is_void AND e.state = '已下单'), 0)::int AS ordered_units
  FROM plan_line l
  LEFT JOIN po_source_map m ON m.line_id = l.line_id
  LEFT JOIN v_po_effective e ON e.po_id = m.po_id
 GROUP BY l.line_id, l.total_units, l.state;

-- ★ PB-D2：上限 = total × 期望[s] / Σ期望，最大余数法；PB-D3：无依据返回空集
CREATE OR REPLACE FUNCTION seller_caps(p_line_id bigint)
RETURNS TABLE (seller_id text, cap int) LANGUAGE plpgsql STABLE AS $$
DECLARE tot bigint; d jsonb; s bigint;
BEGIN
    SELECT total_units, demand_by_seller INTO tot, d FROM plan_line WHERE line_id = p_line_id;
    IF d IS NULL OR NOT EXISTS (SELECT 1 FROM jsonb_each(d)) THEN RETURN; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_each(d) e
                WHERE e.value->>'basis' = 'unknown'
                   OR jsonb_typeof(e.value->'units') IS DISTINCT FROM 'number') THEN
        RETURN;
    END IF;
    SELECT sum((e.value->>'units')::bigint) INTO s FROM jsonb_each(d) e;
    IF s = 0 THEN RETURN; END IF;
    RETURN QUERY
    WITH x AS (SELECT e.key AS sid, (e.value->>'units')::bigint AS u FROM jsonb_each(d) e),
         f AS (SELECT x.sid, (tot * x.u) / s AS fl, (tot * x.u) % s AS rm FROM x),
         r AS (SELECT f.sid, f.fl, row_number() OVER (ORDER BY f.rm DESC, f.sid) AS rk FROM f)
    SELECT r.sid, (r.fl + CASE WHEN r.rk <= tot - (SELECT sum(fl) FROM f) THEN 1 ELSE 0 END)::int
      FROM r;
END $$;

CREATE OR REPLACE FUNCTION assert_line_po_qty(p_line_id bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE q record; v record; has_caps boolean;
BEGIN
    SELECT * INTO q FROM v_plan_line_po_qty WHERE line_id = p_line_id;
    IF q.confirmed_units > q.total_units THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_over_total',
              MESSAGE = format('记录 %s 已组单 %s 超过总量 %s', p_line_id, q.confirmed_units, q.total_units);
    END IF;
    IF q.confirmed_units = 0 THEN RETURN; END IF;
    SELECT EXISTS (SELECT 1 FROM seller_caps(p_line_id)) INTO has_caps;
    IF NOT has_caps THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_no_seller_basis',
              MESSAGE = format('记录 %s 的期望销量合计为 0 或有店为 unknown，不可组单', p_line_id);
    END IF;
    FOR v IN
        SELECT m.seller_id, sum(m.units) AS used, coalesce(c.cap, 0) AS cap
          FROM po_source_map m
          JOIN v_po_effective e ON e.po_id = m.po_id AND NOT e.is_void
          LEFT JOIN seller_caps(p_line_id) c ON c.seller_id = m.seller_id
         WHERE m.line_id = p_line_id
         GROUP BY m.seller_id, c.cap
    LOOP
        IF v.used > v.cap THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_over_seller_cap',
                  MESSAGE = format('记录 %s 店 %s 已用 %s 超过上限 %s', p_line_id, v.seller_id, v.used, v.cap);
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION assert_po_line_sources(p_po_id bigint, p_line_no int) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE u int; reason text; src int;
BEGIN
    SELECT units, unplanned_reason INTO u, reason FROM po_line WHERE po_id = p_po_id AND line_no = p_line_no;
    IF u IS NULL THEN RETURN; END IF;     -- 行已被删
    SELECT coalesce(sum(units), 0) INTO src FROM po_source_map WHERE po_id = p_po_id AND line_no = p_line_no;
    IF u < src THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_line_below_sources',
              MESSAGE = format('采购单 %s 行 %s 数量 %s 少于来源合计 %s', p_po_id, p_line_no, u, src);
    END IF;
    IF u > src AND coalesce(btrim(reason), '') = '' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_unplanned_reason_required',
              MESSAGE = format('采购单 %s 行 %s 有 %s 件无计划来源，必须写理由', p_po_id, p_line_no, u - src);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION c_po_source_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        PERFORM assert_line_po_qty(OLD.line_id);
        PERFORM assert_po_line_sources(OLD.po_id, OLD.line_no);
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
        PERFORM assert_line_po_qty(NEW.line_id);
        PERFORM assert_po_line_sources(NEW.po_id, NEW.line_no);
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION c_po_line_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'DELETE' THEN PERFORM assert_po_line_sources(NEW.po_id, NEW.line_no); END IF;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS c_po_source_qty ON po_source_map;
CREATE CONSTRAINT TRIGGER c_po_source_qty AFTER INSERT OR UPDATE OR DELETE ON po_source_map
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION c_po_source_check();
DROP TRIGGER IF EXISTS c_po_line_qty ON po_line;
CREATE CONSTRAINT TRIGGER c_po_line_qty AFTER INSERT OR UPDATE ON po_line
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION c_po_line_check();

-- ── v_check_*：每个视图返回违例行，空 = 通过（spec §11 G3） ──────────────────
CREATE OR REPLACE VIEW v_check_po_source AS
SELECT q.line_id, NULL::text AS seller_id, 'over_total' AS problem,
       q.confirmed_units AS used, q.total_units AS cap
  FROM v_plan_line_po_qty q WHERE q.confirmed_units > q.total_units
UNION ALL
SELECT m.line_id, m.seller_id, 'over_seller_cap', sum(m.units)::int, coalesce(c.cap, 0)
  FROM po_source_map m
  JOIN v_po_effective e ON e.po_id = m.po_id AND NOT e.is_void
  LEFT JOIN LATERAL seller_caps(m.line_id) c ON c.seller_id = m.seller_id
 GROUP BY m.line_id, m.seller_id, c.cap
HAVING sum(m.units) > coalesce(c.cap, 0);

-- ★ 判据（04:378-380）：总量 > 0 且 已下单量 = 总量 → 已下单；已确认量 = 总量 → 已确认；否则 已提交
CREATE OR REPLACE VIEW v_check_line_state AS
SELECT q.line_id, q.state,
       CASE WHEN q.ordered_units = q.total_units THEN '已下单'
            WHEN q.confirmed_units = q.total_units THEN '已确认'
            ELSE '已提交' END AS derived
  FROM v_plan_line_po_qty q
 WHERE q.state IN ('已提交','已确认','已下单')
   AND q.state <> CASE WHEN q.ordered_units = q.total_units THEN '已下单'
                       WHEN q.confirmed_units = q.total_units THEN '已确认'
                       ELSE '已提交' END;

CREATE OR REPLACE VIEW v_check_po_unplanned AS
SELECT l.po_id, l.line_no, l.units, coalesce(s.src, 0) AS sources,
       CASE WHEN l.units < coalesce(s.src, 0) THEN 'below_sources' ELSE 'reason_missing' END AS problem
  FROM po_line l
  LEFT JOIN (SELECT po_id, line_no, sum(units)::int AS src FROM po_source_map GROUP BY 1, 2) s
         USING (po_id, line_no)
 WHERE l.units < coalesce(s.src, 0)
    OR (l.units > coalesce(s.src, 0) AND coalesce(btrim(l.unplanned_reason), '') = '');

CREATE OR REPLACE VIEW v_po_item_latest AS
SELECT o.* FROM po_item_obs o
  JOIN (SELECT order_sn, max(source_captured_at) AS at FROM po_item_obs GROUP BY order_sn) m
    ON m.order_sn = o.order_sn AND m.at = o.source_captured_at;

-- ★ G1：领星落下的 ≡ 我们发的。行级键优先 lx_item_id，缺时按 (sku, wid)
CREATE OR REPLACE VIEW v_check_po_landed AS
WITH ours AS (
    SELECT p.po_id, p.order_sn, l.line_no, l.sku, l.wid, l.units, l.price, l.lx_item_id
      FROM po p JOIN po_line l USING (po_id) WHERE p.state = '已下单'),
pairs AS (
    SELECT o.*, b.quantity_real AS lx_units, b.price AS lx_price
      FROM ours o JOIN v_po_item_latest b ON b.order_sn = o.order_sn
       AND ((o.lx_item_id IS NOT NULL AND b.lx_item_id = o.lx_item_id)
         OR (o.lx_item_id IS NULL AND b.sku = o.sku AND b.wid IS NOT DISTINCT FROM o.wid)))
SELECT 'not_observed' AS problem, o.po_id, o.order_sn, o.line_no, o.sku, o.wid,
       o.units AS ours_units, NULL::int AS lx_units, o.price AS ours_price, NULL::numeric AS lx_price
  FROM ours o WHERE NOT EXISTS (SELECT 1 FROM po_item_obs b WHERE b.order_sn = o.order_sn)
UNION ALL
SELECT 'missing_item', o.po_id, o.order_sn, o.line_no, o.sku, o.wid, o.units, NULL, o.price, NULL
  FROM ours o
 WHERE EXISTS (SELECT 1 FROM po_item_obs b WHERE b.order_sn = o.order_sn)
   AND NOT EXISTS (SELECT 1 FROM pairs p WHERE p.po_id = o.po_id AND p.line_no = o.line_no)
UNION ALL
SELECT 'qty_or_price_differs', p.po_id, p.order_sn, p.line_no, p.sku, p.wid, p.units, p.lx_units, p.price, p.lx_price
  FROM pairs p WHERE p.lx_units <> p.units OR p.lx_price IS DISTINCT FROM p.price
UNION ALL
SELECT 'unexpected_item', po.po_id, b.order_sn, NULL, b.sku, b.wid, NULL, b.quantity_real, NULL, b.price
  FROM v_po_item_latest b JOIN po ON po.order_sn = b.order_sn AND po.state = '已下单'
 WHERE NOT EXISTS (SELECT 1 FROM pairs p WHERE p.order_sn = b.order_sn
                     AND ((p.lx_item_id IS NOT NULL AND p.lx_item_id = b.lx_item_id)
                       OR (p.lx_item_id IS NULL AND p.sku = b.sku AND p.wid IS NOT DISTINCT FROM b.wid)));

-- 汇总给 GET /v1/checks/purchase
CREATE OR REPLACE VIEW v_check_purchase AS
SELECT 'po_source' AS check_name, to_jsonb(v) AS ref FROM v_check_po_source v
UNION ALL SELECT 'line_state',  to_jsonb(v) FROM v_check_line_state v
UNION ALL SELECT 'po_unplanned', to_jsonb(v) FROM v_check_po_unplanned v
UNION ALL SELECT 'po_landed',   to_jsonb(v) FROM v_check_po_landed v;
```

- [ ] **Step 4: 跑测试通过**

Run: `.venv/bin/pytest tests/test_ddl_012_po_qty.py tests/test_migrations.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migrations/pg/012_po_qty_checks.sql tests/test_ddl_012_po_qty.py
git commit -m "feat(b): 迁移 012 —— 作废感知量视图、分店上限最大余数法、v_check_* 一族"
```

---

### Task 6: `rules/purchase.py` —— 纯判据

**Files:**
- Create: `rules/purchase.py`
- Test: `tests/test_rules_purchase.py`

**Interfaces:**
- Produces:
  - `class NoSellerBasis(Exception)`：属性 `sellers: list[str]`
  - `seller_caps(total_units: int, demand_by_seller: dict) -> dict[str, int]`（无依据抛 `NoSellerBasis`）
  - `target_state(total: int, confirmed: int, ordered: int) -> str`
  - `transition_path(current: str, target: str) -> list[tuple[str, str]]`
  - `preflight(po: dict) -> list[dict]`（每项 `{"gate": str, "ok": bool, "detail": dict}`）
  - `po_digest(po: dict) -> str` · `confirm_token(po_id: int, digest: str, salt: str) -> str`

- [ ] **Step 1: 写失败的测试** `tests/test_rules_purchase.py`

```python
import pytest

from rules.purchase import (NoSellerBasis, confirm_token, po_digest, preflight, seller_caps,
                            target_state, transition_path)


def test_caps_sum_to_total_and_match_sql_tie_break():
    caps = seller_caps(24, {"A": {"units": 12, "basis": "human"}, "B": {"units": 11, "basis": "human"}})
    assert caps == {"A": 13, "B": 11}
    # 并列余数按 seller_id 升序拿多出来的那一件（与 012 seller_caps() 一致）
    caps = seller_caps(1, {"B": {"units": 1, "basis": "human"}, "A": {"units": 1, "basis": "human"}})
    assert caps == {"A": 1, "B": 0}


@pytest.mark.parametrize("demand,named", [
    ({}, []),
    ({"A": {"units": 0, "basis": "human"}}, ["A"]),
    ({"A": {"units": 3, "basis": "unknown"}, "B": {"units": 2, "basis": "human"}}, ["A"]),
    ({"A": {"units": None, "basis": "system"}}, ["A"]),
])
def test_no_basis_names_the_sellers(demand, named):
    with pytest.raises(NoSellerBasis) as e:
        seller_caps(10, demand)
    assert e.value.sellers == named


@pytest.mark.parametrize("total,conf,ordd,want", [
    (100, 0, 0, "已提交"), (100, 60, 0, "已提交"), (100, 100, 0, "已确认"),
    (100, 100, 60, "已确认"), (100, 100, 100, "已下单"),
])
def test_target_state(total, conf, ordd, want):
    assert target_state(total, conf, ordd) == want


@pytest.mark.parametrize("cur,tgt,path", [
    ("已提交", "已下单", [("已提交", "已确认"), ("已确认", "已下单")]),
    ("已确认", "已提交", [("已确认", "已提交")]),
    ("已下单", "已提交", [("已下单", "已提交")]),
    ("已下单", "已确认", [("已下单", "已确认")]),
    ("已确认", "已确认", []),
])
def test_transition_path_uses_whitelisted_edges(cur, tgt, path):
    assert transition_path(cur, tgt) == path


def test_preflight_lists_every_failing_gate_at_once():
    gates = preflight({"supplier_id": None, "purchaser_id": 111, "opt_uid": 0, "qc_type": 2,
                       "lines": [{"line_no": 1, "sku": "S", "wid": None, "units": 5, "price": None,
                                  "sources_units": 3, "unplanned_reason": ""}]})
    failed = {g["gate"] for g in gates if not g["ok"]}
    assert failed == {"supplier", "opt_uid", "line_wid", "line_price", "line_unplanned_reason"}


def test_preflight_accepts_price_read_back_as_text():
    gates = preflight({"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
                       "lines": [{"line_no": 1, "sku": "S", "wid": 1, "units": 5, "price": "2.0000",
                                  "sources_units": 5, "unplanned_reason": None}]})
    assert all(g["ok"] for g in gates)


def test_preflight_empty_po_fails():
    gates = preflight({"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2, "lines": []})
    assert [g["gate"] for g in gates if not g["ok"]] == ["lines"]


def test_digest_changes_with_content_and_token_with_digest():
    a = {"supplier_id": "8085", "lines": [{"sku": "S", "units": 5}]}
    b = {"supplier_id": "8085", "lines": [{"sku": "S", "units": 6}]}
    assert po_digest(a) != po_digest(b)
    assert po_digest(a) == po_digest({"lines": [{"units": 5, "sku": "S"}], "supplier_id": "8085"})
    assert confirm_token(1, po_digest(a), "salt") != confirm_token(1, po_digest(b), "salt")
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_rules_purchase.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'rules.purchase'`

- [ ] **Step 3: 实现** `rules/purchase.py`

```python
"""采购组单的纯判据（spec §3.2 §5，PB-D2/D3/D9）。

★ 与迁移 012 的 seller_caps() / v_check_line_state 逐字同口径：
  最大余数法，并列按 seller_id 升序；状态判据照 04:378-380。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

NON_TERMINAL_PURCHASE_STATES = ("已提交", "已确认", "已下单")

#: 白名单里的边（003 + 010）。只描述本模块会走的那几条。
_EDGES = {
    ("已提交", "已确认"), ("已确认", "已下单"),
    ("已确认", "已提交"), ("已下单", "已确认"), ("已下单", "已提交"),
}
_FORWARD = ["已提交", "已确认", "已下单"]


class NoSellerBasis(Exception):
    def __init__(self, sellers: list[str]):
        self.sellers = sellers
        super().__init__(f"期望销量无法作为分店上限依据：{sellers or '（没有任何店）'}")


def seller_caps(total_units: int, demand_by_seller: dict) -> dict[str, int]:
    bad = sorted(s for s, v in demand_by_seller.items()
                 if (v or {}).get("basis") == "unknown" or not isinstance((v or {}).get("units"), int))
    if bad:
        raise NoSellerBasis(bad)
    expected = {s: v["units"] for s, v in demand_by_seller.items()}
    total_expected = sum(expected.values())
    if total_expected == 0:
        raise NoSellerBasis(sorted(expected))
    floors = {s: total_units * e // total_expected for s, e in expected.items()}
    extra = total_units - sum(floors.values())
    by_remainder = sorted(expected, key=lambda s: (-(total_units * expected[s] % total_expected), s))
    for s in by_remainder[:extra]:
        floors[s] += 1
    return floors


def target_state(total: int, confirmed: int, ordered: int) -> str:
    if total > 0 and ordered == total:
        return "已下单"
    if total > 0 and confirmed == total:
        return "已确认"
    return "已提交"


def transition_path(current: str, target: str) -> list[tuple[str, str]]:
    """前进一格一格走（组单 + 下单可连跳，04:370）；退回走直达边。"""
    if current == target:
        return []
    if (current, target) in _EDGES and _FORWARD.index(target) < _FORWARD.index(current):
        return [(current, target)]
    path, at = [], current
    while at != target:
        nxt = _FORWARD[_FORWARD.index(at) + 1]
        path.append((at, nxt))
        at = nxt
    return path


def _gate(name: str, ok: bool, **detail) -> dict:
    return {"gate": name, "ok": bool(ok), "detail": detail}


def _nonneg_number(x) -> bool:
    """单价从库里读回是 numeric::text（'2.0000'），从请求来可能是数字或字符串 —— 统一按 Decimal 判。"""
    try:
        return Decimal(str(x)) >= 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def preflight(po: dict) -> list[dict]:
    """★ 一次返回全部闸（spec §11 G6），不是遇到第一个就停。"""
    gates = [
        _gate("supplier", po.get("supplier_id")),
        _gate("purchaser", po.get("purchaser_id")),
        _gate("opt_uid", po.get("opt_uid")),
        _gate("qc_type", po.get("qc_type") in (0, 1, 2), got=po.get("qc_type")),
        _gate("lines", po.get("lines")),
    ]
    for ln in po.get("lines") or []:
        n = ln.get("line_no")
        gates.append(_gate("line_wid", ln.get("wid"), line_no=n))
        gates.append(_gate("line_price", ln.get("price") is not None and _nonneg_number(ln["price"]), line_no=n))
        gates.append(_gate("line_units", (ln.get("units") or 0) >= (ln.get("sources_units") or 0) > -1,
                           line_no=n, units=ln.get("units"), sources=ln.get("sources_units")))
        unplanned = (ln.get("units") or 0) - (ln.get("sources_units") or 0)
        gates.append(_gate("line_unplanned_reason",
                           unplanned <= 0 or (ln.get("unplanned_reason") or "").strip(),
                           line_no=n, unplanned=max(unplanned, 0)))
    return gates


def po_digest(po: dict) -> str:
    return hashlib.sha256(json.dumps(po, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def confirm_token(po_id: int, digest: str, salt: str) -> str:
    """H-1：派生、不存储、不过期；内容一变 token 就对不上（00:350-357）。"""
    return hashlib.sha256(f"po|{po_id}|{digest}|{salt}".encode()).hexdigest()
```

- [ ] **Step 4: 跑测试通过**

Run: `.venv/bin/pytest tests/test_rules_purchase.py tests/test_layering.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add rules/purchase.py tests/test_rules_purchase.py
git commit -m "feat(b): rules/purchase —— 分店上限、目标状态与迁移路径、preflight、confirm_token"
```

---

### Task 7: 组单 · 改草稿 · 删草稿（repo + usecase + API）

**Files:**
- Create: `repo/purchase.py`, `usecase/line_state.py`, `usecase/draft_po.py`, `api/ui/purchase_orders.py`
- Modify: `api/__init__.py`（挂路由）, `api/ui/errors.py`（约束名翻译）
- Test: `tests/test_api_purchase_orders.py`

**Interfaces:**
- Consumes: `rules.purchase.*`（Task 6）· 表与约束名（Task 3、5）
- Produces:
  - `usecase.draft_po.create_draft(body: dict, actor: str) -> int` · `update_draft(po_id: int, body: dict, actor: str) -> int`（返回新 `draft_version`）· `delete_draft(po_id: int, actor: str) -> None` · `load_po(po_id: int) -> dict`（含 `confirm_token`、`lines[].sources[]`、`attention[]`）
  - `usecase.line_state.reevaluate(cur, line_ids: list[int], actor: str, src: str) -> list[dict]`（返回每条实际发生的迁移 `{"line_id","from","to"}`）
  - `class usecase.draft_po.Conflict(Exception)`：属性 `status:int, error:str, hint:str, fields:dict`（API 层转 `ApiError`）
  - 端点：`POST /v1/purchase-orders` · `GET /v1/purchase-orders` · `GET /v1/purchase-orders/{po_id}` · `PUT /v1/purchase-orders/{po_id}` · `DELETE /v1/purchase-orders/{po_id}`

请求体（POST / PUT 同形）：

```json
{"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2, "wid": null,
 "draft_version": 1,
 "lines": [{"sku": "S1", "wid": 1001, "units": 30, "price": "12.50", "expect_arrive_date": "2026-12-01",
            "unplanned_reason": "起订量",
            "sources": [{"line_id": 7, "seller_id": "11072", "units": 24}]}]}
```

- [ ] **Step 1: 写失败的 API 测试** `tests/test_api_purchase_orders.py`

```python
import json
import threading

import pytest

from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture
def world(seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=24)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 12, "basis": "human"},
                                 "OTHER": {"units": 11, "basis": "human"}}), lid))
    return lid


def body(seed, lid, units=13, line_units=None, reason=None, version=None):
    b = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
         "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": line_units or units, "price": "1.5",
                    "unplanned_reason": reason,
                    "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": units}]}]}
    if version is not None:
        b["draft_version"] = version
    return b


def line_state(lid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id=%s", (lid,))
        return cur.fetchone()[0]


def test_create_draft_and_read_back(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor))
    assert r.status_code == 201, r.text
    po = client.get(f"/v1/purchase-orders/{r.json()['po_id']}").json()
    assert po["state"] == "草稿" and po["custom_order_sn"] == f"SCM-PO-{po['po_id']}"
    assert po["lines"][0]["sources"] == [{"line_id": world, "seller_id": seed.seller_a, "units": 13}]
    assert po["confirm_token"]
    assert line_state(world) == "已提交"      # 13 / 24，未满


def test_over_seller_cap_names_the_numbers(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=14), headers=H(seed.actor))
    assert r.status_code == 409
    assert r.json()["error"] == "over_seller_cap"
    assert {k: r.json()[k] for k in ("line_id", "seller_id", "cap", "used", "requested")} == \
        {"line_id": world, "seller_id": seed.seller_a, "cap": 13, "used": 0, "requested": 14}


def test_no_seller_basis_is_named(client, seed, world):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("UPDATE plan_line SET demand_by_seller='{\"X\":{\"units\":0,\"basis\":\"human\"}}'"
                    " WHERE line_id=%s", (world,))
    r = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "no_seller_basis"
    assert r.json()["sellers"] == ["X"]


def test_unplanned_needs_reason(client, seed, world):
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=13, line_units=20),
                    headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "unplanned_reason_required"
    r = client.post("/v1/purchase-orders", json=body(seed, world, units=13, line_units=20, reason="起订量"),
                    headers=H(seed.actor))
    assert r.status_code == 201


def test_two_drafts_filling_line_moves_it_to_confirmed_and_delete_moves_back(client, seed, world):
    a = client.post("/v1/purchase-orders", json=body(seed, world, units=13), headers=H(seed.actor))
    b2 = body(seed, world, units=11)
    b2["lines"][0]["sources"][0]["seller_id"] = "OTHER"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO seller (seller_id, name, market, has_fba, platform, refreshed_at)"
                    " VALUES ('OTHER','o','US',true,'amazon',now()) ON CONFLICT DO NOTHING")
    b = client.post("/v1/purchase-orders", json=b2, headers=H(seed.actor))
    assert b.status_code == 201, b.text
    assert line_state(world) == "已确认"
    assert client.delete(f"/v1/purchase-orders/{b.json()['po_id']}", headers=H(seed.actor)).status_code == 204
    assert line_state(world) == "已提交"
    assert a.status_code == 201


def test_update_needs_current_draft_version(client, seed, world):
    po_id = client.post("/v1/purchase-orders", json=body(seed, world), headers=H(seed.actor)).json()["po_id"]
    r = client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=10, version=1),
                   headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["draft_version"] == 2
    stale = client.put(f"/v1/purchase-orders/{po_id}", json=body(seed, world, units=9, version=1),
                       headers=H(seed.actor))
    assert stale.status_code == 409 and stale.json()["error"] == "stale_draft"
    assert stale.json()["current_version"] == 2


def test_duplicate_sku_wid_lines_rejected(client, seed, world):
    b = body(seed, world, units=5)
    b["lines"].append(dict(b["lines"][0], sources=[]))
    r = client.post("/v1/purchase-orders", json=b, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "duplicate_line"


def test_concurrent_drafts_cannot_both_take_the_same_quantity(client, seed, world):
    """Review Focus #1：两个人同时组同一条记录，只能一个成功。"""
    results = []

    def go():
        results.append(client.post("/v1/purchase-orders", json=body(seed, world, units=13),
                                   headers=H(seed.actor)).status_code)

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [201, 409]
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_api_purchase_orders.py -q`
Expected: FAIL —— 404（路由不存在）

- [ ] **Step 3: 实现 `repo/purchase.py`**

```python
"""采购单相关 SQL。★ 只收 cursor（G-2）；事务边界在 usecase/。"""
from __future__ import annotations


def lock_plan_lines(cur, line_ids: list[int]) -> dict[int, dict]:
    """★ 按 line_id 升序加锁：并发组单时所有人以同一顺序取锁，不会死锁。"""
    cur.execute("SELECT line_id, state, total_units, demand_by_seller FROM plan_line"
                " WHERE line_id = ANY(%s) ORDER BY line_id FOR UPDATE", (sorted(set(line_ids)),))
    return {r[0]: {"line_id": r[0], "state": r[1], "total_units": r[2], "demand_by_seller": r[3]}
            for r in cur.fetchall()}


def seller_used(cur, line_ids: list[int], exclude_po: int | None) -> dict[tuple[int, str], int]:
    cur.execute(
        "SELECT m.line_id, m.seller_id, sum(m.units)::int FROM po_source_map m"
        " JOIN v_po_effective e ON e.po_id = m.po_id AND NOT e.is_void"
        " WHERE m.line_id = ANY(%s) AND m.po_id IS DISTINCT FROM %s GROUP BY 1, 2",
        (line_ids, exclude_po))
    return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def line_qty(cur, line_ids: list[int]) -> dict[int, dict]:
    cur.execute("SELECT line_id, total_units, state, confirmed_units, ordered_units"
                " FROM v_plan_line_po_qty WHERE line_id = ANY(%s)", (line_ids,))
    return {r[0]: {"total": r[1], "state": r[2], "confirmed": r[3], "ordered": r[4]}
            for r in cur.fetchall()}


def insert_line_event(cur, line_id: int, frm: str, to: str, actor: str, src: str) -> None:
    cur.execute("INSERT INTO plan_line_event (line_id, from_state, to_state, actor, src)"
                " VALUES (%s, %s, %s, %s, %s)", (line_id, frm, to, actor, src))


def insert_po(cur, h: dict, actor: str) -> int:
    cur.execute("INSERT INTO po (supplier_id, purchaser_id, opt_uid, qc_type, wid, created_by)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING po_id",
                (h["supplier_id"], h["purchaser_id"], h["opt_uid"], h["qc_type"], h.get("wid"), actor))
    return cur.fetchone()[0]


def update_po_header(cur, po_id: int, h: dict) -> int:
    cur.execute("UPDATE po SET supplier_id=%s, purchaser_id=%s, opt_uid=%s, qc_type=%s, wid=%s,"
                " draft_version = draft_version + 1, updated_at = now()"
                " WHERE po_id = %s RETURNING draft_version",
                (h["supplier_id"], h["purchaser_id"], h["opt_uid"], h["qc_type"], h.get("wid"), po_id))
    return cur.fetchone()[0]


def lock_po(cur, po_id: int) -> dict | None:
    cur.execute("SELECT po_id, state, draft_version, order_sn, discarded_at FROM po WHERE po_id = %s FOR UPDATE",
                (po_id,))
    r = cur.fetchone()
    return None if r is None else {"po_id": r[0], "state": r[1], "draft_version": r[2], "order_sn": r[3],
                                   "discarded": r[4] is not None}


def has_calls(cur, po_id: int) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM erp_call WHERE po_id = %s)", (po_id,))
    return cur.fetchone()[0]


def discard_po(cur, po_id: int, actor: str) -> None:
    """★ 试过下单的草稿：清明细（映射随 CASCADE 走、额度释放），单头与 erp_call 留档。"""
    cur.execute("DELETE FROM po_line WHERE po_id = %s", (po_id,))
    cur.execute("UPDATE po SET discarded_at = now(), discarded_by = %s, draft_version = draft_version + 1,"
                " updated_at = now() WHERE po_id = %s", (actor, po_id))


def open_attention(cur, po_id: int) -> list[dict]:
    cur.execute("SELECT attention_id, kind, opened_at FROM po_attention"
                " WHERE po_id = %s AND resolved_at IS NULL ORDER BY attention_id", (po_id,))
    return [{"attention_id": r[0], "kind": r[1], "opened_at": r[2].isoformat()} for r in cur.fetchall()]


def in_flight_call(cur, po_id: int) -> int | None:
    cur.execute("SELECT call_id FROM erp_call WHERE po_id = %s AND outcome = 'in_flight'"
                " AND side_effect = 'irreversible'", (po_id,))
    r = cur.fetchone()
    return r[0] if r else None


def source_line_ids(cur, po_id: int) -> list[int]:
    cur.execute("SELECT DISTINCT line_id FROM po_source_map WHERE po_id = %s", (po_id,))
    return [r[0] for r in cur.fetchall()]


def replace_lines(cur, po_id: int, lines: list[dict]) -> None:
    cur.execute("DELETE FROM po_line WHERE po_id = %s", (po_id,))   # 映射随 CASCADE 走
    for n, ln in enumerate(lines, start=1):
        cur.execute("INSERT INTO po_line (po_id, line_no, sku, wid, units, price, expect_arrive_date,"
                    " unplanned_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (po_id, n, ln["sku"], ln["wid"], ln["units"], ln["price"],
                     ln.get("expect_arrive_date"), (ln.get("unplanned_reason") or None)))
        for s in ln["sources"]:
            cur.execute("INSERT INTO po_source_map VALUES (%s, %s, %s, %s, %s)",
                        (po_id, n, s["line_id"], s["seller_id"], s["units"]))


def delete_po(cur, po_id: int) -> None:
    cur.execute("DELETE FROM po WHERE po_id = %s", (po_id,))


def read_po(cur, po_id: int) -> dict | None:
    cur.execute("SELECT po_id, supplier_id, purchaser_id, opt_uid, qc_type, wid, custom_order_sn,"
                " order_sn, state, placed_mode, placed_at, draft_version, created_by, is_void, discarded_at"
                " FROM v_po_effective WHERE po_id = %s", (po_id,))
    r = cur.fetchone()
    if r is None:
        return None
    keys = ("po_id", "supplier_id", "purchaser_id", "opt_uid", "qc_type", "wid", "custom_order_sn",
            "order_sn", "state", "placed_mode", "placed_at", "draft_version", "created_by", "is_void",
            "discarded_at")
    po = dict(zip(keys, r))
    for k in ("placed_at", "discarded_at"):
        po[k] = po[k].isoformat() if po[k] else None
    cur.execute("SELECT line_no, sku, wid, units, price::text, expect_arrive_date, unplanned_reason,"
                " lx_item_id FROM po_line WHERE po_id = %s ORDER BY line_no", (po_id,))
    po["lines"] = [{"line_no": a, "sku": b, "wid": c, "units": d, "price": e,
                    "expect_arrive_date": f.isoformat() if f else None,
                    "unplanned_reason": g, "lx_item_id": h, "sources": []}
                   for a, b, c, d, e, f, g, h in cur.fetchall()]
    cur.execute("SELECT line_no, line_id, seller_id, units FROM po_source_map WHERE po_id = %s"
                " ORDER BY line_no, line_id, seller_id", (po_id,))
    by_no = {ln["line_no"]: ln for ln in po["lines"]}
    for no, lid, sid, u in cur.fetchall():
        by_no[no]["sources"].append({"line_id": lid, "seller_id": sid, "units": u})
    for ln in po["lines"]:
        ln["sources_units"] = sum(s["units"] for s in ln["sources"])
        ln["unplanned_units"] = ln["units"] - ln["sources_units"]
    return po


def list_pos(cur, state: str | None) -> list[dict]:
    cur.execute("SELECT e.po_id, e.supplier_id, s.name, e.state, e.order_sn, e.placed_mode, e.is_void,"
                " (SELECT count(*) FROM po_line l WHERE l.po_id = e.po_id),"
                " (SELECT coalesce(sum(units), 0) FROM po_line l WHERE l.po_id = e.po_id),"
                " (SELECT count(*) FROM po_attention a WHERE a.po_id = e.po_id AND a.resolved_at IS NULL),"
                " e.created_by, e.created_at"
                " FROM v_po_effective e JOIN supplier s USING (supplier_id)"
                " WHERE e.discarded_at IS NULL AND (%s::text IS NULL OR e.state = %s)"
                " ORDER BY e.po_id DESC", (state, state))
    keys = ("po_id", "supplier_id", "supplier_name", "state", "order_sn", "placed_mode", "is_void",
            "lines", "units", "open_attention", "created_by", "created_at")
    out = [dict(zip(keys, r)) for r in cur.fetchall()]
    for o in out:
        o["created_at"] = o["created_at"].isoformat()
    return out
```

- [ ] **Step 4: 实现 `usecase/line_state.py`**

```python
"""计划单记录状态再评估（04 §2.2「终态先判、退回一次、前进到不动点」）。

★ 一处实现，三个调用者：组单 / 改删草稿 / 下单收尾（以及 M3 的回读）。
"""
from __future__ import annotations

import logging

from repo import purchase as repo
from rules.purchase import NON_TERMINAL_PURCHASE_STATES, target_state, transition_path

log = logging.getLogger("scm.usecase")


def reevaluate(cur, line_ids: list[int], actor: str, src: str) -> list[dict]:
    moved = []
    for lid, q in sorted(repo.line_qty(cur, line_ids).items()):
        if q["state"] not in NON_TERMINAL_PURCHASE_STATES:
            continue        # 准备排货及以后归阶段 C / M3；终态不动
        tgt = target_state(q["total"], q["confirmed"], q["ordered"])
        for frm, to in transition_path(q["state"], tgt):
            repo.insert_line_event(cur, lid, frm, to, actor, src)
            moved.append({"line_id": lid, "from": frm, "to": to})
    if moved:
        log.info("op=reevaluate_lines src=%s moved=%s", src, moved)
    return moved
```

- [ ] **Step 5: 实现 `usecase/draft_po.py`**

```python
"""组单 · 改草稿 · 删草稿（spec §5）。取 → 判 → 写，事务在这里。"""
from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation

from repo import purchase as repo
from rules.purchase import NoSellerBasis, confirm_token, po_digest, preflight, seller_caps
from shared.config import erp
from shared.pg_client import pg_conn, timed
from usecase.line_state import reevaluate

log = logging.getLogger("scm.usecase")


class Conflict(Exception):
    def __init__(self, status: int, error: str, hint: str, **fields):
        self.status, self.error, self.hint, self.fields = status, error, hint, fields
        super().__init__(f"{status} {error}: {hint}")


def _normalize(body: dict) -> tuple[dict, list[dict]]:
    missing = [k for k in ("supplier_id", "purchaser_id", "opt_uid", "qc_type") if body.get(k) in (None, "")]
    if missing:
        raise Conflict(400, "missing_required", "单头必填项缺失", missing=missing)
    lines, seen = [], set()
    for ln in body.get("lines") or []:
        key = (ln.get("sku"), ln.get("wid"))
        if key in seen:
            raise Conflict(400, "duplicate_line", "同一张单里同货号同仓只能一行", sku=key[0], wid=key[1])
        seen.add(key)
        try:
            price = Decimal(str(ln.get("price")))
        except (InvalidOperation, TypeError):
            raise Conflict(400, "missing_required", "单价必填且为数字", missing=["lines[].price"],
                           sku=key[0]) from None
        src = [{"line_id": int(s["line_id"]), "seller_id": str(s["seller_id"]), "units": int(s["units"])}
               for s in ln.get("sources") or []]
        units = int(ln.get("units") or 0)
        if units > sum(s["units"] for s in src) and not (ln.get("unplanned_reason") or "").strip():
            raise Conflict(400, "unplanned_reason_required", "超出计划来源的数量必须写理由",
                           sku=key[0], unplanned=units - sum(s["units"] for s in src))
        lines.append({**ln, "price": price, "units": units, "sources": src})
    if not lines:
        raise Conflict(400, "missing_required", "至少一行明细", missing=["lines"])
    return body, lines


def _check_quantities(cur, lines: list[dict], exclude_po: int | None) -> list[int]:
    """Python 侧先判、给出带数字的 409；库层约束（012）兜底。"""
    want: dict[tuple[int, str], int] = {}
    for ln in lines:
        for s in ln["sources"]:
            want[(s["line_id"], s["seller_id"])] = want.get((s["line_id"], s["seller_id"]), 0) + s["units"]
    line_ids = sorted({k[0] for k in want})
    plan_lines = repo.lock_plan_lines(cur, line_ids)
    for lid in line_ids:
        pl = plan_lines.get(lid)
        if pl is None:
            raise Conflict(404, "line_not_found", "没有这条计划单记录", line_id=lid)
        if pl["state"] not in ("已提交", "已确认"):
            raise Conflict(409, "line_not_orderable", "只有 已提交/已确认 的记录能组单",
                           line_id=lid, state=pl["state"])
    used = repo.seller_used(cur, line_ids, exclude_po)
    for lid in line_ids:
        pl = plan_lines[lid]
        try:
            caps = seller_caps(pl["total_units"], pl["demand_by_seller"])
        except NoSellerBasis as e:
            raise Conflict(409, "no_seller_basis", "期望销量为 0 或 unknown 的店无法定分店上限",
                           line_id=lid, sellers=e.sellers) from None
        total_used = sum(v for (l2, _), v in used.items() if l2 == lid)
        total_want = sum(v for (l2, _), v in want.items() if l2 == lid)
        if total_used + total_want > pl["total_units"]:
            raise Conflict(409, "over_total", "超过记录总量", line_id=lid, total=pl["total_units"],
                           used=total_used, requested=total_want)
        for (l2, sid), req in want.items():
            if l2 != lid:
                continue
            cap, u = caps.get(sid, 0), used.get((lid, sid), 0)
            if u + req > cap:
                raise Conflict(409, "over_seller_cap", "超过该店上限（按期望销量占比）",
                               line_id=lid, seller_id=sid, cap=cap, used=u, requested=req)
    return line_ids


def _assert_editable(cur, po_id: int) -> dict:
    po = repo.lock_po(cur, po_id)
    if po is None:
        raise Conflict(404, "po_not_found", "采购单不存在", po_id=po_id)
    if po["discarded"]:
        raise Conflict(409, "po_discarded", "这张草稿已作废", po_id=po_id)
    if po["state"] != "草稿":
        raise Conflict(409, "po_frozen", "采购单已下单，不可改", po_id=po_id, order_sn=po["order_sn"])
    if repo.in_flight_call(cur, po_id):
        raise Conflict(409, "po_placing", "下单请求进行中", po_id=po_id)
    if any(a["kind"] == "needs_verify" for a in repo.open_attention(cur, po_id)):
        raise Conflict(409, "needs_verify", "这张单的下单结果待人工核实，先处理", po_id=po_id,
                       timeline=f"/v1/purchase-orders/{po_id}/timeline")
    return po


def create_draft(body: dict, actor: str) -> int:
    t0 = time.perf_counter()
    header, lines = _normalize(body)
    with timed("create_po", actor=actor), pg_conn() as c, c.cursor() as cur:
        line_ids = _check_quantities(cur, lines, exclude_po=None)
        po_id = repo.insert_po(cur, header, actor)
        repo.replace_lines(cur, po_id, lines)
        moved = reevaluate(cur, line_ids, actor, f"po:{po_id}:create")
    log.info("op=create_po po_id=%s supplier=%s lines=%d sources=%d lines_moved=%s actor=%s ms=%d",
             po_id, header["supplier_id"], len(lines), sum(len(x["sources"]) for x in lines),
             moved, actor, (time.perf_counter() - t0) * 1000)
    return po_id


def update_draft(po_id: int, body: dict, actor: str) -> int:
    header, lines = _normalize(body)
    with timed("update_po", actor=actor, po_id=po_id), pg_conn() as c, c.cursor() as cur:
        po = _assert_editable(cur, po_id)
        if body.get("draft_version") != po["draft_version"]:
            raise Conflict(409, "stale_draft", "草稿已被别人改过，请刷新后再改", po_id=po_id,
                           current_version=po["draft_version"], got=body.get("draft_version"))
        before = repo.source_line_ids(cur, po_id)
        repo.replace_lines(cur, po_id, [])           # 先清，再按新内容判量（不和自己比）
        line_ids = _check_quantities(cur, lines, exclude_po=po_id)
        version = repo.update_po_header(cur, po_id, header)
        repo.replace_lines(cur, po_id, lines)
        moved = reevaluate(cur, sorted(set(before) | set(line_ids)), actor, f"po:{po_id}:update")
    log.info("op=update_po po_id=%s version=%s lines_moved=%s actor=%s", po_id, version, moved, actor)
    return version


def delete_draft(po_id: int, actor: str) -> None:
    with timed("delete_po", actor=actor, po_id=po_id), pg_conn() as c, c.cursor() as cur:
        _assert_editable(cur, po_id)
        before = repo.source_line_ids(cur, po_id)
        repo.lock_plan_lines(cur, before)
        how = "discard" if repo.has_calls(cur, po_id) else "delete"
        if how == "discard":
            repo.discard_po(cur, po_id, actor)     # 有出站留痕：单头留档，额度释放
        else:
            repo.delete_po(cur, po_id)
        moved = reevaluate(cur, before, actor, f"po:{po_id}:{how}")
    log.info("op=delete_po po_id=%s how=%s lines_moved=%s actor=%s", po_id, how, moved, actor)


def load_po(po_id: int) -> dict:
    with timed("read_po", po_id=po_id), pg_conn() as c, c.cursor() as cur:
        po = repo.read_po(cur, po_id)
        if po is None:
            raise Conflict(404, "po_not_found", "采购单不存在", po_id=po_id)
        po["attention"] = repo.open_attention(cur, po_id)
    content = {k: po[k] for k in ("supplier_id", "purchaser_id", "opt_uid", "qc_type", "wid",
                                  "draft_version")}
    content["lines"] = [{k: ln[k] for k in ("sku", "wid", "units", "price", "unplanned_reason", "sources")}
                        for ln in po["lines"]]
    po["preflight"] = preflight(po)
    po["confirm_token"] = confirm_token(po_id, po_digest(content), erp()["confirm_salt"])
    return po
```

- [ ] **Step 6: 实现 `api/ui/purchase_orders.py` 并挂路由**

```python
"""采购单端点（spec §5）。★ 这里只做 HTTP ↔ usecase 的翻译。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from api.ui.deps import actor, actor_optional, declared
from api.ui.errors import ApiError
from shared.pg_client import pg_conn, timed
from repo import purchase as repo
from usecase import draft_po

router = APIRouter()


def _call(fn, *a):
    try:
        return fn(*a)
    except draft_po.Conflict as e:
        raise ApiError(e.status, e.error, e.hint, e.fields) from None


@router.post("/purchase-orders", status_code=201)
def create(body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return {"po_id": _call(draft_po.create_draft, body, who)}


@router.get("/purchase-orders")
def list_(request: Request, who: str | None = Depends(actor_optional)):
    declared(request, "state")
    with timed("list_po", actor=who), pg_conn() as c, c.cursor() as cur:
        return {"purchase_orders": repo.list_pos(cur, request.query_params.get("state"))}


@router.get("/purchase-orders/{po_id}")
def get_(po_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    return _call(draft_po.load_po, po_id)


@router.put("/purchase-orders/{po_id}")
def update(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return {"po_id": po_id, "draft_version": _call(draft_po.update_draft, po_id, body, who)}


@router.delete("/purchase-orders/{po_id}", status_code=204)
def delete(po_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    _call(draft_po.delete_draft, po_id, who)
    return Response(status_code=204)
```

在 `api/__init__.py` 的 `create_app()` 里 `app.include_router(lines.router, prefix="/v1")` 之后加：

```python
    app.include_router(purchase_orders.router, prefix="/v1")
```

并在 import 区把 `purchase_orders` 加进 `from api.ui import …` 那一行。

`api/ui/errors.py::CONSTRAINT_ERRORS` 追加（库层兜底，Python 侧已先判过，这里只在并发越过 Python 判定时出现）：

```python
    "po_over_total": ("over_total", 409, "超过记录总量（并发组单）"),
    "po_over_seller_cap": ("over_seller_cap", 409, "超过该店上限（并发组单）"),
    "po_no_seller_basis": ("no_seller_basis", 409, "期望销量为 0 或 unknown，不可组单"),
    "po_unplanned_reason_required": ("unplanned_reason_required", 400, "超出计划来源的数量必须写理由"),
    "po_line_below_sources": ("line_below_sources", 400, "明细数量少于来源合计"),
    "po_line_one_per_sku_wid": ("duplicate_line", 400, "同一张单里同货号同仓只能一行"),
    "po_frozen": ("po_frozen", 409, "采购单已下单，不可改"),
    "po_source_line_not_orderable": ("line_not_orderable", 409, "只有 已提交/已确认 的记录能组单"),
    "erp_call_one_in_flight_per_po": ("po_placing", 409, "下单请求进行中"),
```

- [ ] **Step 7: 跑测试通过**

Run: `.venv/bin/pytest tests/test_api_purchase_orders.py tests/test_layering.py -q`
Expected: PASS（并发测试：第二个请求在 `lock_plan_lines` 处等第一个提交，随后在 Python 判量处 409）

- [ ] **Step 8: Commit**

```bash
git add repo/purchase.py usecase/line_state.py usecase/draft_po.py api/ui/purchase_orders.py api/__init__.py api/ui/errors.py tests/test_api_purchase_orders.py
git commit -m "feat(b): 组单 / 改 / 删草稿 —— 分店上限 409 点名、行锁防并发、记录状态随量再评估"
```

---

### Task 8: 供应商镜像（CH 采购单行）

**Files:**
- Modify: `dim/ch_source.py`（加 `SQL_SUPPLIER` + `fetch_supplier`）, `dim/registry.py`（`supplier` 条目从 pending 改为可刷新）
- Test: `tests/test_dim_ch_source.py`（追加）, `tests/test_mirror_registry.py`（若有「pending 条目清单」断言则同步）

**Interfaces:**
- Produces: `dim.ch_source.fetch_supplier(query) -> Fetched`，行形状 `(supplier_id: str, name: str)`；`GET /v1/purchase-options` 在 Task 9 读 `supplier` 表

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_dim_ch_source.py`，沿用该文件里给 `fetch_*` 喂假 `query` 的写法）

```python
def test_fetch_supplier_dedups_and_counts_drops():
    import datetime as dt
    from dim.ch_source import fetch_supplier
    day = dt.date(2026, 9, 29)
    raw = [("8085", "01[吉信德宠物用品]", day), ("8086", "02[吉信隆]", day),
           ("", "无名", day), ("22178", "", day)]
    got = fetch_supplier(lambda sql, params=None: raw)
    # ★ 空 supplier_id 丢弃并计数；空 name 不丢，强制成 '' 并以 coerced_empty_name 留痕（_coerce_empty 既有口径）
    assert sorted(got.rows) == [("22178", ""), ("8085", "01[吉信德宠物用品]"), ("8086", "02[吉信隆]")]
    assert got.dropped == 1
    assert got.drop_reasons == {"empty_supplier_id": 1, "coerced_empty_name": 1}
    assert got.source_max_captured == day
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_dim_ch_source.py -q -k supplier`
Expected: FAIL —— `ImportError: cannot import name 'fetch_supplier'`

- [ ] **Step 3: 实现**（追加到 `dim/ch_source.py`）

```python
#: ★ 领星没有独立供应商表（02:485 · 17:87）；源 = 采购单头上的 supplier_id / supplier_name。
#:   覆盖型表，按 order_sn 取最新一行再按 supplier 去重（spec §2：实测 3 家）。
SQL_SUPPLIER = """
SELECT toString(supplier_id) AS supplier_id,
       argMax(supplier_name, _captured_at) AS name,
       max(_captured_date) AS cap
  FROM jxd_raw.lingxing_purchase_order_list
 GROUP BY supplier_id
"""


def fetch_supplier(query: Query) -> Fetched:
    raw = query(SQL_SUPPLIER)
    _nonempty(raw, "lingxing_purchase_order_list")
    rows, reasons, dropped = [], {}, 0
    for sid, name, _cap in raw:
        if not (sid or "").strip() or sid == "0":
            _bump(reasons, "empty_supplier_id")
            dropped += 1
            continue
        rows.append((sid, _coerce_empty(reasons, "name", name)))
    return Fetched(rows, dropped, reasons, max(r[2] for r in raw))
```

`dim/registry.py` 把 `supplier` 条目替换为：

```python
    Mirror(name="supplier", stage="B", kind="refresh", staleness="label_only",
           key_columns=("supplier_id",), columns=("supplier_id", "name"),
           source="jxd_raw.lingxing_purchase_order_list",
           fetch=ch_source.fetch_supplier, coverage=("rows",),
           note="spec 2026-10-07 §3.1：领星无独立供应商表，取采购单头去重（实测 3 家）。"
                "原登记的领星 /erp/sc/data/local_inventory/supplier 接口需凭证，M1 不用。"
                "label_only：陈旧只影响下拉，不拒绝服务。"),
```

- [ ] **Step 4: 跑测试通过**

Run: `.venv/bin/pytest tests/test_dim_ch_source.py tests/test_mirror_registry.py tests/test_mirror_registry_pg.py tests/test_jobs_refresh_dims.py -q`
Expected: PASS（若 `test_mirror_registry*` 断言 pending 清单或 B 阶段条目数，按报错同步期望并注明 spec §3.1）

- [ ] **Step 5: Commit**

```bash
git add dim/ch_source.py dim/registry.py tests/test_dim_ch_source.py tests/test_mirror_registry.py tests/test_mirror_registry_pg.py
git commit -m "feat(b): 供应商镜像接 CH 采购单头（领星无独立供应商表）"
```

---

### Task 9: 候选（`/plan-lines` 派生字段）· `derived_state` · 下单选项

**Files:**
- Modify: `api/ui/lines.py`
- Create: `api/ui/purchase_options.py`（`GET /v1/purchase-options`）
- Test: `tests/test_api_lines_purchase.py`

**Interfaces:**
- Consumes: `rules.purchase.seller_caps / NoSellerBasis`（Task 6）· `v_plan_line_po_qty` · `seller_used` 口径（Task 5、7）
- Produces: `/v1/plan-lines` 每行新增 `confirmed_units, ordered_units, remaining, sellers{sid:{expected,cap,used,remaining}}, blocked`；`excluded` 新增键 `fully_confirmed`（仅 `orderable=1` 时计数）；查询参数新增 `orderable`。`POST /v1/plan-lines/{id}/transition` 目标为 已确认 / 已下单 / 准备排货 → 422 `derived_state`。`GET /v1/purchase-options` → `{"suppliers":[{supplier_id,name}], "purchasers":[{purchaser_id}], "opt_uids":[{opt_uid,name}]}`

- [ ] **Step 1: 写失败的测试** `tests/test_api_lines_purchase.py`

```python
import json

from shared.pg_client import pg_conn
from tests.helpers import H, advance, mint


def _line(seed, total, demand):
    with pg_conn() as c, c.cursor() as cur:
        _, lid = mint(cur, seed.actor, seed.sku_a, total=total)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps(demand), lid))
    return lid


def test_candidates_carry_caps_and_remaining(client, seed):
    lid = _line(seed, 24, {"A": {"units": 12, "basis": "human"}, "B": {"units": 11, "basis": "human"}})
    row = next(r for r in client.get("/v1/plan-lines").json()["lines"] if r["line_id"] == lid)
    assert row["confirmed_units"] == 0 and row["remaining"] == 24
    assert row["sellers"] == {"A": {"expected": 12, "cap": 13, "used": 0, "remaining": 13},
                              "B": {"expected": 11, "cap": 11, "used": 0, "remaining": 11}}
    assert row["blocked"] is None


def test_blocked_line_is_named_up_front(client, seed):
    lid = _line(seed, 24, {"A": {"units": 0, "basis": "human"}})
    row = next(r for r in client.get("/v1/plan-lines").json()["lines"] if r["line_id"] == lid)
    assert row["blocked"] == {"reason": "no_seller_basis", "sellers": ["A"]}


def test_orderable_filter_counts_what_it_drops(client, seed):
    _line(seed, 24, {"A": {"units": 1, "basis": "human"}})
    with pg_conn() as c, c.cursor() as cur:
        _, gone = mint(cur, seed.actor, seed.sku_a, total=5)
        advance(cur, gone, "已撤销", actor=seed.actor)
    body = client.get("/v1/plan-lines?orderable=1").json()
    assert all(r["state"] in ("已提交", "已确认") and r["remaining"] > 0 for r in body["lines"])
    assert body["excluded"]["state"] >= 1


def test_manual_forward_into_derived_state_is_refused(client, seed):
    lid = _line(seed, 24, {"A": {"units": 1, "basis": "human"}})
    r = client.post(f"/v1/plan-lines/{lid}/transition", json={"to_state": "已确认"}, headers=H(seed.actor))
    assert r.status_code == 422 and r.json()["error"] == "derived_state"


def test_purchase_options_lists_suppliers(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01[吉信德]',now())")
    body = client.get("/v1/purchase-options").json()
    assert body["suppliers"] == [{"supplier_id": "8085", "name": "01[吉信德]"}]
```

- [ ] **Step 2: 跑，确认失败**

Run: `.venv/bin/pytest tests/test_api_lines_purchase.py -q`
Expected: FAIL —— `KeyError: 'confirmed_units'`

- [ ] **Step 3: 改 `api/ui/lines.py`**

1. `declared(request, …)` 的参数表加 `"orderable"`。
2. 取 `candidates` 之后、过滤之前插入派生字段（同一个 `cur` 内）：

```python
        ids = [r["line_id"] for r in candidates]
        cur.execute("SELECT line_id, confirmed_units, ordered_units FROM v_plan_line_po_qty"
                    " WHERE line_id = ANY(%s)", (ids,))
        qty = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("SELECT m.line_id, m.seller_id, sum(m.units)::int FROM po_source_map m"
                    " JOIN v_po_effective e ON e.po_id = m.po_id AND NOT e.is_void"
                    " WHERE m.line_id = ANY(%s) GROUP BY 1, 2", (ids,))
        used = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    for r in candidates:
        conf, ordd = qty.get(r["line_id"], (0, 0))
        r["confirmed_units"], r["ordered_units"] = conf, ordd
        r["remaining"] = r["total_units"] - conf
        try:
            caps = seller_caps(r["total_units"], r["demand_by_seller"])
            r["blocked"] = None
        except NoSellerBasis as e:
            caps, r["blocked"] = {}, {"reason": "no_seller_basis", "sellers": e.sellers}
        r["sellers"] = {
            sid: {"expected": (v or {}).get("units"), "cap": caps.get(sid),
                  "used": used.get((r["line_id"], sid), 0),
                  "remaining": None if sid not in caps else caps[sid] - used.get((r["line_id"], sid), 0)}
            for sid, v in r["demand_by_seller"].items()}
```

（把原来的 `with … as cur:` 块收尾缩进调整为让上面最后的 `for` 落在 `with` 之外。）

3. 过滤段加：

```python
    excluded["fully_confirmed"] = 0
    if q.get("orderable") == "1":
        excluded["state"] += sum(1 for r in lines if r["state"] not in ("已提交", "已确认"))
        lines = [r for r in lines if r["state"] in ("已提交", "已确认")]
        excluded["fully_confirmed"] = sum(1 for r in lines if r["remaining"] <= 0)
        lines = [r for r in lines if r["remaining"] > 0]
```

4. 文件顶部 `from rules.purchase import NoSellerBasis, seller_caps`；定义 `DERIVED_STATES = ("已确认", "已下单", "准备排货")`；在 `move_line` 里 `to_state` 取出后立刻：

```python
    if to_state in DERIVED_STATES:
        # ★ C1：状态事实驱动。这几个态由采购量 / 入库推导（spec §5），不许手推
        raise ApiError(422, "derived_state", "这个状态由采购量推导，不能手动推进",
                       {"line_id": line_id, "to": to_state})
```

> 先 `grep -rn "transition" tests/test_api_lines.py`：若阶段 A 有测试手动推「已提交→已确认」，把它改成断言 422 `derived_state`，并在测试里注明 spec §5 / C1。

- [ ] **Step 4: 新建 `api/ui/purchase_options.py` 并挂路由**

```python
"""组单表单的候选项（spec §2 实测：采购员 1 人 · 采购方 2 个 · 供应商 3 家）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from shared.pg_client import pg_conn, timed

router = APIRouter()


@router.get("/purchase-options")
def options(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("purchase_options", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT supplier_id, name FROM supplier ORDER BY supplier_id")
        suppliers = [{"supplier_id": a, "name": b} for a, b in cur.fetchall()]
        cur.execute("SELECT DISTINCT purchaser_id FROM po ORDER BY 1")
        purchasers = [{"purchaser_id": r[0]} for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT opt_uid FROM po ORDER BY 1")
        opt_uids = [{"opt_uid": r[0], "name": None} for r in cur.fetchall()]
    return {"suppliers": suppliers, "purchasers": purchasers, "opt_uids": opt_uids,
            "note": "采购方 / 采购员候选取自本服务已建的单；首单时为空，按领星实测值手填（spec §2）"}
```

`api/__init__.py` 挂 `purchase_options.router`（prefix `/v1`）。

- [ ] **Step 5: 跑测试通过**

Run: `.venv/bin/pytest tests/test_api_lines_purchase.py tests/test_api_lines.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/ui/lines.py api/ui/purchase_options.py api/__init__.py tests/test_api_lines_purchase.py tests/test_api_lines.py
git commit -m "feat(b): 候选带分店上限与剩余量、不可组单提前点名；已确认等派生态禁手推"
```

---

### Task 10: `erp/` 网关 —— 登记表 · 签名 · 两段式传输 · fake / live driver

**Files:**
- Create: `erp/endpoints.py`, `erp/sign.py`, `erp/transport.py`, `erp/auth.py`, `erp/drivers/fake.py`, `erp/drivers/live.py`, `erp/gateway.py`
- Test: `tests/test_erp_sign.py`, `tests/test_erp_transport.py`, `tests/test_erp_gateway.py`

**Interfaces:**
- Produces:
  - `erp.endpoints.Endpoint(name, method, path, side_effect, idem_param, lookup, source)` · `erp.endpoints.get(name) -> Endpoint`（未登记抛 `UnregisteredEndpoint`）
  - `erp.sign.normalize(obj) -> obj`（整数值 float → int、Decimal → int/float）· `erp.sign.sign(params: dict, app_id: str) -> str`
  - `erp.transport.CallResult(outcome, host, path, elapsed_ms, http_status=None, lx_code=None, lx_msg=None, body=None, cause=None)`，`outcome ∈ {"ok","rejected","not_sent","unknown"}` · `erp.transport.post_json(base_url, path, query, body, timeout_s, user_agent, proxy=None) -> CallResult`（`proxy = {"host","port","user","password"}` 或 `None`）· `erp.transport.proxy_from(cfg: dict) -> dict | None` · `erp.transport.cause_chain(exc) -> str`
  - `erp.gateway.call(name: str, params: dict) -> tuple[Endpoint, CallResult]` · `erp.gateway.mode() -> str` · `erp.gateway.redact(params) -> dict`
  - `erp.drivers.fake.FAKE`（进程内单例）：`enqueue(endpoint: str, outcome: str)` · `reset()` · `mutate_item(order_sn, sku, quantity_real=None, price=None)`；`outcome ∈ {"ok","rejected","not_sent","unknown_created","unknown_absent"}`

- [ ] **Step 1: 写失败的签名测试** `tests/test_erp_sign.py`

```python
from decimal import Decimal

from erp.sign import normalize, sign

APP = "0123456789abcdef"
V1 = {"access_token": "tok", "app_key": APP, "timestamp": 1700000000}
V2 = {**V1, "custom_order_sn": "SCM-PO-1",
      "product_list": [{"sku": "S1", "quantity_real": 30, "price": 12.5}], "remark": "中文", "qc_type": 2}


def test_matches_typescript_reference():
    """★ 期望值由兄弟仓 ecops/plugins/global-ecommerce-erp 的 crypto-js + md5 原实现算出
    （generateSign，2026-10-07）。改签名逻辑而这两条不红 = 没测到。"""
    assert sign(V1, APP) == "LG7wNUD5KdYv4wCsPB0nYyRFM/UWQeK7wYyJ07ItaG43ciLgYakkxZHNnCfqFj7U"
    assert sign(V2, APP) == "4rmcdsPlHxe3y7FAj+UixIuk3ov3RX5wf863K1r4dUA3ciLgYakkxZHNnCfqFj7U"


def test_normalize_matches_js_number_printing():
    # JS String(30.0) == "30"；Python str(30.0) == "30.0" —— 不归一就签不对
    assert normalize({"a": 30.0, "b": Decimal("12.50"), "c": [Decimal("7")]}) == {"a": 30, "b": 12.5, "c": [7]}
```

- [ ] **Step 2: 跑，确认失败** —— Run: `.venv/bin/pytest tests/test_erp_sign.py -q` · Expected: `ModuleNotFoundError: No module named 'erp.sign'`

- [ ] **Step 3: 实现 `erp/sign.py`**

```python
"""领星 OpenAPI 签名 —— 移植自 ecops/plugins/global-ecommerce-erp/src/client/lingxing-client.ts
（generateSign + encrypt）。★ 逐字节对照见 tests/test_erp_sign.py 的两条参考值。

签名 = base64(AES-ECB-PKCS7(key=app_id, MD5(sorted "k=v"&…).upper()))；
对象 / 数组按 JSON.stringify（无空格、不转义中文）。
"""
from __future__ import annotations

import base64
import hashlib
import json
from decimal import Decimal

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def normalize(obj):
    """把会让 JS 与 Python 打印不一致的数归一：整数值 float / Decimal → int，其余 Decimal → float。
    ★ 签名与请求体必须用同一份归一后的对象，否则签的不是发的。"""
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    if isinstance(obj, (float, Decimal)) and not isinstance(obj, bool):
        return int(obj) if obj == int(obj) else float(obj)
    return obj


def _text(v) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return str(v)


def sign(params: dict, app_id: str) -> str:
    plain = "&".join(f"{k}={_text(params[k])}" for k in sorted(params))
    digest = hashlib.md5(plain.encode("utf-8")).hexdigest().upper()
    padder = PKCS7(128).padder()
    data = padder.update(digest.strip().encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(app_id.encode("utf-8")), modes.ECB()).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")
```

- [ ] **Step 4: 跑签名测试通过** —— Run: `.venv/bin/pytest tests/test_erp_sign.py -q` · Expected: PASS

- [ ] **Step 5: 写失败的传输测试** `tests/test_erp_transport.py`（★ 用本机真 socket，不手搓异常 —— 同 `tests/helpers.py::make_ch_unreachable` 的理由）

```python
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from erp.transport import post_json


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _serve(handler_body):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            handler_body(self)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_connection_refused_is_not_sent():
    r = post_json(f"http://127.0.0.1:{_free_port()}", "/x", {}, {}, 2, "t")
    assert r.outcome == "not_sent" and "ConnectionRefusedError" in r.cause


def test_read_timeout_after_send_is_unknown():
    def slow(h):
        import time; time.sleep(2)
    srv, base = _serve(slow)
    r = post_json(base, "/x", {}, {"a": 1}, 0.5, "t")
    srv.shutdown()
    assert r.outcome == "unknown" and "timeout" in r.cause.lower()


def test_business_error_is_rejected_with_code():
    def reject(h):
        b = json.dumps({"code": 1001, "message": "供应商不存在"}).encode()
        h.send_response(200); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)
    srv, base = _serve(reject)
    r = post_json(base, "/x", {}, {}, 2, "t")
    srv.shutdown()
    assert (r.outcome, r.lx_code, r.lx_msg) == ("rejected", "1001", "供应商不存在")


def test_5xx_after_send_is_unknown():
    def boom(h):
        h.send_response(502); h.send_header("Content-Length", "0"); h.end_headers()
    srv, base = _serve(boom)
    r = post_json(base, "/x", {}, {}, 2, "t")
    srv.shutdown()
    assert r.outcome == "unknown" and r.http_status == 502


def test_dead_proxy_is_not_sent_and_names_the_proxy():
    """★ 经代理时，连不上代理 / CONNECT 被拒 都发生在 connect() 阶段 —— 领星什么都没收到。"""
    port = _free_port()
    r = post_json("https://openapi.lingxing.com", "/x", {}, {}, 2, "t",
                  proxy={"host": "127.0.0.1", "port": port, "user": "u", "password": "p"})
    assert r.outcome == "not_sent" and f"via proxy 127.0.0.1:{port}" in r.cause


def test_proxy_auth_header_is_basic():
    from erp.transport import _proxy_headers
    assert _proxy_headers({"user": "dwt", "password": "pw"}) == {"Proxy-Authorization": "Basic ZHd0OnB3"}
    assert _proxy_headers({"user": "", "password": ""}) == {}


def test_proxy_from_config():
    from erp.transport import proxy_from
    assert proxy_from({"proxy_host": ""}) is None
    assert proxy_from({"proxy_host": "h", "proxy_port": 3128, "proxy_user": "u", "proxy_password": "p"}) == \
        {"host": "h", "port": 3128, "user": "u", "password": "p"}


def test_ok_and_user_agent_sent():
    seen = {}

    def ok(h):
        seen["ua"] = h.headers["User-Agent"]
        b = json.dumps({"code": 0, "data": {"order_sn": "PO1"}}).encode()
        h.send_response(200); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)
    srv, base = _serve(ok)
    r = post_json(base, "/x", {"q": 1}, {}, 2, "scm-test/1")
    srv.shutdown()
    assert r.outcome == "ok" and r.body["data"]["order_sn"] == "PO1" and seen["ua"] == "scm-test/1"
```

- [ ] **Step 6: 跑，确认失败** —— Run: `.venv/bin/pytest tests/test_erp_transport.py -q` · Expected: `ModuleNotFoundError`

- [ ] **Step 7: 实现 `erp/transport.py`**

```python
"""两段式出站（spec §6.2）：★ 能不能重试的分界是「请求发出去没有」，不是错误叫什么。

connect() 失败 → not_sent（连不上，领星什么都没收到，可重试）
request()/getresponse() 失败、或 5xx → unknown（可能已建单，必须查重，绝不自动重试）
"""
from __future__ import annotations

import base64
import http.client
import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit


@dataclass
class CallResult:
    outcome: str
    host: str
    path: str
    elapsed_ms: int
    http_status: int | None = None
    lx_code: str | None = None
    lx_msg: str | None = None
    body: dict | None = None
    cause: str | None = None


def cause_chain(exc: BaseException) -> str:
    """★ 不只取 str(e)：errno、对端地址都在链上（全局规范判据 1）。"""
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        errno = getattr(exc, "errno", None)
        parts.append(f"{type(exc).__name__}" + (f"[errno={errno}]" if errno else "") + f": {exc}")
        exc = exc.__cause__ or exc.__context__
    return " ← ".join(parts)


def proxy_from(cfg: dict) -> dict | None:
    if not cfg.get("proxy_host"):
        return None
    return {"host": cfg["proxy_host"], "port": int(cfg["proxy_port"]),
            "user": cfg.get("proxy_user", ""), "password": cfg.get("proxy_password", "")}


def _proxy_headers(proxy: dict) -> dict:
    if not proxy.get("user"):
        return {}
    token = base64.b64encode(f"{proxy['user']}:{proxy['password']}".encode()).decode()
    return {"Proxy-Authorization": f"Basic {token}"}


def post_json(base_url: str, path: str, query: dict, body: dict, timeout_s: float,
              user_agent: str, proxy: dict | None = None) -> CallResult:
    """★ 领星有 IP 白名单（tools/nodejs/commerce/lingxing_erp/INDEX.md:45）：live 必须带 proxy。
    经代理时走 CONNECT 隧道 —— 连代理、CONNECT 都在 connect() 里完成，失败仍归 not_sent。"""
    u = urlsplit(base_url)
    https = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if https else 80)
    conn_cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    if proxy:
        conn = conn_cls(proxy["host"], int(proxy["port"]), timeout=timeout_s)
        conn.set_tunnel(host, port, headers=_proxy_headers(proxy))
        where = f"{host}:{port} via proxy {proxy['host']}:{proxy['port']}"
    else:
        conn = conn_cls(host, port, timeout=timeout_s)
        where = f"{host}:{port}"
    t0 = time.perf_counter()

    def ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    try:
        conn.connect()
    except OSError as e:
        conn.close()
        return CallResult("not_sent", host, path, ms(), cause=cause_chain(e) + f" @ {where}")
    try:
        conn.request("POST", f"{path}?{urlencode(query)}" if query else path,
                     body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                     headers={"Content-Type": "application/json", "User-Agent": user_agent})
        resp = conn.getresponse()
        raw = resp.read()
    except (OSError, http.client.HTTPException) as e:
        return CallResult("unknown", host, path, ms(), cause=cause_chain(e) + f" @ {where}")
    finally:
        conn.close()
    if resp.status >= 500:
        return CallResult("unknown", host, path, ms(), http_status=resp.status,
                          cause=f"http_{resp.status}: {raw[:200]!r}")
    try:
        j = json.loads(raw or b"{}")
    except ValueError:
        return CallResult("rejected", host, path, ms(), http_status=resp.status,
                          cause=f"non_json: {raw[:200]!r}")
    code = str(j.get("code"))
    return CallResult("ok" if code in ("0", "200") else "rejected", host, path, ms(),
                      http_status=resp.status, lx_code=code,
                      lx_msg=j.get("message") or j.get("msg"), body=j)
```

- [ ] **Step 8: 跑传输测试通过** —— Run: `.venv/bin/pytest tests/test_erp_transport.py -q` · Expected: PASS

- [ ] **Step 9: 实现登记表、token、两个 driver、网关**

`erp/endpoints.py`：

```python
"""端点登记表（spec §6.1）。★ 副作用等级显式登记，不按路径前缀推断：
createPurchaseOrder 在 /erp/ 下却不可逆（07:351 与 08:242 矛盾）。未登记即拒。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    side_effect: str          # read | reversible | irreversible
    idem_param: str | None
    lookup: str | None
    source: str               # 出处


class UnregisteredEndpoint(Exception):
    pass


_ALL = (
    Endpoint("create_purchase_order", "POST", "/erp/sc/routing/purchase/purchase/createPurchaseOrder",
             "irreversible", "custom_order_sn", "purchase_order_list", "08:242 · 17:48-51"),
    Endpoint("purchase_order_list", "POST", "/erp/sc/routing/data/local_inventory/purchaseOrderList",
             "read", None, None,
             "jxd_data_collector lingxing_node/src/api-client/purchase.ts:31 · 参数 search_field_time /"
             " start_date / end_date / offset / length / order_sns；★ 无 custom_order_sn 过滤，客户端筛"),
)
ENDPOINTS = {e.name: e for e in _ALL}


def get(name: str) -> Endpoint:
    if name not in ENDPOINTS:
        raise UnregisteredEndpoint(f"未登记的领星端点 {name!r}；已登记：{sorted(ENDPOINTS)}")
    return ENDPOINTS[name]
```

`erp/auth.py`：

```python
"""领星 access_token（移植 lingxing-client.ts authenticate；过期前 60 s 重取）。"""
from __future__ import annotations

import logging
import threading
import time

from erp.transport import CallResult, post_json, proxy_from

log = logging.getLogger("scm.erp")
_lock = threading.Lock()
_cache: dict = {}


class AuthFailed(Exception):
    def __init__(self, result: CallResult):
        self.result = result
        super().__init__(f"领星取 token 失败：{result.outcome} {result.lx_code} {result.cause or result.lx_msg}")


def token(cfg: dict) -> str:
    with _lock:
        if _cache.get("token") and _cache["expires_at"] - time.time() > 60:
            return _cache["token"]
        r = post_json(cfg["base_url"], "/api/auth-server/oauth/access-token",
                      {"appId": cfg["app_id"], "appSecret": cfg["app_secret"]}, {},
                      cfg["timeout_s"], cfg["user_agent"], proxy_from(cfg))
        log.info("erp op=access_token host=%s ms=%s outcome=%s lx_code=%s cause=%s",
                 r.host, r.elapsed_ms, r.outcome, r.lx_code, r.cause)
        if r.outcome != "ok" or str(r.lx_code) != "200":
            raise AuthFailed(r)
        d = r.body["data"]
        _cache.update(token=d["access_token"], expires_at=time.time() + int(d["expires_in"]))
        return _cache["token"]
```

`erp/drivers/live.py`：

```python
from __future__ import annotations

import dataclasses
import time

from erp import auth
from erp.endpoints import Endpoint
from erp.sign import normalize, sign
from erp.transport import CallResult, post_json, proxy_from


class LiveDriver:
    mode = "live"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def call(self, ep: Endpoint, params: dict) -> CallResult:
        try:
            tok = auth.token(self.cfg)
        except auth.AuthFailed as e:
            # ★ 业务请求根本没发出去：not_sent，可安全重试
            return dataclasses.replace(e.result, outcome="not_sent", path=ep.path,
                                       cause=f"access_token: {e.result.cause or e.result.lx_msg}")
        body = normalize(params)
        base = {"access_token": tok, "app_key": self.cfg["app_id"], "timestamp": int(time.time())}
        query = {**base, "sign": sign({**base, **body}, self.cfg["app_id"])}
        return post_json(self.cfg["base_url"], ep.path, query, body,
                         self.cfg["timeout_s"], self.cfg["user_agent"], proxy_from(self.cfg))
```

`erp/drivers/fake.py`：

```python
"""fake driver：扮演领星。★ 进程内存储，重启即失（仅开发 / 测试）。
脚本化结果让「结果未知」「业务拒绝」「没发出去」都能在本机复现（spec §11 G2）。"""
from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict, deque

from erp.endpoints import Endpoint
from erp.transport import CallResult

OUTCOMES = ("ok", "rejected", "not_sent", "unknown_created", "unknown_absent")


class FakeDriver:
    mode = "fake"

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self._orders: dict[str, dict] = {}       # order_sn → order
        self._script: dict[str, deque] = defaultdict(deque)
        self._seq = 0

    def enqueue(self, endpoint: str, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"fake outcome 只能是 {OUTCOMES}，得到 {outcome!r}")
        self._script[endpoint].append(outcome)

    def mutate_item(self, order_sn: str, sku: str, quantity_real=None, price=None) -> None:
        """模拟领星侧变更单（实测 3 张）改了落下的数 —— 喂 G1 核对。"""
        for it in self._orders[order_sn]["item_list"]:
            if it["sku"] == sku:
                if quantity_real is not None:
                    it["quantity_real"] = quantity_real
                if price is not None:
                    it["price"] = price

    def call(self, ep: Endpoint, params: dict) -> CallResult:
        with self._lock:
            outcome = self._script[ep.name].popleft() if self._script[ep.name] else "ok"
            if ep.name == "create_purchase_order":
                return self._create(ep, params, outcome)
            if ep.name == "purchase_order_list":
                return self._list(ep, params)
        raise NotImplementedError(f"fake driver 未实现 {ep.name}")

    def _create(self, ep: Endpoint, p: dict, outcome: str) -> CallResult:
        if outcome == "not_sent":
            return CallResult("not_sent", "fake", ep.path, 1, cause="fake: ConnectionRefusedError[errno=111]")
        if outcome == "rejected":
            return CallResult("rejected", "fake", ep.path, 1, http_status=200, lx_code="1001",
                              lx_msg="fake: 供应商不存在", body={"code": 1001, "message": "fake: 供应商不存在"})
        order_sn = None
        if outcome in ("ok", "unknown_created"):
            self._seq += 1
            order_sn = f"FAKE-PO{self._seq:06d}"
            self._orders[order_sn] = {
                "order_sn": order_sn, "custom_order_sn": p["custom_order_sn"],
                "supplier_id": str(p["supplier_id"]), "status": 2,
                "create_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "item_list": [{"item_id": str(9000 + n), "sku": it["sku"], "wid": it.get("wid", p.get("wid")),
                               "quantity_real": it["quantity_real"], "price": it["price"], "is_delete": 0}
                              for n, it in enumerate(p["product_list"], start=1)]}
        if outcome.startswith("unknown"):
            return CallResult("unknown", "fake", ep.path, 30000, cause="fake: TimeoutError: timed out (read)")
        return CallResult("ok", "fake", ep.path, 5, http_status=200, lx_code="0",
                          body={"code": 0, "data": {"order_sn": order_sn}})

    def _list(self, ep: Endpoint, p: dict) -> CallResult:
        rows = list(self._orders.values())
        if p.get("order_sns"):
            wanted = set(str(p["order_sns"]).split(","))
            rows = [r for r in rows if r["order_sn"] in wanted]
        return CallResult("ok", "fake", ep.path, 3, http_status=200, lx_code="0",
                          body={"code": 0, "data": rows, "total": len(rows)})


FAKE = FakeDriver()
```

`erp/gateway.py`：

```python
"""唯一出站口（L5）。★ 不碰库：erp_call 由 usecase/ 在调用前后落（L6）。"""
from __future__ import annotations

import logging

from erp import endpoints
from erp.drivers.fake import FAKE
from erp.drivers.live import LiveDriver
from erp.transport import CallResult
from shared.config import erp as erp_config

log = logging.getLogger("scm.erp")
_SECRET_KEYS = {"access_token", "sign", "appSecret", "app_secret"}


def mode() -> str:
    return erp_config()["mode"]


def _driver():
    cfg = erp_config()
    return FAKE if cfg["mode"] == "fake" else LiveDriver(cfg)


def redact(params: dict) -> dict:
    return {k: ("***" if k in _SECRET_KEYS else v) for k, v in params.items()}


def call(name: str, params: dict) -> tuple[endpoints.Endpoint, CallResult]:
    ep = endpoints.get(name)
    r = _driver().call(ep, params)
    log.log(logging.INFO if r.outcome == "ok" else logging.WARNING,
            "erp op=%s mode=%s host=%s path=%s idem=%s ms=%s outcome=%s http=%s lx_code=%s lx_msg=%s cause=%s",
            name, mode(), r.host, r.path, params.get(ep.idem_param) if ep.idem_param else None,
            r.elapsed_ms, r.outcome, r.http_status, r.lx_code, r.lx_msg, r.cause)
    return ep, r
```

- [ ] **Step 10: 写网关测试** `tests/test_erp_gateway.py`

```python
import pytest

from erp import endpoints, gateway
from erp.drivers.fake import FAKE


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


def _p():
    return {"custom_order_sn": "SCM-PO-1", "supplier_id": 8085, "purchaser_id": 111, "opt_uid": 1,
            "qc_type": 2, "wid": 1001, "product_list": [{"sku": "S1", "quantity_real": 3, "price": 1.5}]}


def test_unregistered_endpoint_refused():
    with pytest.raises(endpoints.UnregisteredEndpoint):
        gateway.call("cancel_purchase_order", {})


def test_create_is_registered_irreversible_with_lookup():
    ep = endpoints.get("create_purchase_order")
    assert (ep.side_effect, ep.idem_param, ep.lookup) == ("irreversible", "custom_order_sn", "purchase_order_list")


def test_fake_unknown_created_is_findable_by_lookup():
    FAKE.enqueue("create_purchase_order", "unknown_created")
    _, r = gateway.call("create_purchase_order", _p())
    assert r.outcome == "unknown"
    _, lst = gateway.call("purchase_order_list", {"search_field_time": "create_time"})
    assert [o["custom_order_sn"] for o in lst.body["data"]] == ["SCM-PO-1"]


def test_fake_unknown_absent_is_not_findable():
    FAKE.enqueue("create_purchase_order", "unknown_absent")
    gateway.call("create_purchase_order", _p())
    _, lst = gateway.call("purchase_order_list", {})
    assert lst.body["data"] == []


def test_redact_hides_secrets():
    assert gateway.redact({"access_token": "t", "sign": "s", "sku": "S1"}) == \
        {"access_token": "***", "sign": "***", "sku": "S1"}
```

- [ ] **Step 11: 跑全部 erp 测试与分层** —— Run: `.venv/bin/pytest tests/test_erp_sign.py tests/test_erp_transport.py tests/test_erp_gateway.py tests/test_layering.py -q` · Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add erp tests/test_erp_sign.py tests/test_erp_transport.py tests/test_erp_gateway.py
git commit -m "feat(erp): 端点登记表、签名移植（TS 参考值对照）、两段式传输分 not_sent/unknown、fake/live driver"
```

---

### Task 11: ⛔ 下单 —— `usecase/place_po.py`

**Files:**
- Create: `usecase/place_po.py`
- Modify: `rules/purchase.py`（加 `create_order_payload`、`extract_order_sn`、`find_by_custom_sn`）, `repo/purchase.py`（加 erp_call / attention / 观察的 SQL）, `api/ui/purchase_orders.py`（`/place`）, `api/__init__.py`（启动时恢复卡死调用）
- Test: `tests/test_rules_purchase.py`（追加）, `tests/test_api_place_po.py`

**Interfaces:**
- Consumes: `erp.gateway.call / mode / redact`（Task 10）· `usecase.draft_po.load_po / Conflict`（Task 7）· `usecase.line_state.reevaluate`（Task 7）
- Produces:
  - `rules.purchase.create_order_payload(po: dict) -> dict` · `extract_order_sn(body: dict) -> str | None` · `find_by_custom_sn(orders: list[dict], custom_sn: str) -> dict | None`
  - `usecase.place_po.place(po_id: int, token: str, actor: str) -> dict`：返回 `{"outcome": "placed"|"rejected"|"not_sent"|"needs_verify", "po_id", "order_sn"?, "erp_call_id", "lx_code"?, "lx_msg"?, "cause"?}`
  - `usecase.place_po.lookup_and_settle(po_id: int, actor: str, opened_by_call: int | None, src: str) -> dict`（Task 12 复用）
  - `usecase.place_po.recover_stuck(now_minutes: int | None = None) -> list[dict]`
  - `usecase.place_po.read_back(cur, po_id: int, order: dict, call_id: int | None) -> None`（落 `po_item_obs` 并回填 `lx_item_id`）
  - `usecase.place_po.call_logged(name: str, params: dict, *, po_id: int, actor: str, retry_of: int | None) -> tuple[int, CallResult]`（读调用留痕）
  - 端点：`POST /v1/purchase-orders/{po_id}/place {"confirm_token"}`

- [ ] **Step 1: 追加 rules 测试**（`tests/test_rules_purchase.py` 末尾）

```python
from decimal import Decimal

from rules.purchase import create_order_payload, extract_order_sn, find_by_custom_sn


def test_payload_carries_idem_key_and_lines():
    po = {"custom_order_sn": "SCM-PO-7", "supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1,
          "qc_type": 2, "lines": [{"sku": "S1", "wid": 1001, "units": 30, "price": "12.5",
                                   "expect_arrive_date": "2026-12-01"}]}
    p = create_order_payload(po)
    assert p["custom_order_sn"] == "SCM-PO-7" and p["supplier_id"] == 8085 and p["wid"] == 1001
    assert p["product_list"] == [{"sku": "S1", "wid": 1001, "quantity_real": 30, "price": Decimal("12.5"),
                                  "expect_arrive_time": "2026-12-01"}]


def test_payload_multi_warehouse_header_wid_is_zero():
    po = {"custom_order_sn": "X", "supplier_id": "8085", "purchaser_id": 1, "opt_uid": 1, "qc_type": 2,
          "lines": [{"sku": "A", "wid": 1, "units": 1, "price": "1", "expect_arrive_date": None},
                    {"sku": "B", "wid": 2, "units": 1, "price": "1", "expect_arrive_date": None}]}
    assert create_order_payload(po)["wid"] == 0      # B7：实测单头 wid=0 是多仓采购的形态


@pytest.mark.parametrize("body,want", [
    ({"data": {"order_sn": "PO1"}}, "PO1"),
    ({"data": [{"order_sn": "PO2"}]}, "PO2"),
    ({"data": {}}, None), ({}, None),
])
def test_extract_order_sn_shapes(body, want):
    assert extract_order_sn(body) == want


def test_find_by_custom_sn_ignores_others():
    rows = [{"order_sn": "PO1", "custom_order_sn": "PO1"}, {"order_sn": "PO2", "custom_order_sn": "SCM-PO-3"}]
    assert find_by_custom_sn(rows, "SCM-PO-3")["order_sn"] == "PO2"
    assert find_by_custom_sn(rows, "SCM-PO-4") is None
```

- [ ] **Step 2: 实现这三个函数**（追加到 `rules/purchase.py`）

```python
def create_order_payload(po: dict) -> dict:
    """createPurchaseOrder 入参（17:50-51 · 06:186-190）。
    ★ 多仓：单头 wid=0、仓在行上（B7，实测 55/143）。行上仓的入参名首单实调确认（spec §6.7）。
    ★ price 转 Decimal：签名前由 erp.sign.normalize 统一成 JS 同款的数字打印（2.0000 → 2）。"""
    wids = {ln["wid"] for ln in po["lines"]}
    out = {"custom_order_sn": po["custom_order_sn"],
           "supplier_id": int(po["supplier_id"]) if str(po["supplier_id"]).isdigit() else po["supplier_id"],
           "purchaser_id": po["purchaser_id"], "opt_uid": po["opt_uid"], "qc_type": po["qc_type"],
           "wid": next(iter(wids)) if len(wids) == 1 else 0,
           "product_list": []}
    for ln in po["lines"]:
        item = {"sku": ln["sku"], "wid": ln["wid"], "quantity_real": ln["units"], "price": Decimal(str(ln["price"]))}
        if ln.get("expect_arrive_date"):
            item["expect_arrive_time"] = ln["expect_arrive_date"]
        out["product_list"].append(item)
    return out


def extract_order_sn(body: dict) -> str | None:
    """★ 响应形状未实测（spec §6.7）：认得出就用，认不出返回 None 交给查重，不猜。"""
    d = (body or {}).get("data")
    if isinstance(d, dict):
        return d.get("order_sn") or None
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d[0].get("order_sn") or None
    return None


def find_by_custom_sn(orders: list[dict], custom_sn: str) -> dict | None:
    hits = [o for o in orders if str(o.get("custom_order_sn")) == custom_sn]
    return hits[0] if hits else None
```

（`tests/test_rules_purchase.py` 顶部 import 补 `import pytest` 若尚无。）

- [ ] **Step 3: 写失败的下单 API 测试** `tests/test_api_place_po.py`

```python
import json

import pytest

from erp.drivers.fake import FAKE
from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


@pytest.fixture
def draft(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": 10, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": 10}]}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    return po_id, lid


def _place(client, seed, po_id, token=None):
    token = token or client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    return client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": token},
                       headers=H(seed.actor))


def _line_state(lid):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM plan_line WHERE line_id=%s", (lid,))
        return cur.fetchone()[0]


def _calls(po_id):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT endpoint, outcome, retry_of IS NOT NULL FROM erp_call WHERE po_id=%s ORDER BY call_id",
                    (po_id,))
        return cur.fetchall()


def test_ok_freezes_po_and_moves_line_to_ordered(client, seed, draft):
    po_id, lid = draft
    r = _place(client, seed, po_id)
    assert r.status_code == 200 and r.json()["outcome"] == "placed"
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["state"] == "已下单" and po["order_sn"].startswith("FAKE-PO") and po["placed_mode"] == "fake"
    assert po["lines"][0]["lx_item_id"]                     # 读回首检回填了行号
    assert _line_state(lid) == "已下单"
    assert _calls(po_id) == [("create_purchase_order", "ok", False), ("purchase_order_list", "ok", False)]


def test_stale_token_refused(client, seed, draft):
    po_id, _ = draft
    r = _place(client, seed, po_id, token="nope")
    assert r.status_code == 409 and r.json()["error"] == "stale_confirm_token"


def test_rejected_keeps_draft_and_echoes_lingxing(client, seed, draft):
    po_id, lid = draft
    FAKE.enqueue("create_purchase_order", "rejected")
    r = _place(client, seed, po_id)
    assert r.json() | {"erp_call_id": None} == {"outcome": "rejected", "po_id": po_id, "lx_code": "1001",
                                                "lx_msg": "fake: 供应商不存在", "erp_call_id": None}
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["state"] == "草稿"
    assert _line_state(lid) == "已确认"


def test_delete_after_attempt_discards_and_keeps_evidence(client, seed, draft):
    po_id, lid = draft
    FAKE.enqueue("create_purchase_order", "rejected")
    _place(client, seed, po_id)
    assert client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor)).status_code == 204
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["discarded_at"] and po["lines"] == []
    assert _line_state(lid) == "已提交"                    # 额度释放
    assert _calls(po_id) == [("create_purchase_order", "rejected", False)]   # 证据还在


def test_retry_after_not_sent_is_chained(client, seed, draft):
    po_id, _ = draft
    FAKE.enqueue("create_purchase_order", "not_sent")
    _place(client, seed, po_id)
    _place(client, seed, po_id)
    creates = [c for c in _calls(po_id) if c[0] == "create_purchase_order"]
    assert creates == [("create_purchase_order", "not_sent", False), ("create_purchase_order", "ok", True)]


def test_not_sent_is_safe_to_retry(client, seed, draft):
    po_id, _ = draft
    FAKE.enqueue("create_purchase_order", "not_sent")
    assert _place(client, seed, po_id).json()["outcome"] == "not_sent"
    assert _place(client, seed, po_id).json()["outcome"] == "placed"


def test_unknown_but_created_is_found_by_lookup(client, seed, draft):
    """Review Focus #2：发出去了没回音，但领星建了单 —— 查重找回，不重下。"""
    po_id, lid = draft
    FAKE.enqueue("create_purchase_order", "unknown_created")
    r = _place(client, seed, po_id)
    assert r.json()["outcome"] == "placed" and r.json()["found_by"] == "lookup"
    assert _line_state(lid) == "已下单"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1


def test_unknown_absent_goes_to_needs_verify_and_blocks_everything(client, seed, draft):
    """Review Focus #2 / #4：查不到 → 转人工；此后改 / 删 / 再下单一律 409 needs_verify。"""
    po_id, lid = draft
    FAKE.enqueue("create_purchase_order", "unknown_absent")
    r = _place(client, seed, po_id)
    assert r.json()["outcome"] == "needs_verify"
    po = client.get(f"/v1/purchase-orders/{po_id}").json()
    assert po["state"] == "草稿" and [a["kind"] for a in po["attention"]] == ["needs_verify"]
    again = _place(client, seed, po_id)
    assert again.status_code == 409 and again.json()["error"] == "needs_verify"
    assert client.delete(f"/v1/purchase-orders/{po_id}", headers=H(seed.actor)).json()["error"] == "needs_verify"
    assert [c[0] for c in _calls(po_id)].count("create_purchase_order") == 1


def test_stuck_in_flight_is_recovered_by_lookup(client, seed, draft):
    """Review Focus #3：领星成功、本地还没记账就被杀 —— 启动恢复按幂等键找回。"""
    from usecase import place_po
    po_id, lid = draft
    # 造现场：领星侧已有单，本地只有一条 in_flight
    FAKE.enqueue("create_purchase_order", "unknown_created")
    from erp import gateway
    gateway.call("create_purchase_order", {"custom_order_sn": f"SCM-PO-{po_id}", "supplier_id": 8085,
                                           "purchaser_id": 111, "opt_uid": 1, "qc_type": 2, "wid": seed.wid,
                                           "product_list": [{"sku": seed.sku_a, "wid": seed.wid,
                                                             "quantity_real": 10, "price": "2"}]})
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO erp_call (endpoint, side_effect, mode, po_id, idem_key, actor, request,"
                    " started_at) VALUES ('create_purchase_order','irreversible','fake',%s,%s,%s,'{}',"
                    " now() - interval '1 hour')", (po_id, f"SCM-PO-{po_id}", seed.actor))
    out = place_po.recover_stuck()
    assert [o["po_id"] for o in out] == [po_id]
    assert client.get(f"/v1/purchase-orders/{po_id}").json()["state"] == "已下单"
    assert _line_state(lid) == "已下单"
```

- [ ] **Step 4: 跑，确认失败** —— Run: `.venv/bin/pytest tests/test_api_place_po.py -q` · Expected: 404（`/place` 不存在）

- [ ] **Step 5: 在 `repo/purchase.py` 追加 SQL**

```python
def insert_call(cur, *, endpoint: str, side_effect: str, mode: str, po_id: int | None, idem_key: str | None,
                actor: str, request: dict, retry_of: int | None = None) -> int:
    import json
    cur.execute("INSERT INTO erp_call (endpoint, side_effect, mode, po_id, idem_key, actor, request, retry_of)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING call_id",
                (endpoint, side_effect, mode, po_id, idem_key, actor,
                 json.dumps(request, ensure_ascii=False, default=str), retry_of))
    return cur.fetchone()[0]


def close_call(cur, call_id: int, r) -> None:
    import json
    cur.execute("UPDATE erp_call SET outcome=%s, http_status=%s, lx_code=%s, lx_msg=%s, cause=%s,"
                " response=%s::jsonb, host=%s, path=%s, elapsed_ms=%s, finished_at=now() WHERE call_id=%s",
                (r.outcome, r.http_status, r.lx_code, r.lx_msg, r.cause,
                 json.dumps(r.body, ensure_ascii=False, default=str) if r.body is not None else None,
                 r.host, r.path, r.elapsed_ms, call_id))


def mark_placed(cur, po_id: int, order_sn: str, mode: str, actor: str, src: str) -> None:
    cur.execute("UPDATE po SET order_sn=%s, placed_mode=%s, placed_at=now() WHERE po_id=%s",
                (order_sn, mode, po_id))
    cur.execute("INSERT INTO po_event (po_id, from_state, to_state, actor, src)"
                " VALUES (%s, '草稿', '已下单', %s, %s)", (po_id, actor, src))


def open_attention_row(cur, po_id: int, kind: str, call_id: int | None, detail: dict) -> int | None:
    import json
    cur.execute("INSERT INTO po_attention (po_id, kind, opened_by_call, detail) VALUES (%s, %s, %s, %s::jsonb)"
                " ON CONFLICT (po_id, kind) WHERE resolved_at IS NULL DO NOTHING RETURNING attention_id",
                (po_id, kind, call_id, json.dumps(detail, ensure_ascii=False, default=str)))
    r = cur.fetchone()
    return r[0] if r else None


def resolve_attention(cur, po_id: int, kind: str, resolution: str, actor: str, reason: str | None) -> int:
    cur.execute("UPDATE po_attention SET resolved_at=now(), resolution=%s, resolved_by=%s, reason=%s"
                " WHERE po_id=%s AND kind=%s AND resolved_at IS NULL",
                (resolution, actor, reason, po_id, kind))
    return cur.rowcount


def last_create_call(cur, po_id: int) -> int | None:
    cur.execute("SELECT max(call_id) FROM erp_call WHERE po_id = %s AND endpoint = 'create_purchase_order'",
                (po_id,))
    return cur.fetchone()[0]


def stuck_calls(cur, minutes: int) -> list[tuple[int, int]]:
    cur.execute("SELECT call_id, po_id FROM erp_call WHERE outcome='in_flight' AND side_effect='irreversible'"
                " AND started_at < now() - make_interval(mins => %s) ORDER BY call_id", (minutes,))
    return cur.fetchall()


def insert_item_obs(cur, order_sn: str, items: list[dict], source: str, call_id: int | None) -> int:
    n = 0
    for it in items:
        if str(it.get("is_delete", 0)) not in ("0", "False", "false"):
            continue
        cur.execute("INSERT INTO po_item_obs (order_sn, lx_item_id, sku, wid, quantity_real, price, source,"
                    " source_captured_at, call_id) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)",
                    (order_sn, str(it.get("item_id")) if it.get("item_id") is not None else None,
                     it["sku"], it.get("wid"), int(it["quantity_real"]), it.get("price"), source, call_id))
        n += 1
    return n


def backfill_item_ids(cur, po_id: int, items: list[dict]) -> int:
    n = 0
    for it in items:
        cur.execute("UPDATE po_line SET lx_item_id=%s WHERE po_id=%s AND sku=%s AND wid IS NOT DISTINCT FROM %s"
                    " AND lx_item_id IS NULL", (str(it.get("item_id")), po_id, it["sku"], it.get("wid")))
        n += cur.rowcount
    return n
```

- [ ] **Step 6: 实现 `usecase/place_po.py`**

```python
"""⛔ 下单（spec §6.2~§6.4）。

事务①：锁草稿 → 校验 token / 无待核实 → 落 erp_call(in_flight) → COMMIT
HTTP：createPurchaseOrder（★ 不持锁跨 HTTP；in_flight 唯一索引就是锁）
事务②：按结果收尾。结果未知 → 立即按幂等键查重；查不到 → 待人工核实，绝不自动重试。
"""
from __future__ import annotations

import datetime as dt
import logging

from erp import gateway
from erp.endpoints import get as get_endpoint
from repo import purchase as repo
from rules.purchase import create_order_payload, extract_order_sn, find_by_custom_sn
from shared.config import erp as erp_config
from shared.pg_client import pg_conn, timed
from usecase.draft_po import Conflict, load_po
from usecase.line_state import reevaluate

log = logging.getLogger("scm.usecase")


def _lookup_params(day: dt.date) -> dict:
    # ★ 无 custom_order_sn 过滤（erp/endpoints.py 出处）：按建单日前后各一天取，客户端筛
    return {"search_field_time": "create_time", "start_date": (day - dt.timedelta(days=1)).isoformat(),
            "end_date": (day + dt.timedelta(days=1)).isoformat(), "offset": 0, "length": 500}


def _obs_source() -> str:
    return "api" if gateway.mode() == "live" else "sim"


def read_back(cur, po_id: int, order: dict, call_id: int | None) -> None:
    """落地首检的数据源（spec §11 G1）：领星明细进 po_item_obs，行号回填 po_line。"""
    items = order.get("item_list") or []
    n = repo.insert_item_obs(cur, order["order_sn"], items, _obs_source(), call_id)
    m = repo.backfill_item_ids(cur, po_id, items)
    log.info("op=po_read_back po_id=%s order_sn=%s items_obs=%s item_ids_backfilled=%s",
             po_id, order["order_sn"], n, m)


def call_logged(name: str, params: dict, *, po_id: int, actor: str,
                retry_of: int | None) -> tuple[int, object]:
    """读调用：落 erp_call → 调 → 收尾。一个连接一个事务，失败也留痕（Task 12 / 13 复用）。"""
    with pg_conn() as c, c.cursor() as cur:
        ep = get_endpoint(name)
        cid = repo.insert_call(cur, endpoint=name, side_effect=ep.side_effect, mode=gateway.mode(),
                               po_id=po_id, idem_key=None, actor=actor, request=gateway.redact(params),
                               retry_of=retry_of)
    _, r = gateway.call(name, params)
    with pg_conn() as c, c.cursor() as cur:
        repo.close_call(cur, cid, r)
    return cid, r


def lookup_and_settle(po_id: int, actor: str, opened_by_call: int | None, src: str) -> dict:
    """按幂等键查领星；找到 → 补记已下单；找不到 → 开（或保持）待人工核实。"""
    po = load_po(po_id)
    day = dt.date.today()
    cid, r = call_logged("purchase_order_list", _lookup_params(day), po_id=po_id, actor=actor,
                          retry_of=opened_by_call)
    hit = find_by_custom_sn((r.body or {}).get("data") or [], po["custom_order_sn"]) if r.outcome == "ok" else None
    with timed("po_settle", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["state"] == "已下单":
            # 别的路径（启动恢复 / 另一次核对）已经记上了：只收掉待处理，不重复记账
            repo.resolve_attention(cur, po_id, "needs_verify", "rechecked_found", actor, None)
            return {"outcome": "placed", "po_id": po_id, "order_sn": locked["order_sn"],
                    "erp_call_id": cid, "found_by": "already_recorded"}
        if hit:
            repo.mark_placed(cur, po_id, hit["order_sn"], gateway.mode(), actor, src)
            read_back(cur, po_id, hit, cid)
            repo.resolve_attention(cur, po_id, "needs_verify", "rechecked_found", actor, None)
            moved = reevaluate(cur, repo.source_line_ids(cur, po_id), actor, f"po:{po_id}:{src}")
            log.warning("op=po_found_by_lookup po_id=%s order_sn=%s lookup_call=%s moved=%s",
                        po_id, hit["order_sn"], cid, moved)
            return {"outcome": "placed", "po_id": po_id, "order_sn": hit["order_sn"],
                    "erp_call_id": cid, "found_by": "lookup"}
        detail = {"lookup_call": cid, "lookup_outcome": r.outcome, "searched": _lookup_params(day),
                  "custom_order_sn": po["custom_order_sn"],
                  "known": "请求已发到领星，没有收到明确结果",
                  "unknown": "领星是否已建单（列表可能尚未刷新）"}
        repo.open_attention_row(cur, po_id, "needs_verify", opened_by_call, detail)
    log.warning("op=po_needs_verify po_id=%s custom_order_sn=%s lookup_call=%s lookup_outcome=%s",
                po_id, po["custom_order_sn"], cid, r.outcome)
    return {"outcome": "needs_verify", "po_id": po_id, "erp_call_id": opened_by_call,
            "lookup_call_id": cid, "timeline": f"/v1/purchase-orders/{po_id}/timeline"}


def place(po_id: int, token: str, actor: str) -> dict:
    po = load_po(po_id)
    if token != po["confirm_token"]:
        raise Conflict(409, "stale_confirm_token", "采购单内容已变，请刷新后重新确认", po_id=po_id)
    failed = [g for g in po["preflight"] if not g["ok"]]
    if failed:
        raise Conflict(409, "preflight_failed", "下单前检查未通过", po_id=po_id, failed=failed)
    payload = create_order_payload(po)

    # 事务①
    with timed("po_place_begin", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        locked = repo.lock_po(cur, po_id)
        if locked["state"] != "草稿":
            raise Conflict(409, "po_frozen", "采购单已下单", po_id=po_id, order_sn=locked["order_sn"])
        if any(a["kind"] == "needs_verify" for a in repo.open_attention(cur, po_id)):
            raise Conflict(409, "needs_verify", "这张单的下单结果待人工核实，先处理", po_id=po_id,
                           timeline=f"/v1/purchase-orders/{po_id}/timeline")
        if locked["draft_version"] != po["draft_version"]:
            raise Conflict(409, "stale_confirm_token", "采购单内容已变，请刷新后重新确认", po_id=po_id)
        call_id = repo.insert_call(cur, endpoint="create_purchase_order", side_effect="irreversible",
                                   mode=gateway.mode(), po_id=po_id, idem_key=po["custom_order_sn"],
                                   actor=actor, request=gateway.redact(payload),
                                   retry_of=repo.last_create_call(cur, po_id))   # ★ 重下单串成一条链

    # HTTP（不持任何锁）
    _, r = gateway.call("create_purchase_order", payload)

    # 事务②
    with timed("po_place_settle", po_id=po_id, outcome=r.outcome), pg_conn() as c, c.cursor() as cur:
        repo.close_call(cur, call_id, r)
    if r.outcome == "rejected":
        return {"outcome": "rejected", "po_id": po_id, "lx_code": r.lx_code, "lx_msg": r.lx_msg,
                "erp_call_id": call_id}
    if r.outcome == "not_sent":
        return {"outcome": "not_sent", "po_id": po_id, "cause": r.cause, "erp_call_id": call_id,
                "hint": "请求没有发到领星，可以直接重试"}
    order_sn = extract_order_sn(r.body) if r.outcome == "ok" else None
    if r.outcome == "ok" and order_sn:
        with pg_conn() as c, c.cursor() as cur:
            repo.mark_placed(cur, po_id, order_sn, gateway.mode(), actor, f"place:{call_id}")
            moved = reevaluate(cur, repo.source_line_ids(cur, po_id), actor, f"po:{po_id}:place")
        log.info("op=po_placed po_id=%s order_sn=%s call=%s moved=%s", po_id, order_sn, call_id, moved)
        _first_check(po_id, order_sn, actor, call_id)
        return {"outcome": "placed", "po_id": po_id, "order_sn": order_sn, "erp_call_id": call_id}
    # 结果未知，或成功却认不出单号 → 查重
    return lookup_and_settle(po_id, actor, call_id, f"lookup_after:{call_id}")


def _first_check(po_id: int, order_sn: str, actor: str, call_id: int) -> None:
    """下单成功后立即读回一次（spec §11 G1 首检）。读不回不影响已下单，但会在 v_check_po_landed 里亮。"""
    cid, r = call_logged("purchase_order_list", {"order_sns": order_sn, "offset": 0, "length": 50},
                          po_id=po_id, actor=actor, retry_of=None)
    rows = [o for o in ((r.body or {}).get("data") or []) if o.get("order_sn") == order_sn]
    if not rows:
        log.warning("op=po_first_check po_id=%s order_sn=%s outcome=%s found=0", po_id, order_sn, r.outcome)
        return
    with pg_conn() as c, c.cursor() as cur:
        read_back(cur, po_id, rows[0], cid)


def recover_stuck(minutes: int | None = None) -> list[dict]:
    """断点续跑（spec §6.3）：in_flight 超过 V5 分钟的不可逆调用 → 按幂等键查重收尾。"""
    minutes = minutes if minutes is not None else int(erp_config()["stale_in_flight_minutes"])
    with pg_conn() as c, c.cursor() as cur:
        stuck = repo.stuck_calls(cur, minutes)
    out = []
    for call_id, po_id in stuck:
        from erp.transport import CallResult
        with pg_conn() as c, c.cursor() as cur:
            repo.close_call(cur, call_id, CallResult("unknown", "-", "-", 0,
                                                     cause=f"recovered: in_flight > {minutes} min（进程中断）"))
        res = lookup_and_settle(po_id, "system", call_id, f"recover:{call_id}")
        log.warning("op=recover_stuck call=%s po_id=%s result=%s", call_id, po_id, res["outcome"])
        out.append(res)
    return out
```

- [ ] **Step 7: 挂 `/place` 与启动恢复**

`api/ui/purchase_orders.py` 追加：

```python
from usecase import place_po


@router.post("/purchase-orders/{po_id}/place")
def place(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(place_po.place, po_id, (body or {}).get("confirm_token") or "", who)
```

`api/__init__.py::_lifespan` 在 `_startup_check()` 之后加（★ 不吞异常以外的失败：恢复失败只告警，不阻塞启动，同 I-1）：

```python
        try:
            recovered = place_po.recover_stuck()
            if recovered:
                log.warning("startup recover_stuck n=%d results=%s", len(recovered), recovered)
        except Exception as e:  # noqa: BLE001 —— 恢复失败不拖垮启动，但必须首屏可见
            log.error("startup recover_stuck outcome=fail err_type=%s err=%s", type(e).__name__, e)
```

- [ ] **Step 8: 跑测试通过**

Run: `.venv/bin/pytest tests/test_rules_purchase.py tests/test_api_place_po.py tests/test_api_purchase_orders.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add usecase/place_po.py rules/purchase.py repo/purchase.py api/ui/purchase_orders.py api/__init__.py tests/test_rules_purchase.py tests/test_api_place_po.py
git commit -m "feat(b): ⛔ 下单 —— in_flight 唯一锁、结果未知立即查重、查不到转人工、卡死调用启动恢复、下单后首检读回"
```

---

### Task 12: 转人工 —— 时间线 · 重新核对 · 登记已有单 · 确认无此单 · 队列

**Files:**
- Create: `usecase/attention.py`, `api/ui/attention.py`
- Modify: `api/ui/purchase_orders.py`, `api/__init__.py`
- Test: `tests/test_api_attention.py`

**Interfaces:**
- Consumes: `place_po.lookup_and_settle / read_back`（Task 11）· `repo.resolve_attention`（Task 11）
- Produces:
  - `usecase.attention.timeline(po_id) -> dict`：`{"po_id","conclusion":{"known","unknown"}|None,"attempts":[{call_id, at, actor, endpoint, mode, host, path, idem_key, elapsed_ms, outcome, http_status, lx_code, lx_msg, cause, retry_of, request, response}], "events":[…po_event], "attention":[…]}`
  - `recheck(po_id, actor) -> dict` · `register_existing(po_id, order_sn, actor) -> dict` · `confirm_absent(po_id, reason, actor) -> dict` · `queue() -> list[dict]`
  - 端点：`GET /v1/purchase-orders/{id}/timeline` · `POST …/recheck` · `POST …/register-existing {"order_sn"}` · `POST …/confirm-absent {"reason"}` · `GET /v1/attention`

- [ ] **Step 1: 写失败的测试** `tests/test_api_attention.py`

```python
import json

import pytest

from erp.drivers.fake import FAKE
from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


@pytest.fixture
def stuck(client, seed):
    """一张进了「待人工核实」的单。"""
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": 10, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": 10}]}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    FAKE.enqueue("create_purchase_order", "unknown_absent")
    assert client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok},
                       headers=H(seed.actor)).json()["outcome"] == "needs_verify"
    return po_id, lid


def test_timeline_says_what_is_known_and_unknown(client, stuck):
    po_id, _ = stuck
    t = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()
    assert t["conclusion"]["known"] and t["conclusion"]["unknown"]
    a = t["attempts"][0]
    assert (a["endpoint"], a["outcome"], a["mode"]) == ("create_purchase_order", "unknown", "fake")
    assert "TimeoutError" in a["cause"] and a["idem_key"] == f"SCM-PO-{po_id}"
    assert t["attempts"][1]["endpoint"] == "purchase_order_list"


def test_queue_lists_open_items(client, stuck):
    po_id, _ = stuck
    q = client.get("/v1/attention").json()["items"]
    assert [(i["po_id"], i["kind"]) for i in q] == [(po_id, "needs_verify")]


def test_register_existing_verifies_before_accepting(client, seed, stuck):
    po_id, lid = stuck
    # 领星里其实有一张别人的单
    from erp import gateway
    gateway.call("create_purchase_order", {"custom_order_sn": "SOMEONE-ELSE", "supplier_id": 8085,
                                           "purchaser_id": 1, "opt_uid": 1, "qc_type": 2, "wid": seed.wid,
                                           "product_list": [{"sku": seed.sku_a, "wid": seed.wid,
                                                             "quantity_real": 10, "price": "2"}]})
    r = client.post(f"/v1/purchase-orders/{po_id}/register-existing", json={"order_sn": "FAKE-PO000001"},
                    headers=H(seed.actor))
    assert r.status_code == 409 and r.json()["error"] == "order_mismatch"
    assert r.json()["mismatch"]["custom_order_sn"] == {"ours": f"SCM-PO-{po_id}", "lingxing": "SOMEONE-ELSE"}


def test_confirm_absent_needs_reason_then_unlocks_retry(client, seed, stuck):
    po_id, _ = stuck
    r = client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={}, headers=H(seed.actor))
    assert r.status_code == 400 and r.json()["error"] == "reason_required"
    r = client.post(f"/v1/purchase-orders/{po_id}/confirm-absent", json={"reason": "已在领星后台按幂等键搜索，无此单"},
                    headers=H(seed.actor))
    assert r.status_code == 200
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    again = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    assert again.json()["outcome"] == "placed"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT resolution, resolved_by FROM po_attention WHERE po_id=%s", (po_id,))
        assert cur.fetchall() == [("confirmed_absent", seed.actor)]


def test_recheck_finds_order_created_late(client, seed, stuck):
    po_id, lid = stuck
    from erp import gateway
    gateway.call("create_purchase_order", {"custom_order_sn": f"SCM-PO-{po_id}", "supplier_id": 8085,
                                           "purchaser_id": 111, "opt_uid": 1, "qc_type": 2, "wid": seed.wid,
                                           "product_list": [{"sku": seed.sku_a, "wid": seed.wid,
                                                             "quantity_real": 10, "price": "2"}]})
    r = client.post(f"/v1/purchase-orders/{po_id}/recheck", headers=H(seed.actor))
    assert r.json()["outcome"] == "placed"
    assert client.get("/v1/attention").json()["items"] == []
```

- [ ] **Step 2: 跑，确认失败** —— Run: `.venv/bin/pytest tests/test_api_attention.py -q` · Expected: 404

- [ ] **Step 3: 实现 `usecase/attention.py`**

```python
"""转人工：看得见 · 看得懂 · 接得住（spec §6.6）。每个动作都写 po_event 或 po_attention：谁 · 何时 · 理由。"""
from __future__ import annotations

import logging

from repo import purchase as repo
from shared.pg_client import pg_conn, timed
from usecase import place_po
from usecase.draft_po import Conflict, load_po
from usecase.line_state import reevaluate

log = logging.getLogger("scm.usecase")

_ATTEMPT_COLS = ("call_id", "started_at", "actor", "endpoint", "mode", "host", "path", "idem_key",
                 "elapsed_ms", "outcome", "http_status", "lx_code", "lx_msg", "cause", "retry_of",
                 "request", "response")


def timeline(po_id: int) -> dict:
    with timed("po_timeline", po_id=po_id), pg_conn() as c, c.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_ATTEMPT_COLS)} FROM erp_call WHERE po_id=%s ORDER BY call_id", (po_id,))
        attempts = []
        for row in cur.fetchall():
            a = dict(zip(_ATTEMPT_COLS, row))
            a["at"] = a.pop("started_at").isoformat()
            attempts.append(a)
        cur.execute("SELECT from_state, to_state, actor, reason, src, at FROM po_event WHERE po_id=%s ORDER BY seq",
                    (po_id,))
        events = [{"from": a, "to": b, "actor": c_, "reason": d, "src": e, "at": f.isoformat()}
                  for a, b, c_, d, e, f in cur.fetchall()]
        cur.execute("SELECT attention_id, kind, opened_at, resolved_at, resolution, resolved_by, reason, detail"
                    " FROM po_attention WHERE po_id=%s ORDER BY attention_id", (po_id,))
        attention = [{"attention_id": a, "kind": b, "opened_at": c_.isoformat(),
                      "resolved_at": d.isoformat() if d else None, "resolution": e, "resolved_by": f,
                      "reason": g, "detail": h} for a, b, c_, d, e, f, g, h in cur.fetchall()]
    open_ = [x for x in attention if x["resolved_at"] is None]
    conclusion = None
    if open_:
        d = open_[0]["detail"]
        conclusion = {"known": d.get("known"), "unknown": d.get("unknown"), "kind": open_[0]["kind"],
                      "how_to_search": {"custom_order_sn": d.get("custom_order_sn"), "window": d.get("searched")}}
    return {"po_id": po_id, "conclusion": conclusion, "attempts": attempts, "events": events,
            "attention": attention}


def _require_open(po_id: int, kind: str = "needs_verify") -> None:
    with pg_conn() as c, c.cursor() as cur:
        if not any(a["kind"] == kind for a in repo.open_attention(cur, po_id)):
            raise Conflict(409, "nothing_to_resolve", "这张单没有待处理的项", po_id=po_id, kind=kind)


def recheck(po_id: int, actor: str) -> dict:
    _require_open(po_id)
    return place_po.lookup_and_settle(po_id, actor, None, "recheck")


def register_existing(po_id: int, order_sn: str, actor: str) -> dict:
    """★ 对上才接受：防止填错单号把别人的单认成自己的。"""
    _require_open(po_id)
    if not (order_sn or "").strip():
        raise Conflict(400, "missing_required", "必须填领星单号", missing=["order_sn"])
    po = load_po(po_id)
    cid, r = place_po.call_logged("purchase_order_list",
                                   {"order_sns": order_sn, "offset": 0, "length": 50},
                                   po_id=po_id, actor=actor, retry_of=None)
    rows = [o for o in ((r.body or {}).get("data") or []) if o.get("order_sn") == order_sn]
    if not rows:
        raise Conflict(409, "order_not_found", "领星里查不到这个单号", order_sn=order_sn, lookup_call=cid)
    o, mismatch = rows[0], {}
    if str(o.get("custom_order_sn")) != po["custom_order_sn"]:
        mismatch["custom_order_sn"] = {"ours": po["custom_order_sn"], "lingxing": o.get("custom_order_sn")}
    if str(o.get("supplier_id")) != str(po["supplier_id"]):
        mismatch["supplier_id"] = {"ours": po["supplier_id"], "lingxing": o.get("supplier_id")}
    ours = sorted((ln["sku"], ln["units"]) for ln in po["lines"])
    theirs = sorted((it["sku"], int(it["quantity_real"])) for it in o.get("item_list") or []
                    if str(it.get("is_delete", 0)) in ("0", "False", "false"))
    if ours != theirs:
        mismatch["items"] = {"ours": ours, "lingxing": theirs}
    if mismatch:
        raise Conflict(409, "order_mismatch", "这张领星单与本采购单对不上，未接受", order_sn=order_sn,
                       mismatch=mismatch, lookup_call=cid)
    with timed("po_register_existing", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        repo.mark_placed(cur, po_id, order_sn, place_po.gateway.mode(), actor, f"register_existing:{cid}")
        place_po.read_back(cur, po_id, o, cid)
        repo.resolve_attention(cur, po_id, "needs_verify", "found_existing", actor, None)
        moved = reevaluate(cur, repo.source_line_ids(cur, po_id), actor, f"po:{po_id}:register_existing")
    log.warning("op=po_register_existing po_id=%s order_sn=%s actor=%s moved=%s", po_id, order_sn, actor, moved)
    return {"outcome": "placed", "po_id": po_id, "order_sn": order_sn, "found_by": "manual"}


def confirm_absent(po_id: int, reason: str, actor: str) -> dict:
    _require_open(po_id)
    if not (reason or "").strip():
        raise Conflict(400, "reason_required", "确认「领星无此单」必须写理由（怎么核实的）")
    with timed("po_confirm_absent", po_id=po_id, actor=actor), pg_conn() as c, c.cursor() as cur:
        repo.resolve_attention(cur, po_id, "needs_verify", "confirmed_absent", actor, reason.strip())
    log.warning("op=po_confirm_absent po_id=%s actor=%s reason=%r", po_id, actor, reason)
    return {"po_id": po_id, "unlocked": ["place", "update", "delete"]}


def queue() -> list[dict]:
    with timed("attention_queue"), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT a.attention_id, a.po_id, a.kind, a.opened_at, p.custom_order_sn, p.supplier_id"
                    " FROM po_attention a JOIN po p USING (po_id) WHERE a.resolved_at IS NULL"
                    " ORDER BY a.opened_at")
        return [{"attention_id": a, "po_id": b, "kind": k, "opened_at": d.isoformat(), "custom_order_sn": e,
                 "supplier_id": f, "timeline": f"/v1/purchase-orders/{b}/timeline"}
                for a, b, k, d, e, f in cur.fetchall()]
```

（`place_po.call_logged` 是 Task 11 定义的读调用留痕函数。）

- [ ] **Step 4: 端点**

`api/ui/purchase_orders.py` 追加：

```python
from usecase import attention as attention_uc


@router.get("/purchase-orders/{po_id}/timeline")
def timeline(po_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    return attention_uc.timeline(po_id)


@router.post("/purchase-orders/{po_id}/recheck")
def recheck(po_id: int, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(attention_uc.recheck, po_id, who)


@router.post("/purchase-orders/{po_id}/register-existing")
def register_existing(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(attention_uc.register_existing, po_id, (body or {}).get("order_sn") or "", who)


@router.post("/purchase-orders/{po_id}/confirm-absent")
def confirm_absent(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    return _call(attention_uc.confirm_absent, po_id, (body or {}).get("reason") or "", who)
```

新建 `api/ui/attention.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from usecase import attention as attention_uc

router = APIRouter()


@router.get("/attention")
def queue(request: Request, who: str | None = Depends(actor_optional)):
    """★ 顶栏常驻红条的数据源（spec §6.6 ①）：不可关闭，直到这里为空。"""
    declared(request)
    items = attention_uc.queue()
    return {"count": len(items), "items": items}
```

`api/__init__.py` 挂 `attention.router`（prefix `/v1`）。

- [ ] **Step 5: 跑测试通过** —— Run: `.venv/bin/pytest tests/test_api_attention.py tests/test_api_place_po.py -q` · Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add usecase/attention.py api/ui/attention.py api/ui/purchase_orders.py api/__init__.py tests/test_api_attention.py
git commit -m "feat(b): 转人工 —— 失败时间线（已知/未知）、重新核对、登记已有单先核对、确认无此单须理由、待处理队列"
```

---

### Task 13: 落地核对 G1 · 核对汇总 G3 · 模拟外部（下单部分）

**Files:**
- Create: `api/ui/checks.py`, `api/ui/simulate.py`, `usecase/landed.py`
- Modify: `usecase/place_po.py`（首检后调用落地核对）, `api/__init__.py`
- Test: `tests/test_api_checks_simulate.py`

**Interfaces:**
- Consumes: `v_check_purchase` / `v_check_po_landed`（Task 5）· `FAKE.enqueue / mutate_item`（Task 10）· `place_po.read_back / call_logged`（Task 11）
- Produces:
  - `usecase.landed.verify(po_id: int, actor: str) -> list[dict]`：读回一次 → 跑 `v_check_po_landed` → 有问题则开 `landed_mismatch` 待处理（detail 带逐行差异）→ 返回问题行
  - `GET /v1/checks/purchase` → `{"ok": bool, "counts": {check_name: n}, "violations": [{check_name, ref}]}`
  - `POST /v1/simulate/erp/next {"endpoint","outcome"}` · `POST /v1/simulate/po/{po_id}/landed {"sku","quantity_real"?,"price"?}` —— ★ 仅 `[erp] mode=fake` 且 `[auth] mode=dev`，否则 403 `simulation_disabled`

- [ ] **Step 1: 写失败的测试** `tests/test_api_checks_simulate.py`

```python
import json

import pytest

from erp.drivers.fake import FAKE
from shared.pg_client import pg_conn
from tests.helpers import H, mint


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


@pytest.fixture
def placed(client, seed):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now())")
        _, lid = mint(cur, seed.actor, seed.sku_a, total=10)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": 10, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": 10}]}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    return po_id


def test_clean_chain_has_no_violations(client, placed):
    body = client.get("/v1/checks/purchase").json()
    assert body["ok"] is True and body["violations"] == []


def test_lingxing_side_change_is_caught(client, seed, placed):
    """Review Focus #5：领星落下的数被改了 —— 核对红、开待处理，不是「拿到单号就算对」。"""
    r = client.post(f"/v1/simulate/po/{placed}/landed", json={"sku": seed.sku_a, "quantity_real": 8},
                    headers=H(seed.actor))
    assert r.status_code == 200 and r.json()["problems"][0]["problem"] == "qty_or_price_differs"
    checks = client.get("/v1/checks/purchase").json()
    assert checks["ok"] is False and checks["counts"]["po_landed"] == 1
    kinds = [i["kind"] for i in client.get("/v1/attention").json()["items"]]
    assert kinds == ["landed_mismatch"]


def test_simulate_next_outcome_drives_place(client, seed):
    r = client.post("/v1/simulate/erp/next", json={"endpoint": "create_purchase_order", "outcome": "rejected"},
                    headers=H(seed.actor))
    assert r.status_code == 200


def test_simulation_refused_outside_fake(client, seed, monkeypatch):
    from shared import config as cfg
    monkeypatch.setattr(cfg, "erp", lambda: {**cfg._ERP_DEFAULTS, "mode": "live"})
    r = client.post("/v1/simulate/erp/next", json={"endpoint": "create_purchase_order", "outcome": "ok"},
                    headers=H(seed.actor))
    assert r.status_code == 403 and r.json()["error"] == "simulation_disabled"
```

- [ ] **Step 2: 跑，确认失败** —— Run: `.venv/bin/pytest tests/test_api_checks_simulate.py -q` · Expected: 404

- [ ] **Step 3: 实现 `usecase/landed.py`**

```python
"""G1：领星落下的 ≡ 我们发的（spec §11）。读回一次 → 核对 → 不符开待处理。"""
from __future__ import annotations

import logging

from repo import purchase as repo
from shared.pg_client import pg_conn, timed
from usecase import place_po

log = logging.getLogger("scm.usecase")

_COLS = ("problem", "po_id", "order_sn", "line_no", "sku", "wid", "ours_units", "lx_units", "ours_price", "lx_price")


def verify(po_id: int, actor: str) -> list[dict]:
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT order_sn FROM po WHERE po_id=%s AND state='已下单'", (po_id,))
        row = cur.fetchone()
    if row is None:
        return []
    order_sn = row[0]
    cid, r = place_po.call_logged("purchase_order_list", {"order_sns": order_sn, "offset": 0, "length": 50},
                                   po_id=po_id, actor=actor, retry_of=None)
    rows = [o for o in ((r.body or {}).get("data") or []) if o.get("order_sn") == order_sn]
    with timed("po_landed_verify", po_id=po_id), pg_conn() as c, c.cursor() as cur:
        if rows:
            place_po.read_back(cur, po_id, rows[0], cid)
        cur.execute(f"SELECT {', '.join(_COLS)} FROM v_check_po_landed WHERE po_id=%s", (po_id,))
        problems = [dict(zip(_COLS, x)) for x in cur.fetchall()]
        if problems:
            repo.open_attention_row(cur, po_id, "landed_mismatch", cid,
                                    {"known": "领星里的采购单明细与本单不一致", "unknown": "是谁、为何改的",
                                     "problems": problems})
    if problems:
        log.warning("op=po_landed_mismatch po_id=%s order_sn=%s problems=%s", po_id, order_sn, problems)
    return problems
```

把 Task 11 `_first_check` 的函数体整个替换为调用 `landed.verify(po_id, actor)`（`from usecase import landed` 放函数内 import，避免循环），`_first_check` 删除、`place()` 里改调 `landed.verify(po_id, actor)`。

- [ ] **Step 4: 实现 `api/ui/checks.py` 与 `api/ui/simulate.py`**

```python
# api/ui/checks.py
"""G3：v_check_* 汇总。空 = 通过；非空就点名每一行（spec §11.1）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor_optional, declared
from shared.pg_client import pg_conn, timed

router = APIRouter()
CHECKS = ("po_source", "line_state", "po_unplanned", "po_landed")


@router.get("/checks/purchase")
def purchase_checks(request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    with timed("checks_purchase", actor=who), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT check_name, ref FROM v_check_purchase ORDER BY check_name")
        violations = [{"check_name": a, "ref": b} for a, b in cur.fetchall()]
    counts = {k: sum(1 for v in violations if v["check_name"] == k) for k in CHECKS}
    return {"ok": not violations, "counts": counts, "violations": violations}
```

```python
# api/ui/simulate.py
"""「模拟外部」（16 §0 ②）：只在 fake + dev 下开放，扮演领星的返回，不是用户的活。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.ui.deps import actor, declared
from api.ui.errors import ApiError
from erp.drivers.fake import FAKE, OUTCOMES
from shared import config as config_module
from shared.pg_client import pg_conn
from usecase import landed

router = APIRouter()


def _only_in_fake() -> None:
    e, a = config_module.erp()["mode"], config_module.auth()["mode"]
    if e != "fake" or a != "dev":
        raise ApiError(403, "simulation_disabled", "模拟外部只在 [erp] mode=fake 且 [auth] mode=dev 下可用",
                       {"erp_mode": e, "auth_mode": a})


@router.post("/simulate/erp/next")
def next_outcome(body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    _only_in_fake()
    if body.get("outcome") not in OUTCOMES:
        raise ApiError(400, "bad_outcome", "未知的模拟结果", {"got": body.get("outcome"), "allowed": list(OUTCOMES)})
    FAKE.enqueue(body["endpoint"], body["outcome"])
    return {"queued": body}


@router.post("/simulate/po/{po_id}/landed")
def change_landed(po_id: int, body: dict, request: Request, who: str = Depends(actor)):
    declared(request)
    _only_in_fake()
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT order_sn FROM po WHERE po_id=%s AND placed_mode='fake'", (po_id,))
        row = cur.fetchone()
    if row is None:
        raise ApiError(404, "po_not_found", "没有这张 fake 已下单的采购单", {"po_id": po_id})
    FAKE.mutate_item(row[0], body["sku"], body.get("quantity_real"), body.get("price"))
    return {"po_id": po_id, "problems": landed.verify(po_id, who)}
```

`api/__init__.py` 挂 `checks.router` 与 `simulate.router`（prefix `/v1`）。

- [ ] **Step 5: 跑测试通过** —— Run: `.venv/bin/pytest tests/test_api_checks_simulate.py tests/test_api_place_po.py tests/test_api_attention.py -q` · Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add usecase/landed.py usecase/place_po.py api/ui/checks.py api/ui/simulate.py api/__init__.py tests/test_api_checks_simulate.py
git commit -m "feat(b): G1 落地核对 + G3 核对汇总 + 模拟外部（下单结果 / 领星侧改量），仅 fake+dev 开放"
```

---

### Task 14: 反查 trace（G4）· 计划级采购完成度（G5）· preflight 端点（G6）

**Files:**
- Modify: `api/ui/lines.py`（`GET /v1/plan-lines/{line_id}/trace`）, `api/ui/plans.py`（`GET /v1/plans/{plan_id}/purchase-progress`）, `api/ui/purchase_orders.py`（`GET …/preflight`）
- Test: `tests/test_api_trace_progress.py`

**Interfaces:**
- Produces:
  - `GET /v1/plan-lines/{line_id}/trace` → `{"line_id","sku","period","total_units","state","confirmed_units","ordered_units","purchase_orders":[{"po_id","custom_order_sn","order_sn","state","is_void","sources":[{"seller_id","units"}]}]}`
  - `GET /v1/plans/{plan_id}/purchase-progress` → `{"plan_id","rev","rows":[{"sku","period","planned","confirmed","ordered","good_in":null}]}` —— ★ `good_in` 恒 null 并带 `basis.good_in = "M3 入库读回未接"`（空 ≠ 0）
  - `GET /v1/purchase-orders/{po_id}/preflight` → `{"po_id","ok","gates":[…]}`

- [ ] **Step 1: 写失败的测试** `tests/test_api_trace_progress.py`

```python
import json

from shared.pg_client import pg_conn
from tests.helpers import H, mint


def _po(client, seed, lid, units):
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 1, "qc_type": 2,
            "lines": [{"sku": seed.sku_a, "wid": seed.wid, "units": units, "price": "2",
                       "sources": [{"line_id": lid, "seller_id": seed.seller_a, "units": units}]}]}
    return client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]


def _setup(seed, total=10):
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
        plan_id, lid = mint(cur, seed.actor, seed.sku_a, total=total)
        cur.execute("UPDATE plan_line SET demand_by_seller=%s::jsonb WHERE line_id=%s",
                    (json.dumps({seed.seller_a: {"units": 5, "basis": "human"}}), lid))
    return plan_id, lid


def test_trace_lists_every_po_and_store_split(client, seed):
    _, lid = _setup(seed)
    a, b = _po(client, seed, lid, 4), _po(client, seed, lid, 6)
    t = client.get(f"/v1/plan-lines/{lid}/trace").json()
    assert t["confirmed_units"] == 10 and t["state"] == "已确认"
    assert [(p["po_id"], p["sources"]) for p in t["purchase_orders"]] == \
        [(a, [{"seller_id": seed.seller_a, "units": 4}]), (b, [{"seller_id": seed.seller_a, "units": 6}])]


def test_plan_progress_shows_four_columns_and_null_good_in(client, seed):
    plan_id, lid = _setup(seed)
    _po(client, seed, lid, 4)
    p = client.get(f"/v1/plans/{plan_id}/purchase-progress").json()
    row = p["rows"][0]
    assert (row["planned"], row["confirmed"], row["ordered"], row["good_in"]) == (10, 4, 0, None)
    assert p["basis"]["good_in"]


def test_preflight_endpoint_lists_all_gates(client, seed):
    _, lid = _setup(seed)
    po_id = _po(client, seed, lid, 4)
    r = client.get(f"/v1/purchase-orders/{po_id}/preflight").json()
    assert r["ok"] is True and {g["gate"] for g in r["gates"]} >= {"supplier", "opt_uid", "line_price"}
```

- [ ] **Step 2: 跑，确认失败** —— Run: `.venv/bin/pytest tests/test_api_trace_progress.py -q` · Expected: 404

- [ ] **Step 3: 实现**

`api/ui/lines.py` 追加：

```python
@router.get("/plan-lines/{line_id}/trace")
def trace(line_id: int, request: Request, who: str | None = Depends(actor_optional)):
    """UC-O9/P7（08:83）：承重墙① 反查。排货侧（承重墙②）属阶段 C。"""
    declared(request)
    with timed("line_trace", line_id=line_id), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT l.line_id, l.sku, l.period_start, l.total_units, l.state, q.confirmed_units,"
                    " q.ordered_units FROM plan_line l JOIN v_plan_line_po_qty q USING (line_id)"
                    " WHERE l.line_id=%s", (line_id,))
        r = cur.fetchone()
        if r is None:
            raise ApiError(404, "line_not_found", "没有这条记录", {"line_id": line_id})
        cur.execute("SELECT e.po_id, e.custom_order_sn, e.order_sn, e.state, e.is_void, m.seller_id, m.units"
                    " FROM po_source_map m JOIN v_po_effective e USING (po_id)"
                    " WHERE m.line_id=%s ORDER BY e.po_id, m.seller_id", (line_id,))
        pos: dict[int, dict] = {}
        for po_id, csn, osn, st, void, sid, u in cur.fetchall():
            p = pos.setdefault(po_id, {"po_id": po_id, "custom_order_sn": csn, "order_sn": osn,
                                       "state": st, "is_void": void, "sources": []})
            p["sources"].append({"seller_id": sid, "units": u})
    return {"line_id": r[0], "sku": r[1], "period": r[2].strftime("%Y-%m"), "total_units": r[3],
            "state": r[4], "confirmed_units": r[5], "ordered_units": r[6],
            "purchase_orders": list(pos.values())}
```

`api/ui/plans.py` 追加：

```python
@router.get("/plans/{plan_id}/purchase-progress")
def purchase_progress(plan_id: int, request: Request, who: str | None = Depends(actor_optional)):
    """G5：按计划看完成度。四列并排，不合成达成率（00e 阶段 D 同款判据）。"""
    declared(request)
    with timed("plan_purchase_progress", plan_id=plan_id), pg_conn() as c, c.cursor() as cur:
        ensure_plan(cur, plan_id)
        cur.execute("SELECT rev FROM plan_rev WHERE plan_id=%s ORDER BY in_flight DESC, rev DESC LIMIT 1",
                    (plan_id,))
        rev = (cur.fetchone() or [None])[0]
        cur.execute("SELECT l.sku, l.period_start, l.total_units, q.confirmed_units, q.ordered_units"
                    " FROM plan_line l JOIN v_plan_line_po_qty q USING (line_id)"
                    " WHERE l.plan_id=%s AND l.rev=%s ORDER BY l.sku, l.period_start", (plan_id, rev))
        rows = [{"sku": a, "period": b.strftime("%Y-%m"), "planned": c_, "confirmed": d, "ordered": e,
                 "good_in": None} for a, b, c_, d, e in cur.fetchall()]
    return {"plan_id": plan_id, "rev": rev, "rows": rows,
            "basis": {"good_in": "M3 入库读回未接：null 表示「还没接」，不是 0"}}
```

（`ensure_plan` 从 `api.ui.deps` import，若 `plans.py` 尚未 import。）

`api/ui/purchase_orders.py` 追加：

```python
@router.get("/purchase-orders/{po_id}/preflight")
def preflight_(po_id: int, request: Request, who: str | None = Depends(actor_optional)):
    declared(request)
    po = _call(draft_po.load_po, po_id)
    return {"po_id": po_id, "ok": all(g["ok"] for g in po["preflight"]), "gates": po["preflight"]}
```

- [ ] **Step 4: 跑测试通过** —— Run: `.venv/bin/pytest tests/test_api_trace_progress.py -q` · Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/ui/lines.py api/ui/plans.py api/ui/purchase_orders.py tests/test_api_trace_progress.py
git commit -m "feat(b): 记录反查 trace、计划级采购完成度（入库列显式 null）、preflight 端点"
```

---

### Task 15: M1 验收 —— 端到端闭环

**Files:**
- Test: `tests/test_e2e_m1_plan_to_order.py`

**Interfaces:**
- Consumes: 本计划全部端点；阶段 A 的提交端点 `POST /v1/plans/{plan_id}/submit`（沿用 `tests/helpers.py::prepared` 造一张已提交的计划）

- [ ] **Step 1: 写验收测试**（★ 每一跳之后断言 `GET /v1/checks/purchase` 为 ok）

```python
"""M1 验收（spec §11.4）：计划 → 候选 → 组单 → 下单（四种结果）→ 读回核对 → 反查，
每一跳之后 v_check_* 全空。"""
import pytest

from erp.drivers.fake import FAKE
from shared.pg_client import pg_conn
from tests.helpers import H, prepared


@pytest.fixture(autouse=True)
def clean_fake():
    FAKE.reset()
    yield
    FAKE.reset()


def _ok(client):
    body = client.get("/v1/checks/purchase").json()
    assert body["ok"], body["violations"]


def test_plan_to_order_chain(client, seed):
    plan_id = prepared(client, seed)                    # 阶段 A：建计划 → 填两种量
    assert client.post(f"/v1/plans/{plan_id}/submit", headers=H(seed.actor)).status_code == 200
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO supplier VALUES ('8085','01',now()) ON CONFLICT DO NOTHING")
    cands = client.get(f"/v1/plan-lines?plan_ids={plan_id}&orderable=1").json()["lines"]
    assert cands, "提交后应有可组单的记录"
    _ok(client)

    line = next(c for c in cands if c["blocked"] is None)
    sources = [{"line_id": line["line_id"], "seller_id": sid, "units": v["remaining"]}
               for sid, v in line["sellers"].items() if v["remaining"]]
    units = sum(s["units"] for s in sources)
    body = {"supplier_id": "8085", "purchaser_id": 111, "opt_uid": 10925905, "qc_type": 2,
            "lines": [{"sku": line["sku"], "wid": seed.wid, "units": units, "price": "3.2",
                       "sources": sources}]}
    po_id = client.post("/v1/purchase-orders", json=body, headers=H(seed.actor)).json()["po_id"]
    _ok(client)
    assert client.get(f"/v1/plan-lines/{line['line_id']}/trace").json()["state"] == "已确认"

    # 先业务拒绝，再结果未知且领星实际已建 —— 最终只有一张领星单
    FAKE.enqueue("create_purchase_order", "rejected")
    FAKE.enqueue("create_purchase_order", "unknown_created")
    tok = client.get(f"/v1/purchase-orders/{po_id}").json()["confirm_token"]
    assert client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok},
                       headers=H(seed.actor)).json()["outcome"] == "rejected"
    r = client.post(f"/v1/purchase-orders/{po_id}/place", json={"confirm_token": tok}, headers=H(seed.actor))
    assert r.json()["outcome"] == "placed" and r.json()["found_by"] == "lookup"
    _ok(client)

    t = client.get(f"/v1/plan-lines/{line['line_id']}/trace").json()
    assert t["state"] == "已下单" and t["ordered_units"] == t["total_units"]
    assert t["purchase_orders"][0]["order_sn"].startswith("FAKE-PO")
    tl = client.get(f"/v1/purchase-orders/{po_id}/timeline").json()
    assert [a["outcome"] for a in tl["attempts"] if a["endpoint"] == "create_purchase_order"] == \
        ["rejected", "unknown"]
    assert client.get("/v1/attention").json()["count"] == 0
```

- [ ] **Step 2: 先弄失败一次**（`CLAUDE.md`「测试写完先弄失败一次」）：临时把 `usecase/place_po.py` 里 `lookup_and_settle` 的 `if hit and …` 改成 `if False and hit and …`，

Run: `.venv/bin/pytest tests/test_e2e_m1_plan_to_order.py -q`
Expected: FAIL（`outcome == "needs_verify"`），然后**恢复**那一行。

- [ ] **Step 3: 跑全量**

Run: `.venv/bin/pytest -q`
Expected: 全部 PASS（live 测试按既有标记跳过且「跳过必须吵」）

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_m1_plan_to_order.py
git commit -m "test(b): M1 验收 —— 计划到订单全链，每跳 v_check_* 全空，重复下单被查重拦住"
```

---

### Task 16: 文档同步（B5/B6/B7/B8 · PB 组落到设计层）

**Files:**
- Modify: `docs/03-数据库表清单.md`, `docs/04-状态流转.md`, `docs/06-核心业务流程.md`, `docs/07-核心业务模块时序.md`, `docs/08-后端接口清单.md`, `docs/02-本体实体关系.md`, `docs/00d-前端并入本仓改造计划.md`, `docs/01-架构设计.md`, `docs/00e-分阶段实施计划.md`
- Test: `docs/prototype/test_doc_index.js`, `test_retired_claims.js`, `test_doc_flow.js`, `test_mermaid.js`

- [ ] **Step 1: `03`** —— §po 段（`03:394-473`）整段替换为「★ 以迁移 010 / 011 / 012 为准」+ 三个文件的表清单（表名 · 主键 · 关键约束名，逐条照 Task 3~5 的 SQL）；删 `po.wid NOT NULL`（B7）、补 `qc_type`（B8）；`erp_call.outcome` 取值改为 `in_flight/ok/rejected/not_sent/unknown` 并写一句理由（「按能不能重试分，不按怎么失败分」，spec §6.2）；§7.3 加 `v_check_po_source / v_check_line_state / v_check_po_unplanned / v_check_po_landed`。
- [ ] **Step 2: `04`** —— S1 加退回边 `已下单→已确认`、`已下单→已提交`（PB-D5，触发 = 采信的领星状态 -1 且无良品入库）；`04:201` 准备排货判据改为「任一关联 po_line 有已完成 IB 良品 > 0」（PB-D10，M3 实现）；§2.2 触发清单加「📥 领星作废读回」一行（B6）；加「`已确认/已下单/准备排货` 禁止手动推进，422 `derived_state`」。
- [ ] **Step 3: `06` / `07`** —— `06:196-200` 与 `07:150-157`：「真超时 → 查不到可重试」改为「请求发出后的失败（读超时 / 连接重置 / 5xx）= 结果未知 → 查重；查不到 → 待人工核实，不自动重试」（PB-D13）；`07` §2 的「SELECT FOR UPDATE 锁草稿 → 调领星」改为「事务① 落 in_flight → 不持锁调用 → 事务② 收尾」；`07` §6 回执器加「未结单 PO 每日只读接口复核」（PB-D8，M3）。
- [ ] **Step 4: `08`** —— §采购接口表补本计划的全部新端点与错误码（`over_seller_cap` `no_seller_basis` `unplanned_reason_required` `duplicate_line` `stale_draft` `po_placing` `needs_verify` `stale_confirm_token` `preflight_failed` `derived_state` `order_mismatch` `order_not_found` `reason_required` `nothing_to_resolve` `simulation_disabled`）；外部写接口表补到 7 个（B5：`createPurchaseOrder` · `setOrders` · `addLogistics` · `createPurchaseChangeOrder` · `cancel` · `setOrderFinish` · `createPurchasePlan`，出处 `17:45-61`），并注明本服务只调第一个；`08:252` 两类前缀改为「以 `erp/endpoints.py` 登记表为准」；补读接口 `purchaseOrderList` 的真实路径与参数（出处见 `erp/endpoints.py`）。
- [ ] **Step 5: `02`** —— `02:1090`「工厂交货只有到货记录，无独立单据」删除，改为指向 CR / QC / IB 三对象（B4）。
- [ ] **Step 6: `00d` / `01`** —— Q-5 行与 §3 标「★ 已重裁：PB-D11 / PB-D12（2026-10-07）」；`01` §1.2 认证条同步为「dev 模式限开发；生产 Keycloak + oauth2-proxy 之后，经 cf-access-rbac `by-jwt` 判权限」。
- [ ] **Step 7: `00e`** —— §8 进度表 B 行：`☑ B5 B6 B7 B8（M1）· ☐ B1 B2 B4 B9~B11（M3）`。
- [ ] **Step 8: 跑文档门禁**

Run: `cd docs/prototype && node test_doc_index.js && node test_retired_claims.js && node test_doc_flow.js && node test_mermaid.js`
Expected: 全部 ✓（若 `test_retired_claims.js` 报「作废说法仍出现」，按它点名的行改；不许改门禁本身）

- [ ] **Step 9: Commit**

```bash
git add docs
git commit -m "docs(b): PB 组落到设计层 —— 03/04/06/07/08 同步 M1 实现，B5~B8 关闭，Q-5 重裁标注"
```

---

### Task 17: 刀0 只读实调（凭证已就绪；依赖 Task 10，不阻塞其它任务）

**前置：** ★ 已就绪（2026-10-07）：`config.toml [erp]` 已有 `app_id / app_secret / proxy_*`（复制自 `/home/fido/work/2026/tools/config/services.toml`，负责人指定；**不进提交**）。`mode` 保持 `fake` —— 本任务只走只读端点，且直接用 `LiveDriver`，不改全局模式。★ 领星有 IP 白名单，必须经代理；探针输出要能分出「代理连不上」「token 失败」「签名 / 业务拒绝」三种。

**Files:**
- Create: `jobs/probe_lingxing.py`
- Modify: `docs/superpowers/specs/2026-10-07-purchase-order-design.md`（§6.7 左列逐条回填实测结果）

- [ ] **Step 1: 写探针**

```python
"""刀0 只读实调（spec §6.7 左列）。零副作用：只调 purchaseOrderList。
用法：.venv/bin/python -m jobs.probe_lingxing [YYYY-MM-DD]
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from erp.drivers.live import LiveDriver
from erp.endpoints import get
from shared.config import erp


def main(argv: list[str]) -> int:
    cfg = erp()
    missing = [k for k in ("app_id", "app_secret", "proxy_host") if not cfg[k]]
    if missing:
        print(f"[erp] 缺 {missing} —— 本探针需要领星凭证与白名单代理", file=sys.stderr)
        return 2
    day = dt.date.fromisoformat(argv[0]) if argv else dt.date.today() - dt.timedelta(days=1)
    ep = get("purchase_order_list")
    r = LiveDriver(cfg).call(ep, {"search_field_time": "create_time", "start_date": day.isoformat(),
                                  "end_date": day.isoformat(), "offset": 0, "length": 5})
    print(json.dumps({"outcome": r.outcome, "ms": r.elapsed_ms, "http": r.http_status, "lx_code": r.lx_code,
                      "lx_msg": r.lx_msg, "cause": r.cause,
                      "top_keys": sorted((r.body or {}).keys()),
                      "order_keys": sorted(((r.body or {}).get("data") or [{}])[0].keys()),
                      "item_keys": sorted((((r.body or {}).get("data") or [{}])[0].get("item_list") or [{}])[0].keys()),
                      "total": (r.body or {}).get("total")}, ensure_ascii=False, indent=2))
    return 0 if r.outcome == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 2: 跑**（只读，可自行执行）

Run: `.venv/bin/python -m jobs.probe_lingxing`
Expected: `"outcome": "ok"`；`order_keys` 里有 `custom_order_sn`、`item_list`；`item_keys` 里有 `item_id`、`sku`、`wid`、`quantity_real`、`price`、`is_delete`

- [ ] **Step 3: 回填 spec §6.7 左列**（签名是否正确 · 字段名 · 分页形状 · 有无 `custom_order_sn` 过滤），与 `erp/endpoints.py` / `rules.purchase.find_by_custom_sn` / `repo.insert_item_obs` 的假设逐条对照；任何一处对不上 → 改代码 + 补测试，不改断言迁就。

- [ ] **Step 4: Commit**

```bash
git add jobs/probe_lingxing.py docs/superpowers/specs/2026-10-07-purchase-order-design.md
git commit -m "chore(b): 刀0 领星只读实调探针 + 实测回填 spec §6.7"
```
