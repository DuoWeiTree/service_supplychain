"""错误形状与「库层拒绝 → 错误码」的翻译。

★ 形状按 team-lead 2026-09-22 裁定：{"error", "hint", …点名字段平铺在顶层}。
★ 翻译靠 constraint_name 而不是 str(e)：两种冲突的 message 都长得像一句话，
  真凶只在 pgcode / constraint_name 里。
"""
from __future__ import annotations

import logging

import psycopg2

from shared.pg_client import pg_error_fields

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
    "plan_demand_cell_expected_nonneg": ("bad_units", 400, "期望销量不能为负"),
    "plan_purchase_cell_nonneg": ("bad_units", 400, "计划采购量不能为负"),
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
