-- 016 · 阶段 C-a 排货域：排货表 · 安排 · 派生排货单 / 排货行 · 承重墙② · 装箱 · 闸留痕
-- 出处：spec 2026-10-10-dispatch-draft-design §4 · 04 §4.2 / §7.6b · 15 §2 / §5 / §6 · CA-D2/D3/D5/D6/D7（00 CA 组）
-- ★ 形状照 010（白名单 + 事件 + 冻结 + 只追加）与 014（延迟量约束 + FOR NO KEY UPDATE）。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

CREATE SEQUENCE IF NOT EXISTS ledger_seq;      -- 账本水位（03 §5）：每次安排写入取一个值

-- ① 排货表 + 白名单 + 事件 ----------------------------------------------------
CREATE TABLE IF NOT EXISTS sheet (
    sheet_id             bigserial PRIMARY KEY,
    state                text   NOT NULL DEFAULT '草稿',
    watermark_at_build   bigint NOT NULL DEFAULT nextval('ledger_seq'),
    watermark_at_confirm bigint,
    created_by           text   NOT NULL REFERENCES actor(actor_id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sheet_transition (
    from_state   text    NOT NULL,
    to_state     text    NOT NULL,
    kind         text    NOT NULL CONSTRAINT sheet_transition_kind_known
                         CHECK (kind IN ('forward','back','cancel')),
    needs_reason boolean NOT NULL DEFAULT false,
    CONSTRAINT sheet_transition_pkey PRIMARY KEY (from_state, to_state)
);
INSERT INTO sheet_transition VALUES
 ('[*]','草稿','forward',false),
 ('草稿','已确认','forward',false),
 ('草稿','已取消','cancel',true),
 ('已确认','已取消','cancel',true)          -- ★ CA-D7：投送前的取消出口
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS sheet_event (
    seq        bigserial PRIMARY KEY,
    sheet_id   bigint NOT NULL REFERENCES sheet(sheet_id) ON DELETE CASCADE,
    from_state text   NOT NULL,
    to_state   text   NOT NULL,
    actor      text   NOT NULL,
    reason     text,
    src        text,
    at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sheet_event_transition_fk FOREIGN KEY (from_state, to_state)
        REFERENCES sheet_transition(from_state, to_state)
);

-- ② 排货单（系统派生，N-3）+ 白名单 + 事件 --------------------------------------
CREATE TABLE IF NOT EXISTS consignment (
    consignment_id    bigserial PRIMARY KEY,
    sheet_id          bigint NOT NULL REFERENCES sheet(sheet_id),
    channel           text   NOT NULL CONSTRAINT consignment_channel_known CHECK (channel IN ('fba','oversea')),
    dest_wid          int    REFERENCES warehouse(wid),
    dest_sid          text   REFERENCES seller(seller_id),
    our_stage         text   NOT NULL DEFAULT '草稿',
    plan_rev_snapshot jsonb,                                 -- B-2：投送时写（C-b）
    -- ★ 设计取值 · 未实测：领星 inbound_order_no 的长度 / 字符限制未实调（C-b 前确认）
    idem_key          text GENERATED ALWAYS AS ('SCM-CN-' || consignment_id::text) STORED UNIQUE,
    created_by        text   NOT NULL REFERENCES actor(actor_id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consignment_one_dest CHECK (
        (channel = 'oversea' AND dest_wid IS NOT NULL AND dest_sid IS NULL)
     OR (channel = 'fba'     AND dest_sid IS NOT NULL AND dest_wid IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS consignment_one_per_dest_wid
    ON consignment (sheet_id, channel, dest_wid) WHERE dest_wid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS consignment_one_per_dest_sid
    ON consignment (sheet_id, channel, dest_sid) WHERE dest_sid IS NOT NULL;

CREATE TABLE IF NOT EXISTS consignment_transition (
    from_state   text    NOT NULL,
    to_state     text    NOT NULL,
    kind         text    NOT NULL CONSTRAINT consignment_transition_kind_known
                         CHECK (kind IN ('forward','back','cancel')),
    needs_reason boolean NOT NULL DEFAULT false,
    CONSTRAINT consignment_transition_pkey PRIMARY KEY (from_state, to_state)
);
INSERT INTO consignment_transition VALUES
 ('[*]','草稿','forward',false),
 ('草稿','已确认','forward',false),
 ('已确认','已投送','forward',false),
 ('已确认','草稿','back',false),           -- ★ 仅未跨不可逆点；C-a 由 sync 触发器一律拒
 ('已投送','已归档','forward',false),
 ('草稿','已放弃','cancel',false),
 ('已确认','已取消','cancel',false)         -- ★ CA-D7：跟随排货表取消（理由记在表事件上）
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS consignment_event (
    seq            bigserial PRIMARY KEY,
    consignment_id bigint NOT NULL REFERENCES consignment(consignment_id) ON DELETE CASCADE,
    from_state     text   NOT NULL,
    to_state       text   NOT NULL,
    actor          text   NOT NULL,
    reason         text,
    src            text,
    at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consignment_event_transition_fk FOREIGN KEY (from_state, to_state)
        REFERENCES consignment_transition(from_state, to_state)
);

-- ③ 安排 —— 排货域唯一的人工编排输入（N-2，铁律 6）----------------------------
CREATE TABLE IF NOT EXISTS dispatch_arrangement (
    arrangement_id bigserial PRIMARY KEY,
    sheet_id   bigint NOT NULL REFERENCES sheet(sheet_id) ON DELETE CASCADE,
    line_id    bigint REFERENCES plan_line(line_id),          -- ★ CA-D2：NULL = 无计划来源
    sku        text   NOT NULL REFERENCES sku_catalog(sku),    -- 写入时冻结（msku 改挂货号由 v_check 点名）
    seller_sku text   NOT NULL,
    sid        text   NOT NULL REFERENCES seller(seller_id),
    from_wid   int    NOT NULL REFERENCES warehouse(wid),
    channel    text   NOT NULL CONSTRAINT arrangement_channel_known CHECK (channel IN ('fba','oversea')),
    dest_wid   int    REFERENCES warehouse(wid),
    dest_sid   text   REFERENCES seller(seller_id),
    units      int    NOT NULL CONSTRAINT arrangement_units_positive CHECK (units > 0),
    ledger_seq bigint NOT NULL DEFAULT nextval('ledger_seq'),
    updated_by text   NOT NULL REFERENCES actor(actor_id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT arrangement_bridge_fk FOREIGN KEY (seller_sku, sid) REFERENCES msku_bridge(seller_sku, sid)
);
-- ★ 一格 = (来源, msku×店, 来源仓, 目的地)。两条部分索引而不是 NULLS NOT DISTINCT（PG 版本未实测，同 003）
CREATE UNIQUE INDEX IF NOT EXISTS arrangement_planned_cell ON dispatch_arrangement
    (sheet_id, line_id, seller_sku, sid, from_wid, channel, coalesce(dest_wid, -1), coalesce(dest_sid, ''))
    WHERE line_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS arrangement_unplanned_cell ON dispatch_arrangement
    (sheet_id, sku, seller_sku, sid, from_wid, channel, coalesce(dest_wid, -1), coalesce(dest_sid, ''))
    WHERE line_id IS NULL;
CREATE INDEX IF NOT EXISTS arrangement_by_line ON dispatch_arrangement (line_id) WHERE line_id IS NOT NULL;

-- ④ 排货行（派生）· 承重墙②（每条安排一行映射）· 装箱（人填，单独成表）· 闸留痕 -----
CREATE TABLE IF NOT EXISTS consignment_line (
    consignment_id bigint NOT NULL REFERENCES consignment(consignment_id) ON DELETE CASCADE,
    seller_sku     text   NOT NULL,
    sid            text   NOT NULL REFERENCES seller(seller_id),
    from_wid       int    NOT NULL REFERENCES warehouse(wid),
    sku            text   NOT NULL REFERENCES sku_catalog(sku),
    units          int    NOT NULL CONSTRAINT consignment_line_units_positive CHECK (units > 0),
    CONSTRAINT consignment_line_pkey PRIMARY KEY (consignment_id, seller_sku, sid, from_wid),  -- ★ CA-D3
    CONSTRAINT consignment_line_bridge_fk FOREIGN KEY (seller_sku, sid) REFERENCES msku_bridge(seller_sku, sid)
);

CREATE TABLE IF NOT EXISTS dispatch_source_map (
    arrangement_id bigint PRIMARY KEY REFERENCES dispatch_arrangement(arrangement_id) ON DELETE CASCADE,
    consignment_id bigint NOT NULL,
    seller_sku     text   NOT NULL,
    sid            text   NOT NULL,
    from_wid       int    NOT NULL,
    units          int    NOT NULL CONSTRAINT dispatch_source_units_positive CHECK (units > 0),
    CONSTRAINT dispatch_source_line_fk FOREIGN KEY (consignment_id, seller_sku, sid, from_wid)
        REFERENCES consignment_line(consignment_id, seller_sku, sid, from_wid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS dispatch_source_by_line
    ON dispatch_source_map (consignment_id, seller_sku, sid, from_wid);

CREATE TABLE IF NOT EXISTS consignment_packing (
    sheet_id      bigint  NOT NULL REFERENCES sheet(sheet_id) ON DELETE CASCADE,
    seller_sku    text    NOT NULL,
    sid           text    NOT NULL,
    from_wid      int     NOT NULL,
    box_count     int     CONSTRAINT packing_box_count_positive CHECK (box_count > 0),
    box_len       numeric CONSTRAINT packing_len_positive CHECK (box_len > 0),
    box_wid       numeric CONSTRAINT packing_wid_positive CHECK (box_wid > 0),
    box_hei       numeric CONSTRAINT packing_hei_positive CHECK (box_hei > 0),
    box_weight    numeric CONSTRAINT packing_weight_positive CHECK (box_weight > 0),
    units_per_box int     CONSTRAINT packing_units_per_box_positive CHECK (units_per_box > 0),
    updated_by    text    NOT NULL REFERENCES actor(actor_id),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consignment_packing_pkey PRIMARY KEY (sheet_id, seller_sku, sid, from_wid)
);

CREATE TABLE IF NOT EXISTS sheet_gate_run (            -- 15 §2.4「闸结果」留痕；只追加（铁律 8）
    run_id         bigserial PRIMARY KEY,
    sheet_id       bigint  NOT NULL REFERENCES sheet(sheet_id),
    actor          text    NOT NULL,
    ok             boolean NOT NULL,
    seen_watermark bigint,
    ledger_at      bigint  NOT NULL,
    stock_as_of    date,
    stock_rejected jsonb,
    gates          jsonb   NOT NULL,
    at             timestamptz NOT NULL DEFAULT now()
);

-- ⑤ 状态推进：只经事件；表推进时同事务带走全部排货单 --------------------------------
CREATE OR REPLACE FUNCTION sheet_birth_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO sheet_event (sheet_id, from_state, to_state, actor, src)
    VALUES (NEW.sheet_id, '[*]', '草稿', NEW.created_by, 'create');
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION consignment_birth_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, src)
    VALUES (NEW.consignment_id, '[*]', '草稿', NEW.created_by, 'derive');
    RETURN NULL;
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
        ELSIF NEW.to_state = '已取消' AND c.our_stage IN ('草稿', '已确认') THEN
            INSERT INTO consignment_event (consignment_id, from_state, to_state, actor, reason, src)
            VALUES (c.consignment_id, c.our_stage,
                    CASE c.our_stage WHEN '草稿' THEN '已放弃' ELSE '已取消' END,
                    NEW.actor, NEW.reason, 'sheet:' || NEW.seq);
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

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

CREATE OR REPLACE FUNCTION sheet_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
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
    IF NEW.state IS DISTINCT FROM OLD.state
       AND current_setting('app.in_sheet_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_state_via_event',
              MESSAGE = '排货表状态只能由 sheet_event 推进';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION consignment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text; sid bigint;
BEGIN
    sid := CASE WHEN TG_OP = 'DELETE' THEN OLD.sheet_id ELSE NEW.sheet_id END;
    IF TG_OP = 'INSERT' AND NEW.our_stage IS DISTINCT FROM '草稿' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_born_as_draft',
              MESSAGE = format('排货单只能以 草稿 出生，收到 %s', NEW.our_stage);
    END IF;
    IF TG_OP = 'UPDATE' AND current_setting('app.in_consignment_state_sync', true) = 'on'
       -- ★ 逐列比而不是 to_jsonb(NEW)：BEFORE 触发器里生成列 idem_key 在 NEW 上是 NULL
       AND (NEW.sheet_id, NEW.channel, NEW.dest_wid, NEW.dest_sid, NEW.plan_rev_snapshot::text)
           IS NOT DISTINCT FROM (OLD.sheet_id, OLD.channel, OLD.dest_wid, OLD.dest_sid, OLD.plan_rev_snapshot::text) THEN
        RETURN NEW;                                   -- 状态同步是唯一放行的改动
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.our_stage IS DISTINCT FROM OLD.our_stage THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_state_via_event',
              MESSAGE = '排货单状态只能由 consignment_event 推进';
    END IF;
    SELECT state INTO st FROM sheet WHERE sheet_id = sid;
    IF st IS DISTINCT FROM '草稿' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'sheet_frozen',
              MESSAGE = format('排货表 %s 处于 %s，排货单不可 %s', sid, st, TG_OP);
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- ⑥ 冻结：表不在草稿 → 安排 / 行 / 映射 / 装箱 一律不可增删改（04 §4.2「连 INSERT 一起挡」）
CREATE OR REPLACE FUNCTION dispatch_guard_children() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE j jsonb; sid bigint; st text;
BEGIN
    j := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
    IF TG_TABLE_NAME IN ('dispatch_arrangement', 'consignment_packing') THEN
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

-- ⑦ 安排的前置条件（03 §6.2 的排货版；CA-D5 / D6 / Q19）
CREATE OR REPLACE FUNCTION arrangement_precondition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE l record; bsku text; fkind text; dkind text;
BEGIN
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
        SELECT sku, state, demand_by_seller INTO l FROM plan_line WHERE line_id = NEW.line_id;
        IF l.sku IS DISTINCT FROM NEW.sku THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_sku_mismatch',
                  MESSAGE = format('记录 %s 的货号是 %s，格子写的是 %s', NEW.line_id, l.sku, NEW.sku);
        END IF;
        IF l.state NOT IN ('已下单', '准备排货') THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'arrangement_line_not_dispatchable',
                  MESSAGE = format('记录 %s 处于 %s，只有 已下单 / 准备排货 能排', NEW.line_id, l.state);
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

-- ⑧ 量守恒：每条记录 已排未发 + 已投送 ≤ 总量（04 §7.4；无计划来源不进任何记录）
CREATE OR REPLACE FUNCTION assert_line_dispatch_qty(p_line_id bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE tot int; used int;
BEGIN
    -- 同 014：按记录串行化（写偏斜）；★ 不能用 FOR UPDATE（与 FK 的 KEY SHARE 互等）
    SELECT total_units INTO tot FROM plan_line WHERE line_id = p_line_id FOR NO KEY UPDATE;
    SELECT coalesce(sum(m.units), 0) INTO used
      FROM dispatch_source_map m
      JOIN dispatch_arrangement a USING (arrangement_id)
      JOIN consignment c ON c.consignment_id = m.consignment_id
     WHERE a.line_id = p_line_id AND c.our_stage IN ('草稿', '已确认', '已投送', '已归档');
    IF used > tot THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'dispatch_over_total',
              MESSAGE = format('记录 %s 已排 %s 超过总量 %s', p_line_id, used, tot);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION c_dispatch_qty() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lid bigint;
BEGIN
    SELECT line_id INTO lid FROM dispatch_arrangement
     WHERE arrangement_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.arrangement_id ELSE NEW.arrangement_id END;
    IF lid IS NOT NULL THEN
        PERFORM assert_line_dispatch_qty(lid);
    END IF;
    RETURN NULL;
END $$;

-- ⑨ 只追加：事件随父行级联删除是唯一放行（同 010 po_event_guard_delete）
CREATE OR REPLACE FUNCTION dispatch_event_guard_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- ★ 按 jsonb 取键：plpgsql 会对 OR 两侧都解析 OLD 的字段，而两张表的列不同
    IF TG_TABLE_NAME = 'sheet_event'
         AND EXISTS (SELECT 1 FROM sheet WHERE sheet_id = (to_jsonb(OLD) ->> 'sheet_id')::bigint)
       OR TG_TABLE_NAME = 'consignment_event'
         AND EXISTS (SELECT 1 FROM consignment WHERE consignment_id = (to_jsonb(OLD) ->> 'consignment_id')::bigint) THEN
        RAISE EXCEPTION '% 是只追加表，不允许 DELETE（证据一旦可改，就不再是证据）', TG_TABLE_NAME;
    END IF;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS t_sheet_birth ON sheet;
CREATE TRIGGER t_sheet_birth AFTER INSERT ON sheet FOR EACH ROW EXECUTE FUNCTION sheet_birth_event();
DROP TRIGGER IF EXISTS t_sheet_guard ON sheet;
CREATE TRIGGER t_sheet_guard BEFORE INSERT OR UPDATE OR DELETE ON sheet FOR EACH ROW EXECUTE FUNCTION sheet_guard();
DROP TRIGGER IF EXISTS t_sheet_sync ON sheet_event;
CREATE TRIGGER t_sheet_sync AFTER INSERT ON sheet_event FOR EACH ROW EXECUTE FUNCTION sync_sheet_state();
DROP TRIGGER IF EXISTS t_sheet_event_no_update ON sheet_event;
CREATE TRIGGER t_sheet_event_no_update BEFORE UPDATE ON sheet_event FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_sheet_event_no_delete ON sheet_event;
CREATE TRIGGER t_sheet_event_no_delete BEFORE DELETE ON sheet_event FOR EACH ROW EXECUTE FUNCTION dispatch_event_guard_delete();

DROP TRIGGER IF EXISTS t_consignment_birth ON consignment;
CREATE TRIGGER t_consignment_birth AFTER INSERT ON consignment FOR EACH ROW EXECUTE FUNCTION consignment_birth_event();
DROP TRIGGER IF EXISTS t_consignment_guard ON consignment;
CREATE TRIGGER t_consignment_guard BEFORE INSERT OR UPDATE OR DELETE ON consignment FOR EACH ROW EXECUTE FUNCTION consignment_guard();
DROP TRIGGER IF EXISTS t_consignment_sync ON consignment_event;
CREATE TRIGGER t_consignment_sync AFTER INSERT ON consignment_event FOR EACH ROW EXECUTE FUNCTION sync_consignment_state();
DROP TRIGGER IF EXISTS t_consignment_event_no_update ON consignment_event;
CREATE TRIGGER t_consignment_event_no_update BEFORE UPDATE ON consignment_event FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_consignment_event_no_delete ON consignment_event;
CREATE TRIGGER t_consignment_event_no_delete BEFORE DELETE ON consignment_event FOR EACH ROW EXECUTE FUNCTION dispatch_event_guard_delete();

DROP TRIGGER IF EXISTS t_arrangement_guard ON dispatch_arrangement;
CREATE TRIGGER t_arrangement_guard BEFORE INSERT OR UPDATE OR DELETE ON dispatch_arrangement
    FOR EACH ROW EXECUTE FUNCTION dispatch_guard_children();
DROP TRIGGER IF EXISTS t_arrangement_pre ON dispatch_arrangement;
CREATE TRIGGER t_arrangement_pre BEFORE INSERT OR UPDATE ON dispatch_arrangement
    FOR EACH ROW EXECUTE FUNCTION arrangement_precondition();
DROP TRIGGER IF EXISTS t_cline_guard ON consignment_line;
CREATE TRIGGER t_cline_guard BEFORE INSERT OR UPDATE OR DELETE ON consignment_line
    FOR EACH ROW EXECUTE FUNCTION dispatch_guard_children();
DROP TRIGGER IF EXISTS t_dmap_guard ON dispatch_source_map;
CREATE TRIGGER t_dmap_guard BEFORE INSERT OR UPDATE OR DELETE ON dispatch_source_map
    FOR EACH ROW EXECUTE FUNCTION dispatch_guard_children();
DROP TRIGGER IF EXISTS t_packing_guard ON consignment_packing;
CREATE TRIGGER t_packing_guard BEFORE INSERT OR UPDATE OR DELETE ON consignment_packing
    FOR EACH ROW EXECUTE FUNCTION dispatch_guard_children();
DROP TRIGGER IF EXISTS c_dispatch_qty ON dispatch_source_map;
CREATE CONSTRAINT TRIGGER c_dispatch_qty AFTER INSERT OR UPDATE ON dispatch_source_map
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION c_dispatch_qty();

DROP TRIGGER IF EXISTS t_gate_run_append_only ON sheet_gate_run;
CREATE TRIGGER t_gate_run_append_only BEFORE UPDATE OR DELETE ON sheet_gate_run
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
