# 阶段 B 采购单：组单 → 下单 → 入库 — 设计

| | |
|---|---|
| 日期 | 2026-10-07（对话起于 2026-09-30） |
| 状态 | ★ **全文已确认**（负责人 2026-10-07：§6 · §10.2 按建议 · §11 评估与里程碑）· §7~§8 为实施基线，细节随实施计划落地 |
| 依据 | `00e` §1 阶段 B / §2 B1~B11 · `02` §0.2 §2.1 §2.5 · `03` §po · `04` S1 · `06` 采购段 · `07` §2 §6 §7 · `08` 采购接口 · `09` UC-P1~P7 · `17` 阶段 2 · `CLAUDE.md` 出站调用 |
| 实施计划 | 本文确认后由 writing-plans 产出 `docs/superpowers/plans/2026-10-07-purchase-order.md` |

★ **执行者先读 §1（范围与刀）和 §9（文档改动清单）。** 刀0 先改文档、登记 `00`，再写代码（`00e` §2）。

---

## 1. 范围与切刀

**采购阶段 = 组单 → 下单 → 入库完成。** 备货（阶段 C）从「良品入库完成」开始。

```
计划单记录（货号×月，已提交）
  → 采购员组单（草稿：po + po_line + 承重墙① po_source_map）→ 记录转「已确认」
  → ⛔ 下单：📤 领星 createPurchaseOrder → order_sn → 冻结 → 记录转「已下单」
  → 📥 读回：领星 PO 状态 · 收货单 CR · 入库单 IB · 质检单 QC（仓库照旧在领星操作，我们只读）
  → 首批良品入库完成 → 记录转「准备排货」（交给阶段 C）
```

| 刀 | 内容 | 依赖 |
|---|---|---|
| 0 | 文档：B2/B4~B11 落文档 · 本文裁定登记 `00` · ★ 只读实调（签名、`PurchaseOrderList` 字段、302 件待检去向） | — |
| 1 | 迁移：§3 全部表、触发器、视图 | 0 |
| 2 | 规则 + API：候选 · 组单 · 改 / 删草稿 · 收手动推进（§5） | 1 |
| 3 | 网关 + 下单（§6） | 1 |
| 4 | 回读作业 + 状态推进（§4 §7） | 1 · 3 |
| 5 | 前端（§8，用 frontend-design 重新设计，`00e` R-4） | 2 · 3 · 4 |

**不做：** 领星采购计划 PP · 我们写 CR/IB · 采购变更单 `createPurchaseChangeOrder` · 本地作废（作废在领星做，我们读回） · 排货。

---

## 2. 实测（CH `jxd_raw`，会话内探针 2026-09-30）

| 事实 | n | 用在 |
|---|---|---|
| 采购员 `opt_uid` 只有 1 人（10925905 陈彩凤） | 143/143 | §5 候选 |
| 采购方 `purchaser_id` 111 / 185，**无名称列** | 135 / 8 | §10 V8 |
| 供应商 3 家：8085 · 8086 · 22178（有名称） | 78 / 38 / 27 | §3 supplier 镜像 |
| ★ `custom_order_sn` 恒等于 `order_sn`（`PO2609…`） | 143/143 | §6 幂等键前缀 |
| 单头 `wid=0`（多仓） | 55/143 | B7 |
| 作废 | 53/143 | B6 |
| PG `scm.plan_line` 当前行数（测试数据） | 6 行 / 1 rev | 分店上限无量级可推 |

**采集节奏（近 11 天）** —— §4 的依据：

| 表 | 采集时刻 | 规律 |
|---|---|---|
| IB 入库单 | ~12:06 | ★ 每张只采 1 次（4,035/4,035），之后状态变化**看不到** |
| CR 收货单 | ~12:06 | 只采 1 次（1,141/1,146），有缺日 |
| PO 单头 | ~14:36 | ★ 11 天仅 4 天有采集；按变化增量 |
| PO 明细 | ~14:36 | 最近两日整表 2,060 行 |
| QC 质检单 | ~18:00 | ★ 晚约 7 天（采集日 − `create_time`） |
| 采集运行日志 | — | ★ **CH 里没有**（`spider.task_event` 是关键词爬虫，无关） |

---

## 3. 数据模型 —— 已确认

### 3.1 OWN

| 表 | 要点 | 出处 |
|---|---|---|
| `po` | `supplier_id` · `purchaser_id` · `opt_uid` · ★ `qc_type`（0/1/2）· `wid` **可空** · `custom_order_sn`（我们生成，前缀撞不上 `PO…`）· `order_sn` · 本地状态 `草稿→已下单` · ★ `placed_mode ∈ {fake,live}` · `draft_version` | `03:394` · B7 · B8 · §2 |
| `po_line` | `sku` · ★ `wid NOT NULL` · `units`（采购员填）· `price` · `expect_arrive_date` · ★ `unplanned_reason` · `lx_item_id`（下单后回填，回读行级键） | B7 · `17:245` · D9 |
| `po_source_map` | `(po_id, line_no, line_id, seller_id, units)` | `03:423` · N-1 |
| `po_transition` / `po_event` | 白名单 1 行 `草稿→已下单`，校验 `order_sn` 非空 | `04:992` |
| `plan_line_transition` | ★ 新增退回边 `已下单→已确认` · `已下单→已提交` | D5 |
| `erp_call` | 按 `03:1519-1531`；★ 每张 po 至多一条 in_flight（部分唯一索引） | §6 |
| `supplier` | MIRROR，从 CH PO 行上的 `supplier_id/name` 镜像，接 `dim/registry.py` | §2 |
| `purchaser_label` | ★ 人填：`purchaser_id → 显示名`（铁律 6） | V8 |

### 3.2 约束

| 约束 | 失败 |
|---|---|
| Σ_s 映射(line, s) ≤ 上限[line, s]；上限 = `total_units × 期望[s] / Σ期望`，最大余数法取整（V1） | `over_seller_cap` |
| Σ 期望 = 0 或任一店 basis=unknown → 该记录不可组单 | `no_seller_basis` |
| Σ 映射(line) ≤ `total_units` | `over_total`（`03:899` 已有） |
| `po_line.units ≥ Σ 映射(po_line)`；差额 > 0 必填 `unplanned_reason` | `unplanned_reason_required` |
| 映射只能挂在 {已提交, 已确认} 的记录上；已下单的单映射不可删 | `03:926-942` 已有 |

### 3.3 MIRROR 回读表（只追加，铁律 8）

每行带 `source ∈ {ch, api}` · `source_captured_at` · `observed_at` · `run_id`。

| 表 | 粒度 | 关键列 |
|---|---|---|
| `po_snapshot` | 领星 PO × 观察 | `status` · `status_shipped` · `quantity_real/entry/receive` |
| `po_item_obs` | 领星 PO 明细 × 观察 | `order_sn` · `lx_item_id` · `sku` · `wid` · `quantity_real` · `price` —— ★ G1「落下的 ≡ 发出的」核对的数据源（M1 计划 Task 3 补） |
| `po_cr_obs` | CR 行 × 观察 | `cr_order_sn` · `po_order_sn` · `order_item_id` · 实收 · `status` |
| `po_ib_obs` | IB 行 × 观察 | `ib_order_sn` · `receipt_order_sn` · `purchase_order_sn`（**可空**，B11）· `purchase_item_id` · `wid` · ★ 良品 / 待检 / 次品**三列** · `status`（50 不计，B10） |
| `po_qc_obs` | QC 行 × 观察 | `delivery_order_sn` · 良品 / 次品 · `status` |
| `recon_run` | 每轮 | 每源水位 · 进 / 丢行数及原因 |
| `recon_discrepancy` | 每次两源不符 | 哪张单 · CH 说什么 · API 说什么 · 采信哪个 |

### 3.4 派生量（视图，不落库，不摊回记录 —— C4）

```
已确认量[line] = Σ 映射，排除领星作废的 po
已下单量[line] = Σ 映射，po 已下单且未作废
可备货良品[po_line] = Σ 已完成 IB 良品（排除撤销）+ 待检转良品（来源见刀0 实调）
```

---

## 4. 回读与一致性 —— 已确认

```
每日一次（APScheduler 进程内，S-33；时刻 V2，排在三类采集都落地之后）
  1. CH 增量 → *_obs(source='ch')
  2. 未结单 PO（status ∉ {9,-1}，仅 live）→ 领星只读接口复核 PO 头 + 其 CR/IB → *_obs(source='api')
     fake 单不走 CH/API，由「模拟外部」抽屉喂 *_obs(source='sim')（§11 G2）
  3. 两源不符 → 采信较新的一方 + 写 recon_discrepancy（撤销就在这里被抓到）
  4. 写 recon_run：每源水位、进 / 丢 / 原因（无 PO 的 IB 在这里点名，B11）
  5. 触发记录再评估（§7）
```

| 规则 | 出处 |
|---|---|
| 界面每个数带「截至」（每源各自的 `source_captured_at`） | 结论回显依据 |
| 可备货良品只认 IB/QC 事实，不等 PO 单头跟上 | 铁律 4 |
| 单头未跟上：宽限期（V3）内提示，超期告警 | 铁律 3 |
| 作废但已有入库 → 不释放额度，告警 `void_with_inbound` | 静默兜底是最坏的 |
| 某源水位 V4 天不动 → 启动日志 + 界面告警 | 全局规范第 4 条 |

---

## 5. 组单与草稿 —— 已确认

| 端点 | 行为 | 错误码 |
|---|---|---|
| `GET /v1/plan-lines`（扩展） | 加 `confirmed_units` · `ordered_units` · `sellers{sid:{expected,cap,used,remaining}}` · `blocked{reason,sellers}`；被滤掉的计入 `excluded{}` | — |
| `POST /v1/purchase-orders` | 一单一供应商；同货号同仓合并一行；按 `line_id` 升序 `FOR UPDATE`；同事务写三表并再评估记录 | `unknown_supplier` · `missing_required` · `line_not_orderable` · `over_seller_cap` · `no_seller_basis` · `over_total` · `unplanned_reason_required` |
| `PUT /v1/purchase-orders/{id}` | 整单替换；带 `draft_version` | `stale_draft` · `po_frozen` · `po_placing` |
| `DELETE /v1/purchase-orders/{id}` | 映射同删，记录按量退回 | `po_frozen` · `po_placing` |
| `POST /plan-lines/{id}/transition` | 目标为 已确认 / 已下单 / 准备排货 → 拒绝 | 422 `derived_state` |

每个写操作日志：`op= po_id= supplier= lines= sources= lines_moved=[…] actor= ms=`。库层拒绝按 `constraint_name` 翻译（`api/ui/errors.py`），认不出的以 500 冒出，不兜底。

---

## 6. 下单与出站网关 —— 已确认

### 6.1 结构（`erp/`）

`endpoints.py`（★ 登记表：路径 → `read｜reversible｜irreversible` · 幂等键 · 回查；未登记即拒）· `auth.py` · `sign.py`（移植兄弟仓 `ecops/plugins/global-ecommerce-erp/src/client/lingxing-client.ts`，以其样例对照测试）· `gateway.py`（唯一出站口）· `drivers/{fake,live}.py` · `usecase/place_po.py`（G-2）。

★ 用登记表不用前缀推断：`createPurchaseOrder` 在 `/erp/` 下却不可逆（文档矛盾，`07:351` vs `08:242`）。

### 6.2 流程（★ 以 `usecase/place_po.py` 为准）

```
GET  /v1/purchase-orders/{id}   → confirm_token（H-1）
POST /v1/purchase-orders/{id}/place {confirm_token}
  事务①：锁 po → 拒绝 已作废 / 已下单 / 在途 / 待核实 → 最近一次 create 为 ok·unknown 且无 confirmed_absent 也拒绝（补开待核实）
         → draft_version 对？→ INSERT erp_call(in_flight) → COMMIT                  ★ 不持锁跨 HTTP
  HTTP createPurchaseOrder（不持任何锁；in_flight 部分唯一索引就是锁）
  事务②（★ 一个事务）：close_call + 按结果记账
```

| 结果 | 判据 | 事务②（原子） | 之后 |
|---|---|---|---|
| 成功 | `ok` 且认得出 `order_sn` | close_call + 记已下单（`po_event`）+ 重判记录状态 | 读回（尽力而为，失败只 WARN）→ 落地首检（§11 G1） |
| 业务拒绝 | `rejected`（`code≠0`，或 4xx 无 code） | 仅 close_call | 原样回显，留草稿，不重试 |
| 没发出去 | `not_sent`：★ 仅**建连阶段**失败（含代理 CONNECT） | 仅 close_call | 可安全重试，留草稿 |
| 结果未知 | `unknown`：读超时 · 请求发出后的连接重置 / 协议错误 · 5xx · 2xx 读不出 `code`；或 `ok` 却认不出 `order_sn`；或驱动意外抛异常 | close_call + **开 `needs_verify`**（detail 冻结） | 按 `custom_order_sn` 查重：查到 → 记已下单 + 收待核实；其余 → 保持待核实，**不自动重试** |

★ **「已发出但没记账」与 close_call 同一事务落库**：若分开，两步之间进程死掉，这件事就没人记得。
原子化后只剩 `in_flight` 一种半成品，由 §6.3 收。

**查重口径**：`PurchaseOrderList` 无 `custom_order_sn` 过滤，按**开单那次 create 调用的 `started_at`** 日期 ±1 天取（`search_field_time=create_time`），客户端按幂等键筛。
★ **查询失败 / 结果被截断（`total` > 已取条数）永远不算「查无」** —— 查不了不等于没有。
已记账的单若查到**另一张**同幂等键的真单 → 不替人选，开 `landed_mismatch`。

「待人工核实」：界面给「重新核对」；★ **最近一次 create 为 `ok` / `unknown` 的草稿，再次下单必须先有人 `confirmed_absent`**（§6.6）；
`not_sent` / `rejected` 的草稿可直接重下。重下是新 `erp_call`，`retry_of` 串起。

### 6.3 断点续跑

启动钩子 `recover_stuck`（`CLAUDE.md` 出站 #3）：① 扫 `in_flight` 超过 V5 分钟的不可逆 `erp_call` → close 为 `unknown`、开待核实、按幂等键查重；
② 最近一次 create 为 `ok/unknown` 却没记账也没待核实的草稿 → 补开待核实、查重。
★ 恢复动作记在**原下单人** `actor` 名下（`actor` 表里没有「系统」，不编一个），事件 `src=recover:<call_id>` 可分辨；一条失败只记 ERROR、不拖累其余。全程不自动重试 `createPurchaseOrder`。

### 6.4 fake / live

`[erp] mode = fake|live`，默认 fake。fake 单号带 `FAKE-` 前缀，CH 回读与 API 复核跳过、改由「模拟外部」喂数（§11 G2）；界面单头与确认弹窗醒目标 FAKE/LIVE；live 缺凭证 → 启动即报错；启动日志首屏 `erp mode= host=`。★ 第一次 live 下单由负责人亲手开关、亲手点。

### 6.5 日志

```
erp op= host= path= idem= ms= outcome= http= lx_code= lx_msg= cause=<__cause__ 链 errno/类名> retry_of=
```
请求 / 响应体存 `erp_call`（签名、token 脱敏）；查重调用也是一条 `erp_call`。

### 6.6 ★ 转人工：看得见 · 看得懂 · 接得住（负责人 2026-10-07）

> 「跳转人工需要系统有一个明显的用户提示与详细失败记录。否则，用户无法介入。」

**① 看得见 —— 不靠用户自己去翻**

| 位置 | 形态 |
|---|---|
| 顶栏（所有页面） | 红色常驻条「⛔ N 张采购单需要人工处理」，**不可关闭**，直到处理完；点击进待处理队列 |
| 采购员首页 | 工作队列第一项，按类型分组 |
| 采购单列表 / 详情 | 行上红标；详情页顶部红框 = 下面的失败记录 |
| 日志 | WARN 一行，带 `po_id` · `erp_call_id` · `outcome` |

**② 看得懂 —— 详细失败记录（每张单一条时间线，读自 `erp_call`，只追加）**

每次尝试一段：

```
10:01:32  吴 点击「下单」 · LIVE · POST openapi.lingxing.com/erp/sc/routing/purchase/purchase/createPurchaseOrder
          幂等键 SCM-PO-123 · 耗时 30.0 s · 结果：结果未知（读超时，请求已发出）
          cause：ReadTimeout ← 无 errno · 对端 47.x.x.x:443
          请求体 [展开]  响应体：无
10:01:33  系统按 SCM-PO-123 查 PurchaseOrderList · 耗时 0.8 s · 未找到（领星列表截至 10:01:33）
```

顶部一句**大白话结论**，分「已知 / 未知」。★ **`po_attention.detail` 开立即冻结，只存事实**（`create_call_id` · `create_outcome` · `create_cause` · `request_confirmed_sent` · `custom_order_sn` · `opened_by`）；
「已知」由时间线从 `erp_call` **实时推导**（请求是否确认发出 + create 的结果与原因），「未知」固定指**领星是否已建单**并附最近一次查重的 outcome / 时间 / 是否截断 —— 查重结果不写进 detail：

```
已知：请求已发到领星，30 秒内没有回音；随后按 SCM-PO-123 查询，领星里暂未找到。
未知：领星是否已经建单（列表可能尚未刷新）。
```

业务拒绝（`code≠0`）同样进时间线，领星的 code / msg **原文**照抄，不翻译、不截断。

**③ 接得住 —— 用户能做的动作（每个动作写 `po_event`：谁 · 何时 · 理由）**

| 动作 | 前置 | 效果 |
|---|---|---|
| 重新核对 | — | 再查一次 `PurchaseOrderList`，结果追加进时间线 |
| 去领星查 | — | 给出照着搜的条件：幂等键 · 供应商 · 货号清单 · 时间窗 |
| 登记「领星已有此单」 | 填 `order_sn` | ★ 系统先用只读接口读回该单，核对 幂等键 / 供应商 / 明细（同 `v_check_po_landed`），**对上才接受** → 转已下单 |
| 确认「领星无此单」 | 必填理由；无调用在途（否则 409 `lookup_in_progress`） | 记 `confirmed_absent`，解锁「重新下单」（新 `erp_call`，`retry_of` 串起）或「删除草稿」。★ 这是 `ok/unknown` 之后重下单的**唯一**放行条件 |

处理中的单：改 / 删 / 下单 → 409 `needs_verify`（回显时间线链接）。

**同一个队列也收**：`void_with_inbound`（§4）· `v_check_po_landed` 不符（§11 G1）· 某源水位停滞（§4）。类型不同、展示同构：结论 → 时间线 → 可做的动作。

### 6.7 刀0 实调（零副作用）与首单才能回答的

| 只读能答 | 只有首张 live 单能答 |
|---|---|
| **已实测（刀0，2026-10-08，`jobs/probe_lingxing`，仅 `purchaseOrderList`）：** 经白名单代理 + token + 签名一次通过（http 200，`code`=0）· 响应顶层键 `code / data / error_details / message / request_id / response_time / total`，`data` 为订单数组、`total` 在顶层（形状 A）· 2026-09-01~10-07 查到 4 张单，`total`=4；2026-10-07 单日 0 张 · 订单层字段含 `order_sn / custom_order_sn / status / supplier_id / wid / item_list / quantity_real / quantity_receive / quantity_entry / create_time / update_time`（共 59 个键）· ★ 明细行键为 **`id`，不是 `item_id`**（已改 `repo.insert_item_obs` / `backfill_item_ids` 与 fake）；明细含 `sku / wid / quantity_real / quantity_receive / quantity_entry / quantity_qc / quantity_return / price / is_delete / plan_sn / msku` 等 · `custom_order_sn` 是订单上的字段；**能否按它过滤未测**（登记的过滤参数只有 `order_sns`，仍客户端筛） · 仍未测：CR/IB/QC 只读接口路径与字段、302 件待检去向 | 响应带不带明细 `item_id` · `custom_order_sn` 格式 / 长度是否被接受 · 领星是否校验其唯一 |

---

## 7. 回读后的状态推进 —— 已确认（基线）

| 事件 | 判据 | 结果 |
|---|---|---|
| 作废 | 采信的 PO 状态 = -1，且该 PO **无已完成良品入库** | 映射退出量视图 → 记录按量退回（已确认 / 已提交） |
| 作废但有入库 | 同上但有入库 | 不动，告警 `void_with_inbound` |
| ★ 准备排货 | 记录所挂的某条 po_line 有「已完成 IB 良品 > 0」（按 `purchase_item_id` 行级对上） | 记录 已下单 → 准备排货（取代 `04:201` 的 `status_shipped` 判据，D10） |
| 采购单结单 | 领星 status = 9（含 `setOrderFinish`） | po 显示「已结单」；并排显示 下单量 / 良品入库量，不合成达成率 |

记录在「准备排货」之后的迁移属阶段 C。

---

## 8. 前端 —— 已确认（基线，界面细节由 frontend-design 定）

| 页 | 给什么数据 |
|---|---|
| 采购员首页 | 我的采购单 · 本地状态 × 领星状态计数 · 不一致 / 待人工核实告警 · 各源「截至」条 |
| 候选 | 记录按货号分组 · 分店上限 / 已用 / 剩余 · 不可组单的点名原因 |
| 采购单编辑 | 单头表单 · 明细行 · 每行来源 · 无计划来源量 + 理由 |
| ⛔ 下单确认弹窗 | 供应商 / 采购方 / 采购员 · 行 · 合计件数与金额 · FAKE/LIVE |
| 采购单详情 | 每行：下单 · 收货 · 良品入库 · 待检 · 次品 · 各带截至；两轴状态并排 |

按 `CLAUDE.md`：屏幕给数据，说明进折叠区；不可逆按钮 `.btn--danger` + ⛔（`10:87`）。

---

## 9. 文档改动清单（刀0）

| 文档 | 改什么 |
|---|---|
| `00` | 登记 D1~D12（裁定）· V1~V11（设计取值待裁，V9 作废）· U1~U7 · R1~R2（未决 / 风险） |
| `00d` / `01` | ★ Q-5 重裁（D11）：§3「暂不做」改为「dev 模式限开发，生产登录 + RBAC」；`01` §1.2 认证条同步 |
| `00e` | §5.2 地图加本文一行（★ 对「采购单组单 / 下单 / 入库读回」说了算）· §8 进度 |
| `02` | 补 CR/QC 对象的关系 · 「工厂交货无独立单据」删除（B4） |
| `03` | §3 全部 DDL · 删 `po.wid NOT NULL`（B7）· 加 `qc_type`（B8）· `po_receipt` 拆成 §3.3 四表（B9~B11） |
| `04` | 新退回边（D5）· 准备排货判据（D10）· 加「读作废」触发（B6） |
| `06` / `07` | 超时查不到 → 待人工核实，不自动重试 · 回执器加只读接口复核（D8） |
| `08` | 采购写接口补到 7 个（B5）· 新端点与错误码 · 三类前缀 → 登记表 |
| `14` | B2「一笔采购 = 一个批次」改为分批（不改提前期默认值，见 U2） |

---

## 10. 待拍板

### 10.1 已裁定（负责人，2026-09-30 ~ 10-07 对话）

| # | 裁定 |
|---|---|
| D1 | 直接建领星采购单 PO，不经采购计划 PP |
| D2 | 采购量记到店铺，分店上限按期望销量占比 |
| D3 | Σ期望 = 0 或有店 unknown → 拒绝组单并点名 |
| D4 | 真网关 + `fake｜live` 开关，默认 fake |
| D5 | 领星作废 → 释放额度，记录按量退回 |
| D6 | 采购阶段到入库为止；CR / IB 只读回 |
| D7 | 可备货只算良品 |
| D8 | 未结单 PO 每日用领星只读接口复核 |
| D9 | 允许超计划量，超出部分记「无计划来源」并必填理由 |
| D10 | 备货从确定入库开始 ⇒ 准备排货判据 = 首批良品入库完成（修正 C2 的「到货」含义） |
| D11 | 生产必须登录 + RBAC；硬编码身份只限开发（重裁 Q-5，§12）|
| D12 | 生产入口 = Keycloak + oauth2-proxy（同 pm 系统）；本服务不登录，只经 RBAC `by-jwt` 判权限（§12） |

### 10.2 设计取值 · 未实测 —— ★ 负责人 2026-10-07「按建议」；在 `00` 标「设计取值 · 按建议 · 未实测」

| # | 项 | 建议 |
|---|---|---|
| V1 | 分店上限取整 | 最大余数法（Σ上限 ≡ total_units） |
| V2 | 回读时刻 | 每日 19:00（QC ~18:00 落地之后） |
| V3 | 单头未跟上宽限 | 3 天 |
| V4 | 水位不动告警 | 3 天（PO 单头实测 11 天仅 4 天有采集，会常报——需你知情） |
| V5 | in_flight 视为卡死 | 10 分钟 |
| V6 | 下单 HTTP 超时 | 30 秒 |
| V7 | `custom_order_sn` 格式 | `SCM-PO-{po_id}`（首单实调验证） |
| V8 | 采购方显示名 | 人填小表 `purchaser_label` |
| ~~V9~~ | ~~采购员身份~~ | ★ 作废 → 改由 §12 RBAC（D11）|
| V10 | 候选「一键组入」的分店预填 | 预填 = 各店剩余上限，采购员可改；不改就是采购员认可的决定 |
| V11 | 单头 / 单价预填 | 取该货号最近一张领星 PO 的供应商 · 单价 · `qc_type`，界面标出处单号（铅笔灰，同阶段 A 的「机器给的」） |
| V12 | ⛔ 下单前绕过 RBAC 缓存重查权限 | 是（§12.6 R2） |

### 10.3 未决（不在本刀解决，只登记）

| # | 项 |
|---|---|
| U1 | 无计划来源的入库量在阶段 C 怎么排（B4 会挡住） |
| U2 | `make_days` 默认值与命名（`00e` §3，影响预测，不影响本刀） |
| ~~U3~~ | ★ 已确认（2026-10-07）：`ECONNRESET` 等请求发出后的失败归「结果未知」；超时查不到转人工，不自动重试 ⇒ 改 `07` / `06` |
| U4 | 待处理队列要不要**推送**（钉钉 / 邮件）—— 本刀只做站内提示；推送需另定接收人与频率 |

---

## 11. 目标评估 —— 2026-10-07

> 目标（负责人）：以销售计划为输入，采购员做采购单，把可确认的提交领星，★ **确保从计划到订单的过程正确**。
> 基础：闭环跑得通、可检查、可校验。深入：方便交互管理。

### 11.1 闭环每一跳：谁来证明它对

| 跳 | 「对」的含义 | 怎么查（★ 每条都是库里查得出来的东西，不是人看） |
|---|---|---|
| 计划 → 候选 | 每条可组单记录都出现；不出现的有原因 | `excluded{}` 按原因计数 · `blocked{}` 点名（§5） |
| 候选 → 草稿 | 不超分店上限、不超总量；超计划量有理由 | 约束触发器（§3.2）+ ★ `v_check_po_source`（G3） |
| 草稿 → 记录状态 | 已确认 ⇔ 已确认量 = 总量 | ★ `v_check_line_state`：派生状态 ≠ 物化状态的行（G3） |
| 草稿 → 领星 | ★ **我们发出去的 ≡ 领星落下的**（货号 · 数量 · 单价 · 仓） | ★ `v_check_po_landed`（G1）—— **原设计缺** |
| 领星 → 回读 | 作废 / 入库被看见；看不见的被告警 | `recon_run` · `recon_discrepancy` · 水位告警（§4） |
| 回读 → 记录状态 | 作废退回、良品入库进准备排货 | `v_check_line_state` 同上 |
| 计划 ← 订单（反查） | 任一记录能说出：哪几张单 · 每店多少 · 落了多少 | ★ `GET /v1/plan-lines/{id}/trace`（G4，`08:83` 已有定义）—— **原设计缺** |

### 11.2 评估出的缺口与修补

| # | 缺口 | 不补的后果 | 修补 |
|---|---|---|---|
| ★ G1 | 下单后没有核对「领星落下的」与「我们发的」是否一致；领星侧还能用变更单改量（实测 3 张） | 领星里数量被改、或接口静默截断，计划→订单的链在**最后一跳**断了，没有任何东西失败 | `v_check_po_landed`：按 `lx_item_id`（缺时按 sku + 仓）比 `po_line` 与领星 PO 明细的 sku / `quantity_real` / 单价 / 仓；不符进 `recon_discrepancy` 并在采购单上告警。下单成功后立即用 `PurchaseOrderList` 读回一次做首检 |
| ★ G2 | fake 模式下回读跳过 fake 单 ⇒ **闭环在下单之后断掉**，到不了「准备排货」，mock 与真 API 跑不出同一屏（`00e` A 判据⑥ 同款要求） | 只能在 live 下验证后半段 = 每次验证都在领星建真单 | 「模拟外部」抽屉（`16` §0 · E-1 全局一个）：对 fake 单模拟 收货 / 入库（良品·待检·次品）/ 撤销 / 作废 / 变更数量 → 写 `*_obs(source='sim')`。★ 明确标「模拟外部」，不长成业务输入框 |
| ★ G3 | 恒等式只在触发器里，**查不出来**；`00e` 阶段 B 判据① 措辞与视图不符（矛盾 2） | 「过了」和「没测到」长得一样 | `v_check_*` 一族：每个视图返回**违例行**，空 = 通过 · `GET /v1/checks/purchase` 汇总 · E2E 每步之后断言全空 · 判据① 改写为「Σ 映射 ≡ 已确认量；Σ 已下单单的映射 ≡ 已下单量」 |
| G4 | UC-P7 反查没进设计 | 运营问「我这条计划下到哪了」答不出 | `/plan-lines/{id}/trace` 的承重墙① 部分本刀实现 |
| G5 | 只有按采购单看，没有**按计划看**的完成度 | 采购员不知道还剩哪些没组；运营不知道计划落了多少 | 计划级汇总：计划 × 货号 × 月 —— 计划采购 / 已确认 / 已下单 / 良品入库 四列并排，不合成达成率（`00e` D 判据同款） |
| G6 | 错误一次只报一个；⛔ 前才发现缺字段 | 来回试错 | `GET /v1/purchase-orders/{id}/preflight` 一次返回全部闸的通过 / 不通过；草稿列表直接显示「缺什么」 |
| G7 | 采购员身份未定 | 「我的采购单」无主；不可逆操作无主 | ★ 改由 §12 RBAC（D11）；登录源见 U5 |
| G8 | 范围膨胀：API 复核、QC 回读等压在核心目标前面 | 核心闭环迟迟不能验收 | 按 §11.4 分里程碑：先证明「计划→订单」正确，再接入库回读 |

### 11.3 对 `00e` 阶段 B 六条判据

| 判据 | 由什么兑现 | 状态 |
|---|---|---|
| ① 承重墙① 恒等式 | `v_check_po_source` + 判据改写（G3） | 补后可兑现 |
| ② 部分下单用量表达 | 量视图（§3.4），不新增状态值 | ✓ |
| ③ 作废读回并退出已下单 | D5 + 新退回边 + 回读 / 模拟外部 | ✓（fake 靠 G2） |
| ④ 分批到货，首批与到齐分得开 | 首批 → 准备排货（D10）· 到齐 = 领星 9 + 量并排（§7） | ✓ |
| ⑤ 良品 / 待检 / 次品分开 | `po_ib_obs` 三列 · D7 | ✓ |
| ⑥ 多仓库采购 | `po.wid` 可空 + `po_line.wid`（B7） | ✓ |

### 11.4 里程碑 —— 每个都能独立验收

| 里程碑 | 含 | ★ 验收（全部可复现） |
|---|---|---|
| **M1 计划→订单正确**（fake） | 刀0 · 1 · 2 · 3 · G1~G6 · 模拟外部的「下单」部分 | 建计划 → 提交 → 组单（含分店上限、超计划量）→ 下单四种结果 → `v_check_*` 全空 → trace 能反查 |
| **M2 首张 live 单** | M1 + live 开关 | 负责人亲手下一张 → `v_check_po_landed` 空 → 回答 §6.7 右列三问 |
| **M3 入库闭环** | 刀4 · 模拟外部的到货部分 · 作废 / 撤销 | fake：模拟到货 → 准备排货；模拟作废 → 退回；live：首次回读 + API 复核 → `recon_run` 留痕 |
| **M4 交互完善** | 刀5 余项 · V10 / V11 预填 | 采购员独立完成一轮：从首页工作队列到下单 |

### 11.5 交互管理（深入要求）

| 采购员要做的事 | 设计给的 |
|---|---|
| 一眼知道今天要干什么 | 首页工作队列：待组单记录 · 草稿（缺什么）· 待人工核实 · 不一致告警 · 各源截至 |
| 少填 | 采购员只有 1 人、采购方 2 个 → 默认值；V10 分店预填；V11 供应商 / 单价 / 质检方式按历史预填并标出处 |
| 下单前不踩雷 | preflight 一次列全；⛔ 弹窗列出将产生什么 + FAKE/LIVE |
| 出错可回 | 草稿随便改 / 删；下单后只能去领星作废，读回自动释放额度（D5） |
| 回答别人的问题 | 从计划 / 记录 / 采购单三个入口都能反查到同一条链（G4 · G5） |

---

## 12. 身份与权限（RBAC）—— 负责人 2026-10-07

> 「采购员身份不应该是硬编码，生产环境必须是由用户登录后，并有 RBAC 权限的用户才能使用。」
> 「RBAC 是使用 `/home/fido/work/2026/cf-access-rbac/` 直接通过 HTTP 调用的。这是生产环境存在的。
>   而我们系统不负责登录，只管用户是否有权使用。」

★ **重裁 Q-5（D11）**：命中 `00d` §3 风险「不可逆操作无主」—— `createPurchaseOrder` 是本服务第一条不可逆出站写。V9 作废。
以下出处 `rbac:` = 兄弟仓 `cf-access-rbac`。

### 12.1 谁做什么

```
浏览器 ─▶ [IdP 网关：CF Access 或 Keycloak/oauth2-proxy]  登录在这里，不在我们
            │ 注入用户 JWT（Cf-Access-Jwt-Assertion / Bearer / X-Auth-Request-Access-Token / cookie CF_Authorization）
            ▼
        service_supplychain 后端 ─▶ POST rbac.bogoo.ai/api/service/users/by-jwt {jwt}
            │                         带 Service Token（CF-Access-Client-Id/Secret）
            │                       ◀ user{id,display_name,...} · roles[] · permissions[]
            ▼
        按动作判权限 → 放行 / 403
```

| 事实 | 出处 |
|---|---|
| RBAC 不登录、只验 JWT（按 `iss` 选 JWKS，RS256，验 exp） | `rbac:docs/架构设计.md:5-9,93` · `src/lib/jwt.ts` |
| 下游只转发原始 JWT，不自己验签 | `rbac:README.md:267-273` |
| 「替真人查权限」用法，Service Token 不必在 RBAC 建用户 | `rbac:docs/机器账号接入指南.md:31-39` |
| 现成 FastAPI 样板 `src/dev-examples/fastapi_auth.py`（四级取 JWT、缓存、`require_permission`） | `rbac:README.md:120` |
| 前端拿当前用户 = 我们自己的 `GET /v1/me`；浏览器不能跨域直调 RBAC | `rbac:fastapi_auth.py:625` · `src/index.ts:22-36` |

### 12.2 两种模式

| `[auth] mode` | 身份 | 用于 |
|---|---|---|
| `dev` | 现状 `x-actor` 下拉，屏上常驻「开发身份 · 留痕可伪造」 | 开发 · 测试 |
| `rbac` | 上面的链路；`x-actor` 头被忽略 | 生产 |

| 硬闸（启动钩子，全局规范第 4 条） |
|---|
| ★ `[erp] mode=live` 且 `[auth] mode=dev` → **启动失败**（不可逆操作必须有主） |
| `rbac` 模式缺 `RBAC_API_URL` / Service Token → 启动失败；启动时探 `GET /health`，不通 → 首屏 WARN（不阻塞 /health，同 I-1） |

### 12.3 ★ 失败必须分得开（样板会把它们混成 401，我们不照搬）

| 情况 | 我们返回 | 缓存 |
|---|---|---|
| 请求里没有 JWT | 401 `not_authenticated`（「请经登录网关访问」） | — |
| RBAC 说 JWT 无效 / `matched:false` | 401 `not_authenticated` | 不缓存 |
| 用户已停用（权限空） | 403 `account_disabled` | 5 分钟 |
| 缺某权限 | 403 `forbidden {permission}` | 5 分钟 |
| ★ RBAC 连不上 / 超时 / 5xx | ★ **503 `rbac_unavailable`**，带 cause code；★ **fail closed**（不放行），但不伪装成「你没登录」 | ★ 不缓存 |

缓存：`sha256(jwt)` → 结果，TTL 300 s（`rbac:docs/架构设计.md:103`）；进程内（单进程部署），多进程时再议。
日志三问：`rbac op=by_jwt host= ms= outcome= http= cause= user= cache=hit|miss`；每次 403 记 WARN（谁 · 想做什么 · 缺哪个权限）。

### 12.4 权限码（两段式 `<action>:<resource>`，`system='scm'`）

规范：码全局唯一；通配只看 action 段（`read:*` 会命中所有系统）；不申请 `<action>:*`；管理类用 `manage:` 不用 `admin:`（`rbac:docs/权限目录接入指南.md:125-158`）。
★ 资源名统一带 `scm-` 前缀以免与他系统撞车；**申请前先拉 `/api/service/catalog` 查重**。

| 码 | 动作 | 建议给 |
|---|---|---|
| `read:scm` | 进入本系统、读 | 全部 |
| `edit:scm-plan` · `submit:scm-plan` | 编辑 / 提交销售计划 | 运营 |
| `draft:scm-po` | 组单 · 改 · 删草稿 | 采购员 |
| ★ `place:scm-po` | ⛔ 下单 | 采购员 |
| ★ `resolve:scm-po` | §6.6 人工处理 | 采购员 |
| `cancel:scm-line` | 撤销计划单记录（须理由） | 管理 |
| `manage:scm-binding` | 维护 用户 ↔ 领星采购员 绑定 | 管理 |
| `simulate:scm-external` | 模拟外部（仅 fake） | 开发 / 测试 |

角色由 RBAC 管理后台分配（含按部门 / 职位自动规则，`rbac:README.md:886-894`），**不在本服务**。
登记方式：按目录 `conventions.request_channel` 向 RBAC 维护者申请，由其录入（同 `rbac:migrations/0006` 的做法）。

### 12.5 用户 ↔ 本服务 ↔ 领星

| | |
|---|---|
| 本服务 `actor_id` | = RBAC `user.id`（稳定 UUID）；首次通过认证时 upsert 进 `actor`（名称取 `display_name`，只作显示快照） |
| 领星采购员 | RBAC 无外部 ID 映射（`rbac` 全仓无 `opt_uid`）⇒ 人填表 `actor_lingxing(actor_id → opt_uid)`，`manage:scm-binding` 维护（铁律 6）；未绑定 → preflight 点名 |
| 留痕 | `po_event` · `erp_call` · `plan_line_event` 的 actor 取已验证身份；带 `auth_mode`，dev 记录与生产永远分得开 |
| 阶段 A 遗留 | 007 种的三个开发 actor 只在 dev 模式可用；rbac 模式下以它们为 `x-actor` 的请求被忽略 |

### 12.6 风险与未决

| # | 项 |
|---|---|
| ~~U5~~ | ★ **已裁定（D12，负责人 2026-10-07）：生产放在 Keycloak（`auth.zestrade.com`）+ oauth2-proxy 之后，同 `pm.zestrade.com`。** JWT 取 `X-Auth-Request-Access-Token` → `Authorization: Bearer`（`rbac:fastapi_auth.py:30-33,283-286`）；CF 两个来源保留在回退链末尾，不作主路径。登出走 Keycloak 登出地址（样板 `KEYCLOAK_LOGOUT_URL`）。oauth2-proxy 与 Keycloak client 的配置属部署，不在本仓 |
| U6 | 后端需出网访问 `rbac.bogoo.ai:443`。★ 开发机已实测可达（2026-10-07，`/health` 200 ~2 s）；**生产部署环境仍待确认** |
| U7 | ★ Service Token 已到位（负责人 2026-10-07，存 `config.toml [rbac]`，git-ignored）；`whoami` 200、`looks_like_service_account:true`。★ 拟定 9 个码与目录 91 个**零撞名**（目录里的 `jxd_purchase` 是物料对账 / 交期跟进，不是本服务）。申请渠道 = 目录 `request_channel`：RBAC 维护者 fido@duoweitree.com。M2 前录入 |
| F1 | ★ 实调事实：`rbac.bogoo.ai` 的 Cloudflare 边缘按 User-Agent 拦截 —— Python 默认 UA 得 **403 / error code 1010**（与凭证无关）。RBAC 客户端必须带显式 `User-Agent`；且 1010 要单独归类，不许读成「没权限」 |
| F2 | ★ 领星 OpenAPI 有 **IP 白名单**：必须经固定 IP 的 Squid 代理（HTTP CONNECT + Basic Auth）出站（出处 `/home/fido/work/2026/tools/nodejs/commerce/lingxing_erp/INDEX.md:32-45`）。凭证与代理 2026-10-07 复制进 `config.toml [erp]`（git-ignored）。live 缺代理 → 启动失败；经代理时 connect() 阶段失败仍归 not_sent，cause 点名代理地址 |
| R1 | `rbac` 侧不校验 `aud`（`src/lib/jwt.ts:154-162`，与其 `docs/架构设计.md:492` 矛盾）：同一 team 下任一 Access 应用的 JWT 都能换到身份。不是本服务能修的，登记风险 |
| R2 | RBAC 无主动失效：撤权后最多 5 分钟仍有效。对 ⛔ `place:scm-po` 可在下单前**绕过缓存**再查一次 —— 设计取值 V12，建议「是」 |

---

## 13. 实施中的裁定（M1，2026-10-07）

> 台账原文在 `.superpowers/sdd/2026-10-07-purchase-order-m1/` 的 ledger；这里只登记**改变了设计**的几条，论证见各节。

| # | 裁定 | 落点 |
|---|---|---|
| 1 | **锁模式 `FOR NO KEY UPDATE`**（不是 `FOR UPDATE`）：提交时检查锁 `plan_line`，`FOR UPDATE` 与并发事务经 `po_source_map` 外键持有的 `KEY SHARE` 冲突，两个提交者互等；`NO KEY UPDATE` 与 `KEY SHARE` 兼容且仍按记录串行。应用层 `lock_plan_lines` 同模式 | `012` `assert_line_po_qty` · `03` §4.0 |
| 2 | **恢复动作的 actor = 原下单人**：库里无 system actor（`plan_line_event_actor_fk`），M1 不加种子；事件 `src=recover:<call_id>` 区分；M2 接 RBAC 时再定系统身份 | §6.3 |
| 3 | **模拟接口门控**：仅 `[erp] mode=fake` 且 `[auth] mode=dev`，否则 403 `simulation_disabled`；测试配置钉死这两项，免得本机切 live 后测试经真代理打领星建真单 | `08` §1.2 |
| 4 | **落地核对三态**：`checked` / `read_outcome` / `read_cause` / `problems`。读回不成 → `checked=false`，不开 `landed_mismatch`、不拿旧观测冒充「已核对」；`not_observed` 由 `v_check_po_landed` 如实反映 | `07` §2 |
| 5 | **settle 原子化**：close_call 与「记已下单」/「开待核实」同一事务（§6.2）；`needs_verify.detail` 冻结为事实、时间线实时推导已知 / 未知（§6.6） | §6.2 §6.6 |
| 6 | **手动推进拒绝推导态**：`已确认 / 已下单 / 准备排货` → 422 `derived_state`，`allowed[]` 过滤它们；错误码优先级 404 → `unknown_state` → `terminal_state` → `derived_state` → `illegal_transition`（对已撤销记录说「由量推导」不真实） | `04` §1.2 |
| 7 | 试过下单（有 `erp_call`）的草稿被删 → **软作废**（`discarded_at`），单头留档、释放额度；对已作废草稿的 place / 登记 → 409 `po_discarded` | `03` §4.0 |
