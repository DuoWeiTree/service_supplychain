"""连真 CH 的冒烟。★ 跳过必须吵 —— 静默跳过的测试与没写这个测试一样。"""
import pytest

from dim import ch_source as cs
from shared.ch_client import ch_client, ch_query, describe_failure


@pytest.fixture(scope="module")
def live_query():
    try:
        return ch_query(ch_client())
    except BaseException as e:  # noqa: BLE001 - 跳过判据要兜住一切连不上的形状
        # （超时 / DNS 解析失败 / 代理劫持出的 503 / 鉴权拒绝……），漏一种就会让
        # 这条冒烟在内网抖动时炸出一个假失败，而不是干净地跳过并吵出理由。
        pytest.skip(f"CH 连不上，跳过实盘冒烟：{describe_failure(e)}"
                    f" —— 这不是「通过」，是没测", allow_module_level=True)


@pytest.mark.parametrize("fetch", [cs.fetch_sku_catalog, cs.fetch_msku_bridge,
                                   cs.fetch_warehouse])
def test_real_ch_shape_matches_the_transform(live_query, fetch):
    got = fetch(live_query)
    assert got.rows, "取回 0 行"
    assert got.source_max_captured is not None
    print(f"\n{fetch.__name__}: {len(got.rows)} 行 / 丢 {got.dropped} "
          f"{got.drop_reasons} / captured={got.source_max_captured}")
