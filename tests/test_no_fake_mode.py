"""PB-D14：产品代码只有一条真路径。领星替身只许住在 tests/。

★ 允许清单为空：任何一处命中都是 fake 模式在长回来。
"""
import re
from pathlib import Path

from tests.test_layering import PACKAGES

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"fake|simulat|placed_mode|'sim'", re.IGNORECASE)


def _product_sources() -> list[Path]:
    out = [p for pkg in PACKAGES for ext in ("*.py", "*.sql") for p in sorted((ROOT / pkg).rglob(ext))]
    assert len(out) > 50, f"只扫到 {len(out)} 个文件 —— 这条门禁是空转的"
    return out


def test_product_code_has_no_fake_mode():
    hits = [f"{p.relative_to(ROOT)}:{n}: {line.strip()}"
            for p in _product_sources()
            for n, line in enumerate(p.read_text("utf-8").splitlines(), start=1)
            if FORBIDDEN.search(line)]
    assert hits == [], "产品代码里出现 fake / 模拟 / placed_mode / 'sim'：\n" + "\n".join(hits)


def test_simulate_routes_are_gone(client):
    paths = set(client.app.openapi()["paths"])
    assert len(paths) > 20, "路由表读空了 —— 这条断言是空转的"
    assert not [p for p in paths if "simulate" in p]
    assert client.post("/v1/simulate/erp/next", json={}).status_code == 404
