"""计划单记录状态再评估（04 §2.2「终态先判、退回一次、前进到不动点」）。

★ 一处实现，三个调用者：组单 / 改删草稿 / 下单收尾（以及 M3 的回读）。
"""
from __future__ import annotations

import logging

from repo import purchase as repo
from rules.purchase import NON_TERMINAL_PURCHASE_STATES, target_state, transition_path

log = logging.getLogger("scm.usecase")


def reevaluate(cur, line_ids: list[int], actor: str, src: str) -> list[dict]:
    moved = []
    for lid, q in sorted(repo.line_qty(cur, line_ids).items()):
        if q["state"] not in NON_TERMINAL_PURCHASE_STATES:
            continue        # 准备排货及以后归阶段 C / M3；终态不动
        tgt = target_state(q["total"], q["confirmed"], q["ordered"])
        for frm, to in transition_path(q["state"], tgt):
            repo.insert_line_event(cur, lid, frm, to, actor, src)
            moved.append({"line_id": lid, "from": frm, "to": to})
    if moved:
        log.info("op=reevaluate_lines src=%s moved=%s", src, moved)
    return moved
