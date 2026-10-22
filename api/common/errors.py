"""错误形状与「库层拒绝 → 错误码」的翻译。

★ 形状按 team-lead 2026-09-22 裁定：{"error", "hint", …点名字段平铺在顶层}。
★ 翻译靠 constraint_name 而不是 str(e)：两种冲突的 message 都长得像一句话，
  真凶只在 pgcode / constraint_name 里。
"""
from __future__ import annotations

import logging

import psycopg2

from shared.pg_client import pg_error_fields
from usecase.lead_time import LeadTimeMisconfigured

log = logging.getLogger("scm.api")


class ApiError(Exception):
    """响应体 = {"error": …, "hint": …, **fields}。

    ★ 点名字段平铺在顶层而不是塞进 detail：前端按 `body.in_flight_rev` 取值，
      多一层嵌套就是多一处会对不上的约定。
    """

    def __init__(self, status: int, error: str, hint: str, fields: dict | None = None):
        self.status, self.error, self.hint, self.fields = status, error, hint, fields or {}
        super().__init__(f"{status} {error}: {hint}")

    def body(self) -> dict:
        return {"error": self.error, "hint": self.hint, **self.fields}


#: constraint / index 名 → (error, status, hint)
CONSTRAINT_ERRORS: dict[str, tuple[str, int, str]] = {
    "msku_claim_one_active_idx": ("msku_already_claimed", 409,
                                  "该 msku 已被另一张尚未下单的计划占用"),
    "plan_rev_one_in_flight_idx": ("rev_in_flight", 409, "该计划已有一版在流转"),
    "plan_rev_pkey": ("rev_in_flight", 409, "同一版号已被并发提交占用"),
    "plan_line_event_transition_fk": ("illegal_transition", 422, "这条状态迁移不在白名单里"),
    "plan_months_1_24": ("bad_months", 400, "计划跨月数必须在 1~24 之间"),
    "plan_period_start_is_month_start": ("bad_period_start", 400, "起始月必须是月初"),
    "plan_owner_fk": ("unknown_actor", 400, "负责人不在 actor 表里"),
    "plan_demand_cell_claim_fk": ("msku_not_claimed", 409, "没认领就没有格子"),
    "plan_demand_cell_expected_nonneg": ("bad_units", 400, "计划销量不能为负"),
    "plan_purchase_cell_nonneg": ("bad_units", 400, "计划采购量不能为负"),
    "po_over_total": ("over_total", 409, "超过记录总量（并发组单）"),
    "po_seller_not_in_line": ("seller_not_in_line", 409, "来源店不在这条记录里"),
    "po_unplanned_reason_required": ("unplanned_reason_required", 400, "超出计划来源的数量必须写理由"),
    "po_line_below_sources": ("line_below_sources", 400, "明细数量少于来源合计"),
    "po_line_one_per_sku_wid": ("duplicate_line", 400, "同一张单里同货号同仓只能一行"),
    "po_frozen": ("po_frozen", 409, "采购单已下单，不可改"),
    "po_source_line_not_orderable": ("line_not_orderable", 409, "只有 已提交/已确认 的记录能组单"),
    "erp_call_one_in_flight_per_po": ("po_placing", 409, "下单请求进行中"),
    # ── 排货（016）：用例层先判、给出带数字的错误；这里是库层兜底（并发 / 绕过用例的写入）
    "consignment_dispatch_incomplete": ("dispatch_incomplete", 409, "排货单还有来源仓没有领星备货单"),
    "consignment_cancel_needs_void": ("ows_not_deleted", 409, "领星里还有未删除的备货单，先删再取消"),
    "ext_ref_idem_key_key": ("duplicate_submission", 409, "这张备货单已经记过（同一参考号）"),
    "erp_call_one_write_in_flight_per_consignment": ("submitting", 409, "这张排货单的提交请求进行中"),
    "sheet_needs_plan": ("missing_required", 400, "排货表必须对应一个销售计划"),
    "sheet_plan_fixed": ("illegal_transition", 422, "排货表的计划不可改"),
    "arrangement_line_not_in_plan": ("line_not_in_plan", 409, "这条记录不属于这张排货表的计划"),
    "arrangement_from_not_local": ("not_local_warehouse", 409, "来源仓必须是本地仓"),
    "arrangement_invalid_destination": ("invalid_destination", 409, "目的地不合法（海外仓须是海外仓；FBA 须是本店）"),
    # ★ CA-D14（022）：退回修改
    "sheet_reopen_after_submit": ("already_submitted", 409, "这张排货表已有单发到过领星（或正在发 / 待核实），不能退回修改，只能取消"),
    "sheet_reopen_needs_reason": ("reason_required", 400, "退回修改必须写理由"),
    "consignment_back_follows_sheet": ("illegal_transition", 422, "排货单只能跟随排货表退回修改"),
    # ★ CA-D13（021）：按去向分表
    "arrangement_route_mismatch": ("route_mismatch", 409, "这张排货表的去向不是这个目的地：平台仓表只排 FBA，海外仓表只排海外仓"),
    "sheet_route_missing": ("legacy_mixed", 409, "旧版混合表只能撤回与取消：按去向另开新表"),
    "sheet_needs_route": ("missing_required", 400, "排货表要选去向"),
    "sheet_route_fixed": ("route_fixed", 409, "排货表的去向建后不可改"),
    "sheet_one_draft_per_plan_route": ("draft_exists", 409, "这个计划在这个去向已有一张草稿"),
    "arrangement_line_not_dispatchable": ("line_not_dispatchable", 409, "记录已撤销 / 已完结，不能再排"),
    "arrangement_msku_not_this_sku": ("msku_not_this_sku", 409, "msku 对应的货号与格子货号不同"),
    "arrangement_seller_not_in_line": ("seller_not_in_line", 409, "店不在这条记录里"),
    "arrangement_sku_mismatch": ("msku_not_this_sku", 409, "格子货号与记录货号不同"),
    "arrangement_planned_cell": ("duplicate_cell", 409, "同一格子已存在（并发写入）"),
    "arrangement_unplanned_cell": ("duplicate_cell", 409, "同一格子已存在（并发写入）"),
    "dispatch_over_total": ("over_total", 409, "超过记录总量（并发安排）"),
    "sheet_frozen": ("sheet_frozen", 409, "排货表已确认 / 已取消，不可改"),
    "sheet_cancel_needs_reason": ("reason_required", 400, "取消排货表必须写理由"),
    "sheet_stale_state": ("stale_state", 409, "排货表状态已被别人改过，请刷新"),
    "consignment_stale_state": ("stale_state", 409, "排货单状态已被别人改过，请刷新"),
    "consignment_back_needs_dispatch": ("illegal_transition", 422, "已确认的排货单在投送前不能退回草稿（C-b 起）"),
    "consignment_confirm_follows_sheet": ("illegal_transition", 422, "排货单只能跟随排货表确认"),
    "sheet_state_via_event": ("illegal_transition", 422, "排货表状态只能经事件推进"),
    "consignment_state_via_event": ("illegal_transition", 422, "排货单状态只能经事件推进"),
    "sheet_born_as_draft": ("illegal_transition", 422, "排货表只能以草稿出生"),
    "consignment_born_as_draft": ("illegal_transition", 422, "排货单只能以草稿出生"),
    "sheet_birth_once": ("illegal_transition", 422, "排货表出生事件只能一次"),
    "consignment_birth_once": ("illegal_transition", 422, "排货单出生事件只能一次"),
    "sheet_no_delete": ("illegal_transition", 422, "排货表不删除，只取消"),
    "sheet_event_transition_fk": ("illegal_transition", 422, "这条状态迁移不在白名单里"),
    "consignment_event_transition_fk": ("illegal_transition", 422, "这条状态迁移不在白名单里"),
}


def translate(e: psycopg2.Error) -> ApiError | None:
    """认得出的库层拒绝翻成错误码；认不出的返回 None 让它以 500 冒出来。

    ★ 不许有 else 兜底成某个笼统的 409 —— 那会让一条没人预料到的约束
      看起来像一次正常的业务冲突。
    """
    f = pg_error_fields(e)
    hit = CONSTRAINT_ERRORS.get(f["constraint"] or "")
    if hit is None:
        log.warning("untranslated pg error %s", f)
        return None
    error, status, hint = hit
    return ApiError(status, error, hint, {"constraint": f["constraint"], "pg_detail": f["detail"]})


def lead_time_error(e: LeadTimeMisconfigured) -> ApiError:
    return ApiError(500, "lead_time_misconfigured",
                    "提前期配置不是「生产 / 货运各一条当前行」—— 库存推演算不了",
                    {"route": e.route, "current_rows": e.current_rows})
