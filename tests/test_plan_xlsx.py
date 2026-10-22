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


def test_broken_package_is_not_xlsx_with_cause_chained():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<broken")
    with pytest.raises(NotXlsx) as ei:
        read_first_sheet(buf.getvalue())
    assert ei.value.__cause__ is not None
    assert type(ei.value.__cause__).__name__ in str(ei.value)


def test_corrupt_sheet_xml_during_iteration_is_not_xlsx():
    import zipfile

    src = io.BytesIO()
    wb = Workbook()
    wb.active.append(["a"] * 3)
    wb.save(src)
    out = io.BytesIO()
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(out, "w") as zo:
        for n in zi.namelist():
            d = zi.read(n)
            zo.writestr(n, b"<worksheet><sheetData><row>" if n == "xl/worksheets/sheet1.xml" else d)
    with pytest.raises(NotXlsx):
        read_first_sheet(out.getvalue())
