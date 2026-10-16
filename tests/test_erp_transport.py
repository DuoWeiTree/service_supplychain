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


def test_error_details_reaches_lx_msg():
    # erp_call 3（2026-10-09）实收形状：message 只写「内部错误」，真因在 error_details
    def reject(h):
        b = json.dumps({"code": 500, "message": "内部错误", "error_details": "仓库参数错误",
                        "request_id": "1F7C88E8", "data": None}, ensure_ascii=False).encode()
        h.send_response(200); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)
    srv, base = _serve(reject)
    r = post_json(base, "/x", {}, {}, 2, "t")
    _stop(srv)
    assert (r.outcome, r.lx_code, r.lx_msg) == ("rejected", "500", "内部错误：仓库参数错误")


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
    r = post_json("https://target.invalid", "/x", {}, {}, 2, "t",
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


def _raw_server(reply: bytes):
    """逐字节回包的本机 socket：http.server 发不出非 HTTP 的状态行。"""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0)); s.listen(5)

    def run():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            c.settimeout(2)
            try:
                c.recv(65536)
            except OSError:
                pass
            c.sendall(reply); c.close()
    threading.Thread(target=run, daemon=True).start()
    return s, s.getsockname()[1]


def _http(body: bytes, status: bytes = b"200 OK") -> bytes:
    return b"HTTP/1.1 " + status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def _post_raw(reply: bytes):
    s, port = _raw_server(reply)
    try:
        return post_json(f"http://127.0.0.1:{port}", "/x", {}, {}, 2, "t")
    finally:
        s.close()


def test_2xx_without_usable_code_is_unknown_never_rejected():
    """★ 不可逆调用上 rejected = 放行重提 = 可能重复建单。"""
    for label, reply in [("empty", _http(b"")), ("html", _http(b"<html>gw</html>")),
                         ("no code", _http(b'{"data":{"order_sn":"P1"}}')),
                         ("list", _http(b"[1,2]")), ("string", _http(b'"x"')),
                         ("bad utf8", _http(b"\xff\xfe"))]:
        r = _post_raw(reply)
        assert r.outcome == "unknown", label
        assert r.lx_code is None and r.cause, label


def test_non_json_4xx_stays_rejected():
    r = _post_raw(_http(b"<html>", b"403 Forbidden"))
    assert r.outcome == "rejected" and r.http_status == 403


def test_proxy_answering_connect_with_garbage_is_not_sent():
    s, port = _raw_server(b"SSH-2.0-OpenSSH\r\n")
    try:
        r = post_json("https://target.invalid", "/x", {}, {}, 2, "t",
                      proxy={"host": "127.0.0.1", "port": port, "user": "u", "password": "p"})
    finally:
        s.close()
    assert r.outcome == "not_sent" and f"via proxy 127.0.0.1:{port}" in r.cause
