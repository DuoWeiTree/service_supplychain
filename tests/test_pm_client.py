"""`shared/pm_client.py` 的门禁（Task 1 Step 7/8）。

全部离线：`urllib.request` 的 opener 一律被 monkeypatch 掉，不联真网——真连
`pm.zestrade.com`/`auth.zestrade.com` 是 `tests/test_pm_live.py` 的职责。

参考实现（只读，未 import）：兄弟仓
`model_inventory_forecast/v2/data/tests/test_pm_catalog_fetch.py` ——
`_RecordingOpener`/`_Resp` 两个桩形状照抄，其余断言按本仓 `shared/pm_client.py`
的真实签名（`fetch_categories`/`fetch_products`，不是 `fetch_tree`/
`fetch_all_products`）重写。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

import shared.config as C
import shared.pm_client as PM


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()
        self.headers = {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecordingOpener:
    """记下每次 `.open()` 收到的 `Request`，`answer(req)` 决定怎么回应。"""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[urllib.request.Request] = []

    def open(self, req, timeout=None):
        self.calls.append(req)
        return self.answer(req)


def _write_config(tmp_path, section: dict | None):
    p = tmp_path / "config.toml"
    if section is None:
        p.write_text("[business_pg]\nhost = \"x\"\n", encoding="utf-8")
    else:
        lines = ["[pm_zestrade]"]
        for k, v in section.items():
            lines.append(f'{k} = "{v}"')
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _point_config_at(monkeypatch, path):
    """★ 必须走 `monkeypatch.setattr`，不能裸调 `reset_cache()`——`_CFG` 是
    进程级缓存，裸调只把它设成 `None`/换成临时配置，测试结束后不会自动还原；
    下一个用到 `business_pg()`/`clickhouse()` 的测试会读到这份只有
    `[pm_zestrade]` 的残缺配置并炸在 `KeyError: 'business_pg'`——2026-09-29
    实测踩过一次：`tests/test_pm_live.py` 紧跟在本文件后面跑就是这样中招的。
    `monkeypatch.setattr` 在测试结束时把 `_CFG` 还原成原来那份（`business_db`
    夹具已经建好、指向 `scm_test` 的完整配置），其余测试才不会被牵连。"""
    monkeypatch.setenv("JXD_SCM_CONFIG", str(path))
    monkeypatch.setattr(C, "_CFG", None)


_FULL_SECTION = {
    "token_url": "https://auth.zestrade.com/realms/sso/protocol/openid-connect/token",
    "client_id": "api-bot",
    "client_secret": "s3cr3t-from-config-9f2a",
    "pm_base": "https://pm.zestrade.com",
}


# ══════════════════════════════════════════════════════════════════════
#  ① 配置缺段/缺字段：在 mint_token 这里炸，不等到发出网络请求
#     （CLAUDE.md「配置类错误往启动钩子放，别等第一个请求才炸」）
# ══════════════════════════════════════════════════════════════════════

def test_missing_pm_zestrade_section_fails_before_any_request(monkeypatch, tmp_path):
    cfg_path = _write_config(tmp_path, None)
    _point_config_at(monkeypatch, cfg_path)

    def _boom_if_called():
        raise AssertionError(
            "_opener() 被调用了——配置缺失没能在 mint_token 里挡住，"
            "代码已经往下走到了要发网络请求的地方")

    monkeypatch.setattr(PM, "_opener", _boom_if_called)

    with pytest.raises(RuntimeError) as exc_info:
        PM.mint_token()
    assert "pm_zestrade" in str(exc_info.value), (
        f"错误必须点名段名 [pm_zestrade]：{exc_info.value}")


def test_missing_field_names_the_field(monkeypatch, tmp_path):
    section = dict(_FULL_SECTION)
    section["client_secret"] = ""  # 空字符串等于没填
    cfg_path = _write_config(tmp_path, section)
    _point_config_at(monkeypatch, cfg_path)
    monkeypatch.setattr(PM, "_opener", lambda: (_ for _ in ()).throw(
        AssertionError("不该走到网络请求")))

    with pytest.raises(RuntimeError) as exc_info:
        PM.mint_token()
    assert "pm_zestrade" in str(exc_info.value)
    assert "client_secret" in str(exc_info.value)


def test_secret_never_appears_in_the_source_file():
    """★ 反证：secret 只能来自 config，不能字面量写死进源码。"""
    with open(PM.__file__, encoding="utf-8") as f:
        text = f.read()
    assert _FULL_SECTION["client_secret"] not in text


# ══════════════════════════════════════════════════════════════════════
#  ② mint_token / fetch_categories / fetch_products 的响应信封
# ══════════════════════════════════════════════════════════════════════

def test_mint_token_extracts_access_token_and_posts_client_credentials(monkeypatch):
    opener = _RecordingOpener(lambda req: _Resp({"access_token": "tok-abc"}))
    monkeypatch.setattr(PM, "_opener", lambda: opener)

    token = PM.mint_token(cfg=_FULL_SECTION)

    assert token == "tok-abc"
    assert len(opener.calls) == 1
    req = opener.calls[0]
    assert req.get_method() == "POST"
    body = urllib.parse.parse_qs(req.data.decode())
    assert body["grant_type"] == ["client_credentials"]
    assert body["client_id"] == [_FULL_SECTION["client_id"]]
    assert body["client_secret"] == [_FULL_SECTION["client_secret"]]


def test_fetch_categories_returns_the_bare_top_level_array(monkeypatch):
    raw_tree = [{"category_code": "PMC-1", "category_name": "宠物用品",
                 "parent_category_code": None, "level": 1, "is_leaf": False,
                 "sort_order": 0, "children": []}]
    opener = _RecordingOpener(lambda req: _Resp(raw_tree))
    monkeypatch.setattr(PM, "_opener", lambda: opener)

    tree = PM.fetch_categories("tok", cfg=_FULL_SECTION)
    assert tree == raw_tree
    req = opener.calls[0]
    assert req.get_header("Authorization") == "Bearer tok"


def test_fetch_products_pages_by_the_responses_pageSize_not_the_requested_one(monkeypatch):
    """★★ 同兄弟仓实测样本：请求 page_size=100，PM 却回 pageSize=20 ——
    翻页页数必须按响应的 pageSize 算，否则会漏掉大半货号且没有任何报错。"""
    total = 45
    real_page_size = 20
    requested_pages: list[int] = []

    def _answer(req):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        page = int(qs["page"][0])
        assert int(qs["page_size"][0]) == PM.PRODUCTS_PAGE_SIZE
        requested_pages.append(page)
        start = (page - 1) * real_page_size
        end = min(start + real_page_size, total)
        items = [{"sku_code": f"SKU-{i}"} for i in range(start, end)]
        return _Resp({"items": items, "page": page, "pageSize": real_page_size,
                      "total": total})

    opener = _RecordingOpener(_answer)
    monkeypatch.setattr(PM, "_opener", lambda: opener)

    items = PM.fetch_products("tok", cfg=_FULL_SECTION)

    expected_pages = -(-total // real_page_size)
    assert requested_pages == list(range(1, expected_pages + 1)), (
        f"该按真实 pageSize={real_page_size} 请求 {expected_pages} 页，"
        f"实际 {requested_pages}")
    assert len(items) == total


def test_fetch_products_single_page_when_total_fits(monkeypatch):
    calls = []

    def _answer(req):
        calls.append(1)
        return _Resp({"items": [{"sku_code": "A"}, {"sku_code": "B"}],
                      "page": 1, "pageSize": 100, "total": 2})

    opener = _RecordingOpener(_answer)
    monkeypatch.setattr(PM, "_opener", lambda: opener)
    items = PM.fetch_products("tok", cfg=_FULL_SECTION)
    assert len(calls) == 1
    assert len(items) == 2


# ══════════════════════════════════════════════════════════════════════
#  ③ 超时与连不上必须分得开（CLAUDE.md 铁律 1）
# ══════════════════════════════════════════════════════════════════════

def test_timeout_is_classified_as_timeout_not_connect_refused(monkeypatch, caplog):
    def _boom(req):
        raise urllib.error.URLError(TimeoutError("timed out"))

    opener = _RecordingOpener(_boom)
    monkeypatch.setattr(PM, "_opener", lambda: opener)

    caplog.set_level("ERROR")
    with pytest.raises(urllib.error.URLError):
        PM.fetch_categories("tok", cfg=_FULL_SECTION)

    assert "kind=timeout" in caplog.text or "'kind': 'timeout'" in caplog.text, (
        f"超时没被分类成 timeout：{caplog.text!r}")
    assert "connect_refused" not in caplog.text


def test_connection_refused_is_classified_as_connect_refused_not_timeout(monkeypatch, caplog):
    def _boom(req):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    opener = _RecordingOpener(_boom)
    monkeypatch.setattr(PM, "_opener", lambda: opener)

    caplog.set_level("ERROR")
    with pytest.raises(urllib.error.URLError):
        PM.fetch_categories("tok", cfg=_FULL_SECTION)

    assert ("kind=connect_refused" in caplog.text
            or "'kind': 'connect_refused'" in caplog.text), (
        f"连不上没被分类成 connect_refused：{caplog.text!r}")
    assert "'kind': 'timeout'" not in caplog.text and "kind=timeout" not in caplog.text


def test_outbound_failure_logs_host_and_path(monkeypatch, caplog):
    def _boom(req):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    opener = _RecordingOpener(_boom)
    monkeypatch.setattr(PM, "_opener", lambda: opener)

    caplog.set_level("ERROR")
    with pytest.raises(urllib.error.URLError):
        PM.fetch_categories("tok", cfg=_FULL_SECTION)

    assert "pm.zestrade.com" in caplog.text
    assert "/api/integration/categories" in caplog.text


# ══════════════════════════════════════════════════════════════════════
#  ④ 免代理 + 显式 UA
# ══════════════════════════════════════════════════════════════════════

def test_opener_uses_proxyhandler_empty_dict(monkeypatch):
    """★ `ProxyHandler({})` 的空字典意味着它不注册任何 `<protocol>_open` 方法，
    于是 `OpenerDirector.add_handler()` 压根不会把它收进 `.handlers`——查那份
    列表查不出「传没传空字典」。照兄弟仓的写法：拦截 `build_opener` 本身，
    看它收到的**参数**是不是一个 `proxies == {}` 的 `ProxyHandler`。"""
    captured: list = []
    real_build_opener = urllib.request.build_opener

    def _fake_build_opener(*handlers):
        captured.extend(handlers)
        return real_build_opener(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", _fake_build_opener)

    PM._opener()

    assert any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {}
              for h in captured), (
        f"_opener() 必须传 ProxyHandler({{}})——本机常设 HTTP_PROXY 会把内网/"
        f"专线 IP 劫持掉：{captured}")


def test_requests_carry_explicit_user_agent(monkeypatch):
    opener = _RecordingOpener(lambda req: _Resp({"access_token": "tok"}))
    monkeypatch.setattr(PM, "_opener", lambda: opener)
    PM.mint_token(cfg=_FULL_SECTION)

    ua = opener.calls[0].get_header("User-agent")
    assert ua == PM._UA
    assert not ua.lower().startswith("python-urllib"), (
        f"UA 是 urllib 默认值，会被当机器人拦 403：{ua!r}")
