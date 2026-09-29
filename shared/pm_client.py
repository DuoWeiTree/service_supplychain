"""总公司 PM 产品系统客户端 —— Keycloak `client_credentials` 换 token +
`GET /api/integration/{categories,products}`。

★ 无新依赖：走 stdlib `urllib`，同兄弟仓 `model_inventory_forecast/v2/data/pm_catalog.py`
  的 I/O 段（只读参考，未 import、未复制）。

★ 免代理 opener：本机常设 `HTTP_PROXY` 会把内网/专线 IP 劫持掉，PM/Keycloak 的
  调用会以莫名其妙的连接失败或 503 收场——`shared/ch_client.py` 记着同一个坑，
  这里是第二份（三问日志同一条理由：解决方法不是调大超时，是不要走代理）。

★ 三问日志：打的谁（host/path）· 多久（elapsed_ms）· 怎么 failed 的（`kind` 区分
  timeout 与 connect_refused ——两者签名互斥、处置相反，见 CLAUDE.md 铁律）。

★ dim/ 不许 import 本模块（同 `shared/ch_client.py` 的分层规矩，
  `tests/test_layering.py` 会扫）——I/O 只能由 jobs/ 或 `dim/pm_source.py` 的
  纯变换以外的调用方（也就是 jobs/）注入。
"""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from shared.config import pm_zestrade

log = logging.getLogger("scm.pm")

#: ★ Global Constraints：必须小于网关上限，否则调用方拿到的是没有 body 的
#:   网关超时，不是带诊断的 502/日志。
TIMEOUT_S = 15

#: PM `/api/integration/products` 分页上限（照兄弟仓实测值）。调大不会让
#: 请求变快，只会越过 PM 那边真正的硬上限。
PRODUCTS_PAGE_SIZE = 100

_UA = "jxd-service-supplychain-pm-client/1.0 (+internal ops tool)"


def _opener() -> urllib.request.OpenerDirector:
    """免代理 + 每次现建（零 I/O，换来的是测试能直接 monkeypatch 这个函数本身）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _classify(exc: BaseException) -> str:
    """★ 只按标准库异常判，不按 urllib 包装类判——同 `shared/ch_client.py::_classify`
    的理由：包装类的继承关系可能是反的，按错的判据判会把「连不上」报成「超时」，
    于是有人去调大超时，而那是一行都不会生效的改动。

    `urllib.error.URLError.reason` 是真正的底层异常（`socket.timeout` /
    `ConnectionRefusedError` / `socket.gaierror` / 裸 `OSError`）；`HTTPError`
    本身是 `URLError` 子类但代表「连上了、服务端给了非 2xx」，必须先排除。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return "http_error"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return "timeout"
        if isinstance(reason, ConnectionRefusedError):
            return "connect_refused"
        if isinstance(reason, socket.gaierror):
            return "dns"
        if isinstance(reason, OSError):
            return "connect_failed"
        return "other"
    if isinstance(exc, TimeoutError):
        # ★ `urlopen(..., timeout=N)` 触发的超时有时不经 URLError 包装
        #   （取决于调用点），显式裸 TimeoutError 也要认得。
        return "timeout"
    return "other"


def _describe_failure(exc: BaseException) -> dict:
    kind = _classify(exc)
    code = getattr(exc, "code", None)  # HTTPError.code
    reason = getattr(exc, "reason", None)
    errno = getattr(reason, "errno", None) if reason is not None else None
    return {"kind": kind, "type": type(exc).__name__, "code": code,
            "errno": errno, "msg": str(exc)}


def _outbound(url: str, *, method: str = "GET", token: str | None = None,
              data: bytes | None = None, headers: dict[str, str] | None = None,
              timeout: int = TIMEOUT_S) -> tuple[bytes, str]:
    """打一次 PM/Keycloak，回答三问后把结果/异常交回去。失败不吞——
    这里只负责记日志，`raise` 把原始异常原样交给调用方分类/处理。

    → ``(body, content_type)``。★ 连 content-type 一起交出去——`mint_token`/
    `fetch_categories`/`fetch_products` 只要 JSON body 会把它丢在一边，但
    `fetch_image_bytes`（Task 2：图片代理）要透传 PM 响应头里的 content-type，
    不能凭空猜一个；两条调用路径共用同一段出站/日志/异常分类逻辑，不必
    另写一份 try/except。"""
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("user-agent", _UA)
    if token is not None:
        req.add_header("authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    parsed = urllib.parse.urlparse(url)
    host, path = parsed.netloc, parsed.path
    t0 = time.monotonic()
    try:
        with _opener().open(req, timeout=timeout) as resp:
            body = resp.read()
            content_type = resp.headers.get("content-type", "application/octet-stream")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        log.error("op=pm_outbound host=%s path=%s elapsed_ms=%d outcome=fail %s",
                  host, path, elapsed_ms, _describe_failure(e))
        raise
    elapsed_ms = (time.monotonic() - t0) * 1000
    log.info("op=pm_outbound host=%s path=%s elapsed_ms=%d outcome=ok bytes=%d",
             host, path, elapsed_ms, len(body))
    return body, content_type


def mint_token(cfg: dict | None = None) -> str:
    """client_credentials 换 access_token。

    ``cfg`` 为 `None`（默认）时读 `shared.config.pm_zestrade()`——★ 配置缺失/
    不全在那里炸（启动钩子式：点名段名 + 缺的字段），不等到发出下面这个
    HTTP 请求、更不等到 `KeyError` 深处才炸出来。
    """
    c = cfg if cfg is not None else pm_zestrade()
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
    }).encode()
    raw, _ct = _outbound(c["token_url"], method="POST", data=body,
                        headers={"content-type": "application/x-www-form-urlencoded"})
    return json.loads(raw)["access_token"]


def fetch_categories(token: str, cfg: dict | None = None) -> list[dict]:
    """`GET /api/integration/categories`——一次拉回整棵嵌套树。

    ★ 响应体顶层就是裸 JSON 数组（同兄弟仓实测记录），这个函数不做任何加工，
    扁平化/校验交给 `dim/pm_source.py::build_tree`。
    """
    c = cfg if cfg is not None else pm_zestrade()
    url = c["pm_base"].rstrip("/") + "/api/integration/categories"
    raw, _ct = _outbound(url, token=token)
    return json.loads(raw)


def fetch_products(token: str, cfg: dict | None = None) -> list[dict]:
    """`GET /api/integration/products`——按响应里的 `total`/`pageSize` 翻页。

    ★★ 翻页页数必须按**响应回报的** `pageSize` 算，不能按我们自己传的
    `PRODUCTS_PAGE_SIZE` 算——PM 不保证照我们要求的数走（同兄弟仓实测记录：
    请求 `page_size=100`，响应回 `"pageSize":20`）。终止条件双保险：
    `items` 为空，或 `page * 响应的 pageSize >= total`。
    """
    c = cfg if cfg is not None else pm_zestrade()
    base = c["pm_base"].rstrip("/") + "/api/integration/products"

    items: list[dict] = []
    page = 1
    while True:
        url = f"{base}?page={page}&page_size={PRODUCTS_PAGE_SIZE}"
        raw, _ct = _outbound(url, token=token)
        payload = json.loads(raw)
        page_items = payload["items"]
        items.extend(page_items)
        total = payload["total"]
        effective_page_size = payload.get("pageSize") or PRODUCTS_PAGE_SIZE
        if not page_items or page * effective_page_size >= total:
            break
        page += 1
    return items


def fetch_image_bytes(url: str, token: str, timeout: int = TIMEOUT_S) -> tuple[bytes, str]:
    """GET 任意 PM 资源 URL（Task 2：货号主图代理）。

    与 `fetch_categories`/`fetch_products` 不同：调用方要的是原始字节 +
    响应头里的 content-type（图片代理原样透传），不是解析好的 JSON——所以
    这里不 `json.loads`，直接把 `_outbound` 的两个返回值交出去。
    """
    return _outbound(url, token=token, timeout=timeout)
