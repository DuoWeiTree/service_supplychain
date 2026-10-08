from __future__ import annotations

import dataclasses
import time

from erp import auth
from erp.endpoints import Endpoint
from erp.sign import normalize, sign
from erp.transport import CallResult, post_json, proxy_from


class LiveDriver:
    mode = "live"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def call(self, ep: Endpoint, params: dict) -> CallResult:
        try:
            tok = auth.token(self.cfg)
        except auth.AuthFailed as e:
            # ★ 业务请求根本没发出去：not_sent，可安全重试
            return dataclasses.replace(e.result, outcome="not_sent", path=ep.path,
                                       cause=f"access_token: {e.result.cause or e.result.lx_msg}")
        body = normalize(params)
        base = {"access_token": tok, "app_key": self.cfg["app_id"], "timestamp": int(time.time())}
        query = {**base, "sign": sign({**base, **body}, self.cfg["app_id"])}
        return post_json(self.cfg["base_url"], ep.path, query, body,
                         self.cfg["timeout_s"], self.cfg["user_agent"], proxy_from(self.cfg))
