# 领星备货单回执 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 定时 + 手动从领星 API 读回海外仓备货单进度，只追加落观察，全部已完成 → 排货单归档，两轴不一致点名。

**Architecture:** 纯函数 `rules/receipt.py`（状态映射 · 当前进度）；迁移 `023`（观察 / 轮次两张只追加表 · 当前进度视图 · 归档前置触发器）；`usecase/receipt.py`（逐表持 `sheet_op` 读 `ows_order_detail`、写观察、归档）；调度器 job `receipt_tick`；`POST /v1/sheets/{id}/receipt`；详情带进度；web 显示 + 现查按钮。

**Tech Stack:** PostgreSQL plpgsql · FastAPI · APScheduler · React/TS · pytest · vitest

**Spec:** `docs/superpowers/specs/2026-10-13-dispatch-receipt-design.md`

## Global Constraints

- 只读领星 API，不碰 CH（AR-D1）。
- 观察 / 轮次只追加；当前进度派生不存；旁路（已驳回 / 已撤销 / 领星无此单）优先，否则 rank 最大。
- 未知 status 不写观察，日志点名 `unknown_status`。
- 归档只在排货单全部未作废领星单都「已完成」；库层 `consignment_archive_needs_complete` 再核。
- 两轴不一致只点名，不改我方状态。
- 轮询间隔 30 分钟 · 设计取值 · 未实测（`service.toml` `[receipt] interval_minutes`）。
- 日志三问：打的谁 · 多久 · 怎么失败的。

## Review Focus

1. 一张排货单两张领星单，一张已完成一张待收货 → 不归档。
2. 读回倒退（60 之后读到 50）→ 观察照写，当前进度仍是已完成，不会把已归档打回。
3. 读失败 → 无观察、轮次记失败；同一轮其余单照读。
4. 表被提交 / 取消占着 → 本轮跳过该表，记 `skipped`，不算失败。
5. 已作废的领星单（取消删掉的）不再读。

---

### Task 1: 纯函数 `rules/receipt.py`
- [ ] `tests/test_rules_receipt.py`：映射表全行（含 `不存在或已删除` → 领星无此单、未知值 → None）；`current(obs)` 旁路优先 / rank 最大 / 倒退不影响；`all_complete(stages)`。先红 → 实现 → 绿。

### Task 2: 迁移 023 + DDL 测试
- [ ] `tests/test_ddl_023_receipt.py`：只追加；`v_ext_stage` 口径；归档前置。先红 → 迁移 → 绿。

### Task 3: 用例 · 接口 · 作业
- [ ] 替身 `ship_ows(no, status, sub_status)` 已有 / 补；`tests/test_api_receipt.py` 覆盖 Review Focus 1~5 与 API 形状、`sheet_busy`；作业测试 `receipt_tick` 只读已投送。先红 → 实现 → 绿。

### Task 4: 前端
- [ ] 类型 · client · 排货单面板进度 / drift / 现查按钮；测试先红。契约样本重写。

### Task 5: 文档 · 全量 · 应用迁移
- [ ] `00`（AR-D1 清单回执一行 ✅）· `03` · `04` · `08` · `10` · spec 状态；全量 py / web / 文档闸；应用 023。
