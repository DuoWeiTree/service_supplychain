"""CH 客户端。★ 连不上时要说清「打的谁 · 多久 · 怎么失败的」。"""
import logging

import pytest

from shared.ch_client import CH_CONNECT_TIMEOUT_S, ch_query


class _Boom:
    def query(self, sql):
        raise TimeoutError("connect timed out")


class _Rows:
    def __init__(self):
        self.seen = []

    def query(self, sql):
        self.seen.append(sql)
        return type("R", (), {"result_rows": [(1, "a")]})()


def test_query_returns_plain_tuples(monkeypatch, tmp_path):
    q = ch_query(_Rows())
    assert q("SELECT 1") == [(1, "a")]


def test_failure_logs_target_elapsed_and_cause(caplog, monkeypatch):
    """★ 只打 str(e) 等于丢掉全部上下文 —— 下次得重新复现一遍。"""
    with caplog.at_level(logging.WARNING, logger="scm.ch"), pytest.raises(TimeoutError):
        ch_query(_Boom())("SELECT 1")
    line = caplog.text
    assert "192.168.66.211" in line and "jxd_raw" in line, "没说打的谁"
    assert "elapsed_ms=" in line, "没说多久"
    assert "TimeoutError" in line, "没说怎么失败的"


def test_timeout_and_connect_failure_are_distinguishable():
    """★ 真超时与连不上签名互斥、处置相反：把后者读成前者会去调大超时，一行都不生效。"""
    from shared.ch_client import describe_failure
    assert describe_failure(TimeoutError("x"))["kind"] == "timeout"
    e = OSError("connect")
    e.errno = 110
    assert describe_failure(e)["kind"] == "connect"


def test_connect_timeout_is_explicit():
    assert CH_CONNECT_TIMEOUT_S == 5
