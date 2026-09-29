"""实测：`sku_category` 端到端跑通真实 CH + 真实 PM（Task 1 Step 10）。

★ 只写 `scm_test`，绝不碰生产 schema `scm`——`business_db`/`wipe` 已经把
  `JXD_SCM_CONFIG` 钉在 `scm_test` 一整个测试会话，这里只是重申。
★ 跳过必须点名 host/cause——静默跳过与没测过是同一件坏事（同
  `tests/test_dim_ch_source_live.py`/`tests/test_jobs_refresh_dims_live.py`
  的既有纪律，PM 那一半是本文件新加的对应品）。
"""
from __future__ import annotations

import pytest

from jobs import refresh_dims as rd
from shared import pm_client
from shared.ch_client import ch_client, ch_query, describe_failure
from shared.config import clickhouse
from shared.pg_client import pg_conn


def _skip_if_ch_unreachable():
    c = clickhouse()
    where = f"{c['host']}:{c.get('port', 8123)}/{c.get('database', 'jxd_raw')}"
    try:
        return ch_query(ch_client())
    except Exception as e:  # noqa: BLE001 - 跳过判据要兜住一切连不上的形状
        pytest.skip(f"CH 不可达 target={where} cause={describe_failure(e)}"
                    " —— 跳过而非静默：本条从不因为「没测到」而绿")


def _skip_if_pm_unreachable():
    """★ 真的换一次 token——mint_token() 内部已经三问日志过一遍失败，这里
    只负责把结果翻译成 pytest.skip 而不是让测试报一个裸异常。"""
    try:
        pm_client.mint_token()
    except Exception as e:  # noqa: BLE001 - 同上，PM/Keycloak 连不上的形状不可预知
        pytest.skip(f"PM/Keycloak 不可达 err_type={type(e).__name__} err={e}"
                    " —— 跳过而非静默：本条从不因为「没测到」而绿")


def test_sku_category_refreshes_against_real_ch_and_real_pm(wipe):
    """★ 存在的理由：`tests/test_dim_pm_source.py` 全部用手写的桩，从不碰
    真实响应信封——`dim/pm_source.py` 的 base-aware 匹配/占位图判据在真实
    PM 数据下到底命中多少，只有这里能回答。同时把控制器实测过的覆盖率数字
    （Global Constraints「1003 / 660 / 60」）在当前时点重新量一次，供报告
    引用——不是重新推导裁定，是给出「现在还是不是这个数量级」的证据。
    """
    live_ch = _skip_if_ch_unreachable()
    _skip_if_pm_unreachable()

    # ① 真 CH：把当前真实的货号全集刷进 scm_test.sku_catalog——
    #   sku_category 匹配用的"宇宙"就是这张表（dim/registry.py 的
    #   depends_on=("sku_catalog",)）。
    catalog_run = rd.refresh_all("cli", only="sku_catalog", query=live_ch)
    assert catalog_run[0].ok, f"真实 CH 取数失败：{catalog_run[0].error}"
    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM sku_catalog")
        total = cur.fetchone()[0]
    assert total > 0, "真实 sku_catalog 一行都没取到——口径大概率有问题"

    # ② 真 PM：走完整的 refresh_all()——不注入桩，让 `_fetch_pm_mirror`
    #   真的 mint_token() + fetch_products() + fetch_categories()。
    got = rd.refresh_all("cli", only="sku_category")
    assert got[0].ok, f"PM 取数/落库失败：{got[0].error}"

    with pg_conn() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM sku_category")
        matched = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sku_category WHERE NOT image_is_placeholder")
        real_image = cur.fetchone()[0]

    print(f"\nsku_category 实测：总数={total} 匹配上={matched} 有真图={real_image}"
          f" drop_reasons={got[0].error or got[0]}")

    assert matched > 0, (
        f"PM 目录一个货号都没匹配上（总数={total}）——两段匹配大概率断了，"
        f"drop_reasons 见上面这行的完整 RunRow")
    assert real_image > 0, (
        f"匹配上的 {matched} 个货号里一张真图都没有——占位判据大概率把全部"
        "都误判成了占位图")
