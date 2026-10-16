# C-b 海外仓排货单提交领星 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 已确认的排货表一键「提交领星」：每张海外仓排货单按来源仓拆成领星备货单（status 40），查重 · 待核实 · 断点续跑；取消时删除领星备货单。

**Architecture:** 沿用采购单下单的出站形状（`erp_call` 留痕 · 结果未知查重 · 绝不自动重试 · 待核实三出口），对象换成 `ext_ref`（只追加）。纯规则 `rules/ows.py` 组单与 confirm_token；`usecase/submit_ows.py` 管调用与事务；迁移 `020`。

**Spec:** `docs/superpowers/specs/2026-10-12-dispatch-submit-ows-design.md`（CB-D1~D7 · CB-D4 实调）

## Global Constraints

- 建单 `status=40`、`logistics_list_type=1`、`head_logistics_list={"tax_fee_type": 3}`（CB-D4 实调；3 = 设计取值 · 未实测，CB-Q5）。
- 参考号 `SCM-CN-{consignment_id}-{from_wid}`；建前 / 结果未知后都 `listOrderNos` 查重；**超时一律转人工，绝不自动重试**（出站 #2）。
- 每次出站一行 `erp_call`（脱敏）；日志三问。只追加表禁 UPDATE / DELETE。
- 迁移先应用到 `scm` 再告诉负责人重启（memory: apply-migrations-with-the-change）。
- FBA 排货单不提交（C-c）。

## Review Focus

1. 同一排货单两个来源仓：第一张建成、第二张被拒 → 再点只建第二张（断点续跑）。
2. 建单超时且查重仍查不到 → 待核实，整表禁止再提交，直到人处理。
3. 取消时有一张已过「待发货」→ 整体拒绝、点名，已删的留痕，表不取消。
4. 货号镜像缺 `product_id` → 提交前点名阻断，不发请求。
5. 并发两人同时点提交 → 只有一个在途（部分唯一索引），另一个 409。

---

### Task 1: 迁移 020 + 状态机
- [ ] 测试 `tests/test_ddl_020_submit.py`：`consignment_logistics` 冻结随表；`ext_ref` / `ext_ref_void` / `dispatch_attention` 只追加；`已确认→已投送` 要每个来源仓都有未作废 ext_ref；`已投送→已取消` 要全部 ext_ref 已作废；白名单与 `04` §7.6b 一致（T21）。先红。
- [ ] `migrations/pg/020_dispatch_submit.sql` + `04` 白名单表 + `03` §5 摘要 + conftest DATA_TABLES。

### Task 2: 物流渠道与 G7
- [ ] `usecase/logistics.py`：`refresh(actor)` 分页读 `channelList` 落 `logistics_channel`；`GET /v1/logistics-channels`（空或陈旧 24h 自动刷，设计取值 · 未实测）· `POST /v1/logistics-channels/refresh` · `PUT /v1/sheets/{id}/logistics/{dest_wid}`。
- [ ] `rules.gates` 加 G7（oversea 排货单都选了启用的渠道）。测试先红。

### Task 3: 组单纯规则 `rules/ows.py`
- [ ] `plan_orders(consignments, logistics, products, fnsku)` → `[{key, consignment_id, from_wid, r_wid, logistics_id, idem_key, product_list, lines}]` + `blockers`；同货号无 fnsku 合并、有 fnsku 分行；`confirm_token(orders)` 稳定。测试先红。

### Task 4: 提交 `usecase/submit_ows.py`
- [ ] 替身扩 `create_ows_order` / `ows_order_nos` / `ows_order_detail` / `ows_order_delete`（脚本化 ok / rejected / not_sent / unknown_created / unknown_absent）。
- [ ] `GET /v1/sheets/{id}/submission` · `POST /v1/sheets/{id}/submit`：§5.2 全部分支；排货单全建成 → 已投送。测试先红（Review Focus 1、2、4、5）。

### Task 5: 待核实三出口 + 取消删除
- [ ] `POST /v1/sheets/{id}/ext-refs/verify|register|confirm-absent`；取消扩展（§5.4，Review Focus 3）。测试先红。

### Task 6: 前端
- [ ] 排货单面板：海外仓单「物流方式」下拉；已建备货单号 · 已投送；待核实红条 + 三出口。
- [ ] 底部：已确认 →「提交领星」⛔ 确认框（逐张列出）→ 逐张结果；取消确认框写明会删几张领星单。
- [ ] 契约样本 + 页面测试。

### Task 7: 文档 · e2e · 全量
- [ ] `08` / `10` / `00e` / spec 状态；`test_e2e_c_dispatch_draft.py` 加提交与取消；全量 py / web / 文档闸；应用迁移。
