# 阶段 C-b · 海外仓排货单提交领星备货单 —— 设计

| | |
|---|---|
| 日期 | 2026-10-12 |
| 状态 | 负责人已批准（「Yes, build it as specified」）· 已实现（计划 `superpowers/plans/2026-10-12-dispatch-submit-ows.md`）· 待手动验收 |
| 依据 | `00` CB 组（CB-D1~D7 · CB-D4 实调记录）· C-a spec `2026-10-10-dispatch-draft-design.md` · `04` §4.3 / §5 / §7.6b · `07` §2（出站约定）· `08` §1.5 · `18` §2 · 领星文档 `Warehouse/CreateInbound.md` · `listOrderNos` · `OverSeasStockDetail` · `DeleteOverSeaStockOrder` · `Logistics/ChannelList` |
| 前置 | C-a 已上线（迁移 015~018） |
| 不在本篇 | FBA 提交（C-c，STA）· 领星备货单进度的定时回执（CB-Q4）· 计划记录 `已排货` 状态推进（CB-Q3） |

---

## 1. 负责人裁定（`00` CB 组，原文选项）

| # | 裁定 |
|---|---|
| CB-D1 | 先做海外仓备货单（`createInbound`），FBA 后做 |
| CB-D2 | 建成 **40 待发货**：只建单；配货 / 装箱 / 发货（扣库存）由仓库在领星做 |
| CB-D3 | 物流方式：镜像领星「头程物流渠道列表」，**每张海外仓排货单必选**，确认闸查齐 |
| CB-D5 | **一张已确认的排货表一个「提交领星」**；确认框列出将建的全部备货单；逐张建，已建的跳过（断点续跑） |
| CB-D6 | 已建备货单后仍可取消：逐张 `stockOrder/delete`（仅待发货）；有任一已过待发货 → 拒绝并点名 |
| CB-D7 | 备货单行：有 fnsku 的带 `product_id + fnsku + sid`；没有的只带 `product_id` |

## 2. 实调事实（CB-D4，2026-10-12）

- `logistics_list_type` 必须 = 1；`head_logistics_list` 只给 `{tax_fee_type: 3}` 即接受（3 = 按产品数量分摊；文档里标必填的轨迹 / 费用字段建单时不需要）。
- `status=40` 建单 → 直接「待发货」、**锁定**发货仓库存（不扣），`match_num` = 件数。
- `stockOrder/delete` → **释放**锁定；删后 `listOrderNos` 查不到。
- 被拒的建单**不落单**。
- ⚠️ 未知：同一 `inbound_order_no` 再建会不会被拒（CB-Q2）→ 幂等**只靠我们自己查重**，不依赖领星。
- ⚠️ 详情 `logistics_id` 回 null（CB-Q1）—— 物流方式写进去了没有，提交后在领星页面人工核一次。

## 3. 排货单 → 备货单的映射

```
一张海外仓排货单（channel=oversea, dest_wid）
   └─ 按排货行的来源仓 from_wid 分组 ── 每组一张领星备货单（领星一张单只有一个 s_wid）
        s_wid = from_wid · r_wid = dest_wid · logistics_id = 这张排货单选的物流方式
        status = 40 · logistics_list_type = 1 · head_logistics_list = {tax_fee_type: 3}
        inbound_order_no = SCM-CN-{consignment_id}-{from_wid}      ← 幂等键 / 查重键
        remark = 「scm 排货表 #{sheet} 排货单 #{consignment}」
        product_list = 该组每个 (货号, msku, 店) 一行：
            product_id = sku_catalog.product_id（★ 空 → 不能提交，点名「先刷新货号镜像」）
            stock_num  = 件数 · receive_num = 0
            fnsku + sid = msku_bridge.fnsku 非空时带上（CB-D7），否则不带
```

★ 同一组里同一 `product_id` 若因 fnsku 不同出现多行，照样多行（领星按 fnsku 区分）；fnsku 都为空的同货号行合并成一行。

## 4. 数据（迁移 `020_dispatch_submit.sql`（019 已被 CA-D12 现读占用））

| 表 / 改动 | 说明 |
|---|---|
| `logistics_channel` | 镜像领星头程物流渠道（`id · channel_name · method_name · provider_name · receive_country_codes · enabled · refreshed_at`）。源 = 领星 API `channelList`（CH 里没有这张表），由 `jobs/` 刷新 + 页面「刷新物流渠道」触发 |
| `consignment_logistics` | ★ 人填的（铁律 6）：`(sheet_id, dest_wid) → logistics_id`。按表级目的地键，重派生不丢（同装箱的理由） |
| `erp_call` + `consignment_id`（可空） | 出站留痕复用采购单那张表；`consignment_id` 与 `po_id` 二选一；同一排货单同一时刻最多一条在途写调用（部分唯一索引） |
| `ext_ref`（只追加） | 我们投送产生的外部单：`ext_ref_id · consignment_id · from_wid · kind='ows' · idem_key UNIQUE · ext_no（OWS…）· call_id · found_by（create / lookup）· at` |
| `ext_ref_void`（只追加） | 取消时删除外部单的留痕：`ext_ref_id · call_id · actor · reason · at` |
| `dispatch_attention` | 结果未知 → 待人工核实（同采购单 `po_attention` 的形状：开立冻结、只关一次）|
| 白名单 | S4 加 ★ `已投送 → 已取消`（CB-D6）；触发器：只有该排货单全部 `ext_ref` 都已作废才放行 |
| `已确认 → 已投送` 前置 | 触发器：该排货单每个来源仓都有一条未作废的 `ext_ref` |

## 5. 流程

### 5.1 确认前（C-a 的确认闸加一道）

- **G7 海外仓排货单已选物流方式**（CB-D3）：每张 oversea 排货单在 `consignment_logistics` 里有值且渠道仍启用。

### 5.2 提交（`POST /v1/sheets/{id}/submit`）

```
预览 GET /v1/sheets/{id}/submission
   → 待建的备货单清单（每张：发货仓 → 海外仓 · 物流方式 · 货号 × 件数 · fnsku/店 · 参考号）+ 已建的（OWS 号）
   → 阻断项点名：缺 product_id / 物流方式已停用 / 有待核实未处理
   → confirm_token = 清单内容摘要（同采购单 H-1：派生，不存储）

提交（带 confirm_token；内容变了 → 409 stale_confirm_token）
   对每张「还没有 ext_ref 的」备货单，按 consignment_id, from_wid 顺序：
     ① 查重：listOrderNos([参考号]) 命中 → 记 ext_ref(found_by=lookup)，跳过
     ② erp_call(in_flight) → createInbound → 收尾
        ok + 单号      → ext_ref(found_by=create)
        rejected       → 停在这里，原样点名领星的 code / msg（被拒不落单，已实调）
        not_sent       → 停，可直接重试
        unknown / 超时 → 再查重一次：命中 → ext_ref；仍不知 → 开「待核实」并停（★ 绝不自动重试，出站 #2）
   某张排货单所有来源仓都有 ext_ref → 该排货单 已确认 → 已投送
   返回：每张备货单的结果（created / adopted / rejected / not_sent / needs_verify / skipped）
```

★ 断点续跑：再点「提交领星」只建还没有 `ext_ref` 的。待核实未处理时整表拒绝再提交。

### 5.3 待核实（同采购单的三个出口）

「重新查重」·「登记已有备货单号」（人在领星找到了）·「确认领星无此单」（写理由；之后允许重建）。

### 5.4 取消（CB-D6，扩展 C-a 的取消）

```
已确认 / 部分或全部已投送 的排货表取消：
  对每条未作废的 ext_ref：详情 → 状态 ≠ 待发货 → 整体拒绝，点名哪张备货单、什么状态
  全部是待发货 → 逐张 stockOrder/delete（实调：释放锁定）→ ext_ref_void
  任一删除失败 / 结果未知 → 停，点名；已删的照样留痕；表不取消
  全部删净 → 排货表 已取消（排货单 已投送 / 已确认 → 已取消）
```

## 6. 接口（`api/ui/`）

| 方法 · 路径 | 说明 |
|---|---|
| `GET /v1/logistics-channels` | 镜像里启用的渠道（`id · channel_name · method_name · receive_country_codes`），带 `refreshed_at` |
| `PUT /v1/sheets/{id}/logistics/{dest_wid}` `{logistics_id}` | 草稿表才能改；渠道不存在 / 停用 → 409 |
| `GET /v1/sheets/{id}/submission` | 预览 + 已建 + 阻断项 + `confirm_token` |
| `POST /v1/sheets/{id}/submit` `{confirm_token}` | 见 §5.2；409 `sheet_not_confirmed` · `stale_confirm_token` · `needs_verify` · `submission_blocked` |
| `POST /v1/sheets/{id}/ext-refs/verify` · `register` · `confirm-absent` | 待核实三个出口 |
| `POST /v1/sheets/{id}/cancel` | 扩展：已投送也可（§5.4）；409 `ows_not_cancellable`（点名单号 + 状态） |

## 7. 界面（照设计 artifact 的风格扩展，不另起）

- 「排货单与装箱」里每张海外仓排货单加 **物流方式** 下拉（必选）；FBA 单写「FBA 提交在下一阶段」。
- 已确认的表：底部 **提交领星** → 确认框列出将建的每张备货单（发货仓 → 海外仓 · 物流 · 货号 × 件数 · 店），⛔ 样式（对外部系统的写）；提交后逐张显示结果。
- 排货单上显示领星备货单号（OWS…）与「已投送」。待核实 → 红条 + 三个出口。
- 取消已投送的表：确认框写明「会删除领星里的 N 张备货单（仅待发货可删）」。

## 8. 可追踪

- 每次出站：`erp_call` 一行（脱敏请求 · 结果 · 领星 code/msg · 耗时 · host/path），`op=ows_submit sheet= consignment= from_wid= idem= outcome= ows= ms=`。
- 查重命中、待核实开立、删除，各自一行日志 + 表留痕。

## 9. 待裁定（登记 `00`，本篇不定）

| # | 问题 | 暂行 |
|---|---|---|
| CB-Q1 | 详情 `logistics_id` 回 null：物流方式是否真写入 | 首次真提交后在领星页面人工核一次 |
| CB-Q2 | 领星是否拒绝重复 `inbound_order_no` | 不依赖它：建前 / 超时后都查重 |
| CB-Q3 | 投送后计划记录要不要推进到「已排货」（CA-D10 后记录可能停在 已提交，状态机没有这条边） | 不推进；量（已投送）照常进视图 |
| CB-Q4 | 领星备货单进度（待发货 → 待收货 → 已完成）回执频率与落表 | 本篇不做；提交结果页给「查看领星状态」现查 |
| CB-Q5 | `tax_fee_type` 取 3（按数量）是否合适 | 设计取值 · 未实测（只影响领星里的费用分摊） |

## 10. 校验

- 纯：备货单分组 / product_list 组装（fnsku 有无、合并）· confirm_token 稳定性 · G7。
- 集成（领星替身 `tests/lingxing_double.py` 扩展 owms 三个端点）：正常建 · 被拒停 · 超时后查重命中 · 超时仍未知 → 待核实且不重试 · 断点续跑 · 取消删除成功 / 已发货拒绝 · 白名单 `已投送 → 已取消` 前置。
- 每条先红一次。
