"""两段式出站（spec §6.2）：★ 能不能重试的分界是「请求发出去没有」，不是错误叫什么。

connect() 失败 → not_sent（连不上，领星什么都没收到，可重试）
request()/getresponse() 失败、或 5xx → unknown（可能已建单，必须查重，绝不自动重试）
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit


@dataclass
class CallResult:
    outcome: str
    host: str
    path: str
    elapsed_ms: int
    http_status: int | None = None
    lx_code: str | None = None
    lx_msg: str | None = None
    body: dict | None = None
    cause: str | None = None


#: 领星「授权 / 权限」类拒绝：改草稿无用，要管理员去领星开放平台处理。
#: 出处：erp_call 1·2（2026-10-09）createPurchaseOrder 回 HTTP 200 + code 403「授权失效，请更新授权有效期或检查权限」，
#: 同一凭据 access_token 与 purchaseOrderList 照常成功。★ 只登记实测见过的码，别的码没见过不猜。
AUTH_REJECT_CODES = frozenset({"403"})


def _lx_msg(j: dict) -> str | None:
    # 领星把真正的原因放在 error_details（message 只写「内部错误」）：只取 message 等于把原因丢掉
    msg = j.get("message") or j.get("msg")
    details = j.get("error_details")
    if details and str(details) != str(msg):
        return f"{msg}：{details}" if msg else str(details)
    return msg


def rejection_kind(outcome: str | None, lx_code) -> str | None:
    """rejected 才有：auth（授权/权限，改草稿无用）| business（领星按内容拒绝）。"""
    if outcome != "rejected":
        return None
    return "auth" if lx_code is not None and str(lx_code) in AUTH_REJECT_CODES else "business"


def cause_chain(exc: BaseException) -> str:
    """★ 不只取 str(e)：errno、对端地址都在链上（全局规范判据 1）。"""
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        errno = getattr(exc, "errno", None)
        parts.append(f"{type(exc).__name__}" + (f"[errno={errno}]" if errno else "") + f": {exc}")
        exc = exc.__cause__ or exc.__context__
    return " ← ".join(parts)


def proxy_from(cfg: dict) -> dict | None:
    if not cfg.get("proxy_host"):
        return None
    return {"host": cfg["proxy_host"], "port": int(cfg["proxy_port"]),
            "user": cfg.get("proxy_user", ""), "password": cfg.get("proxy_password", "")}


def _proxy_headers(proxy: dict) -> dict:
    if not proxy.get("user"):
        return {}
    token = base64.b64encode(f"{proxy['user']}:{proxy['password']}".encode()).decode()
    return {"Proxy-Authorization": f"Basic {token}"}


def host_safe_in_tests(host: str | None) -> bool:
    h = (host or "").strip("[]").rstrip(".").lower()
    if h == "localhost" or h.endswith(".invalid"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _refuse_outside_sandbox(host: str | None, proxy: dict | None) -> None:
    """★ 测试永远打不到真领星（PB-D14/D15：建单不可逆）。闸放在唯一出站口，
    而不是 driver / token 上 —— 后者挡不住新写的出站路径，也让真 driver 无法对本机桩做端到端测试。
    判据用 sys.modules 而非 PYTEST_CURRENT_TEST：后者在收集期与 session 夹具外不存在。"""
    if "pytest" not in sys.modules:
        return
    bad = [h for h in (host, proxy["host"] if proxy else "localhost") if not host_safe_in_tests(h)]
    if bad:
        raise RuntimeError(f"测试进程禁止出站到非本机 / 非 .invalid 主机：{bad}")


def post_json(base_url: str, path: str, query: dict, body: dict, timeout_s: float,
              user_agent: str, proxy: dict | None = None) -> CallResult:
    """★ 领星有 IP 白名单（tools/nodejs/commerce/lingxing_erp/INDEX.md:45）：live 必须带 proxy。
    经代理时走 CONNECT 隧道 —— 连代理、CONNECT 都在 connect() 里完成，失败仍归 not_sent。"""
    u = urlsplit(base_url)
    https = u.scheme == "https"
    host, port = u.hostname, u.port or (443 if https else 80)
    _refuse_outside_sandbox(host, proxy)
    conn_cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    if proxy:
        conn = conn_cls(proxy["host"], int(proxy["port"]), timeout=timeout_s)
        conn.set_tunnel(host, port, headers=_proxy_headers(proxy))
        where = f"{host}:{port} via proxy {proxy['host']}:{proxy['port']}"
    else:
        conn = conn_cls(host, port, timeout=timeout_s)
        where = f"{host}:{port}"
    t0 = time.perf_counter()

    def ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    try:
        conn.connect()
    except (OSError, http.client.HTTPException) as e:
        # 代理对 CONNECT 回非 HTTP 状态行会抛 BadStatusLine/LineTooLong（非 OSError）；仍在 connect() 内 = 没发出去
        conn.close()
        return CallResult("not_sent", host, path, ms(), cause=cause_chain(e) + f" @ {where}")
    try:
        conn.request("POST", f"{path}?{urlencode(query)}" if query else path,
                     body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                     headers={"Content-Type": "application/json", "User-Agent": user_agent})
        resp = conn.getresponse()
        raw = resp.read()
    except (OSError, http.client.HTTPException) as e:
        return CallResult("unknown", host, path, ms(), cause=cause_chain(e) + f" @ {where}")
    finally:
        conn.close()
    if resp.status >= 500:
        return CallResult("unknown", host, path, ms(), http_status=resp.status,
                          cause=f"http_{resp.status}: {raw[:200]!r}")
    # ★ 2xx/3xx 却读不出 code = 不知道领星认没认；不可逆调用上 rejected 会放行重提 → 一律 unknown。
    # 4xx 无 code 才是明确的拒绝。
    sent_ok = resp.status < 400
    try:
        j = json.loads(raw or b"{}")
        why = None if isinstance(j, dict) and j.get("code") is not None else \
            f"no_code: {raw[:200]!r}"
    except ValueError:
        j, why = None, f"non_json: {raw[:200]!r}"
    if why:
        return CallResult("unknown" if sent_ok else "rejected", host, path, ms(),
                          http_status=resp.status, cause=why)
    code = str(j["code"])
    return CallResult("ok" if code in ("0", "200") else "rejected", host, path, ms(),
                      http_status=resp.status, lx_code=code,
                      lx_msg=_lx_msg(j), body=j)
