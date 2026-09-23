"""实测：CH 可达时探针必须真的读到四张源表的列。

★ 只在 CH 不可达时跳过，且跳过原因必须点名 host/port/cause —— 不许静默跳过，
否则「CH 抖了一下」和「探针本来就没测到东西」长得一模一样。
"""
from __future__ import annotations

import pytest

from dim.registry import MIRRORS
from shared.ch_client import ch_client, ch_query, describe_failure
from shared.config import clickhouse

#: 与 jobs/probe_ch.py 的 TABLES 同一条筛选逻辑 —— 阶段 A 四张非 pending 的 CH 源。
TABLES = [m.source for m in MIRRORS
          if m.source and m.source.startswith("jxd_raw.") and not m.pending]


@pytest.fixture(scope="module")
def q():
    c = clickhouse()
    where = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"
    try:
        return ch_query(ch_client())
    except Exception as e:  # noqa: BLE001 - 建连失败的类型不可预知，跳过前先分类记下来
        pytest.skip(f"CH 不可达 target={where} cause={describe_failure(e)} —— "
                     f"跳过而非静默：本条从不因为「没测到」而绿")


def test_registry_lists_the_four_stage_a_sources():
    """★ 扫 0 张表的探针测试永远是绿的 —— 先证明筛选逻辑真的选中了东西。"""
    assert len(TABLES) == 4, f"阶段 A 非 pending 的 CH 源应为 4 张，实得 {TABLES}"


def test_each_source_table_has_at_least_one_column(q):
    for table in TABLES:
        db, name = table.split(".", 1)
        rows = q(f"SELECT count() FROM system.columns"
                 f" WHERE database = '{db}' AND table = '{name}'")
        assert rows and rows[0][0] >= 1, f"{table} 一个列都没探到"
