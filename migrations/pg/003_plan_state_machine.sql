-- 003 · S1 状态机四件套：白名单 · 事件表 · 触发器
-- 出处：03 §3 / §3.2 · 04 §1 / §2 · S-2（哨兵与铸出事件）· S-4（in_flight）

-- ① 白名单：状态机本身是数据
CREATE TABLE IF NOT EXISTS plan_line_transition (
    from_state   text    NOT NULL,
    to_state     text    NOT NULL,
    kind         text    NOT NULL CONSTRAINT plan_line_transition_kind_known
                         CHECK (kind IN ('forward','back','cancel')),
    needs_reason boolean NOT NULL DEFAULT false,
    CONSTRAINT plan_line_transition_pkey PRIMARY KEY (from_state, to_state)
);

INSERT INTO plan_line_transition VALUES
 ('[*]','已提交','forward',false),          -- ★ S-2 哨兵：铸出这一步（04:67 S1-1）也留痕，事件链不断
 ('已提交','已确认','forward',false),
 ('已确认','已下单','forward',false),
 ('已下单','准备排货','forward',false),
 ('准备排货','已排货','forward',false),
 ('已排货','已完结','forward',false),
 ('已确认','已提交','back',   false),       -- ★ 全服务唯一一条退回
 ('已提交','已撤销','cancel', true),
 ('已确认','已撤销','cancel', true),
 ('已下单','已撤销','cancel', true),
 ('准备排货','已撤销','cancel', true),
 ('已排货','已撤销','cancel', true)
ON CONFLICT DO NOTHING;
-- ★ 表里没有以 已完结/已撤销 作 from_state 的行 → 终态不可离开，由外键裁决。

-- ② 事件表：唯一能改状态的入口
CREATE TABLE IF NOT EXISTS plan_line_event (
    seq        bigserial PRIMARY KEY,       -- ★ 序列，不用时间戳（时钟回拨会乱序）
    line_id    bigint NOT NULL REFERENCES plan_line(line_id),
    from_state text   NOT NULL,
    to_state   text   NOT NULL,
    actor      text   NOT NULL,
    reason     text,
    src        text,                        -- 触发来源：哪张采购单/排货单/哪轮回执
    at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_line_event_transition_fk FOREIGN KEY (from_state, to_state)
        REFERENCES plan_line_transition(from_state, to_state)   -- ★ 非法迁移 = 外键违反
);

-- ⓐ 乐观校验 + 写物化列（同时解决并发）
CREATE OR REPLACE FUNCTION sync_plan_line_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur text;
BEGIN
    SELECT state INTO cur FROM plan_line WHERE line_id = NEW.line_id FOR UPDATE;
    -- ★ 铸出事件：行已带 state='已提交' 插入，这里只核对、不再 UPDATE。
    --   （拦截器只挡 UPDATE OF state，所以 INSERT 那一刻的值必须在这里被验一次，
    --     否则「状态只能由事件推进」在铸记录这一刻就是假的。）
    IF NEW.from_state = '[*]' THEN
        IF cur IS DISTINCT FROM '已提交' THEN
            RAISE EXCEPTION '铸出事件要求行处于 已提交，当前 %', cur;
        END IF;
        RETURN NEW;
    END IF;
    IF cur IS DISTINCT FROM NEW.from_state THEN
        RAISE EXCEPTION 'from_state 不匹配：当前 %，事件称 %', cur, NEW.from_state;
    END IF;
    PERFORM set_config('app.in_state_sync','on',true);
    UPDATE plan_line SET state = NEW.to_state WHERE line_id = NEW.line_id;
    PERFORM set_config('app.in_state_sync','off',true);
    RETURN NEW;
END $$;

-- ⓑ 理由必填
CREATE OR REPLACE FUNCTION require_reason() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE need boolean;
BEGIN
    SELECT needs_reason INTO need FROM plan_line_transition
     WHERE from_state = NEW.from_state AND to_state = NEW.to_state;
    IF need AND coalesce(btrim(NEW.reason),'') = '' THEN
        RAISE EXCEPTION '这条迁移必须写理由：% → %', NEW.from_state, NEW.to_state;
    END IF;
    RETURN NEW;
END $$;

-- ⓒ 禁止直改 state
CREATE OR REPLACE FUNCTION state_must_go_through_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('app.in_state_sync', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION '状态只能由事件表推进，不许直接 UPDATE state';
    END IF;
    RETURN NEW;
END $$;

-- ⓓ 每条记录在事务提交前必须有铸出事件
CREATE OR REPLACE FUNCTION assert_birth_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM plan_line_event
                    WHERE line_id = NEW.line_id AND from_state = '[*]') THEN
        RAISE EXCEPTION '记录 % 没有铸出事件（[*]→已提交）', NEW.line_id;
    END IF;
    RETURN NULL;
END $$;

-- ⓔ 一版什么时候算「不在流转」：它的记录全进了终态。
-- ★ 一个实现，两个调用者（触发器 + 提交路径）—— 两份实现必然分叉（04:329）。
--   提交路径必须自己调一次，因为**铸出 0 条记录的空版本没有任何行能触发触发器**，
--   而它会永远占着「唯一在流转」那个位子，把这张计划的后续提交全挡住。
CREATE OR REPLACE FUNCTION close_rev_if_settled(p_plan_id bigint, p_rev int)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE plan_rev SET in_flight = false
     WHERE plan_id = p_plan_id AND rev = p_rev AND in_flight
       AND NOT EXISTS (SELECT 1 FROM plan_line
                        WHERE plan_id = p_plan_id AND rev = p_rev
                          AND state NOT IN ('已完结','已撤销'));
END $$;

CREATE OR REPLACE FUNCTION sync_rev_in_flight() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM close_rev_if_settled(NEW.plan_id, NEW.rev);
    RETURN NULL;
END $$;

-- ★ 触发器用 DROP IF EXISTS + CREATE：CREATE OR REPLACE TRIGGER 要 PG 14+，
--   而这台实例的版本未实测。
DROP TRIGGER IF EXISTS t_append_only ON plan_line_event;
CREATE TRIGGER t_append_only BEFORE UPDATE OR DELETE ON plan_line_event
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

DROP TRIGGER IF EXISTS t_require_reason ON plan_line_event;
CREATE TRIGGER t_require_reason BEFORE INSERT ON plan_line_event
    FOR EACH ROW EXECUTE FUNCTION require_reason();

DROP TRIGGER IF EXISTS t_sync_state ON plan_line_event;
CREATE TRIGGER t_sync_state AFTER INSERT ON plan_line_event
    FOR EACH ROW EXECUTE FUNCTION sync_plan_line_state();

DROP TRIGGER IF EXISTS t_state_is_readonly ON plan_line;
CREATE TRIGGER t_state_is_readonly BEFORE UPDATE OF state ON plan_line
    FOR EACH ROW EXECUTE FUNCTION state_must_go_through_event();

DROP TRIGGER IF EXISTS c_line_birth_event ON plan_line;
CREATE CONSTRAINT TRIGGER c_line_birth_event AFTER INSERT ON plan_line
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION assert_birth_event();

DROP TRIGGER IF EXISTS t_rev_in_flight ON plan_line;
CREATE TRIGGER t_rev_in_flight AFTER INSERT OR UPDATE OF state ON plan_line
    FOR EACH ROW EXECUTE FUNCTION sync_rev_in_flight();
