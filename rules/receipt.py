"""领星备货单进度（spec 2026-10-13 §2 · `04` §4.4）。★ 纯函数（L1）。

用领星自己的词：读的人一眼知道这是领星的进度，不是我们的。
★ 当前进度 = 旁路（非前进终态）取最新那条，否则取 rank 最大的那条 —— 读回倒退照写观察，但当前进度结构上不会倒退（`03` §5）。
"""
from __future__ import annotations

#: 领星文档 OverSeasStockDetail：10 待审批 · 30 待配货 · 40 待发货 · 50 待收货 · 60 已完成；20 / 51 见 `04` §4.4
_STAGES = {"10": ("待审核", 1), "30": ("待配货", 2), "40": ("待发货", 3), "50": ("待收货", 4), "60": ("已完成", 5),
           "20": ("已驳回", -1), "51": ("已撤销", -1)}
#: 详情答「不存在或已删除」：有人在领星直接删了
GONE = ("领星无此单", -1)
COMPLETE = "已完成"
_DRIFT = {"已驳回": "领星已驳回", "已撤销": "领星已撤销", "领星无此单": "领星里没有这张单"}


def stage_of(status) -> tuple[str, int] | None:
    """领星 status（字符串或整数）→ (领星进度, rank)；认不出的返回 None —— 不猜。"""
    return None if status is None else _STAGES.get(str(status).strip())


def current(obs: list[tuple]) -> tuple[str, int, object] | None:
    """obs: [(observed_at, stage, rank)] → (当前进度, rank, 最后观察时刻)。"""
    if not obs:
        return None
    last = max(o[0] for o in obs)
    bypass = [o for o in obs if o[2] < 0]
    if bypass:
        b = max(bypass, key=lambda o: o[0])
        return b[1], b[2], last
    top = max(obs, key=lambda o: (o[2], o[0]))
    return top[1], top[2], last


def all_complete(stages: list[str | None]) -> bool:
    """一张排货单的全部未作废领星单都已完成 → 可归档（`04` S4 #16）。空 = 否。"""
    return bool(stages) and all(s == COMPLETE for s in stages)


def drift(our_stage: str, lx_stage: str | None) -> str | None:
    """两轴不一致（`04` §4.5）：我方已投送，领星却走不下去了 —— 只点名，不改我方。"""
    return _DRIFT.get(lx_stage) if our_stage in ("已投送", "已归档") and lx_stage else None
