-- 013 · 再提交保留未变记录（R2）· 跳过原因拆空与 0（R1）
-- 出处：spec 2026-10-09-resubmit-and-purchase-entry §2 §3 · S-4（只 1 版在流转，不变）· S-14（新原因 → 加值走迁移）
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① S-14：空 ≠ 0。没填采购量 = 没打算采购，填了 0 = 明确表态
ALTER TABLE plan_submit_skip DROP CONSTRAINT IF EXISTS plan_submit_skip_reason_known;
ALTER TABLE plan_submit_skip ADD CONSTRAINT plan_submit_skip_reason_known
    CHECK (reason IN ('empty_purchase','zero_purchase','no_claimed_msku'));

-- ② 记录换版：没变的记录原样带进新版（同一 line_id，状态不动）。
-- ★ 不走 plan_line_event：白名单里没有同态边，硬加一条 X→X 等于给状态机开一个
--   「什么都没发生」的迁移，所有按白名单穷举的地方都要为它破例。
-- ★ plan_line.rev 改成「当前归属哪一版」，而这张表记「它从哪一版带过来」——
--   一版当时包含哪些记录 = rev 列是它的 ∪ 从它带走的，二者合起来不丢历史。
CREATE TABLE IF NOT EXISTS plan_line_carry (
    carry_id  bigserial PRIMARY KEY,
    line_id   bigint NOT NULL REFERENCES plan_line(line_id),
    plan_id   bigint NOT NULL,
    from_rev  int    NOT NULL,
    to_rev    int    NOT NULL,
    actor     text   NOT NULL,
    src       text   NOT NULL,
    at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_line_carry_forward CHECK (to_rev > from_rev),
    CONSTRAINT plan_line_carry_from_fk FOREIGN KEY (plan_id, from_rev) REFERENCES plan_rev(plan_id, rev),
    CONSTRAINT plan_line_carry_to_fk   FOREIGN KEY (plan_id, to_rev)   REFERENCES plan_rev(plan_id, rev),
    CONSTRAINT plan_line_carry_once UNIQUE (line_id, from_rev)
);
CREATE INDEX IF NOT EXISTS plan_line_carry_by_rev_idx ON plan_line_carry (plan_id, from_rev);

-- ⓐ 换版只能由这张表推进（同 003 「状态只能由事件推进」的形状）
CREATE OR REPLACE FUNCTION apply_plan_line_carry() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE l record;
BEGIN
    SELECT plan_id, rev, state INTO l FROM plan_line WHERE line_id = NEW.line_id FOR UPDATE;
    IF l.plan_id IS DISTINCT FROM NEW.plan_id OR l.rev IS DISTINCT FROM NEW.from_rev THEN
        RAISE EXCEPTION '换版不匹配：记录 % 当前在 计划 % rev %，换版称 计划 % rev %',
              NEW.line_id, l.plan_id, l.rev, NEW.plan_id, NEW.from_rev;
    END IF;
    -- ★ 已撤销的不属于任何一版；已完结 要带（裁定 A）—— 不带，没变的格子会被当成新增再铸一条、再买一次
    IF l.state = '已撤销' THEN
        RAISE EXCEPTION '记录 % 已撤销，不能带进新版', NEW.line_id;
    END IF;
    -- 只许带进这张计划最新的一版：带进中间某版，历史就解释不通
    IF NEW.to_rev IS DISTINCT FROM (SELECT max(rev) FROM plan_rev WHERE plan_id = NEW.plan_id) THEN
        RAISE EXCEPTION '换版目标 rev % 不是计划 % 的最新版', NEW.to_rev, NEW.plan_id;
    END IF;
    PERFORM set_config('app.in_rev_carry','on',true);
    UPDATE plan_line SET rev = NEW.to_rev WHERE line_id = NEW.line_id;
    PERFORM set_config('app.in_rev_carry','off',true);
    -- ★ 旧版的活记录被带走后可能已一条不剩：t_rev_in_flight 只盯 state，看不到这次挪动
    PERFORM close_rev_if_settled(NEW.plan_id, NEW.from_rev);
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION rev_must_go_through_carry() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.rev IS DISTINCT FROM OLD.rev
       AND current_setting('app.in_rev_carry', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION '记录归属哪一版只能由 plan_line_carry 推进，不许直接 UPDATE rev';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS t_carry_apply ON plan_line_carry;
CREATE TRIGGER t_carry_apply AFTER INSERT ON plan_line_carry
    FOR EACH ROW EXECUTE FUNCTION apply_plan_line_carry();
DROP TRIGGER IF EXISTS t_carry_append_only ON plan_line_carry;
CREATE TRIGGER t_carry_append_only BEFORE UPDATE OR DELETE ON plan_line_carry
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_rev_is_readonly ON plan_line;
CREATE TRIGGER t_rev_is_readonly BEFORE UPDATE OF rev ON plan_line
    FOR EACH ROW EXECUTE FUNCTION rev_must_go_through_carry();

-- ⓑ 再提交时新版先以「不在流转」立起来（旧版此刻还占着部分唯一索引），
--   旧版活记录处置完、自己的记录落定后，再由这里判定它是否在流转。
-- ★ 与 close_rev_if_settled 同一判据的反面：有任一非终态记录 = 在流转。
--   旧版没让出位子时这里撞 plan_rev_one_in_flight_idx —— 那是处置漏了一条，要响，不能吞。
CREATE OR REPLACE FUNCTION open_rev_if_live(p_plan_id bigint, p_rev int)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE live boolean;
BEGIN
    SELECT EXISTS (SELECT 1 FROM plan_line
                    WHERE plan_id = p_plan_id AND rev = p_rev
                      AND state NOT IN ('已完结','已撤销')) INTO live;
    UPDATE plan_rev SET in_flight = live WHERE plan_id = p_plan_id AND rev = p_rev;
    RETURN live;
END $$;
