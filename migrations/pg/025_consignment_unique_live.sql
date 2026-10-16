-- 025 · 一张表一个目的地一张排货单 —— 只管还活着的单（10-13 负责人实测：提交被领星拒 → 退回修改 → 改目的地 500）
-- 出处：CA-D14（退回修改）· 016 consignment_one_per_dest_*
-- ★ 带外部留痕（erp_call / 待核实 / 领星单号）的排货单重派生时不删、改「已放弃」（留痕不断）；
--   改回原目的地要新开一张 —— 原唯一索引不分死活，会撞上已放弃的那张。
-- ★ 不写 scm. 前缀、不写 BEGIN/COMMIT（同 001）。

DROP INDEX IF EXISTS consignment_one_per_dest_wid;
DROP INDEX IF EXISTS consignment_one_per_dest_sid;
CREATE UNIQUE INDEX consignment_one_per_dest_wid
    ON consignment (sheet_id, channel, dest_wid) WHERE dest_wid IS NOT NULL AND our_stage NOT IN ('已放弃', '已取消');
CREATE UNIQUE INDEX consignment_one_per_dest_sid
    ON consignment (sheet_id, channel, dest_sid) WHERE dest_sid IS NOT NULL AND our_stage NOT IN ('已放弃', '已取消');
