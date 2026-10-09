"""唯一出站口（L5）。★ 不碰库：erp_call 由 usecase/ 在调用前后落（L6）。"""
from __future__ import annotations

import logging

from erp import endpoints
from erp.drivers.live import LiveDriver
from erp.transport import CallResult, cause_chain
from shared.config import erp as erp_config

log = logging.getLogger("scm.erp")
_SECRET_KEYS = {"access_token", "sign", "appSecret", "app_secret"}


def _driver():
    return LiveDriver(erp_config())


def redact(params: dict) -> dict:
    return {k: ("***" if k in _SECRET_KEYS else v) for k, v in params.items()}


def call(name: str, params: dict) -> tuple[endpoints.Endpoint, CallResult]:
    ep = endpoints.get(name)
    try:
        r = _driver().call(ep, params)
    except Exception as e:
        # 驱动不该抛；抛了也不许无声（可能已发出请求，结果未知）
        log.warning("erp op=%s path=%s idem=%s outcome=exception cause=%s", name, ep.path,
                    params.get(ep.idem_param) if ep.idem_param else None, cause_chain(e))
        raise
    log.log(logging.INFO if r.outcome == "ok" else logging.WARNING,
            "erp op=%s host=%s path=%s idem=%s ms=%s outcome=%s http=%s lx_code=%s lx_msg=%s cause=%s",
            name, r.host, r.path, params.get(ep.idem_param) if ep.idem_param else None,
            r.elapsed_ms, r.outcome, r.http_status, r.lx_code, r.lx_msg, r.cause)
    return ep, r
