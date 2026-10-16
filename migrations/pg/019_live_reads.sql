-- 019 · 排货页数据源取较新（负责人 2026-10-11：CA-D12）
-- 出处：00 CA-D12 · 10-12 实测：领星 purchaseOrderList item_list 行级带 quantity_entry / quantity_receive；
--       inventoryDetails 实时（不等 CH 次日采集）
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

-- ① 采购单行级 已入库 / 待到货（领星读回）。可空：老观测没有这两列 = 未知，不是 0
ALTER TABLE po_item_obs ADD COLUMN IF NOT EXISTS quantity_entry int;
ALTER TABLE po_item_obs ADD COLUMN IF NOT EXISTS quantity_receive int;

-- ② 本地仓库存现读（领星 inventoryDetails）。★ 只追加（铁律 8）：一次现读 = 同一 read_id 的一批行；
--    读到「这个货号在这个仓没有行」不写行，整批靠 read 头记录「查过了」
CREATE TABLE IF NOT EXISTS stock_read (
    read_id     bigserial PRIMARY KEY,
    sku         text NOT NULL,
    call_id     bigint REFERENCES erp_call(call_id),
    rows_in     int  NOT NULL,
    dropped     jsonb NOT NULL,           -- 非本地仓 / 认不出的店：计数（统计被丢掉的那一侧）
    actor       text NOT NULL,
    read_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stock_read_latest ON stock_read (sku, read_at DESC, read_id DESC);

CREATE TABLE IF NOT EXISTS stock_obs (
    read_id    bigint NOT NULL REFERENCES stock_read(read_id),
    sku        text   NOT NULL,
    wid        int    NOT NULL,
    bucket     text   NOT NULL,           -- 'shared' 或店铺 sid（CA-D8）
    valid      int    NOT NULL,           -- product_valid_num（只读这一列，02 §3.1）
    CONSTRAINT stock_obs_pkey PRIMARY KEY (read_id, wid, bucket)
);

DROP TRIGGER IF EXISTS t_stock_read_append_only ON stock_read;
CREATE TRIGGER t_stock_read_append_only BEFORE UPDATE OR DELETE ON stock_read
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
DROP TRIGGER IF EXISTS t_stock_obs_append_only ON stock_obs;
CREATE TRIGGER t_stock_obs_append_only BEFORE UPDATE OR DELETE ON stock_obs
    FOR EACH ROW EXECUTE FUNCTION forbid_update_delete();
