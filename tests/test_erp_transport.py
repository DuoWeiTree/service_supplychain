import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from erp.transport import post_json


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _serve(handler_body):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            handler_body(self)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _stop(srv):
    srv.shutdown()
    srv.server_close()


def test_connection_refused_is_not_sent():
    r = post_json(f"http://127.0.0.1:{_free_port()}", "/x", {}, {}, 2, "t")
    assert r.outcome == "not_sent" and "ConnectionRefusedError" in r.cause


def test_read_timeout_after_send_is_unknown():
    def slow(h):
        time.sleep(2)
    srv, base = _serve(slow)
    r = post_json(base, "/x", {}, {"a": 1}, 0.5, "t")
    _stop(srv)
    assert r.outcome == "unknown" and "timeout" in r.cause.lower()


def test_business_error_is_rejected_with_code():
    def reject(h):
        b = json.dumps({"code": 1001, "message": "供应商不存在"}).encode()
        h.send_response(200); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)
    srv, base = _serve(reject)
    r = post_json(base, "/x", {}, {}, 2, "t")
    _stop(srv)
    assert (r.outcome, r.lx_code, r.lx_msg) == ("rejected", "1001", "供应商不存在")


def test_5xx_after_send_is_unknown():
    def boom(h):
        h.send_response(502); h.send_header("Content-Length", "0"); h.end_headers()
    srv, base = _serve(boom)
    r = post_json(base, "/x", {}, {}, 2, "t")
    _stop(srv)
    assert r.outcome == "unknown" and r.http_status == 502


def test_dead_proxy_is_not_sent_and_names_the_proxy():
    """★ 经代理时，连不上代理 / CONNECT 被拒 都发生在 connect() 阶段 —— 领星什么都没收到。"""
    port = _free_port()
    r = post_json("https://openapi.lingxing.com", "/x", {}, {}, 2, "t",
                  proxy={"host": "127.0.0.1", "port": port, "user": "u", "password": "p"})
    assert r.outcome == "not_sent" and f"via proxy 127.0.0.1:{port}" in r.cause


def test_proxy_auth_header_is_basic():
    from erp.transport import _proxy_headers
    assert _proxy_headers({"user": "dwt", "password": "pw"}) == {"Proxy-Authorization": "Basic ZHd0OnB3"}
    assert _proxy_headers({"user": "", "password": ""}) == {}


def test_proxy_from_config():
    from erp.transport import proxy_from
    assert proxy_from({"proxy_host": ""}) is None
    assert proxy_from({"proxy_host": "h", "proxy_port": 3128, "proxy_user": "u", "proxy_password": "p"}) == \
        {"host": "h", "port": 3128, "user": "u", "password": "p"}


def test_ok_and_user_agent_sent():
    seen = {}

    def ok(h):
        seen["ua"] = h.headers["User-Agent"]
        b = json.dumps({"code": 0, "data": {"order_sn": "PO1"}}).encode()
        h.send_response(200); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)
    srv, base = _serve(ok)
    r = post_json(base, "/x", {"q": 1}, {}, 2, "scm-test/1")
    _stop(srv)
    assert r.outcome == "ok" and r.body["data"]["order_sn"] == "PO1" and seen["ua"] == "scm-test/1"
