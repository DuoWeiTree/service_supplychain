"""PM 品类镜像（`sku_category`）的纯变换层（Task 1）。

品类树扁平化 + 面包屑组装 · 货号→PM sku_code 的 base-aware 两段匹配 ·
占位图判定 · 把三者拼成 `sku_category` 的一批 upsert 行。

★ **纯函数，零 I/O**：不 import `urllib`/`psycopg2`/`clickhouse_connect` 任何
  客户端 —— `tests/test_layering.py` 按此扫 `dim/`。真正的网络请求在
  `shared/pm_client.py`，由 `jobs/refresh_dims.py` 把它们包成两个无参回调
  （`fetch_products`/`fetch_tree`）注入进 `fetch_sku_category`，与
  `dim/ch_source.py` 的 `Query` 注入是同一个约定，只是这里注入的是两个
  callable 而不是一个。

参考实现（只读，未 import、未复制）：兄弟仓
`model_inventory_forecast/v2/data/pm_catalog.py`——`build_tree`/
`index_products`/`build_base_index`/`match_one`/`_is_placeholder` 的判据
逐条照搬，字段名与落库形状按本仓 009 迁移重写。

## 与兄弟仓的差异

- 兄弟仓的 `classify()` 把「没有品类」拆成 junk/absent/ch_only 三桶，落进
  独立的 `pm_sku_unmapped` 表——那是给"联合查看按品类分组"用的完整功能。
  本仓这一轮（sku-image 计划）只要「有没有真图」，不需要那三桶：匹配不上
  PM 的货号直接计入 `drop_reasons["no_pm_match"]`，不单独落表。
- 落库列多两列 `main_image_url`/`image_is_placeholder`，provenance 两列
  `pm_sku_matched`/`match_path` 与兄弟仓同名同义。
- `category_id`/`category_path` 用 PM 品类树的 `category_code` + 根→叶面包
  屑（"/" 分隔），不是兄弟仓的 `category_name` 单层名字——03 §8 草案原文
  就是 `category_id`/`category_path` 这两个名字。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple


class Fetched(NamedTuple):
    rows: list[tuple]
    dropped: int
    drop_reasons: dict[str, int]
    source_max_captured: None  # PM 没有"采集日"这个概念，恒为 None（同 dim/ch_source.py 的形状）


class UnknownShape(Exception):
    """品类树认不出的形态（悬空 parent / 环 / level 声明与实际深度不符）。
    硬失败——不许把截断/错乱的树悄悄画成一棵好树（CLAUDE.md「未匹配的仓名
    必须硬失败」同一条纪律，换成了品类树）。"""


#: PM 占位图的文件名。★ 只按 basename 判，不按完整 URL —— PM 的图片域名/
#: 路径前缀将来可能变，但"这是那张占位图"这件事不会变；按 basename 匹配
#: 才不会在域名迁移那天悄悄失效（Global Constraints 明写）。
_PLACEHOLDER_BASENAMES = frozenset({"default.png"})

#: `sku_category` 落库的列序（不含 `refreshed_at`，那一列由
#: `jobs/refresh_dims.py::_upsert` 统一追加）。与 009 迁移的列名一一对应。
COLUMNS = ("sku", "category_id", "category_path", "pm_sku_matched",
           "match_path", "main_image_url", "image_is_placeholder")


def is_placeholder(url: str | None) -> bool:
    """PM 占位图判据：没有图，或图是 `default.png`——两者都不算「有真图」。"""
    if not url:
        return True
    basename = url.rsplit("/", 1)[-1]
    return basename in _PLACEHOLDER_BASENAMES


# ---------------------------------------------------------------- build_tree

def build_tree(raw_tree: list[dict]) -> list[dict]:
    """PM `/categories` 的嵌套树 → 扁平行（带根→叶面包屑 `path`）。

    每行：``{category_code, name, parent_category_code, level, is_leaf,
    sort_order, path}``。三条完整性校验，任一违反都硬失败：

    - ``parent_category_code`` 非空时必须在整棵树的 category_code 集合内
      （悬空 parent —— 树被截断或父节点丢失）
    - ``level`` 必须等于按 children 实际递归深度算出来的值
    - 无环（同一个 category_code 不能出现在自己的祖先链里）
    """
    all_codes: set[str] = set()

    def _collect(nodes: list[dict], path_ids: frozenset[int]) -> None:
        for node in nodes:
            node_id = id(node)
            if node_id in path_ids:
                raise UnknownShape(
                    f"品类树有环：{node.get('category_code')!r} 的子树里包含它自己")
            all_codes.add(node["category_code"])
            _collect(node.get("children") or [], path_ids | {node_id})

    _collect(raw_tree, frozenset())

    rows: list[dict] = []

    def _walk(nodes: list[dict], depth: int, ancestors: frozenset[str],
              ancestor_names: tuple[str, ...]) -> None:
        for node in nodes:
            code = node["category_code"]
            if code in ancestors:
                raise UnknownShape(f"品类树有环：{code!r} 出现在自己的祖先链里")
            parent = node.get("parent_category_code")
            if parent and parent not in all_codes:
                raise UnknownShape(
                    f"品类树悬空 parent：{code!r} 声明 parent_category_code="
                    f"{parent!r}，但该 code 不在整棵树里")
            declared_level = node.get("level")
            if declared_level != depth:
                raise UnknownShape(
                    f"品类树 level 不一致：{code!r} 声明 level={declared_level!r}，"
                    f"按 children 实测深度是 {depth}")
            name = node.get("category_name")
            path = "/".join([*ancestor_names, name or code])
            rows.append({
                "category_code": code, "name": name,
                "parent_category_code": parent, "level": declared_level,
                "is_leaf": node.get("is_leaf"), "sort_order": node.get("sort_order"),
                "path": path,
            })
            _walk(node.get("children") or [], depth + 1, ancestors | {code},
                  (*ancestor_names, name or code))

    _walk(raw_tree, 1, frozenset(), ())
    return rows


def path_by_code(tree_rows: list[dict]) -> dict[str, str]:
    """`build_tree()` 的输出 → `{category_code: 根→叶面包屑}`，供匹配阶段查表。"""
    return {r["category_code"]: r["path"] for r in tree_rows}


# ---------------------------------------------------------------- index_products

def index_products(items: list[dict]) -> dict[str, dict]:
    """PM `/products` 的 items → ``{sku_code: item}``。

    ★ ``sku_code`` 本不该重复（PM 那边的主键），重复就说明取数分页/去重出了
    问题——硬失败，不悄悄用后一条覆盖前一条（覆盖等于给一个货号随机丢了一份
    品类信息，且没有任何痕迹）。
    """
    by_sku: dict[str, dict] = {}
    for item in items:
        code = item["sku_code"]
        if code in by_sku:
            raise ValueError(f"PM sku_code 重复：{code!r}")
        by_sku[code] = item
    return by_sku


def _category_dict(item: dict) -> dict:
    return item.get("category") or {}


def _category_code(item: dict) -> str | None:
    return _category_dict(item).get("code")


def _category_name(item: dict) -> str | None:
    return _category_dict(item).get("name")


# ---------------------------------------------------------------- build_base_index

def build_base_index(by_sku: dict[str, dict]) -> tuple[dict[str, dict], set[str]]:
    """预建 ``{base_code: item}`` 供 `match_one` 的 base 兜底查表用。

    → ``(base_index, ambiguous_bases)``。``base = rsplit('-', 1)[0]``。

    ★ 撞键（两个不同 PM sku_code 算出同一个 base，且**品类 code 不同**）不
    raise——不管选哪个都是编的，这条原则不变，只是把"不许猜"从"硬失败"
    改成"从兜底表里剔除"，同时把这些 base 收进 `ambiguous_bases` 供调用方
    在日志里点名，不许静默（同兄弟仓 2026-09-14 部署订正的处置）。品类 code
    相同的撞键无害（保留先出现的那个）。★ 本仓按 **category code** 判同异，
    不按 name——code 是 PM 那边真正的主键，两个品类改名撞出同名不该被当成
    "无害的撞键"放行。
    """
    base_index: dict[str, dict] = {}
    base_category: dict[str, str | None] = {}
    ambiguous_bases: set[str] = set()
    for sku_code, item in by_sku.items():
        base = sku_code.rsplit("-", 1)[0]
        cat_code = _category_code(item)
        if base in ambiguous_bases:
            continue
        if base in base_index:
            if base_category[base] != cat_code:
                del base_index[base]
                del base_category[base]
                ambiguous_bases.add(base)
                continue
            continue
        base_index[base] = item
        base_category[base] = cat_code
    return base_index, ambiguous_bases


# ---------------------------------------------------------------- match_one

def match_one(sku: str, by_sku: dict[str, dict],
              base_index: dict[str, dict]) -> tuple[str | None, str | None, dict | None]:
    """→ ``(pm_sku_matched, match_path, item)``，三者要么全 None 要么全有值。

    顺序：先 exact（``sku`` 本身就是某个 PM ``sku_code``）→ ``'direct'``；
    未命中再拿 ``sku`` 的 base 查 `build_base_index` 预建好的表 → ``'base'``。
    base 按两式尝试：``rsplit('-', 1)[0]`` 优先，未命中再试
    ``split('-', 1)[0]``（两式在只有一个 '-' 的 sku 上结果相同，多个 '-'
    时才会分叉——两式都要试，只信一式会漏掉另一种货号命名习惯）。

    ★ 这里只读预建好的 `base_index`，不做任何判断或打分——撞键的处置已经
    在 `build_base_index` 里做完了，这个函数是纯确定性查表。
    """
    item = by_sku.get(sku)
    if item is not None:
        return item["sku_code"], "direct", item

    base_rsplit = sku.rsplit("-", 1)[0]
    item = base_index.get(base_rsplit)
    if item is None and "-" in sku:
        base_split = sku.split("-", 1)[0]
        if base_split != base_rsplit:
            item = base_index.get(base_split)
    if item is not None:
        return item["sku_code"], "base", item

    return None, None, None


# ---------------------------------------------------------------- fetch_sku_category

def _bump(reasons: dict[str, int], key: str) -> None:
    reasons[key] = reasons.get(key, 0) + 1


def fetch_sku_category(
    skus: list[str],
    fetch_products: Callable[[], list[dict]],
    fetch_tree: Callable[[], list[dict]],
) -> Fetched:
    """PM 品类镜像的编排入口：两次注入的 I/O + 上面几个纯变换的拼装。

    ``skus``：这一轮要不要给它一行的边界——只有已经在我们自己 `sku_catalog`
    里的货号才会被写进 `sku_category`（009 迁移的 `sku` 有外键约束，写一个
    不存在的 sku 会撞 FK）。这个宇宙必须由调用方（`jobs/refresh_dims.py`）
    从 PG 现查现传，不能在这一层猜——`dim/` 不许碰任何 DB 客户端。

    ``fetch_products``/``fetch_tree``：两个无参回调，真实实现是
    ``lambda: pm_client.fetch_products(token)`` 这类闭包，由调用方注入。
    这个函数本身不知道 token/config 是什么，只管拿到手的两份原始数据怎么变换。

    「没有品类」的成因不单独落表（见模块 docstring），但仍要在
    `drop_reasons` 里分得开，不许合并成一个数：

        no_pm_match       —— 两段匹配都没对上（含被 build_base_index 判定
                             ambiguous 因而从兜底表里剔除的那些）
        no_category        —— PM 对上了，但这个 PM 产品没有品类，或品类
                             code 不在品类树里（`orphan_category` 细分）
    """
    products = fetch_products()
    tree = fetch_tree()
    by_sku = index_products(products)
    base_index, ambiguous_bases = build_base_index(by_sku)
    paths = path_by_code(build_tree(tree))

    rows: list[tuple] = []
    reasons: dict[str, int] = {}

    for sku in skus:
        pm_sku, path, item = match_one(sku, by_sku, base_index)
        if pm_sku is None:
            _bump(reasons, "no_pm_match")
            continue

        cat_code = _category_code(item)
        if not cat_code:
            _bump(reasons, "no_category")
            continue

        cat_path = paths.get(cat_code)
        if cat_path is None:
            # ★「认不出的形态必须默认可见」：品类树里查不到这个 code 不该
            #   悄悄丢掉这个货号的图——用 PM 给的品类名/code 兜一个单层路径，
            #   同时计数，供刷新日志点名有多少条落在这个缝隙里。
            _bump(reasons, "orphan_category")
            cat_path = _category_name(item) or cat_code

        image_url = item.get("main_image_url")
        rows.append((sku, cat_code, cat_path, pm_sku, path,
                    image_url, is_placeholder(image_url)))

    if ambiguous_bases:
        reasons["ambiguous_base"] = len(ambiguous_bases)

    dropped = len(skus) - len(rows)
    return Fetched(rows, dropped, reasons, None)
