"""xlsx 读写（openpyxl，内存里造工作簿）。"""
import io

import pytest
from openpyxl import Workbook

from rules.plan_import import COLUMNS, parse_rows
from usecase.plan_xlsx import NotXlsx, build_template, read_first_sheet


def test_not_xlsx():
    with pytest.raises(NotXlsx):
        read_first_sheet(b"not a zip")


def test_template_roundtrip():
    P = ["2026-11", "2026-12"]
    header, rows = read_first_sheet(build_template(P))
    assert tuple(header) == COLUMNS + tuple(P)
    assert len(rows) == 2
    r = parse_rows(header, rows, P)
    assert {e.code for e in r.errors} == {"bad_type"}
    assert sorted(e.row for e in r.errors) == [2, 3]


def test_reads_first_sheet_only_and_pads_rows():
    wb = Workbook()
    ws = wb.active
    ws.append(["a", "b", "c"])
    ws.append([1])
    wb.create_sheet("second").append(["x"])
    buf = io.BytesIO()
    wb.save(buf)
    header, rows = read_first_sheet(buf.getvalue())
    assert header == ["a", "b", "c"] and rows == [[1, None, None]]
