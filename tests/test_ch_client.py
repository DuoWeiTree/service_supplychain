"""CH 客户端。★ 连不上时要说清「打的谁 · 多久 · 怎么失败的」。

★ 本文件必须离线可跑（README「离线可跑的那部分」列了它）：下面两种真实失败
  形态全部造在 **loopback** 上 —— 127.0.0.1:9 没人听 → 连接被拒；一个接得上
  却一个字节都不回的本机 socket → 读超时。两者都不依赖内网、不依赖路由、
  不依赖超时时长，也不需要任何 mock。
"""
import logging
import socket
import threading

import pytest

from shared import ch_client as mod
from shared.ch_client import CH_CONNECT_TIMEOUT_S, ReadOnlyClient, ch_client, ch_query


class _Boom:
    def query(self, sql):
        raise TimeoutError("connect timed out")


class _Rows:
    def __init__(self):
        self.seen = []

    def query(self, sql):
        self.seen.append(sql)
        return type("R", (), {"result_rows": [(1, "a")]})()


def test_query_returns_plain_tuples(monkeypatch):
    _point_at(monkeypatch, "192.168.66.211", 8123)
    q = ch_query(_Rows())
    assert q("SELECT 1") == [(1, "a")]


def test_failure_logs_target_elapsed_and_cause(caplog, monkeypatch):
    """★ 只打 str(e) 等于丢掉全部上下文 —— 下次得重新复现一遍。

    ★ 终审 I-5 连带：`ch_query()` 一进门就读配置取 host/db，所以这两条
      原本在 `JXD_SCM_CONFIG=/nonexistent` 下以 `FileNotFoundError` 红 ——
      README 的「离线可跑」命令里混进来的不止 `test_mirror_registry.py` 一个。
      它们本身是纯的，缺的只是别去读那个文件。
    """
    _point_at(monkeypatch, "192.168.66.211", 8123)
    with caplog.at_level(logging.WARNING, logger="scm.ch"), pytest.raises(TimeoutError):
        ch_query(_Boom())("SELECT 1")
    line = caplog.text
    assert "192.168.66.211" in line and "jxd_raw" in line, "没说打的谁"
    assert "elapsed_ms=" in line, "没说多久"
    assert "TimeoutError" in line, "没说怎么失败的"


def _point_at(monkeypatch, host: str, port: int) -> None:
    """让 `ch_client()` 去打指定地址。★ 覆盖的是 `shared.ch_client` 里那个
    `clickhouse` 名字，不是环境变量 —— 这样离线（JXD_SCM_CONFIG=/nonexistent）
    也不会去读配置文件。"""
    monkeypatch.setattr(mod, "clickhouse", lambda: {
        "host": host, "port": port, "user": "default", "password": "",
        "database": "jxd_raw", "secure": False})


@pytest.fixture
def silent_server():
    """接得上、一个字节都不回的本机服务端。

    ★ 「连上了但不回话」这一形态没有别的造法：它与「连不上」签名互斥、处置
      相反，而在 loopback 上它是确定性的 —— 不需要黑洞地址，因而不依赖路由，
      离线机器上照样红/绿分明。
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    held: list[socket.socket] = []

    def accept_and_stay_silent():
        try:
            conn, _ = srv.accept()
            held.append(conn)          # 接住不放，一个字节都不回
        except OSError:
            pass

    threading.Thread(target=accept_and_stay_silent, daemon=True).start()
    try:
        yield srv.getsockname()[1]
    finally:
        for conn in held:
            conn.close()
        srv.close()


def _fail(monkeypatch, host, port) -> BaseException:
    """真的用 `ch_client()` 去连，把真驱动抛出来的那个异常原样交出来。"""
    _point_at(monkeypatch, host, port)
    with pytest.raises(BaseException) as exc:   # 异常类型正是待测内容，不能先写死
        ch_client()
    return exc.value


def test_a_refused_connection_is_classified_from_the_real_driver_exception(monkeypatch):
    """★ 127.0.0.1:9 没人听 —— 真驱动实测（clickhouse-connect 1.5.0 / urllib3 2.8.0）：
    `OperationalError ← MaxRetryError ← NewConnectionError ← ConnectionRefusedError`。
    旧断言喂的是手搓的 `OSError(errno=110)`，这条链上永远不会出现那个形状。
    """
    d = mod.describe_failure(_fail(monkeypatch, "127.0.0.1", 9))
    assert d["kind"] == "connect_refused", f"连接被拒没被认出来：{d}"
    assert "ConnectionRefusedError" in d["causes"], f"cause 链没留痕：{d}"
    assert d["code"] == 111, f"errno 只在 cause 链最里层，没挖出来：{d}"


def test_a_timeout_is_classified_from_the_real_driver_exception(monkeypatch, silent_server):
    """★ 接得上、不回话 —— 真驱动实测：
    `OperationalError ← ReadTimeoutError ← builtins.TimeoutError`。
    旧断言喂的是手搓的内建 `TimeoutError`，而 `isinstance(e, TimeoutError)`
    对真驱动抛出的 `OperationalError` **永远不成立**。
    """
    monkeypatch.setattr(mod, "CH_SEND_RECEIVE_TIMEOUT_S", 1)
    d = mod.describe_failure(_fail(monkeypatch, "127.0.0.1", silent_server))
    assert d["kind"] == "timeout", f"读超时没被认出来：{d}"
    assert "TimeoutError" in d["causes"], f"cause 链没留痕：{d}"


def test_timeout_and_connect_failure_are_distinguishable(monkeypatch, silent_server):
    """★ 这才是 docstring 承诺的那条判据本身：两种真形态必须给出**不同**的 kind。
    前者调大超时有用，后者一行都不生效 —— 混成同一个 kind 就会去改那一行。
    """
    refused = mod.describe_failure(_fail(monkeypatch, "127.0.0.1", 9))
    monkeypatch.setattr(mod, "CH_SEND_RECEIVE_TIMEOUT_S", 1)
    slow = mod.describe_failure(_fail(monkeypatch, "127.0.0.1", silent_server))
    assert refused["kind"] != slow["kind"], (
        f"两种真形态被压成同一个 kind：refused={refused} slow={slow}")
    assert {refused["kind"], slow["kind"]} == {"connect_refused", "timeout"}


def test_the_driver_still_chains_down_to_a_builtin(monkeypatch):
    """★ 分类是按 cause 链最里层那个**标准库**异常判的（urllib3 的继承关系是
    反的：`NameResolutionError ⊂ NewConnectionError ⊂ ConnectTimeoutError`，
    按 urllib3 类型判、顺序写反就会把「被拒」报成「超时」）。

    这条守的是那个前提：驱动哪天不再 chain 到标准库异常，`kind` 会安静地
    退成 `other` —— 安静地退化正是最难发现的那一种。
    """
    chain = mod._cause_chain(_fail(monkeypatch, "127.0.0.1", 9))
    assert any(isinstance(e, ConnectionRefusedError) for e in chain), (
        f"链里没有标准库异常了，分类的依据没了：{[type(e).__name__ for e in chain]}")


def test_connect_timeout_is_explicit():
    assert CH_CONNECT_TIMEOUT_S == 5


def test_read_timeout_is_explicit():
    """★ M-7：`clickhouse_connect` 的 `send_receive_timeout` 默认 300 秒。
    不覆盖它，CH 接了 TCP 却不回数据时，启动触发的那轮刷新会把 lifespan
    卡住最坏 300s × 4 张镜像 —— /health 同样不可达，与 I-1 同一个代价。"""
    assert mod.CH_SEND_RECEIVE_TIMEOUT_S == 60


def test_read_only_wrapper_hides_write_methods_of_the_raw_client():
    """★ CH 只读必须由类型收窄硬保证，不是靠注释：`ch_client()` 的模块级
    入口只能交出这层封装，原始 clickhouse_connect 客户端的 command()/insert()
    绝不能逃出 shared/ch_client.py。"""
    class _RawWithWriteMethods:
        def query(self, sql):
            return type("R", (), {"result_rows": [(1,)]})()

        def command(self, *a, **k):
            raise AssertionError("command() 必须不可达 —— CH 只读")

        def insert(self, *a, **k):
            raise AssertionError("insert() 必须不可达 —— CH 只读")

    w = ReadOnlyClient(_RawWithWriteMethods())
    assert not hasattr(w, "insert"), "insert() 逃出了只读封装"
    assert not hasattr(w, "command"), "command() 逃出了只读封装"
    assert w.query("SELECT 1").result_rows == [(1,)]
