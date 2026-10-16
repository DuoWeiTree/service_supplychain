-- 023 · 领星备货单回执（CB-D9：定时轮询 + 手动现查；AR-D1：只读领星 API）
-- 出处：spec 2026-10-13-dispatch-receipt-design §3 / §4 · `04` §4.4 / §7.5 · `03` §5 ext_stage_observation 草案
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① 一轮回执：开始一行、结束一行（两张都只追加 —— 「这轮读了几张、谁触发的」可查）
CREATE TABLE IF NOT EXISTS receipt_run (
    run_id     bigserial   PRIMARY KEY,
    trigger    text        NOT NULL CONSTRAINT receipt_run_trigger_known CHECK (trigger IN ('scheduler', 'manual')),
    actor      text        REFERENCES actor(actor_id),
    sheet_id   bigint      REFERENCES sheet(sheet_id),          -- 手动现查是哪张表；定时为空
    started_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT receipt_run_manual_needs_actor CHECK (trigger <> 'manual' OR actor IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS receipt_run_result (
    run_id      bigint      PRIMARY KEY REFERENCES receipt_run(run_id),
    finished_at timestamptz NOT NULL DEFAULT now(),
    refs        int         NOT NULL,
    ok          int         NOT NULL,
    failed      int         NOT NULL,
    skipped     int         NOT NULL,              -- 表被提交 / 取消 / 退回占着，本轮没读（不是失败）
    archived    bigint[]    NOT NULL DEFAULT '{}', -- 本轮归档的排货单
    failures    jsonb       NOT NULL               -- [{ext_no, outcome, lx_msg, cause}]
);

-- ② 某轮读到某张领星单处于什么进度 —— 只追加；倒退的读回照写（当前进度由视图取，结构上不倒退）
CREATE TABLE IF NOT EXISTS ext_stage_obs (
    obs_id      bigserial   PRIMARY KEY,
    ext_ref_id  bigint      NOT NULL REFERENCES ext_ref(ext_ref_id),
    run_id      bigint      NOT NULL REFERENCES receipt_run(run_id),
    observed_at timestamptz NOT NULL DEFAULT now(),
    status      text        NOT NULL,              -- 领星原值；「不存在或已删除」记 'gone'
    stage       text        NOT NULL,              -- 领星的词：待审核 / 待配货 / 待发货 / 待收货 / 已完成 / 已驳回 / 已撤销 / 领星无此单
    stage_rank  int         NOT NULL,              -- 旁路（非前进终态）= -1
    sub_status  text,                              -- 待收货时分「部分收货」（`04` §4.4 陷阱①）
    detail      jsonb       NOT NULL DEFAULT '{}'  -- 发货 / 到达 / 签收时间、物流单号等原样
);
CREATE INDEX IF NOT EXISTS ext_stage_obs_by_ref ON ext_stage_obs (ext_ref_id, observed_at);

DROP TRIGGER IF EXISTS t_receipt_run_append_only ON receipt_run;
CREATE TRIGGER t_receipt_run_append_only BEFORE UPDATE OR DELETE ON receipt_run FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_receipt_run_result_append_only ON receipt_run_result;
CREATE TRIGGER t_receipt_run_result_append_only BEFORE UPDATE OR DELETE ON receipt_run_result FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_ext_stage_obs_append_only ON ext_stage_obs;
CREATE TRIGGER t_ext_stage_obs_append_only BEFORE UPDATE OR DELETE ON ext_stage_obs FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();

-- ③ 当前进度：有旁路取最新的旁路，否则取 rank 最大（同 rules/receipt.current）
CREATE OR REPLACE VIEW v_ext_stage AS
SELECT ext_ref_id,
       CASE WHEN bool_or(stage_rank < 0)
            THEN (array_agg(stage ORDER BY observed_at DESC, obs_id DESC) FILTER (WHERE stage_rank < 0))[1]
            ELSE (array_agg(stage ORDER BY stage_rank DESC, observed_at DESC, obs_id DESC))[1] END AS stage,
       CASE WHEN bool_or(stage_rank < 0) THEN -1 ELSE max(stage_rank) END AS stage_rank,
       max(observed_at) AS observed_at
  FROM ext_stage_obs GROUP BY ext_ref_id;

-- ④ 归档前置（`04` S4 #16）：排货单的全部未作废领星单当前进度都是「已完成」，且至少一张
CREATE OR REPLACE FUNCTION consignment_archive_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.to_state = '已归档' AND (
           NOT EXISTS (SELECT 1 FROM ext_ref e WHERE e.consignment_id = NEW.consignment_id
                          AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id))
        OR EXISTS (SELECT 1 FROM ext_ref e LEFT JOIN v_ext_stage s USING (ext_ref_id)
                    WHERE e.consignment_id = NEW.consignment_id
                      AND NOT EXISTS (SELECT 1 FROM ext_ref_void v WHERE v.ext_ref_id = e.ext_ref_id)
                      AND s.stage IS DISTINCT FROM '已完成')) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'consignment_archive_needs_complete',
              MESSAGE = format('排货单 %s 还有领星备货单没到「已完成」，不能归档', NEW.consignment_id);
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS t_consignment_archive_guard ON consignment_event;
CREATE TRIGGER t_consignment_archive_guard BEFORE INSERT ON consignment_event
    FOR EACH ROW EXECUTE FUNCTION consignment_archive_guard();
