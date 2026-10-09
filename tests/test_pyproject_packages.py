"""★ 新增的包没进 pyproject 的 packages 清单：在仓库根目录跑时靠 cwd 也能 import（测试全绿），
换个目录起服务就 ModuleNotFoundError（2026-10-09 usecase / repo / erp.drivers 实际踩到）。"""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "tests", "web", "docs", "node_modules", ".superpowers"}


def test_every_product_package_is_listed():
    listed = set(tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["setuptools"]["packages"])
    on_disk = {".".join(p.parent.relative_to(ROOT).parts) for p in ROOT.rglob("__init__.py")
               if p.parent != ROOT and not SKIP & set(p.relative_to(ROOT).parts)}
    assert len(on_disk) > 8, f"只扫到 {sorted(on_disk)} —— 这条门禁是空转的"
    assert sorted(on_disk - listed) == [], "这些包没登记进 pyproject.toml [tool.setuptools] packages，离开仓库根目录起服务会找不到"
