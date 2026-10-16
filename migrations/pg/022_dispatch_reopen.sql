-- 022 · 退回修改：排货表 已确认 → 草稿（负责人 2026-10-12：CA-D14「只要在没有发到领星 ERP 前，都应该允许调整……
--        只有发给领星 ERP 后，才能被锁死」；部分已发 → 整表不许退回，只能取消）
-- 出处：00 CA-D14
-- ★ 016~021 已执行，不改原文件；这里 CREATE OR REPLACE 覆盖 sync_sheet_state / sync_consignment_state。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

INSERT INTO sheet_transition VALUES ('已确认','草稿','back',true) ON CONFLICT DO NOTHING;

-- 表退回：只在这张表从没碰过领星时（无任何领星单号 —— 删掉的也算 · 无待核实 · 无在途写调用）
CREATE OR REPLACE FUNCTION sync_sheet_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text; need boolean; k text; c record;
BEGIN
    IF NEW.from_state = '[*]' THEN
        IF EXISTS (SELECT 1 FROM sheet_event WHERE sheet_id = NEW.sheet_id AND seq < NEW.seq) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_birth_once',
                  MESSAGE = format('排货表 %s 已有出生事件', NEW.sheet_id);
        END IF;
        RETURN NEW;
    END IF;
    SELECT needs_reason, kind INTO need, k FROM sheet_transition
     WHERE from_state = NEW.from_state AND to_state = NEW.to_state;
    IF need AND coalesce(btrim(NEW.reason), '') = '' THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
              CONSTRAINT = CASE k WHEN 'back' THEN 'sheet_reopen_needs_reason' ELSE 'sheet_cancel_needs_reason' END,
              MESSAGE = format('排货表 %s %s → %s 必须写理由', NEW.sheet_id, NEW.from_state, NEW.to_state);
    END IF;
    SELECT state INTO cur FROM sheet WHERE sheet_id = NEW.sheet_id FOR UPDATE;
    IF cur IS DISTINCT FROM NEW.from_state THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_stale_state',
              MESSAGE = format('排货表 %s 当前 %s，事件称 %s', NEW.sheet_id, cur, NEW.from_state);
    END IF;
    IF NEW.from_state = '已确认' AND NEW.to_state = '草稿' AND (
           EXISTS (SELECT 1 FROM ext_ref e JOIN consignment cn USING (consignment_id) WHERE cn.sheet_id = NEW.sheet_id)
        OR EXISTS (SELECT 1 FROM dispatch_attention a JOIN consignment cn USING (consignment_id)
                    WHERE cn.sheet_id = NEW.sheet_id
                      AND NOT EXISTS (SELECT 1 FROM dispatch_attention_close x WHERE x.attention_id = a.attention_id))
        OR EXISTS (SELECT 1 FROM erp_call e JOIN consignment cn USING (consignment_id)
                    WHERE cn.sheet_id = NEW.sheet_id AND e.outcome = 'in_flight')) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_reopen_after_submit',
              MESSAGE = format('排货表 %s 已有单发到过领星（或正在发 / 待核实），不能退回修改，只能取消', NEW.sheet_id);
    END IF;
    PERFORM set_config('app.in_sheet_state_sync', 'on', true);
    UPDATE sheet SET state = NEW.to_state, updated_at = now(),
           watermark_at_confirm = CASE WHEN NEW.to_state = '已确认' THEN (SELECT last_value FROM ledger_seq)
                                       WHEN NEW.to_state = '草稿' THEN NULL
                                       ELSE watermark_at_confirm END
     WHERE sheet_id = NEW.sheet_id;
    PERFORM set_config('app.in_sheet_state_sync', 'off', true);
    -- ★ 04 §4.2：排货单跟随排货表（同一事务）
    FOR c IN SELECT consignment_id, our_stage FROM consignment WHERE sheet_id = NEW.sheet_id ORDER BY consignment_id LOOP
        IF NEW.to_state = '已确认' AND c.our_stage = '草稿' THEN
            INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)
            VALUES (c.consignment_id, '草稿', '已确认', NEW.actor, 'sheet:' || NEW.seq);
        ELSIF NEW.to_state = '草稿' AND c.our_stage = '已确认' THEN
            PERFORM set_config('app.in_sheet_reopen', 'on', true);
            INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, reason, src)
            VALUES (c.consignment_id, '已确认', '草稿', NEW.actor, NEW.reason, 'sheet:' || NEW.seq);
            PERFORM set_config('app.in_sheet_reopen', 'off', true);
        ELSIF NEW.to_state = '已取消' AND c.our_stage IN ('草稿', '已确认', '已投送') THEN
            INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, reason, src)
            VALUES (c.consignment_id, c.our_stage,
                    CASE c.our_stage WHEN '草稿' THEN '已放弃' ELSE '已取消' END,
                    NEW.actor, NEW.reason, 'sheet:' || NEW.seq);
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

-- 单退回：只许跟随表退回（判据在表上，单上不重复 —— 两处各判一遍会分叉）
CREATE OR REPLACE FUNCTION sync_consignment_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text; sst text;
BEGIN
    IF NEW.from_state = '[*]' THEN
        IF EXISTS (SELECT 1 FROM consignment_event WHERE consignment_id = NEW.consignment_id AND seq < NEW.seq) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_birth_once',
                  MESSAGE = format('排货单 %s 已有出生事件', NEW.consignment_id);
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.from_state = '已确认' AND NEW.to_state = '草稿'
       AND current_setting('app.in_sheet_reopen', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_back_follows_sheet',
              MESSAGE = format('排货单 %s 只能跟随排货表「退回修改」回到草稿', NEW.consignment_id);
    END IF;
    -- ★ C-b（spec 2026-10-12 §4）：已投送 = 每个来源仓都有一张未作废的领星备货单
    IF NEW.to_state = '已投送' AND EXISTS (
        SELECT 1 FROM consignment_line l WHERE l.consignment_id = NEW.consignment_id
           AND NOT EXISTS (SELECT 1 FROM ext_ref e WHERE e.consignment_id = l.consignment_id AND e.from_wid = l.from_wid
                              AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id))) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_dispatch_incomplete',
              MESSAGE = format('排货单 %s 还有来源仓没有领星备货单，不能算已投送', NEW.consignment_id);
    END IF;
    -- ★ CB-D6：取消前，领星里建过的备货单必须全部删掉（作废留痕）
    IF NEW.to_state IN ('已取消', '已放弃') AND EXISTS (
        SELECT 1 FROM ext_ref e WHERE e.consignment_id = NEW.consignment_id
           AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id)) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_cancel_needs_void',
              MESSAGE = format('排货单 %s 在领星还有未删除的备货单，先删再取消', NEW.consignment_id);
    END IF;
    SELECT our_stage INTO cur FROM consignment WHERE consignment_id = NEW.consignment_id FOR UPDATE;
    IF cur IS DISTINCT FROM NEW.from_state THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_stale_state',
              MESSAGE = format('排货单 %s 当前 %s，事件称 %s', NEW.consignment_id, cur, NEW.from_state);
    END IF;
    SELECT s.state INTO sst FROM sheet s JOIN consignment c USING (sheet_id) WHERE c.consignment_id = NEW.consignment_id;
    IF NEW.to_state = '已确认' AND sst IS DISTINCT FROM '已确认' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_confirm_follows_sheet',
              MESSAGE = format('排货单 %s 只能跟随所属排货表确认', NEW.consignment_id);
    END IF;
    PERFORM set_config('app.in_consignment_state_sync', 'on', true);
    UPDATE consignment SET our_stage = NEW.to_state, updated_at = now() WHERE consignment_id = NEW.consignment_id;
    PERFORM set_config('app.in_consignment_state_sync', 'off', true);
    RETURN NEW;
END $$;
