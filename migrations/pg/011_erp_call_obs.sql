-- 011 · 出站留痕 · 待处理队列 · 领星观察（只追加）
-- 出处：spec §3.3 §6 · 03:1519-1531（erp_call 原型）· CLAUDE.md 出站 #3 #5 · 铁律 8
-- ★ outcome 取值与 03 原型不同（in_flight/ok/rejected/not_sent/unknown）：
--   「没发出去」与「发出去了不知道结果」处置相反（spec §6.2），03 的 conn_failed/timeout
--   按「怎么失败」分，不按「能不能重试」分。文档同步见计划 Task 16。

CREATE TABLE IF NOT EXISTS erp_call (
    call_id     bigserial PRIMARY KEY,
    endpoint    text  NOT NULL,                  -- erp/endpoints.py 的登记名
    side_effect text  NOT NULL CONSTRAINT erp_call_side_effect_known
                      CHECK (side_effect IN ('read','reversible','irreversible')),
    mode        text  NOT NULL CONSTRAINT erp_call_mode_known CHECK (mode IN ('fake','live')),
    po_id       bigint REFERENCES po(po_id),   -- ★ 不级联：试过下单的草稿只能「作废」不能删（证据要留）
    idem_key    text,
    retry_of    bigint REFERENCES erp_call(call_id),
    actor       text  NOT NULL,
    request     jsonb NOT NULL,                  -- ★ 已脱敏（无 token / sign）
    outcome     text  NOT NULL DEFAULT 'in_flight' CONSTRAINT erp_call_outcome_known
                      CHECK (outcome IN ('in_flight','ok','rejected','not_sent','unknown')),
    http_status int,
    lx_code     text,
    lx_msg      text,
    cause       text,
    response    jsonb,
    host        text,
    path        text,
    elapsed_ms  int,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS erp_call_one_in_flight_per_po
    ON erp_call (po_id) WHERE outcome = 'in_flight' AND side_effect = 'irreversible';
CREATE INDEX IF NOT EXISTS erp_call_by_po ON erp_call (po_id, call_id);

-- ★ 一次性收尾：只能从 in_flight 改一次，且只能改结果列
CREATE OR REPLACE FUNCTION erp_call_close_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'erp_call 是出站证据，不许删除';
    END IF;
    IF OLD.outcome <> 'in_flight' THEN
        RAISE EXCEPTION 'erp_call % 只能收尾一次（已是 %）', OLD.call_id, OLD.outcome;
    END IF;
    IF (NEW.endpoint, NEW.side_effect, NEW.mode, NEW.po_id, NEW.idem_key, NEW.retry_of,
        NEW.actor, NEW.request, NEW.started_at)
       IS DISTINCT FROM
       (OLD.endpoint, OLD.side_effect, OLD.mode, OLD.po_id, OLD.idem_key, OLD.retry_of,
        OLD.actor, OLD.request, OLD.started_at) THEN
        RAISE EXCEPTION 'erp_call % 只能收尾结果列，请求侧不可改', OLD.call_id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS t_erp_call_close_once ON erp_call;
CREATE TRIGGER t_erp_call_close_once BEFORE UPDATE OR DELETE ON erp_call
    FOR EACH ROW EXECUTE FUNCTION erp_call_close_once();

-- 待处理队列（spec §6.6）：同一张单同一类最多一条未处理
CREATE TABLE IF NOT EXISTS po_attention (
    attention_id   bigserial PRIMARY KEY,
    po_id          bigint NOT NULL REFERENCES po(po_id) ON DELETE CASCADE,
    kind           text   NOT NULL CONSTRAINT po_attention_kind_known
                          CHECK (kind IN ('needs_verify','landed_mismatch')),
    opened_at      timestamptz NOT NULL DEFAULT now(),
    opened_by_call bigint REFERENCES erp_call(call_id),
    resolved_at    timestamptz,
    resolution     text CONSTRAINT po_attention_resolution_known
                        CHECK (resolution IN ('rechecked_found','found_existing',
                                              'confirmed_absent','mismatch_acknowledged')),
    resolved_by    text REFERENCES actor(actor_id),
    reason         text,
    detail         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS po_attention_one_open
    ON po_attention (po_id, kind) WHERE resolved_at IS NULL;

CREATE OR REPLACE FUNCTION po_attention_resolve_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.resolved_at IS NOT NULL THEN
        RAISE EXCEPTION '待处理 % 已处理，不可再改', OLD.attention_id;
    END IF;
    IF NEW.resolved_at IS NULL OR NEW.resolution IS NULL OR NEW.resolved_by IS NULL THEN
        RAISE EXCEPTION '处理待处理必须同时给出 resolved_at / resolution / resolved_by';
    END IF;
    -- 处理是唯一允许的 UPDATE，不能顺手改写「这条待处理是关于什么的」
    IF (NEW.po_id, NEW.kind, NEW.opened_at, NEW.opened_by_call, NEW.detail)
       IS DISTINCT FROM
       (OLD.po_id, OLD.kind, OLD.opened_at, OLD.opened_by_call, OLD.detail) THEN
        RAISE EXCEPTION '待处理 % 只能填处理结果，身份列不可改', OLD.attention_id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS t_po_attention_resolve_once ON po_attention;
CREATE TRIGGER t_po_attention_resolve_once BEFORE UPDATE ON po_attention
    FOR EACH ROW EXECUTE FUNCTION po_attention_resolve_once();

-- 领星观察（只追加，铁律 8）。M1 只用 po_item_obs；po_snapshot 先建出来供 M3
CREATE TABLE IF NOT EXISTS po_snapshot (
    snap_id            bigserial PRIMARY KEY,
    order_sn           text NOT NULL,
    status             int  NOT NULL,     -- 3 待提交 / 1 待下单 / 2 待到货 / 9 完成 / -1 作废 / 121 / 122
    status_shipped     int,
    quantity_real      int,
    quantity_entry     int,
    quantity_receive   int,
    source             text NOT NULL CONSTRAINT po_snapshot_source_known CHECK (source IN ('ch','api','sim')),
    source_captured_at timestamptz NOT NULL,
    observed_at        timestamptz NOT NULL DEFAULT now(),
    call_id            bigint REFERENCES erp_call(call_id)
);
CREATE INDEX IF NOT EXISTS po_snapshot_latest ON po_snapshot (order_sn, source_captured_at DESC, snap_id DESC);

CREATE TABLE IF NOT EXISTS po_item_obs (
    obs_id             bigserial PRIMARY KEY,
    order_sn           text NOT NULL,
    lx_item_id         text,
    sku                text NOT NULL,
    wid                int,
    quantity_real      int  NOT NULL,
    price              numeric(18,4),
    source             text NOT NULL CONSTRAINT po_item_obs_source_known CHECK (source IN ('ch','api','sim')),
    source_captured_at timestamptz NOT NULL,
    observed_at        timestamptz NOT NULL DEFAULT now(),
    call_id            bigint REFERENCES erp_call(call_id)
);
CREATE INDEX IF NOT EXISTS po_item_obs_latest ON po_item_obs (order_sn, source_captured_at DESC, obs_id DESC);

DROP TRIGGER IF EXISTS t_po_snapshot_append_only ON po_snapshot;
CREATE TRIGGER t_po_snapshot_append_only BEFORE UPDATE OR DELETE ON po_snapshot
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_po_item_obs_append_only ON po_item_obs;
CREATE TRIGGER t_po_item_obs_append_only BEFORE UPDATE OR DELETE ON po_item_obs
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
