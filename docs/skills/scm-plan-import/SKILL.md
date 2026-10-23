---
name: scm-plan-import
description: 当用户要你为供应链系统（service_supplychain）生成并导入「销售计划」时使用 —— 按用户选定的货号 / MSKU 查映射与库存销量预测，填写 Excel，交人审查后通过 /pub/v1 接口导入为新计划；导入被拒时按错误码修表或交还给人，错太多时删除重来。
---

# scm-plan-import · 销售计划 Excel 导入

只讲怎么用。Excel 的列与格子规则以 `docs/superpowers/specs/2026-10-22-plan-excel-import-design.md` §2 为准（本文不抄）；
接口字段以 `docs/08-后端接口清单.md` §1.8 为准。

## 调用约定

- Base URL：`<BASE_URL>`（由部署方告知），下文路径都挂在它后面。
- 每个请求带 `x-actor: ai.agent`。导入与删除**只认**这个身份，其它身份 → 403 `actor_not_allowed`。
- 错误信封：`{"error": "<code>", "hint": "...", ...}`。导入被拒是 422 `import_rejected`，全部问题在 `errors[]`。

## 流程（五步，顺序不能换）

1. **问用户**要生成哪些 SKU（货号）/ MSKU。没给就问，不要自己挑。
2. **查映射**：`GET /pub/v1/mappings?sku=...`（还可用 `seller_sku` `sid` `market`，至少给一个条件，否则返回 `need_query: true` 空列表）。
   拿到每个 (msku, 店铺ID) 的 `sku` `seller_sku` `sid` `seller_name` `market`。
   `claimed_by` 非空 = 已被别的计划占用，导入会被拒（`claimed_elsewhere`）—— 先告诉用户。
   `truncated: true` 说明结果被截断，收窄条件再查。
3. **查预测**：`POST /pub/v1/forecast/preview`，JSON `{"period_start": "YYYY-MM", "months": N, "mskus": [{"seller_sku": "...", "sid": "..."}], "skus": ["..."]}`
   （`mskus` 与 `skus` 至少一个，不许多余字段）。得到这些 msku 逐月的预测销量与预测库存、断货日。
   `demand[].system_units` 就是预测销量，`demand[].system_basis` 是它的依据（用了哪几个月历史），给人审查时一并展示；预测库存在 `forecast[]`（每 msku × 月：`opening` 期初 · `closing` 期末 · `stockout_date`）与按货号汇总的 `forecast_sku[]`（`opening` `closing`），`forecast_msku[]` 只给首个断货日；`state` 为 `unknown` 时数是 null、`reason` 说明为什么算不出；`source_notes` 写明数据截至何时、哪些没取到（未知不是 0）。
4. **生成 Excel**：`GET /pub/v1/plans/import/template?period_start=YYYY-MM&months=N` 下载模板，**只填用户选的产品**，
   销量行（msku × 月）填期望销量，采购行（货号 × 月）填计划采购。**删掉两行示例**。空格子留空，不要写 0（空 = 未知，0 = 明确为零）。
   **交给人审查、确认数量后才能导入**，不要自己直接导。
5. **导入**：`POST /pub/v1/plans/import`，`multipart/form-data`：`title` `period_start`（`YYYY-MM`）`months` `file`。
   成功 201，返回 `plan_id`。失败 422，见下表。

导入是整体的：任何一处有错，一张计划都不建，且**所有**错一次列全，每条有 `row`（Excel 行号，0 = 表头 / 文件级）和 `col`。

## 导入被拒（422）怎么办

- 小问题（格式、数值）：按 `errors[]` 逐条改表，改完**再交人确认**，重新导入。
- 产品没维护好（`unknown_msku` `unknown_seller` `claimed_elsewhere`）：**停下，告诉用户**，由人去维护产品或释放占用。**绝不要改 msku / 店铺ID 去绕过**。
- 错太多、改表不划算：`DELETE /pub/v1/plans/{plan_id}` 删掉已建的计划（只对已经建成的计划有意义；导入被拒时没有计划可删），按用户意见重新生成再导入。

### 错误码 → 处置

| `errors[].code` | 含义 | 你该做什么 |
|---|---|---|
| `title_required` `validation_error` `bad_months` `bad_period_start` `bad_date` | 表单的标题 / 起始月 / 月数不对（`row=0`，出现时只回这些） | 改表单参数重新提交 |
| `file_too_large` | 超 5 MB 或 2,000 行 | 拆成多个计划 / 缩小范围，请用户确认 |
| `not_xlsx` | 不是 xlsx | 用模板重新生成 |
| `bad_header` | 缺必填列 / 列名不认识 / 重复 | 回到模板的表头，不要改列名 |
| `missing_month` `extra_month` | 月份列与计划的月份不一致 | 让月份列正好是计划的 N 个月 |
| `empty_file` | 没有数据行 | 检查是否把示例行删光了又没填 |
| `bad_type` | `类型` 不是「销量」/「采购」 | 改成这两个值；多半是没删示例行 |
| `bad_units` | 月份格不是空或 ≥0 整数 | 改成整数，或留空 |
| `missing_field` | 销量行缺 msku / 店铺ID，或采购行缺货号 | 补上（值来自第 2 步映射） |
| `unexpected_field` | 采购行填了 msku / 店铺ID / 店铺 / 国家 | 清空这些格 |
| `duplicate_row` | 同一 (msku, 店铺ID) 或同一采购货号出现多行 | 合并成一行 |
| `purchase_sku_not_claimed` | 采购货号不在任何销量行的货号里 | 补对应销量行，或删掉该采购行 |
| `seller_mismatch` `market_mismatch` | 填的店铺名 / 国家与店铺ID 对不上（逐字比较） | 以第 2 步映射里的值为准改正，或清空 |
| `sku_mismatch` | 填的货号与这个 msku 的货号不符 | 以第 2 步映射里的货号为准 |
| `unknown_seller` | 店铺ID 系统里没有 | ★ 产品没维护好 → 停下，告诉用户，不要改 |
| `unknown_msku` | 系统里没有这个 msku 的映射 | ★ 产品没维护好 → 停下，告诉用户，不要改 msku 绕过 |
| `claimed_elsewhere` | msku 已被另一张计划占用（`extra.plan_id` `title` 点名） | ★ 停下，告诉用户是哪张计划占着，由人决定 |

## 导入之外的响应

| 响应 | 含义 | 你该做什么 |
|---|---|---|
| 403 `actor_not_allowed` / 400 `unknown_actor` | 身份头不对 | 检查 `x-actor: ai.agent` |
| 404 `unknown_msku`（预览） | 有 msku / 货号不在桥表，`mskus[]` `skus[]` 点名 | 告诉用户，不要硬算 |
| 409 `plan_submitted`（删除） | 计划已提交过，不能删 | 不要重试；告诉用户由人归档 |
| 409 `plan_referenced`（删除） | 被别的表引用着 | 不要重试；告诉用户（`table` 点名） |
| 404 `plan_not_found`（删除） | 计划不存在或已删 | 视为已删，别重试 |
| 503 `mirror_stale` / `forecast_source_unavailable` / `forecast_source_unusable` | 数据源暂不可用 | 告诉用户，稍后再试；不要拿旧数顶替 |
| 500 `stock_window_past_anchor` | 系统自身错误（导入已回滚） | 不要重试，告诉用户 |
