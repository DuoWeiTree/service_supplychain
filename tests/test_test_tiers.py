"""T0.2 测试分层的自检：纯层真的连不出去，归类真的按夹具走。"""
import socket

import psycopg2
import pytest


def test_pure_test_cannot_open_a_network_connection(request):
    assert request.node.get_closest_marker("pure") is not None
    s = socket.socket()
    s.settimeout(1)
    try:
        with pytest.raises(RuntimeError, match="该标 integration"):
            s.connect(("192.0.2.1", 5432))          # TEST-NET-1：真放出去也连不上，不会误伤外网
    finally:
        s.close()


def test_pure_test_cannot_open_a_pg_connection():
    with pytest.raises(RuntimeError, match="试图连 PG"):
        psycopg2.connect(host="192.0.2.1", connect_timeout=1)


class _Item:
    def __init__(self, fixturenames, marks=()):
        self.fixturenames, self.marks = list(fixturenames), {m.name for m in marks}

    def get_closest_marker(self, name):
        return name if name in self.marks else None

    def add_marker(self, mark):
        self.marks.add(mark.name)


def test_classification_follows_fixtures_and_explicit_marks():
    from conftest import pytest_collection_modifyitems
    db, plain, live = _Item(["wipe", "business_db"]), _Item(["tmp_path"]), _Item([], [pytest.mark.integration])
    pytest_collection_modifyitems(None, [db, plain, live])
    assert db.marks == {"integration"}
    assert plain.marks == {"pure"}
    assert live.marks == {"integration"}
