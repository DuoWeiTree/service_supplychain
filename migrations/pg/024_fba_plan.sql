-- 024 · FBA 发货计划提交（CC-D1：先做到领星 FBA 发货计划；CC-D3 实调：建计划不锁库存、无作废接口）
-- 出处：spec 2026-10-13-dispatch-submit-fba-plan-design · `00` CC-D1~D4
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。我们的名字不用领星占用词 shipment_plan。

-- ① 外部单引用多一类：FBA 发货计划批次（ext_no = 批次号 RP…）
ALTER TABLE ext_ref DROP CONSTRAINT IF EXISTS ext_ref_kind_known;
ALTER TABLE ext_ref ADD CONSTRAINT ext_ref_kind_known CHECK (kind IN ('ows', 'fba_plan'));

-- ② 批次下的计划号（R…）：锁 / 释放 / 编辑都按计划号调 —— 只追加
CREATE TABLE IF NOT EXISTS fba_plan_order (
    ext_ref_id bigint      PRIMARY KEY REFERENCES ext_ref(ext_ref_id),
    order_sns  text[]      NOT NULL CONSTRAINT fba_plan_order_nonempty CHECK (cardinality(order_sns) > 0),
    at         timestamptz NOT NULL DEFAULT now()
);

-- ③ 锁 / 释放库存事件（CC-D3：建计划不锁，必须另锁）—— 只追加；当前是否已锁 = 最后一条
CREATE TABLE IF NOT EXISTS fba_plan_stock (
    event_id   bigserial   PRIMARY KEY,
    ext_ref_id bigint      NOT NULL REFERENCES ext_ref(ext_ref_id),
    action     text        NOT NULL CONSTRAINT fba_plan_stock_action_known CHECK (action IN ('lock', 'unlock')),
    call_id    bigint      REFERENCES erp_call(call_id),
    actor      text        NOT NULL,
    at         timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS t_fba_plan_order_append_only ON fba_plan_order;
CREATE TRIGGER t_fba_plan_order_append_only BEFORE UPDATE OR DELETE ON fba_plan_order FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_fba_plan_stock_append_only ON fba_plan_stock;
CREATE TRIGGER t_fba_plan_stock_append_only BEFORE UPDATE OR DELETE ON fba_plan_stock FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

CREATE OR REPLACE VIEW v_fba_plan_lock AS
SELECT DISTINCT ON (ext_ref_id) ext_ref_id, action = 'lock' AS locked, at
  FROM fba_plan_stock ORDER BY ext_ref_id, event_id DESC;

-- ④ FBA 排货单已投送 = 每张未作废的计划批次都已锁库存（锁失败的不算投送，可只重锁）
CREATE OR REPLACE FUNCTION consignment_dispatch_lock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.to_state = '已投送' AND EXISTS (
        SELECT 1 FROM ext_ref e LEFT JOIN v_fba_plan_lock l USING (ext_ref_id)
         WHERE e.consignment_id = NEW.consignment_id AND e.kind = 'fba_plan'
           AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id)
           AND l.locked IS NOT TRUE) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_dispatch_needs_lock',
              MESSAGE = format('排货单 %s 有领星发货计划还没锁库存，不能算已投送', NEW.consignment_id);
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS t_consignment_dispatch_lock_guard ON consignment_event;
CREATE TRIGGER t_consignment_dispatch_lock_guard BEFORE INSERT ON consignment_event
    FOR EACH ROW EXECUTE FUNCTION consignment_dispatch_lock_guard();
