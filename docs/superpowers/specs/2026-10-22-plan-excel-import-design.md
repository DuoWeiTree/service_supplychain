# 销售计划 Excel 导入 · 查询接口 · 硬删除 —— 设计说明

> 裁定：`docs/00` IM 组 IM-D1 ~ D9（负责人 2026-10-22 对话）。
> 改动面（预估）：新 `api/pub/`（首次出现）· `api/ui/plans.py` · `usecase/`（认领 · 推演入参 · 导入 · 删除从 `api/ui` 抽下来）·
> `rules/`（导入校验，纯函数）· 迁移（AI 员工 actor）· `web/src/pages/OpsHome.tsx` · `pyproject.toml`（xlsx 读写库）·
> 外部 AI 用的 skill · `docs/01` §3.1 · `docs/08` · `docs/10` §2.5 · `docs/00`。

## 0. 意图

负责人要为「日后 AI 代替人操作」做准备。两条路**顺序不同、本质一样**（负责人 10-22，IM-D10）：

```
界面（人）：新建计划 → 添加产品（认领 msku）→ 在整表里填数
            或：新建计划表单里直接选 Excel → 一步建好

外部 AI：  1. 问用户要生成哪些 SKU / MSKU
           2. 查 ①映射 + ②预测（库存与销量）
           3. 生成 Excel：只含用户选的产品，填建议销量 / 采购
           4. 人审查 Excel、确认数量
           5. ③导入 = 新建计划（产品由 Excel 带进来，不再单独「添加产品」）
              ↓ 有小错 → 人进整表 UI 调
              ↓ 错太多 → ④硬删除，重新生成再导入
```

★ 界面上「先建计划、再添加产品」这一步，在 AI 这条路上由「按用户选择查询后生成 Excel」代替；落到库里都是同一组认领与格子。

**目前以人操作为主**（IM-D6）。AI 的 prompt / 生成逻辑**不在本次范围**（负责人原话：「如何生成的 AI prompt 不在我们本次范围」）。

| | 本次做 | 不做 |
|---|---|---|
| ① | 映射查询：店铺 · 货号 · msku，可按货号 / msku / 店铺 / 国家筛 | |
| ② | 预测查询：指定 msku（或货号）的预测销量与预测库存 | |
| ③ | Excel 导入建新计划：界面（新建计划表单里）+ 接口 | 导入到已有计划（IM-D1）|
| ④ | 硬删除计划接口 | 界面删除按钮（IM-D5，人用归档）|
| ⑤ | 给外部 AI 用的 skill：怎么调 ①~④、Excel 格式、错误码怎么处理（IM-D7）| |
| | | 系统内任何 AI 功能（IM-D7）；AI prompt；「AI 来源」标记（IM-D2：AI 填的就是人填的）|

**反目标**（技术上能跑也算失败）：
- 导入一半成功，留下半张计划或一张空计划（IM-D3：整体拒绝）。
- 拒绝时只报第一个错 —— AI / 人要改一轮报一个，十轮才改完。**所有错一次列全**。
- Excel 里空格子被读成 0（空 ≠ 0）。
- 界面导入与接口导入结果不一致 —— 两条路必须是同一个后端函数。

## 1. 接口总览

调用方是**外部程序**（IM-D7）→ AI 用的接口全部进 `api/pub/`（对外契约：字段冻结，改动走契约版本，`08` 逐条标注）。
界面只打 `api/ui/`（L9），所以导入与模板在 `api/ui/` 另有一份薄壳。

| # | `api/pub/`（外部 AI）| `api/ui/`（本仓界面）| 用途 |
|---|---|---|---|
| ① | `GET /pub/v1/mappings` | — | 映射清单 |
| ② | `POST /pub/v1/forecast/preview` | — | 预测销量 + 预测库存 |
| ③ | `POST /pub/v1/plans/import` | `POST /plans/import` | 解析 Excel → 建计划 + 认领 + 写格，一个事务 |
| ③' | `GET /pub/v1/plans/import/template` | `GET /plans/import/template` | 按起始月 / 月数生成空模板 xlsx |
| ④ | `DELETE /pub/v1/plans/{plan_id}` | — | 硬删除 |

（路径前缀 `/pub/v1` 是我的写法，实现时与 `01` §7 / `08` 的 pub 约定对齐即可。下文 ①~④ 不再重复前缀。）

### 1.0 分层与身份

- ★ **L10：`api/pub/` 不许 import `api/ui/`**。所以两边共用的东西必须下沉到 `usecase/`（它不许知道 `api/`，
  错误用 usecase 自己的异常，由两边各自翻成 `ApiError`）：
  - 认领一个 msku（现在在 `api/ui/plans.py::claim` 里，自己开连接）→ 抽成「在给定游标里认领」；
  - 推演入参的收集与序列化（现在在 `api/ui/plans.py::grid` + `api/ui/grid_forecast.py`）→ 抽成不依赖 `plan_id` 的函数；
  - 导入、删除本身。
  `claim` / `grid` 改成调这些函数，行为与现有测试不变。
- `api/pub/` 第一次出现：`tests/test_layering.py::test_new_layer_dirs_bring_their_own_rules` 要求 L8~L10 有测试守着
  （`tests/test_layering_web.py` 已在，核对 L10 那条真的覆盖到新目录、并**先弄失败一次**）。
- **AI 员工身份**（IM-D8）：种子迁移加一个 actor（id `ai.agent`、名「AI 员工」—— 写法是我的判断）。
  **不进**界面操作人下拉（`web/src/shell/actors.ts`）；`tests/test_actor_seed.py` 现在断言「TS 常量 = 007 种子」，
  要改成「TS 常量 = 种子 − 非界面身份」，非界面身份在一处列明。
- pub 的写接口（③ ④）**只认 AI 员工身份**，别的 actor → 403 `actor_not_allowed`（负责人 10-22 确认，IM-D10）。
  身份仍走 `x-actor` 头 —— 与全系统现状一样**可伪造**（S-28，内网），登录 / RBAC 接入时一起换。

### ① `GET /mappings`

查询参数（都可选，可组合，AND）：`sku` · `seller_sku` · `sid` · `market`（国家，取 `seller.market`）· `limit`。
**一个都不给 → `need_query=true` 空列表**（同 `api/ui` 的 `/catalog/skus`：「没给条件」与「查不到」分得开）。

每行一个 (msku, 店铺)：

```json
{ "sku": "...", "sku_name": "...", "seller_sku": "...", "sid": "...",
  "seller_name": "...", "market": "US", "platform": "amazon", "has_fba": true,
  "claimed_by": { "plan_id": 12, "title": "..." } | null }
```

- ★ `claimed_by`（我的判断）：这个 msku 当前被哪张计划占着（`msku_claim` 活认领，口径同 `claim()` 的 409 判据）。
  导入会因为占用整体失败（IM-D3），AI 生成前就该知道。
- 返回 `matched` / `truncated` / `limit`；inner join 吃掉的行（msku 的店铺或货号不在镜像）要计数回显，同 `catalog_skus_recent`。

### ② `POST /forecast/preview`（pub）

```json
{ "period_start": "2026-11", "months": 3,
  "mskus": [{"seller_sku": "...", "sid": "..."}],   // 与 skus 至少给一个
  "skus": ["..."] }                                  // 货号 → 展开成它在桥表里的全部 msku
```

**口径（我的判断）**：= 「一张刚建好、只认领了这些 msku、人一格没填」的计划，整表会显示的数。即：
- 预测销量 = 认领时种进 `system_units` 的同一个计算（同一来源、同一 `system_basis`），逐月；
- 预测库存 = 整表同一套推演（`forecast/plan_forecast.py`），期望销量取预测销量，**本计划采购为空**；
  已下采购单的在途照整表口径进（`source.purchase_in_transit(sku)`：CH 全部在途采购单，不分哪张计划下的）。
- 不写库、不认领。

返回按 msku 逐月（预测销量、期初、期末、断货日、状态与原因）+ 按货号的本地仓 / 在途，字段名与 `/plans/{id}/grid`
平铺的推演键（`forecast_sku` 等）对齐，不另起一套。★ 回显依据：`as_of`、`system_basis`（用了哪几个月历史）、不适用 / 未知照实写，不填 0。

★ 实现上把 `grid()` 里「取推演入参 → 调推演 → 序列化」抽成一个不依赖 `plan_id` 的函数，`grid` 与 `preview` 共用 ——
两份抄写的推演迟早对不上。

### ③ `POST /plans/import`（pub 与 ui 各一份薄壳，同一个 usecase 函数）

`multipart/form-data`：`title` · `period_start`（`YYYY-MM`）· `months` · `file`（xlsx）。可选 `owner_actor`，缺省 = `x-actor`。

**流程 —— 先全查、后一次写：**

```
1. 抬头校验（同 POST /plans：标题必填、months 1~24 整数）
2. 解析 xlsx → 行（§2 格式）；格式错逐格收集
3. 对库校验（只读）：msku 在不在桥表、店铺 / 国家 / 货号对不对得上、是否被别的计划占用、采购货号有没有对应销量行
4. 有任何错 → 422，返回全部错误，一张计划都不建
5. 一个事务：INSERT plan → 逐个认领（种格子 + 预测）→ 写人填的格子 → 提交
   ★ 3 的占用检查只是读；裁决仍在认领的 INSERT（唯一索引）。并发撞上 → 整个事务回滚 → 同样 409 列出
6. 201 { plan_id, mskus, skus, demand_cells, purchase_cells, elapsed_ms }
```

- 现有 `claim()` 自己开连接、一次一个。要抽出「在给定游标里认领一个 msku」的函数，`claim()` 与导入共用；
  认领的规则（取数源、不适用、复活已释放）只有一份。
- 写格复用 `put_cells` 的单格校验（`_batch_cell` / `_units`）；★ `_units` 只收 `int`，Excel 读出的 `12.0` 要先规整成 12 再交给它（非整数值仍报 `bad_units`）；★ 导入**不受** `CELLS_BATCH_MAX = 1000` 限制
  （100 msku × 24 月就超了），它限的是一次界面操作；导入的上限见 §2 末。
- 界面：成功 → 跳 `/plans/{plan_id}`。

### ③' `GET /plans/import/template?period_start=2026-11&months=3`（pub 与 ui 各一份）

返回 xlsx：表头一行（§2 的固定列 + 月份列）+ 一行销量示例 + 一行采购示例（示例行标明删掉）。
界面的「下载模板」与 AI 都用它，保证格式只有一个出处。（我的判断：人也可能手做 Excel，界面需要格式来源。）

### ④ `DELETE /plans/{plan_id}`（只 pub）

- ★ 有任何 `plan_rev` → **409 `plan_submitted`**（IM-D4），不删。已下采购单必然已提交，被这条包含。
- 否则一个事务：删格子 → 删认领（`msku_claim` 无级联，显式删）→ 删 `plan`。任何残留外键（例如 `sheet.plan_id`）
  撞上 → 回滚，409 点名是哪张表挡住，**不级联去删别的**。
- 200 `{ plan_id, title, mskus, demand_cells, purchase_cells }`。
- ★ 库里不再留痕（IM-D5），**删除日志是唯一痕迹**：`op=delete_plan plan_id title actor mskus=[...] cells=N elapsed_ms outcome`。
- 已归档未提交的计划同样可删。

## 2. Excel 格式

只读**第一个工作表**。第 1 行是表头，从第 2 行起每行一条。

| 列 | 销量行 | 采购行 | 说明 |
|---|---|---|---|
| `类型` | `销量` | `采购` | 其它值 → 错 |
| `货号` | 可填 | **必填** | 销量行填了就必须与桥表里这个 msku 的货号一致 |
| `店铺ID` | **必填** | 空 | = `sid`；★ 判定店铺**只认它** |
| `店铺` | 可填 | 空 | 店名，给人看；填了必须与 `店铺ID` 对得上 |
| `国家` | 可填 | 空 | 填了必须与店铺的 `market` 对得上 |
| `msku` | **必填** | 空 | |
| `YYYY-MM` × N | 期望销量 | 计划采购 | 月份列必须**正好**是计划的 N 个月，缺一个、多一个都错 |

★ 「可填但填了要对得上」是我的判断：AI 生成的表里店名 / 国家写错，常常意味着它把行对到了错的店；对不上就拒，不静默以 `店铺ID` 为准。

**格子值：**
- 空 → 不填（`null`，**不是 0**）；`0` → 0。
- ≥ 0 的整数；Excel 存成 `12.0` 的数按 12 收；小数、负数、文字 → 错。
- 表头列名前后空格忽略；列的先后顺序不限。

**行规则：**
- 每条销量行 = 认领这个 msku，**哪怕月份格全空**（行在就是要它进计划）。
- 同一 (msku, 店铺ID) 出现两次 → 错；同一货号两条采购行 → 错。
- 采购行的货号，必须是本文件某条销量行的货号（采购格子随认领长出来）—— 否则错 `purchase_sku_not_claimed`。
- 一行都没有 → 错 `empty_file`。

**错误回执（422，信封同全系统：`error` · `hint` · 字段平铺顶层）：** 每条带 Excel 坐标，人能直接去表里找：

```json
{ "error": "import_rejected", "hint": "有 N 处问题，计划没建", "errors": [
  { "row": 7, "col": "E", "msku": "...", "sid": "...", "code": "unknown_msku", "message": "msku 不在桥表里" },
  { "row": 9, "col": null, "msku": "...", "sid": "...", "code": "claimed_elsewhere",
    "message": "被计划「...」（#12）占用", "plan_id": 12 } ] }
```

错误码（初版，实现时可增）：`bad_header` · `missing_month` · `extra_month` · `bad_type` · `missing_field` · `bad_units` ·
`duplicate_row` · `unknown_msku` · `unknown_seller` · `seller_mismatch` · `market_mismatch` · `sku_mismatch` ·
`claimed_elsewhere` · `purchase_sku_not_claimed` · `empty_file` · `file_too_large` · `not_xlsx`。

**上限**：文件 5 MB、2,000 行 —— **设计取值 · 未实测**（口述量级：一个计划约 100 msku，UX 组记录）。

## 3. 界面（`OpsHome` 新建销售计划）

```
新建销售计划
  标题 [__________]  起始月 [2026-11]  月数 [3]
  从 Excel 导入（可选） [选择文件]  下载模板
                                     [取消] [新建]
```

- 不选文件 → 与现在完全一样。
- 选了文件 → 调 ③；按钮在请求中禁用（同 I3 防双击）。
- 422 → 弹窗内列出全部错误（行号 · 列 · msku · 原因），**计划没建**写在最上面；文件保留，改完可重选。
- 成功 → 跳计划整表。界面上不出解说文字（工程约定：屏幕给数据）。
- 页面操作流程改 `docs/10` §2.5，`test_doc_flow.js` 两边对核。

## 4. 可追踪

| 处 | 日志 |
|---|---|
| ① ② | `timed(...)` 带筛选条件、行数、截断与被 join 丢掉的数 |
| ③ | `op=plan_import actor title rows mskus skus cells file_bytes parse_ms validate_ms write_ms outcome errors=[code×n]`；拒绝时把前 20 条错误逐条写出 |
| ③ 取数源 | 认领时打 CH / 领星的照现有 `claim` 日志（打的谁 · 多久 · `cause.code`）|
| ④ | 见 §1 ④，唯一痕迹 |

## 5. 文档

`01` §3.1 / §7：`api/pub/` 落地、`usecase/` 新增的几个函数 · `08`：pub 五个接口逐条标注为契约 + ui 两个 · `10` §2.5：新建计划表单 ·
`00` IM 组：已全部裁定（IM-D1 ~ D10）。

## 6. 给外部 AI 的 skill（⑤）

一份 `SKILL.md`（放仓内，位置实现时定），内容只写**怎么用**：base URL 与 `x-actor: ai.agent` · ①~④ 的调用顺序
（先问用户要哪些 SKU / MSKU → 查映射 → 查预测 → 下载模板 → 只填用户选的产品 → 交人审查 → 导入 → 失败按 `errors[].code` 改表或请人维护产品 → 错太多则删除重导；§0 的五步）·
Excel 格式（引用本文 §2，不另抄一份）· 每个错误码该怎么处理（尤其 `unknown_msku` / `claimed_elsewhere` = 产品没维护好，交给人，不要自己改 msku 绕过）。
★ 不写 prompt / 生成建议量的方法（不在范围）。

## 7. 留给实现者

- xlsx 库的选型（后端解析与模板生成用同一个）。
- 认领与推演的抽取方式，只要 `claim` / `grid` 行为不变、现有测试不动。
- 导入时逐个 msku 取预测的耗时：一个计划约 100 msku（口述 · 未实测），实现时实测一次，前端请求超时按实测定。
