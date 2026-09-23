"""假 CH 返回。★ 每组都带一个「长得像 0 其实不是」的形态。"""
import datetime as dt

D1, D2 = dt.date(2026, 9, 20), dt.date(2026, 9, 21)

SKU_CATALOG = [("DCC1800264G1", "猫爬架", D2), ("", "无名氏", D2)]          # 空货号要被丢掉

#: ★ OQ-3 裁定（09-22）：SQL_SELLER 已 join lingxing_product_listing 派生 has_fba_flag。
#:   controller 09-22 进一步裁定（探针 §7.0 实测后）：lingxing_seller_list 没有
#:   marketplace/platform 列（真实是 region/country/marketplace_id）——
#:   market 改用 country；platform 该表压根没有，不再是源列。假行按新
#:   SQL_SELLER 的 5 列排布：(seller_id, name, country, captured, has_fba_flag)。
#:   11072 名下有条 FBA listing、11094 没有。
#: ★ 09-23 真实缺陷修复：country **不是**代码——2026-09-23 实测
#:   `lingxing_seller_list.country`（21 个 sid 全量）给的是中文国名（美国/英国/…），
#:   不是 "US"/"UK"。这两行原先直接写英文码，等于假装 `fetch_seller` 已经做了
#:   翻译——真正没做，之前测不出来正是因为这里数据不真实。改成真实的中文国名，
#:   `fetch_seller` 必须把它们译成 001_foundation.sql:27 约定的代码。
SELLER = [("11072", "A4Pet-US", "美国", D2, 1),
          ("11094", "A4Pet-BS-UK", "英国", D1, 0)]

#: ★ 控制器 09-22 追加裁定：seller_sku 形如 amzn.gr.* 是亚马逊虚拟促销组，
#:   不是真 listing，必须丢弃且计数（不是静默滤掉的那一侧）。
MSKU_BRIDGE = [("MSKU-A", "11072", "DCC1800264G1", D2),
               ("MSKU-A", "11094", "DCC1800264G1", D2),   # ★ 同串跨店 = 两个 listing
               ("MSKU-Z", "11072", "", D2),                # 未绑货号要被丢掉
               ("amzn.gr.9001", "11072", "DCC1800264G1", D2)]  # 虚拟促销组要被丢掉
WAREHOUSE = [(1, "11-北美亚马逊东孚仓", 1, 0, D2),
             (2, "30-LA自有海外仓", 3, 1, D2),
             (3, "A4pet英国仓", 3, 2, D2)]
WAREHOUSE_UNKNOWN = WAREHOUSE + [(9, "说不清是什么仓", 7, 7, D2)]


def replay(rows):
    """把一组固定行喂给 fetch_*，无视它发的 SQL。"""
    return lambda sql: list(rows)
