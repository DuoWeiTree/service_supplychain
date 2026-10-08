-- 012 · 作废感知的量 · 分店上限 · 超计划量 · v_check_* 一族
-- 出处：spec §3.2 §3.4 §11.1 · PB-D2 PB-D3 PB-D5 PB-D9 · 03:869-913（量视图与 c_po_qty 原型）
-- ★ Python 侧（rules/purchase.py）先判并给出带字段的 409；这里是最后一道防线，
--   两边口径必须逐字一致 —— 最大余数法的并列按 seller_id 升序。

CREATE OR REPLACE VIEW v_po_lx_status AS
SELECT DISTINCT ON (order_sn) order_sn, status, source, source_captured_at
  FROM po_snapshot
 ORDER BY order_sn, source_captured_at DESC, snap_id DESC;

CREATE OR REPLACE VIEW v_po_effective AS
SELECT p.*, coalesce(s.status = -1, false) AS is_void
  FROM po p LEFT JOIN v_po_lx_status s ON s.order_sn = p.order_sn;

CREATE OR REPLACE VIEW v_plan_line_po_qty AS
SELECT l.line_id, l.total_units, l.state,
       coalesce(sum(m.units) FILTER (WHERE NOT e.is_void), 0)::int                        AS confirmed_units,
       coalesce(sum(m.units) FILTER (WHERE NOT e.is_void AND e.state = '已下单'), 0)::int AS ordered_units
  FROM plan_line l
  LEFT JOIN po_source_map m ON m.line_id = l.line_id
  LEFT JOIN v_po_effective e ON e.po_id = m.po_id
 GROUP BY l.line_id, l.total_units, l.state;

-- ★ PB-D2：上限 = total × 期望[s] / Σ期望，最大余数法；PB-D3：无依据返回空集
CREATE OR REPLACE FUNCTION seller_caps(p_line_id bigint)
RETURNS TABLE (seller_id text, cap int) LANGUAGE plpgsql STABLE AS $$
DECLARE tot bigint; d jsonb; s bigint;
BEGIN
    SELECT total_units, demand_by_seller INTO tot, d FROM plan_line WHERE line_id = p_line_id;
    IF d IS NULL OR NOT EXISTS (SELECT 1 FROM jsonb_each(d)) THEN RETURN; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_each(d) e
                WHERE e.value->>'basis' = 'unknown'
                   OR jsonb_typeof(e.value->'units') IS DISTINCT FROM 'number') THEN
        RETURN;
    END IF;
    SELECT sum((e.value->>'units')::bigint) INTO s FROM jsonb_each(d) e;
    IF s = 0 THEN RETURN; END IF;
    RETURN QUERY
    WITH x AS (SELECT e.key AS sid, (e.value->>'units')::bigint AS u FROM jsonb_each(d) e),
         f AS (SELECT x.sid, (tot * x.u) / s AS fl, (tot * x.u) % s AS rm FROM x),
         r AS (SELECT f.sid, f.fl, row_number() OVER (ORDER BY f.rm DESC, f.sid) AS rk FROM f)
    SELECT r.sid, (r.fl + CASE WHEN r.rk <= tot - (SELECT sum(fl) FROM f) THEN 1 ELSE 0 END)::int
      FROM r;
END $$;

CREATE OR REPLACE FUNCTION assert_line_po_qty(p_line_id bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE q record; v record; has_caps boolean;
BEGIN
    -- 检查延迟到提交，快照看不到并发事务未提交的行（写偏斜）；按记录串行化提交者，后到者才能看见先到者的行。★ 不能用 FOR UPDATE：它与并发事务经 FK 持有的 KEY SHARE 冲突，两个提交者会互相等
    PERFORM 1 FROM plan_line WHERE line_id = p_line_id FOR NO KEY UPDATE;
    SELECT * INTO q FROM v_plan_line_po_qty WHERE line_id = p_line_id;
    IF q.confirmed_units > q.total_units THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_over_total',
              MESSAGE = format('记录 %s 已组单 %s 超过总量 %s', p_line_id, q.confirmed_units, q.total_units);
    END IF;
    IF q.confirmed_units = 0 THEN RETURN; END IF;
    SELECT EXISTS (SELECT 1 FROM seller_caps(p_line_id)) INTO has_caps;
    IF NOT has_caps THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_no_seller_basis',
              MESSAGE = format('记录 %s 的期望销量合计为 0 或有店为 unknown，不可组单', p_line_id);
    END IF;
    FOR v IN
        SELECT m.seller_id, sum(m.units) AS used, coalesce(c.cap, 0) AS cap
          FROM po_source_map m
          JOIN v_po_effective e ON e.po_id = m.po_id AND NOT e.is_void
          LEFT JOIN seller_caps(p_line_id) c ON c.seller_id = m.seller_id
         WHERE m.line_id = p_line_id
         GROUP BY m.seller_id, c.cap
    LOOP
        IF v.used > v.cap THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_over_seller_cap',
                  MESSAGE = format('记录 %s 店 %s 已用 %s 超过上限 %s', p_line_id, v.seller_id, v.used, v.cap);
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION assert_po_line_sources(p_po_id bigint, p_line_no int) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE u int; reason text; src int;
BEGIN
    SELECT units, unplanned_reason INTO u, reason FROM po_line WHERE po_id = p_po_id AND line_no = p_line_no;
    IF u IS NULL THEN RETURN; END IF;     -- 行已被删
    SELECT coalesce(sum(units), 0) INTO src FROM po_source_map WHERE po_id = p_po_id AND line_no = p_line_no;
    IF u < src THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_line_below_sources',
              MESSAGE = format('采购单 %s 行 %s 数量 %s 少于来源合计 %s', p_po_id, p_line_no, u, src);
    END IF;
    IF u > src AND coalesce(btrim(reason), '') = '' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_unplanned_reason_required',
              MESSAGE = format('采购单 %s 行 %s 有 %s 件无计划来源，必须写理由', p_po_id, p_line_no, u - src);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION c_po_source_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        PERFORM assert_line_po_qty(OLD.line_id);
        PERFORM assert_po_line_sources(OLD.po_id, OLD.line_no);
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
        PERFORM assert_line_po_qty(NEW.line_id);
        PERFORM assert_po_line_sources(NEW.po_id, NEW.line_no);
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION c_po_line_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'DELETE' THEN PERFORM assert_po_line_sources(NEW.po_id, NEW.line_no); END IF;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS c_po_source_qty ON po_source_map;
CREATE CONSTRAINT TRIGGER c_po_source_qty AFTER INSERT OR UPDATE OR DELETE ON po_source_map
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION c_po_source_check();
DROP TRIGGER IF EXISTS c_po_line_qty ON po_line;
CREATE CONSTRAINT TRIGGER c_po_line_qty AFTER INSERT OR UPDATE ON po_line
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION c_po_line_check();

-- ── v_check_*：每个视图返回违例行，空 = 通过（spec §11 G3） ──────────────────
CREATE OR REPLACE VIEW v_check_po_source AS
SELECT q.line_id, NULL::text AS seller_id, 'over_total' AS problem,
       q.confirmed_units AS used, q.total_units AS cap
  FROM v_plan_line_po_qty q WHERE q.confirmed_units > q.total_units
UNION ALL
SELECT m.line_id, m.seller_id, 'over_seller_cap', sum(m.units)::int, coalesce(c.cap, 0)
  FROM po_source_map m
  JOIN v_po_effective e ON e.po_id = m.po_id AND NOT e.is_void
  LEFT JOIN LATERAL seller_caps(m.line_id) c ON c.seller_id = m.seller_id
 GROUP BY m.line_id, m.seller_id, c.cap
HAVING sum(m.units) > coalesce(c.cap, 0);

-- ★ 判据（04:378-380）：总量 > 0 且 已下单量 = 总量 → 已下单；已确认量 = 总量 → 已确认；否则 已提交
CREATE OR REPLACE VIEW v_check_line_state AS
SELECT q.line_id, q.state,
       CASE WHEN q.ordered_units = q.total_units THEN '已下单'
            WHEN q.confirmed_units = q.total_units THEN '已确认'
            ELSE '已提交' END AS derived
  FROM v_plan_line_po_qty q
 WHERE q.state IN ('已提交','已确认','已下单')
   AND q.state <> CASE WHEN q.ordered_units = q.total_units THEN '已下单'
                       WHEN q.confirmed_units = q.total_units THEN '已确认'
                       ELSE '已提交' END;

CREATE OR REPLACE VIEW v_check_po_unplanned AS
SELECT l.po_id, l.line_no, l.units, coalesce(s.src, 0) AS sources,
       CASE WHEN l.units < coalesce(s.src, 0) THEN 'below_sources' ELSE 'reason_missing' END AS problem
  FROM po_line l
  LEFT JOIN (SELECT po_id, line_no, sum(units)::int AS src FROM po_source_map GROUP BY 1, 2) s
         USING (po_id, line_no)
 WHERE l.units < coalesce(s.src, 0)
    OR (l.units > coalesce(s.src, 0) AND coalesce(btrim(l.unplanned_reason), '') = '');

CREATE OR REPLACE VIEW v_po_item_latest AS
SELECT o.* FROM po_item_obs o
  JOIN (SELECT order_sn, max(source_captured_at) AS at FROM po_item_obs GROUP BY order_sn) m
    ON m.order_sn = o.order_sn AND m.at = o.source_captured_at;

-- ★ G1：领星落下的 ≡ 我们发的。行级键优先 lx_item_id，缺时按 (sku, wid)
CREATE OR REPLACE VIEW v_check_po_landed AS
WITH ours AS (
    SELECT p.po_id, p.order_sn, l.line_no, l.sku, l.wid, l.units, l.price, l.lx_item_id
      FROM po p JOIN po_line l USING (po_id) WHERE p.state = '已下单'),
pairs AS (
    SELECT o.*, b.quantity_real AS lx_units, b.price AS lx_price
      FROM ours o JOIN v_po_item_latest b ON b.order_sn = o.order_sn
       AND ((o.lx_item_id IS NOT NULL AND b.lx_item_id = o.lx_item_id)
         OR (o.lx_item_id IS NULL AND b.sku = o.sku AND b.wid IS NOT DISTINCT FROM o.wid)))
SELECT 'not_observed' AS problem, o.po_id, o.order_sn, o.line_no, o.sku, o.wid,
       o.units AS ours_units, NULL::int AS lx_units, o.price AS ours_price, NULL::numeric AS lx_price
  FROM ours o WHERE NOT EXISTS (SELECT 1 FROM po_item_obs b WHERE b.order_sn = o.order_sn)
UNION ALL
SELECT 'missing_item', o.po_id, o.order_sn, o.line_no, o.sku, o.wid, o.units, NULL, o.price, NULL
  FROM ours o
 WHERE EXISTS (SELECT 1 FROM po_item_obs b WHERE b.order_sn = o.order_sn)
   AND NOT EXISTS (SELECT 1 FROM pairs p WHERE p.po_id = o.po_id AND p.line_no = o.line_no)
UNION ALL
SELECT 'qty_or_price_differs', p.po_id, p.order_sn, p.line_no, p.sku, p.wid, p.units, p.lx_units, p.price, p.lx_price
  FROM pairs p WHERE p.lx_units <> p.units OR p.lx_price IS DISTINCT FROM p.price
UNION ALL
SELECT 'unexpected_item', po.po_id, b.order_sn, NULL, b.sku, b.wid, NULL, b.quantity_real, NULL, b.price
  FROM v_po_item_latest b JOIN po ON po.order_sn = b.order_sn AND po.state = '已下单'
 WHERE NOT EXISTS (SELECT 1 FROM pairs p WHERE p.order_sn = b.order_sn
                     AND ((p.lx_item_id IS NOT NULL AND p.lx_item_id = b.lx_item_id)
                       OR (p.lx_item_id IS NULL AND p.sku = b.sku AND p.wid IS NOT DISTINCT FROM b.wid)));

-- 汇总给 GET /v1/checks/purchase
CREATE OR REPLACE VIEW v_check_purchase AS
SELECT 'po_source' AS check_name, to_jsonb(v) AS ref FROM v_check_po_source v
UNION ALL SELECT 'line_state',  to_jsonb(v) FROM v_check_line_state v
UNION ALL SELECT 'po_unplanned', to_jsonb(v) FROM v_check_po_unplanned v
UNION ALL SELECT 'po_landed',   to_jsonb(v) FROM v_check_po_landed v;
