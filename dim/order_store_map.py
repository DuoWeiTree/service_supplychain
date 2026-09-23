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
    销量记到另一家头上，而两边各自都「没报错」。

    ★ 残留轮次：成因必须**随异常一起走**，不能只留在消息文本里。上层要按成因
    给出完全相反的建议 —— 「没人声明过」该去补一行映射；「已登记的取数缺口」
    绝不能去补（补了就是把别人家的销量记到它头上）。让翻译层去 `str(e)` 里
    捞关键词，就是把判据寄存在一句会被改写的中文上。
    """

    def __init__(self, message: str, *, sid: str | None = None,
                 registered_gap: str | None = None) -> None:
        #: 认不出的那个 sid（`store_for()` 抛时唯一已知量）；`sid_for_or_raise()` 抛时为 None。
        self.sid = sid
        #: 非 None ⇒ 这是 `NO_ORDER_REPORT_SID` 里**已登记**的缺口，值就是那条实测出处。
        #: None ⇒ 没人声明过这个 sid（真正该去补映射的那一种）。
        self.registered_gap = registered_gap
        super().__init__(message)


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

#: 登记当天（2026-09-23）各豁免 sid 名下的 msku 数，**给过期守卫用**。
#: ★ 一张永远不会变红的白名单，就是把「静默少一批货」重新请回来。所以豁免本身
#:   也要能过期（残留轮次裁定，option 3）。
NO_ORDER_REPORT_SID_MSKUS: dict[str, int] = {"11098": 145, "11099": 0, "11100": 34}

#: 允许的增长余量：相对 20%，外加 5 个的绝对底。超了就红，要求重新审视豁免。
#: ★ 出处（2026-09-23 实测，`lingxing_product_listing` 近 31 个采集日）：
#:   三个 sid 的 msku 数在**整个 31 天里一次都没变过**（145 / 0 / 34）——
#:   观测到的真实churn 是 **0%**。所以 20% 是**刻意留宽**的，它要抓的是
#:   「这家店变得实质更大了、豁免该重新审」，不是日常抖动。
#: ★ 为什么不设得更紧：日采会**整店掉档**（实测 11098 有 4 天、11100 有 2 天
#:   整店 0 行 —— 那是采集缺口的形状，不是业务的形状）。`SQL_MSKU_BRIDGE` 按
#:   30 天窗口取并集，掉档被邻日盖住了，所以窗口聚合值稳定；但把阈值压到贴着
#:   实测值会让这条守卫对采集侧的任何一次口径变化过度敏感，而一条会假红的
#:   守卫是一条会被人删掉的守卫（同 I-3 的教训）。
#: ★ 绝对底 5 是给 11099（今天 0 个 msku）留的：纯相对余量会让它一有 listing
#:   就红，而 1~2 个新 listing 更可能是上架试水而不是「这家店起来了」。
NO_ORDER_REPORT_GROWTH_RATIO = 0.20
NO_ORDER_REPORT_GROWTH_FLOOR = 5

#: 订单报表 `store` 列的**取值全集**（2026-09-23 实测，不限时间窗，5 个值）。
#: ★ 这是三条豁免成立的前提：没有任何一个 store 值属于 UNITFREE / DWJ /
#:   A4Pet-JP。出现第六个值就意味着「一个我们从没见过的账号族开始出单了」，
#:   而那正是「豁免的 sid 开始在订单报表里出现」唯一能被推导出来的信号 ——
#:   这张表里没有 sid 列，直接判定做不到（这也正是它们成为豁免的原因）。
ORDER_REPORT_STORES: frozenset[str] = frozenset({
    "PETSFIT_NORTH_AMERICA", "PETSFIT_EUROPE", "PETSFIT_ASIA",
    "A4PET_NORTH_AMERICA", "A4PET_EUROPE",
})


def no_sales_source(sid: str) -> str | None:
    """这个 sid **压根没有销量取数源**时，返回登记的实测出处；否则 None。

    ★ 与 `store_for()` 抛 `UnknownStore` 是同一张表的两个问法，区别在调用方要
    的东西：`store_for()` 要的是「给我 store」，答不上来就硬失败；这里要的是
    「这个 sid 是不是**已登记**的取数缺口」，答案是一条可以直接回显给人看的
    出处。★ 判据只有这一处，接口层不许自己再列一遍 sid ——
    硬编店铺号会让新增豁免时「dim 里加了、接口还说它未知」。
    """
    return NO_ORDER_REPORT_SID.get(str(sid))


def growth_budget(sid: str) -> int:
    """豁免 sid 的 msku 数上限（登记值 + 余量）。超了就该重新审视这条豁免。"""
    recorded = NO_ORDER_REPORT_SID_MSKUS[str(sid)]
    return max(int(recorded * (1 + NO_ORDER_REPORT_GROWTH_RATIO)),
               recorded + NO_ORDER_REPORT_GROWTH_FLOOR)

_SID_STORE = {v: k for k, v in STORE_SID.items()}
assert len(_SID_STORE) == len(STORE_SID), "两组 (store, channel) 指向了同一个 sid"
#: ★ 一个 sid 不能既「有映射」又「没有订单」—— 两边都占说明某一侧过期了。
assert not (set(NO_ORDER_REPORT_SID) & set(_SID_STORE)), (
    f"这些 sid 同时出现在 STORE_SID 与 NO_ORDER_REPORT_SID 里："
    f"{sorted(set(NO_ORDER_REPORT_SID) & set(_SID_STORE))}")
#: ★ 豁免名单与它的登记 msku 数必须逐个对上 —— 加了豁免却忘了登记基数，
#:   过期守卫就会在那一条上 KeyError 而不是安静放行（要的是前者）。
assert set(NO_ORDER_REPORT_SID) == set(NO_ORDER_REPORT_SID_MSKUS), (
    f"豁免名单与登记基数对不上：只在名单里 "
    f"{sorted(set(NO_ORDER_REPORT_SID) - set(NO_ORDER_REPORT_SID_MSKUS))}，"
    f"只在基数里 {sorted(set(NO_ORDER_REPORT_SID_MSKUS) - set(NO_ORDER_REPORT_SID))}")


def _excluded(channel: str) -> bool:
    return any(channel.startswith(p) for p in EXCLUDED_CHANNELS)


def sid_for(store: str, channel: str) -> str | None:
    if _excluded(channel):
        return None
    return STORE_SID.get((store, channel))


def sid_for_or_raise(store: str, channel: str) -> str:
    """★ 终审 M-4：报错文案分开三种成因。原先一律说「新开的店请补进 STORE_SID」，
    而对一个**显式排除**的非 Amazon 渠道，那是错的建议 —— 按它去做就会把
    Walmart / Chewy 的单记成某个 Amazon 店的销量，正是这张表存在的理由的反面。

    ★ 本函数目前生产无调用点（只有 `store_for()` 有）。留着而不删，是因为反向
    查询在接新平台时会需要它；但一个会给出错误建议的报错比没有这个函数更坏，
    所以先把文案修对。
    """
    sid = sid_for(store, channel)
    if sid is None:
        if _excluded(channel):
            why = ("这是**显式排除**的非 Amazon 渠道（EXCLUDED_CHANNELS）——"
                   "不许为它补一行 STORE_SID，那会把别的平台的单记成这个 Amazon 店的销量")
        elif (store, channel) in UNRESOLVED:
            why = "这是 design §8 OQ-3 已登记的缺口（有单但 lingxing_seller_list 里没有对应 sid）"
        else:
            why = "新开的店请补进 dim/order_store_map.py::STORE_SID"
        raise UnknownStore(
            f"(store={store!r}, sales_channel={channel!r}) 取不到 sid。"
            f"★ 不猜 —— 猜一个 sid 就是把一家店的销量记到另一家头上。{why}")
    return sid


def store_for(sid: str) -> tuple[str, str]:
    key = str(sid)
    if key not in _SID_STORE:
        # ★ 已知缺口与「没人声明过」在留痕里必须分得开：前者是这条取数链路上
        #   真的没有数据（NO_ORDER_REPORT_SID，带实测出处），后者是该补一行。
        #   ★ 成因随异常一起走（`registered_gap`），不留给上层去消息文本里捞
        #   关键词 —— 两者的处置**相反**，判据不能寄存在一句会被改写的中文上。
        known = NO_ORDER_REPORT_SID.get(key)
        why = (f"这是已登记的取数缺口：{known}" if known else
               "新开的店请补进 dim/order_store_map.py::STORE_SID")
        raise UnknownStore(
            f"sid={sid!r} 在订单报表里没有对应的 store —— "
            f"这个店的销量取不到，不能当成「卖了 0 件」。{why}",
            sid=key, registered_gap=known)
    return _SID_STORE[key]
