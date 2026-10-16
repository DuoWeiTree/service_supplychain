-- 026 · 新建 FBA 排货表时的默认分配留痕（UX-D7 ~ D10 · spec 2026-10-16-dispatch-detail-design §1.4）
-- ★ 填进去的格子就是人的格子（dispatch_arrangement），不加来源列（铁律 6）；
--   「按什么规则、什么输入、填了哪些格」只记在这里，只追加（铁律 8）。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

CREATE TABLE IF NOT EXISTS dispatch_autofill (
    autofill_id bigserial   PRIMARY KEY,
    sheet_id    bigint      NOT NULL REFERENCES sheet(sheet_id),
    rule        text        NOT NULL,
    outcome     text        NOT NULL CONSTRAINT dispatch_autofill_outcome_known CHECK (outcome IN ('done', 'failed')),
    inputs      jsonb       NOT NULL,                 -- 输入快照：记录 · 各店期望 · 库存截至 · 用到的桶与已承诺
    filled      jsonb       NOT NULL DEFAULT '[]',    -- [{arrangement_id, line_id, seller_sku, sid, from_wid, units}]
    skipped     jsonb       NOT NULL DEFAULT '[]',    -- [{line_id, sid?, seller_sku?, reason, …}]
    error       jsonb,                                -- failed：哪一步、错误码、点名字段
    elapsed_ms  int         NOT NULL,
    actor       text        NOT NULL REFERENCES actor(actor_id),
    at          timestamptz NOT NULL DEFAULT now(),
    -- ★ UX-D7「新建时自动填一次」：一张表一次，之后人改格子不重填
    CONSTRAINT dispatch_autofill_once_per_sheet UNIQUE (sheet_id),
    CONSTRAINT dispatch_autofill_failed_has_error CHECK (outcome <> 'failed' OR error IS NOT NULL)
);

DROP TRIGGER IF EXISTS t_dispatch_autofill_append_only ON dispatch_autofill;
CREATE TRIGGER t_dispatch_autofill_append_only BEFORE UPDATE OR DELETE ON dispatch_autofill
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
