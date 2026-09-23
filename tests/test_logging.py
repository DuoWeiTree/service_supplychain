"""日志在**部署形态**下真的出得来。

★ 为什么要有这个文件：本仓在 timed()、pg_error_fields()、每一条提交后的结果行上
  花了真功夫，但进程里从来没有人给 `scm` 这个 logger 挂过 handler ——
  于是 INFO 全部落进 logging.lastResort（level=WARNING）被丢掉，而 WARNING 那几条
  会以没有时间戳、没有级别、没有 logger 名的裸字符串出去。
  没有这个文件，那次回归不会有任何东西变红：pytest 自己的 logging 插件在
  WARNING 上捕获，而谁都没断言过一条日志记录。
"""
from __future__ import annotations

import logging

import pytest

from shared.logging import ROOT_LOGGER, setup_logging


@pytest.fixture
def clean_scm_logger():
    """把 `scm` 复原成「没人配过」的样子，跑完再装回去。

    ★ setup_logging 是幂等的（进程级），不复原的话，本文件的断言证明的会是
      「别的测试早就配过了」，而不是「create_app() 配了」。
    """
    log = logging.getLogger(ROOT_LOGGER)
    saved = (list(log.handlers), log.level, log.propagate)
    log.handlers = []
    log.setLevel(logging.NOTSET)
    log.propagate = True
    try:
        yield log
    finally:
        log.handlers, log.propagate = saved[0], saved[2]
        log.setLevel(saved[1])


def test_setup_logging_attaches_exactly_one_handler_however_often_it_runs(clean_scm_logger):
    """★ uvicorn --reload 会重建 app —— 每次叠一个 handler 就是每条日志打 N 遍。"""
    setup_logging()
    setup_logging()
    mine = [h for h in clean_scm_logger.handlers if getattr(h, "_scm_handler", False)]
    assert len(mine) == 1
    assert isinstance(mine[0], logging.StreamHandler)
    assert mine[0].formatter._fmt == "%(asctime)s %(levelname)s %(name)s %(message)s"
    assert clean_scm_logger.level == logging.INFO
    assert clean_scm_logger.propagate is False


def test_info_reaches_our_own_handler_without_going_through_root(clean_scm_logger):
    """★ 要害是「不依赖 root」：部署时 root 上什么都没挂（uvicorn 的 LOGGING_CONFIG
    只配 uvicorn*），指望 propagate 到 root 就是指望运气 —— 而运气的结果是
    lastResort 按 WARNING 丢掉全部 INFO。
    """
    assert not clean_scm_logger.handlers, "夹具没把 scm 清干净，下面证明不了是谁配的"
    seen: list[logging.LogRecord] = []

    class Probe(logging.Handler):
        def emit(self, record):
            seen.append(record)

    setup_logging()
    clean_scm_logger.addHandler(Probe())
    logging.getLogger("scm.pg").info("op=probe elapsed_ms=1 outcome=ok")
    assert [r.getMessage() for r in seen] == ["op=probe elapsed_ms=1 outcome=ok"]
    assert clean_scm_logger.propagate is False


def test_startup_and_timed_records_reach_a_handler_at_info(seed, clean_scm_logger, caplog):
    """★ M6：`_log_startup` 只有把 TestClient 当上下文管理器用才会跑 ——
    否则 Starlette 根本不执行 lifespan，这个「配置问题第一屏可见」的钩子没有任何覆盖。

    ★ C1：同时钉住 timed() 的成功行确实进了 handler（而不是 lastResort）。
    """
    from starlette.testclient import TestClient

    from api import create_app

    app = create_app()
    # ★ propagate=False 是刻意的（见 shared/logging.py），caplog 装在 root 上因此看不见 ——
    #   把它自己的 handler 挂到 scm 上，验的才是「记录真的走到了 scm 的 handler」。
    clean_scm_logger.addHandler(caplog.handler)
    with TestClient(app) as client:                      # ← 进入上下文 = 跑 lifespan
        assert client.get("/v1/readiness").status_code == 200

    records = [r for r in caplog.records if r.name.startswith(ROOT_LOGGER)]
    info = [r.getMessage() for r in records if r.levelno == logging.INFO]
    assert any(m.startswith("startup mirror=") for m in info), \
        f"启动钩子的镜像行没出来：{info}"
    assert any(m.startswith("startup schema=") for m in info), \
        f"启动钩子的迁移行没出来：{info}"
    # ★ 三问里的「多久」：timed() 的成功行只在 INFO 上打，它到不了 handler
    #   就等于本仓所有 DB 调用的耗时全部丢失
    assert any("op=readiness" in m and "elapsed_ms=" in m and "outcome=ok" in m for m in info), \
        f"timed() 的成功行没进 handler：{info}"
    assert {r.name for r in records} >= {"scm.api", "scm.pg"}
