"""016 里每一个 `RAISE … CONSTRAINT = 'x'` 都要能翻成错误码 —— 认不出的约束会以 500 冒出来。"""
import re
from pathlib import Path

from api.common.errors import CONSTRAINT_ERRORS

ROOT = Path(__file__).resolve().parents[1]


def test_every_016_constraint_is_translated():
    sql = "".join((ROOT / f"migrations/pg/{n}").read_text("utf-8")
                  for n in ("016_dispatch.sql", "018_dispatch_by_plan.sql", "020_dispatch_submit.sql"))
    names = set(re.findall(r"CONSTRAINT = '([a-z_]+)'", sql))
    assert len(names) >= 15, "解析出的约束名太少 —— 正则坏了"
    assert sorted(names - set(CONSTRAINT_ERRORS)) == []
