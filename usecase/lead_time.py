"""提前期的读侧（spec §4.4 · 03 §41）：grid 与对外预测预览共用，所以不在 api/ 里。

★ 当前生效行 = valid_to IS NULL。写侧（PUT /lead-time）仍在 api/ui/lead_time.py。
"""
from __future__ import annotations

from dataclasses import dataclass

from forecast.calendar import ANCHOR_DAY

#: spec §4.4：货运 60 = 国际海运那一档。★ 预测只读这一条线路（OP-4）。
ROUTE = "oversea_sea"


@dataclass(frozen=True)
class LeadTime:
    route: str
    make_days: int
    ship_days: int
    anchor_day: int
    valid_from: dict[str, str]


class LeadTimeMisconfigured(Exception):
    """不是「生产 / 货运各一条当前行」。api 层翻成 500 lead_time_misconfigured。"""

    def __init__(self, route: str, current_rows: list[list]):
        self.route, self.current_rows = route, current_rows
        super().__init__(f"lead_time misconfigured route={route} current_rows={current_rows}")


def current_rows(cur, lock: bool = False) -> dict[str, tuple[int, object, bool]]:
    """当前生效行：{segment: (days, valid_from, valid_from 是不是今天)}。"""
    cur.execute("SELECT segment, days, valid_from, valid_from = CURRENT_DATE FROM lead_time"
                " WHERE route = %s AND valid_to IS NULL" + (" FOR UPDATE" if lock else ""),
                (ROUTE,))
    rows = cur.fetchall()
    got = {seg: (days, vf, today) for seg, days, vf, today in rows}
    # ★ 恰好 making / shipping 各一行；少一行或同一段两行都是配置坏了，不猜用哪行
    if sorted(seg for seg, *_ in rows) != ["making", "shipping"]:
        raise LeadTimeMisconfigured(ROUTE, [[s, d] for s, d, _, _ in rows])
    return got


def read_lead_time(cur) -> LeadTime:
    got = current_rows(cur)
    return LeadTime(ROUTE, got["making"][0], got["shipping"][0], ANCHOR_DAY,
                    {s: got[s][1].isoformat() for s in ("making", "shipping")})
