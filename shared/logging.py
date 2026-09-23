"""进程的日志配置。★ 只有一个调用点：`api.create_app()`。

★ 为什么必须显式配：uvicorn 的 LOGGING_CONFIG 只给 `uvicorn*` 那几个 logger 挂
  handler，`scm.*` 的记录会一路 propagate 到没有任何 handler 的 root，最后落进
  `logging.lastResort` —— 它的 level 是 WARNING，于是 `timed()` 的耗时行、启动钩子
  的镜像/迁移行、以及每一条提交后的结果行**全部被丢弃**，而失败那几条会以没有时间戳、
  没有级别、没有 logger 名的裸字符串出去。日志三问一个都答不上来。
"""
from __future__ import annotations

import logging
import sys

#: 本仓所有 logger 都挂在这个前缀下（scm.api / scm.pg）。
ROOT_LOGGER = "scm"
FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

#: 打在自己装的 handler 上的记号。★ 认 handler 而不是数 handlers ——
#: 数个数的话，别人（uvicorn / pytest）往同一个 logger 上挂一个，就会被当成「已配过」。
_MARK = "_scm_handler"


def setup_logging(level: str = "INFO") -> logging.Logger:
    """幂等：重复调用不会叠出第二个 handler（uvicorn --reload 会重建 app）。"""
    log = logging.getLogger(ROOT_LOGGER)
    log.setLevel(level)
    # ★ 不 propagate 到 root：root 上挂着什么不归我们管，
    #   传上去的结果是同一条记录按别人的格式再打一遍（或者一次都不打）。
    log.propagate = False
    if not any(getattr(h, _MARK, False) for h in log.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(FORMAT))
        setattr(handler, _MARK, True)
        log.addHandler(handler)
    return log
