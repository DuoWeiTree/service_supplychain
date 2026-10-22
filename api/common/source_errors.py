"""取数源（ChSource）失败 → ApiError。api/ui 与 api/pub 共用这一份翻译。"""
from __future__ import annotations

from api.common.errors import ApiError
from dim.ch_source import ChDataUnusable, ChUnavailable, PurchaseTableStale, UnknownShape
from dim.order_store_map import UnknownStore

#: source_error 认识的全部类型 —— except 元组用它，不许各写一份
SOURCE_ERRORS = (ChUnavailable, ChDataUnusable, PurchaseTableStale, UnknownShape, UnknownStore)


def source_error(e: Exception) -> ApiError:
    """把 ChSource 的失败翻成 ApiError —— 同 jobs/refresh_dims.ChUnreachable → refresh_failed
    的形状，不另发明一种。★ 一律 503：拿不到数算出来的曲线看起来完全正常，S-29/S-30 的
    错误形状统一裁定同样适用于这条路径。"""
    if isinstance(e, ChUnavailable):
        return ApiError(503, "forecast_source_unavailable",
                        "预测取数源连不上，拒绝服务 —— 不拿旧数或空数冒充",
                        {"target": e.target, **e.cause})
    if isinstance(e, ChDataUnusable):
        return ApiError(503, "forecast_source_unusable",
                        "近 7 个采集日全都掉档或未采完 —— 这批数据不可用，不是「库存为 0」",
                        {"rejected": e.rejected})
    if isinstance(e, PurchaseTableStale):
        # ★ OQ-5 裁定：陈旧阈值突破 → 未知，不是 0；与「掉档守卫全拒」同一类失败，
        #   共用 forecast_source_unusable，不再单起一个错误码。
        # ★ F4（复核 09-23）：`stale_table` 点名是哪张表老了——行项表停了是
        #   没有新采购，单据表停了是状态冻住（还可能两张一起停，
        #   `dim.ch_source.BOTH_PURCHASE_TABLES`），处置不同。不加这个字段，
        #   两个成因相反的 503 body 会逐字节相同，拿到它的人得去翻日志才知道
        #   该查哪张——日志已经点名了，响应体不该比日志更瞎。
        return ApiError(503, "forecast_source_unusable",
                        "采购单快照太久没更新 —— 在途视为未知，不是 0",
                        {"captured": e.captured.isoformat(), "age_days": e.age_days,
                         "threshold_days": e.threshold_days, "stale_table": e.stale_table})
    if isinstance(e, UnknownShape):
        return ApiError(503, "forecast_source_unusable", "取数源出现认不出的形态",
                        {"detail": str(e)})
    if isinstance(e, UnknownStore):
        # ★ fix round 1：design §8 OQ-3 点名的真实缺口（6 组 (store, sales_channel)、
        #   近 90 天 21 单没有对应 sid）会从 daily_sales() → store_for()
        #   一路抛到这里。与 UnknownShape 同一类「认不出的形态」，不新开错误码；
        #   detail 里带着 store_for() 原样写出的 sid（它是唯一已知量——正因为它没有
        #   对应的 (store, channel) 才会抛这个异常）。
        # ★★ 残留轮次：这句 hint 原先对**所有** UnknownStore 都说「缺一行，去补」，
        #   包括 `NO_ORDER_REPORT_SID` 上那三个已登记的缺口 —— 而对它们，补一行
        #   就是硬编一行不存在的映射，把另一家店的销量记到它头上（正是 M-4 刚从
        #   `sid_for_or_raise` 拿掉的那条错建议，留在了人**先读到**的字段里）。
        # ★ 修法不是在这里再劈一支（那一支没有调用路径，而没有路径的分支没人能
        #   信）。分派放在 `usecase/plan_claim.seed_forecast` 的 `except UnknownStore` 里，按
        #   `e.registered_gap` 把「已登记的缺口」翻成 200 + 不适用 —— 所以走到
        #   这里的**只有「没人声明过」那一种**，这句「去补一行」也就只对着它该
        #   对的人说。改那处分派的人要连这句话一起重新想。
        return ApiError(503, "forecast_source_unusable",
                        "这个店没有声明取数映射（dim/order_store_map.py 缺一行）"
                        "—— 销量取不到，不能当成「卖了 0 件」",
                        {"detail": str(e), "sid": e.sid})
    # ★ 调用方只该拿 SOURCE_ERRORS 里的类型来调；走到这里说明 except 元组与本函数脱了节
    raise e   # ★ 认不出的异常不许被兜成这五种之一——同 CLAUDE.md「不许 else 兜底」
