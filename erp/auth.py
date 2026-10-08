"""领星 access_token（移植 lingxing-client.ts authenticate；过期前 60 s 重取）。"""
from __future__ import annotations

import logging
import threading
import time

from erp.transport import CallResult, post_json, proxy_from

log = logging.getLogger("scm.erp")
_lock = threading.Lock()
_cache: dict = {}


class AuthFailed(Exception):
    def __init__(self, result: CallResult):
        self.result = result
        super().__init__(f"领星取 token 失败：{result.outcome} {result.lx_code} {result.cause or result.lx_msg}")


def token(cfg: dict) -> str:
    with _lock:
        if _cache.get("token") and _cache["expires_at"] - time.time() > 60:
            return _cache["token"]
        r = post_json(cfg["base_url"], "/api/auth-server/oauth/access-token",
                      {"appId": cfg["app_id"], "appSecret": cfg["app_secret"]}, {},
                      cfg["timeout_s"], cfg["user_agent"], proxy_from(cfg))
        log.info("erp op=access_token host=%s ms=%s outcome=%s lx_code=%s cause=%s",
                 r.host, r.elapsed_ms, r.outcome, r.lx_code, r.cause)
        if r.outcome != "ok" or str(r.lx_code) != "200":
            raise AuthFailed(r)
        d = r.body["data"]
        _cache.update(token=d["access_token"], expires_at=time.time() + int(d["expires_in"]))
        return _cache["token"]
