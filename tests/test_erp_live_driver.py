"""真 driver 端到端：只对本机桩服务（127.0.0.1）。

★ 网关在其余测试里一律是替身（conftest autouse）；只有这里显式构造 LiveDriver。
  真领星由 transport 层的测试闸挡住：非本机 / 非 .invalid 主机在测试进程里直接抛。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from erp import auth
from erp.drivers.live import LiveDriver
from erp.endpoints import get as get_endpoint
from erp.sign import normalize, sign
from erp.transport import post_json

APP_ID = "abcdefghijklmnop"          # AES 密钥长度须为 16 / 24 / 32
TOKEN_PATH = "/api/auth-server/oauth/access-token"


class Stub:
    def __init__(self, token_reply=(200, {"code": "200", "data": {"access_token": "tok-1", "expires_in": 7200}})):
        self.token_reply = token_reply
        self.seen: list[dict] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                u = urlsplit(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                stub.seen.append({"path": u.path, "query": {k: v[0] for k, v in parse_qs(u.query).items()},
                                  "body": json.loads(raw or b"{}")})
                status, reply = stub.token_reply if u.path == TOKEN_PATH else \
                    (200, {"code": 0, "data": [], "total": 0})
                b = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def cfg(self) -> dict:
        return {"base_url": self.base, "app_id": APP_ID, "app_secret": "secret-x", "timeout_s": 2,
                "user_agent": "scm-test/1", "proxy_host": ""}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def stub_factory():
    made = []
    auth._cache.clear()

    def make(**kw):
        s = Stub(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()
    auth._cache.clear()


def test_live_driver_signs_and_reaches_stub(stub_factory):
    s = stub_factory()
    ep = get_endpoint("purchase_order_list")
    params = {"search_field_time": "create_time", "start_date": "2026-10-07", "end_date": "2026-10-09",
              "offset": 0, "length": 500}
    r = LiveDriver(s.cfg()).call(ep, params)
    assert r.outcome == "ok" and r.host == "127.0.0.1" and r.path == ep.path, r
    tok_req, biz = s.seen
    assert tok_req["path"] == TOKEN_PATH
    assert tok_req["query"] == {"appId": APP_ID, "appSecret": "secret-x"}
    assert biz["path"] == ep.path and biz["body"] == normalize(params)
    q = biz["query"]
    assert (q["access_token"], q["app_key"]) == ("tok-1", APP_ID) and q["timestamp"].isdigit()
    base = {"access_token": "tok-1", "app_key": APP_ID, "timestamp": int(q["timestamp"])}
    assert q["sign"] == sign({**base, **normalize(params)}, APP_ID)


def test_token_failure_is_not_sent_and_business_call_never_goes_out(stub_factory):
    s = stub_factory(token_reply=(200, {"code": "401", "message": "appSecret 错误"}))
    r = LiveDriver(s.cfg()).call(get_endpoint("create_purchase_order"), {"custom_order_sn": "SCM-PO-1"})
    assert r.outcome == "not_sent" and r.cause.startswith("access_token")
    assert [x["path"] for x in s.seen] == [TOKEN_PATH]


@pytest.mark.parametrize("base_url, proxy", [
    ("https://lingxing-guard-test.example", None),
    ("https://target.invalid", {"host": "proxy-guard-test.example", "port": 3128, "user": "", "password": ""}),
    ("https://192.0.2.1", None),
])
def test_transport_refuses_non_local_hosts_in_tests(base_url, proxy):
    """★ 主机名都选了保留域（.example）/ 不可路由地址：闸若失效，也只会连不上，打不到任何真系统。"""
    with pytest.raises(RuntimeError, match="测试进程禁止出站"):
        post_json(base_url, "/x", {}, {}, 0.5, "t", proxy)


def test_live_driver_still_refused_for_non_local_target():
    with pytest.raises(RuntimeError, match="测试禁止实例化真领星 driver"):
        LiveDriver({"base_url": "https://lingxing.invalid", "proxy_host": ""})
