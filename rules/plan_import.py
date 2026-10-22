"""销售计划 Excel 导入 · 格式规则（spec §2）。纯判断：吃已读出的单元格，不碰 IO。"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime

COLUMNS = ("类型", "货号", "店铺ID", "店铺", "国家", "msku")
#: 这几列表里必须出现；店铺/国家是「可填」，缺列等于没填。
REQUIRED_COLUMNS = ("类型", "货号", "店铺ID", "msku")
MAX_ROWS = 2000  # 设计取值 · 未实测（spec §2）
T_DEMAND, T_PURCHASE = "销量", "采购"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class RowError:
    row: int
    col: str | None
    code: str
    message: str
    seller_sku: str | None = None
    sid: str | None = None
    sku: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DemandRow:
    row: int
    seller_sku: str
    sid: str
    sku: str | None
    seller_name: str | None
    market: str | None
    units: dict[str, int | None]


@dataclass(frozen=True)
class PurchaseRow:
    row: int
    sku: str
    units: dict[str, int | None]


@dataclass(frozen=True)
class Parsed:
    demand: list[DemandRow]
    purchase: list[PurchaseRow]
    errors: list[RowError]


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # Excel 把纯数字店铺ID 存成 1234.0
    s = str(v).strip()
    return s or None


def _head(v) -> str | None:
    if isinstance(v, (datetime, date)):
        return f"{v.year:04d}-{v.month:02d}"
    return _text(v)


def _units(v) -> tuple[bool, int | None]:
    """(合法, 值)。空 → None，不是 0。"""
    if v is None or (isinstance(v, str) and not v.strip()):
        return True, None
    if isinstance(v, bool):
        return False, None
    if isinstance(v, int):
        return (v >= 0), v
    if isinstance(v, float) and v.is_integer() and v >= 0:
        return True, int(v)
    return False, None


def parse_rows(header: list[object], rows: list[list[object]], periods: list[str]) -> Parsed:
    errs: list[RowError] = []
    names = [_head(h) for h in header]
    pos: dict[str, int] = {}
    months: dict[str, int] = {}
    for i, n in enumerate(names):
        if n is None:
            continue
        tgt = months if _MONTH.match(n) else pos
        if n in tgt:
            errs.append(RowError(1, col_letter(i), "bad_header", f"表头「{n}」重复"))
        elif tgt is pos and n not in COLUMNS:
            errs.append(RowError(1, col_letter(i), "bad_header", f"表头「{n}」不认识"))
        else:
            tgt[n] = i
    for n in REQUIRED_COLUMNS:
        if n not in pos:
            errs.append(RowError(1, None, "bad_header", f"缺少列「{n}」"))
    for p in periods:
        if p not in months:
            errs.append(RowError(1, None, "missing_month", f"缺少月份列 {p}", extra={"month": p}))
    for m, i in months.items():
        if m not in periods:
            errs.append(RowError(1, col_letter(i), "extra_month", f"多出月份列 {m}，计划不含该月",
                                 extra={"month": m}))
    if any(e.code == "bad_header" for e in errs):
        return Parsed([], [], errs)  # 列都认不全，逐行报只会是噪音

    if len(rows) > MAX_ROWS:
        return Parsed([], [], errs + [RowError(
            1, None, "file_too_large", f"超过 {MAX_ROWS} 行上限（{len(rows)} 行）")])

    def cell(r, name):
        i = pos.get(name)
        return r[i] if i is not None and i < len(r) else None

    demand: list[DemandRow] = []
    purchase: list[PurchaseRow] = []
    seen = 0
    for k, r in enumerate(rows):
        n = k + 2
        if all(_text(c) is None for c in r):
            continue
        seen += 1
        typ = _text(cell(r, "类型"))
        sku, msku, sid = _text(cell(r, "货号")), _text(cell(r, "msku")), _text(cell(r, "店铺ID"))
        L = lambda name: col_letter(pos[name])
        ctx = {"seller_sku": msku, "sid": sid, "sku": sku}
        if typ not in (T_DEMAND, T_PURCHASE):
            errs.append(RowError(n, L("类型"), "bad_type",
                                 f"类型须为「销量」或「采购」，实为「{typ or '空'}」", **ctx))
            continue
        ok = True
        units: dict[str, int | None] = {}
        for m in periods:
            if m not in months:
                continue
            good, val = _units(r[months[m]] if months[m] < len(r) else None)
            if not good:
                ok = False
                errs.append(RowError(n, col_letter(months[m]), "bad_units",
                                     f"{m} 须为空或 ≥0 的整数", **ctx))
            units[m] = val
        need = ("msku", "店铺ID") if typ == T_DEMAND else ("货号",)
        vals = {"msku": msku, "店铺ID": sid, "货号": sku}
        for f in need:
            if vals[f] is None:
                ok = False
                errs.append(RowError(n, L(f), "missing_field", f"「{f}」必填", **ctx))
        if not ok:
            continue
        if typ == T_DEMAND:
            demand.append(DemandRow(n, msku, sid, sku, _text(cell(r, "店铺")),
                                    _text(cell(r, "国家")), units))
        else:
            purchase.append(PurchaseRow(n, sku, units))

    if seen == 0:
        errs.append(RowError(1, None, "empty_file", "表里没有数据行"))

    def dups(items, key, what, ctxf):
        by: dict = {}
        for x in items:
            by.setdefault(key(x), []).append(x.row)
        for kk, rs in by.items():
            if len(rs) > 1:
                for x in items:
                    if key(x) == kk:
                        errs.append(RowError(x.row, None, "duplicate_row",
                                             f"{what}重复，出现在第 {', '.join(map(str, rs))} 行",
                                             extra={"rows": rs}, **ctxf(x)))

    dups(demand, lambda x: (x.seller_sku, x.sid), "msku + 店铺ID",
         lambda x: {"seller_sku": x.seller_sku, "sid": x.sid, "sku": x.sku})
    dups(purchase, lambda x: x.sku, "采购货号", lambda x: {"sku": x.sku})

    claimed = {d.sku for d in demand if d.sku}
    for p in purchase:
        if p.sku not in claimed:
            errs.append(RowError(p.row, col_letter(pos["货号"]), "purchase_sku_not_claimed",
                                 "采购货号不在任何销量行的货号里", sku=p.sku))
    errs.sort(key=lambda e: (e.row, e.col or ""))
    return Parsed(demand, purchase, errs)
