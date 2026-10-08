-- 010 · 阶段 B 采购单：供应商镜像 · 采购单三表 · 白名单状态机 · 冻结 · 计划单记录退回边
-- 出处：spec 2026-10-07 §3.1 §3.2 · 03 §po（03:394-473）· 04:992 · PB-D5 · B7 · B8
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① 供应商：MIRROR。源 = CH 采购单行上的 supplier_id / supplier_name（实测 3 家，spec §2）
CREATE TABLE IF NOT EXISTS supplier (
    supplier_id  text PRIMARY KEY,
    name         text NOT NULL,
    refreshed_at timestamptz NOT NULL
);

-- ② 采购单头
CREATE TABLE IF NOT EXISTS po (
    po_id           bigserial PRIMARY KEY,
    supplier_id     text     NOT NULL REFERENCES supplier(supplier_id),
    purchaser_id    int      NOT NULL,                 -- 领星采购方（实测 111 / 185）
    opt_uid         bigint   NOT NULL,                 -- 领星采购员
    qc_type         smallint NOT NULL CONSTRAINT po_qc_type_known CHECK (qc_type IN (0,1,2)),  -- ★ B8
    wid             int      REFERENCES warehouse(wid),  -- ★ B7：单头可空，仓在行上
    -- ★ V7：幂等键由 po_id 派生，撞不上领星自填的 PO… 形态（实测 custom=order_sn 143/143）
    custom_order_sn text GENERATED ALWAYS AS ('SCM-PO-' || po_id::text) STORED UNIQUE,
    order_sn        text UNIQUE,
    state           text     NOT NULL DEFAULT '草稿',
    placed_mode     text     CONSTRAINT po_placed_mode_known CHECK (placed_mode IN ('fake','live')),
    placed_at       timestamptz,
    draft_version   int      NOT NULL DEFAULT 1,
    -- ★ 试过下单（有 erp_call）的草稿不能硬删 —— 出站证据要留着；只能「作废草稿」：
    --   清掉明细与映射（释放额度），单头留档
    discarded_at    timestamptz,
    discarded_by    text     REFERENCES actor(actor_id),
    created_by      text     NOT NULL REFERENCES actor(actor_id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ③ 明细：一单内同货号同仓只一行（N-1「下单时合并」由来源映射承载）
CREATE TABLE IF NOT EXISTS po_line (
    po_id              bigint NOT NULL REFERENCES po(po_id) ON DELETE CASCADE,
    line_no            int    NOT NULL,
    sku                text   NOT NULL REFERENCES sku_catalog(sku),
    wid                int    NOT NULL REFERENCES warehouse(wid),          -- ★ B7
    units              int    NOT NULL CONSTRAINT po_line_units_positive CHECK (units > 0),
    price              numeric(18,4) NOT NULL CONSTRAINT po_line_price_nonneg CHECK (price >= 0),
    expect_arrive_date date,
    unplanned_reason   text,                                               -- ★ PB-D9
    lx_item_id         text,                                               -- 下单后回填，回读行级键
    CONSTRAINT po_line_pkey PRIMARY KEY (po_id, line_no),
    CONSTRAINT po_line_one_per_sku_wid UNIQUE (po_id, sku, wid)
);

-- ④ 承重墙①
CREATE TABLE IF NOT EXISTS po_source_map (
    po_id     bigint NOT NULL,
    line_no   int    NOT NULL,
    line_id   bigint NOT NULL REFERENCES plan_line(line_id),
    seller_id text   NOT NULL REFERENCES seller(seller_id),
    units     int    NOT NULL CONSTRAINT po_source_units_positive CHECK (units > 0),
    CONSTRAINT po_source_map_pkey PRIMARY KEY (po_id, line_no, line_id, seller_id),
    CONSTRAINT po_source_line_fk FOREIGN KEY (po_id, line_no)
        REFERENCES po_line(po_id, line_no) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS po_source_by_line_idx   ON po_source_map (line_id);
CREATE INDEX IF NOT EXISTS po_source_by_seller_idx ON po_source_map (line_id, seller_id);

-- ⑤ 采购单本地状态机：白名单 + 事件表（同 003 的形状）
CREATE TABLE IF NOT EXISTS po_transition (
    from_state text NOT NULL,
    to_state   text NOT NULL,
    CONSTRAINT po_transition_pkey PRIMARY KEY (from_state, to_state)
);
INSERT INTO po_transition VALUES ('[*]','草稿'), ('草稿','已下单') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS po_event (
    seq        bigserial PRIMARY KEY,
    po_id      bigint NOT NULL REFERENCES po(po_id) ON DELETE CASCADE,
    from_state text   NOT NULL,
    to_state   text   NOT NULL,
    actor      text   NOT NULL,
    reason     text,
    src        text,
    at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT po_event_transition_fk FOREIGN KEY (from_state, to_state)
        REFERENCES po_transition(from_state, to_state)
);

CREATE OR REPLACE FUNCTION po_birth_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO po_event (po_id, from_state, to_state, actor, src)
    VALUES (NEW.po_id, '[*]', '草稿', NEW.created_by, 'create');
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION sync_po_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text; sn text; mode text;
BEGIN
    IF NEW.from_state = '[*]' THEN
        -- 出生事件只认第一条，否则已下单的单也能被追加一条 [*]→草稿
        IF EXISTS (SELECT 1 FROM po_event WHERE po_id = NEW.po_id AND seq < NEW.seq) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_birth_once',
                  MESSAGE = format('po %s 已有出生事件，不可重复', NEW.po_id);
        END IF;
        RETURN NEW;
    END IF;
    SELECT state, order_sn, placed_mode INTO cur, sn, mode FROM po WHERE po_id = NEW.po_id FOR UPDATE;
    IF cur IS DISTINCT FROM NEW.from_state THEN
        RAISE EXCEPTION 'po % from_state 不匹配：当前 %，事件称 %', NEW.po_id, cur, NEW.from_state;
    END IF;
    IF NEW.to_state = '已下单' AND (sn IS NULL OR mode IS NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_placed_needs_order_sn',
              MESSAGE = format('po %s 转已下单前必须已记 order_sn 与 placed_mode', NEW.po_id);
    END IF;
    PERFORM set_config('app.in_po_state_sync','on',true);
    UPDATE po SET state = NEW.to_state, updated_at = now() WHERE po_id = NEW.po_id;
    PERFORM set_config('app.in_po_state_sync','off',true);
    RETURN NEW;
END $$;

-- ⑥ 冻结：已下单之后，单头只许状态同步改 state；明细只许回填 lx_item_id；映射不许动
CREATE OR REPLACE FUNCTION po_guard_header() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state IS DISTINCT FROM OLD.state
       AND current_setting('app.in_po_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION '采购单状态只能由 po_event 推进';
    END IF;
    IF OLD.state = '已下单' AND current_setting('app.in_po_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_frozen',
              MESSAGE = format('采购单 %s 已下单，不可改', OLD.po_id);
    END IF;
    RETURN NEW;
END $$;

-- 硬删已下单的单会连带抹掉明细、映射（释放额度）和事件；草稿照常可删（级联清事件）
CREATE OR REPLACE FUNCTION po_guard_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state = '已下单' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_frozen',
              MESSAGE = format('采购单 %s 已下单，不可删除', OLD.po_id);
    END IF;
    RETURN OLD;
END $$;

-- 状态只能经 po_event 推进，出生时就不许直接写成别的状态
CREATE OR REPLACE FUNCTION po_guard_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state IS DISTINCT FROM '草稿' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_born_as_draft',
              MESSAGE = format('采购单只能以 草稿 出生，收到 %s', NEW.state);
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION po_guard_children() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text; pid bigint;
BEGIN
    pid := CASE WHEN TG_OP = 'DELETE' THEN OLD.po_id ELSE NEW.po_id END;
    SELECT state INTO st FROM po WHERE po_id = pid;
    IF st = '已下单' THEN
        IF TG_TABLE_NAME = 'po_line' AND TG_OP = 'UPDATE'
           AND (to_jsonb(NEW) - 'lx_item_id') = (to_jsonb(OLD) - 'lx_item_id') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_frozen',
              MESSAGE = format('采购单 %s 已下单，%s 不可 %s', pid, TG_TABLE_NAME, TG_OP);
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- ⑦ 映射只能挂在可组单的记录上（03:942）
CREATE OR REPLACE FUNCTION po_source_precondition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
    SELECT state INTO st FROM plan_line WHERE line_id = NEW.line_id;
    IF st NOT IN ('已提交','已确认') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_source_line_not_orderable',
              MESSAGE = format('记录 %s 处于 %s，只有 已提交/已确认 能组单', NEW.line_id, st);
    END IF;
    RETURN NEW;
END $$;

-- ⑦b 领星行号一次写入：回填是唯一被放行的已下单改动，改第二次就是在改证据
CREATE OR REPLACE FUNCTION po_line_lx_item_id_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.lx_item_id IS NOT NULL AND NEW.lx_item_id IS DISTINCT FROM OLD.lx_item_id THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'po_line_lx_item_id_set_once',
              MESSAGE = format('po %s 行 %s 的 lx_item_id 已是 %s，不可改为 %s',
                               OLD.po_id, OLD.line_no, OLD.lx_item_id, NEW.lx_item_id);
    END IF;
    RETURN NEW;
END $$;

-- ⑦c 事件只追加（铁律 8）。唯一放行：删草稿时随单头级联 —— 级联执行时单头已不可见
CREATE OR REPLACE FUNCTION po_event_guard_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM po WHERE po_id = OLD.po_id) THEN
        RAISE EXCEPTION 'po_event 是只追加表，不允许 DELETE（证据一旦可改，就不再是证据）；po % 仍在', OLD.po_id;
    END IF;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS t_po_birth ON po;
CREATE TRIGGER t_po_birth AFTER INSERT ON po FOR EACH ROW EXECUTE FUNCTION po_birth_event();
DROP TRIGGER IF EXISTS t_po_event_append_only ON po_event;
CREATE TRIGGER t_po_event_append_only BEFORE UPDATE ON po_event
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_po_event_no_delete ON po_event;
CREATE TRIGGER t_po_event_no_delete BEFORE DELETE ON po_event
    FOR EACH ROW EXECUTE FUNCTION po_event_guard_delete();
DROP TRIGGER IF EXISTS t_po_sync_state ON po_event;
CREATE TRIGGER t_po_sync_state AFTER INSERT ON po_event FOR EACH ROW EXECUTE FUNCTION sync_po_state();
DROP TRIGGER IF EXISTS t_po_guard ON po;
CREATE TRIGGER t_po_guard BEFORE UPDATE ON po FOR EACH ROW EXECUTE FUNCTION po_guard_header();
DROP TRIGGER IF EXISTS t_po_guard_delete ON po;
CREATE TRIGGER t_po_guard_delete BEFORE DELETE ON po FOR EACH ROW EXECUTE FUNCTION po_guard_delete();
DROP TRIGGER IF EXISTS t_po_guard_insert ON po;
CREATE TRIGGER t_po_guard_insert BEFORE INSERT ON po FOR EACH ROW EXECUTE FUNCTION po_guard_insert();
DROP TRIGGER IF EXISTS t_po_line_guard ON po_line;
CREATE TRIGGER t_po_line_guard BEFORE INSERT OR UPDATE OR DELETE ON po_line
    FOR EACH ROW EXECUTE FUNCTION po_guard_children();
DROP TRIGGER IF EXISTS t_po_source_guard ON po_source_map;
CREATE TRIGGER t_po_source_guard BEFORE INSERT OR UPDATE OR DELETE ON po_source_map
    FOR EACH ROW EXECUTE FUNCTION po_guard_children();
DROP TRIGGER IF EXISTS t_po_source_pre ON po_source_map;
-- ★ UPDATE OF line_id 同样要判：否则把映射改挂到已撤销记录上能绕过本闸
CREATE TRIGGER t_po_source_pre BEFORE INSERT OR UPDATE OF line_id ON po_source_map
    FOR EACH ROW EXECUTE FUNCTION po_source_precondition();
DROP TRIGGER IF EXISTS t_po_line_lx_item_id_once ON po_line;
CREATE TRIGGER t_po_line_lx_item_id_once BEFORE UPDATE ON po_line
    FOR EACH ROW EXECUTE FUNCTION po_line_lx_item_id_once();

-- ⑧ 计划单记录：作废退回边（PB-D5）。由量判据自动落点，不需要理由
INSERT INTO plan_line_transition VALUES
 ('已下单','已确认','back',false),
 ('已下单','已提交','back',false)
ON CONFLICT DO NOTHING;
