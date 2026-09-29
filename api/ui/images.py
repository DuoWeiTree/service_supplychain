"""货号主图代理（sku-image 计划 Task 2）：`GET /v1/skus/{sku}/image`。

不落库、不缓存到 PG——`sku_category.main_image_url` 只存 PM 那边的图片 URL，
真正的字节每次现取，图片本体只在**进程内存**里留一份短 TTL 缓存
（`IMAGE_CACHE_TTL_SECONDS`，照兄弟仓 `model_inventory_forecast/v2/serve/
api/categories.py` 的同名常量，600 秒）。

## ★★ 占位/无 URL/无该行必须在任何出站之前短路

三种「不用去 PM 取图」的情形，判据全部来自本仓 `sku_catalog`/`sku_category`
两张表本身（零出站就能回答），响应体的 `reason` 三者互不相同（计划 Global
Constraints「没有图的三种成因不许合并」）：

    sku_not_in_catalog  —— 本仓 sku_catalog 里没有这个货号
    no_pm_match         —— 有货号，但 sku_category 里没有它这一行
                            （两段匹配没对上，或对上了但没有可用品类——
                            这两种在 dim/pm_source.py 那一层已经不细分了，
                            这里跟着不细分）
    placeholder_only    —— sku_category 有这一行，但 image_is_placeholder=true
                            （PM 的 default.png）

★★ 这是最容易犯的坑：占位图本身是一张真实存在、能取回来的图，如果判断
顺序写反（先取图再判占位），代码看起来一样能工作，只是白白多打一次
PM/Keycloak，还会把"这个货号没有真图"渲染成一张假照片。
`test_placeholder_image_returns_404_without_any_outbound_call` 用
「被调用就 fail 测试」的桩钉住这个顺序（Task 2 Step 8 的变异证据：把
`_lookup` 挪到 `pm_client.fetch_image_bytes` 之后就会让这条测试转红）。

## 出站与缓存

否则 `pm_client.mint_token()` 换 token → `pm_client.fetch_image_bytes(url,
token)` 取字节 → 原样回传（`content-type` 透传自 PM 响应头，取不到时退回
`application/octet-stream`，同 `shared/pm_client.py::_outbound` 的默认）。

★ 两个函数都通过 `pm_client.xxx()` 模块属性调用，不在 import 时抄一份本地
名字——同 `jobs/refresh_dims.py::_fetch_pm_mirror` 的既有纪律，monkeypatch
打的是模块属性，`from`-import 会让测试的补丁打不中这里。

超时 15s（`_IMAGE_FETCH_TIMEOUT_S`，小于网关上限）。失败（超时/HTTP 错误/
连不上）不吞：按 CLAUDE.md 铁律记 host/path/耗时/cause，转 502，不返回一张
看起来正常的空图。

TTL 缓存 key=`sku`（不是 URL——同一个 sku 换绑到不同 PM 图时，`sku` 仍是调用
方认得的那个身份），命中不重新出站；★ 缓存只记成功——`fetch_image_bytes`
失败时不写缓存，下一次同一个 sku 还是会真的重试。并发重复出站两次是可接受
的重复工作，不是正确性问题，未命中/已过期的写路径不加锁。
"""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse

from fastapi import APIRouter, Request, Response

from api.ui.deps import declared
from api.ui.errors import ApiError
from shared import pm_client
from shared.pg_client import pg_conn, timed

log = logging.getLogger("scm.api")

#: ★★ 本路由**不挂** `require_fresh_mirrors`（负责人 2026-09-29 复核，照
#: `api/ui/ops.py` 的先例：那个端点也不挂，理由写在它模块 docstring 第一行）。
#: 依据是登记表自己的声明——`dim/registry.py` 里 `sku_category` 的
#: `staleness="label_only"`：「不进 v_mirror_freshness、不拒绝服务」（设计
#: A-1：品类/图片只影响展示，不影响任何一个数的正确性）。挂上闸会变成
#: `sku_catalog`（gate_503 的四张之一）一陈旧，看图就跟着 503——而一张看不到
#: 的图和计划里的数字对不对毫无关系，运营看到的是一个与真问题无关的红。
#: 门禁：`tests/test_api_sku_image.py` 里两条「镜像陈旧时这个端点仍然服务」。
router = APIRouter()

#: ★ 未经实测的占位（同兄弟仓 `IMAGE_CACHE_TTL_SECONDS`），先给一个数，
#: 能测出真实回源频率后再调。
IMAGE_CACHE_TTL_SECONDS = 600

#: 出站取图超时。★ 必须小于网关上限——超时发生在这里而不是网关那一侧，
#: 调用方才能拿到一个带 host/path/耗时的 502，而不是一个没有 body 的网关超时。
_IMAGE_FETCH_TIMEOUT_S = 15

#: sku → (写入时刻, 字节, content_type)。★ 模块级、进程内——不落 PG/Redis，
#: 多进程部署时每个进程各自一份缓存，是刻意的降级。
_IMAGE_CACHE: dict[str, tuple[float, bytes, str]] = {}


def _cache_get(sku: str) -> tuple[bytes, str] | None:
    entry = _IMAGE_CACHE.get(sku)
    if entry is None:
        return None
    written_at, content, content_type = entry
    if time.monotonic() - written_at > IMAGE_CACHE_TTL_SECONDS:
        return None
    return content, content_type


def _cache_put(sku: str, content: bytes, content_type: str) -> None:
    _IMAGE_CACHE[sku] = (time.monotonic(), content, content_type)


def _lookup(sku: str) -> tuple[bool, dict | None]:
    """查两张表，回答"要不要出站"。零出站。

    → ``(in_catalog, category_row | None)``。``category_row`` 为
    ``{"main_image_url", "image_is_placeholder"}`` 或 ``None``（没有
    `sku_category` 行）。
    """
    with timed("sku_image_lookup", sku=sku), pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM sku_catalog WHERE sku = %s", (sku,))
        in_catalog = cur.fetchone() is not None
        if not in_catalog:
            return False, None
        cur.execute("SELECT main_image_url, image_is_placeholder"
                    " FROM sku_category WHERE sku = %s", (sku,))
        row = cur.fetchone()
    if row is None:
        return True, None
    return True, {"main_image_url": row[0], "image_is_placeholder": row[1]}


def _fetch_and_cache(sku: str, url: str) -> tuple[bytes, str]:
    """mint token → 取 `url` 的字节。失败不吞，按 host/path/耗时/cause 转 502。

    ★ `pm_client.mint_token()`/`pm_client.fetch_image_bytes()` 都经模块属性
    调用（模块 docstring 已说明理由）。
    """
    parsed = urllib.parse.urlparse(url)
    host, path = parsed.netloc, parsed.path
    t0 = time.monotonic()
    try:
        token = pm_client.mint_token()
        content, content_type = pm_client.fetch_image_bytes(
            url, token, timeout=_IMAGE_FETCH_TIMEOUT_S)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        took = time.monotonic() - t0
        cause = pm_client._describe_failure(e)
        log.error("op=sku_image sku=%s host=%s path=%s elapsed_ms=%d outcome=fail %s",
                  sku, host, path, took * 1000, cause)
        raise ApiError(502, "image_fetch_failed", "PM 主图取图失败", {
            "sku": sku, "host": host, "path": path,
            "elapsed_ms": round(took * 1000), "cause": cause}) from e
    took = time.monotonic() - t0
    log.info("op=sku_image sku=%s host=%s path=%s elapsed_ms=%d outcome=ok",
             sku, host, path, took * 1000)
    _cache_put(sku, content, content_type)
    return content, content_type


@router.get("/skus/{sku}/image")
def sku_image(sku: str, request: Request):
    declared(request)  # ★ 未声明查询参数一律 400 —— 本仓全部端点的既有规矩，这条不例外
    cached = _cache_get(sku)
    if cached is not None:
        content, content_type = cached
        return Response(content=content, media_type=content_type)

    in_catalog, row = _lookup(sku)
    if not in_catalog:
        log.info("op=sku_image sku=%s outcome=no_row reason=sku_not_in_catalog"
                 " —— 不出站取图", sku)
        raise ApiError(404, "sku_not_in_catalog", "本仓没有这个货号",
                       {"reason": "sku_not_in_catalog", "sku": sku})
    if row is None:
        log.info("op=sku_image sku=%s outcome=no_row reason=no_pm_match"
                 " —— 不出站取图", sku)
        raise ApiError(404, "no_pm_match", "这个货号没有匹配上 PM 目录",
                       {"reason": "no_pm_match", "sku": sku})

    # ★★ 占位判断必须在出站之前——这两行的顺序就是本任务的核心断言
    #   （模块 docstring「Task 2」一节）。
    if row["image_is_placeholder"]:
        log.info("op=sku_image sku=%s outcome=placeholder reason=placeholder_only"
                 " —— 不出站取图", sku)
        raise ApiError(404, "placeholder_only", "PM 给的是占位图，不是真主图",
                       {"reason": "placeholder_only", "sku": sku})

    url = row["main_image_url"]
    if not url:
        # ★ 理论上不该出现（fetch_sku_category 写 main_image_url 为空时
        #   image_is_placeholder 恒为 true，见 dim/pm_source.py::is_placeholder）
        #   ——出现说明镜像那一层的不变式被破坏了，仍按 no_pm_match 处理，
        #   不许在这里裸 500。
        log.warning("op=sku_image sku=%s outcome=no_url reason=no_pm_match"
                    " —— sku_category 有行但 main_image_url 为空，镜像层的"
                    "不变式被破坏了，仍按 no_pm_match 处理", sku)
        raise ApiError(404, "no_pm_match", "这个货号没有匹配上 PM 目录",
                       {"reason": "no_pm_match", "sku": sku})

    content, content_type = _fetch_and_cache(sku, url)
    return Response(content=content, media_type=content_type)
