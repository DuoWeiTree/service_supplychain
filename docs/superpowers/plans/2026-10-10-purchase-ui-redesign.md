# 采购页 UI 按设计系统重做 —— 实施计划

> 设计来源：设计系统「供应链工作台」artifact `TCZbzG6ZdmT2BySP8mxyYJ`（版本 1791618789-ce95，`project/components/{PendingLines,PoEditor,PlaceDialog,PoDetail,AttentionPanel,AttentionBar,StatusPair,GateList,CommitButton,PoList}`）。
> 输入文档：`specs/2026-10-10-purchase-ui-brief.md`。★ 只换界面与交互；后端、接口、闸、额度判据不动。

## 与设计稿的偏离（以后端事实为准）

| 设计稿 | 实现 | 为什么 |
|---|---|---|
| 金额按分（2 位）求和，>2 位小数判非法 | 沿用 `exactTotal`，单价 4 位小数 | 领星单价 numeric(18,4)，现有数据 `12.5000` / `0.3333` |
| 列表「待处理」列写类型名 | 写「待处理 N」 | `GET /v1/purchase-orders` 只给 `open_attention` 计数 |
| 明细只由分配派生，无来源的行会消失 | 保留「全是无计划来源」的行 | 后端允许；`removeSource` 已有此语义 |
| 本地闸只有一个「全单一个仓」 | 本地闸名照后端（`line_wid` 逐行 + `single_wid`） | 前后端闸名同名（`poEditorModel.validate` 的既有约定） |
| 设计用 `sc-btn` / `sc-chip` / `sc-flash` 等类 | 用 `shell.css` 既有 `.btn` / `.chip--bad` / `.flash` | 同一设计系统；不引第二套按钮 |
| 文案用「 · 」串联（「计划 · 版本」「N 行 · M 件 · 金额」「code 403 · 接口」） | 改成分开的列 / 逗号 | 已裁定的文案纪律 F16（`web/src/e2e/copyDiscipline.test.tsx`：整屏不许有 ` · ` 元串） |
| ⛔ 标 `aria-hidden` | ⛔ 进可访问名称（「⛔ 下单」） | 不可逆的提示读屏也该听到 |
| ⛔ 提示 `role="status"` | 不挂 role，经 `aria-describedby` 关联按钮 | `role=status` 是 Toasts 的查询口（同 `AttentionBanner` 的约定） |
| 全局红条「需要人工处理 N 项」 | 标题仍数「N 张采购单」，类型后面数项 | 一张单常同时开两项（终审 I2 已裁定按单计数） |

## 步骤

1. `web/src/styles/purchase.css`：移植 bundle.css「采购单」一节（`.sc-commit` `.sp` `.gl` `.ab` `.pt` `.pl` `.pe` `.pd` `.ap` `.pdt`），类名映射到既有基础件。
2. 共用件：`CommitButton` · `StatusPair` · `GateList`（`web/src/pages/purchase/parts.tsx`）+ 单测。
3. `AttentionBanner` → 设计的 AttentionBar（按类型计数 + 去处理）。
4. `PurchaseHome`：PendingLines（搜索、整行点选、全选当前筛出）→ 处理队列 → 计数与核对 → PoList。
5. `PoEditor`：单头加「仓库（整张单一个仓）」写到每行；左栏逐店直接打件数（Enter / 失焦采用，绝对量）+「剩余全给」；右栏明细；GateList（标注本地 / 服务端）；底部 sticky 动作条，⛔ CommitButton 单独在右。模型加 `setAllocation` / `setDraftWid`。
6. `PlaceDialog`：dl 快照 + 表格 + 合计；结果区按设计（placed 显示单号 +「去详情」；rejected 时 ⛔ 禁用并写下一步；not_sent →「再次下单」；unknown 只给重新载入）。
7. `PoDetail` + `AttentionPanel`：头部单号 + 元数据 + StatusPair；处理区逐项；明细表；两个折叠。
8. 每步：既有行为测试照改（断言语义不丢）、新交互先写失败测试；`tsc` + `vitest` 全绿；`docs/10` §2.0 同步。
