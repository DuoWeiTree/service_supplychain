-- 018 · 排货按销售计划、按量（负责人 2026-10-11：CA-D10 按量排 · CA-D11 一张排货表只对应一个销售计划）
-- 出处：00 CA 组 · spec 2026-10-10-dispatch-draft-design §1 / §4
-- ★ 016 / 017 已执行，不改原文件；这里 CREATE OR REPLACE 覆盖函数与视图。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① 排货表挂计划。★ 列可空只为容纳 018 之前建的历史表；新表由 sheet_guard 强制必填（sheet_needs_plan）
ALTER TABLE sheet ADD COLUMN IF NOT EXISTS plan_id bigint REFERENCES plan(plan_id);
CREATE INDEX IF NOT EXISTS sheet_by_plan ON sheet (plan_id);

CREATE OR REPLACE FUNCTION sheet_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.plan_id IS NULL THEN       -- ★ CA-D11
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_needs_plan',
                  MESSAGE = '排货表必须对应一个销售计划';
        END IF;
        IF NEW.state IS DISTINCT FROM '草稿' THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_born_as_draft',
                  MESSAGE = format('排货表只能以 草稿 出生，收到 %s', NEW.state);
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_no_delete',
              MESSAGE = format('排货表 %s 不删除，只取消（留痕）', OLD.sheet_id);
    END IF;
    IF NEW.plan_id IS DISTINCT FROM OLD.plan_id THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_plan_fixed',
              MESSAGE = format('排货表 %s 的计划不可改', OLD.sheet_id);
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state
       AND current_setting('app.in_sheet_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_state_via_event',
              MESSAGE = '排货表状态只能由 sheet_event 推进';
    END IF;
    RETURN NEW;
END $$;


CREATE OR REPLACE FUNCTION arrangement_precondition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE l record; bsku text; fkind text; dkind text; sp bigint;
BEGIN
    SELECT plan_id INTO sp FROM sheet WHERE sheet_id = NEW.sheet_id;
    SELECT kind INTO fkind FROM warehouse WHERE wid = NEW.from_wid;
    IF fkind IS DISTINCT FROM 'local' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_from_not_local',
              MESSAGE = format('来源仓 %s 是 %s，C-a 只许本地仓（CA-Q19）', NEW.from_wid, fkind);
    END IF;
    IF NEW.channel = 'oversea' THEN
        SELECT kind INTO dkind FROM warehouse WHERE wid = NEW.dest_wid;
        IF NEW.dest_sid IS NOT NULL OR dkind IS NULL OR dkind NOT LIKE 'oversea_%' THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_invalid_destination',
                  MESSAGE = format('海外仓目的地 %s 不是海外仓（%s）', NEW.dest_wid, dkind);
        END IF;
    ELSIF NEW.dest_wid IS NOT NULL OR NEW.dest_sid IS DISTINCT FROM NEW.sid THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_invalid_destination',
              MESSAGE = format('FBA 目的地只能是本店 %s，收到 %s', NEW.sid, NEW.dest_sid);
    END IF;
    IF NEW.line_id IS NOT NULL THEN
        SELECT sku, state, demand_by_seller, plan_id INTO l FROM plan_line WHERE line_id = NEW.line_id;
        IF l.sku IS DISTINCT FROM NEW.sku THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_sku_mismatch',
                  MESSAGE = format('记录 %s 的货号是 %s，格子写的是 %s', NEW.line_id, l.sku, NEW.sku);
        END IF;
        -- ★ CA-D10：按量排 —— 只挡终态；上限由 dispatch_over_total 按量守
        IF l.state IN ('已撤销', '已完结') THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_line_not_dispatchable',
                  MESSAGE = format('记录 %s 已%s，不能再排', NEW.line_id, substr(l.state, 2));
        END IF;
        -- ★ CA-D11：一张排货表只排它那张计划的记录（历史上无计划的表不受此限）
        IF sp IS NOT NULL AND l.plan_id IS DISTINCT FROM sp THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_line_not_in_plan',
                  MESSAGE = format('记录 %s 属于计划 %s，这张排货表对应计划 %s', NEW.line_id, l.plan_id, sp);
        END IF;
        IF NOT (coalesce(l.demand_by_seller, '{}'::jsonb) ? NEW.sid) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_seller_not_in_line',
                  MESSAGE = format('店 %s 不在记录 %s 里（CA-D6）', NEW.sid, NEW.line_id);
        END IF;
    END IF;
    SELECT sku INTO bsku FROM msku_bridge WHERE seller_sku = NEW.seller_sku AND sid = NEW.sid;
    IF bsku IS DISTINCT FROM NEW.sku THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_msku_not_this_sku',
              MESSAGE = format('msku %s@%s 对应货号 %s，不是 %s', NEW.seller_sku, NEW.sid, bsku, NEW.sku);
    END IF;
    RETURN NEW;
END $$;


-- ② 核对：挂在终态记录上 / 挂在别的计划的记录上的安排（草稿表）—— 确认前的完整性闸据此挡
CREATE OR REPLACE VIEW v_check_dispatch_line_state AS
SELECT a.sheet_id, a.arrangement_id, a.line_id, l.state
  FROM dispatch_arrangement a
  JOIN sheet s USING (sheet_id)
  JOIN plan_line l ON l.line_id = a.line_id
 WHERE s.state = '草稿'
   AND (l.state IN ('已撤销','已完结') OR (s.plan_id IS NOT NULL AND l.plan_id <> s.plan_id));
