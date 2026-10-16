# C-c1 FBA 发货计划提交 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** 平台仓 · FBA 表确认后「提交领星」= 每张 FBA 排货单 × 来源仓在领星建 FBA 发货计划批次并锁库存；取消 = 释放库存 + 备注 + 点名人工作废；回执读回计划状态。

**Architecture:** 纯函数 `rules/fba_plan.py`（分组 · product_list · 阻断项 · token · 状态映射）；迁移 `024`（ext_ref kind 加 `fba_plan` · 计划号表 · 锁 / 释放事件表 · 已投送前置「全部已锁」）；`usecase/submit_fba.py`（查重 · 建 · 锁 · 取消 · 回执读一张），`submit_ows` / `receipt` / `sheet.cancel` 按去向分派；web 提交框与排货单显示适配 FBA。

**Spec:** `docs/superpowers/specs/2026-10-13-dispatch-submit-fba-plan-design.md`

## Global Constraints
- 建后必锁；锁按 `data.failNum` 判；锁失败 → 排货单不算已投送，可只重锁不重建（CC-D3）。
- 无幂等键：批次 `remark` = 参考号，建前 / 结果未知后查重（`shipmentPlanLists`，`sids` 逗号串）；绝不自动重试。
- 取消：计划已处理（10）→ 拒；否则释放 → 改计划备注「SCM 已取消 请作废」→ 作废留痕 → 点名人工作废（CC-D4）。
- 回执：-5 已驳回（旁路）· 0 待审核 · 5 待处理 · 10 已处理；不归档（CC-Q4）。
- `packing_type` = 2 原厂 · 设计取值 · 未实测（CC-Q3）。
- 我们的名字不用 shipment_plan / allocation / inbound。

## Review Focus
1. 建成但锁失败 → 再点提交只重锁，不再建。
2. 结果未知且查不到 → 待核实，不重试。
3. 取消时计划已处理 → 一张都不释放，整体拒绝。
4. 缺 fnsku / 缺装箱 → 提交前点名，不发请求。
5. 回执把 FBA 计划状态读回，不误归档。

### Task 1: 纯函数 · Task 2: 迁移 024 · Task 3: 用例 / 接口 / 取消 / 回执 · Task 4: 前端 · Task 5: 文档 / 全量 / 应用迁移
