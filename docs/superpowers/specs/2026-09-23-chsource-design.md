# ChSource · 阶段 A 预测取数接真 CH · 设计（2026-09-23）

> 范围：把 `dim/ch_source.py::ChSource` 四个 `NotImplementedError` 换成真取数，
> 让 `api/ui/plans.py:32` 从 `FixtureSource` 换成 `ChSource`。
> **不改 `dim/source.py` 的协议**（四个签名与返回类型固定）。

---

## 1. 背景与事实（全部带出处；CH 证据均为 2026-09-23 实测）

### 1.1 继承自兄弟仓 `model_inventory_forecast` 的四条铁律（不再论证，照搬判据）

| # | 铁律 | 出处 |
|---|---|---|
| L-1 | **全集用「商品目录 LEFT JOIN 快照」，不能直接查快照。** Amazon 的 FBA 库存报表不返回库存为 0 的 SKU，直接查快照会让完全断货的货号整个消失 —— 而断货正是最该被看见的状态 | 兄弟仓 `CLAUDE.md`「取数的四条铁律 1」· `data/mappings/inventory_forecast.ch.toml:31-42` |
| L-2 | **msku→货号按 as_of 取绑定，但 `sid` 绝不参与 `argMax`。** 同一 msku 字符串在不同店铺下是不同 listing；listing 日采不完整，argMax 掉 sid 会让归属取决于「最后一次采到它时采的是哪个店」 | 同上「铁律 2」· `inventory_forecast.ch.toml:342-363` |
| L-3 | **订单是滞后采集的**，近几天销量会被持续追加（兄弟仓实测首采 0.5%~62%，1~4 天才稳；`base_rate` 系统性低估约 23%，**未修**） | 同上「铁律 3」 |
| L-4 | **库存日报会整天缺、会掉档、会重复行。** 查任何日报必须 ① 先按 `_captured_date` 去重；② 比对相邻两日的行数与 SKU 覆盖数，掉档超阈值就当这批不可用，而不是把缺失读成 0 | 同上「铁律 4」 |
| L-5 | **FBA 库存按店铺一律用 `lingxing_inventory_fba_detail`**（带 `sid` + 货号）；**不要用** `amazon_sp_api_report_fba_inventory` —— 它按 `store` 聚合，`A4PET_EUROPE` 把 UK/DE/PL/FR/IT 混成一行 | 同上「其它约定」· 本仓 `docs/08-后端接口清单.md` §2.3 |
| L-6 | `amzn.gr.%` 是虚拟促销组，不是真 listing；排除且**必须计数**。头程在途 / 海外仓在仓**不进** X | `inventory_forecast.ch.toml:62,101,146` · `dim/ch_source.py:187-191` · 记忆 `first-leg-goods-invisible` |

### 1.2 本仓的约束（代码实测）

| # | 事实 | 出处 |
|---|---|---|
| B-1 | 协议固定：`as_of() -> date`、`monthly_sales_history(seller_sku, sid, months) -> list[(date,int)]`、`onhand_available(seller_sku, sid) -> int\|None`、`purchase_in_transit(sku) -> list[InTransit]` | `dim/source.py:18-31` |
| B-2 | **`dim/` 不许 import `shared.ch_client`**（`NO_CONNECTIONS`）。既有模式：`dim/` 只装 SQL 文本 + 纯变换，客户端由调用方注入 | `tests/test_layering.py:53-55,114-127` · `dim/ch_source.py:33-35` |
| B-3 | `dim/` 里出现任何写动词（含注释字符串）都会让 L7 红；`shared/ch_client.py` 已给只读 `query()` + 三问日志（`kind` 区分超时与连不上） | `tests/test_layering.py:138-143` · `shared/ch_client.py:45-56,106-154` |
| B-5 | grid 逐 (msku,sid) 调 `onhand_available`、逐 sku 调 `purchase_in_transit`，`as_of` 调一次 | `api/ui/plans.py:322-338` |
| B-6 | `has_fba=false` 的店根本不会走到 `onhand_available` —— 调用方先按 PG `seller.has_fba` 闸住 | `api/ui/plans.py:333` |
| B-7 | `monthly_estimate` 对**中间缺月**抛 `HistoryGap`、对同月多行抛 `ValueError`、对空历史抛 `InsufficientHistory` | `forecast/estimate.py:35,46-57` |
| B-8 | 掉档阈值配置键已存在：`[freshness] coverage_drop_threshold = 0.30`；陈旧/不可用一律 503 + 具名错误码，不许 200 兜底 | `config.example.toml:38-40` · `api/ui/deps.py:36-38` |

### 1.3 CH 实测证据（2026-09-23，只读）

| # | 证据 | 数字 |
|---|---|---|
| E-1 | `lingxing_inventory_fba_detail` 覆盖面**极稳**：近 20 个采集日 7,934~8,080 行、**每日恰好 21 个 sid**、~5,300 个 seller_sku；且**无重复行**（最新采集日 (sid, seller_sku) 全部 n=1，8,080/8,080） | 日间波动 < 2% |
| E-3 | 该表采集**当天 06:34~07:39 开始、约 60 秒跑完**（9/20~9/22 为 06:34，9/23 为 07:38） | 而 `[freshness] refresh_at = "06:30"` 排在它**之前** |
| E-4 | ★ 该表含 **`sid = 0`：1,114 行 / 14,073 件可售**，`name='PETSFIT-JXD-UK欧洲仓'`、`seller_group_name='PETSFIT-JXD-PL,PETSFIT-JXD-SE'` —— 欧洲共享池，不是任何单店 | 占全部可售 50,947 件的 **27.6%** |
| E-5 | `available_total ≠ afn_fulfillable_quantity` 的行有 **972/8,080（12%）**（样本 1,567 vs 1,323），`total = available_total + inbound`；该表 `sku` 列 **1,932/8,080（23.9%）为空**，绝不能按 `sku` 关联 | —— |
| E-7 | PG `msku_bridge` 口径下 sid 11072 有 1,257 个 msku，其中 **7 个（0.6%）在最新快照里没有行** —— 这 7 个正是 L-1 说的「应当是 0，不是消失」 | —— |
| E-8 | ★ **`amazon_sp_api_report_all_orders` 没有 `sid` 列**，只有 `store`；**全库没有任何一张表同时有 `store` 与 `sid`** | 桥只能自己声明 |
| E-9 | ★ 该表有**重复行**：8,980 组 (amazon_order_id, order_item_id) 出现 2 次，跨采集日值会变（`Pending → Shipped`）。去重前后差：2026-08 **13,611 → 11,906（−12.5%）**、2026-09 **11,331 → 9,130（−19.4%）**；2026-06/07 **不变** | 近月被系统性高估 |
| E-10 | 去重后与**另一个独立源** `lingxing_multiplatform_sales_stats`（按 sid 直接聚合）对上：sid 11072，2026-06 15,209 vs 15,218（0.06%）、07 12,857 vs 12,928（0.55%）、08 11,906 vs 12,137（1.9%） | 两源互证 |
| E-11 | `lingxing_multiplatform_sales_stats` **不能当主源**：日采每天只覆盖 300~360 个 msku / 3~8 个 stat_date，历史只靠 2026-09-01 与 09-23 两次回填，`stat_date` 最早 2026-01-27 | 覆盖面不足 |
| E-12 | ★ **采购单 `status=9（已完成）` 的行项 `quantity_receive` 全为 0**：966 行 / 104,479 件。只按 `quantity_real − quantity_receive > 0` 取在途会凭空多出 104,479 件，是真实开口量 **28,561 件的 3.7 倍** | 必须按 `status` 过滤 |
| E-13 | 真实开口在途（`status=2 待到货`）：166 行 / **28,561 件** / 41 个货号，全部来自 **2 张采购单**（PO260514011、PO260721011）。按 `expect_arrive_time` 分月：2026-06 10,199 · 07 6,594 · 08 6,098 · 09 5,325 · **10 仅 345**，**10 月之后为 0** | 绝大多数已逾期 |
| E-14 | `expect_arrive_time` 有 **1 行为 NULL**（50 件）。两张采购表是覆盖型，只有 4 个采集日（2026-09-19~22），`max(_captured_date)=2026-09-22`，比 fba_detail 晚一天 | 认不出的形态 + 两个 as_of |
| E-16 | `(store, sales_channel)` 近 90 天共 **37 组**，其中 Amazon 渠道 **24 组：18 组能映射到 sid**，**6 组映射不到**（`A4PET_EUROPE` 的 es/fr/ie/it/pl/com.be —— A4Pet 欧洲只有 UK 11094 与 DE 11095 两个 sid，共 21 单）；另有 **13 组** `Non-Amazon*` | 未映射必须点名 |
| E-16a | ★ **本表初稿漏了 `(A4PET_EUROPE, Amazon.de) → 11095`**（Task 4 实现时按 live 查出：`lingxing_seller_list` 里 11095 = `A4Pet-BS-DE`、德国、在用，近 90 天数百单）。它**不是** OQ-3 那 6 组无主渠道——那 6 组在 `seller_list` 里根本没有行。漏掉的后果不是报错而是**这家店的销量历史永远取不到、界面永远偏低**。18/6/13 合计 37 已复核 | 声明表必须有回归测试钉住（`tests/test_order_store_map.py`） |
| E-17 | 同一 marketplace 下 msku 几乎唯一：US 四个 sid（11072/11093/11098/11099）共 1,589 个 msku，**只有 2 个跨 sid**（`DHWC007036Y1Z2B`、`DHWC007036N1Z2B`，均 11072∩11098）| 0.13% |
| E-18 | 全快照聚合查询实测 **23 ms**（8,080 行 / 21 个 sid）；单店 16 ms。`order_status` 只有 Shipped / Shipping / Cancelled / Pending，Pending 在已完结月份残留很少（2026-07 为 165/25,772） | 批量与单查同价 |

---

## 2. 目标 / 非目标

**目标** ① 四个方法真取数，口径每一条都带上面的编号；② 每个数都能回答「它是关于什么的」（`as_of` 随数走）；
③ **未知 / 0 / 不适用三者永远分得开**；④ 任何过滤与 join 都统计被丢弃的那一侧，认不出的形态点名；
⑤ 每次外部调用留三问日志；⑥ fixture 路径逐字节不动，离线仍全绿。

**非目标** 不改 `dim/source.py` 协议（B-1）；不新建 PG 镜像表；不做需求在同 ASIN 货号间的分摊；
不接非 Amazon 平台销量；不修 L-3 的滞后偏差本身（见 §8 OQ-1）。

---

## 3. 裁定：**逐查询取数，不做镜像**

四张 DIM 镜像是**慢变维度**（小、被外键引用、需要事务一致）。预测事实是**带时点的量**——
`basis.as_of` 必须随每个数走（`api/ui/plans.py:349`）。把它镜像进 PG 会立刻出现**两个真相**：
镜像表里的「在仓」和它自己的刷新时钟，与 CH 快照的 `_captured_date` 不是同一件事。

且成本上不需要：E-18 实测整快照聚合 23 ms，与单店 16 ms 同量级 —— **批量读一次比逐个读便宜**。

### 3.1 每个查询：走 PG 镜像还是 CH

| 用途 | 走哪边 | 理由 |
|---|---|---|
| 全集（哪些 msku 要出数） | **PG `msku_bridge`** | 它已经是认领的闸门（`plans.py:127-133`），也已按 L-2 刷好。在 CH 再做一次 `argMax` 就是第二个真相 |
| `has_fba` / 不适用判定 | **PG `seller`** | B-6，调用方已在用 |
| FBA 在仓快照 | **CH** `lingxing_inventory_fba_detail` | 带时点，必须随 as_of |
| 月销量 | **CH** `amazon_sp_api_report_all_orders` | 无 PG 镜像（`sales_actual` 属阶段 D，登记表里 `pending`） |
| 采购在途 | **CH** 采购单两表 | 无 PG 镜像（`po_snapshot` 属阶段 B，`pending`） |

★ L-1 的「目录 LEFT JOIN 快照」在本仓**跨库**实现：左侧是 PG 的 `msku_bridge`（调用方持有），右侧是 CH 快照
（ChSource 持有）。ChSource 不查 PG —— 它对一个已被认领、快照里没有的 msku 给 `0`，LEFT JOIN 的语义由这条承担。

### 3.2 客户端怎么注入（B-2）

`dim/` 只拿一个 `Query = Callable[[str], list[tuple]]`：

```python
class ChSource:
    def __init__(self, query: Query, *, now: Callable[[], dt.datetime] = ...,
                 drop_threshold: float = 0.30, cache_ttl_s: int = 300) -> None: ...
```

装配在 `api/ui/source_factory.py`（`api/` 层，允许 import `shared.ch_client`）：
`config.toml [forecast] source = "ch" | "fixture"` 决定造哪个；CH 客户端**惰性**建连
（import 时不连），第一次真查询时才 `ch_client()`，并缓存在闭包里。
`api/ui/plans.py:32` 因此仍然只改**一行**：`SOURCE = make_source()`。

---

## 4. 四个方法的口径

### 4.1 `as_of()` —— 裁定：**取通过掉档守卫且已采完的最新 `_captured_date`**

候选 = `lingxing_inventory_fba_detail` 近 7 个采集日。逐日从新到旧判两条，第一个通过的即 as_of：

1. **已采完**：`max(_captured_at) < now() - 30min`。依据 E-3：该表当天 06:34~07:39 起跑、约 60 秒跑完，
   而刷新调度就在 06:30 —— 半写窗口真实存在。30 分钟 = 实测时长的 30 倍。
2. **没掉档**：`rows ≥ (1−0.30) × 上一个被接受日的 rows` **且** `uniq(sid) ≥ (1−0.30) × 上一日的 uniq(sid)`。
   依据 E-1（日间波动 < 2%，21 个 sid 恒定）与 L-4。阈值复用 B-8 的 `coverage_drop_threshold`。

★ **不采用「固定 today−2」**（兄弟仓记忆 `compute-two-days-back`）：那条是为**订单**的整日采集跨度定的，
而 as_of 标注的是**库存快照**，E-3 证明它每天一分钟内写完。用守卫比用常数多测一件事、少扔 16 小时好数据。
每次回退都打 WARNING 并点名被拒日与它的行数/sid 数 —— **回退可以，必须有声**。

七日全不通过 → 抛 `ChDataUnusable`（§6）。

### 4.2 `monthly_sales_history(seller_sku, sid, months)`

源：`amazon_sp_api_report_all_orders`。四个口径决定：

1. **按 (amazon_order_id, order_item_id) 去重**，取 `argMax(_captured_date)` 的那一版状态与数量。
   依据 E-9：不去重时近月被高估 12.5%~19.4%。去重后与独立源对上（E-10）。
   ★ 兄弟仓那条 SQL 没有这一步（`inventory_forecast.ch.toml:129-155`）—— 这是本仓实测追加的。
2. **`store`→`sid` 用声明的字面映射表** `dim/order_store_map.py`，**18 组**（E-16 + E-16a：初稿漏了 Amazon.de→11095）。
   不解析店名、不猜。**任何近 90 天出现过、却不在映射且未被显式声明排除的 (store, sales_channel) 一律硬失败**
   （守卫测试见 §8）。`Non-Amazon*` 14 组显式排除并计数 —— 它们不是这个 Amazon 店的需求。
   ★ 不用「marketplace + msku 唯一」这条近路：E-17 实测仍有 2 个 msku 跨 sid，0.13% 的静默错账。
3. **只返回完整自然月**：窗口 = `[as_of 所在月 − months, as_of 所在月)`。当月绝不入选 ——
   E-9 的 2026-09 只有 9,130 件 vs 8 月 11,906 件，当月混进去会被读成 23% 的下滑。
4. **窗口内的空月补 0，并计数**；窗口起点若早于该 store 的 `min(purchase_date)`，**截短窗口并打日志**，
   不把「没覆盖」补成 0。依据 B-7：`monthly_estimate` 对中间缺月抛 `HistoryGap`，而「这个月一件没卖」
   与「这个月没数据」是两件事，只有后者允许改变窗口长度。一条都没有 → 返回 `[]`（协议：不补 0）。

**L-3 滞后：本期不修，但要说得出来。** 去重（口径 1）已消掉「重复采集」那一成因，残余的「真·晚到订单」
无法用单次快照与它分开（§8 OQ-1）。所以 `POST /plans/{id}/claims` 的响应**追加 `history_window`**
（用了哪几个月、哪些是补 0、有没有被截短、最新那个月距今多少天），界面据它说明这组数的来历。
**没有标记的数比没有数更坏**，这个标记是它能落地的地方。

### 4.3 `onhand_available(seller_sku, sid)` —— 一次批量，按 as_of 缓存

```sql
SELECT toString(sid) AS sid, seller_sku,
       toInt64(sum(afn_fulfillable_quantity)) AS units, count() AS raw_rows
  FROM jxd_raw.lingxing_inventory_fba_detail
 WHERE _captured_date = {as_of:Date} AND sid != 0
 GROUP BY sid, seller_sku
```

- **列裁定：`afn_fulfillable_quantity`**（= 可售）。不用 `available_total`（E-5：12% 的行更大，含预留/不可售），不用 `total`（含 inbound，而 inbound 在 grid 里是另一列）。
- ★ **`sid = 0` 排除并计数**（E-4）：它是欧洲共享池（PL+SE 合池），不是任何单店的在仓，
  折进任何一个 sid 都是凭空给它 14,073 件。这一条**必须出现在日志里**——
  占全部可售 27.6% 的一批货被排除，不留痕就再也没人想得起来它去哪了。
- `GROUP BY + sum()` 而不是取单行：E-2 今天一行不重，但重复行是 L-4 点名的形态；
  同时记 `rows_collapsed = raw_rows − 1`，真开始重复的那天看得见。
- **0 与未知**：批次被接受 ⇒ 认领中的 msku 在快照里没有行 = **`0`**（L-1、E-7 的那 7 个）。
  批次不可用 / CH 连不上 ⇒ **抛异常**，由调用方呈现「未知」，**绝不返回 0**。
  `has_fba=false` 的「不适用」不经过本方法（B-6），原样不动。

### 4.4 `purchase_in_transit(sku)`

```sql
WITH o AS (SELECT order_sn, argMax(status, _captured_date) AS st
             FROM jxd_raw.lingxing_purchase_order_list GROUP BY order_sn)
SELECT i.sku, formatDateTime(i.expect_arrive_time, '%Y-%m') AS period,
       toInt64(sum(i.quantity_real - i.quantity_receive)) AS units, i.order_sn
  FROM jxd_raw.lingxing_purchase_order_list_items AS i
 INNER JOIN o ON o.order_sn = i.order_sn
 WHERE i._captured_date = {purchase_as_of:Date} AND o.st = 2
   AND ifNull(i.is_delete, 0) = 0 AND i.quantity_real > i.quantity_receive
 GROUP BY i.sku, period, i.order_sn
```

- ★ **必须按 `o.st = 2（待到货）` 过滤，不能只靠 `real > receive`**：E-12 实测 `status=9（已完成）`
  的行项 `quantity_receive` 全为 0，只按算术会多出 104,479 件 —— 真实开口量的 3.7 倍。
  `status=-1（已作废）` 同样显式排除并计数（468 行 / 39,375 件），不依赖「它恰好净为 0」。
- **算什么**：已下单、未到货的采购单行项，数量 = `quantity_real − quantity_receive`，`ref = order_sn`，
  `period` 取 `expect_arrive_time` 的月份。
- **刻意不算**：FBA 入库在途（`afn_inbound_*`）、`lingxing_fba_shipment_list` 货件、仓间调拨、海外仓入库单。
  依据 L-7 与 `api/ui/plans.py:355-358` —— 采购在途是**货号级**、无店铺归属，阶段 A 恒不进任何店的加项。
- `expect_arrive_time` 为 NULL 的行（E-14，1 行 / 50 件）：**丢弃 + WARNING 点名 order_sn 与件数** +
  计入响应的 `sku_pipeline_excluded`。不许落进 `else ''` 再被下游过滤掉。
- **`purchase_as_of` 用采购表自己的 `max(_captured_date)`**（E-14：比 fba_detail 晚一天）。强行对齐会在
  采集没跑的那天返回空，与「真的没有在途」长得一样。它随每条 `sku_pipeline` 出去，`as_of()` 仍是库存快照日。
- ★ **必须让人知道「今天几乎一件都不落在计划窗口内」**：E-13 实测 28,561 件开口在途里 2026-10 只有 345 件、
  之后为 0，整批只来自 2 张采购单。10 月起的计划看到的在途接近空 —— 这是**数据的形状**，不是「没取到」。

---

## 5. 缓存与批量

grid 一次请求实测调用次数（B-5）：`as_of` 1 次 + `onhand_available` N 次（N = 认领的 msku 数）
+ `purchase_in_transit` M 次（M = 涉及的货号数）。**10 个 msku / 6 个月的计划 ⇒ 1 + 10 + 10 = 21 次**。

裁定：**ChSource 内部按快照日批量取一次并缓存，对外仍是协议里那四个单点方法。**

- 缓存键 = **解析出来的快照日**。同一 `_captured_date` 的快照不可变（E-1/E-3：一天写一次、60 秒写完），
  所以按天缓存是**正确**而不只是省事；日期一变整份丢弃重建（不合并 —— 记忆 `defaults-preserve-staleness`）。
- `as_of()` 的解析结果另缓存 `cache_ttl_s`（默认 300 秒）；销量按 `(sid, 窗口)` 批量并缓存同键。
- 效果：21 次 → **3 条 SQL**（as_of 候选 / 在仓批量 / 采购批量），实测 23 ms + 16 ms 量级。
- 缓存命中率与重建次数打进 `op=ch_source_cache` 日志 —— 没有留痕的缓存查不出「为什么两次结果不一样」。

---

## 6. 失败行为

| 情形 | ChSource | 端点 | 日志 |
|---|---|---|---|
| CH 连不上 / 超时 | `ChUnavailable`，带 `describe_failure(e)`（`kind` 区分 timeout / connect_refused / dns） | **503 `forecast_source_unavailable`** + `{kind, target, elapsed_ms}` | `ch_client` 三问 + `op=ch_source outcome=fail` |
| 表/列不存在 · `(store, sales_channel)` 认不出 | `UnknownShape`，点名表/列/那一组 | **503 `forecast_source_unusable`** | ERROR，点名 |
| 七日候选全被掉档守卫拒 | `ChDataUnusable`，带每个被拒日的 rows/uniq_sid | **503 `forecast_source_unusable`** + 被拒日与数字 | 每次回退一条 WARNING |
| 批次被接受、这个 msku 没行 | 返回 **`0`** | 200，`closing` 有真数 | DEBUG 计数 |
| 店铺 `has_fba=false` | **不经过 ChSource**（B-6） | 200，`closing_reason = "not_applicable"` | —— |

★ **一律 503，不降级成 200**（同 B-8 的 `mirror_stale`）：拿不到数算出来的曲线看起来完全正常。
★ **没有静默兜底**：任何回退（as_of 退一天、窗口截短、行被丢弃）都必须有日志 + 计数，且计数出现在响应里。
★ 控制器在途的 `closing_reason = "unknown_onhand"`（有 FBA 但拿不到数）与 `not_applicable`（没有 FBA）
的区别，由「0 只在批次被接受时给，否则抛」保住 —— ChSource **永远不用 0 冒充未知**。

---

## 7. 与 FixtureSource 的对账

- `tests/fixtures/` 的 5 个假 msku 与 `as_of.txt` **一个字节都不动**；`FixtureSource` 一行不改。
  `[forecast] source = "fixture"` 是测试与离线的默认值，`tests/test_grid_fixture.py`、
  `tests/test_dim_fixture.py`、`tests/test_api_grid.py` 全部照旧绿。
- 新增一条**形状对账**：对同一组输入，`ChSource` 与 `FixtureSource` 的返回**类型与结构**逐字段相同
  （`as_of` 是 `date`、`monthly_sales_history` 是升序且月初、`onhand_available` 是 `int|None`、
  `purchase_in_transit` 是 `InTransit` 且 `period` 形如 `YYYY-MM`）。
- **真值对账**（live）：msku `DCC1800264G1Z2B` / sid `11072`。冻结两个已完结月份：
  **2026-06 = 561、2026-07 = 254**（2026-09-23 实测，去重后；去重前 2026-08 是 370、去重后 318）。
  在仓：该 msku 2026-09-23 快照 `afn_fulfillable_quantity = 672`（09-22 为 615、09-21 为 605）。
  测试用**另写一条** SQL 手算，与 ChSource 的输出比对 —— 同一条 SQL 自己对自己不算对账。

---

## 8. 开放问题

> ★ 下面六条已由控制器在 2026-09-23 预飞（preflight）裁定，原文见
> `.superpowers/sdd/2026-09-23-chsource/progress.md` §「Rulings」，本节只誊写理由、
> 不重新论证。**裁定覆盖本节此前写的任何「现状」**；仍未裁定的部分单独标「留阶段 B 实调」，
> 阶段 A 一律先硬失败或先排除，不得自行猜一个答案垫上。

| # | 问题 | ★ 裁定（控制器 09-23） |
|---|---|---|
| OQ-1 | L-3 的**真·晚到订单**占比无法与「重复采集」分开：单次快照只能看到 `_captured_date`，看不到同一行的历史值。去重已消掉后者（E-9/E-10），前者未测 | **不猜补偿。** 只取**完整月**（当月不计入），响应里带口径标记（`history_window`）；UI 必须能说出「近 N 天可能偏低」——一个低 23% 却不带标记的数字比没有数字更坏（邻仓 CLAUDE.md 铁律 3 未修，我们不重蹈）。真·晚到订单占「重复采集」以外还剩多少，**留阶段 B 实调**。 |
| OQ-2 | `sid = 0`（欧洲共享池，14,073 件 / 27.6%）**属于谁**未裁定。阶段 A 排除，但 PL/SE 两店的在仓因此**系统性偏低** | **不分摊给任何店铺**——按单店给全额与平摊都是发明分配规则（C4 禁，同在途）。但 PL/SE 因此系统性偏低这件事**必须看得见**：响应带 `shared_pool_excluded` 计数，不许静默。归属（该记到哪个主体、要不要按比例摆回去）**留阶段 B 实调**（与记忆「平台需求由共用池满足」/「欧洲是共享池」同一条） |
| OQ-3 | `A4PET_EUROPE` 的 6 组 Amazon 渠道（es/fr/ie/it/pl/com.be，近 90 天 21 单）**没有对应 sid**：是真没开店还是 `lingxing_seller_list` 缺行，未查清 | **计数并点名，不许静默丢弃**（房子规矩：统计被丢掉的那一侧）。是「没有店」还是「缺行」**留阶段 B 实调**；阶段 A 一律硬失败（`UnknownStore`），不猜一个 sid 垫上 |
| OQ-4 | `available_total` 与 `afn_fulfillable + reserved + unsellable + researching` 对不上（1,567 vs 1,403），差额来自哪几项未拆开 | **只读我们要的那个字段，禁止用总量相减凑数**（邻仓 CLAUDE.md 同条铁律）。差额从哪几项来**不解释就不用它**——不影响本设计，因为本设计只读 `afn_fulfillable_quantity`，不碰 `available_total` |
| OQ-5 | 采购单只有 4 个采集日（2026-09-19 起）且是覆盖型，**没有历史快照**（`docs/01:94`）。某天没采时 `purchase_as_of` 会停住，而「停多久算太久」没有阈值 | **设陈旧阈值，超了整批不可用 →「未知」，不是 0。** 与 as_of 的掉档守卫同一条理由：拿不到数算出来的曲线看起来完全正常，静默兜底是最坏的一种。阈值取多大**留阶段 B 用真实采集节奏校准**，阶段 A 先给一个配置化的保守默认值（见 Task 3） |
| OQ-6 | `expect_arrive_time` 大面积逾期（E-13：28,216/28,561 件的预计到货月已过去）。在途按什么规则重排到未来月份，**未裁定** | **不重新定日期**（猜一个新日期就是发明数据）。归入「逾期/未确认」单列展示，**绝不静默并进未来某个月**。展示规则本身（单独一栏 / 单独一个总量牌）**留阶段 B 与前端一起定**，阶段 A 只要求「不许悄悄消失、不许悄悄挪月」两条底线成立 |
