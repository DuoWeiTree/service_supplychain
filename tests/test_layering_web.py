"""L8~L10（01 §3.1）：前端并入本仓（Q 组）新增的三条边界。

★ 每条规则都要断言「扫到的文件不是 0 个」——扫 0 个文件的规则永远是绿的，
  而它看起来和真的守住了一模一样（同 tests/test_layering.py 的原则）。

`test_layering.py::test_new_layer_dirs_bring_their_own_rules` 只保证「新层
悄悄出现而没有任何规则守着」这件事会红，本文件就是它点名要求补上的那份规则。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from api import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "web" / "src"


def web_sources() -> list[Path]:
    """`web/src/**` 的全部文件 + `web/` 顶层的 `*.ts`（01 §3.1 L8 划定的扫描范围）。"""
    out = [p for p in WEB_SRC.rglob("*") if p.is_file()] if WEB_SRC.exists() else []
    out += sorted((ROOT / "web").glob("*.ts"))
    assert out, "web/src/** 与 web/*.ts 一个文件都没扫到 —— 这条规则是空转的"
    return sorted(out)


# ---------------------------------------------------------------------------
# L8 · web/ 里不许出现 PG / CH / 领星的客户端或连接串
# ---------------------------------------------------------------------------

#: 大小写不敏感 —— `Postgres://` 与 `postgres://` 一样是连接串
L8_FORBIDDEN = ("psycopg", "clickhouse", "192.168.66.", "postgres://",
               "postgresql://", "lingxing", "app_secret")


def scan_l8(text: str) -> list[str]:
    """★ 独立成函数：既给 test_l8_* 用，也给下面的证伪测试用合成字符串验证——
    不用为了「证明这条规则真会报」在仓库里留一份临时违规文件。"""
    low = text.lower()
    return [tok for tok in L8_FORBIDDEN if tok.lower() in low]


def test_l8_web_never_touches_a_database_directly():
    bad = {}
    for p in web_sources():
        hits = scan_l8(p.read_text("utf-8"))
        if hits:
            bad[str(p.relative_to(ROOT))] = hits
    assert not bad, f"L8 违规（前端直连数据库 = 事务边界与网关全部失效）：{bad}"


def test_l8_scanner_actually_catches_a_violation():
    """★ 证伪：合成含连接串/驱动名的文本，确认 scan_l8 真的会报，不是摆设。"""
    assert scan_l8("const dsn = 'postgresql://u:p@192.168.66.210/jxd_ml'")
    assert scan_l8("import psycopg2")
    assert scan_l8("clickhouse_connect.get_client()")
    assert scan_l8("const key = LINGXING_APP_SECRET")
    assert not scan_l8("const base = import.meta.env.VITE_API_BASE ?? '/v1'")


# ---------------------------------------------------------------------------
# L9 · web/ 只打 api/ui/ 暴露的路径 —— 允许集从活的 FastAPI app 路由推导，不手写
# ---------------------------------------------------------------------------

_PARAM = re.compile(r"\{[^}]*\}")
#: `${qs(...)}` 是查询串的生成器，不是路径的一部分 —— 它恒在模板末尾，先掐掉。
_QS_TAIL = re.compile(r"\$\{qs\(.*\)\}$")
_INTERP = re.compile(r"\$\{[^}]*\}")
#: `call<T>('METHOD', <path>, ...)`；泛型实参里可能嵌套更多 `<...>`
#: （如 `Awaited<ReturnType<SupplyChainApi['putPurchase']>>`），所以用非贪婪
#: `.*?` 一路扩展到「后面紧跟 `(`」的那个 `>` 为止，不能用 `[^>]*`（撞到第一层
#: 嵌套的 `>` 就会提前截断，导致这一整条 call() 从扫描结果里静默消失）。
_CALL = re.compile(
    r"\bcall(?:<.*?>)?\(\s*'(?P<method>[A-Z]+)'\s*,\s*"
    r"(?:`(?P<tmpl>[^`]*)`|'(?P<lit>[^']*)')"
)
#: 防的是绕开 http.ts 的 call()、在别处另起炉灶手写 `/v1/...` 绝对路径。
#: 引号必须紧贴 `/v1/` ——排除像 `'GET /v1/plans/1/grid'`
#: 这种日志断言里嵌了一段路径子串的情形，那不是「前端在拼路径」。
_LITERAL_V1 = re.compile(r"[`'\"](?P<path>/v1/[^`'\"?]*)")

HTTP_TS = ROOT / "web" / "src" / "api" / "http.ts"


def normalize_backend_path(path: str) -> str:
    """`{plan_id}` 这类参数名归一成 `{param}`——前端拼路径用的变量名跟后端的
    参数名并不总一样（`sellerSku` vs `seller_sku`），要比的是路径结构。"""
    return _PARAM.sub("{param}", path)


def normalize_frontend_template(src: str) -> str:
    """把 http.ts 里传给 `call()` 的模板字符串归一成同一种 `{param}` 记法。"""
    src = _QS_TAIL.sub("", src)
    return _INTERP.sub("{param}", src)


def path_matches_route(candidate: str, route: str) -> bool:
    """`candidate`（已按 `/` 分段的具体或已归一路径）是否落在 `route`
    （後端路由，段可能是 `{param}`）的形状里 —— 段数必须相等，`{param}` 段
    吸收对面任何一个段，其余段要求逐字相同。"""
    c = candidate.strip("/").split("/")
    r = route.strip("/").split("/")
    return len(c) == len(r) and all(rs == "{param}" or rs == cs for cs, rs in zip(c, r))


def allowed_routes() -> set[tuple[str, str]]:
    """★ 从活的 FastAPI app 推导，不手写清单——手写的清单会跟真实路由分叉，
    而「界面打的路径与后端实际暴露的对不上」正是 L9 要防的事本身。"""
    spec = create_app().openapi()
    return {(method.upper(), normalize_backend_path(path))
            for path, methods in spec["paths"].items() for method in methods}


def frontend_call_paths(path: Path) -> list[tuple[str, str]]:
    """http.ts 里 `call(method, path, …)` 的 (方法, 归一化后的相对路径) 列表。"""
    text = path.read_text("utf-8")
    out = []
    for m in _CALL.finditer(text):
        raw = m.group("tmpl") if m.group("tmpl") is not None else m.group("lit")
        out.append((m.group("method"), normalize_frontend_template(raw)))
    return out


def frontend_literal_v1_paths(path: Path) -> list[str]:
    text = path.read_text("utf-8")
    return [normalize_backend_path(_INTERP.sub("{param}", m.group("path")))
            for m in _LITERAL_V1.finditer(text)]


def test_l9_web_only_calls_paths_the_backend_actually_exposes():
    assert HTTP_TS.exists(), "http.ts 不存在，这条规则扫的是空气"
    allowed = allowed_routes()
    assert allowed, "从活 app 推出的允许集是空的 —— 后端路由没挂上，这条规则测不出东西"

    calls = frontend_call_paths(HTTP_TS)
    assert calls, "http.ts 里一个 call() 都没扫到 —— 这条规则是空转的"
    bad = [(method, "/v1" + rel) for method, rel in calls
           if not any(method == am and path_matches_route("/v1" + rel, aroute)
                      for am, aroute in allowed)]
    assert not bad, f"L9 违规：http.ts 打了后端没暴露的路径：{bad}\n允许集={sorted(allowed)}"

    # ★ 除 http.ts 自己拼接的相对路径外，web/src/** 里任何硬写的 /v1/... 绝对路径
    #   都要单独抓一遍 —— 那种写法绕开了 http.ts 唯一的出口，会让契约被界面改动
    #   牵着走而没人看得见（01 §3.1 L9 的原话）。
    literal_bad: dict[str, list[str]] = {}
    for p in web_sources():
        misses = [f for f in frontend_literal_v1_paths(p)
                  if not any(path_matches_route(f, aroute) for _, aroute in allowed)]
        if misses:
            literal_bad[str(p.relative_to(ROOT))] = misses
    assert not literal_bad, f"L9 违规：硬写的 /v1/... 路径不在后端路由里：{literal_bad}"


def test_l9_no_frontend_path_reaches_outside_v1_or_into_pub():
    for _, rel in frontend_call_paths(HTTP_TS):
        full = "/v1" + rel
        assert full == "/v1" or full.startswith("/v1/"), f"路径跑到 /v1 之外：{full}"
        assert not full.startswith("/v1/pub") and not full.startswith("/pub"), \
            f"打到了 api/pub 的地界：{full}"


def test_l9_allowlist_derivation_actually_rejects_a_bad_path():
    """★ 证伪：造一条真实路由集里没有的路径，确认判据真的会红，不是永远为真
    （不用为了这条证据在仓库里留一份打了假路径的临时代码）。"""
    allowed = allowed_routes()
    assert not any(m == "GET" and path_matches_route("/v1/plans/{param}/does-not-exist", r)
                  for m, r in allowed)
    assert not any(path_matches_route("/v1/pub/anything", r) for _, r in allowed)
    assert normalize_frontend_template("/plans/${planId}/grid") == "/plans/{param}/grid"
    assert normalize_frontend_template("/plans${qs({ a: 1 })}") == "/plans"


# ---------------------------------------------------------------------------
# L10 · api/pub/ 不许 import api/ui/ —— 目录还不存在，先让它空转，一出现立刻生效
# ---------------------------------------------------------------------------

def _is_api_ui(module: str) -> bool:
    return module == "api.ui" or module.startswith("api.ui.")


def _node_imports_api_ui(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return bool(node.module) and _is_api_ui(node.module)
    if isinstance(node, ast.Import):
        return any(_is_api_ui(a.name) for a in node.names)
    return False


def _imports_api_ui(path: Path) -> list[int]:
    tree = ast.parse(path.read_text("utf-8"))
    return [node.lineno for node in ast.walk(tree) if _node_imports_api_ui(node)]


def test_l10_api_pub_must_not_import_api_ui():
    pub = ROOT / "api" / "pub"
    if not pub.exists():
        return   # ★ 阶段 A 还没有这个目录；目录一出现，下面这段扫描立刻生效
    bad = {}
    for p in sorted(pub.rglob("*.py")):
        lines = _imports_api_ui(p)
        if lines:
            bad[str(p.relative_to(ROOT))] = lines
    assert not bad, f"L10 违规（api/pub 是给别人的承诺，不许依赖随界面变的 api/ui）：{bad}"


def test_l10_scanner_actually_catches_a_violation(tmp_path):
    """★ 证伪：在 pytest 的 tmp_path（跑完自动清理，不留在仓库里）造一份
    `from api.ui import x`，证明扫描逻辑真的会抓到，不是摆设。"""
    bad_file = tmp_path / "leak.py"
    bad_file.write_text("from api.ui import plans\n", encoding="utf-8")
    assert _imports_api_ui(bad_file) == [1]

    ok_file = tmp_path / "clean.py"
    ok_file.write_text("from api import create_app\n", encoding="utf-8")
    assert _imports_api_ui(ok_file) == []


# ---------------------------------------------------------------------------
# 判据⑥ 的 Python 侧：grid fixture 双侧字节同源
# ---------------------------------------------------------------------------

def test_grid_fixture_byte_identical_on_both_sides():
    """★ 前端 web/src/api/fixtures/grid-1.json 是 tests/fixtures/grid_response.json
    的原样拷贝（Task 2 报告已记）——这里补 Python 侧的守卫：任一侧单独重新生成，
    这条测试必须红，不能只靠前端 fixtures.test.ts 单方面守着。"""
    backend = ROOT / "tests" / "fixtures" / "grid_response.json"
    frontend = WEB_SRC / "api" / "fixtures" / "grid-1.json"
    assert backend.exists() and frontend.exists()
    assert backend.read_bytes() == frontend.read_bytes(), (
        "grid fixture 前后端字节不同源 —— 前端 mock 全绿、切 api 才发现字段不一样")
