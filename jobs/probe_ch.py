"""只读探针：把四张源表的真实列名与量级打出来。

★ 存在的理由：设计 §7 的 SQL 草案全部标着「未实测」—— 本仓文档 17:84 / 17:202
  只给了表名与行数，一个列名都没写。先探再写，别对着猜出来的列名写 SQL。
用法：uv run python -m jobs.probe_ch
"""
from __future__ import annotations

import sys

from dim.registry import MIRRORS
from shared.ch_client import ch_client, ch_query, describe_failure

TABLES = [m.source for m in MIRRORS
          if m.source and m.source.startswith("jxd_raw.") and not m.pending]


def main() -> int:
    try:
        q = ch_query(ch_client())
    except Exception as e:  # noqa: BLE001 - 建连失败的类型不可预知，describe_failure 兜底分类
        print(f"连不上 CH：{describe_failure(e)}", file=sys.stderr)
        return 2
    for table in TABLES:
        db, name = table.split(".", 1)
        print(f"\n=== {table} ===")
        for col, typ in q(f"SELECT name, type FROM system.columns"
                          f" WHERE database = '{db}' AND table = '{name}' ORDER BY position"):
            print(f"  {col:40s} {typ}")
        for rows, days, lo, hi in q(
                f"SELECT count(), uniq(_captured_date), min(_captured_date),"
                f" max(_captured_date) FROM {table}"):
            print(f"  -- {rows} 行 / {days} 个采集日 / {lo} ~ {hi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
