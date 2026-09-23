# 维度镜像刷新 · 设计（2026-09-22）

> 范围：把「镜像有哪些 · 谁刷 · 什么时候刷 · 刷失败怎么办」从散在各文档的句子，
> 收成**一张登记表 + 两道门禁 + 一个进程内调度**。阶段 A 先落四张 DIM 镜像。

---

## 1. 背景与事实（全部带出处）

| # | 事实 | 出处 |
|---|---|---|
| F-1 | `03` §1 表清单里归属含 MIRROR / DIM 的共 **10 行**：A 段 `sku_catalog` `msku_bridge` `seller` `warehouse`（DIM 镜像 · 刷新）+ `sku_category`（+`category_refresh`，PM 源，只标注陈旧）；B 段 `po_snapshot` `po_receipt`（只追加）`supplier`（可变镜像）；C 段 `ext_stage_observation`（只追加）；D 段 `sales_actual`（只追加） | `docs/03-数据库表清单.md:68-70,79,84-88,94` |
| F-2 | 四张 DIM 镜像刷新失败 → **拒绝服务**（启动钩子 + 接口 503）；品类镜像只「显式标注已 N 天未刷新」 | `03:1616-1626`（E-4）· `01:274-275` |
| F-3 | `v_mirror_freshness` 已建，恰含那四张，供启动钩子与就绪探针共用 | `03:1627-1631` · `migrations/pg/001_foundation.sql:63-67` |
| F-4 | **文档从未给过刷新频率与负责人**。唯一写过频率的是 B/C 回执器：「每日一次，cron，在 CH 采集窗口之后；另留一个**仅运维可用**的手动触发」 | `07:481-482` · `00:235`（D-2） |
| F-5 | 读哪一版：取 `max(_captured_date)` 最新一版，**不做认知时间回放**；第一步先跑采集面校验，**掉档超阈值 → 整批不可用、不写** | `07:483-485` |
| F-6 | 现状缺口：`api/__init__.py:54-62` 的启动钩子**只打日志、不拦服务**，与 F-2 的「启动钩子」不符；`api/ui/deps.py:22-38` 的逐请求 503 已经在了 | 代码实测 |
| F-7 | `[freshness] max_age_hours = 24` 自带标注「设计取值 · 未实测 —— 刷新频率定下来后按实测改」 | `config.example.toml:25-28` |
| F-8 | `jobs/__init__.py` 刻意为空；`dim/ch_source.py` 四个方法全 `NotImplementedError`；`shared/ch_client.py` **不存在** | 代码实测 |
| F-9 | ★ **`tests/test_layering.py:53-55,114-127` 明令 `dim/` 不许 import `shared.ch_client`**（`NO_CONNECTIONS` 里点名了它，注释写着「这条是给阶段 B 的 `ChSource` 备的」）；`test_l7_no_writes_to_clickhouse` 用正则扫 `dim/` 里的 `insert into` / `create table` 等词 | 代码实测 |
| F-10 | `sku_category` / `category_refresh` **在 `migrations/pg/*.sql` 里一张都没有** | 代码实测（grep） |
| F-11 | 免代理 `PoolManager` 是打内网 CH 的关键（`HTTP_PROXY` 会劫持内网 IP → 503）；邻居仓 `model_inventory_forecast/shared/ch_client.py` 是这条的既有实现，跑在 `clickhouse-connect 1.5.0` 上 | 邻居仓代码实测 |

### 1.1 负责人裁定（本设计的前提，不再论证）

> **维度/镜像刷新走 APScheduler 进程内定时**（Python 库）。不用外部 cron，不用 `pg_cron`（该实例没有）。

---

## 2. 目标 / 非目标

**目标**：① 「有哪些镜像」只有**一个真相** `dim/registry.py`，文档新增一行而登记表没跟上 → 门禁红；
② 四张 DIM 镜像能从 CH `jxd_raw` 真刷出来，三个入口（调度 / CLI / 运维接口）**互斥**；
③ 每轮留痕：打的谁、多久、进来多少行、丢了多少行、为什么丢、成没成；
④ 掉档超阈值 **拒绝整批**并保留旧镜像（F-5）；⑤ 启动钩子按 F-2 真的拦住服务，补上 F-6。

**非目标**

- 不实现 B/C/D 段镜像的取数（`po_snapshot` `supplier` `ext_stage_observation` `sales_actual`）—— 它们在登记表里以 `pending` 登记，只保证「漏了会红」。
- 不做告警（`01:276`：本期只写日志，A-4）。
- 不建 `sku_category` / `category_refresh`（F-10，属 A-1，另案）。
- 不改已执行的迁移（001~005），只追加 `006`。

---

## 3. 登记表 `dim/registry.py`

```python
@dataclass(frozen=True)
class Mirror:
    name: str                       # = PG 表名，也是 v_mirror_freshness 的 mirror 值
    stage: str                      # "A" | "B" | "C" | "D"
    source: str | None              # CH / PM 源表全名；None = 源未定（见 §10）
    kind: str                       # "refresh" | "append" | "mutable"
    key_columns: tuple[str, ...]
    staleness: str                  # "gate_503" | "label_only" | "none"
    fetch: Callable[[Query], Fetched] | None   # None ⟺ pending
    coverage: tuple[str, ...]       # ("rows",) 或 ("rows", "distinct:sid")
    depends_on: tuple[str, ...]     # 外键父表，决定刷新次序
    companions: tuple[str, ...]     # 同一行文档里并列的附属表（留痕表等）
    pending: bool                   # True = 本阶段不实现，但名字必须在
```

| name | stage | kind | staleness | source | coverage | depends_on |
|---|---|---|---|---|---|---|
| `seller` | A | refresh | `gate_503` | `jxd_raw.lingxing_seller_list` ⚠️ | rows | — |
| `sku_catalog` | A | refresh | `gate_503` | `jxd_raw.lingxing_product_local_products` | rows | — |
| `msku_bridge` | A | refresh | `gate_503` | `jxd_raw.lingxing_product_listing` ⚠️ | rows, distinct:sid | seller, sku_catalog |
| `warehouse` | A | refresh | `gate_503` | `jxd_raw.lingxing_inventory_warehouses` | rows | — |
| `sku_category` | A | refresh | `label_only` | PM `/api/integration/*` | rows | pending（F-10）· companions=`category_refresh` |
| `po_snapshot` `po_receipt` `supplier` | B | append/append/mutable | `none` | — | — | pending |
| `ext_stage_observation` | C · `sales_actual` D | append | `none` | — | — | pending |

★ `staleness` 与 `v_mirror_freshness` 的关系：**视图不由登记表生成**（001 已执行，不可改），
改由门禁 (b) 断言二者一致 —— 视图里的 mirror 集合 ≡ 登记表里 `staleness == "gate_503"` 的集合。

★ **`[freshness] max_age_hours` 对 `gate_503` 那一组生效**，`label_only` 的只进 `/v1/readiness` 的标注。

---

## 4. 两道门禁 `tests/test_mirror_registry.py`

**(a) 文档 ⇄ 登记表 集合相等（离线可跑）**：解析 `03` §1 表格，取「归属」列含
`MIRROR` / `DIM` 的行，抽「表」列里**全部**反引号标识符（第 34 行有两个）。
期望集合 = 登记表 `names ∪ companions`，**双向断言集合相等**，不是包含。
`pending=True` 的条目允许没有 `fetch`，**名字仍必须在** —— 于是 `03` 新增一行 MIRROR
而没人加登记 → 红。★ **同时统计被丢掉的那一侧**：断言解析到的总行数 ≥ 42
（`03:51` 写的就是 43 张，含 S-33 新增的 `dim_refresh_run`），否则是正则坏了 —— 扫 0 行的门禁永远是绿的。

**(b) 非 pending 条目必须兑现（需连 PG 测试库）**：① `fetch` 可调用；② 该表有
`refreshed_at` 列（查 `information_schema.columns`）；③ `staleness == "gate_503"` 的
在测试库 `v_mirror_freshness` 里有一行，**反向也查**：视图里出现登记表没有的 mirror → 红。

---

## 5. 调度（负责人裁定：APScheduler 进程内）

`jobs/scheduler.py`，`AsyncIOScheduler` 挂在 FastAPI 的 lifespan 里。

| 项 | 做法 | 依据 |
|---|---|---|
| 频率 | 每日一次 `CronTrigger`，时刻取 `[freshness] refresh_at`（默认 `"06:30"`，在 CH 采集窗口之后） | `07:481` 的同一条理由：CH 延迟 ≥ 1 天，跑更密没有新数据 |
| 启动时 | §8 的启动检查先跑一次新鲜度检查；**任一 `gate_503` 镜像陈旧 → 立刻触发一次刷新**——只触发，不拦启动（OQ-8 裁定 09-22） | `03:1622` 启动钩子 |
| 错过 | `misfire_grace_time = 3600`、`coalesce = True` | 睡醒/重启后补跑一次，不补跑一串 |
| 互斥 | PG **会话级 advisory lock**（`pg_try_advisory_lock(20260922)`），三个入口共用 | 三个入口同时刷 = 同一张表两个写入者 |
| 关 | `[freshness] scheduler_enabled = false` → 不启动，**并打一条 WARNING 说明它是关着的** | 「不执行的东西不会失败」：静默不启动与启动了长得一样 |
| 日志 | 启动/停止/每次触发各一条；任务异常挂 `EVENT_JOB_ERROR` 监听器打全 traceback | APScheduler 默认把 job 异常吞进它自己的 logger |

**三个入口**（`dim_refresh_run.trigger` 三个取值）：调度 `scheduler`；
CLI `cli` = `python -m jobs.refresh_dims [--only NAME]`；运维 `api` =
`POST /v1/jobs/refresh-dims`（必须带 `x-actor`，体可给 `{"only": "..."}`）。
★ 拿不到 advisory lock → **409 `refresh_in_flight`**（CLI 退出码 3），**不排队、不假装成功**。

★ 依赖：`apscheduler==3.11.3`（PyPI 可达，已实测）· `clickhouse-connect==1.5.0`
—— 后者钉在邻居仓正在跑的那一版，因为免代理 `httputil.get_pool_manager(
http_proxy=None, https_proxy=None)` 是在它上面实测过的（F-11）。

---

## 6. 留痕与覆盖面 · 迁移 `006_dim_refresh_run.sql`

```sql
CREATE TABLE IF NOT EXISTS dim_refresh_run (
    run_id              bigserial PRIMARY KEY,
    mirror              text        NOT NULL,
    trigger             text        NOT NULL
        CONSTRAINT dim_refresh_run_trigger_known CHECK (trigger IN ('scheduler','cli','api')),
    actor               text        REFERENCES actor(actor_id),   -- ★ 可空：调度没有人
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    source_max_captured date,          -- 该批 CH 数据的 max(_captured_date)（07:484）
    rows_in             integer,
    rows_dropped        integer,
    drop_reasons        jsonb,         -- {"empty_sku": 12, "unknown_kind": 0} —— 丢的那一侧按原因分列
    ok                  boolean     NOT NULL DEFAULT false,
    error               text
);
CREATE INDEX dim_refresh_run_by_mirror ON dim_refresh_run (mirror, started_at DESC);
```

**判据**

1. `refreshed_at` **只在成功时**更新，且与 upsert **同一事务**。失败 → 旧镜像原封不动。
2. 覆盖面校验（`07:485`）：本轮 `rows_in` 与该镜像**上一条 `ok=true`** 的 `rows_in` 比，
   跌幅 > `coverage_drop_threshold` → **拒绝整批**，写 `ok=false` + `error='coverage_drop'`，
   镜像不动。`msku_bridge` 另比 `distinct sid` —— 整店 0 行是**采集缺口的形状**，行数总量看不出来。
3. **首轮没有基线**：接受，但 `drop_reasons` 写 `{"no_baseline": 1}` 并打 WARNING ——
   静默放行与「比过了、没问题」长得一模一样。
4. 丢弃分类计数进 `drop_reasons`；★ **认不出的形态硬失败**，不许落进 `else` 再被下游过滤掉
   （例：`warehouse.kind` 映射不中的 `type/sub_type` → 整批拒绝，不写 `''`）。
5. `dim_refresh_run` 是只追加的证据 —— 挂 `forbid_update_delete()` 触发器（`001` 已有该函数）。
6. ★ **只 upsert，绝不 DELETE 源里消失的行。** 一行从 CH 消失**不等于**那个 listing/货号没了 ——
   那是采集缺口的形状（`07:485` 的同一条）。何况 `plan_cell_msku` 的外键指着 `msku_bridge`，
   删一行就是让一张已提交的计划失去依据。消失的行保留旧 `refreshed_at`，本身就是线索。

---

## 7. 四张镜像的取数 SQL 草案

★ **OQ-1 已由 Task 3 只读探针解决**（`jobs/probe_ch.py`，实测 2026-09-22，内网
`192.168.66.211`）。下方 §7.0 是探针打出来的真实列（`system.columns`），
**下面的 SQL 草案本身未改**（改写是 Task 4 的事），但探针已经发现两处草案
与真实列名对不上，Task 4 写 SQL 前必须处理：

- ⚠️ **`lingxing_seller_list` 没有 `marketplace` 列也没有 `platform` 列**——
  草案第 17/18 行 `s.marketplace` / `s.platform` 在真实表里不存在。该表实有
  `region`（Nullable String）、`country`（Nullable String）、`marketplace_id`
  （Nullable String）。`market`/`platform` 改取哪一列，OQ-2/OQ-3 裁定时未预见
  这个落差，需 Task 4 重新裁定或升级人工，不许照抄草案硬跑。
- ⚠️ **`lingxing_product_local_products` 没有 `local_sku` 列**——草案第 8/10
  行的 `local_sku` 在真实表里不存在，真实列名是 `sku`（Nullable String）。
  `local_sku` 是**另一张表**（`lingxing_product_listing`）里的列，草案在这里
  显然是抄错了源表。
- `lingxing_product_listing` 的 `local_sku` `seller_sku` `sid`（Int64）
  `fulfillment_channel_type` 均存在，草案第 25/33/38 行的用法与真实列一致。
- `lingxing_inventory_warehouses` 的 `wid`（Int64）`type` `sub_type` `name`
  均存在，草案第 41~45 行与真实列一致；该表另有 `country_code`（几乎全空，
  与 CLAUDE.md「38 个国内仓 country_code 几乎全为空」的记载一致，OQ-5 留
  `market=NULL` 的裁定不受影响）。

### 7.0 探针实测列（`system.columns`，2026-09-22）

```
=== jxd_raw.lingxing_seller_list （21 行 / 1 个采集日 / 2026-09-22 ~ 2026-09-22）===
_captured_at                             Nullable(DateTime)
_captured_date                           Nullable(Date)
_task_id                                 Nullable(String)
sid                                      String
seller_id                                Nullable(String)
mid                                      Nullable(Int64)
name                                     Nullable(String)
account_name                             Nullable(String)
seller_account_id                        Nullable(Int64)
region                                   Nullable(String)
country                                  Nullable(String)
marketplace_id                           Nullable(String)
status                                   Nullable(Int64)
has_ads_setting                          Nullable(Int64)
_ingested_at                             DateTime

=== jxd_raw.lingxing_product_local_products （5850 行 / 2 个采集日 / 2026-09-21 ~ 2026-09-22）===
_captured_at                             Nullable(DateTime)
_captured_date                           Nullable(Date)
_task_id                                 Nullable(String)
id                                       Int64
cid                                      Nullable(Int64)
bid                                      Nullable(Float64)
sku                                      Nullable(String)
sku_identifier                           Nullable(String)
product_name                             Nullable(String)
pic_url                                  Nullable(String)
cg_delivery                              Nullable(Int64)
cg_transport_costs                       Nullable(Float64)
purchase_remark                          Nullable(String)
cg_price                                 Nullable(Float64)
status                                   Nullable(Int64)
open_status                              Nullable(Int64)
is_combo                                 Nullable(Int64)
create_time                              Nullable(Int64)
update_time                              Nullable(Int64)
product_developer_uid                    Nullable(Int64)
cg_opt_uid                               Nullable(Int64)
cg_opt_username                          Nullable(String)
spu                                      Nullable(String)
ps_id                                    Nullable(Int64)
attribute                                Nullable(String)
brand_name                               Nullable(String)
category_name                            Nullable(String)
status_text                              Nullable(String)
product_developer                        Nullable(String)
supplier_quote                           Nullable(String)
aux_relation_list                        Nullable(String)
custom_fields                            Nullable(String)
global_tags                              Nullable(String)
_ingested_at                             DateTime

=== jxd_raw.lingxing_product_listing （475760 行 / 58 个采集日 / 2026-07-24 ~ 2026-09-22）===
_captured_at                             Nullable(DateTime)
_captured_date                           Date
_task_id                                 Nullable(String)
listing_id                               String
seller_sku                               Nullable(String)
fnsku                                    Nullable(String)
item_name                                Nullable(String)
local_sku                                Nullable(String)
local_name                               Nullable(String)
price                                    Nullable(Float64)
quantity                                 Nullable(Int64)
asin                                     Nullable(String)
parent_asin                              Nullable(String)
small_image_url                          Nullable(String)
status                                   Nullable(Int64)
is_delete                                Nullable(Int64)
store_type                               Nullable(String)
afn_fulfillable_quantity                 Nullable(Int64)
afn_reserved_quantity                    Nullable(Int64)
reserved_fc_transfers                    Nullable(Int64)
reserved_fc_processing                   Nullable(Int64)
reserved_customerorders                  Nullable(Int64)
afn_inbound_shipped_quantity             Nullable(Int64)
afn_unsellable_quantity                  Nullable(Int64)
afn_inbound_working_quantity             Nullable(Int64)
afn_inbound_receiving_quantity           Nullable(Int64)
currency_code                            Nullable(String)
landed_price                             Nullable(Float64)
listing_price                            Nullable(Float64)
list_price                               Nullable(Float64)
b2b_price                                Nullable(Float64)
b2b_price_discount                       Nullable(String)
open_date                                Nullable(String)
listing_update_date                      Nullable(String)
seller_rank                              Nullable(Int64)
seller_brand                             Nullable(String)
seller_category                          Nullable(String)
review_num                               Nullable(Int64)
last_star                                Nullable(Float64)
fulfillment_channel_type                 Nullable(String)
open_date_display                        Nullable(String)
principal_info                           Nullable(String)
shipping                                 Nullable(Float64)
points                                   Nullable(String)
sid                                      Int64
dimension_info                           Nullable(String)
pair_update_time                         Nullable(String)
small_rank                               Nullable(String)
on_sale_time                             Nullable(String)
first_order_time                         Nullable(String)
global_tags                              Nullable(String)
variant                                  Nullable(String)
total_volume                             Nullable(Float64)
yesterday_volume                         Nullable(Float64)
fourteen_volume                          Nullable(Float64)
thirty_volume                            Nullable(Float64)
yesterday_amount                         Nullable(Float64)
seven_amount                             Nullable(Float64)
fourteen_amount                          Nullable(Float64)
thirty_amount                            Nullable(Float64)
average_seven_volume                     Nullable(String)
average_fourteen_volume                  Nullable(String)
average_thirty_volume                    Nullable(String)
parent_msku                              Nullable(String)
marketplace                              Nullable(String)
seller_category_new                      Nullable(String)
_ingested_at                             DateTime

=== jxd_raw.lingxing_inventory_warehouses （59 行 / 1 个采集日 / 2026-09-22 ~ 2026-09-22）===
query_type                               Int64
_captured_at                             Nullable(DateTime)
_captured_date                           Nullable(Date)
_task_id                                 Nullable(String)
wid                                      Int64
type                                     Nullable(Int64)
sub_type                                 Nullable(Int64)
name                                     Nullable(String)
is_delete                                Nullable(Int64)
country_code                             Nullable(String)
wp_id                                    Nullable(Int64)
wp_name                                  Nullable(String)
local_name                               Nullable(String)
t_warehouse_name                         Nullable(String)
t_warehouse_code                         Nullable(String)
t_country_area_name                      Nullable(String)
t_status                                 Nullable(String)
_ingested_at                             DateTime
```

★ 顺带一提：`lingxing_product_listing` 里其实**有** `marketplace`（Nullable
String）列，只是挂在 listing 表而不是 `lingxing_seller_list`——OQ-2/OQ-3 重新
裁定 `seller.market`/`platform` 时可以留意这条，但要不要用它、怎么去重到
sid 粒度，不属于本 Task 的裁定范围。

### 7.2 SQL 草案（未按上述发现改写，留给 Task 4）

```sql
-- sku_catalog ← jxd_raw.lingxing_product_local_products（17:84，2,925 货号 · 09-21 起采）
SELECT local_sku AS sku, argMax(product_name, _captured_date) AS name,
       max(_captured_date) AS captured
  FROM jxd_raw.lingxing_product_local_products GROUP BY local_sku
-- 空 local_sku 丢弃并计入 drop_reasons.empty_sku

-- seller ← jxd_raw.lingxing_seller_list（OQ-2 裁定 09-22；源名已由兄弟仓代码确认，
--   本仓自己的列名仍待 Task 3 探针核实）
SELECT toString(s.sid) AS seller_id,             -- ★ 店铺号全字段字符串（001:24）
       argMax(s.name, s._captured_date) AS name,
       argMax(s.marketplace, s._captured_date) AS market,
       argMax(s.platform, s._captured_date) AS platform,
       max(s._captured_date) AS captured,
       -- ★ OQ-3 裁定 09-22：has_fba 是派生列，不是源列 —— 该 sid 名下只要有一条
       --   listing 的 fulfillment_channel_type = 'FBA' 就算有 FBA
       --   （依据：兄弟仓 027_fbm_ships_from_overseas_pool.sql:30）
       maxIf(1, l.fulfillment_channel_type = 'FBA') AS has_fba_flag
  FROM jxd_raw.lingxing_seller_list s
  LEFT JOIN jxd_raw.lingxing_product_listing l ON l.sid = s.sid
 GROUP BY s.sid

-- msku_bridge ← jxd_raw.lingxing_product_listing（⚠️ 同上）
-- ★ 裁定（preflight 09-22）：未绑货号的丢弃不在 SQL 里做，落在 Python 侧的
--   fetch_msku_bridge（计划 Task 4）——因为丢弃必须计数进 drop_reasons.unbound_sku，
--   SQL 的 HAVING 会把这一侧静默滤掉，看不出丢了多少（铁律：丢的一侧必须统计）。
--   本条与计划 SQL_MSKU_BRIDGE 保持一致，不在 SQL 里加 HAVING sku != ''。
SELECT seller_sku, toString(sid) AS sid,
       argMax(local_sku, _captured_date) AS sku,  -- ★ 改绑：取 as_of 当时有效的那个
       max(_captured_date) AS captured
  FROM jxd_raw.lingxing_product_listing
 WHERE _captured_date >= today() - 30
 GROUP BY seller_sku, sid                         -- ★★ sid 绝不参与 argMax

-- warehouse ← jxd_raw.lingxing_inventory_warehouses（17:202，118 仓）
SELECT wid, argMax(name, _captured_date) AS name,
       argMax(type, _captured_date) AS type,       -- 1 国内 38 / 3 海外
       argMax(sub_type, _captured_date) AS sub_type,  -- 3/1 自建 14 · 3/2 第三方 7
       max(_captured_date) AS captured
  FROM jxd_raw.lingxing_inventory_warehouses GROUP BY wid
```

★★ `GROUP BY seller_sku, sid` 是铁律：把 sid 也 `argMax` 掉，店铺归属就取决于
「最后一次采到它时采的是哪个店」—— 邻居仓实测美国两店的 msku 数在 08-18~08-25
之间摆动于 **735~1445**，接近 2 倍。

`warehouse.kind` 映射（`001:54` 的 CHECK 只认四个值）：`type=1 → local`；`3/1 →
oversea_self`；`3/2 → oversea_3pl`；**其余一律硬失败**。`fba` 这一档在这张表里
没有源（§10 OQ-4）。`market` 对国内仓留 `NULL` 并计数（§10 OQ-5）—— 留空是
「还没到」，写成 `''` 就再也分不开。

### 7.3 取数放哪一层（F-9 定的，不是偏好）

| 文件 | 装什么 |
|---|---|
| `shared/ch_client.py` | 建客户端（免代理 PoolManager）。★ `dim/` 不许 import 它 |
| `dim/ch_source.py` | SQL 文本 + 纯变换 + 丢弃计数；★ **客户端由调用方注入**：`fetch_seller(query: Callable[[str], list[tuple]]) -> Fetched` |
| `jobs/refresh_dims.py` | 建客户端 · 覆盖面校验 · upsert · 写 `dim_refresh_run` |

★ **PG 的 upsert SQL 必须写在 `jobs/`** —— `test_l7_no_writes_to_clickhouse` 按正则扫
`dim/` 里的 `insert into`，一段 upsert 字符串就会让它红（F-9）。

---

## 8. 启动检查（补 F-6 的缺口；OQ-8 裁定 09-22 之后的形态）

★ **本节已按 OQ-8 裁定改写**（控制器 09-22）：启动检查**不拦启动**，也**不拦**
`/health` / `/v1/readiness` —— 它只做两件事：记一条新鲜度日志，以及在
`gate_503` 镜像陈旧时**触发**一次立即刷新，不是**拒绝**。真正的拒绝服务只发生
在业务路由，按请求走 `require_fresh_mirrors` 逐请求 503。旧版「抛异常、进程
起不来」的设计（曾对应 `03:1622` 的「启动钩子」原文）已作废，`03:1622` 已改写为
「启动钩子：记录 + 触发刷新；拒绝服务只在业务路由按请求判」。

lifespan 次序：`setup_logging()` → 起调度器 → `_startup_check()`：读
`v_mirror_freshness`，对每张镜像打一条 `startup mirror=... refreshed_at=...`
的日志（沿用既有的 `_log_startup`）；若任一 `gate_503` 镜像陈旧
**且** `[freshness] startup_gate = true` → 通过调度器与 CLI 共用的同一个刷新
入口（`jobs.refresh_dims.refresh_all("scheduler")`，同一把 PG advisory lock）
**排入一次立即刷新**；`startup_gate = false` → **只记日志，不触发刷新**（配置
键名沿用 `startup_gate`，语义从「是否拒绝启动」改成「是否在启动时触发刷新」）。
刷新本身失败（连不上 CH、掉档拒批等）**只记日志，不阻止 `yield`**——
`_startup_check()` 全程不抛出会中断 lifespan 的异常。

★ **必须验证的三件事**（都不涉及拒绝启动）：
① 全部镜像陈旧时应用照常起来，`/health` 200，`/v1/readiness` 如实报告陈旧的
镜像，业务路由（如 `/v1/plans`）仍按请求返回 503 `mirror_stale`，且刷新入口
恰好被调用一次；② `startup_gate = false` 时刷新入口一次都不调用（只有日志）；
③ 启动时触发的那次刷新本身抛异常，要连同异常原因一起被记下来，且不能让应用
起不来。

---

## 9. 阶段 B/C/D 怎么加条目

1. `pending` 改 `False`，补 `source` / `fetch` / `coverage` / `depends_on`。
2. 只追加的四张（`po_snapshot` `po_receipt` `ext_stage_observation` `sales_actual`）
   `kind="append"`：**不 upsert，只 INSERT**，没有 `refreshed_at` 列，`staleness="none"`，
   门禁 (b) 的第 ②③ 条按 `staleness` 跳过。
3. `supplier` 是 `mutable`，源是领星 `/erp/sc/data/local_inventory/supplier`（`08:243`）
   而不是 CH —— 走 `erp/`，`fetch` 由 `erp` 侧提供。
4. 新条目一律**先让门禁 (a) 红一次**（先加 `03` 的行、不加登记），再补登记。

---

## 10. 开放问题（全部不许自己定，按 `CLAUDE.md` 纪律 ③ 进 `00-待裁定清单`）

| # | 问题 | 猜错的代价 |
|---|---|---|
| **OQ-1** | 四张 CH 源表的**真实列名**本仓文档一字未写（`17:84`/`17:202` 只给了表名与量级） | SQL 写出来跑不通，或跑通了取错列而没人发现 |
| **OQ-2** | `seller` ← `lingxing_seller_list`、`msku_bridge` ← `lingxing_product_listing` —— 这两个源名**只见于兄弟仓 `model_inventory_forecast` 的代码**，本仓 `17`/`08`/`03` 全无记载 | 换了源表而两边都以为对方写过 |
| **OQ-3** | `seller.has_fba`（`001:29` NOT NULL）没有任何数据源 | 猜成 `true` → 无 FBA 的平台店会拿到 `0` 而不是「不适用」，正是 `02` §3.1a 点名要分开的那件事 |
| **OQ-4** | `warehouse.kind` 的 `fba` 一档在 `lingxing_inventory_warehouses` 里没有源（该表 118 仓全是国内/海外仓） | FBA 站点进不了 `warehouse`，阶段 C 排货的目的地缺一整类 |
| **OQ-5** | `warehouse.market` 对 38 个国内仓怎么给 —— 兄弟仓的办法是解析中文仓名并落 `warehouse_id`，但那个生成器（`gen_locations.py`）不在本仓 | 留 NULL 则阶段 C 无从按市场归属；就地解析则违反「解析只能在一个地方」 |
| **OQ-6** | `coverage_drop_threshold` 取多少 —— **未实测**。兄弟仓实测过库存日报会整天缺、单日掉 40% | 定高了掉档照写，定低了正常波动天天拒批 |
| **OQ-7** | `max_age_hours = 24` 是否仍成立（F-7 自己说「刷新频率定下来后按实测改」）；`refresh_at="06:30"` 落在 CH 采集窗口之后**没有实测依据**，`07:481` 只说了「之后」 | 刷在采集窗口中间 → 每天都拿半批数据，而它看起来完全正常 |
| **OQ-8** | `startup_gate` 真开时，CH 或 PG 一挂服务就起不来，连 `/health` `/v1/readiness` 都没了 —— 而那正是要用来查「为什么起不来」的两个口子。`03:1622` 写的是拒绝服务，没写拒绝到什么程度 | 一次 CH 抖动变成一次人工到场 |
| **OQ-9** | `sku_category` / `category_refresh` 至今无迁移（F-10），PM 接入方式只在记忆里（Keycloak service account → Bearer），`08:250` 只有一行 | 登记表里挂着一个永远 pending 的条目，久了没人记得它为什么 pending |
| **OQ-10** | `refresh_timezone`（`CronTrigger` 必须给时区，容器里的本地时区不可靠）—— 本仓文档从未出现过时区 | 06:30 落在 UTC 上就是北京时间 14:30，正好在采集窗口里 |

### 10.1 裁定（controller，2026-09-22）

- **OQ-1 已解决**（Task 3，实测 2026-09-22，内网 `jobs/probe_ch.py` 跑通）：
  四张源表的真实列名已打出并回填进 §7.0。发现两处 §7.2 草案与真实列名不符，
  留给 Task 4 处理，不在本 Task 里改 SQL：`lingxing_seller_list` 没有
  `marketplace`/`platform` 列（真实是 `region`/`country`/`marketplace_id`）；
  `lingxing_product_local_products` 没有 `local_sku` 列（真实是 `sku`，
  `local_sku` 其实在 `lingxing_product_listing`）。`lingxing_product_listing`
  与 `lingxing_inventory_warehouses` 的草案列名与实测一致，无需改。
- **OQ-2 裁定**：`seller` ← `jxd_raw.lingxing_seller_list`；`msku_bridge` ←
  `jxd_raw.lingxing_product_listing`。依据：兄弟仓 `model_inventory_forecast`
  `data/schema/026_channel_is_brand_by_country.sql:11`「实测 2026-09-10
  （jxd_raw.lingxing_seller_list）」· `data/tools/trace_asin.py:75`；
  `data/schema/002_dimensions.sql:59` 的 source 默认值 · `data/tests/test_profile_parity.py:215`。
  已回填 `docs/03` §1 第 85~86 行与 `docs/17`（见 mirror-refresh 文档增量提案第 6/7 节）。
- **OQ-3 裁定**：`seller.has_fba` 是**派生列**，不是源列 ——
  `has_fba = EXISTS(该 sid 名下任一 listing 的 fulfillment_channel_type = 'FBA')`，
  取自 `jxd_raw.lingxing_product_listing`（依据：兄弟仓
  `027_fbm_ships_from_overseas_pool.sql:30`「一律用
  lingxing_product_listing.fulfillment_channel_type」）；`platform` 取自
  `lingxing_seller_list`，字段名待 Task 3 探针确认。`fetch_seller` **不许再抛**——
  计划 Task 4 已按此改写（`SQL_SELLER` 加 join/派生列、`fetch_seller` 计算
  `has_fba`），Task 4 Step 3、Task 5 的混合测试、Task 8 Step 6、Self-Review 里
  「seller 会以 `ok=false` 出现」的说明已删除或改写。★ §7 的 `SQL_SELLER` 草案
  与其下方「has_fba 无源 → 裁定前刻意抛」的注记已按本裁定更新。
- **OQ-4 裁定**：本轮刷新只写 `local` / `oversea_self` / `oversea_3pl` 三种
  `kind`；`fba` 在阶段 A **不产出**（FBA 库存是按店铺持有的，见 `01`/`03`）；
  PG 的 `kind` CHECK 约束**保留** `fba` 这个取值，留给阶段 C 用。
  ★ 刷新时按 `kind` 记一条计数日志（多少仓落到哪一档）。
- **OQ-5 裁定**：阶段 A 里 38 个国内仓的 `warehouse.market` 一律留 `NULL`；
  刷新作业**不做中文仓名解析**（解析只能在一个地方 —— 兄弟仓的
  `gen_locations.py` —— 这次刷新不是那个地方）；刷新时记一条 `market_null`
  计数日志。阶段 C 重新开放这个问题。
- **OQ-6 裁定**：`coverage_drop_threshold = 0.30`。★ **设计取值 · 未实测** ——
  邻仓实测库存日报单日掉 40% 是坏批，取一个比它更松的阈值，避免把 30% 以内的
  正常波动也当成掉档拒批。
- **OQ-7 裁定**：`max_age_hours = 24`、`refresh_at = "06:30"` 均保留，两处注释
  继续标「设计取值 · 未实测」；`refresh_timezone` 按 OQ-10 显式给
  `"Asia/Shanghai"`，`jobs/scheduler.py` 的 `CronTrigger` 必须显式传它，
  不依赖容器本地时区。
- **OQ-8 裁定**：`startup_gate` **不拦启动**，也**不拦** `/health` /
  `/v1/readiness`；启动时只记一条新鲜度日志，若有 `gate_503` 镜像陈旧就立刻
  触发一次刷新；真正的拒绝服务只在业务路由按请求判（`require_fresh_mirrors`
  逐请求 503）。`docs/03:1622` 已按「启动钩子：记录 + 触发刷新；拒绝服务只在
  业务路由按请求判」改写。
  ✅ **本文 §8「启动检查」与计划 Task 7 已按本裁定重写**（09-22 同批变更）：
  `_startup_check()` 不再抛 `MirrorStale`、不再拦 `lifespan`，只记日志 + 通过
  与调度器共用的刷新入口触发一次刷新；`/health` / `/v1/readiness` 永远可达，
  拒绝服务只在业务路由逐请求 503。`MirrorStale` 异常类与「抛异常拒绝启动」的
  设计已从 §8 与 Task 7 中移除。
- **OQ-9 裁定**：`sku_category` / `category_refresh` 本轮仍登记为 `pending`
  （阶段 A、`label_only`），本计划不新增迁移。
- **OQ-10 裁定**：`refresh_timezone = "Asia/Shanghai"`，`CronTrigger` 显式
  传入，不依赖容器本地时区（与 OQ-7 同一条裁定的一部分）。
