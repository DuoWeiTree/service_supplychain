# 阶段 C-a · 备货草稿（两链共用）—— 设计

| | |
|---|---|
| 日期 | 2026-10-10 |
| 状态 | 待负责人审阅 |
| 依据 | `15` 全文 · `02` §承重墙② / §3.1 · `03` §5（546-705）· `04` §7 · `00e` 阶段 C · `18` §2 · 领星官方文档 `apidoc.lingxing.com/docs/Warehouse/*.md` |
| 取代 | B4（`00:26`）· 承重墙② NOT NULL（`02:749-760`）· `15:242` 渠道由 `has_fba` 决定 · N-6 一表一选 |
| 实施计划 | `docs/superpowers/plans/2026-10-10-dispatch-draft.md`（待写） |

> 来源：负责人 2026-10-10：「备货表是基于采购计划与采购订单的基础上做备货分发……列出对应已经采购、并收货到本地仓的货（包含已有库存），
> 根据发货仓库与收货仓库，以 sku 或 msku 作为单位，分配给对应的目标平台店铺……领星的备货单视为正式备货单，我们实现的视为备货单草稿……领星作为权威目标。」
> 下列 **负责人裁定** 为对话中的选择原文；其余未决项登记进 `00`，本文不定。

---

## 0. 名词对齐

| 负责人说法 | 本仓对象 | 归属 |
|---|---|---|
| 备货表 / 备货单草稿 | 排货表 `sheet` + 安排 `dispatch_arrangement` → 派生排货单 `consignment` / 排货行 `consignment_line` | OWN |
| 领星备货单（正式） | 海外仓备货单 OWS（`overseas_order_no`）；FBA 链无备货单，是 RP → STA → SP | MIRROR |
| 提交给领星 | 投送（C-b / C-c，本刀不做） | 📤 调用 |

★ 命名铁律：不用 `inbound` / `shipment` / `allocation`。

## 1. 负责人裁定（2026-10-10）

| # | 问题 | 裁定 | 推翻 / 影响 |
|---|---|---|---|
| CA-D1 | 本阶段覆盖哪条链 | 「海外仓 + FBA 两链」 | 拆 C-a / C-b / C-c（§2） |
| CA-D2 | 无计划来源的现有库存 | 「允许，作为独立来源」+「所有本地可用库存，不要求理由」 | ★ 推翻 B4、承重墙② NOT NULL；PB-U1 结案。恒等式：排货行 = Σ计划来源 + Σ无计划来源 |
| CA-D3 | 分配单位 | 「msku（fnsku）直接」 | 排货行键 → `(consignment_id, seller_sku, sid, from_wid)` |
| CA-D4 | 入口交互 | 「需求网格，排货单派生」 | 重申 N-2 / N-3 / N-4 |
| CA-D5 | 渠道怎么定 | 「逐格：用户选目的地」—— FBA（本店）或海外仓 | ★ 推翻 `15:242`（`has_fba` 只要有一条 FBA listing 即真，海外仓链永远走不到）与 N-6 |
| CA-D6 | 计划来源格子的店 / msku | 「店必须在计划里，msku 自由」—— `sid ∈ demand_by_seller` 的键；msku 为该店该货号任一 | 与 `po_source_map` 同一道闸（`014`） |
| CA-D7 | C-a 无投送，确认后怎么退 | 「加取消出口」—— 排货表与排货单 `已确认 → 已取消`，必填理由，释放已排未发 | 修 `04:571` 的死胡同；C-b 从「已确认」起步 |
| CA-D8 | 本地库存分「共享桶（store=0）」与「店铺桶」，给店 X 的 msku 能用多少 | 「共享 + 本店桶」—— 别店的桶不可用 | G3 按桶判可行（§5）；C-b 实调若不符再改 |
| CA-D9 | 预测的采购在途口径错了怎么办 | 「现在单独修」 | 已修：`de57e93`（在途 = Σ`quantity_receive`） |
| ★ CA-D10 | 哪些记录能排（10-11 试用：记录只下单一部分就停在已提交，排货页看不到） | 「按量排」 | 未撤销 / 未完结都能排，上限 = 总量 − 已排；推翻「只有 已下单 / 准备排货」；迁移 `018` |
| ★ CA-D11 | 一张排货表几个计划 | 「一张排货表只对应一个销售计划」 | `sheet.plan_id`；入口 `GET /v1/dispatch/plans`（未完成的计划 + 进度）；排货页每条记录一行：需求 · 采购 · 入库 · 本地可发 · 已排 · 还差；无计划来源库存另起页签 |

## 2. 拆分

| 块 | 内容 | 前置 |
|---|---|---|
| **C-a（本文）** | 维度补齐 · 计划↔采购↔库存并排视图 · msku 安排 · 派生排货单 · 装箱 · 六闸 · 确认 / 取消。★ **零出站调用** | — |
| C-b 海外仓投送 | `createInbound`（幂等 `inbound_order_no`）· `listOrderNos` 超时查重 · `ext_ref` · 回执 | ★ 实调 C-3（默认 status=60 是否即扣）· C-4（delete 是否释放 allocate 锁） |
| C-c FBA 投送 | RP → STA 四步 → SP · 承重墙③ 回读 | C-5 · O-7 · C-9 |

## 3. 对象与关系（本体四层）

### ① 对象

| 对象 | 主键 | 谁产生 | 代码 |
|---|---|---|---|
| 计划单记录 | `line_id` | 运营提交 | ✅ `002` |
| 采购单 | `po_id` | 采购 | ✅ `010` |
| 排货表 | `sheet_id` | ★ 人 | 本刀 |
| 安排 | `arrangement_id`；格子唯一键见 §4 | ★ 人 —— 唯一的编排输入 | 本刀 |
| 排货单 | `consignment_id`；唯一 `(sheet, channel, dest)` | ★ 系统派生 | 本刀 |
| 排货行 | `(consignment_id, seller_sku, sid, from_wid)` | ★ 系统派生 | 本刀 |

### ② 关系

| 关系 | 基数 | 聚合语义 |
|---|---|---|
| 计划单记录 → 安排 | 1:N（可空端：安排 line_id NULL = 无计划来源） | ★ 必须求和（待排量） |
| 安排 → 排货行 | ★ **N:1**（同 msku×店×仓、不同计划行 / 无计划来源合进一行；原 `15:120` 写 1:1 作废） | ★ 必须求和（承重墙②） |
| 排货行 → 排货单 | N:1 | 派生 |
| 采购单 → 计划单记录 | N:M（承重墙① `po_source_map`） | 必须求和 |
| 本地库存（货号×仓）↔ 计划单记录 | ★ **无关系** | ★ 并排展示，不相减、不分摊（`02` §3.1，C4） |

### ③ 状态·事实（不升格）

| 事实 | 粒度 | 来源 |
|---|---|---|
| 待排量 | 计划单记录 | 总量 − 已排未发 − 已投送（视图） |
| 已排未发 | 计划单记录 · 货号×仓 | 承重墙②求和，排货单 ∈ {草稿, 已确认} |
| 已投送 | 计划单记录 | C-a 结构上恒 0；★ 只算本系统，`inv` 不在内（`00:662`） |
| 可发量 | ★ 货号 × 本地仓 × **桶**（共享 / 店铺） | CH `lingxing_inventory_warehouse.product_valid_num`，只读该列（`02:813`）；源键 `(wid, sku, seller_id, fnsku)`，`seller_id ∈ {'0',''}` = 共享桶 |
| 可安排量 | 货号 × 本地仓 × 店 | 本店桶 + 共享桶 − 本店已排未发 − 别店溢出到共享桶的量（CA-D8）；可发量未知 → 未知，不是 0 |

### ④ 动作

开排货表 · 安排 / 改 / 撤回（同一原语）· 填装箱 · 确认（过六闸）· 取消（草稿或已确认，必填理由）。

## 4. 数据模型

迁移：`015_dispatch_dims` · `016_dispatch` · `017_dispatch_views`。形状照搬 `010`（白名单 + 事件 + 冻结 + 只追加）与 `014`（延迟约束 + `FOR NO KEY UPDATE`）。

**015 维度补齐**

| 列 | CH 来源 | 状态 |
|---|---|---|
| `msku_bridge.fnsku`（可空） | `lingxing_product_listing.fnsku` | 列存在；C-b 用 |
| `sku_catalog.product_id`（可空） | `lingxing_product_local_products.id` | 列存在；★ 是否等于 createInbound 的 `product_id` 未实调（Q16） |
| `warehouse.market`（海外仓） | `lingxing_inventory_warehouses.country_code` | ★ 填充率未实测，探针先行；填不出则候选列全部海外仓 |

**本地库存源实测（2026-10-10，`jobs/probe_ch.py` 同口径只读）**

| 事实 | n |
|---|---|
| 键 | `(wid, sku, seller_id, fnsku)` 当日**零重复** |
| 每日行数 | 7,927 ~ 7,978 行 / 57 仓 / 960 货号（09-28 ~ 10-10，每天都有） |
| 写入时刻 | 06:33:15 ~ 06:33:29（一次写完） |
| 共享桶（`seller_id ∈ {'0',''}`） | 6,457 行 · 可发 149,300 |
| 店铺桶 | 1,519 行 · 可发 7,607；★ 部分 `seller_id` 不在店铺镜像（如 `110625196267809792` 可发 2,626）—— 非亚马逊店，本刀不可用，计入 `excluded` |
| 覆盖仓 | 本地 37 · 自营海外 13 · 三方海外 7 —— ★ 只取 `warehouse.kind='local'` |
| `product_valid_num` 为 NULL / 负 | 0 / 0 |
| `product_id` = `local_products.id` | 960 / 960（Q16 部分答：与库存表一致；与 createInbound 是否一致仍待 C-b 实调） |
| 海外仓 `country_code` | 21 仓中 13 有值（US / GB / DE），8 空 —— 空的不按市场过滤，全列为候选 |
| listing `fnsku` | FBA msku 9,774 有 / 85 空；FBM 1,233 全空 |

**★ CA-D12（10-12）数据源取较新**：CH 每日采集作底（库存 / 采购行项）；采购页领星读回（`po_item_obs` 行级 已入库 / 待到货，迁移 `019`）与「现查领星」（`stock_read` / `stock_obs`）比 CH 新时取它们；确认前对本表货号现读库存再判 G3，现读失败 = 503，不退回旧数。CH 的 `DateTime` 实测为 UTC。

**本地可发量不做镜像**：`dim/ch_stock.py` 按需读取（`refresh_dims` 只 upsert 不删，消失的行会变成幽灵库存）；确认时采用的快照写进 `sheet_gate_run` 作证据。

**016 本域表**

| 表 | 要点 |
|---|---|
| `sheet` + `sheet_transition` / `sheet_event` | `[*]→草稿`、`草稿→已确认`、`草稿→已取消`、`已确认→已取消`（CA-D7，必填理由） |
| `dispatch_arrangement` | `sheet_id · line_id NULL · sku · seller_sku · sid · from_wid · channel · dest_wid / dest_sid · units > 0 · ledger_seq`。两条部分唯一索引（计划 / 无计划）。触发器：表已冻结 · 记录不可排 · 货号不符 · msku 属别的货号 · `sid ∉ demand_by_seller`（CA-D6，仅 line_id 非空）· 来源仓非本地 · 目的地非法（oversea 须 `kind LIKE 'oversea_%'`；fba 须 `dest_sid = sid`） |
| `consignment` + 白名单 / 事件 | 唯一 `(sheet, channel, dest)`；CHECK 两种目的地恰一；`idem_key = 'SCM-CN-'‖id`（设计取值 · 未实测，C-b 才用）；`已确认→草稿` 在 C-a 拒绝 |
| `consignment_line` | 键 `(consignment_id, seller_sku, sid, from_wid)` |
| `dispatch_source_map` | ★ 承重墙②：每条安排一行，`line_id` 可空 |
| 延迟约束 `dispatch_over_total` | 每条记录 已排未发 + 已投送 ≤ 总量（`04 §7.4`） |
| `consignment_packing` | ★ 人填的装箱单独成表（铁律 6），按表级格子 `(sheet, seller_sku, sid, from_wid)` 键；重派生不丢。★ 偏离 `03:652` 的「装箱挂行上 + 重算贴回」 |
| `sheet_gate_run` | 只追加：每次确认的闸结果 · 采用的库存快照日 · 被拒快照 |

**017 视图**：`v_plan_line_dispatch_qty` · `v_stock_committed` · `v_check_dispatch`（安排≠映射 · 行≠Σ映射 · 超总量 · msku 改挂货号）→ `/v1/checks/dispatch`。

## 5. 规则（`rules/dispatch.py`，纯函数）

- `derive(arrangements) → consignments / lines / maps`：按 `(channel, dest)` 分组；渠道、目的地取自格子本身（CA-D5）。
- `diff(old, new)`：新增 / 保留 / 删除；★ 装箱对不上新键的**逐项点名**，不静默丢。
- `available = valid − committed`；`valid=None` → 未知。
- `gates()` **永远返回 G1–G6 全表**：

| 闸 | C-a 查什么 | 谁能修 |
|---|---|---|
| G1 | 仅 FBA 行：装箱字段齐全（`10:423`）；一致性公式待裁定（Q10） | 人 |
| G2 | FBA 排货单有 `dest_sid`，不查 FC | 系统 |
| G3 | 每个 货号×来源仓：Σ_店 max(0, 已排未发_店 − 本店桶) ≤ 共享桶（CA-D8，已排未发跨全部未发表）；★ 读不到 → 「无法判定」，不放行 | 人（改安排） |
| G4 | 自 `seen_watermark` 后无别表撞同一货号×仓；范围待裁定（Q12） | 人 |
| G5 | 「疑似未关联发货」—— ★ 显示「待裁定 · 未检查」（Q9） | — |
| G6 | 目的地合法 | 系统 |

## 6. 接口（`api/ui/sheets.py`，前缀 `/v1`）

| 方法 | 路径 | 要点 |
|---|---|---|
| POST / GET | `/sheets` | |
| GET | `/sheets/{id}` | 表头 · 排货单 · 行 · 装箱 · `stock_as_of` |
| GET | `/sheets/{id}/grid` | 需求行（记录 × 店 × msku：待排 · 关联采购单含「截至」· 各本地仓 可发 / 已承诺 / 可安排 / 截至）· 无计划来源库存区 · `excluded{}` 被丢掉的一侧计数 |
| PUT | `/sheets/{id}/cells` | ★ 唯一替换原语；同事务重派生，返回 diff；超可安排只警告（Q20） |
| PUT | `/sheets/{id}/packing/{msku}/{sid}/{from_wid}` | oversea 行 → 409 `packing_not_applicable` |
| GET | `/sheets/{id}/preflight` | |
| POST | `/sheets/{id}/confirm` | 每次写 `sheet_gate_run`；409 `confirm_blocked`（全部闸列出）；503 `stock_unavailable` |
| POST | `/sheets/{id}/cancel` | 必填理由；草稿 / 已确认 |

错误码：`sheet_not_found` · `line_not_found` · `sheet_frozen` · `line_not_dispatchable` · `over_total` · `msku_not_this_sku` · `seller_not_in_line` · `not_local_warehouse` · `invalid_destination` · `bad_units`。★ 退役：`no_plan_line_for_demand`。

## 7. 界面（`web/src/pages/dispatch/`）

- `/dispatch`：排货表列表 + 新建。
- `/dispatch/sheets/:id`：需求网格（计划·rev·货号·月 / 店·msku；待排 · 可发（截至）· 关联采购单折叠）· 无计划来源库存区 · 派生排货单面板（装箱列只在 FBA 行出现）· 闸列表 · 确认 / 取消。
- 关联采购单显示「已入库 / 待到货」，各带截至。
- 屏幕给数据、文档给论证；说明进折叠区。

## 8. 可追踪

- CH 读库存：`op=ch_local_stock target= as_of= rows= rejected= dropped= elapsed_ms=`；失败带 `cause.code` → 503。
- 写：`op=arrange / derive / sheet_confirm / sheet_cancel`，带 sheet · 格子 · 前后值 · 失败闸 · 库存快照日 · 操作人 · 耗时。
- 证据：`sheet_gate_run` + 事件表。

## 9. 待裁定（登记进 `00`，本文不定）

| # | 问题 | C-a 暂行 |
|---|---|---|
| Q9 | G5 查哪类单据、什么窗口 | 显示「未检查」 |
| Q10 | G1 一致性公式；混箱 | 只查齐全 |
| Q12 | G4 撞车范围 | |
| Q13 | CH 库存陈旧阈值 | 设计取值 · 未实测 |
| ~~Q14~~ | ✅ 已答（10-10 实测）：`quantity_receive` = 待到货量；`quantity_entry` = 已入库量。关联采购单显示「已入库 / 待到货」取 CH 行项（按 `order_sn × sku`，带截至） | — |
| ~~Q15~~ | ✅ 已答，见 §4 实测表 | — |
| Q16 | `local_products.id` == createInbound `product_id`？（库存表已对上 960/960） | C-b 实调 |
| Q17 | `logistics_id` 来源 | C-b |
| Q18 | `06:239` 建议量 与 `04:278` 冲突 | 不做 |
| Q19 | 来源仓能否是海外仓（`18` §6：14/364） | 只许本地 |
| Q20 | 超可安排是否写时就挡 | 只警告 |

## 10. 文档改动清单（先改文档，`00e:186`）

`00`（CA 组 + Q9-Q20）· `02:474 / 749-760 / 822` · `03` §5 · `04`（B4 行、取消边）· `15`（`:66-68` · `:120` · `:242` · §5.3 · §6 G1）· `06:235` · `08` §1.3 · `09` · `10` · `00e`（C-a/b/c · 判据② · §5.2 登记本文）· `00c` §5 作废说法（「没有计划单记录禁止排货」· `no_plan_line_for_demand`）。

## 11. 校验

- 纯：`tests/test_rules_dispatch.py` · `tests/test_dim_ch_stock.py`。
- 集成：`test_ddl_016_dispatch.py`（冻结 · 白名单对 `04` · 取消释放 · line_id 空可写 · CA-D6 · 超总量延迟报错）· `test_ddl_017_views.py`（正常流程 `v_check_*` 空，故意写坏非空）· `test_api_sheets.py`（同记录并发恰一个 409）。
- 前端：`npm test` + `node test_doc_index.js test_retired_claims.js test_doc_flow.js`。
- ★ 每条新测试先弄失败一次。
