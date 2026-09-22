"""生效值：人填优先，没填就用系统预估，两者都没有就是未知。

★ S-15 的两半缺一不可：COALESCE 给数，basis 给出处。
  只给数，冻结之后「这 300 件是谁给的」永远答不出来（02 §3.1b / M-8）。
"""
from __future__ import annotations

from typing import NamedTuple


class Effective(NamedTuple):
    units: int | None
    basis: str      # human | system | unknown


def effective_demand(system_units: int | None, expected_units: int | None) -> Effective:
    if expected_units is not None:
        return Effective(expected_units, "human")      # ★ 0 也是人的表态
    if system_units is not None:
        return Effective(system_units, "system")
    return Effective(None, "unknown")
