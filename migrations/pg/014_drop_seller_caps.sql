-- 014 · 取消分店上限（PB-D22，推翻 PB-D2 上限部分 · PB-D3 · PB-V1）
-- 出处：00-待裁定清单 PB-D22 · 012 的 seller_caps / assert_line_po_qty / v_check_po_source
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。
-- ★ 量闸只剩两道：总量 · 店必须是这条记录里的店（demand_by_seller 的键）。
--   后者原来是「不在记录里 → 上限 0」顺带挡住的；上限拿掉后不单独写，就会悄悄放开。

CREATE OR REPLACE FUNCTION assert_line_po_qty(p_line_id bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE q record; stray text;
BEGIN
    -- 同 012：按记录串行化提交者（写偏斜）；★ 不能用 FOR UPDATE（与 FK 的 KEY SHARE 互等）
    PERFORM 1 FROM plan_line WHERE line_id = p_line_id FOR NO KEY UPDATE;
    SELECT * INTO q FROM v_plan_line_po_qty WHERE line_id = p_line_id;
    IF q.confirmed_units > q.total_units THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_over_total',
              MESSAGE = format('记录 %s 已组单 %s 超过总量 %s', p_line_id, q.confirmed_units, q.total_units);
    END IF;
    SELECT string_agg(DISTINCT m.seller_id, ',' ORDER BY m.seller_id) INTO stray
      FROM po_source_map m JOIN plan_line l USING (line_id)
     WHERE m.line_id = p_line_id AND NOT (coalesce(l.demand_by_seller, '{}'::jsonb) ? m.seller_id);
    IF stray IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_seller_not_in_line',
              MESSAGE = format('记录 %s 的来源店 %s 不在这条记录里', p_line_id, stray);
    END IF;
END $$;

-- 列形状不变（v_check_purchase 依赖它）；seller_not_in_line 的 cap 为 NULL —— 没有上限，不是 0
CREATE OR REPLACE VIEW v_check_po_source AS
SELECT q.line_id, NULL::text AS seller_id, 'over_total' AS problem,
       q.confirmed_units AS used, q.total_units AS cap
  FROM v_plan_line_po_qty q WHERE q.confirmed_units > q.total_units
UNION ALL
SELECT m.line_id, m.seller_id, 'seller_not_in_line', sum(m.units)::int, NULL::int
  FROM po_source_map m
  JOIN plan_line l USING (line_id)
 WHERE NOT (coalesce(l.demand_by_seller, '{}'::jsonb) ? m.seller_id)
 GROUP BY m.line_id, m.seller_id;

DROP FUNCTION IF EXISTS seller_caps(bigint);
