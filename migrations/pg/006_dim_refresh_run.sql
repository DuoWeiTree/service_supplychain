-- 006 · 维度镜像刷新留痕
-- 出处：设计 docs/superpowers/specs/2026-09-22-mirror-refresh-design.md §6（依据 07:483-485）
--
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT：同 001 的理由。

CREATE TABLE IF NOT EXISTS dim_refresh_run (
    run_id              bigserial PRIMARY KEY,
    mirror              text        NOT NULL,
    trigger             text        NOT NULL
        CONSTRAINT dim_refresh_run_trigger_known
        CHECK (trigger IN ('scheduler', 'cli', 'api')),
    -- ★ 可空：调度触发的那一轮背后没有人。写成 'system' 会让「机器跑的」
    --   和「某个叫 system 的人跑的」长得一模一样。
    actor               text        REFERENCES actor(actor_id),
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    source_max_captured date,          -- 该批 CH 数据的 max(_captured_date)（07:484）
    rows_in             integer,
    rows_dropped        integer,
    drop_reasons        jsonb,         -- 丢的那一侧按原因分列，含 no_baseline
    -- ★ 默认 false：崩在半路没人写结果时，它必须看起来像失败。
    ok                  boolean     NOT NULL DEFAULT false,
    error               text
);

CREATE INDEX IF NOT EXISTS dim_refresh_run_by_mirror
    ON dim_refresh_run (mirror, started_at DESC);

DROP TRIGGER IF EXISTS dim_refresh_run_append_only ON dim_refresh_run;
CREATE TRIGGER dim_refresh_run_append_only
    BEFORE UPDATE OR DELETE ON dim_refresh_run
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
