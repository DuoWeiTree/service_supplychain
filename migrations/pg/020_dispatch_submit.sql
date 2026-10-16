-- 020 · C-b 海外仓排货单提交领星备货单（spec 2026-10-12-dispatch-submit-ows-design §4 · 00 CB 组）
-- ★ 016 已执行不改；这里 CREATE OR REPLACE 覆盖三个函数（投送 / 取消的前置、表取消带走已投送的单、物流方式随表冻结）。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① 领星头程物流渠道镜像（源 = 领星 API channelList；CH 里没有这张表）
CREATE TABLE IF NOT EXISTS logistics_channel (
    logistics_id          int  PRIMARY KEY,           -- 领星 data[].id（createInbound.logistics_id）
    channel_name          text NOT NULL,
    method_name           text,
    provider_name         text,
    receive_country_codes jsonb,
    enabled               boolean NOT NULL,
    refreshed_at          timestamptz NOT NULL
);

-- ② 人填的：每张海外仓排货单的物流方式（CB-D3）。按表级目的地键，重派生不丢（同装箱，铁律 6）
CREATE TABLE IF NOT EXISTS consignment_logistics (
    sheet_id     bigint NOT NULL REFERENCES sheet(sheet_id) ON DELETE CASCADE,
    dest_wid     int    NOT NULL REFERENCES warehouse(wid),
    logistics_id int    NOT NULL REFERENCES logistics_channel(logistics_id),
    updated_by   text   NOT NULL REFERENCES actor(actor_id),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consignment_logistics_pkey PRIMARY KEY (sheet_id, dest_wid)
);

-- ③ 出站留痕复用 erp_call：排货单那一侧
ALTER TABLE erp_call ADD COLUMN IF NOT EXISTS consignment_id bigint REFERENCES consignment(consignment_id);
CREATE UNIQUE INDEX IF NOT EXISTS erp_call_one_write_in_flight_per_consignment
    ON erp_call (consignment_id) WHERE outcome = 'in_flight' AND side_effect <> 'read' AND consignment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS erp_call_by_consignment ON erp_call (consignment_id, call_id) WHERE consignment_id IS NOT NULL;

-- ④ 我们投送产生的外部单（只追加）。★ 参考号唯一 = 我们自己的幂等键（领星拒不拒重未实测，CB-Q2）
CREATE TABLE IF NOT EXISTS ext_ref (
    ext_ref_id     bigserial PRIMARY KEY,
    consignment_id bigint NOT NULL REFERENCES consignment(consignment_id),
    from_wid       int    NOT NULL REFERENCES warehouse(wid),
    kind           text   NOT NULL CONSTRAINT ext_ref_kind_known CHECK (kind IN ('ows')),
    idem_key       text   NOT NULL CONSTRAINT ext_ref_idem_key_key UNIQUE,
    ext_no         text   NOT NULL,                       -- 领星备货单号 OWS…
    call_id        bigint REFERENCES erp_call(call_id),
    found_by       text   NOT NULL CONSTRAINT ext_ref_found_by_known CHECK (found_by IN ('create','lookup','register')),
    actor          text   NOT NULL,
    at             timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ext_ref_void (                 -- 取消时删掉领星单的留痕（CB-D6）
    ext_ref_id bigint PRIMARY KEY REFERENCES ext_ref(ext_ref_id),
    call_id    bigint REFERENCES erp_call(call_id),
    actor      text   NOT NULL,
    reason     text,
    at         timestamptz NOT NULL DEFAULT now()
);

-- ⑤ 待核实（结果未知且查重查不到）：开立一行、关闭另一行，都只追加
CREATE TABLE IF NOT EXISTS dispatch_attention (
    attention_id   bigserial PRIMARY KEY,
    consignment_id bigint NOT NULL REFERENCES consignment(consignment_id),
    from_wid       int    NOT NULL,
    idem_key       text   NOT NULL,
    call_id        bigint REFERENCES erp_call(call_id),
    detail         jsonb  NOT NULL,
    opened_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dispatch_attention_close (
    attention_id bigint PRIMARY KEY REFERENCES dispatch_attention(attention_id),
    how          text   NOT NULL CONSTRAINT dispatch_attention_close_how CHECK (how IN ('recheck','register','confirm_absent')),
    ext_ref_id   bigint REFERENCES ext_ref(ext_ref_id),
    actor        text   NOT NULL,
    reason       text,
    at           timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS t_ext_ref_append_only ON ext_ref;
CREATE TRIGGER t_ext_ref_append_only BEFORE UPDATE OR DELETE ON ext_ref FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_ext_ref_void_append_only ON ext_ref_void;
CREATE TRIGGER t_ext_ref_void_append_only BEFORE UPDATE OR DELETE ON ext_ref_void FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_dattn_append_only ON dispatch_attention;
CREATE TRIGGER t_dattn_append_only BEFORE UPDATE OR DELETE ON dispatch_attention FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_dattn_close_append_only ON dispatch_attention_close;
CREATE TRIGGER t_dattn_close_append_only BEFORE UPDATE OR DELETE ON dispatch_attention_close FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

-- ⑥ 我方进度白名单：★ CB-D6 已投送 → 已取消（前置：全部 ext_ref 已作废，见下面 sync_consignment_state）
INSERT INTO consignment_transition VALUES ('已投送','已取消','cancel',false) ON CONFLICT DO NOTHING;

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
    IF NEW.from_state = '已确认' AND NEW.to_state = '草稿' THEN
        -- ★ 04 §7.6b：退回只在未跨不可逆点时存在，判据依赖 ext_ref —— C-a 没有 ext_ref，一律拒
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_back_needs_dispatch',
              MESSAGE = format('排货单 %s 已确认 → 草稿 只在投送失败且未跨不可逆点时存在（C-b 起）', NEW.consignment_id);
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

CREATE OR REPLACE FUNCTION sync_sheet_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text; need boolean; c record;
BEGIN
    IF NEW.from_state = '[*]' THEN
        IF EXISTS (SELECT 1 FROM sheet_event WHERE sheet_id = NEW.sheet_id AND seq < NEW.seq) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_birth_once',
                  MESSAGE = format('排货表 %s 已有出生事件', NEW.sheet_id);
        END IF;
        RETURN NEW;
    END IF;
    SELECT needs_reason INTO need FROM sheet_transition
     WHERE from_state = NEW.from_state AND to_state = NEW.to_state;
    IF need AND coalesce(btrim(NEW.reason), '') = '' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_cancel_needs_reason',
              MESSAGE = format('排货表 %s %s → %s 必须写理由', NEW.sheet_id, NEW.from_state, NEW.to_state);
    END IF;
    SELECT state INTO cur FROM sheet WHERE sheet_id = NEW.sheet_id FOR UPDATE;
    IF cur IS DISTINCT FROM NEW.from_state THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_stale_state',
              MESSAGE = format('排货表 %s 当前 %s，事件称 %s', NEW.sheet_id, cur, NEW.from_state);
    END IF;
    PERFORM set_config('app.in_sheet_state_sync', 'on', true);
    UPDATE sheet SET state = NEW.to_state, updated_at = now(),
           watermark_at_confirm = CASE WHEN NEW.to_state = '已确认'
                                       THEN (SELECT last_value FROM ledger_seq) ELSE watermark_at_confirm END
     WHERE sheet_id = NEW.sheet_id;
    PERFORM set_config('app.in_sheet_state_sync', 'off', true);
    -- ★ 04 §4.2：排货单跟随排货表（同一事务）
    FOR c IN SELECT consignment_id, our_stage FROM consignment WHERE sheet_id = NEW.sheet_id ORDER BY consignment_id LOOP
        IF NEW.to_state = '已确认' AND c.our_stage = '草稿' THEN
            INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)
            VALUES (c.consignment_id, '草稿', '已确认', NEW.actor, 'sheet:' || NEW.seq);
        ELSIF NEW.to_state = '已取消' AND c.our_stage IN ('草稿', '已确认', '已投送') THEN
            INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, reason, src)
            VALUES (c.consignment_id, c.our_stage,
                    CASE c.our_stage WHEN '草稿' THEN '已放弃' ELSE '已取消' END,
                    NEW.actor, NEW.reason, 'sheet:' || NEW.seq);
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION dispatch_guard_children() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE j jsonb; sid bigint; st text;
BEGIN
    j := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
    IF TG_TABLE_NAME IN ('dispatch_arrangement', 'consignment_packing', 'consignment_logistics') THEN
        sid := (j ->> 'sheet_id')::bigint;
    ELSE
        SELECT c.sheet_id INTO sid FROM consignment c WHERE c.consignment_id = (j ->> 'consignment_id')::bigint;
    END IF;
    IF sid IS NULL THEN                       -- 父排货单已在同一语句里被级联删掉（草稿重派生）
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    SELECT state INTO st FROM sheet WHERE sheet_id = sid;
    IF st IS DISTINCT FROM '草稿' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_frozen',
              MESSAGE = format('排货表 %s 处于 %s，%s 不可 %s', sid, st, TG_TABLE_NAME, TG_OP);
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

DROP TRIGGER IF EXISTS t_logistics_guard ON consignment_logistics;
CREATE TRIGGER t_logistics_guard BEFORE INSERT OR UPDATE OR DELETE ON consignment_logistics
    FOR EACH ROW EXECUTE FUNCTION dispatch_guard_children();
