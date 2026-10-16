-- 017 · 排货量视图与核对：承重墙② 的「求和函数 + 恒等式」（CLAUDE.md 架构铁律 2）
-- 出处：spec 2026-10-10-dispatch-draft-design §3③ / §4 · 02 §3.1 · 04 §1.2.2 · CA-D2
-- ★ 求和一律经视图（03 §6）；已放弃 / 已取消的排货单不进任何量，映射本身不删（04 §7.6）。

-- 每条记录：已排未发（草稿 / 已确认）· 已投送（已投送 / 已归档）· 待排。★ 无计划来源不进任何记录
CREATE OR REPLACE VIEW v_plan_line_dispatch_qty AS
SELECT l.line_id, l.total_units, l.state,
       coalesce(sum(m.units) FILTER (WHERE c.our_stage IN ('草稿','已确认')), 0)::int AS staged_units,
       coalesce(sum(m.units) FILTER (WHERE c.our_stage IN ('已投送','已归档')), 0)::int AS dispatched_units,
       (l.total_units
        - coalesce(sum(m.units) FILTER (WHERE c.our_stage IN ('草稿','已确认','已投送','已归档')), 0))::int
           AS pending_units
  FROM plan_line l
  LEFT JOIN dispatch_arrangement a ON a.line_id = l.line_id
  LEFT JOIN dispatch_source_map m ON m.arrangement_id = a.arrangement_id
  LEFT JOIN consignment c ON c.consignment_id = m.consignment_id
 GROUP BY l.line_id, l.total_units, l.state;

-- 已承诺未发出（跨全部排货表）：货号 × 来源仓 × 店，计划 / 无计划分列（G3 按桶判可行的输入，CA-D8）
CREATE OR REPLACE VIEW v_stock_committed AS
SELECT a.sku, a.from_wid, a.sid,
       coalesce(sum(m.units) FILTER (WHERE a.line_id IS NOT NULL), 0)::int AS planned_units,
       coalesce(sum(m.units) FILTER (WHERE a.line_id IS NULL), 0)::int     AS unplanned_units,
       sum(m.units)::int                                                    AS units
  FROM dispatch_source_map m
  JOIN dispatch_arrangement a USING (arrangement_id)
  JOIN consignment c ON c.consignment_id = m.consignment_id
 WHERE c.our_stage IN ('草稿','已确认')
 GROUP BY a.sku, a.from_wid, a.sid;

-- ① 安排与映射对不上（含：草稿表里有安排却没派生出映射 —— 派生漏了）
CREATE OR REPLACE VIEW v_check_dispatch_map AS
SELECT a.sheet_id, a.arrangement_id, a.units AS arrangement_units, m.units AS map_units
  FROM dispatch_arrangement a
  JOIN sheet s USING (sheet_id)
  LEFT JOIN dispatch_source_map m USING (arrangement_id)
 WHERE s.state <> '已取消'
   AND (m.arrangement_id IS NULL OR m.units <> a.units
        OR (m.seller_sku, m.sid, m.from_wid) IS DISTINCT FROM (a.seller_sku, a.sid, a.from_wid));

-- ② 排货行 ≠ Σ映射（= Σ计划来源 + Σ无计划来源，CA-D2 的恒等式）
CREATE OR REPLACE VIEW v_check_consignment_line_sum AS
SELECT l.consignment_id, l.seller_sku, l.sid, l.from_wid, l.units AS line_units,
       coalesce(sum(m.units), 0)::int AS map_units
  FROM consignment_line l
  LEFT JOIN dispatch_source_map m
         ON (m.consignment_id, m.seller_sku, m.sid, m.from_wid) = (l.consignment_id, l.seller_sku, l.sid, l.from_wid)
 GROUP BY l.consignment_id, l.seller_sku, l.sid, l.from_wid, l.units
HAVING l.units <> coalesce(sum(m.units), 0);

-- ③ 超总量（延迟约束兜底之外的读侧证据）
CREATE OR REPLACE VIEW v_check_dispatch_over_total AS
SELECT line_id, total_units, staged_units, dispatched_units
  FROM v_plan_line_dispatch_qty
 WHERE staged_units + dispatched_units > total_units;

-- ④ msku 在镜像里改挂了别的货号（安排写入时冻结了 sku；argMax 可能事后改挂）
CREATE OR REPLACE VIEW v_check_dispatch_sku_drift AS
SELECT a.sheet_id, a.arrangement_id, a.seller_sku, a.sid, a.sku AS arranged_sku, b.sku AS bridge_sku
  FROM dispatch_arrangement a
  JOIN sheet s USING (sheet_id)
  JOIN msku_bridge b ON (b.seller_sku, b.sid) = (a.seller_sku, a.sid)
 WHERE s.state <> '已取消' AND b.sku <> a.sku;

-- ⑤ 未发的排货表里，安排挂着的记录已不在 已下单 / 准备排货（撤销 · 作废退回边，003:24 / 010:231）
--   ★ 退回边画得出就要退得回去：这里点名，用例层据此挡确认、并允许撤回格子
CREATE OR REPLACE VIEW v_check_dispatch_line_state AS
SELECT a.sheet_id, a.arrangement_id, a.line_id, l.state
  FROM dispatch_arrangement a
  JOIN sheet s USING (sheet_id)
  JOIN plan_line l ON l.line_id = a.line_id
 WHERE s.state = '草稿' AND l.state NOT IN ('已下单','准备排货');

-- 汇总给 GET /v1/checks/dispatch（形状同 v_check_purchase）
CREATE OR REPLACE VIEW v_check_dispatch AS
SELECT 'map_ne_arrangement' AS check_name, to_jsonb(v) AS ref FROM v_check_dispatch_map v
UNION ALL SELECT 'line_ne_sum_map', to_jsonb(v) FROM v_check_consignment_line_sum v
UNION ALL SELECT 'over_total',      to_jsonb(v) FROM v_check_dispatch_over_total v
UNION ALL SELECT 'msku_sku_drift',  to_jsonb(v) FROM v_check_dispatch_sku_drift v
UNION ALL SELECT 'line_not_dispatchable', to_jsonb(v) FROM v_check_dispatch_line_state v;
