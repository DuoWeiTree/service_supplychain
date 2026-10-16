-- 021 · 排货表按去向分表（负责人 2026-10-12：CA-D13 「建表时就选去向」· 平台仓表绑定一个平台 · 旧表自动归类）
-- 出处：00 CA-D13 · spec 2026-10-12-dispatch-route-split-design §3 / §4
-- ★ 016~020 已执行，不改原文件；这里 CREATE OR REPLACE 覆盖 sheet_guard。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① 去向。★ 可空只为容纳 021 之前的旧表（混合 / 空）；新表由 sheet_guard 强制必填（sheet_needs_route）
ALTER TABLE sheet ADD COLUMN IF NOT EXISTS route text
    CONSTRAINT sheet_route_known CHECK (route IN ('fba','oversea'));

-- ② 回填：表内出现过的渠道（排货单 ∪ 安排，含已放弃 / 已取消的）只有一种 → 该渠道；否则 NULL（旧版混合表 / 空表）
DO $$
DECLARE n_fba int; n_os int; n_mixed int; n_empty int;
BEGIN
    WITH ch AS (
        SELECT sheet_id, channel FROM consignment
        UNION SELECT sheet_id, channel FROM dispatch_arrangement
    ), one AS (
        SELECT sheet_id, min(channel) AS channel FROM ch GROUP BY sheet_id HAVING count(DISTINCT channel) = 1
    )
    UPDATE sheet s SET route = one.channel FROM one WHERE s.sheet_id = one.sheet_id AND s.route IS NULL;

    SELECT count(*) FILTER (WHERE route = 'fba'), count(*) FILTER (WHERE route = 'oversea') INTO n_fba, n_os FROM sheet;
    SELECT count(*) INTO n_mixed FROM sheet s WHERE s.route IS NULL
       AND (SELECT count(DISTINCT channel) FROM (SELECT channel FROM consignment c WHERE c.sheet_id = s.sheet_id
             UNION SELECT channel FROM dispatch_arrangement a WHERE a.sheet_id = s.sheet_id) x) > 1;
    SELECT count(*) - n_mixed INTO n_empty FROM sheet WHERE route IS NULL;
    RAISE NOTICE '021 回填去向：fba=% oversea=% 旧版混合=% 空表=%', n_fba, n_os, n_mixed, n_empty;
END $$;

-- ③ R1 必填 · R2 不可改（其余同 018）
CREATE OR REPLACE FUNCTION sheet_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.plan_id IS NULL THEN       -- ★ CA-D11
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_needs_plan',
                  MESSAGE = '排货表必须对应一个销售计划';
        END IF;
        IF NEW.route IS NULL THEN         -- ★ CA-D13
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_needs_route',
                  MESSAGE = '排货表必须选去向：平台仓 · FBA 或 海外仓';
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
    IF NEW.route IS DISTINCT FROM OLD.route THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_route_fixed',
              MESSAGE = format('排货表 %s 的去向不可改（%s）', OLD.sheet_id, OLD.route);
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state
       AND current_setting('app.in_sheet_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_state_via_event',
              MESSAGE = '排货表状态只能由 sheet_event 推进';
    END IF;
    RETURN NEW;
END $$;

-- ④ R3 格子渠道 = 表去向 · R5 无去向（旧表）不收新格子。★ 只挡 INSERT / UPDATE：撤回（DELETE）照常
CREATE OR REPLACE FUNCTION arrangement_route() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r text;
BEGIN
    SELECT route INTO r FROM sheet WHERE sheet_id = NEW.sheet_id;
    IF r IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_route_missing',
              MESSAGE = format('排货表 %s 是旧版混合 / 无去向的表，只能撤回与取消；按去向另开新表', NEW.sheet_id);
    END IF;
    IF NEW.channel IS DISTINCT FROM r THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_route_mismatch',
              MESSAGE = format('排货表 %s 的去向是 %s，不能排去 %s', NEW.sheet_id, r, NEW.channel);
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS t_arrangement_route ON dispatch_arrangement;
CREATE TRIGGER t_arrangement_route BEFORE INSERT OR UPDATE ON dispatch_arrangement
    FOR EACH ROW EXECUTE FUNCTION arrangement_route();

-- ⑤ R4 同一计划同一去向最多一张草稿（已确认 / 已取消不占名额；NULL 去向不计）
CREATE UNIQUE INDEX IF NOT EXISTS sheet_one_draft_per_plan_route ON sheet (plan_id, route)
    WHERE state = '草稿' AND route IS NOT NULL;
