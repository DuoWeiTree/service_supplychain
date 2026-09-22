"""分层由测试强制（01 §3.1 L1~L7）。

★ 每条规则都要断言「扫到的文件不是 0 个」——
  扫 0 个文件的规则永远是绿的，而它看起来和真的守住了一模一样。
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 按 01 §7 的目录结构。api/ui 是子包，随 api 一起扫到。
PACKAGES = ["api", "dim", "erp", "forecast", "jobs", "migrations", "rules", "shared"]

DB_CLIENTS = {"psycopg2", "clickhouse_connect", "clickhouse_driver"}
HTTP_CLIENTS = {"httpx", "requests", "urllib3", "aiohttp"}


def sources(*rel: str) -> list[Path]:
    out: list[Path] = []
    for r in rel:
        out += sorted((ROOT / r).rglob("*.py"))
    assert out, f"{rel} 下一个 .py 都没扫到 —— 这条规则是空转的"
    return out


def imports(path: Path) -> set[str]:
    """只收绝对 import 的顶层模块名。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_no_relative_imports():
    """相对 import 会从上面那个 imports() 里漏出去 —— 漏出去的那条不会报错。"""
    bad = []
    for p in sources(*PACKAGES):
        for node in ast.walk(ast.parse(p.read_text("utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                bad.append(f"{p.relative_to(ROOT)}:{node.lineno}")
    assert not bad, f"相对 import 会绕过分层扫描：{bad}"


def test_l1_rules_is_pure():
    forbidden = DB_CLIENTS | HTTP_CLIENTS | {"api", "dim", "erp", "jobs", "fastapi", "starlette"}
    bad = {p.name: sorted(imports(p) & forbidden)
           for p in sources("rules") if imports(p) & forbidden}
    assert not bad, f"L1 违规（判据必须离线可测）：{bad}"


def test_l2_forecast_only_stdlib_and_rules():
    allowed = set(sys.stdlib_module_names) | {"rules"}
    bad = {p.name: sorted(imports(p) - allowed)
           for p in sources("forecast") if imports(p) - allowed}
    assert not bad, f"L2 违规（移植来的算法必须可独立回归）：{bad}"


def test_l3_dim_knows_nothing_about_api_or_erp():
    bad = {p.name: sorted(imports(p) & {"api", "erp", "jobs"})
           for p in sources("dim") if imports(p) & {"api", "erp", "jobs"}}
    assert not bad, f"L3 违规：{bad}"


def test_l4_l5_outbound_http_only_in_erp():
    others = [x for x in PACKAGES if x != "erp"]
    bad = {str(p.relative_to(ROOT)): sorted(imports(p) & HTTP_CLIENTS)
           for p in sources(*others) if imports(p) & HTTP_CLIENTS}
    assert not bad, f"L5 违规（出站写只能在 erp/）：{bad}"


def test_l6_psycopg2_only_where_transactions_live():
    """★ L6 的原文是「psycopg 连接只在 api/ 与 jobs/」。

    这里放宽到「连接工厂本身 + 事务边界所在层 + 迁移」：连接工厂必须有一个地方实现
    （shared/pg_client.py），把它一起禁掉等于禁掉整条链路。真正要防的是
    rules/ forecast/ dim/ erp/ 里出现连接 —— 那几层一旦连库就不再离线可测。
    """
    allowed = ("api/", "jobs/", "migrations/", "shared/pg_client.py")
    bad = [str(p.relative_to(ROOT)) for p in sources(*PACKAGES)
           if "psycopg2" in imports(p) and not str(p.relative_to(ROOT)).startswith(allowed)]
    assert not bad, f"L6 违规：{bad}"


def test_l7_no_writes_to_clickhouse():
    write = re.compile(r"(?i)\b(insert\s+into|alter\s+table|drop\s+table|create\s+table)\b")
    bad = [str(p.relative_to(ROOT)) for p in sources("dim") if write.search(p.read_text("utf-8"))]
    assert not bad, f"L7 违规（CH 只读）：{bad}"


def test_new_layer_dirs_bring_their_own_rules():
    """★ web/ 与 api/pub/ 一出现，L8~L10 就必须有人写。

    它们属「前端并入本仓」（00d Q 组）那份计划。这里不替它们写规则，
    只保证「新层悄悄出现而没有任何规则守着」这件事会红。
    """
    if (ROOT / "web").exists() or (ROOT / "api" / "pub").exists():
        assert (ROOT / "tests" / "test_layering_web.py").exists(), (
            "出现了 web/ 或 api/pub/，但 L8~L10 的测试还没写")


def test_every_declared_package_exists():
    missing = [p for p in PACKAGES if not (ROOT / p / "__init__.py").exists()]
    assert not missing, f"01 §7 声明了但不存在的包：{missing}"
