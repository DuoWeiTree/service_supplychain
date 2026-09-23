"""(store, sales_channel) → sid 的字面映射。★ 不解析店名，也不靠「msku 唯一」。

★ 为什么必须有这张表：`amazon_sp_api_report_all_orders` **没有 sid 列**，
  只有 `store`；而全库没有任何一张表同时带 `store` 与 `sid`（2026-09-23 实测）。
★ 为什么不用「marketplace + msku 唯一」这条近路：美国四个 sid 共 1,589 个 msku，
  实测仍有 2 个跨 sid（DHWC007036Y1Z2B、DHWC007036N1Z2B，均 11072∩11098）——
  0.13% 的静默错账，而静默错账正是本仓踩过六次的那一类。

★ 与 design §1.3 E-16 的一处出入（Task 4 实现时 live 复核发现，2026-09-23）：
  design 文档写的是「17 组能映射到 sid」，但 `lingxing_seller_list` 里
  sid=11095 = 'A4Pet-BS-DE'（country=德国）确实存在，而近 90 天
  `(A4PET_EUROPE, Amazon.de)` 有 539 单——不是「查过了、没有对应 sid」（那是
  OQ-3 的 6 组：es/fr/ie/it/pl/com.be，各自只有 1~12 单且 `lingxing_seller_list`
  里没有对应行）。这不是猜一个 sid，是 STORE_SID 表里原本该有的一行被漏抄了；
  不补上就是让一整个市场（德国）的销量历史永远拿不到。见
  `.superpowers/sdd/2026-09-23-chsource/task-4-report.md`「与 design 的出入」。
"""
import pytest

from dim import order_store_map as m


def test_the_eighteen_amazon_pairs_are_all_there():
    assert len(m.STORE_SID) == 18
    assert m.sid_for("PETSFIT_NORTH_AMERICA", "Amazon.com") == "11072"
    assert m.sid_for("A4PET_NORTH_AMERICA", "Amazon.com") == "11093"
    assert m.sid_for("A4PET_EUROPE", "Amazon.co.uk") == "11094"
    # ★ 上面文档段落里记录的那处出入：A4Pet 欧洲的德国店确实存在（sid 11095），
    #   近 90 天 539 单——漏掉这一行就是让整个德国市场的历史永远拿不到。
    assert m.sid_for("A4PET_EUROPE", "Amazon.de") == "11095"


def test_non_amazon_channels_are_excluded_explicitly_not_by_accident():
    assert m.sid_for("PETSFIT_NORTH_AMERICA", "Non-Amazon US") is None
    assert m.sid_for("PETSFIT_EUROPE", "Non-Amazon DE") is None


def test_an_unmapped_amazon_pair_hard_fails():
    """★ A4PET_EUROPE 的 es/fr/ie/it/pl/com.be 近 90 天有 21 单却没有对应 sid
    （A4Pet 欧洲只有 UK 11094 与 DE 11095 两个真实存在的 sid）—— 是真没开店还是
    seller_list 缺行，没查清（design §8 OQ-3）。没查清就硬失败，不猜。"""
    with pytest.raises(m.UnknownStore, match="A4PET_EUROPE.*Amazon.fr"):
        m.sid_for_or_raise("A4PET_EUROPE", "Amazon.fr")


def test_reverse_lookup_is_one_to_one():
    assert m.store_for("11072") == ("PETSFIT_NORTH_AMERICA", "Amazon.com")
    assert m.store_for("11095") == ("A4PET_EUROPE", "Amazon.de")
    with pytest.raises(m.UnknownStore, match="11100"):
        m.store_for("11100")     # A4Pet-BS-JP-JP：SP-API 采集里根本没有它的 store


def test_an_excluded_channel_is_not_told_to_add_a_store_sid_row():
    """★ 终审 M-4：`sid_for_or_raise()` 对一个**显式排除**的非 Amazon 渠道
    原先也说「新开的店请补进 STORE_SID」—— 按那条建议去做，就会把 Walmart /
    Chewy 的单记成某个 Amazon 店的销量，正是这张表存在的理由的反面。

    三种成因的文案必须分得开：显式排除 / OQ-3 已登记的缺口 / 真的没人声明过。
    """
    with pytest.raises(m.UnknownStore, match="显式排除") as excluded:
        m.sid_for_or_raise("PETSFIT_NORTH_AMERICA", "Non-Amazon US")
    assert "不许为它补一行" in str(excluded.value), (
        f"给排除渠道的建议里没有明确的「不许补」：{excluded.value}")
    assert "新开的店" not in str(excluded.value), (
        f"给排除渠道的建议还是「新开的店请补进 STORE_SID」：{excluded.value}")

    with pytest.raises(m.UnknownStore, match="OQ-3"):
        m.sid_for_or_raise("A4PET_EUROPE", "Amazon.fr")

    with pytest.raises(m.UnknownStore, match="新开的店"):
        m.sid_for_or_raise("BRAND_NEW_STORE", "Amazon.com")
