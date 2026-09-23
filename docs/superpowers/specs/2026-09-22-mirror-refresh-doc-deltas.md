# 维度镜像刷新 · 文档增量提案（2026-09-22）

> ★ **本文只提案，不执行。** 五处都在裁定层/设计层，按 `CLAUDE.md` 纪律 ③
> 由主控落笔。实施者只核对是否已落（计划 Task 8 Step 5）。
>
> 设计：`docs/superpowers/specs/2026-09-22-mirror-refresh-design.md`
> 计划：`docs/superpowers/plans/2026-09-22-mirror-refresh.md`

---

## 1. `docs/00-待裁定清单.md` —— S 组新增 S-33

**位置**：第 467 行（`| S-32 | …`）之后、第 468 行空行之前，在
「### ★ 全部已裁定（2026-09-22）」那张表（表头 `| # | 裁定 | 依据 | ★ 错了的代价 |`）末尾追加一行。

```markdown
| ★ S-33 | **维度镜像刷新**：① **APScheduler 进程内定时**（负责人裁定 09-22；不用外部 cron，该实例也没有 `pg_cron`）；② 每日一次，时刻 `[freshness] refresh_at`，**在 CH 采集窗口之后**；③ 三个入口——调度 / CLI `python -m jobs.refresh_dims` / 仅运维可用的 `POST /v1/jobs/refresh-dims`——共用**一把 PG advisory lock**，第二个来的拿 409、**不排队**；④ ★ **`dim/registry.py` 是「有哪些镜像」的唯一真相**，`03` §1 与 `v_mirror_freshness` 由门禁钉住；⑤ 掉档超 `coverage_drop_threshold` → **拒绝整批、保留旧镜像**，写 `dim_refresh_run(ok=false)`；⑥ ★ **只 upsert，绝不 DELETE 源里消失的行** | 频率/触发/读哪一版/先校采集面：`07:481-485`（D-2 同一条理由——CH 延迟 ≥ 1 天）· 拒绝服务：`03:1616-1626`（E-4）· `01:274` · 进程内调度：**负责人 09-22 裁定** | 散着刷 → 「有哪些镜像」有两份真相，`03` 新增一行没人刷而**接口照常 200**；不互斥 → 三个入口同写一张表；把掉档读成 0 → 有货的仓整批消失，看起来像出库了（兄弟仓实测「28-A4pet英国仓 40 件」就这样没过）；DELETE 消失行 → `plan_cell_msku` 的外键指着 `msku_bridge`，一张已提交的计划会失去依据 |
```

★ 同时把文末「三组裁定汇总表」（`00` 最后一节）里 S 组那一行的条数从 32 改成 33
（若该表列了 S 组条数；当前文末表未列 S 组，则**不改**）。

---

## 2. `docs/01-架构设计.md` —— §6 可观测表补一行

**位置**：第 274 行（`| 维度镜像陈旧 | ★ **拒绝服务**（E-4），…|`）与第 275 行
（`| 品类镜像陈旧 | …|`）之间插入。

```markdown
| ★ 镜像刷新 | ★ **APScheduler 进程内**每日一次（S-33），三入口共用 PG advisory lock；每轮进 `dim_refresh_run`：打的谁 · 多久 · 进来多少行 · 丢了多少行 · 为什么丢 | 散着刷等于「有哪些镜像」有两份真相 |
```

★ **不改** §7 的目录结构块里 `jobs/` 那一行的说明文字（「回执器与定时」已经涵盖）。

---

## 3. `docs/00e-分阶段实施计划.md` —— 两处

### 3.1 §1「阶段 A · 销售计划」的「表」一行后追加一行

**位置**：第 42 行（`| 表 | 地基 \`actor\` + 四张镜像 · …|`）之后。

```markdown
| ★ 刷新 | ★ **镜像刷新**（S-33）：`dim/registry.py` 登记表 + `jobs/refresh_dims.py` + APScheduler 进程内日调度 + `dim_refresh_run` 留痕（迁移 `006`）。★ **四张 DIM 镜像不自己刷起来，阶段 A 的外键就永远是手工种的** |
```

### 3.2 §1 阶段 A「校验判据」代码块追加第 ⑦ 条

**位置**：第 53~60 行那个 ``` 块里，在 `⑥ ★ 前端 mock 与真 API 两种数据源跑出同一屏` 之后。

```
⑦ ★ 四张 DIM 镜像能从 CH 刷出来，掉档超阈值拒绝整批且旧镜像不动，每轮在
  dim_refresh_run 里查得到（谁触发 · 多少行进 · 多少行丢 · 为什么丢）
```

### 3.3 §5.2 文档地图表追加一行

**位置**：第 228 行（`| \`prompt.md\` | 项目指令书 |…`）**之前**（保持 `prompt.md` 在最后）。

```markdown
| `superpowers/specs/2026-09-22-mirror-refresh-design.md` | 设计 | ★ **镜像登记表 · 两道门禁 · 调度形态 · 四张镜像的取数 SQL** | 阶段划分（问 `00e`）· 表结构（问 `03`） |
```

★ 判据：这一份**对「有哪些镜像、谁刷、刷失败怎么办」说了算**；`03` 仍对表结构说了算。
两边重叠的只有 `dim_refresh_run` 一张表，而它的 DDL 在 `03`（见第 4 条）。

---

## 4. `docs/03-数据库表清单.md` —— 两处

### 4.1 §1 表清单总览追加 `dim_refresh_run`

**位置**：第 96 行（`| 42 | \`plan_cell_dropped\` |…`）之后，编号 43。

```markdown
| 43 | **`dim_refresh_run`** | 系统 | OWN | ★ 只追加 | ★ **S-33** 镜像刷新留痕 |
```

★ 同时把第 51 行的标题 `## 1. 表清单总览（42 张）` 改成 `（43 张）`，
并把第 98 行「★ 共 **42 张**…」那句里的 42 改成 43、`41+2` 改成 `42+2`。
★ **这三处必须同改** —— 门禁 (a) 的 `test_doc_table_parser_actually_sees_the_table`
断言解析到的行数 ≥ 42，标题与正文分叉不会让它红，但下一个读文档的人会数错。

### 4.2 §7.1 E-4 之后新增 §7.2

**位置**：第 1631 行（`v_mirror_freshness` 的 `CREATE VIEW` 代码块结束）之后、
第 1633 行「★ **配置类错误往启动钩子放**…」之前。

````markdown
#### 7.2 谁来刷 —— 登记表与留痕（S-33）

★ **「有哪些镜像」的唯一真相是 `dim/registry.py`**，不是本清单也不是
`v_mirror_freshness`。两者由 `tests/test_mirror_registry.py` 的两道门禁钉住：
(a) 本节 §1 里归属含 MIRROR / DIM 的行 ≡ 登记表的名字（**集合相等**，双向）；
(b) 每个非 `pending` 条目都有可调用的取数函数、有 `refreshed_at` 列、
在 `v_mirror_freshness` 里有一行。
★ 于是**本清单新增一行 MIRROR 而没人刷它**这件事会红，而不是安静地永远陈旧。

```sql
CREATE TABLE scm.dim_refresh_run (         -- ★ 只追加：刷新是证据，不是状态
    run_id              bigserial PRIMARY KEY,
    mirror              text        NOT NULL,          -- = 登记表里的 name
    trigger             text        NOT NULL CHECK (trigger IN ('scheduler','cli','api')),
    actor               text        REFERENCES scm.actor(actor_id),   -- ★ 可空：调度没有人
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    source_max_captured date,          -- 该批 CH 数据的 max(_captured_date)（07:484）
    rows_in             integer,
    rows_dropped        integer,
    drop_reasons        jsonb,         -- ★ 丢的那一侧按原因分列，含首轮的 no_baseline
    ok                  boolean     NOT NULL DEFAULT false,   -- ★ 崩在半路要看起来像失败
    error               text
);
CREATE INDEX dim_refresh_run_by_mirror ON scm.dim_refresh_run (mirror, started_at DESC);
```

★ **三条判据**

| | |
|---|---|
| `refreshed_at` **只在成功时**更新，与 upsert **同一事务** | 失败 → 旧镜像原封不动，而不是半新半旧 |
| 本轮行数较上一次 `ok=true` 跌幅超 `[freshness] coverage_drop_threshold` → ★ **拒绝整批**（`07:485`）；`msku_bridge` 另比 `distinct sid` | 整店 0 行是**采集缺口的形状**，总行数看不出来 —— 静默丢失已踩六次 |
| ★ **只 upsert，绝不 DELETE 源里消失的行** | 消失 ≠ 没了；何况 `plan_cell_msku` 的外键指着 `msku_bridge`，删一行就是让一张已提交的计划失去依据 |

★ `actor` 可空是刻意的：写成 `'system'` 会让「机器跑的」与「某个叫 system 的人跑的」
长得一模一样。
````

---

## 5. `config.example.toml` —— `[freshness]` 段整段替换

**位置**：第 24~28 行（当前 `[freshness]` 段）整段替换为：

```toml
[freshness]
# ★ 维度镜像陈旧超过它 → 拒绝服务（E-4）。
# ★ 设计取值 · 未实测 —— 文档没给过阈值（设计 §10 OQ-7）。
max_age_hours = 24

# ★ 进程内 APScheduler 每日刷一次的时刻（S-33）。
#   07:481 说的是「在 CH 采集窗口之后」——「之后」是文档原话，
#   06:30 是设计取值 · 未实测（OQ-7）。
refresh_at       = "06:30"
# ★ 容器里的本地时区不可靠，必须显式写：06:30 落在 UTC 上就是北京时间 14:30，
#   正好在采集窗口中间 —— 而那样每天都拿半批数据，看起来完全正常（OQ-10）。
refresh_timezone = "Asia/Shanghai"

# ★ false = 不起调度器（测试、只跑 CLI 的部署）。
#   关着时启动日志里有一条 WARNING —— 静默不启动与启动了长得一样。
scheduler_enabled = true

# ★ 本轮行数较上一次成功轮跌幅超过它 → 拒绝整批、保留旧镜像（07:485）。
# ★ 设计取值 · 未实测（OQ-6）：兄弟仓实测过库存日报单日掉约 40%。
coverage_drop_threshold = 0.20

# ★ 启动时维度镜像仍陈旧 → 进程起不来（03 §7.1 E-4 的「启动钩子」那一半）。
#   测试夹具置 false —— 它 TRUNCATE 掉四张镜像。
#   ⚠️ 开着时 CH 或 PG 一挂，/health 与 /v1/readiness 一起没了，而那正是
#      用来查「为什么起不来」的两个口子（设计 §10 OQ-8，未裁定）。
startup_gate = true
```

---

## 6. 原「五处没有覆盖」—— 已由控制器裁定（2026-09-22），状态更新

| # | 什么 | 裁定结果 |
|---|---|---|
| OQ-2 | `seller` / `msku_bridge` 的 CH 源表名**本仓文档一字未写**，只见于兄弟仓代码 | ★ **已裁定**：`seller` ← `jxd_raw.lingxing_seller_list`；`msku_bridge` ← `jxd_raw.lingxing_product_listing`（依据见设计 §10）。已补进 `docs/03` §1 第 85~86 行与 `docs/17`（本文件第 4.1 节之外的追加编辑，见下方第 7 节） |
| OQ-3 | `seller.has_fba`（`001:29` `NOT NULL`）**无数据源** | ★ **已裁定**：派生列，`EXISTS(该 sid 的 listing 里 fulfillment_channel_type='FBA')`，取自 `lingxing_product_listing`；`fetch_seller` 不再抛异常，计划 Task 4/5/8 已按此改写 |
| OQ-4 | `warehouse.kind` 的 `fba` 一档无源（`lingxing_inventory_warehouses` 全是国内/海外仓） | ★ **已裁定**：阶段 A 只写 `local`/`oversea_self`/`oversea_3pl`，`fba` 不产出；CHECK 保留该值给阶段 C；刷新按 kind 记一条计数日志 |
| OQ-5 | `warehouse.market` 对 38 个国内仓怎么给（中文仓名解析的生成器不在本仓） | ★ **已裁定**：阶段 A 一律留 `NULL`，刷新作业里不做中文仓名解析；记 `market_null` 计数日志；阶段 C 重新开放 |
| OQ-9 | `sku_category` / `category_refresh` 至今**无迁移**，PM 接入方式未落文档 | ★ **已裁定**：本轮仍登记 `pending`（阶段 A、`label_only`），本计划不新增迁移 |

★ OQ-1、OQ-6、OQ-7、OQ-8、OQ-10 的裁定见设计 `2026-09-22-mirror-refresh-design.md` §10.1；
其中 OQ-1 是**唯一保持开放**的一条 —— 要等计划 Task 3 的只读探针把真实列名打出来才算解决。

---

## 7. 控制器裁定对本文原提案的覆盖（2026-09-22）

- **OQ-6**：本文件第 5 节原提案 `coverage_drop_threshold = 0.20`**已被裁定推翻** ——
  控制器定的是 **0.30**（邻仓实测库存日报单日掉约 40% 是坏批，0.20 太紧会把
  30% 内的正常波动也当掉档拒了）。`config.example.toml` 已按 0.30 落地，
  计划里 `_threshold()` 的默认值与 `_FRESHNESS_DEFAULTS` 也需同步改成 0.30
  （落在计划 Task 5 / Task 6，不在本文件五处提案范围内，见执行报告）。
- **OQ-8**：本文件原未提出 `docs/03:1622` 的改写（当时 OQ-8 未裁定）。
  控制器 09-22 裁定后追加了这一处改写：把「拒绝服务（启动钩子 + 接口 503）」
  改成「拒绝服务：启动钩子只记录 + 触发刷新，拒绝服务只在业务路由按请求判」，
  已直接落在 `docs/03` 第 1622 行（本文件第 4.2 节之外的追加编辑）。
  ✅ 设计 §8「启动检查」与计划 Task 7 已在同一批变更里按 OQ-8 重写（09-22）：
  不再抛异常拒绝启动，只记日志 + 通过与调度器共用的入口触发一次刷新，
  `/health` / `/v1/readiness` 永远可达，拒绝服务只在业务路由逐请求 503。
