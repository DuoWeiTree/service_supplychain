"""(store, sales_channel) → sid。★ 这是一张**声明**的表，不是解析出来的。

★ 为什么需要它：`amazon_sp_api_report_all_orders` 没有 `sid` 列，只有 `store`；
  而 jxd_raw 里**没有任何一张表同时带 `store` 与 `sid`**（2026-09-23 实测）。
  桥只能自己声明 —— 而声明的表必须有守卫盯着它别过期（tests/test_order_store_map.py）。

★ 为什么不解析店名：`PETSFIT_NORTH_AMERICA` 要拆成「账号族 + 区域」，
  而 `lingxing_seller_list.account_name`（PETSFIT-JXD-US）与它并不同形。
  名字会被改写，id 不会（同 CLAUDE.md 对仓名的约定）。

★ 为什么不用「marketplace + msku 唯一」：美国四个 sid 共 1,589 个 msku，
  实测仍有 2 个跨 sid —— 0.13% 的静默错账。

★ Task 4 实现时 live 复核发现的一处出入（2026-09-23，见 tests/test_order_store_map.py
  顶部同一条注释、task-4-report.md「与 design 的出入」）：design §1.3 E-16 写的是
  「17 组能映射到 sid」，但 `lingxing_seller_list` 里 sid=11095='A4Pet-BS-DE'
  （country=德国）确实存在，近 90 天 `(A4PET_EUROPE, Amazon.de)` 有 539 单 ——
  这不是「查过了、没有对应 sid」（OQ-3 的 6 组各自只有 1~12 单、seller_list 里
  没有对应行），是 STORE_SID 表本该有的一行在设计稿转录时漏抄了。下表已补上，
  18 组，不是 17 组。
"""
from __future__ import annotations


class UnknownStore(Exception):
    """认不出的 (store, channel) 或 sid。★ 硬失败 —— 猜一个 sid 就是把一家店的
    销量记到另一家头上，而两边各自都「没报错」。"""


#: 2026-09-23 实测：近 90 天出现过 37 组 (store, sales_channel)，其中 24 组以
#: `Amazon.` 开头，这 18 组能对到 sid（design §1.3 E-16 写的「17 组」漏了下面
#: 这条：sid=11095='A4Pet-BS-DE' 在 lingxing_seller_list 里确实存在，且近 90 天
#: `(A4PET_EUROPE, Amazon.de)` 有 539 单）。另外 6 组（A4PET_EUROPE 的
#: es/fr/ie/it/pl/com.be，共 21 单）在 lingxing_seller_list 里没有对应 sid ——
#: 见 design §8 OQ-3，硬失败。其余 13 组是 `Non-Amazon*`，显式排除。
STORE_SID: dict[tuple[str, str], str] = {
    ("PETSFIT_NORTH_AMERICA", "Amazon.com"):    "11072",
    ("PETSFIT_NORTH_AMERICA", "Amazon.ca"):     "11073",
    ("PETSFIT_NORTH_AMERICA", "Amazon.com.mx"): "11075",
    ("PETSFIT_NORTH_AMERICA", "Amazon.com.br"): "11076",
    ("PETSFIT_EUROPE", "Amazon.co.uk"):         "11077",
    ("PETSFIT_EUROPE", "Amazon.de"):            "11078",
    ("PETSFIT_EUROPE", "Amazon.fr"):            "11079",
    ("PETSFIT_EUROPE", "Amazon.it"):            "11085",
    ("PETSFIT_EUROPE", "Amazon.pl"):            "11086",
    ("PETSFIT_EUROPE", "Amazon.es"):            "11087",
    ("PETSFIT_EUROPE", "Amazon.se"):            "11088",
    ("PETSFIT_EUROPE", "Amazon.com.be"):        "11089",
    ("PETSFIT_EUROPE", "Amazon.nl"):            "11090",
    ("PETSFIT_EUROPE", "Amazon.ie"):            "11091",
    ("PETSFIT_ASIA",   "Amazon.co.jp"):         "11092",
    ("A4PET_NORTH_AMERICA", "Amazon.com"):      "11093",
    ("A4PET_EUROPE", "Amazon.co.uk"):           "11094",
    ("A4PET_EUROPE", "Amazon.de"):              "11095",
}

#: ★ 显式排除并计数，不是「碰巧没映射上」。这些是走同一张报表的非 Amazon 渠道
#:   （Walmart / Chewy 等），不是这个 Amazon 店的需求。近 90 天 13 组。
EXCLUDED_CHANNELS = ("Non-Amazon",)

#: ★ A4PET_EUROPE 的这几个渠道近 90 天有单但没有 sid（OQ-3 未查清，es/fr/ie/it/
#:   pl/com.be，共 21 单）。这里只是留痕列出——不参与任何逻辑判断，`sid_for()`
#:   对它们本来就走「不在 STORE_SID 里」这条路径返回 None；`sid_for_or_raise()`
#:   仍然抛。**不许把它们悄悄并进 EXCLUDED_CHANNELS**——那样就从「认不出、硬
#:   失败」变成「排除、不计数」，OQ-3 要求的是前者。
UNRESOLVED: frozenset[tuple[str, str]] = frozenset({
    ("A4PET_EUROPE", "Amazon.es"), ("A4PET_EUROPE", "Amazon.fr"),
    ("A4PET_EUROPE", "Amazon.ie"), ("A4PET_EUROPE", "Amazon.it"),
    ("A4PET_EUROPE", "Amazon.pl"), ("A4PET_EUROPE", "Amazon.com.be"),
})

#: ★ 终审 I-1：**反方向的豁免名单**。`STORE_SID` 是按订单报表里出现过的
#:   `(store, sales_channel)` 组攒出来的，而真正的消费者是 `store_for(sid)` ——
#:   它的宇宙是「所有可能被认领的 sid」，也就是 `msku_bridge` 里的 sid。两个
#:   宇宙不一样，于是有三个真实的店从正向门禁底下整个漏了过去。
#:
#:   2026-09-23 实测（`lingxing_seller_list` 21 个 sid / `lingxing_product_listing`
#:   推出来的 msku_bridge）：
#:
#:     sid     店名                    msku_bridge   订单报表近 365 天
#:     11098   UNITFREE-GQ-P品牌-US        145 个        0 单
#:     11099   DWJ-A品牌-US                  0 个        0 单
#:     11100   A4Pet-BS-JP-JP               34 个        0 单
#:
#:   三个都是 `platform='amazon'` 的真店，它们的 179 个 msku 都能被认领 ——
#:   `source = "ch"` 一开，认领它们就是 503。
#:
#: ★ 为什么是豁免而不是补映射：`amazon_sp_api_report_all_orders` **有史以来**
#:   只出现过 5 个 store 值（PETSFIT_NORTH_AMERICA / PETSFIT_EUROPE /
#:   PETSFIT_ASIA / A4PET_NORTH_AMERICA / A4PET_EUROPE），没有一个属于这三家；
#:   该表也没有任何一列带 sid / seller / shop / account（2026-09-23 DESCRIBE 实测）。
#:   所以不是「忘了抄一行」，是这条取数链路上压根没有它们的数据 —— 硬编一行
#:   映射过去就是把别人家的销量记到它头上。
#: ★ 认领它们仍然 503（OQ-3：不猜一个 sid）。**这份名单不改变行为**，它改变的
#:   是「什么时候知道」：把上线当天的发现变成切换前就红的一条门禁
#:   （`tests/test_order_store_map_live.py`）。0 单是**采集/链路缺口的形状，
#:   不是业务的形状** —— 所以是「未知」，不能读成「卖了 0 件」。
NO_ORDER_REPORT_SID: dict[str, str] = {
    "11098": "UNITFREE-GQ-P品牌-US（美国）：msku_bridge 145 个 msku，订单报表 0 单",
    "11099": "DWJ-A品牌-US（美国）：msku_bridge 0 个 msku，订单报表 0 单",
    "11100": "A4Pet-BS-JP-JP（日本）：msku_bridge 34 个 msku，订单报表 0 单",
}

_SID_STORE = {v: k for k, v in STORE_SID.items()}
assert len(_SID_STORE) == len(STORE_SID), "两组 (store, channel) 指向了同一个 sid"
#: ★ 一个 sid 不能既「有映射」又「没有订单」—— 两边都占说明某一侧过期了。
assert not (set(NO_ORDER_REPORT_SID) & set(_SID_STORE)), (
    f"这些 sid 同时出现在 STORE_SID 与 NO_ORDER_REPORT_SID 里："
    f"{sorted(set(NO_ORDER_REPORT_SID) & set(_SID_STORE))}")


def _excluded(channel: str) -> bool:
    return any(channel.startswith(p) for p in EXCLUDED_CHANNELS)


def sid_for(store: str, channel: str) -> str | None:
    if _excluded(channel):
        return None
    return STORE_SID.get((store, channel))


def sid_for_or_raise(store: str, channel: str) -> str:
    sid = sid_for(store, channel)
    if sid is None:
        raise UnknownStore(
            f"(store={store!r}, sales_channel={channel!r}) 不在映射表里。"
            "★ 不猜 —— 猜一个 sid 就是把一家店的销量记到另一家头上。"
            "新开的店请补进 dim/order_store_map.py::STORE_SID")
    return sid


def store_for(sid: str) -> tuple[str, str]:
    key = str(sid)
    if key not in _SID_STORE:
        # ★ 已知缺口与「没人声明过」在留痕里必须分得开：前者是这条取数链路上
        #   真的没有数据（NO_ORDER_REPORT_SID，带实测出处），后者是该补一行。
        #   两者都硬失败、都 503，但运维要做的事完全不同。
        known = NO_ORDER_REPORT_SID.get(key)
        why = (f"这是已登记的取数缺口：{known}" if known else
               "新开的店请补进 dim/order_store_map.py::STORE_SID")
        raise UnknownStore(
            f"sid={sid!r} 在订单报表里没有对应的 store —— "
            f"这个店的销量取不到，不能当成「卖了 0 件」。{why}")
    return _SID_STORE[key]
