# 阶段 A · 销售计划 —— 前端实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `web/` 建出阶段 A 销售计划的四屏前端（运营首页 · 计划编辑网格 · 批量添加 · 版本编辑），`mock` 与 `api` 两种数据源跑出同一屏。

**Architecture:** Vite + React 18 + TypeScript 单页应用，四条路由。数据层是**一个接口两个实现**（`mock.ts` / `http.ts`），由 `VITE_DATA_SOURCE` 选，fixture JSON 是两者唯一的数据来源 —— 判据⑥ 靠它证明。视觉层把 `docs/prototype/assets/shell.css` **纯 CSS 原样带走**，不重画；React 只重写结构，不重写样式。

**Tech Stack:** React 18.3.1 · react-dom 18.3.1 · react-router-dom 6.26.2 · Vite 5.4.8 · TypeScript 5.6.2 · vitest 2.1.1 + jsdom 25.0.1 + @testing-library/react 16.0.1 + @testing-library/user-event 14.5.2 + @testing-library/jest-dom 6.5.0。★ **不引任何 UI 组件库**（`00d` §4：墨迹系统已成形，再引一套等于两种视觉语言）。react-router-dom 是路由不是组件库，是唯一例外。

**Spec:** `docs/prototype/.stage-a-frontend-spec.md`（屏 · 网格 · 设计系统 · 两种人工输入 · 缺口）
**同时读：** `docs/00e-分阶段实施计划.md` §0 R 组 / §1 阶段 A · `docs/00-待裁定清单.md` S 组「全部已裁定」表 · `docs/10-前端交互界面.md` §0 §1 §3 · `docs/08-后端接口清单.md` §0 §1.1

---

## Global Constraints

每个 Task 的要求都隐含包含本节。

**文案**
- 界面文案只写**当场发生的事**：状态、结果、错误、下一步。★ **不写功能描述与解说**（`CLAUDE.md` 铁律）。
- 说明一律进 `.fold`（`<details>`），默认收起。★ 判据：**屏幕给数据，文档给论证。**
- 不写「加载中…请稍候」这类填充语；空屏用 `.empty` 给一个可点的动作。

**空 ≠ 0**
- 期望销量留空 = **未知**，发 `null`，**不发 0**；输入框 `placeholder` 为空串，不是 `"0"`。
- 无 FBA 的店铺显示 **「不适用」**（`.chip--dim`），**不是 0**。
- 阶段 A 够不着的状态**不渲染**（不是置灰、不是 0）。首页状态计数只渲染 **未提交 / 已提交 / 已撤销** 三态（`00e:45` · S-20）。
- 存量**不跨月求和**：`onhand` 是一条链（本月期初 = 上月期末），加起来等于把同一批货数三遍 ⇒ 合计列给 **「期末」**，不给和。
- 未知渲染 `—`（`.cell__unknown`）· 不适用渲染 **「不适用」**（`.chip--dim`）· 「不可求和」留给阶段 C 的跨货号/跨市场合计。三者三个字形，**不许合成一个**。
- ★ `basis.reason` 与 `basis.closing_reason` 是**两个字段**：前者恒为 `no_seller_attribution`（解释 `inbound` 为什么是 null），后者才解释 `closing`。混用会出现「这一格既未知又恒定」而只说得出一件。

**一个数要能回答「它是关于什么的」**
- 库存预估每格必须回显 `basis`（在仓 / `as_of`）与标注 **「未计本计划采购」**；★ 采购在途是**货号级**，单独一行只读并标 **「未分摊到店铺」**，一件都不并进任何店铺的库存。
- 系统预估用**铅笔灰**、人填用**蓝黑**、要处理用**朱批**、已落地用**石绿** —— 墨色代替说明文字（`10` §3.1）。

**错误**
- 后端错误形状 ★ `{"error": "...", "hint": "...", …点名字段**平铺在顶层**}`（team-lead 2026-09-22 裁定；`08` §0 写的 `{code,message,detail}` 已被它覆盖，两份只能有一份生效）→ 前端**逐项点名显示**（`10` §1 原则五），**不许显示「保存失败」**。
- 409 = 冲突与闸（你没写错，但现在不行）· 400 = 你写错了 —— 两者的界面处置不同，必须分支（`08:55`）。
- 提交结果必须逐条列 `skipped[]`（判据②）；409 `rev_in_flight` 要**点名旧版号**。

**接口**
- ★ **契约以 `docs/superpowers/plans/2026-09-22-stage-a-backend.md` 为准**（它是有测试的那份）。两边不一致时改前端，并把差异记进附录 A。
- 月份 ★ 对外一律 `"YYYY-MM"`（如 `"2026-10"`）；只有建计划的 `period_start` 是月初日期 `"YYYY-MM-01"`。
- 列表类响应 ★ 都带一层包裹（`{plans}` `{sellers}` `{items}` `{revs}`），不是裸数组；拆包在数据层做，页面不该知道这层。
- 店铺号（`seller_id` / `sid`）★ **全字段字符串**，18 位，JS 会精度丢失。类型里禁止 `number`。
- ★ **未声明查询参数一律 400** → 前端只发契约里声明过的参数，不加自造参数。
- 操作人走 `x-actor` 请求头，顶栏下拉选（Q-5 内网裸跑）；屏上标 **「留痕可伪造」**。
- `web/` 只打 `api/ui/`，★ 不许出现 PG / CH / 领星连接，不许打 `api/pub/`（`10` §0.3 的 L8~L10）。

**日志（`CLAUDE.md` 全局铁律）**
- 每次出站调用的日志要能回答三问：**打的谁**（method + path）· **多久**（耗时 ms）· **怎么失败的**（HTTP 状态码 + `code`，或 `e.cause?.code`）。
- ★ 分得清「超时」与「连不上」：`AbortSignal.timeout` 抛 `TimeoutError`；建连失败抛 `TypeError: fetch failed`，真凶在 `e.cause.code`。只取 `e.message` 的 catch 视为未完成。
- ★ 静默兜底是最坏的一种：任何回退分支必须留日志。

**测试**
- ★ **每个测试写完先把它弄失败一次** —— 每个 Task 的步骤里都有一步「跑它，确认按预期红」，并写明预期的失败文字。
- 检查任何过滤 / join / 分类，**必须同时统计被丢掉的那一侧**，并对未预期的排除硬失败。

**提交**
- 每个 Task 末尾提交一次。commit message 末尾附：

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
```

**不做**
- ✗ 不照抄 `docs/prototype/` 的流程（R-3），它只提供操作风格。
- ✗ 不做「模拟外部」抽屉与「⏩ 推进一天」：阶段 A **没有任何外部写接口**（`.stage-a-frontend-spec.md` ④），没有 ② 类人工输入可喂。该抽屉的样式缺失登记在附录 B，留到阶段 B。
- ✗ 不碰采购 / 排货 / 投送的任何接口。

---

## File Structure

```
web/
├── package.json                    Task 1
├── tsconfig.json  tsconfig.node.json
├── vite.config.ts                  Task 1（含 vitest 配置）
├── index.html                      Task 1
├── .env.example                    Task 1
├── vitest.setup.ts                 Task 1
└── src/
    ├── main.tsx                    Task 1  挂载 + 路由
    ├── routes.tsx                  Task 1  四条路由
    ├── styles/
    │   ├── shell.css               Task 1  ★ 从 docs/prototype/assets/ 原样复制，不改一个字符
    │   └── app.css                 Task 1  ★ 只补 shell.css 没有的：三行格布局、计数条
    ├── shell/
    │   ├── AppShell.tsx            Task 1  顶栏 + 面包屑 + toast 挂载点
    │   ├── actorStore.ts           Task 1  当前 actor（http.ts 读它发 x-actor）
    │   ├── actors.ts               Task 1  ★ actor 名单（无接口，见附录 B · 缺口 11）
    │   ├── toastStore.ts           Task 1
    │   └── Toasts.tsx              Task 1
    ├── components/
    │   ├── Fold.tsx                Task 1  <details class="fold">
    │   ├── ErrorDetail.tsx         Task 2  ★ 逐项点名，不说「失败」
    │   ├── Qty.tsx                 Task 3  .qty / .qty--zero / 未知 / 不适用 / 不可求和
    │   └── Modal.tsx               Task 6  .modal-backdrop 单套（shell.css 里 dialog 与 div 两套并存，只留这一套）
    ├── api/
    │   ├── types.ts                Task 2  ★ 全部接口类型，后续 Task 只引用不新增
    │   ├── client.ts               Task 2  SupplyChainApi 接口 + ApiError
    │   ├── index.ts                Task 2  按 VITE_DATA_SOURCE 选实现
    │   ├── mock.ts                 Task 2  内存实现，读 fixtures
    │   ├── http.ts                 Task 2  真实现，含三问日志
    │   └── fixtures/
    │       ├── grid-1.json         Task 2  ★ tests/fixtures/grid_response.json 的副本（后端 Task 15 产出），字节比对盯着
    │       ├── plans.json          Task 2  前端自管
    │       ├── catalog.json        Task 2  前端自管
    │       ├── revs-1.json         Task 2  前端自管
    │       └── sellers.json        Task 2  前端自管
    └── pages/
        ├── OpsHome.tsx             Task 3
        ├── planGridModel.ts        Task 4  ★ 纯函数：三数组 → 行模型（可单测，不碰 DOM）
        ├── PlanGrid.tsx            Task 4（渲染 + 编辑）/ Task 7（提交按钮）
        ├── PlanAdd.tsx             Task 5
        └── PlanRevs.tsx            Task 6
```

**为什么这么切**：`planGridModel.ts` 与 `PlanGrid.tsx` 分开，是因为「合计怎么求和 / 谁不可求和 / 未知怎么传染」全是纯计算，塞进组件里就只能靠渲染断言去测，而渲染断言**比目标宽**（整页取样会被旁边的同形文字救绿）。`api/types.ts` 一次定完，是因为 Task 之间只看到自己那一份，类型分两处长必然分叉。

---

## Task 1: 设计计划 · 脚手架 · 墨迹 token · 全局外壳

**Files:**
- Create: `web/package.json` `web/tsconfig.json` `web/tsconfig.node.json` `web/vite.config.ts` `web/index.html` `web/.env.example` `web/vitest.setup.ts`
- Create: `web/src/main.tsx` `web/src/routes.tsx`
- Create: `web/src/styles/shell.css`（复制）`web/src/styles/app.css`
- Create: `web/src/shell/AppShell.tsx` `web/src/shell/actorStore.ts` `web/src/shell/actors.ts` `web/src/shell/toastStore.ts` `web/src/shell/Toasts.tsx`
- Create: `web/src/components/Fold.tsx`
- Test: `web/src/styles/tokens.test.ts` `web/src/shell/AppShell.test.tsx`

**Interfaces:**
- Consumes: 无（第一个 Task）
- Produces:
  - `getActor(): string` · `setActor(id: string): void` · `subscribeActor(fn: () => void): () => void` · `ACTORS: readonly Actor[]`（`{ actor_id: string; name: string }`）—— Task 2 的 `http.ts` 用 `getActor()` 发 `x-actor`
  - `pushToast(t: { kind: 'ok' | 'fail' | 'warn'; text: string }): void` —— Task 3~7 用
  - `<AppShell crumb={string}>{children}</AppShell>`
  - `<Fold summary={string}>{children}</Fold>`
  - 路由：`/` → `OpsHome` · `/plans/:planId` → `PlanGrid` · `/plans/:planId/add` → `PlanAdd` · `/plans/:planId/revs` → `PlanRevs`

---

### ★ 设计计划（frontend-design skill，第一遍：token 系统）

> 本节是交付物的一部分，执行时**照它建 `app.css` 与组件结构**，不要自由发挥。
> 前提：`docs/10` §3 与 `shell.css` 已把视觉方向**钉死**（frontend-design：「Where the brief pins down a visual direction, follow it exactly」）。所以本节只做两件事：① 把已钉死的部分**写明白**；② 在它留白的轴上做**这一份**的选择。

**主题是什么**：一张给运营填的**工作表**。不是 dashboard，不是 SaaS 产品页。观众是每天要在几十个格子里填数、并且要一眼看出「哪个数是我填的、哪个是机器猜的」的运营。它的主要任务是**填两种量并看见影响**。

**Color（6 个命名值，全部来自 `shell.css`，不新增）**

| 名 | 值 | 角色 |
|---|---|---|
| `--paper` | `#F0F1ED` | 底。★ **冷纸，偏绿灰** —— `shell.css` 原注释写死「不是暖奶油」 |
| `--paper-lo` | `#E7E9E3` | 压下去一层：表头、只读区、合计列 |
| `--ink` | `#16293D` | 蓝黑墨 —— **人写的数**（期望销量、计划采购量） |
| `--pencil` | `#8B939B` | 铅笔灰 —— **机器估的数**、元信息 |
| `--vermilion` | `#C4342A` | 朱批 —— 断货、缺口、被拒、占用冲突 |
| `--jade` | `#2E6B54` | 石绿 —— 已落地的事实（当前使用的 rev、已确认量） |

★ 注意 `#F0F1ED` 与 frontend-design 点名的 AI tell `#F4F1EA`（暖奶油）只差几个色阶，**但方向相反**（绿灰 vs 红黄）。抄成暖色就是那个 tell，所以复制 CSS 时不许「顺手微调」。

**Type**

一个字族：`--face`（PingFang SC / Hiragino Sans GB / Microsoft YaHei / Noto Sans CJK SC …）。
★ **层级靠字号 + 墨色，不靠字重** —— CJK 系统字重不可靠，很多只有 regular/bold 两档（`shell.css` 原注释）。

| 号 | 值 | 只给谁 |
|---|---|---|
| `--t-xl` | 26px | ★ **整屏只有一处**：首页三个状态计数的数字（`.kpi__n`） |
| `--t-lg` | 17px | 格子里的结论数（`.cell__close` 库存预估）与 `.qty` |
| `--t-md` | 14px | 正文、输入框 |
| `--t-sm` | 12.5px | 表格、参数条、按钮 |
| `--t-xs` | 11px | 元信息、msku 码、chip |

全表数字 `font-variant-numeric: tabular-nums`（body 已设）—— ★ 数字对齐是功能不是造型，所以**不换等宽字体**（那是把工作表打扮成终端）。

frontend-design 点名要避开的三种排版 tell，本设计的处置：
- 不在标题里单独强调一个词（墨色已经承担了区分，再强调就是两套语言）；
- 不用全大写标签（CJK 无大小写，天然规避；拉丁文 msku 码也保持原大小写，因为它是**要被复制粘贴的标识符**）；
- 不在内容上方加多余的类别标签（`.sheet__head` 一行里就是「店铺名 · 货号 · 名称 · 统计」，没有 eyebrow）。

**Layout**

版面三段式（`10` §3.2）：**一行标题 + 一行元数据/参数 + 剩下全给数据**。

```
运营首页 /
┌────────────────────────────────────────────────────────────┐
│ service_supplychain   销售计划            [运营▾] 留痕可伪造 │  .shell__bar
├────────────────────────────────────────────────────────────┤
│ 我的计划                              [ 新建销售计划 ]      │  .head
│                                                             │
│   12          3           1                                 │  ← --t-xl，整屏唯一
│   未提交      已提交      已撤销                            │  .kpi__n / .kpi__d
│                                                             │
│ ┌ 未提交 ────────────────┬ 提交后又改过 ─────────────────┐ │  两栏，A-2
│ │ 2026 Q4 销售计划       │ 2026 Q3 补货计划  rev 2       │ │
│ └────────────────────────┴───────────────────────────────┘ │
│                                                             │
│ 周期[2026-10▾] 计划[  ] 版本[  ] 状态[未提交▾]  排序[周期▾] │  .bar
│ ┌──────┬──────────────┬────┬────────┬──────┐               │
│ │ 周期 │ 计划名        │ 版本│ 状态   │ 详情 │               │  table.table
│ └──────┴──────────────┴────┴────────┴──────┘               │
└────────────────────────────────────────────────────────────┘

计划编辑网格 /plans/:id
┌────────────────────────────────────────────────────────────┐
│ 2026 Q4 销售计划            [添加货品][重置][提交] rev 3 ▾  │  .head + .head__act
│ 起始月 2026-10 · 跨 3 月 · 负责人 zhang                     │  .head__meta
├──────────────┬────────┬────────┬────────┬────────┐         │
│ 店铺·货号     │2026-10 │2026-11 │2026-12 │ 合计   │         │  th.gh--row / gh--sum
├──────────────┼────────┼────────┼────────┼────────┤         │
│ ▸ A4Pet-US   │ 预估180│ 预估200│ 预估210│  590   │ ← 铅笔灰 │
│   DCC1800264 │ 库存420│ 库存220│ 库存 10│期末  10│ ← 灰+basis│
│              │ [ 180 ]│ [ 200 ]│ [    ] │  380   │ ← 蓝黑   │
├──────────────┼────────┼────────┼────────┼────────┤         │
│ 计划采购量    │ [ 500 ]│ [    ] │ [    ] │  500   │         │  第二块 .sheet
└──────────────┴────────┴────────┴────────┴────────┘         │
```

对齐：**文字左对齐，数字右对齐**。行头 `th.gh--row` 左对齐、最小宽 190px；合计列 `td.gsum` 背景压深一档并右对齐。整块横向滚动由 `.sheet__scroll` 承担 —— ★ 页面本身**不许**横向滚动。

**Principles（这一份的五条）**

1. **墨色即注释。** 屏幕上永远不写「这是系统预估」，用铅笔灰说。一个数用哪种墨，就说明了它是哪种数。
2. **一格三行，顺序恒定**：预估（灰）→ 库存（灰 + basis）→ 输入（蓝黑）。位置固定，眼睛在 30 个格子之间移动时不用重新定位。
3. **唯一的大字留给结论。** 26px 整屏只出现在首页三个计数上；网格里最大的是库存预估（17px），因为那是「填完数当场看见的影响」（`14` §0）。其余一律 12.5px 以下。
4. **输入框是下划线不是盒子**（`.cell input.g` 只有 `border-bottom`）。一屏 100 个盒子会把纸变成表单；下划线让「可以写字的地方」既显眼又不抢版面。
5. **空、不适用、不可求和，三个字形。** `—` / `不适用` / `不可求和` 各自独立，**永不退化成 0** —— 这三种情况在本项目里已经咬过人（`CLAUDE.md` 判据表第一行）。阶段 A 用到前两个；第三个留给阶段 C 的跨货号合计（登记在附录 A.3）。

**★ 对照 brief 复核（frontend-design 第二遍）—— 改了两处**

| 初稿 | 问题 | 改成 |
|---|---|---|
| 首页三个计数做成三张带边框、圆角、阴影的卡片 | 这正是 frontend-design 列的第 4 类 tell「SaaS-card kit：内容切成一模一样的圆角卡 + 同一个灰阴影」。而且 `shell.css` 的 `--r: 3px` 原注释写着「**纸张裁切，不是卡片**」 | 去掉卡片：三个数横排在 `.sec` 里，只有数字和标签，没有边框没有阴影 |
| 计划状态用彩色徽章（已提交=绿、已撤销=红） | `10` §3.2 ③ 写死「**状态用左边条不用徽章** —— 徽章抢注意力又占位」 | 列表行用 `.chip--dim` 素面，网格格子用 `.cell[data-state]` 左边条 |

其余部分照 brief 原样执行（配色、字族、折叠、墨色语义都由 `10` §3 与 `shell.css` 钉死，不是自由轴）。

---

- [ ] **Step 1: 建目录与 npm 工程**

在 `/home/fido/work/2026/jxd_service_group/service_supplychain` 下执行：

```bash
mkdir -p web/src/{styles,shell,components,api/fixtures,pages}
cat > web/package.json <<'EOF'
{
  "name": "service-supplychain-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --port 5173",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "react-router-dom": "6.26.2"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "6.5.0",
    "@testing-library/react": "16.0.1",
    "@testing-library/user-event": "14.5.2",
    "@types/react": "18.3.10",
    "@types/react-dom": "18.3.0",
    "@vitejs/plugin-react": "4.3.1",
    "jsdom": "25.0.1",
    "typescript": "5.6.2",
    "vite": "5.4.8",
    "vitest": "2.1.1"
  }
}
EOF
cd web && npm install
```

- [ ] **Step 2: 配置文件**

```bash
cat > web/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "resolveJsonModule": true,
    "noEmit": true,
    "skipLibCheck": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "vitest.setup.ts", "vite.config.ts"]
}
EOF
cat > web/vite.config.ts <<'EOF'
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    css: true,
  },
});
EOF
cat > web/vitest.setup.ts <<'EOF'
import '@testing-library/jest-dom/vitest';
EOF
cat > web/.env.example <<'EOF'
# mock = 内存假数据，前端独自跑通交互；api = 打 api/ui/ 的真接口
VITE_DATA_SOURCE=mock
VITE_API_BASE=/v1
VITE_API_TIMEOUT_MS=8000
EOF
cat > web/index.html <<'EOF'
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>销售计划 · service_supplychain</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
EOF
```

`css: true` 是必须的：`tokens.test.ts` 要读真实 CSS 变量，vitest 默认不处理 CSS 导入，那样测试会**假绿**。

- [ ] **Step 3: 原样复制 shell.css，并写守住它的失败测试**

```bash
cp docs/prototype/assets/shell.css web/src/styles/shell.css
```

```ts
// web/src/styles/tokens.test.ts
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const SRC = 'docs/prototype/assets/shell.css';
const DST = 'src/styles/shell.css';

// ★ 墨迹系统的六个承重 token —— 值写死在这里，因为 #F0F1ED（冷纸）与 #F4F1EA（暖奶油）
//   只差几个色阶而方向相反，"顺手微调" 不会有任何东西报错。
const TOKENS: Record<string, string> = {
  '--paper': '#F0F1ED',
  '--paper-lo': '#E7E9E3',
  '--ink': '#16293D',
  '--pencil': '#8B939B',
  '--vermilion': '#C4342A',
  '--jade': '#2E6B54',
};

describe('shell.css 原样带走', () => {
  it('与原型的那一份逐字节相同', () => {
    const src = readFileSync(new URL(`../../../${SRC}`, import.meta.url));
    const dst = readFileSync(new URL(`../../${DST}`, import.meta.url));
    const h = (b: Buffer) => createHash('sha256').update(b).digest('hex');
    expect(h(dst)).toBe(h(src));
  });

  it.each(Object.entries(TOKENS))('%s = %s', (name, value) => {
    const css = readFileSync(new URL(`../../${DST}`, import.meta.url), 'utf8');
    const m = new RegExp(`${name}:\\s*([#A-Za-z0-9(),. %-]+);`).exec(css);
    expect(m?.[1]?.trim()).toBe(value);
  });
});
```

- [ ] **Step 4: 先把它弄失败一次**

```bash
cd web && printf '\n/* probe */\n' >> src/styles/shell.css && npx vitest run src/styles/tokens.test.ts
```
Expected: FAIL —— `与原样带走 > 与原型的那一份逐字节相同`，两个 sha256 不等。

再把 `--paper` 改成 `#F4F1EA` 重跑：Expected: FAIL —— `--paper = #F0F1ED` 那条 `expected '#F4F1EA' to be '#F0F1ED'`。

```bash
cd web && git checkout -- src/styles/shell.css 2>/dev/null || cp ../docs/prototype/assets/shell.css src/styles/shell.css
npx vitest run src/styles/tokens.test.ts
```
Expected: PASS（7 个用例）。

- [ ] **Step 5: 写 app.css —— 只补 shell.css 没有的**

```css
/* web/src/styles/app.css
   ★ 只允许放 shell.css 里没有的东西。凡 shell.css 已有的类，一律不在这里重定义
     —— .dropped 曾在三个页面各定义一遍，改一处忘一处，屏幕上不报错。 */

/* 一格三行：预估（灰）→ 库存（灰 + basis）→ 输入（蓝黑），顺序恒定 */
.cell__stack { display: grid; gap: calc(var(--u) * 1.25); }
.cell__lead { font-size: var(--t-xs); color: var(--pencil); text-align: right; }

/* 库存预估的依据：在仓 / 采购在途 / as_of —— 数据，不是解说 */
.basis { display: flex; justify-content: flex-end; gap: calc(var(--u) * 1.5); font-size: 10px; color: var(--pencil); }

/* 首页三个计数横排。★ 不做卡片：--r 是纸张裁切，不是卡片 */
.counts { display: flex; gap: calc(var(--u) * 10); align-items: baseline; }
.counts .kpi { flex-direction: column; align-items: flex-start; gap: calc(var(--u) * 0.5); }

/* 两栏：未提交 / 提交后又改过 */
.twocol { display: grid; grid-template-columns: 1fr 1fr; gap: calc(var(--u) * 4); }
@media (max-width: 860px) { .twocol { grid-template-columns: 1fr; } }

/* 行展开箭头：不是装饰，它说明这一行底下还有 msku */
.gr__toggle { background: none; border: 0; padding: 0 calc(var(--u)) 0 0; color: var(--pencil); cursor: pointer; font: inherit; }
```

- [ ] **Step 6: actor 名单与 store**

```ts
// web/src/shell/actors.ts
// ★ 阶段 A 没有 actor 接口（08 §1.4 只有 skus/sellers/warehouses/categories）。
//   两种数据源都读这一份常量，登记在计划附录 B。
export interface Actor { actor_id: string; name: string }

export const ACTORS: readonly Actor[] = [
  { actor_id: 'ops.zhang', name: '张 · 运营' },
  { actor_id: 'ops.li', name: '李 · 运营' },
  { actor_id: 'admin.wu', name: '吴 · 管理员' },
];
```

```ts
// web/src/shell/actorStore.ts
import { ACTORS } from './actors';

let current: string = ACTORS[0]!.actor_id;
const listeners = new Set<() => void>();

export function getActor(): string { return current; }

export function setActor(id: string): void {
  if (!ACTORS.some((a) => a.actor_id === id)) {
    // ★ 静默兜底是最坏的一种：认不出的 actor 要报错，不是悄悄落回默认值
    throw new Error(`unknown actor: ${id}`);
  }
  current = id;
  listeners.forEach((fn) => fn());
}

export function subscribeActor(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
```

- [ ] **Step 7: toast store 与组件**

```ts
// web/src/shell/toastStore.ts
export interface Toast { id: number; kind: 'ok' | 'fail' | 'warn'; text: string }

let seq = 0;
let items: Toast[] = [];
const listeners = new Set<() => void>();

export function getToasts(): Toast[] { return items; }
export function pushToast(t: Omit<Toast, 'id'>): void {
  items = [...items, { ...t, id: ++seq }];
  listeners.forEach((fn) => fn());
}
export function dismissToast(id: number): void {
  items = items.filter((t) => t.id !== id);
  listeners.forEach((fn) => fn());
}
export function subscribeToasts(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
```

```tsx
// web/src/shell/Toasts.tsx
import { useSyncExternalStore } from 'react';
import { dismissToast, getToasts, subscribeToasts } from './toastStore';

export function Toasts() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, getToasts);
  if (items.length === 0) return null;
  return (
    <div className="toasts" role="status">
      {items.map((t) => (
        <button key={t.id} type="button" className={`toast toast--${t.kind}`} onClick={() => dismissToast(t.id)}>
          {t.text}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 8: 写 AppShell 的失败测试**

```tsx
// web/src/shell/AppShell.test.tsx
import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AppShell } from './AppShell';
import { getActor, setActor } from './actorStore';
import { ACTORS } from './actors';

beforeEach(() => setActor(ACTORS[0]!.actor_id));

describe('AppShell', () => {
  it('顶栏有 actor 下拉，选了就改当前操作人', async () => {
    render(<MemoryRouter><AppShell crumb="我的计划"><p>x</p></AppShell></MemoryRouter>);
    const select = screen.getByLabelText('操作人');
    await userEvent.selectOptions(select, 'ops.li');
    expect(getActor()).toBe('ops.li');
  });

  it('★ 屏上标「留痕可伪造」—— 无认证这件事不许只写在文档里', () => {
    render(<MemoryRouter><AppShell crumb="我的计划"><p>x</p></AppShell></MemoryRouter>);
    expect(screen.getByText('留痕可伪造')).toBeInTheDocument();
  });

  it('★ 不写功能描述：顶栏与面包屑之外没有解说文字', () => {
    const { container } = render(<MemoryRouter><AppShell crumb="我的计划"><p>x</p></AppShell></MemoryRouter>);
    const bar = container.querySelector('.shell__bar')!;
    // 每段可见文字都必须 ≤ 8 字（品牌名 / 面包屑 / 状态标记），一句话解说必然超
    const longs = Array.from(bar.querySelectorAll('*'))
      .filter((el) => el.children.length === 0)
      .map((el) => el.textContent?.trim() ?? '')
      .filter((s) => s.length > 8);
    expect(longs).toEqual([]);
  });
});
```

- [ ] **Step 9: 跑它，确认三条全红**

```bash
cd web && npx vitest run src/shell/AppShell.test.tsx
```
Expected: FAIL —— `Failed to resolve import "./AppShell"`（模块还不存在）。

- [ ] **Step 10: 写 AppShell 与 Fold**

```tsx
// web/src/shell/AppShell.tsx
import type { ReactNode } from 'react';
import { useSyncExternalStore } from 'react';
import { Link } from 'react-router-dom';
import { ACTORS } from './actors';
import { getActor, setActor, subscribeActor } from './actorStore';
import { Toasts } from './Toasts';

export function AppShell({ crumb, children }: { crumb: string; children: ReactNode }) {
  const actor = useSyncExternalStore(subscribeActor, getActor, getActor);
  return (
    <>
      <div className="shell">
        <div className="shell__bar">
          <Link className="shell__brand" to="/">销售计划</Link>
          <span className="sep" />
          <span className="shell__crumb">{crumb}</span>
          <span className="shell__spacer" />
          <label className="lbl" htmlFor="actor">操作人</label>
          <select
            id="actor"
            className="inp inp--tiny"
            style={{ width: 'auto' }}
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          >
            {ACTORS.map((a) => <option key={a.actor_id} value={a.actor_id}>{a.name}</option>)}
          </select>
          <span className="chip chip--dim">留痕可伪造</span>
        </div>
      </div>
      <div className="wrap mt">{children}</div>
      <Toasts />
    </>
  );
}
```

```tsx
// web/src/components/Fold.tsx
import type { ReactNode } from 'react';

// ★ 说明一律进折叠区，默认收起（CLAUDE.md：屏幕给数据，文档给论证）
export function Fold({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="fold">
      <summary>{summary}</summary>
      <div className="fold__body">{children}</div>
    </details>
  );
}
```

- [ ] **Step 11: 路由与入口**

```tsx
// web/src/routes.tsx
import { createBrowserRouter } from 'react-router-dom';
import { OpsHome } from './pages/OpsHome';
import { PlanGrid } from './pages/PlanGrid';
import { PlanAdd } from './pages/PlanAdd';
import { PlanRevs } from './pages/PlanRevs';

export const router = createBrowserRouter([
  { path: '/', element: <OpsHome /> },
  { path: '/plans/:planId', element: <PlanGrid /> },
  { path: '/plans/:planId/add', element: <PlanAdd /> },
  { path: '/plans/:planId/revs', element: <PlanRevs /> },
]);
```

```tsx
// web/src/main.tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import './styles/shell.css';
import './styles/app.css';
import { router } from './routes';

createRoot(document.getElementById('root')!).render(
  <StrictMode><RouterProvider router={router} /></StrictMode>,
);
```

四个页面组件在 Task 3/4/5/6 才写。为让 Task 1 能独立编译与跑测，先建四个**最小占位**（★ 它们在各自 Task 里被整体替换，不是 TODO）：

```tsx
// web/src/pages/OpsHome.tsx   —— Task 3 整体替换
import { AppShell } from '../shell/AppShell';
export function OpsHome() { return <AppShell crumb="我的计划"><div className="empty"><p className="empty__title">还没有计划</p></div></AppShell>; }
```

`PlanGrid.tsx` / `PlanAdd.tsx` / `PlanRevs.tsx` 同形，`crumb` 分别为 `计划编辑`、`添加货品`、`版本`，导出名 `PlanGrid` / `PlanAdd` / `PlanRevs`。

- [ ] **Step 12: 跑测试，确认变绿**

```bash
cd web && npx vitest run && npx tsc -b --noEmit false --emitDeclarationOnly false 2>&1 | tail -5
```
Expected: `Test Files 2 passed`，`Tests 10 passed`；tsc 无错误输出。

- [ ] **Step 13: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
cat >> .gitignore <<'EOF'
web/node_modules/
web/dist/
web/.env
EOF
git add web .gitignore
git commit -m "$(cat <<'EOF'
feat(web): 阶段 A 前端脚手架 —— 墨迹 token 原样带走 + 全局外壳

shell.css 逐字节复制并由 sha256 守住；actor 下拉旁标「留痕可伪造」。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## Task 2: 数据层 —— 一个接口 · 两个实现 · 一份 fixture

> ★ **本 Task 的契约以后端计划 `docs/superpowers/plans/2026-09-22-stage-a-backend.md` 为准**
> （它是有测试的那份）。下面每个类型都能指到后端计划的某一行；两边不一致时改这里，不改那边。
> ★ 四处与 `08` §0 / 我先前草稿**不同**，最容易写错：
> ① 错误形状是 `{error, hint, …点名字段平铺在顶层}`，**不是** `{code, message, detail}`；
> ② 月份对外一律 `"YYYY-MM"`（`"2026-10"`），**库里**才是月初日期；
> ③ 列表类响应都**带一层包裹**（`{plans:[…]}` / `{sellers:[…]}` / `{items:[…]}`），不是裸数组；
> ④ ★ **库存的身份是 `(sku, sid)`**（`02` §3.1a 可售库存[店铺, 货号]），不是 msku、不是货号；
>   `inbound` 恒 `null`（在途是货号级，不分摊），在途总量单独走 `sku_pipeline[]`；
> ⑤ ★★ **`basis.reason` 与 `basis.closing_reason` 是两个字段，不许混用** ——
>   `reason` **恒为** `"no_seller_attribution"`，它解释的是 `inbound` 为什么是 null；
>   `closing` 为什么是 null 记在 `closing_reason`。两件事挤进一个字段，就会出现
>   「这一格既未知又恒定」而只说得出一件。

**Files:**
- Create: `web/src/api/types.ts` `web/src/api/client.ts` `web/src/api/index.ts` `web/src/api/mock.ts` `web/src/api/http.ts`
- Create: `web/src/api/fixtures/{plans,catalog,revs-1,sellers}.json`
- Copy: `web/src/api/fixtures/grid-1.json` ← `tests/fixtures/grid_response.json`（后端 Task 15 产出）
- Create: `web/src/components/ErrorDetail.tsx`
- Test: `web/src/api/mock.test.ts` `web/src/api/http.test.ts` `web/src/api/fixtures.test.ts` `web/src/components/ErrorDetail.test.tsx`

**★ 跨计划的执行顺序**：本 Task 排在**后端 Task 15 之后**。那份 `tests/fixtures/grid_response.json`
还没生成时，`fixtures.test.ts` 的字节比对就是红的 —— ★ **这条红不许改成 skip**：
默认 skip 的测试会静默死掉，而「后端 fixture 还没生成」正是它要报的事。

**Interfaces:**
- Consumes: `getActor()`（Task 1）
- Produces（后续 Task **只引用、不新增**类型）：
  - `api: SupplyChainApi`（`web/src/api/index.ts` 的单例）
  - `class ApiError extends Error { status: number; error: string; hint: string; fields: Record<string, unknown> }`
  - 类型：`Period` `PlanSummary` `PlanList` `DashboardPlans` `UnsubmittedBoard` `GridResponse` `DemandCell` `PurchaseCell` `InventoryCell` `InventoryBasis` `SkuPipelineRow` `CatalogResult` `CatalogItem` `CatalogMsku` `ClaimHolder` `SubmitResult` `SkippedCell` `SkipReason` `RevList` `Rev` `PlanDiff` `Seller`
  - `<ErrorDetail err={ApiError} />`

- [ ] **Step 1: 写全部类型（一次定完）**

```ts
// web/src/api/types.ts
// ★ 逐条对应后端计划的返回体。字段名、类型、null 语义都不许自己改 ——
//   mock 与真 API 对不上，判据⑥ 直接挂，而界面是照这份形状先写完的。

/** ★ 对外一律 "YYYY-MM"（如 "2026-10"）。库里是月初 date —— 前端永远不碰那个形态 */
export type Period = string;
/** ★ 店铺号 18 位，JS number 会精度丢失 ⇒ 全字段字符串 */
export type SellerId = string;
export type Sid = string;
export type PlanId = number;

/** 04 §2 的 S1 值域。★ 没有「未提交」—— 从未提交的计划 state 为 null */
export type LineState = '已提交' | '已确认' | '已下单' | '准备排货' | '已排货' | '已完结' | '已撤销';

export interface PlanSummary {
  plan_id: PlanId;
  title: string;
  /** ★ 这一个是月初日期 "YYYY-MM-01"（建计划时的入参口径） */
  period_start: string;
  months: number;
  owner_actor: string;
  /** null = 未归档 */
  archived_at: string | null;
  /** ★ 木桶派生；null = 从未提交过（没有 rev 就没有记录） */
  state: LineState | null;
  state_rev: number | null;
}

export interface PlanList {
  plans: PlanSummary[];
  /** ★ 被挡掉的那一侧：不说「挡掉了几张」，人只会觉得计划凭空少了 */
  excluded: { archived: number };
}

/** 看板计数的键是**中文状态名**（后端 `COUNTED`），不是英文 */
export type CountKey = '进行中' | '已提交' | '已提交未确认' | '已下单' | '准备排货' | '已排货';

export interface DashboardPlans {
  counts: Record<CountKey, number>;
  /** ★ 后端返回全集（诚实），由前端按阶段不渲染（S-20） */
  scope_note: { unreachable_in_stage_a: CountKey[]; never_submitted_excluded: number };
}

export interface UnsubmittedBoard {
  never_submitted: { plan_id: PlanId; title: string }[];
  /** 按 content_digest 判（A-2）；`since_rev` = 与哪一版比出来的 */
  changed_since_submit: { plan_id: PlanId; title: string; since_rev: number }[];
}

export interface Seller {
  seller_id: SellerId;
  name: string;
  market: string;
  /** ★ false ⇒ 该店的 FBA 库存是「不适用」，不是 0（02 §3.1a） */
  has_fba: boolean;
  platform: string;
}

/** S-15：生效值的来源。★ 冻结后靠它分清人填与采用预估 */
export type DemandBasis = 'human' | 'system' | 'unknown';

export interface DemandCell {
  seller_sku: string;
  sid: Sid;
  sku: string;
  period: Period;
  /** 系统预估；★ null = 连预估都没有 */
  system_units: number | null;
  /** ★ 14 §5：外推标记随结果一起回来，界面不许回头读原始格子 */
  system_extrapolated: boolean;
  /** ★ null = 未知（M-8），不是 0 */
  expected_units: number | null;
  /** 生效值 = 人填优先，否则系统预估，两者都没有则 null */
  effective_units: number | null;
  basis: DemandBasis;
}

export interface PurchaseCell {
  /** ★ 货号级，不带店铺（P1/P2） */
  sku: string;
  period: Period;
  planned_units: number | null;
}

/** ★ `inbound` 为什么是 null。阶段 A **恒定**是这一个值 —— 它不说明 closing 的任何事 */
export type InboundReason = 'no_seller_attribution';

/** ★ `closing` 为什么是 null。两种成因处置不同，**不许合成一个裸 null** */
export type ClosingReason = null | 'not_applicable' | 'unknown_demand';

export interface InventoryBasis {
  source: 'ch';
  as_of: string;
  /** ★ 恒 false 且必须显式返回 —— 屏上标「未计本计划采购」 */
  includes_plan_purchase: false;
  /** 这一格用掉的期望销量（= 该店该货号各 msku 生效值之和）。★ 与前端自己算的那份互为证人 */
  demand: number | null;
  /** ★ 恒 'no_seller_attribution' —— 只解释 inbound，不解释 closing */
  reason: InboundReason;
  /** ★ closing 的成因，与 reason 分开的那一个 */
  closing_reason: ClosingReason;
  /** 该货号该月的在途总量。★ 放在这里是因为它**不属于这一格**（没有店铺归属），只展示不分摊 */
  sku_level_in_transit: number | null;
  sources: { units: number; kind: string; ref: string }[];
}

/** ★ 可售库存的身份是 **[店铺, 货号]**（`02` §3.1a）—— 不是 msku，也不带任何分摊 */
export interface InventoryCell {
  sku: string;
  sid: Sid;
  period: Period;
  /** 该格期初：首月是在仓事实，其后是**上月期末**（★ 跨月是一条链，不是同一个快照） */
  onhand: number | null;
  /** ★ 阶段 A 恒 null —— 在途是货号级，按单店给全额也是分摊假设（C4 禁） */
  inbound: null;
  /** ★ = onhand − basis.demand。null 时看 basis.closing_reason */
  closing: number | null;
  basis: InventoryBasis;
}

/** 货号级在途总量。★ 只展示，不分摊到任何店铺 */
export interface SkuPipelineRow {
  sku: string;
  period: Period;
  units: number | null;
  sources: { units: number; kind: string; ref: string }[];
  no_seller_attribution: true;
}

export interface GridResponse {
  plan_id: PlanId;
  /** ["2026-10","2026-11","2026-12"] */
  periods: Period[];
  demand: DemandCell[];
  purchase: PurchaseCell[];
  /** ★ 店铺 × 货号 × 月 */
  inventory: InventoryCell[];
  /** ★ 货号 × 月；画成一行只读，**不并进任何一个店铺的库存** */
  sku_pipeline: SkuPipelineRow[];
}

export interface ClaimHolder { plan_id: PlanId; title: string; actor: string }

export interface CatalogMsku {
  seller_sku: string;
  sid: Sid;
  seller_name: string;
  /** ★ false = 已被别的计划占用；★ 行**留在表里标出来，不过滤**（P11） */
  selectable: boolean;
  claimed_by: ClaimHolder | null;
}

/** 06 §1.2：店铺没挂渠道 → 建不出格子，必须点名。★ 阶段 A 后端恒返回空数组 */
export interface UnbuildableSeller { sid: Sid; reason: 'no_channel_code' }

export interface CatalogItem {
  sku: string;
  name: string;
  mskus: CatalogMsku[];
  unbuildable_sellers: UnbuildableSeller[];
  /** 占用方唯一时才有一个答案；两张计划各占一部分 → null，名单在 claimed_by_plans */
  claimed_by: { plan_id: PlanId; title: string } | null;
  claimed_by_plans: { plan_id: PlanId; title: string }[];
}

export interface CatalogResult {
  /** ★ 与「查不到」分得开：没给条件是 need_query，不是空结果 */
  need_query: boolean;
  truncated: boolean;
  limit: number;
  items: CatalogItem[];
  /** 头部接口形状块没列它（实现里有）⇒ 可选。★ 判「查不到」一律用 `!need_query && items.length === 0`，
   *  不依赖这个字段 —— 依赖一个可能缺的字段去判空，缺了就会显示成「还没搜」 */
  matched?: number;
}

export interface ClaimTarget { seller_sku: string; sid: Sid }
export interface ClaimResult { claimed: { seller_sku: string; sid: Sid; sku: string } }
// ★ 后端把丢掉的格逐条回给界面（含人填过的数），不是一个数；货号级采购格不删、只点名「搁浅」
export interface DroppedCell { period: string; expected_units: number | null }
export interface StrandedPurchaseCell { sku: string; period: string }
export interface ReleaseResult {
  released: { seller_sku: string; sid: Sid };
  dropped_cells: DroppedCell[];
  stranded_purchase_cells: StrandedPurchaseCell[];
}

/** S-14：值域已裁定，只有这两个 */
export type SkipReason = 'zero_purchase' | 'no_claimed_msku';
export interface SkippedCell { sku: string; period: Period; reason: SkipReason }

export interface SubmitResult {
  rev: number;
  /** ★ 铸出几条。字段名是 `lines`（team-lead 裁定；后端若残留 `minted` 以 `lines` 为准） */
  lines: number;
  skipped: SkippedCell[];
  /** ★ 空版本不占在流转位时为 false。头部接口形状块没列它，可能缺 ⇒ 可选，缺了就不渲染那一行 */
  in_flight?: boolean;
  content_digest?: string;
}

export interface Rev {
  rev: number;
  content_digest: string;
  is_current: boolean;
  in_flight: boolean;
  submitted_by: string;
  submitted_at: string;
  lines: number;
}

export interface RevList {
  revs: Rev[];
  /** ★ 两个标记不是一回事（06 §1.3）：流转中 ≠ 当前使用 */
  in_flight_rev: number | null;
  current_rev: number | null;
}

export interface DiffMoved { sku: string; period: Period; total_units: number }
export interface DiffChanged {
  sku: string;
  period: Period;
  total_units: { from: number; to: number };
  demand_at_submit: { from: number; to: number };
}
/** ★ 三个数组分开，不合成一个「变化量」—— 新增和改动的处置不同 */
export interface PlanDiff { added: DiffMoved[]; removed: DiffMoved[]; changed: DiffChanged[] }

export interface CancelRevResult { cancelled: number[]; reason: string }

export interface ListPlansQuery {
  /** ★ 只发契约里声明过的参数 —— 未声明查询参数一律 400 */
  state?: LineState;
  owner?: string;
  archived?: boolean;
}
export interface CreatePlanInput { title: string; period_start: string; months: number }
export interface CatalogQuery { q?: string; limit?: number }
```

- [ ] **Step 2: 写接口与 ApiError**

```ts
// web/src/api/client.ts
import type {
  CancelRevResult, CatalogQuery, CatalogResult, ClaimResult, ClaimTarget, CreatePlanInput,
  DashboardPlans, DemandCell, GridResponse, ListPlansQuery, Period, PlanDiff, PlanId,
  PlanList, PlanSummary, PurchaseCell, ReleaseResult, RevList, Seller, Sid, SubmitResult,
  UnsubmittedBoard,
} from './types';

/** ★ 后端的错误形状是 `{error, hint, …点名字段平铺在顶层}`（team-lead 2026-09-22 裁定）。
 *  `08` §0 写的 `{code, message, detail}` 已被这一条覆盖 —— 两份形状只能有一份生效。 */
export class ApiError extends Error {
  readonly status: number;
  readonly error: string;
  readonly hint: string;
  /** 顶层除 error/hint 外的全部字段，就是「点名是哪几行」的那部分 */
  readonly fields: Record<string, unknown>;
  constructor(status: number, error: string, hint: string, fields: Record<string, unknown> = {}) {
    super(`${status} ${error}: ${hint}`);
    this.name = 'ApiError';
    this.status = status;
    this.error = error;
    this.hint = hint;
    this.fields = fields;
  }
}

export interface SupplyChainApi {
  listPlans(q: ListPlansQuery): Promise<PlanList>;
  /** `GET /grid` 不带计划抬头 ⇒ 从列表里取。封在数据层，页面不必知道要拉两次 */
  getPlan(planId: PlanId): Promise<PlanSummary>;
  createPlan(input: CreatePlanInput): Promise<{ plan_id: PlanId }>;
  dashboardPlans(): Promise<DashboardPlans>;
  dashboardUnsubmitted(): Promise<UnsubmittedBoard>;
  listSellers(): Promise<Seller[]>;

  getGrid(planId: PlanId): Promise<GridResponse>;
  /** ★ units 可以是 null —— 空 = 未知 */
  putDemand(planId: PlanId, sellerSku: string, sid: Sid, period: Period, units: number | null): Promise<DemandCell>;
  putPurchase(planId: PlanId, sku: string, period: Period, units: number | null): Promise<PurchaseCell>;

  searchCatalog(q: CatalogQuery): Promise<CatalogResult>;
  /** ★ 一次一个 msku（后端只有单条端点）；页面循环并逐条收集 409 */
  claim(planId: PlanId, target: ClaimTarget): Promise<ClaimResult>;
  releaseClaim(planId: PlanId, sellerSku: string, sid: Sid): Promise<ReleaseResult>;

  submit(planId: PlanId): Promise<SubmitResult>;
  listRevs(planId: PlanId): Promise<RevList>;
  setCurrentRev(planId: PlanId, rev: number): Promise<{ current_rev: number }>;
  diff(planId: PlanId, from: number, to: number): Promise<PlanDiff>;
  cancelRev(planId: PlanId, rev: number, reason: string): Promise<CancelRevResult>;
}
```

- [ ] **Step 3: 把后端的 fixture 复制过来，并写字节比对**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
cp tests/fixtures/grid_response.json web/src/api/fixtures/grid-1.json
```

```ts
// web/src/api/fixtures.test.ts
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const BACKEND = '../../../tests/fixtures/grid_response.json';
const MINE = './fixtures/grid-1.json';

describe('grid fixture 与后端同源', () => {
  it('★ 逐字节相同 —— 两份各自维护的话，前端跑 mock 全绿、切 api 才发现字段名不一样', () => {
    const h = (p: string) => createHash('sha256')
      .update(readFileSync(new URL(p, import.meta.url))).digest('hex');
    expect(h(MINE)).toBe(h(BACKEND));
  });

  it('★ fixture 覆盖三种长得像的形态，缺一种前端就永远画不出它', () => {
    const g = JSON.parse(readFileSync(new URL(MINE, import.meta.url), 'utf8'));
    const inv = g.inventory as { closing: number | null; onhand: number | null; inbound: null;
                                 basis: { reason: string; closing_reason: string | null } }[];
    expect(inv.some((r) => r.basis.closing_reason === 'not_applicable')).toBe(true);  // 不适用
    expect(inv.some((r) => r.basis.closing_reason === 'unknown_demand')).toBe(true);  // 未知
    expect(inv.some((r) => typeof r.closing === 'number')).toBe(true);                // 有数
    // ★ 两种 null 成因必须靠 closing_reason 分得开，不能只看 closing===null
    expect(new Set(inv.filter((r) => r.closing === null).map((r) => r.basis.closing_reason)).size)
      .toBeGreaterThan(1);
    // ★ inbound 恒 null（不是 0）；reason 恒定 —— 它解释的是 inbound，不是 closing
    expect(inv.every((r) => r.inbound === null)).toBe(true);
    expect(new Set(inv.map((r) => r.basis.reason))).toEqual(new Set(['no_seller_attribution']));
    expect(g.demand.some((d: { basis: string }) => d.basis === 'human')).toBe(true);
    expect(g.demand.some((d: { basis: string }) => d.basis === 'system')).toBe(true);
  });
});
```

- [ ] **Step 4: 跑它，确认红**

```bash
cd web && npx vitest run src/api/fixtures.test.ts
```
Expected（后端 Task 15 还没跑时）: FAIL —— `ENOENT ... tests/fixtures/grid_response.json`。
★ 这条红的处置是**去等后端 Task 15**，不是把测试改成 skip。
后端产物到位后重跑，Expected: PASS（2 个用例）。

- [ ] **Step 5: 写其余四份 fixture（前端自管）**

★ 只有 `grid-1.json` 与后端同源；下面四份是前端 mock 的布景，后端有对应端点时再收敛。

```json
// web/src/api/fixtures/sellers.json
{ "sellers": [
  { "seller_id": "11072", "name": "A4Pet-US", "market": "US", "has_fba": true,  "platform": "amazon" },
  { "seller_id": "11094", "name": "A4Pet-BS-UK", "market": "UK", "has_fba": true,  "platform": "amazon" },
  { "seller_id": "90001", "name": "A4Pet-WM", "market": "US", "has_fba": false, "platform": "walmart" }
] }
```

```json
// web/src/api/fixtures/plans.json
{ "plans": [
  { "plan_id": 1, "title": "2026 Q4 销售计划", "period_start": "2026-10-01", "months": 3,
    "owner_actor": "ops.zhang", "archived_at": null, "state": null, "state_rev": null },
  { "plan_id": 2, "title": "2026 Q3 补货计划", "period_start": "2026-07-01", "months": 3,
    "owner_actor": "ops.zhang", "archived_at": null, "state": "已提交", "state_rev": 2 },
  { "plan_id": 3, "title": "2026 Q2 清库计划", "period_start": "2026-04-01", "months": 3,
    "owner_actor": "ops.li", "archived_at": null, "state": "已撤销", "state_rev": 1 },
  { "plan_id": 4, "title": "2026 Q1 首发计划", "period_start": "2026-01-01", "months": 3,
    "owner_actor": "ops.li", "archived_at": null, "state": "已完结", "state_rev": 3 }
], "excluded": { "archived": 2 } }
```

★ `plan 4` 是 `已完结`：它存在**只为了证明首页把它过滤掉了** —— 没有一条完结的计划，
「完结的计划不显示」这条规矩就测不出来。`excluded.archived: 2` 同理，用来测「挡掉的那一侧有个数」。

```json
// web/src/api/fixtures/catalog.json
{ "need_query": false, "matched": 2, "truncated": false, "limit": 50, "items": [
  { "sku": "DCC1800264G1", "name": "猫爬架",
    "mskus": [
      { "seller_sku": "MSKU-A", "sid": "11072", "seller_name": "A4Pet-US", "selectable": true, "claimed_by": null },
      { "seller_sku": "MSKU-C", "sid": "11094", "seller_name": "A4Pet-BS-UK", "selectable": false,
        "claimed_by": { "plan_id": 2, "title": "2026 Q3 补货计划", "actor": "ops.li" } },
      { "seller_sku": "MSKU-W", "sid": "90001", "seller_name": "A4Pet-WM", "selectable": true, "claimed_by": null }
    ],
    "unbuildable_sellers": [],
    "claimed_by": { "plan_id": 2, "title": "2026 Q3 补货计划" },
    "claimed_by_plans": [{ "plan_id": 2, "title": "2026 Q3 补货计划" }] },
  { "sku": "A4P-TOY-002", "name": "逗猫棒",
    "mskus": [
      { "seller_sku": "MSKU-B", "sid": "11072", "seller_name": "A4Pet-US", "selectable": true, "claimed_by": null }
    ],
    "unbuildable_sellers": [], "claimed_by": null, "claimed_by_plans": [] }
] }
```

```json
// web/src/api/fixtures/revs-1.json
{ "revs": [
  { "rev": 2, "content_digest": "3b7e5d1c8a90f2e4", "is_current": true, "in_flight": true,
    "submitted_by": "ops.zhang", "submitted_at": "2026-09-20T10:31:00+08:00", "lines": 3 },
  { "rev": 1, "content_digest": "8f1c2a0b9d4e6f77", "is_current": false, "in_flight": false,
    "submitted_by": "ops.zhang", "submitted_at": "2026-09-19T14:03:00+08:00", "lines": 2 }
], "in_flight_rev": 2, "current_rev": 2 }
```

- [ ] **Step 6: 写 mock 的失败测试**

```ts
// web/src/api/mock.test.ts
import { beforeEach, describe, expect, it } from 'vitest';
import { createMockApi } from './mock';
import { ApiError } from './client';

let api = createMockApi();
beforeEach(() => { api = createMockApi(); });

const firstMsku = async () => (await api.getGrid(1)).demand[0]!;

describe('mock 数据源', () => {
  it('月份一律 "YYYY-MM"，不是月初日期', async () => {
    const g = await api.getGrid(1);
    expect(g.periods.every((p) => /^\d{4}-\d{2}$/.test(p))).toBe(true);
  });

  it('putDemand(null) 存进去的是 null，不是 0；basis 退回 unknown 或 system', async () => {
    const d = await firstMsku();
    const cell = await api.putDemand(1, d.seller_sku, d.sid, d.period, null);
    expect(cell.expected_units).toBeNull();
    expect(['system', 'unknown']).toContain(cell.basis);
    const back = (await api.getGrid(1)).demand
      .find((x) => x.seller_sku === d.seller_sku && x.period === d.period)!;
    expect(back.expected_units).toBeNull();
  });

  it('★ 库存的身份是「店铺 × 货号」—— 一格对应多个 msku，不是每个 msku 一格', async () => {
    const g = await api.getGrid(1);
    expect(g.inventory.every((i) => 'sku' in i && 'sid' in i && !('seller_sku' in i))).toBe(true);
    const keys = new Set(g.inventory.map((i) => `${i.sku}/${i.sid}/${i.period}`));
    expect(keys.size).toBe(g.inventory.length);           // ★ 每个 (货号,店铺,月) 只有一行
  });

  it('★ 任何一个 msku 未知 → 整格未知（不是把它当 0 再把别的 msku 加进来）', async () => {
    const d = await firstMsku();
    await api.putDemand(1, d.seller_sku, d.sid, d.period, null);
    const g = await api.getGrid(1);
    const cell = g.inventory.find((i) => i.sku === d.sku && i.sid === d.sid && i.period === d.period)!;
    expect(cell.closing).toBeNull();
    expect(cell.basis.closing_reason).toBe('unknown_demand');
    // ★ reason 是另一件事，不许被顺手改掉 —— 改了就把「在途没归属」这条信息抹掉了
    expect(cell.basis.reason).toBe('no_seller_attribution');
  });

  it('★ inbound 恒 null（不是 0）；在途总量只在 basis 与 sku_pipeline 里出现', async () => {
    const g = await api.getGrid(1);
    expect(g.inventory.every((i) => i.inbound === null)).toBe(true);
    expect(g.inventory.every((i) => i.basis.includes_plan_purchase === false)).toBe(true);
    expect(g.sku_pipeline.every((r) => r.no_seller_attribution === true)).toBe(true);
    // ★ 在途一件都没有并进任何一格库存
    for (const i of g.inventory) {
      const t = i.basis.sku_level_in_transit;
      if (t !== null && i.onhand !== null && i.closing !== null) expect(i.closing).not.toBe(i.onhand + t);
    }
  });

  it('★ 跨月是一条链：本月期初 = 上月期末（不是同一个在仓快照抄三遍）', async () => {
    const g0 = await api.getGrid(1);
    const groups = new Map<string, typeof g0.inventory>();
    for (const i of g0.inventory) {
      const k = `${i.sku}/${i.sid}`;
      groups.set(k, [...(groups.get(k) ?? []), i]);
    }
    const chain = [...groups.values()]
      .map((rows) => rows.sort((a, b) => a.period.localeCompare(b.period)))
      .find((rows) => rows.length >= 2 && rows[0]!.closing !== null
                      && rows[0]!.closing !== rows[0]!.onhand);   // ★ 有消耗，两种口径才分得开
    // ★ fixture 里没有一个「有消耗」的月份时必须硬失败：那说明这条门禁什么都没测
    expect(chain, 'fixture 里没有 closing ≠ onhand 的月份，链式口径无法证伪，请让后端补一个').toBeDefined();
    expect(chain![1]!.onhand).toBe(chain![0]!.closing);
  });

  it('★ basis.demand 与前端自己算的 Σ 是两个证人，必须一致', async () => {
    const g = await api.getGrid(1);
    for (const i of g.inventory) {
      if (i.basis.closing_reason === 'not_applicable') continue;
      const mine = g.demand.filter((d) => d.sku === i.sku && d.sid === i.sid && d.period === i.period);
      const sum = mine.length === 0 || mine.some((d) => d.effective_units === null)
        ? null : mine.reduce((a, d) => a + (d.effective_units as number), 0);
      expect(i.basis.demand).toBe(sum);
    }
  });

  it('★ 占用撞了抛 409 并点名占用方（点名字段在顶层，不在 detail 里）', async () => {
    const err = await api.claim(1, { seller_sku: 'MSKU-C', sid: '11094' }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).error).toBe('msku_already_claimed');
    expect((err as ApiError).fields).toMatchObject({ plan_id: 2, title: '2026 Q3 补货计划' });
  });

  it('★ 提交逐条列 skipped[]，理由取 S-14 的两个值；铸出与跳过两个数都给', async () => {
    const r = await api.submit(1);
    expect(r.rev).toBe(1);
    expect(r.skipped.map((s) => s.reason)).toContain('zero_purchase');
    // ★ 被丢掉的那一侧要对得上：铸出 + 跳过 = 参与评估的（货号 × 月）格子数
    const g = await api.getGrid(1);
    expect(r.lines + r.skipped.length).toBe(g.purchase.length);
  });

  it('★ 已有在流转的版本 → 再提交 409 rev_in_flight，点名旧版号', async () => {
    const err = await api.submit(2).catch((e) => e);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).error).toBe('rev_in_flight');
    expect((err as ApiError).fields).toMatchObject({ in_flight_rev: 2 });
  });

  it('搜索目录：不给条件 → need_query=true 且 items 为空，与「查不到」不同形', async () => {
    const none = await api.searchCatalog({});
    expect(none.need_query).toBe(true);
    expect(none.items).toEqual([]);
    const miss = await api.searchCatalog({ q: 'ZZZZ' });
    expect(miss.need_query).toBe(false);
    expect(miss.matched).toBe(0);
  });

  it('撤销版本不填理由 → 400 reason_required', async () => {
    const err = await api.cancelRev(2, 2, '   ').catch((e) => e);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).error).toBe('reason_required');
  });
});
```

- [ ] **Step 7: 跑它，确认红**

```bash
cd web && npx vitest run src/api/mock.test.ts
```
Expected: FAIL —— `Failed to resolve import "./mock"`。

- [ ] **Step 8: 写 mock 实现**

```ts
// web/src/api/mock.ts
import { ApiError, type SupplyChainApi } from './client';
import type {
  CatalogResult, GridResponse, PlanList, PlanSummary, RevList, Seller, SkippedCell,
} from './types';
import plansFixture from './fixtures/plans.json';
import gridFixture from './fixtures/grid-1.json';
import catalogFixture from './fixtures/catalog.json';
import revsFixture from './fixtures/revs-1.json';
import sellersFixture from './fixtures/sellers.json';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export function createMockApi(): SupplyChainApi {
  const list = clone(plansFixture) as PlanList;
  const grids = new Map<number, GridResponse>([[1, clone(gridFixture) as GridResponse]]);
  const catalog = clone(catalogFixture) as CatalogResult;
  const revs = new Map<number, RevList>([[2, clone(revsFixture) as RevList]]);
  const sellers = (clone(sellersFixture) as { sellers: Seller[] }).sellers;
  const inFlight = new Map<number, number | null>([[1, null], [2, 2], [3, null], [4, null]]);

  const plan = (id: number): PlanSummary => {
    const p = list.plans.find((x) => x.plan_id === id);
    if (!p) throw new ApiError(404, 'plan_not_found', '没有这张计划', { plan_id: id });
    return p;
  };
  const grid = (id: number): GridResponse => {
    const g = grids.get(id);
    if (!g) throw new ApiError(404, 'plan_not_found', '这张计划还没有网格', { plan_id: id });
    return g;
  };

  /** ★ 一格的库存 = 该**店铺 × 货号**的在仓 − 该店该货号各 msku 的生效期望销量之和。
   *  任何一个 msku 未知 ⇒ 整格未知（M-8 向后传染）。落回 0 就是把「算不出来」说成「没货」。
   *  ★ 跨月是一条链：本月期初 = 上月期末（后端接口形状块 `onhand` 那一行写死了这一点）。 */
  function recompute(g: GridResponse, sku: string, sid: string): void {
    const rows = g.inventory
      .filter((i) => i.sku === sku && i.sid === sid)
      .sort((a, b) => a.period.localeCompare(b.period));
    let carried: number | null = rows[0]?.onhand ?? null;
    for (const row of rows) {
      if (row.basis.closing_reason === 'not_applicable') {
        // ★ 不适用：不是 0，也不参与链。reason 不动 —— 它恒为 no_seller_attribution
        row.onhand = null; row.basis.demand = null; row.closing = null; continue;
      }
      const mine = g.demand.filter((d) => d.sku === sku && d.sid === sid && d.period === row.period);
      const demand = mine.length === 0 || mine.some((d) => d.effective_units === null)
        ? null : mine.reduce((a, d) => a + (d.effective_units as number), 0);
      row.onhand = carried;
      row.basis.demand = demand;
      row.closing = carried === null || demand === null ? null : carried - demand;
      // ★ 只改 closing_reason；reason 是另一件事，改它就会把「在途没归属」这条信息抹掉
      row.basis.closing_reason = row.closing === null ? 'unknown_demand' : null;
      carried = row.closing;                      // ★ 本月期末 = 下月期初（链）
    }
  }

  return {
    async listPlans(q) {
      const plans = list.plans.filter((p) =>
        (q.state === undefined || p.state === q.state) &&
        (q.owner === undefined || p.owner_actor === q.owner) &&
        (q.archived === undefined || (p.archived_at !== null) === q.archived));
      return { plans, excluded: list.excluded };
    },
    async getPlan(planId) { return clone(plan(planId)); },
    async createPlan(input) {
      const plan_id = Math.max(...list.plans.map((p) => p.plan_id)) + 1;
      list.plans.push({
        plan_id, title: input.title, period_start: input.period_start, months: input.months,
        owner_actor: 'ops.zhang', archived_at: null, state: null, state_rev: null,
      });
      inFlight.set(plan_id, null);
      grids.set(plan_id, { plan_id, periods: [], demand: [], purchase: [], inventory: [], sku_pipeline: [] });
      return { plan_id };
    },
    async dashboardPlans() {
      // ★ 返回全集（诚实）。够不着的三个态由前端不渲染，不是接口抹成 0（S-20）
      return {
        counts: { 进行中: 2, 已提交: 1, 已提交未确认: 1, 已下单: 0, 准备排货: 0, 已排货: 0 },
        scope_note: { unreachable_in_stage_a: ['已下单', '准备排货', '已排货'], never_submitted_excluded: 1 },
      };
    },
    async dashboardUnsubmitted() {
      return {
        never_submitted: list.plans.filter((p) => p.state === null).map((p) => ({ plan_id: p.plan_id, title: p.title })),
        changed_since_submit: list.plans.filter((p) => p.plan_id === 2)
          .map((p) => ({ plan_id: p.plan_id, title: p.title, since_rev: p.state_rev ?? 0 })),
      };
    },
    async listSellers() { return clone(sellers); },

    async getGrid(planId) { return clone(grid(planId)); },

    async putDemand(planId, sellerSku, sid, period, units) {
      const g = grid(planId);
      const cell = g.demand.find((d) => d.seller_sku === sellerSku && d.sid === sid && d.period === period);
      if (!cell) {
        throw new ApiError(404, 'cell_not_found', '这一格还没长出来（先认领这个 msku）',
          { plan_id: planId, seller_sku: sellerSku, sid, period });
      }
      if (units !== null && (!Number.isInteger(units) || units < 0)) {
        throw new ApiError(400, 'bad_units', 'expected_units 必须是 ≥0 的整数或 null',
          { field: 'expected_units', got: units });
      }
      cell.expected_units = units;
      cell.effective_units = units ?? cell.system_units;
      cell.basis = units !== null ? 'human' : cell.system_units !== null ? 'system' : 'unknown';
      recompute(g, cell.sku, sid);
      return clone(cell);
    },

    async putPurchase(planId, sku, period, units) {
      const g = grid(planId);
      const cell = g.purchase.find((p) => p.sku === sku && p.period === period);
      if (!cell) {
        throw new ApiError(404, 'cell_not_found', '这一格还没长出来（先认领该货号下的 msku）',
          { plan_id: planId, sku, period });
      }
      if (units !== null && (!Number.isInteger(units) || units < 0)) {
        throw new ApiError(400, 'bad_units', 'planned_units 必须是 ≥0 的整数或 null',
          { field: 'planned_units', got: units });
      }
      cell.planned_units = units;
      return clone(cell);
    },

    async searchCatalog(q) {
      const limit = q.limit ?? catalog.limit;
      if (!q.q) return { need_query: true, truncated: false, limit, items: [], matched: 0 };
      const needle = q.q.toUpperCase();
      const items = catalog.items.filter((s) =>
        s.sku.toUpperCase().includes(needle) || s.name.includes(q.q!) ||
        s.mskus.some((m) => m.seller_sku.toUpperCase().includes(needle)));
      return { need_query: false, truncated: items.length > limit, limit, items: clone(items), matched: items.length };
    },

    async claim(planId, target) {
      const item = catalog.items.find((s) => s.mskus.some((m) => m.seller_sku === target.seller_sku && m.sid === target.sid));
      const msku = item?.mskus.find((m) => m.seller_sku === target.seller_sku && m.sid === target.sid);
      if (!item || !msku) throw new ApiError(404, 'msku_not_found', '没有这个 msku', { ...target });
      if (!msku.selectable && msku.claimed_by) {
        // ★ 点名字段平铺在顶层，前端据此说出「被哪张计划、被谁占着」
        throw new ApiError(409, 'msku_already_claimed', `${target.seller_sku} 已被占用`,
          { ...target, plan_id: msku.claimed_by.plan_id, title: msku.claimed_by.title, actor: msku.claimed_by.actor });
      }
      msku.selectable = false;
      msku.claimed_by = { plan_id: planId, title: plan(planId).title, actor: 'ops.zhang' };
      return { claimed: { ...target, sku: item.sku } };
    },

    async releaseClaim(_planId, sellerSku, sid) {
      const msku = catalog.items.flatMap((s) => s.mskus).find((m) => m.seller_sku === sellerSku && m.sid === sid);
      if (msku) { msku.selectable = true; msku.claimed_by = null; }   // ★ 释放不删行
      return { released: { seller_sku: sellerSku, sid }, dropped_cells: [], stranded_purchase_cells: [] };
    },

    async submit(planId) {
      const held = inFlight.get(planId) ?? null;
      if (held !== null) {
        throw new ApiError(409, 'rev_in_flight', `rev ${held} 还在流转`, { in_flight_rev: held });
      }
      const g = grid(planId);
      const skipped: SkippedCell[] = [];
      let lines = 0;
      for (const pc of g.purchase) {
        const claimed = g.demand.some((d) => d.sku === pc.sku && d.period === pc.period);
        if (!claimed) { skipped.push({ sku: pc.sku, period: pc.period, reason: 'no_claimed_msku' }); continue; }
        if (pc.planned_units === null || pc.planned_units === 0) {
          skipped.push({ sku: pc.sku, period: pc.period, reason: 'zero_purchase' }); continue;
        }
        lines += 1;
      }
      const p = plan(planId);
      const rev = (p.state_rev ?? 0) + 1;
      // ★ 空版本不占在流转位：占着的话这张计划从此再也提交不了，而错误会说「有一版在流转」
      const alive = lines > 0;
      p.state_rev = rev;
      p.state = alive ? '已提交' : '已撤销';
      inFlight.set(planId, alive ? rev : null);
      revs.set(planId, {
        revs: [{ rev, content_digest: `mock-${planId}-${rev}`, is_current: true, in_flight: alive,
                 submitted_by: 'ops.zhang', submitted_at: new Date().toISOString(), lines }],
        in_flight_rev: alive ? rev : null, current_rev: rev,
      });
      return { rev, lines, in_flight: alive, content_digest: `mock-${planId}-${rev}`, skipped };
    },

    async listRevs(planId) {
      return clone(revs.get(planId) ?? { revs: [], in_flight_rev: null, current_rev: null });
    },
    async setCurrentRev(planId, rev) {
      const l = revs.get(planId);
      if (!l || !l.revs.some((r) => r.rev === rev)) throw new ApiError(404, 'rev_not_found', '没有这一版', { plan_id: planId, rev });
      l.revs.forEach((r) => { r.is_current = r.rev === rev; });
      l.current_rev = rev;
      return { current_rev: rev };
    },
    async diff(_planId, _from, _to) {
      return {
        added: [{ sku: 'DCC1800264G1', period: '2026-11', total_units: 300 }],
        removed: [],
        changed: [{ sku: 'DCC1800264G1', period: '2026-10',
                    total_units: { from: 500, to: 600 }, demand_at_submit: { from: 160, to: 180 } }],
      };
    },
    async cancelRev(planId, rev, reason) {
      if (reason.trim() === '') throw new ApiError(400, 'reason_required', '撤销必须填理由', { plan_id: planId, rev });
      const l = revs.get(planId);
      const target = l?.revs.find((r) => r.rev === rev);
      if (!l || !target) throw new ApiError(404, 'rev_not_found', '没有这一版', { plan_id: planId, rev });
      target.in_flight = false;
      l.in_flight_rev = null;
      inFlight.set(planId, null);
      plan(planId).state = '已撤销';
      return { cancelled: Array.from({ length: target.lines }, (_, i) => i + 1), reason };
    },
  };
}
```

- [ ] **Step 9: 跑 mock 测试，确认全绿**

```bash
cd web && npx vitest run src/api/mock.test.ts
```
Expected: PASS（9 个用例）。
★ 若 `lines + skipped.length === purchase.length` 那条红，说明分类把某些格子**两边都没算** ——
那正是这条断言存在的理由，不许改断言，去查分支。

- [ ] **Step 10: 写 http 的失败测试（含三问日志）**

```ts
// web/src/api/http.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createHttpApi } from './http';
import { ApiError } from './client';
import { setActor } from '../shell/actorStore';
import gridFixture from './fixtures/grid-1.json';

afterEach(() => { vi.restoreAllMocks(); });

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

const mk = () => createHttpApi({ base: '/v1', timeoutMs: 1000 });

describe('http 数据源', () => {
  it('发 x-actor 头，取自顶栏下拉', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok(gridFixture));
    setActor('ops.li');
    await mk().getGrid(1);
    expect(new Headers((spy.mock.calls[0]![1] as RequestInit).headers).get('x-actor')).toBe('ops.li');
  });

  it('★ 只发声明过的查询参数 —— undefined 的不拼进 URL（未声明参数后端一律 400）', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ plans: [], excluded: { archived: 0 } }));
    await mk().listPlans({ archived: false });
    expect(String(spy.mock.calls[0]![0])).toBe('/v1/plans?archived=false');
  });

  it('★ 列表响应带一层包裹，数据层拆开 —— 页面不该知道这层', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({
      sellers: [{ seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' }],
    }));
    expect(await mk().listSellers()).toEqual([
      { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
    ]);
  });

  it('★ PUT 的 body 字段名按契约：期望销量 expected_units、采购量 planned_units', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ cell: {} }));
    const api = mk();
    await api.putDemand(1, 'MSKU-A', '11072', '2026-10', null);
    await api.putPurchase(1, 'SKU-1', '2026-10', 500);
    expect(JSON.parse((spy.mock.calls[0]![1] as RequestInit).body as string)).toEqual({ expected_units: null });
    expect(JSON.parse((spy.mock.calls[1]![1] as RequestInit).body as string)).toEqual({ planned_units: 500 });
  });

  it('409 抛 ApiError，点名字段从顶层收进 fields', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ error: 'rev_in_flight', hint: '先处理 rev 2', in_flight_rev: 2 }),
      { status: 409, headers: { 'content-type': 'application/json' } }));
    const err = await mk().submit(1).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).error).toBe('rev_in_flight');
    expect((err as ApiError).hint).toBe('先处理 rev 2');
    expect((err as ApiError).fields).toEqual({ in_flight_rev: 2 });
  });

  it('★ 日志三问：打的谁 · 多久 · 怎么失败的（带 cause.code）', async () => {
    const logged: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { logged.push(a); });
    const boom = new TypeError('fetch failed');
    (boom as { cause?: unknown }).cause = { code: 'UND_ERR_CONNECT_TIMEOUT' };
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(boom);

    await mk().getGrid(1).catch(() => undefined);

    const line = JSON.stringify(logged[0]);
    expect(line).toContain('GET /v1/plans/1/grid');   // 打的谁
    expect(line).toMatch(/"ms":\d+/);                  // 多久
    expect(line).toContain('UND_ERR_CONNECT_TIMEOUT'); // ★ 不是「fetch failed」五个字
  });

  it('★ 超时与连不上分得开：超时记 TimeoutError，不是 cause code', async () => {
    const logged: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { logged.push(a); });
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(
      new DOMException('The operation was aborted due to timeout', 'TimeoutError'));
    const err = await createHttpApi({ base: '/v1', timeoutMs: 5 }).getGrid(1).catch((e) => e);
    expect((err as ApiError).error).toBe('timeout');
    expect(JSON.stringify(logged[0])).toContain('TimeoutError');
  });
});
```

- [ ] **Step 11: 跑它确认红，然后写 http 实现**

```bash
cd web && npx vitest run src/api/http.test.ts
```
Expected: FAIL —— `Failed to resolve import "./http"`。

```ts
// web/src/api/http.ts
import { ApiError, type SupplyChainApi } from './client';
import { getActor } from '../shell/actorStore';
import type { CatalogResult, PlanList, PlanSummary, Seller } from './types';

interface HttpOptions { base: string; timeoutMs: number }

/** ★ 三问日志：打的谁（method+path）· 多久（ms）· 怎么失败的（status+error 或 cause.code）。
 *  缺一个，下次就得重新复现一遍。 */
function logFail(call: string, ms: number, extra: Record<string, unknown>): void {
  console.error('[api]', JSON.stringify({ call, ms, ...extra }));
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  // ★ 未声明查询参数后端一律 400 ⇒ undefined 的不拼，也不发空串
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined);
  return pairs.length === 0 ? '' : `?${pairs.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')}`;
}

export function createHttpApi(opt: HttpOptions): SupplyChainApi {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${opt.base}${path}`;
    const call_ = `${method} ${url}`;
    const started = Date.now();
    let res: Response;
    try {
      res = await fetch(url, {
        method,
        signal: AbortSignal.timeout(opt.timeoutMs),
        headers: { 'content-type': 'application/json', 'x-actor': getActor() },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (e) {
      const ms = Date.now() - started;
      // ★ 「超时」与「连不上」签名互斥、处置相反：前者调大超时有用，后者一行都不会生效
      if (e instanceof DOMException && e.name === 'TimeoutError') {
        logFail(call_, ms, { kind: 'TimeoutError', timeout_ms: opt.timeoutMs });
        throw new ApiError(0, 'timeout', `${opt.timeoutMs}ms 内没有响应`, { timeout_ms: opt.timeoutMs });
      }
      const cause = (e as { cause?: { code?: string; errors?: { code?: string }[] } }).cause;
      const code = cause?.code ?? cause?.errors?.[0]?.code ?? 'unknown';
      logFail(call_, ms, { kind: (e as Error).name, cause_code: code, message: (e as Error).message });
      throw new ApiError(0, 'network', `连不上（${code}）`, { cause_code: code });
    }

    const ms = Date.now() - started;
    if (ms >= 2000) console.warn('[api]', JSON.stringify({ call: call_, ms, status: res.status, slow: true }));

    const text = await res.text();
    let parsed: unknown = null;
    if (text !== '') {
      try { parsed = JSON.parse(text); }
      catch {
        // ★ 静默兜底是最坏的一种：「代理挂了」和「接口本身就错」必须长得不一样
        logFail(call_, ms, { status: res.status, kind: 'non_json_body', head: text.slice(0, 120) });
        throw new ApiError(res.status, 'bad_response', '返回的不是 JSON', { head: text.slice(0, 120) });
      }
    }
    if (!res.ok) {
      const { error, hint, ...fields } = (parsed ?? {}) as
        { error?: string; hint?: string } & Record<string, unknown>;
      logFail(call_, ms, { status: res.status, error: error ?? 'unknown' });
      throw new ApiError(res.status, error ?? 'unknown', hint ?? `${res.status}`, fields);
    }
    return parsed as T;
  }

  const plans = (q: Parameters<SupplyChainApi['listPlans']>[0]) =>
    call<PlanList>('GET', `/plans${qs({ state: q.state, owner: q.owner, archived: q.archived })}`);

  return {
    listPlans: plans,
    async getPlan(planId) {
      // ★ GET /grid 不带计划抬头；这里拆两次调用，页面不必知道
      const { plans: rows } = await plans({});
      const hit = rows.find((p: PlanSummary) => p.plan_id === planId);
      if (!hit) throw new ApiError(404, 'plan_not_found', '没有这张计划', { plan_id: planId });
      return hit;
    },
    createPlan: (input) => call('POST', '/plans', input),
    dashboardPlans: () => call('GET', '/dashboard/plans'),
    dashboardUnsubmitted: () => call('GET', '/dashboard/unsubmitted'),
    listSellers: async () => (await call<{ sellers: Seller[] }>('GET', '/sellers')).sellers,

    getGrid: (planId) => call('GET', `/plans/${planId}/grid`),
    putDemand: async (planId, sellerSku, sid, period, units) =>
      (await call<{ cell: Awaited<ReturnType<SupplyChainApi['putDemand']>> }>(
        'PUT', `/plans/${planId}/demand/${encodeURIComponent(sellerSku)}/${sid}/${period}`,
        { expected_units: units })).cell,
    putPurchase: async (planId, sku, period, units) =>
      (await call<{ cell: Awaited<ReturnType<SupplyChainApi['putPurchase']>> }>(
        'PUT', `/plans/${planId}/purchase/${encodeURIComponent(sku)}/${period}`,
        { planned_units: units })).cell,

    searchCatalog: (q) => call<CatalogResult>('GET', `/catalog/skus${qs({ q: q.q, limit: q.limit })}`),
    claim: (planId, target) => call('POST', `/plans/${planId}/claims`, target),
    releaseClaim: (planId, sellerSku, sid) =>
      call('DELETE', `/plans/${planId}/claims/${encodeURIComponent(sellerSku)}/${sid}`),

    submit: (planId) => call('POST', `/plans/${planId}/submit`),
    listRevs: (planId) => call('GET', `/plans/${planId}/revs`),
    setCurrentRev: (planId, rev) => call('POST', `/plans/${planId}/revs/${rev}/current`),
    diff: (planId, from, to) => call('GET', `/plans/${planId}/diff${qs({ from, to })}`),
    cancelRev: (planId, rev, reason) => call('POST', `/plans/${planId}/revs/${rev}/cancel`, { reason }),
  };
}
```

- [ ] **Step 12: 数据源开关**

```ts
// web/src/api/index.ts
import { createHttpApi } from './http';
import { createMockApi } from './mock';
import type { SupplyChainApi } from './client';

const source = import.meta.env.VITE_DATA_SOURCE ?? 'mock';

// ★ 配置类错误往启动钩子放，别等第一个请求才炸
if (source !== 'mock' && source !== 'api') {
  throw new Error(`VITE_DATA_SOURCE 只能是 mock | api，收到 ${JSON.stringify(source)}`);
}
console.info('[api]', JSON.stringify({ data_source: source, base: import.meta.env.VITE_API_BASE ?? '/v1' }));

export const api: SupplyChainApi = source === 'mock'
  ? createMockApi()
  : createHttpApi({
      base: import.meta.env.VITE_API_BASE ?? '/v1',
      timeoutMs: Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 8000),
    });

export { ApiError } from './client';
export type { SupplyChainApi } from './client';
```

- [ ] **Step 13: 写 ErrorDetail 的失败测试**

```tsx
// web/src/components/ErrorDetail.test.tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ErrorDetail } from './ErrorDetail';
import { ApiError } from '../api/client';

describe('ErrorDetail', () => {
  it('★ 逐项点名，不说「保存失败」', () => {
    render(<ErrorDetail err={new ApiError(409, 'msku_already_claimed', 'MSKU-C 已被占用',
      { plan_id: 2, title: '2026 Q3 补货计划', actor: 'ops.li' })} />);
    expect(screen.getByText('MSKU-C 已被占用')).toBeInTheDocument();
    expect(screen.getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(screen.getByText('ops.li')).toBeInTheDocument();
    expect(screen.queryByText(/失败$/)).toBeNull();
  });

  it('★ 409 与 400 的样子不同 —— 一个该刷新重来，一个该改表单', () => {
    const { container: conflict } = render(
      <ErrorDetail err={new ApiError(409, 'rev_in_flight', '先处理 rev 2', { in_flight_rev: 2 })} />);
    const { container: bad } = render(
      <ErrorDetail err={new ApiError(400, 'reason_required', '撤销必须填理由', {})} />);
    expect(conflict.querySelector('.flash--bad')).not.toBeNull();
    expect(conflict.querySelector('.gate__code')?.textContent).toBe('409 rev_in_flight');
    expect(bad.querySelector('.gate__code')?.textContent).toBe('400 reason_required');
  });
});
```

- [ ] **Step 14: 跑它确认红，再写实现**

```bash
cd web && npx vitest run src/components/ErrorDetail.test.tsx
```
Expected: FAIL —— `Failed to resolve import "./ErrorDetail"`。

```tsx
// web/src/components/ErrorDetail.tsx
import type { ApiError } from '../api/client';

// ★ 原则五：闸失败要点名 —— 一次列全，不是修一条报一条
export function ErrorDetail({ err }: { err: ApiError }) {
  const rows = Object.entries(err.fields);
  return (
    <div className="flash flash--bad" role="alert">
      <div>{err.hint}</div>
      <div className="gate__code">{err.status} {err.error}</div>
      {rows.length > 0 && (
        <ul>
          {rows.map(([k, v]) => (
            <li key={k} className="gate__detail">
              <span className="muted">{k}</span> {typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 15: 全跑一遍并提交**

```bash
cd web && npx vitest run && npx tsc -b
```
Expected: `Test Files 6 passed`（tokens · AppShell · fixtures · mock · http · ErrorDetail），tsc 静默。

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 数据层 —— 一个接口两个实现，grid fixture 与后端同源（字节比对）

错误形状按裁定的 {error, hint, …顶层点名字段}；月份对外一律 YYYY-MM。
http 侧日志答三问并分得清超时与连不上；ErrorDetail 逐项点名不说「失败」。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## Task 3: 运营首页

**Files:**
- Create: `web/src/components/Qty.tsx`
- Modify（整体替换 Task 1 的占位）: `web/src/pages/OpsHome.tsx`
- Test: `web/src/components/Qty.test.tsx` `web/src/pages/OpsHome.test.tsx`

**Interfaces:**
- Consumes: `api`（Task 2）· `AppShell` `pushToast`（Task 1）· 类型 `PlanSummary` `PlanList` `DashboardPlans` `UnsubmittedBoard` `CountKey`
- Produces:
  - `type QtyValue = { kind: 'num'; value: number } | { kind: 'unknown' } | { kind: 'na' } | { kind: 'nosum' }`
  - `<Qty v={QtyValue} big?: boolean />` —— Task 4 网格的合计列与结论数都用它
  - `STAGE_A_VISIBLE: readonly ['未提交', '已提交', '已撤销']`

- [ ] **Step 1: 写 Qty 的失败测试**

```tsx
// web/src/components/Qty.test.tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Qty } from './Qty';

describe('Qty', () => {
  it('★ 四种形态四个字形，没有一个退化成 0', () => {
    const { container } = render(
      <>
        <Qty v={{ kind: 'num', value: 380 }} />
        <Qty v={{ kind: 'num', value: 0 }} />
        <Qty v={{ kind: 'unknown' }} />
        <Qty v={{ kind: 'na' }} />
        <Qty v={{ kind: 'nosum' }} />
      </>,
    );
    expect(screen.getByText('380')).toBeInTheDocument();
    expect(container.querySelector('.qty--zero')?.textContent).toBe('0');
    expect(screen.getByText('—')).toHaveClass('cell__unknown');
    expect(screen.getByText('不适用')).toHaveClass('chip', 'chip--dim');
    expect(screen.getByText('不可求和')).toHaveClass('cell__unknown');
    // ★ 未知 / 不适用 / 不可求和 三者的字互不相同 —— 合成一个就查不出是哪种病
    expect(new Set(['—', '不适用', '不可求和']).size).toBe(3);
  });

  it('big 用 .cell__close（整格唯一的大字）', () => {
    const { container } = render(<Qty v={{ kind: 'num', value: 420 }} big />);
    expect(container.querySelector('.cell__close')?.textContent).toBe('420');
  });
});
```

- [ ] **Step 2: 跑它确认红**

```bash
cd web && npx vitest run src/components/Qty.test.tsx
```
Expected: FAIL —— `Failed to resolve import "./Qty"`。

- [ ] **Step 3: 写 Qty**

```tsx
// web/src/components/Qty.tsx

/** ★ 空 ≠ 0 · 不适用 ≠ 0 · 不可求和 ≠ 未知 —— 这三条在本项目各咬过一次，所以给三个字形 */
export type QtyValue =
  | { kind: 'num'; value: number }
  | { kind: 'unknown' }
  | { kind: 'na' }
  | { kind: 'nosum' };

export function Qty({ v, big = false }: { v: QtyValue; big?: boolean }) {
  if (v.kind === 'unknown') return <span className="cell__unknown">—</span>;
  if (v.kind === 'nosum') return <span className="cell__unknown">不可求和</span>;
  if (v.kind === 'na') return <span className="chip chip--dim">不适用</span>;
  const cls = big ? 'cell__close num' : `qty num${v.value === 0 ? ' qty--zero' : ''}`;
  return <span className={cls}>{v.value}</span>;
}
```

- [ ] **Step 4: 写首页的失败测试**

```tsx
// web/src/pages/OpsHome.test.tsx
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { OpsHome } from './OpsHome';

const renderHome = () => render(<MemoryRouter><OpsHome /></MemoryRouter>);

describe('运营首页', () => {
  it('只渲染够得着的三态，其余★不渲染（不是 0）', async () => {
    renderHome();
    const counts = await screen.findByTestId('counts');
    expect(within(counts).getByText('未提交')).toBeInTheDocument();
    expect(within(counts).getByText('已提交')).toBeInTheDocument();
    expect(within(counts).getByText('已撤销')).toBeInTheDocument();
    // ★ 阶段 A 够不着的：一个字都不许出现，包括写成 0
    for (const gone of ['已下单', '准备排货', '已排货', '进行中', '已提交未确认']) {
      expect(within(counts).queryByText(gone)).toBeNull();
    }
    expect(counts.textContent).not.toContain('0');
  });

  it('★ 接口说哪些态够不着，屏上就一个都不许有 —— 名单由接口给，不在前端硬编码', async () => {
    renderHome();
    const counts = await screen.findByTestId('counts');
    const unreachable = JSON.parse(screen.getByTestId('unreachable').textContent!) as string[];
    expect(unreachable.length).toBeGreaterThan(0);          // ★ 空名单等于这条断言没跑
    for (const s of unreachable) expect(counts.textContent).not.toContain(s);
  });

  it('★ 完结的计划不显示，但被挡掉的两侧都要有个数', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    expect(within(table).queryByText('2026 Q1 首发计划')).toBeNull();
    expect(screen.getByTestId('hidden-rows')).toHaveTextContent('已完结 1 张');
    expect(screen.getByTestId('hidden-rows')).toHaveTextContent('已归档 2 张');
  });

  it('两栏：未提交 / 提交后又改过（第二栏点名是跟哪一版比的）', async () => {
    renderHome();
    expect(await screen.findByTestId('never-submitted')).toHaveTextContent('2026 Q4 销售计划');
    const changed = screen.getByTestId('changed-since-submit');
    expect(changed).toHaveTextContent('2026 Q3 补货计划');
    expect(changed).toHaveTextContent('rev 2');
  });

  it('按状态筛选，列表跟着变', async () => {
    renderHome();
    await screen.findByTestId('plan-list');
    await userEvent.selectOptions(screen.getByLabelText('状态'), '已撤销');
    const table = screen.getByTestId('plan-list');
    expect(within(table).getByText('2026 Q2 清库计划')).toBeInTheDocument();
    expect(within(table).queryByText('2026 Q4 销售计划')).toBeNull();
  });

  it('新建销售计划 → 走到网格', async () => {
    renderHome();
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    await userEvent.type(screen.getByLabelText('标题'), '2027 Q1 销售计划');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(await screen.findByTestId('nav-to')).toHaveTextContent('/plans/5');
  });
});
```

★ 最后一条用 `nav-to` 而不是真跳转：首页只负责**发起**跳转，路由本身在 Task 8 的端到端里验。

- [ ] **Step 5: 跑它确认红**

```bash
cd web && npx vitest run src/pages/OpsHome.test.tsx
```
Expected: FAIL —— 6 条全红，第一条报 `Unable to find an element by: [data-testid="counts"]`（占位组件只有 `.empty`）。

- [ ] **Step 6: 写首页**

```tsx
// web/src/pages/OpsHome.tsx
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Qty } from '../components/Qty';
import { api, ApiError } from '../api';
import type { DashboardPlans, PlanList, PlanSummary, UnsubmittedBoard } from '../api/types';

/** ★ 阶段 A 只渲染够得着的三态（00e:45）。挡的地方只有这一处 —— 接口照样返回全集（S-20） */
export const STAGE_A_VISIBLE = ['未提交', '已提交', '已撤销'] as const;

type SortKey = 'period' | 'title' | 'state';

export function OpsHome() {
  const navigate = useNavigate();
  const [list, setList] = useState<PlanList | null>(null);
  const [board, setBoard] = useState<UnsubmittedBoard | null>(null);
  const [dash, setDash] = useState<DashboardPlans | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [navTo, setNavTo] = useState<string | null>(null);

  const [fPeriod, setFPeriod] = useState('');
  const [fTitle, setFTitle] = useState('');
  const [fRev, setFRev] = useState('');
  const [fState, setFState] = useState('');
  const [sort, setSort] = useState<SortKey>('period');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    // ★ 三个接口一起拉：计数与列表来自不同端点，分开拉会出现「计数说 3 张、列表 2 张」
    Promise.all([api.listPlans({ archived: false }), api.dashboardUnsubmitted(), api.dashboardPlans()])
      .then(([l, b, d]) => { setList(l); setBoard(b); setDash(d); })
      .catch((e: ApiError) => setErr(e));
  }, []);

  const finished = useMemo(() => (list?.plans ?? []).filter((p) => p.state === '已完结'), [list]);
  const visible = useMemo(() => {
    const kept = (list?.plans ?? []).filter((p) => p.state !== '已完结');
    const rows = kept.filter((p) =>
      (fPeriod === '' || p.period_start.startsWith(fPeriod)) &&
      (fTitle === '' || p.title.includes(fTitle)) &&
      (fRev === '' || String(p.state_rev ?? '') === fRev) &&
      (fState === '' || stateLabel(p) === fState));
    return [...rows].sort((a, b) =>
      sort === 'period' ? b.period_start.localeCompare(a.period_start)
      : sort === 'title' ? a.title.localeCompare(b.title)
      : stateLabel(a).localeCompare(stateLabel(b)));
  }, [list, fPeriod, fTitle, fRev, fState, sort]);

  const counts: Record<(typeof STAGE_A_VISIBLE)[number], number> = {
    未提交: board?.never_submitted.length ?? 0,
    已提交: dash?.counts['已提交'] ?? 0,
    已撤销: (list?.plans ?? []).filter((p) => p.state === '已撤销').length,
  };

  async function create(form: HTMLFormElement) {
    const data = new FormData(form);
    try {
      const { plan_id } = await api.createPlan({
        title: String(data.get('title')),
        period_start: `${String(data.get('period_start'))}-01`,  // ★ 建计划的入参是月初日期
        months: Number(data.get('months')),
      });
      setNavTo(`/plans/${plan_id}`);
      navigate(`/plans/${plan_id}`);
    } catch (e) {
      setErr(e as ApiError);
      pushToast({ kind: 'fail', text: (e as ApiError).hint });
    }
  }

  if (err && list === null) return <AppShell crumb="我的计划"><ErrorDetail err={err} /></AppShell>;

  return (
    <AppShell crumb="我的计划">
      <div className="head">
        <div className="head__main"><h1>我的计划</h1></div>
        <div className="head__act">
          <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>新建销售计划</button>
        </div>
      </div>

      <div className="sec counts" data-testid="counts">
        {STAGE_A_VISIBLE.map((k) => (
          <div className="kpi" key={k}>
            <span className="kpi__n">{counts[k]}</span>
            <span className="kpi__d">{k}</span>
          </div>
        ))}
      </div>
      {/* ★ 接口自报哪些态阶段 A 够不着；测试拿它当靶子，前端不硬编码这份名单 */}
      <span data-testid="unreachable" hidden>
        {JSON.stringify(dash?.scope_note.unreachable_in_stage_a ?? [])}
      </span>

      {creating && (
        <form className="sec bar" onSubmit={(e) => { e.preventDefault(); void create(e.currentTarget); }}>
          <label className="field"><span className="lbl">标题</span>
            <input className="inp inp--text" name="title" required /></label>
          <label className="field"><span className="lbl">起始月</span>
            <input className="inp" name="period_start" type="month" defaultValue="2026-10" required /></label>
          <label className="field"><span className="lbl">跨 N 月</span>
            <input className="inp inp--tiny" name="months" type="number" min={1} max={24} defaultValue={3} required /></label>
          <button type="submit" className="btn btn--primary">创建</button>
          <button type="button" className="btn btn--ghost" onClick={() => setCreating(false)}>取消</button>
        </form>
      )}
      {navTo && <span data-testid="nav-to" hidden>{navTo}</span>}

      <div className="sec twocol">
        <div className="panel">
          <div className="panel__head">未提交</div>
          <div className="panel__body" data-testid="never-submitted">
            {(board?.never_submitted ?? []).map((p) => (
              <div key={p.plan_id}><a href={`/plans/${p.plan_id}`}>{p.title}</a></div>
            ))}
            {board?.never_submitted.length === 0 && <div className="todos__empty">无</div>}
          </div>
        </div>
        <div className="panel">
          <div className="panel__head">提交后又改过</div>
          <div className="panel__body" data-testid="changed-since-submit">
            {(board?.changed_since_submit ?? []).map((p) => (
              <div key={p.plan_id}>
                <a href={`/plans/${p.plan_id}`}>{p.title}</a>{' '}
                <span className="muted">rev {p.since_rev}</span>
              </div>
            ))}
            {board?.changed_since_submit.length === 0 && <div className="todos__empty">无</div>}
          </div>
        </div>
      </div>

      <div className="bar">
        <label className="bar__grp"><span className="bar__lbl">周期</span>
          <input className="inp inp--tiny" value={fPeriod} onChange={(e) => setFPeriod(e.target.value)} /></label>
        <label className="bar__grp"><span className="bar__lbl">计划</span>
          <input className="inp" value={fTitle} onChange={(e) => setFTitle(e.target.value)} /></label>
        <label className="bar__grp"><span className="bar__lbl">版本</span>
          <input className="inp inp--tiny" value={fRev} onChange={(e) => setFRev(e.target.value)} /></label>
        <label className="bar__grp"><span className="bar__lbl">状态</span>
          <select className="inp" value={fState} onChange={(e) => setFState(e.target.value)}>
            <option value="">全部</option>
            {STAGE_A_VISIBLE.map((k) => <option key={k} value={k}>{k}</option>)}
          </select></label>
        <label className="bar__grp"><span className="bar__lbl">排序</span>
          <select className="inp" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            <option value="period">周期</option><option value="title">计划名</option><option value="state">状态</option>
          </select></label>
      </div>

      <div className="table-scroll">
        <table className="table table--dense" data-testid="plan-list">
          <thead><tr><th>周期</th><th>计划名</th><th className="r">版本</th><th>状态</th><th>详情</th></tr></thead>
          <tbody>
            {visible.map((p) => (
              <tr key={p.plan_id}>
                <td>{p.period_start.slice(0, 7)} · {p.months} 月</td>
                <td>{p.title}</td>
                <td className="r">{p.state_rev === null ? <Qty v={{ kind: 'unknown' }} /> : p.state_rev}</td>
                <td><span className="chip chip--dim">{stateLabel(p)}</span></td>
                <td><a href={`/plans/${p.plan_id}`}>打开</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* ★ 挡掉的两侧都要有个数 —— 「没有」和「被我藏了」长得一样 */}
      <div className="muted mt" data-testid="hidden-rows">
        已完结 {finished.length} 张 · 已归档 {list?.excluded.archived ?? 0} 张 不在列表
      </div>
      {err && <ErrorDetail err={err} />}
    </AppShell>
  );
}

/** ★ 从未提交的计划 state 为 null（没有 rev 就没有记录）—— 不是 04 里的任何一个状态值 */
function stateLabel(p: PlanSummary): string {
  return p.state === null ? '未提交' : p.state;
}
```

- [ ] **Step 7: 跑测试，确认变绿**

```bash
cd web && npx vitest run src/pages/OpsHome.test.tsx src/components/Qty.test.tsx
```
Expected: PASS（8 个用例）。
★ 若 `counts.textContent).not.toContain('0')` 红，检查是不是把够不着的态渲染成了 0 —— 不许把断言改松。

- [ ] **Step 8: 把「不渲染够不着的态」弄失败一次**

```bash
cd web
sed -i "s/{STAGE_A_VISIBLE.map((k) => (/{([...STAGE_A_VISIBLE, ...(dash ? Object.keys(dash.counts) : [])] as string[]).map((k) => (/" src/pages/OpsHome.tsx
npx vitest run src/pages/OpsHome.test.tsx
```
Expected: FAIL —— 第一、二条都红：`已下单` / `准备排货` / `已排货` 出现在计数区，且值是 **0**。
★ 这正是 `00e:45` 点名的错：「没有」和「还没做」长得一样。

```bash
cd web && git checkout -- src/pages/OpsHome.tsx && npx vitest run src/pages/OpsHome.test.tsx
```
Expected: PASS。

- [ ] **Step 9: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 运营首页 —— 只渲染够得着的三态，完结与归档各留一个数

够不着的名单由 scope_note 给，不在前端硬编码。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## Task 4: 计划编辑网格

**Files:**
- Create: `web/src/pages/planGridModel.ts`
- Modify（整体替换占位）: `web/src/pages/PlanGrid.tsx`
- Test: `web/src/pages/planGridModel.test.ts` `web/src/pages/PlanGrid.test.tsx`

**Interfaces:**
- Consumes: `api.getGrid` `api.getPlan` `api.putDemand` `api.putPurchase` `api.releaseClaim` `api.listSellers`（Task 2）· `<Qty>` `QtyValue`（Task 3）· `<AppShell>` `pushToast`（Task 1）· `<ErrorDetail>`（Task 2）
- Produces:
  - `buildGridModel(grid: GridResponse, sellers: Seller[]): GridModel`
  - `GridModel = { periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[]; pipeline: SkuPipelineRow[]; orphans: Orphan[] }`
  - `SkuBlock = { sid: Sid; seller_name: string; has_fba: boolean; sku: string; mskus: MskuRow[]; inventory: InventoryCell[] }`
  - `MskuRow = { seller_sku: string; sid: Sid; cells: MskuCell[] }`
  - `MskuCell = { period; system_units; system_extrapolated; expected_units; effective_units; basis }`
  - `PurchaseRow = { sku: string; cells: { period: Period; planned_units: number | null }[] }`
  - `Orphan = { kind: 'unknown_seller' | 'inventory_without_demand' | 'demand_without_inventory' | 'na_disagrees_with_seller' | 'in_transit_disagrees' | 'demand_disagrees' | 'chain_broken'; key: string }`
    ★ `chain_broken`（Task 4 评审后裁定加入）：同一 (sku, sid) 相邻两月 `onhand(N+1) ≠ closing(N)` —— `14` §1.1 恒等式 ④ 写明「必须由代码强制」，前端不守它就只能靠 fixture 碰巧成立
    ★ 同轮裁定的钩子变更（Task 7 按此取）：折叠行的期末 testid **恒为 `closing-sku`**，块上以 `data-open` 表状态；在途行 `transit-row-{sku}`；无 FBA 的块**不渲染 `sum-inventory`**；在途按 `inventory[].basis.sku_level_in_transit` 取、`sku_pipeline` 只作交叉核对
    ★ Task 5 评审后裁定：msku 级钩子一律带 `sid`（同名 msku 在两店是两个 listing）—— PlanAdd 已改 `msku-{seller_sku}-{sid}`；PlanGrid 的 `cell-{msku}-{p}` 由 **Task 7** 改为 `cell-{msku}-{sid}-{p}`（Task 7「不改 testid」规则的明示例外，连同其测试）
  - `sumUnits(values: (number | null)[]): QtyValue` · `demandAt(block, period): QtyValue` · `inventoryAt(block, period): QtyValue` · `closingOfLast(block, periods): QtyValue` · `outageCount(block): number`
  - Task 7 会在这个文件里加「提交」按钮，**不改本 Task 的任何导出名与 `data-testid`**

**这一屏的四条口径**（`02` §3.1a 可售库存 = [店铺, 货号] · 负责人原理图）

```
① 库存预估的身份是**店铺 × 货号** —— 画在**店铺·货号行**上（折叠态就能看见，与原理图一致）
② 展开后的 msku 行只有**销量预估**与**期望销量输入** —— 库存不在 msku 这一层，不许重复画
③ 货号级在途单独一行只读（铅笔灰 + 「未分摊到店铺」）—— 只展示，一件都不并进任何店铺的库存
④ 合计列：量跨月求和；★ **存量不求和，给「期末」**（`onhand` 是一条链，加起来等于把同一批货数三遍）
⑤ 断货计数挂在块头：按**折叠行的 closing** 数出几个月见底 —— 折起来也看得见
```

★ 「不可求和」这个字形在阶段 A **没有落点**（每块只有一个货号一个店铺，不存在跨货号/跨市场的合计列）。
`Qty` 里保留这个分支是为阶段 C，登记在附录 A.3 —— ★ 它现在属于「不执行的东西不会失败」的一例。

★ 由 ① ② 得出一个后果，写在这里免得被当成 bug：**折叠态没有输入框，要填数必须先展开**。
这是原理图本来的样子（「输入框…msku 级；折叠行显示 Σ 只读」），不是遗漏。

- [ ] **Step 1: 写模型层的失败测试**

```ts
// web/src/pages/planGridModel.test.ts
import { describe, expect, it } from 'vitest';
import {
  buildGridModel, closingOfLast, demandAt, inventoryAt, outageCount, sumUnits,
} from './planGridModel';
import type { ClosingReason, DemandCell, GridResponse, InventoryCell, Seller } from '../api/types';

const P = ['2026-10', '2026-11', '2026-12'];

const sellers: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
  { seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart' },
];

const d = (seller_sku: string, sid: string, period: string, eff: number | null): DemandCell => ({
  seller_sku, sid, sku: 'SKU-1', period, system_units: 100, system_extrapolated: false,
  expected_units: eff, effective_units: eff, basis: eff === null ? 'unknown' : 'human',
});
const inv = (sid: string, period: string, onhand: number | null, closing: number | null,
             closing_reason: ClosingReason = null, transit: number | null = 80,
             demand: number | null = null): InventoryCell => ({
  sku: 'SKU-1', sid, period, onhand, inbound: null, closing,
  basis: { source: 'ch', as_of: '2026-09-21', includes_plan_purchase: false,
           demand: demand ?? (onhand !== null && closing !== null ? onhand - closing : null),
           reason: 'no_seller_attribution', closing_reason,
           sku_level_in_transit: transit, sources: [] },
});

function makeGrid(): GridResponse {
  return {
    plan_id: 1, periods: [...P],
    demand: [
      d('MSKU-A', '11072', P[0]!, 180), d('MSKU-A', '11072', P[1]!, 200), d('MSKU-A', '11072', P[2]!, null),
      d('MSKU-B', '11072', P[0]!, 40), d('MSKU-B', '11072', P[1]!, null), d('MSKU-B', '11072', P[2]!, null),
      d('MSKU-W', '90001', P[0]!, 60), d('MSKU-W', '90001', P[1]!, 65), d('MSKU-W', '90001', P[2]!, 70),
    ],
    purchase: P.map((p, n) => ({ sku: 'SKU-1', period: p, planned_units: n === 0 ? 500 : null })),
    inventory: [
      inv('11072', P[0]!, 420, 200), inv('11072', P[1]!, 200, null, 'unknown_demand'),
      inv('11072', P[2]!, null, null, 'unknown_demand'),
      inv('90001', P[0]!, null, null, 'not_applicable', null),
      inv('90001', P[1]!, null, null, 'not_applicable', null),
      inv('90001', P[2]!, null, null, 'not_applicable', null),
    ],
    sku_pipeline: P.map((p, n) => ({
      sku: 'SKU-1', period: p, units: n === 0 ? 80 : null,
      sources: [], no_seller_attribution: true as const,
    })),
  };
}

describe('网格模型', () => {
  it('分块：一个店铺 · 一个货号 一块；msku 挂在块下，库存挂在块上', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.blocks.map((b) => `${b.sid}/${b.sku}`)).toEqual(['11072/SKU-1', '90001/SKU-1']);
    expect(m.blocks[0]!.mskus.map((r) => r.seller_sku)).toEqual(['MSKU-A', 'MSKU-B']);
    expect(m.blocks[0]!.inventory).toHaveLength(3);
    // ★ 库存不挂在 msku 上 —— 挂上去就会被画两遍、加两遍
    expect(Object.keys(m.blocks[0]!.mskus[0]!.cells[0]!)).not.toContain('inventory');
  });

  it('★ 未知向后传染：任何一个是 null，和就是未知；空数组也是未知，不是 0', () => {
    expect(sumUnits([180, 200, 210])).toEqual({ kind: 'num', value: 590 });
    expect(sumUnits([180, null, 210])).toEqual({ kind: 'unknown' });
    expect(sumUnits([])).toEqual({ kind: 'unknown' });
  });

  it('期望销量跨 msku 可以求和', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(demandAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 220 });
    expect(demandAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });  // MSKU-B 11 月未知
  });

  it('★ 库存一格三种形态三个字：有数 / 未知 / 不适用（都由 closing_reason 说了算）', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(inventoryAt(m.blocks[0]!, P[0]!)).toEqual({ kind: 'num', value: 200 });
    expect(inventoryAt(m.blocks[0]!, P[1]!)).toEqual({ kind: 'unknown' });
    expect(inventoryAt(m.blocks[1]!, P[0]!)).toEqual({ kind: 'na' });
    // ★ 「不适用」与「未知」都不是 0，也互不相同
    expect(inventoryAt(m.blocks[1]!, P[0]!)).not.toEqual(inventoryAt(m.blocks[0]!, P[1]!));
  });

  it('★ 合计列的存量给「期末」，不是三个月相加（onhand 是一条链，加起来数三遍）', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(closingOfLast(m.blocks[0]!, m.periods)).toEqual({ kind: 'unknown' });   // 12 月未知
    const solid = buildGridModel({ ...makeGrid(), inventory: [
      inv('11072', P[0]!, 420, 200), inv('11072', P[1]!, 200, 150), inv('11072', P[2]!, 150, 100),
      inv('90001', P[0]!, null, null, 'not_applicable', null),
      inv('90001', P[1]!, null, null, 'not_applicable', null),
      inv('90001', P[2]!, null, null, 'not_applicable', null),
    ] }, sellers);
    expect(closingOfLast(solid.blocks[0]!, solid.periods)).toEqual({ kind: 'num', value: 100 });
    expect(closingOfLast(solid.blocks[0]!, solid.periods)).not.toEqual({ kind: 'num', value: 450 });
  });

  it('★ 断货计数按折叠行的 closing 数：未知与不适用都不算断货', () => {
    const g = makeGrid();
    g.inventory[0] = inv('11072', P[0]!, 420, -5);
    const m = buildGridModel(g, sellers);
    expect(outageCount(m.blocks[0]!)).toBe(1);
    expect(outageCount(m.blocks[1]!)).toBe(0);    // 整块不适用
  });

  it('★ 在途只展示不分摊：一件都没并进任何一格库存', () => {
    const m = buildGridModel(makeGrid(), sellers);
    expect(m.pipeline[0]!.units).toBe(80);
    const closings = m.blocks.flatMap((b) => b.inventory.map((i) => i.closing));
    expect(closings).not.toContain(280);   // 200 + 80 —— 并进去就是这个数
  });

  it('★ basis.demand 与前端算的 Σ 是两个证人；不一致要点名，不许挑一个信', () => {
    const g = makeGrid();
    g.inventory[0] = { ...g.inventory[0]!, basis: { ...g.inventory[0]!.basis, demand: 999 } };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'demand_disagrees', key: 'SKU-1/11072/2026-10' });
  });

  it('★ 被丢掉的那一侧必须统计：认不出的店铺 / 对不上的库存行 / 两处在途不一致', () => {
    const g = makeGrid();
    g.demand.push(d('GHOST', '99999', P[0]!, 1));
    g.inventory.push(inv('11094', P[0]!, 5, 5));                       // 没有对应的需求
    g.inventory[0] = { ...g.inventory[0]!, basis: { ...g.inventory[0]!.basis, sku_level_in_transit: 999 } };
    // ★ 在途两处来源不一致 —— 两个真相不报，最后就会有人拿其中一个去对账
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toEqual(expect.arrayContaining([
      { kind: 'unknown_seller', key: '99999/GHOST' },
      { kind: 'inventory_without_demand', key: 'SKU-1/11094/2026-10' },
      { kind: 'in_transit_disagrees', key: 'SKU-1/2026-10' },
    ]));
    expect(m.blocks.some((b) => b.sid === '99999')).toBe(false);   // 认不出的不许静默并块
  });

  it('★ 「不适用」必须与镜像里的 has_fba 一致 —— 不一致是两个真相，要点名', () => {
    const g = makeGrid();
    g.inventory[0] = { ...g.inventory[0]!,
      basis: { ...g.inventory[0]!.basis, closing_reason: 'not_applicable' } };
    const m = buildGridModel(g, sellers);
    expect(m.orphans).toContainEqual({ kind: 'na_disagrees_with_seller', key: 'SKU-1/11072/2026-10' });
  });

  it('★ reason 恒定：它解释 inbound，不参与 closing 的判断', () => {
    const m = buildGridModel(makeGrid(), sellers);
    const all = m.blocks.flatMap((b) => b.inventory.map((i) => i.basis.reason));
    expect(new Set(all)).toEqual(new Set(['no_seller_attribution']));
  });
});
```

- [ ] **Step 2: 跑它确认红**

```bash
cd web && npx vitest run src/pages/planGridModel.test.ts
```
Expected: FAIL —— `Failed to resolve import "./planGridModel"`。

- [ ] **Step 3: 写模型层**

```ts
// web/src/pages/planGridModel.ts
import type { QtyValue } from '../components/Qty';
import type {
  DemandBasis, GridResponse, InventoryCell, Period, Seller, Sid, SkuPipelineRow,
} from '../api/types';

export interface MskuCell {
  period: Period;
  system_units: number | null;
  system_extrapolated: boolean;
  expected_units: number | null;
  effective_units: number | null;
  basis: DemandBasis;
}
export interface MskuRow { seller_sku: string; sid: Sid; cells: MskuCell[] }
/** ★ 库存挂在**块**上（店铺 × 货号），不挂在 msku 上 */
export interface SkuBlock {
  sid: Sid; seller_name: string; has_fba: boolean; sku: string;
  mskus: MskuRow[]; inventory: InventoryCell[];
}
export interface PurchaseRow { sku: string; cells: { period: Period; planned_units: number | null }[] }
export interface Orphan {
  kind: 'unknown_seller' | 'inventory_without_demand' | 'demand_without_inventory'
      | 'na_disagrees_with_seller' | 'in_transit_disagrees' | 'demand_disagrees';
  key: string;
}
export interface GridModel {
  periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[];
  pipeline: SkuPipelineRow[]; orphans: Orphan[];
}

/** ★ 任何一项未知 ⇒ 和未知。空数组也是未知：没有数不等于 0 */
export function sumUnits(values: (number | null)[]): QtyValue {
  if (values.length === 0 || values.some((v) => v === null)) return { kind: 'unknown' };
  return { kind: 'num', value: values.reduce<number>((a, b) => a + (b as number), 0) };
}

export function demandAt(block: SkuBlock, period: Period): QtyValue {
  return sumUnits(block.mskus.map((r) => r.cells.find((c) => c.period === period)?.effective_units ?? null));
}

export function inventoryAt(block: SkuBlock, period: Period): QtyValue {
  const cell = block.inventory.find((i) => i.period === period);
  if (!cell) return { kind: 'unknown' };
  // ★ 「不适用」不是 0 也不是未知：该店根本没有 FBA，这一格问的问题不成立（02 §3.1a）
  // ★ 看的是 closing_reason，不是 reason —— reason 恒为 no_seller_attribution，只解释 inbound
  if (cell.basis.closing_reason === 'not_applicable') return { kind: 'na' };
  return cell.closing === null ? { kind: 'unknown' } : { kind: 'num', value: cell.closing };
}

/** ★ 合计列的存量给**期末**，不是三个月相加 —— `onhand` 是一条链，加起来等于把同一批货数三遍 */
export function closingOfLast(block: SkuBlock, periods: Period[]): QtyValue {
  const last = periods[periods.length - 1];
  return last === undefined ? { kind: 'unknown' } : inventoryAt(block, last);
}

/** ★ 断货计数按**折叠行的 closing** 数。未知与不适用都不算断货 —— 它们是「不知道」，不是「没货」 */
export function outageCount(block: SkuBlock): number {
  return block.inventory.filter((i) =>
    i.basis.closing_reason !== 'not_applicable' && i.closing !== null && i.closing <= 0).length;
}

export function buildGridModel(grid: GridResponse, sellers: Seller[]): GridModel {
  const sellerById = new Map(sellers.map((s) => [s.seller_id, s]));
  const orphans: Orphan[] = [];
  const blocks = new Map<string, SkuBlock>();
  const usedInv = new Set<string>();
  const invKey = (sku: string, sid: Sid, p: Period) => `${sku}/${sid}/${p}`;
  const invByKey = new Map(grid.inventory.map((i) => [invKey(i.sku, i.sid, i.period), i]));

  for (const d of grid.demand) {
    const seller = sellerById.get(d.sid);
    if (!seller) {
      // ★ 认不出的店铺硬性点名，不许落进 else 再被下游过滤掉
      orphans.push({ kind: 'unknown_seller', key: `${d.sid}/${d.seller_sku}` });
      continue;
    }
    const bKey = `${d.sid}/${d.sku}`;
    let block = blocks.get(bKey);
    if (!block) {
      block = { sid: d.sid, seller_name: seller.name, has_fba: seller.has_fba, sku: d.sku, mskus: [], inventory: [] };
      blocks.set(bKey, block);
    }
    let row = block.mskus.find((r) => r.seller_sku === d.seller_sku);
    if (!row) { row = { seller_sku: d.seller_sku, sid: d.sid, cells: [] }; block.mskus.push(row); }
    row.cells.push({
      period: d.period, system_units: d.system_units, system_extrapolated: d.system_extrapolated,
      expected_units: d.expected_units, effective_units: d.effective_units, basis: d.basis,
    });
  }

  for (const block of blocks.values()) {
    for (const period of grid.periods) {
      const k = invKey(block.sku, block.sid, period);
      const cell = invByKey.get(k);
      if (!cell) { orphans.push({ kind: 'demand_without_inventory', key: k }); continue; }
      usedInv.add(k);
      // ★ 「不适用」必须与镜像里的 has_fba 一致；不一致是两个真相，要点名而不是挑一个信
      if ((cell.basis.closing_reason === 'not_applicable') !== !block.has_fba) {
        orphans.push({ kind: 'na_disagrees_with_seller', key: k });
      }
      // ★ 这一格用掉的需求有两处来源（后端的 basis.demand 与前端自己算的 Σ）；不一致要点名
      if (cell.basis.closing_reason !== 'not_applicable') {
        const mine = demandAt(block, period);
        const theirs: QtyValue = cell.basis.demand === null
          ? { kind: 'unknown' } : { kind: 'num', value: cell.basis.demand };
        if (JSON.stringify(mine) !== JSON.stringify(theirs)) {
          orphans.push({ kind: 'demand_disagrees', key: k });
        }
      }
      block.inventory.push(cell);
    }
  }

  for (const [k, cell] of invByKey) {
    if (!usedInv.has(k)) orphans.push({ kind: 'inventory_without_demand', key: `${cell.sku}/${cell.sid}/${cell.period}` });
  }

  // ★ 同一个在途数字有两处来源（basis 与 sku_pipeline）；不一致要报，不许挑一个显示
  for (const p of grid.sku_pipeline) {
    for (const cell of grid.inventory.filter((i) => i.sku === p.sku && i.period === p.period)) {
      if (cell.basis.closing_reason === 'not_applicable') continue;
      if (cell.basis.sku_level_in_transit !== p.units) {
        orphans.push({ kind: 'in_transit_disagrees', key: `${p.sku}/${p.period}` });
        break;
      }
    }
  }

  const skus = [...new Set(grid.purchase.map((p) => p.sku))];
  const purchase: PurchaseRow[] = skus.map((sku) => ({
    sku,
    cells: grid.periods.map((period) => ({
      period,
      planned_units: grid.purchase.find((p) => p.sku === sku && p.period === period)?.planned_units ?? null,
    })),
  }));

  // ★ pipeline 原样带过来，**不与 inventory 相加** —— 层级不同，加起来是每个店各多一份货
  return { periods: grid.periods, blocks: [...blocks.values()], purchase, pipeline: grid.sku_pipeline, orphans };
}
```

- [ ] **Step 4: 跑模型测试确认绿**

```bash
cd web && npx vitest run src/pages/planGridModel.test.ts
```
Expected: PASS（11 个用例）。

- [ ] **Step 5: 写网格页的失败测试**

★ 页面测试**自己造 grid** 并注入，不吃后端 fixture 的具体数字 —— 那些数字由后端的 seed 决定，
写死在断言里就会随后端改动无故转红。最后一条**专门**用真 fixture，只断言三种形态都画得出来。

```tsx
// web/src/pages/PlanGrid.test.tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ClosingReason, GridResponse, PlanSummary, Seller } from '../api/types';

const P = ['2026-10', '2026-11', '2026-12'];
const PLAN: PlanSummary = {
  plan_id: 1, title: '2026 Q4 销售计划', period_start: '2026-10-01', months: 3,
  owner_actor: 'ops.zhang', archived_at: null, state: null, state_rev: null,
};
const SELLERS: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
  { seller_id: '90001', name: 'A4Pet-WM', market: 'US', has_fba: false, platform: 'walmart' },
];

function makeGrid(): GridResponse {
  const d = (ss: string, sid: string, p: string, eff: number | null, extrap = false) => ({
    seller_sku: ss, sid, sku: 'SKU-1', period: p, system_units: 100, system_extrapolated: extrap,
    expected_units: eff, effective_units: eff,
    basis: (eff === null ? 'unknown' : 'human') as 'unknown' | 'human',
  });
  const i = (sid: string, p: string, onhand: number | null, closing: number | null,
             closing_reason: ClosingReason = null, transit: number | null = 80) => ({
    sku: 'SKU-1', sid, period: p, onhand, inbound: null as null, closing,
    basis: { source: 'ch' as const, as_of: '2026-09-21', includes_plan_purchase: false as const,
             demand: onhand !== null && closing !== null ? onhand - closing : null,
             reason: 'no_seller_attribution' as const, closing_reason,
             sku_level_in_transit: transit, sources: [] },
  });
  return {
    plan_id: 1, periods: [...P],
    demand: [d('MSKU-A', '11072', P[0]!, 180), d('MSKU-A', '11072', P[1]!, 200),
             d('MSKU-A', '11072', P[2]!, null, true),
             d('MSKU-B', '11072', P[0]!, 40), d('MSKU-B', '11072', P[1]!, null),
             d('MSKU-B', '11072', P[2]!, null),
             d('MSKU-W', '90001', P[0]!, 60), d('MSKU-W', '90001', P[1]!, 65), d('MSKU-W', '90001', P[2]!, 70)],
    purchase: P.map((p, n) => ({ sku: 'SKU-1', period: p, planned_units: n === 0 ? 500 : null })),
    inventory: [i('11072', P[0]!, 420, 200), i('11072', P[1]!, 200, -30),
                i('11072', P[2]!, -30, null, 'unknown_demand'),
                i('90001', P[0]!, null, null, 'not_applicable', null),
                i('90001', P[1]!, null, null, 'not_applicable', null),
                i('90001', P[2]!, null, null, 'not_applicable', null)],
    sku_pipeline: P.map((p, n) => ({
      sku: 'SKU-1', period: p, units: n === 0 ? 80 : null,
      sources: [], no_seller_attribution: true as const,
    })),
  };
}

beforeEach(() => { vi.resetModules(); });

async function renderGrid(grid: GridResponse = makeGrid()) {
  const { ApiError } = await import('../api/client');
  const state = { grid };
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      getPlan: async () => PLAN,
      listSellers: async () => SELLERS,
      getGrid: async () => JSON.parse(JSON.stringify(state.grid)) as GridResponse,
      putDemand: async (_p: number, ss: string, sid: string, period: string, units: number | null) => {
        const cell = state.grid.demand.find((x) => x.seller_sku === ss && x.period === period)!;
        cell.expected_units = units;
        cell.effective_units = units ?? cell.system_units;
        cell.basis = units === null ? 'system' : 'human';
        const iv = state.grid.inventory.find((x) => x.sku === cell.sku && x.sid === sid && x.period === period)!;
        const mine = state.grid.demand.filter((x) => x.sku === cell.sku && x.sid === sid && x.period === period);
        const sum = mine.some((x) => x.effective_units === null)
          ? null : mine.reduce((a, x) => a + (x.effective_units as number), 0);
        iv.basis.demand = sum;
        iv.closing = iv.onhand === null || sum === null ? null : iv.onhand - sum;
        iv.basis.closing_reason = iv.closing === null ? 'unknown_demand' : null;
        return cell;
      },
      putPurchase: async (_p: number, sku: string, period: string, units: number | null) => {
        const c = state.grid.purchase.find((x) => x.sku === sku && x.period === period)!;
        c.planned_units = units;
        return c;
      },
      releaseClaim: async () => ({ released: { seller_sku: '', sid: '' }, dropped_cells: [], stranded_purchase_cells: [] }),
      submit: async () => ({ rev: 1, lines: 1, in_flight: true, content_digest: 'x', skipped: [] }),
    },
  }));
  const { PlanGrid } = await import('./PlanGrid');
  return render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
}

const expand = async (testId: string) => {
  const block = await screen.findByTestId(testId);
  await userEvent.click(within(block).getByRole('button', { name: '展开' }));
  return block;
};

describe('计划编辑网格', () => {
  it('标题 + 月份列 + 合计列', async () => {
    await renderGrid();
    expect(await screen.findByRole('heading', { name: '2026 Q4 销售计划' })).toBeInTheDocument();
    const head = within(await screen.findByTestId('block-11072-SKU-1')).getAllByRole('columnheader');
    expect(head.map((h) => h.textContent)).toEqual(['店铺·货号', '2026-10', '2026-11', '2026-12', '合计']);
  });

  it('★ 库存预估画在店铺·货号行上，折叠态就看得见', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-10');
    expect(within(cell).getByTestId('closing')).toHaveTextContent('200');
    expect(within(cell).getByText('期初 420')).toBeInTheDocument();
    expect(within(cell).getByText('未计本计划采购')).toBeInTheDocument();
    expect(within(cell).getByTestId('sum-expected-2026-10')).toHaveTextContent('220');
    // ★ 折叠态没有输入框：填数是 msku 级的事
    expect(within(block).queryByRole('textbox')).toBeNull();
  });

  it('★ 展开后的 msku 行只有预估与输入 —— 库存不在这一层，不许重复画', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const cell = within(block).getByTestId('cell-MSKU-A-2026-10');
    expect(within(cell).getByTestId('system')).toHaveClass('i-pencil');
    expect(within(cell).getByRole('textbox')).toHaveValue('180');
    expect(within(cell).queryByTestId('closing')).toBeNull();
    // 店铺·货号行还在，库存只画那一遍
    expect(within(block).getAllByTestId(/^closing$/)).toHaveLength(0);
    expect(within(block).getAllByTestId('closing-sku')).toHaveLength(3);
  });

  it('★ 外推的预估带朱批角标 —— 外推 ≠ 预估', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    expect(within(within(block).getByTestId('cell-MSKU-A-2026-12')).getByTitle('外推')).toHaveClass('ext');
    expect(within(within(block).getByTestId('cell-MSKU-A-2026-10')).queryByTitle('外推')).toBeNull();
  });

  it('★ 空 = 未知：输入框空着，不显示 0，placeholder 也不是 "0"', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-A-2026-12')).getByRole('textbox');
    expect(input).toHaveValue('');
    expect(input).toHaveAttribute('placeholder', '');
    expect(within(block).getByTestId('sku-cell-2026-12')).toHaveTextContent('—');
  });

  it('★ 断货格：朱批左边条 + data-state=out，画在店铺·货号行上', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    const cell = within(block).getByTestId('sku-cell-2026-11');
    expect(cell).toHaveAttribute('data-state', 'out');
    expect(within(cell).getByTestId('closing')).toHaveTextContent('-30');
  });

  it('★ 无 FBA 的店铺：写「不适用」，整块里不出现 0 库存', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-90001-SKU-1');
    expect(within(block).getAllByText('不适用')).toHaveLength(3);
    expect(within(block).getByTestId('sku-cell-2026-10')).not.toHaveAttribute('data-state');
  });

  it('★ 合计列：期望跨月求和；存量给「期末」而不是三个月相加', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    expect(within(block).getByText('期末')).toBeInTheDocument();
    // 12 月 closing 未知 ⇒ 期末也是未知；★ 而 200 + (−30) 这种和一个字都不许出现
    expect(within(block).getByTestId('sum-inventory')).toHaveTextContent('—');
    expect(within(block).queryByText('170')).toBeNull();
    expect(within(block).getByTestId('sum-demand')).toHaveTextContent('—');  // 12 月未知 ⇒ 传染
  });

  it('★ 断货计数挂在块头，折起来也看得见', async () => {
    await renderGrid();
    const block = await screen.findByTestId('block-11072-SKU-1');
    expect(within(block).getByTestId('outage-11072-SKU-1')).toHaveTextContent('断货 1 个月');
    // ★ 整块不适用的不算断货
    expect(within(screen.getByTestId('block-90001-SKU-1'))
      .queryByTestId('outage-90001-SKU-1')).toBeNull();
  });

  it('填期望销量 → 保存并当场刷新店铺·货号行的库存预估', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-A-2026-10')).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, '300');
    await userEvent.tab();
    // 420 − (300 + 40)
    expect(await within(block).findByTestId('sku-cell-2026-10')).toHaveTextContent('80');
  });

  it('★ 清空一个 msku 的输入 → 整格未知（不是把它当 0 再把别的 msku 加进来）', async () => {
    await renderGrid();
    const block = await expand('block-11072-SKU-1');
    const input = within(within(block).getByTestId('cell-MSKU-B-2026-10')).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.tab();
    expect(await within(block).findByTestId('sku-cell-2026-10')).toHaveTextContent('—');
  });

  it('计划采购量是货号级，行头不带店铺；在途单独一行只读', async () => {
    await renderGrid();
    const block = await screen.findByTestId('purchase-block');
    expect(within(block).getByText('SKU-1')).toBeInTheDocument();
    expect(within(block).queryByText('A4Pet-US')).toBeNull();
    expect(within(block).getAllByRole('textbox')).toHaveLength(3);

    const transit = within(block).getByTestId('transit-row');
    expect(transit).toHaveTextContent('80');
    expect(transit).toHaveTextContent('货号级在途');
    expect(within(transit).getByText('未分摊到店铺')).toBeInTheDocument();
    expect(within(transit).queryByRole('textbox')).toBeNull();   // ★ 只读
    expect(transit).toHaveTextContent('—');                       // 11/12 月无在途 ⇒ 未知不是 0
  });

  it('★ 在途一件都没并进库存', async () => {
    await renderGrid();
    await screen.findByTestId('block-11072-SKU-1');
    expect(screen.queryByText('280')).toBeNull();   // 200 + 80
  });

  it('★ 对不上的行要点名；数据干净时丢弃区不渲染（不是渲染一个空框）', async () => {
    await renderGrid();
    await screen.findByTestId('block-11072-SKU-1');
    expect(screen.queryByTestId('orphans')).toBeNull();

    const dirty = makeGrid();
    dirty.sku_pipeline[0] = { sku: 'SKU-1', period: P[0]!, units: 999,
                              sources: [], no_seller_attribution: true };
    vi.resetModules();
    await renderGrid(dirty);
    expect(await screen.findByTestId('orphans')).toHaveTextContent('in_transit_disagrees');
  });

  it('★ 后端那份真 fixture 也画得出三种形态（不写死数字，只认形态）', async () => {
    vi.resetModules();
    const { createMockApi } = await import('../api/mock');
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
    const { PlanGrid } = await import('./PlanGrid');
    render(
      <MemoryRouter initialEntries={['/plans/1']}>
        <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
      </MemoryRouter>,
    );
    const blocks = await screen.findAllByTestId(/^block-/);
    expect(blocks.length).toBeGreaterThan(0);
    expect(screen.getAllByText('不适用').length).toBeGreaterThan(0);
    expect(screen.getAllByText('未计本计划采购').length).toBeGreaterThan(0);
    expect(screen.getAllByText('未分摊到店铺').length).toBeGreaterThan(0);
    // ★ 「不可求和」在阶段 A 没有落点：出现了就说明有人把它当默认值用了
    expect(screen.queryByText('不可求和')).toBeNull();
  });
});
```

- [ ] **Step 6: 跑它确认红**

```bash
cd web && npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: FAIL —— 16 条全红，首条报 `Unable to find an accessible element with the role "heading" and name "2026 Q4 销售计划"`。

- [ ] **Step 7: 写网格页**

```tsx
// web/src/pages/PlanGrid.tsx
import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Qty, type QtyValue } from '../components/Qty';
import { api, ApiError } from '../api';
import type { GridResponse, PlanSummary, Seller } from '../api/types';
import {
  buildGridModel, closingOfLast, demandAt, inventoryAt, outageCount, sumUnits, type SkuBlock,
} from './planGridModel';

export function PlanGrid() {
  const planId = Number(useParams().planId);
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [sellers, setSellers] = useState<Seller[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<ApiError | null>(null);

  const load = () => Promise.all([api.getPlan(planId), api.getGrid(planId), api.listSellers()])
    .then(([p, g, s]) => { setPlan(p); setGrid(g); setSellers(s); setErr(null); })
    .catch((e: ApiError) => setErr(e));

  useEffect(() => { void load(); }, [planId]);

  const model = useMemo(() => (grid ? buildGridModel(grid, sellers) : null), [grid, sellers]);

  if (err && grid === null) return <AppShell crumb="计划编辑"><ErrorDetail err={err} /></AppShell>;
  if (!grid || !model || !plan) return <AppShell crumb="计划编辑"><div className="empty" /></AppShell>;

  async function saveDemand(sellerSku: string, sid: string, period: string, raw: string) {
    // ★ 空串 = 未知 ⇒ null；不是 0
    const units = raw.trim() === '' ? null : Number(raw);
    if (units !== null && !Number.isInteger(units)) {
      pushToast({ kind: 'fail', text: `${sellerSku} ${period}：${raw} 不是整数` });
      return;
    }
    try { await api.putDemand(planId, sellerSku, sid, period, units); await load(); }
    catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
  }

  async function savePurchase(sku: string, period: string, raw: string) {
    const units = raw.trim() === '' ? null : Number(raw);
    try { await api.putPurchase(planId, sku, period, units); await load(); }
    catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).hint }); }
  }

  async function removeSku(block: SkuBlock) {
    let dropped = 0;
    for (const row of block.mskus) {
      dropped += (await api.releaseClaim(planId, row.seller_sku, row.sid)).dropped_cells.length;
    }
    await load();
    pushToast({ kind: 'ok', text: `${block.sku} 移出 ${block.mskus.length} 个 msku · 丢弃 ${dropped} 格` });
  }

  async function removeMsku(sellerSku: string, sid: string) {
    const { dropped_cells } = await api.releaseClaim(planId, sellerSku, sid);
    await load();
    pushToast({ kind: 'ok', text: `${sellerSku} 已移出 · 丢弃 ${dropped_cells.length} 格` });
  }

  const toggle = (key: string) => setExpanded((s) => {
    const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n;
  });

  return (
    <AppShell crumb="计划编辑">
      <div className="head">
        <div className="head__main">
          <h1>{plan.title}</h1>
          <div className="head__meta">
            起始月 <b>{plan.period_start.slice(0, 7)}</b> · 跨 <b>{plan.months}</b> 月 ·
            负责人 <b>{plan.owner_actor}</b>
          </div>
        </div>
        <div className="head__act">
          <a className="btn" href={`/plans/${planId}/add`}>添加货品</a>
          <button type="button" className="btn btn--ghost" onClick={() => void load()}>重置</button>
          <a className="btn" href={`/plans/${planId}/revs`}>版本</a>
        </div>
      </div>

      {model.orphans.length > 0 && (
        <div className="sec" data-testid="orphans">
          {model.orphans.map((o) => (
            <div className="dropline" key={`${o.kind}-${o.key}`}>
              <span className="k">{o.kind}</span><span>{o.key}</span>
            </div>
          ))}
        </div>
      )}

      {model.blocks.map((block) => {
        const key = `${block.sid}-${block.sku}`;
        const open = expanded.has(key);
        return (
          <div className="sheet" key={key} data-testid={`block-${key}`}>
            <div className="sheet__head">
              <button type="button" className="gr__toggle" onClick={() => toggle(key)}>
                {open ? '折叠' : '展开'}
              </button>
              <span className="sheet__sku">{block.sku}</span>
              <span className="sheet__name">{block.seller_name}</span>
              <span className="sheet__stat">
                {/* ★ 断货计数挂在块头：折起来也看得见 */}
                {outageCount(block) > 0 && (
                  <span className="chip chip--bad" data-testid={`outage-${block.sid}-${block.sku}`}>
                    断货 {outageCount(block)} 个月
                  </span>
                )}
                <button type="button" className="btn btn--sm btn--danger" onClick={() => void removeSku(block)}>
                  删除货号
                </button>
              </span>
            </div>
            <div className="sheet__scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th className="gh--row">店铺·货号</th>
                    {model.periods.map((p) => <th key={p}>{p}</th>)}
                    <th className="gh--sum">合计</th>
                  </tr>
                </thead>
                <tbody>
                  {/* ★ 店铺·货号行：库存预估的身份就是这一行（02 §3.1a），折叠态也在 */}
                  <tr>
                    <td className="gr">
                      <div className="gr__who">{block.seller_name}</div>
                      <div className="gr__code">{block.sku}</div>
                      <div className="gr__sub">{block.mskus.length} 个 msku</div>
                    </td>
                    {model.periods.map((p) => {
                      const closing = inventoryAt(block, p);
                      const cell = block.inventory.find((i) => i.period === p);
                      return (
                        <td className="cell" key={p} data-state={stateOf(closing)} data-testid={`sku-cell-${p}`}>
                          <div className="cell__stack">
                            <div className="cell__lead i-pencil">
                              预估 <Qty v={sumUnits(block.mskus.map((r) =>
                                r.cells.find((c) => c.period === p)?.system_units ?? null))} />
                            </div>
                            <div data-testid={closing.kind === 'na' ? 'closing-na' : 'closing'}>
                              <Qty v={closing} big />
                            </div>
                            <span hidden data-testid="closing-sku" />
                            {cell && cell.basis.closing_reason !== 'not_applicable' && (
                              <div className="basis">
                                <span>期初 {cell.onhand ?? '—'}</span>
                                <span>{cell.basis.as_of}</span>
                                <span className="chip chip--dim">未计本计划采购</span>
                              </div>
                            )}
                            <div className="cell__row"><span className="k">Σ 期望</span>
                              <span className="v i-ink" data-testid={`sum-expected-${p}`}>
                                <Qty v={demandAt(block, p)} /></span></div>
                          </div>
                        </td>
                      );
                    })}
                    <td className="gsum">
                      <div data-testid="sum-demand">
                        <Qty v={sumUnits(block.mskus.flatMap((r) => r.cells.map((c) => c.effective_units)))} />
                      </div>
                      {/* ★ 存量不求和，给期末：onhand 是一条链，三个月加起来等于把同一批货数三遍 */}
                      <div className="cell__row"><span className="k">期末</span>
                        <span className="v" data-testid="sum-inventory">
                          <Qty v={closingOfLast(block, model.periods)} /></span></div>
                    </td>
                  </tr>

                  {open && block.mskus.map((row) => (
                    <tr key={row.seller_sku}>
                      <td className="gr">
                        <div className="gr__code">{row.seller_sku}</div>
                        <div className="gr__sub">
                          sid {row.sid}{' '}
                          <button type="button" className="btn btn--sm btn--ghost"
                                  onClick={() => void removeMsku(row.seller_sku, row.sid)}>删除 msku</button>
                        </div>
                      </td>
                      {model.periods.map((p) => {
                        const c = row.cells.find((x) => x.period === p);
                        return (
                          <td className="cell" key={p} data-testid={`cell-${row.seller_sku}-${p}`}>
                            <div className="cell__stack">
                              <div className="cell__lead i-pencil" data-testid="system">
                                预估 {c?.system_units ?? '—'}
                                {c?.system_extrapolated && <sup className="ext" title="外推">外</sup>}
                              </div>
                              <input
                                className="g" type="text" inputMode="numeric" placeholder=""
                                aria-label={`期望销量 ${row.seller_sku} ${p}`}
                                defaultValue={c?.expected_units == null ? '' : String(c.expected_units)}
                                data-touched={c?.basis === 'human' ? '1' : undefined}
                                onBlur={(e) => void saveDemand(row.seller_sku, row.sid, p, e.target.value)}
                              />
                            </div>
                          </td>
                        );
                      })}
                      <td className="gsum"><Qty v={sumUnits(row.cells.map((c) => c.effective_units))} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      <div className="sheet" data-testid="purchase-block">
        <div className="sheet__head"><span className="sheet__sku">计划采购量</span></div>
        <div className="sheet__scroll">
          <table className="grid">
            <thead>
              <tr>
                <th className="gh--row">货号</th>
                {model.periods.map((p) => <th key={p}>{p}</th>)}
                <th className="gh--sum">合计</th>
              </tr>
            </thead>
            <tbody>
              {model.purchase.map((row) => (
                <tr key={row.sku}>
                  <td className="gr"><div className="gr__code">{row.sku}</div></td>
                  {row.cells.map((c) => (
                    <td className="cell" key={c.period}>
                      <input
                        className="g" type="text" inputMode="numeric" placeholder=""
                        aria-label={`计划采购量 ${row.sku} ${c.period}`}
                        defaultValue={c.planned_units === null ? '' : String(c.planned_units)}
                        data-touched={c.planned_units === null ? undefined : '1'}
                        onBlur={(e) => void savePurchase(row.sku, c.period, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="gsum"><Qty v={sumUnits(row.cells.map((c) => c.planned_units))} /></td>
                </tr>
              ))}
              {/* ★ 货号级在途：只读一行，一件都不分摊到店铺 */}
              {model.purchase.map((row) => (
                <tr key={`transit-${row.sku}`} data-testid="transit-row">
                  <td className="gr">
                    <div className="gr__code i-pencil">货号级在途</div>
                    <div className="gr__sub"><span className="chip chip--dim">未分摊到店铺</span></div>
                  </td>
                  {model.periods.map((p) => {
                    const t = model.pipeline.find((x) => x.sku === row.sku && x.period === p)?.units ?? null;
                    return (
                      <td className="cell" key={p}>
                        <span className="i-pencil">
                          <Qty v={t === null ? { kind: 'unknown' } : { kind: 'num', value: t }} />
                        </span>
                      </td>
                    );
                  })}
                  <td className="gsum">
                    <Qty v={sumUnits(model.periods.map((p) =>
                      model.pipeline.find((x) => x.sku === row.sku && x.period === p)?.units ?? null))} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {err && <ErrorDetail err={err} />}
    </AppShell>
  );
}

/** ★ 状态用左边条不用徽章（10 §3.2 ③）。未知与不适用都不给 data-state —— 它们不是「没货」 */
function stateOf(v: QtyValue): 'out' | 'low' | undefined {
  if (v.kind !== 'num') return undefined;
  if (v.value <= 0) return 'out';
  if (v.value < 50) return 'low';
  return undefined;
}
```

`app.css` 追加一条（`shell.css` 的角标类是 `sup.ext`，元素选择器已覆盖，这里只补可见性）：

```css
/* 外推角标：朱批小字，紧跟在预估数后面 */
sup.ext { cursor: help; }
```

- [ ] **Step 8: 跑网格测试**

```bash
cd web && npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: PASS（16 个用例）。

- [ ] **Step 9: 把三条守卫各弄失败一次**

```bash
cd web
# ① 空串落回 0
sed -i "s/const units = raw.trim() === '' ? null : Number(raw);/const units = Number(raw || 0);/" src/pages/PlanGrid.tsx
npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: FAIL —— `★ 清空一个 msku 的输入 → 整格未知`：格子给出一个数而不是 `—`。

```bash
git checkout -- src/pages/PlanGrid.tsx
# ② 「不适用」当成 0
sed -i "s/if (cell.basis.closing_reason === 'not_applicable') return { kind: 'na' };/if (cell.basis.closing_reason === 'not_applicable') return { kind: 'num', value: 0 };/" src/pages/planGridModel.ts
npx vitest run src/pages/planGridModel.test.ts src/pages/PlanGrid.test.tsx
```
Expected: FAIL —— `★ 库存一格三种形态三个字` 与 `★ 无 FBA 的店铺：写「不适用」…`：屏上出现 0。

```bash
git checkout -- src/pages/planGridModel.ts
# ③ 把在途并进库存
sed -i 's/return cell.closing === null ? { kind: .unknown. } : { kind: .num., value: cell.closing };/return cell.closing === null ? { kind: "unknown" } : { kind: "num", value: cell.closing + (cell.basis.sku_level_in_transit ?? 0) };/' src/pages/planGridModel.ts
npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: FAIL —— `★ 在途一件都没并进库存`：`screen.queryByText('280')` 不再为 null。

```bash
cd web && git checkout -- src/pages/PlanGrid.tsx src/pages/planGridModel.ts && npx vitest run src/pages
```
Expected: PASS。

- [ ] **Step 10: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 计划编辑网格 —— 库存按 [店铺, 货号] 画在折叠行，在途只读不分摊

msku 行只有预估与输入；合计列的存量给「期末」不给和；「不适用」与「未知」两个字形互不相同。
认不出的店铺、对不上的库存行、两处在途不一致全部进丢弃区点名。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## Task 5: 批量添加货号 / msku

**Files:**
- Modify（整体替换占位）: `web/src/pages/PlanAdd.tsx`
- Test: `web/src/pages/PlanAdd.test.tsx`

**Interfaces:**
- Consumes: `api.searchCatalog(q)` `api.claim(planId, target)`（Task 2）· 类型 `CatalogResult` `CatalogItem` `CatalogMsku` `ClaimHolder` `UnbuildableSeller` · `<AppShell>` `pushToast` `<ErrorDetail>`
- Produces: `PlanAdd`（路由组件，无对外导出函数）

**这一屏的四条硬规矩**

```
① 不给条件 → need_query=true，与「查不到」（need_query=false 且 matched=0）长得不一样
② 被占用的行 ★ 留在表里标出来，不过滤（selectable=false），并点名占用方（哪张计划 / 谁）
③ 认领是 msku 级 —— 逐 msku 勾，不是「勾货号带走全部」
④ 建不出格子的店铺要点名（unbuildable_sellers）
```

★ ④ 在阶段 A 后端**恒返回空数组**（渠道码不在 `seller` 镜像里，后端有一条测试盯着这件事）。
⇒ 前端照样实现渲染分支，并**用注入数据测它**：不执行的分支不会失败，等渠道码进表那天才发现画不出来。

- [ ] **Step 1: 写失败测试**

```tsx
// web/src/pages/PlanAdd.test.tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { CatalogResult } from '../api/types';

const CATALOG: CatalogResult = {
  need_query: false, truncated: false, limit: 50,
  items: [
    { sku: 'SKU-1', name: '猫爬架',
      mskus: [
        { seller_sku: 'MSKU-A', sid: '11072', seller_name: 'A4Pet-US', selectable: true, claimed_by: null },
        { seller_sku: 'MSKU-C', sid: '11094', seller_name: 'A4Pet-BS-UK', selectable: false,
          claimed_by: { plan_id: 2, title: '2026 Q3 补货计划', actor: 'ops.li' } },
        { seller_sku: 'MSKU-W', sid: '90001', seller_name: 'A4Pet-WM', selectable: true, claimed_by: null },
      ],
      unbuildable_sellers: [{ sid: '30112', reason: 'no_channel_code' }],
      claimed_by: { plan_id: 2, title: '2026 Q3 补货计划' },
      claimed_by_plans: [{ plan_id: 2, title: '2026 Q3 补货计划' }] },
    { sku: 'SKU-2', name: '逗猫棒',
      mskus: [{ seller_sku: 'MSKU-B', sid: '11072', seller_name: 'A4Pet-US', selectable: true, claimed_by: null }],
      unbuildable_sellers: [], claimed_by: null, claimed_by_plans: [] },
  ],
};

beforeEach(() => { vi.resetModules(); });

async function renderAdd(opts: { failOn?: string } = {}) {
  const { ApiError } = await import('../api/client');
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      searchCatalog: async (q: { q?: string }) =>
        !q.q ? { need_query: true, truncated: false, limit: 50, items: [] }
             // ★ 故意不带 matched：判空不许依赖这个可选字段
             : q.q === 'ZZZZ' ? { need_query: false, truncated: false, limit: 50, items: [] }
             : JSON.parse(JSON.stringify(CATALOG)) as CatalogResult,
      claim: async (_p: number, t: { seller_sku: string }) => {
        if (t.seller_sku === opts.failOn) {
          throw new ApiError(409, 'msku_already_claimed', `${t.seller_sku} 已被占用`,
            { plan_id: 2, title: '2026 Q3 补货计划', actor: 'ops.li' });
        }
        return { claimed: { seller_sku: t.seller_sku, sid: '11072', sku: 'SKU-1' } };
      },
    },
  }));
  const { PlanAdd } = await import('./PlanAdd');
  return render(
    <MemoryRouter initialEntries={['/plans/1/add']}>
      <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
    </MemoryRouter>,
  );
}

const search = async (q: string) => {
  await userEvent.type(screen.getByLabelText('搜货号'), q);
  await userEvent.click(screen.getByRole('button', { name: '搜索' }));
};

describe('批量添加', () => {
  it('★ 还没搜 ≠ 搜了没有：两种空屏的字不一样', async () => {
    await renderAdd();
    expect(await screen.findByTestId('need-query')).toBeInTheDocument();
    expect(screen.queryByTestId('no-hit')).toBeNull();

    await search('ZZZZ');
    expect(await screen.findByTestId('no-hit')).toBeInTheDocument();
    expect(screen.queryByTestId('need-query')).toBeNull();
  });

  it('★ 被占用的行留在表里标出来并点名占用方', async () => {
    await renderAdd();
    await search('SKU-1');
    const row = await screen.findByTestId('msku-MSKU-C');
    expect(row).toHaveClass('off');
    expect(within(row).getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(within(row).getByText('ops.li')).toBeInTheDocument();
    expect(within(row).getByRole('checkbox')).toBeDisabled();
  });

  it('★ 认领是 msku 级：勾一个不会把同货号的另一个也勾上', async () => {
    await renderAdd();
    await search('SKU-1');
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    expect(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 1');
  });

  it('★ 建不出格子的店铺点名（阶段 A 后端恒空 ⇒ 这里用注入数据把这条分支跑一遍）', async () => {
    await renderAdd();
    await search('SKU-1');
    const box = await screen.findByTestId('unbuildable');
    expect(box).toHaveTextContent('30112');
    expect(box).toHaveTextContent('no_channel_code');
  });

  it('添加：成功与被拒各自逐条列出，两个数都给', async () => {
    await renderAdd({ failOn: 'MSKU-W' });
    await search('SKU-1');
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: '添加' }));

    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('成功 1');
    expect(report).toHaveTextContent('被拒 1');
    // ★ 被拒的那一条要说出是被谁占着 —— 只说「被拒 1」等于没说
    expect(within(report).getByText(/2026 Q3 补货计划/)).toBeInTheDocument();
    expect(within(report).getAllByRole('listitem')).toHaveLength(2);
  });

  it('★ 一条被拒不阻断其余 —— 修一条报一条，人就开始绕（原则五）', async () => {
    await renderAdd({ failOn: 'MSKU-A' });
    await search('SKU-1');
    await userEvent.click(within(await screen.findByTestId('msku-MSKU-A')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('msku-MSKU-W')).getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    const report = await screen.findByTestId('claim-report');
    expect(report).toHaveTextContent('成功 1');
    expect(report).toHaveTextContent('被拒 1');
  });
});
```

- [ ] **Step 2: 跑它确认红**

```bash
cd web && npx vitest run src/pages/PlanAdd.test.tsx
```
Expected: FAIL —— 6 条全红，首条报 `Unable to find an element by: [data-testid="need-query"]`。

- [ ] **Step 3: 写页面**

```tsx
// web/src/pages/PlanAdd.tsx
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { api, ApiError } from '../api';
import type { CatalogMsku, CatalogResult } from '../api/types';

interface Outcome { seller_sku: string; sid: string; ok: boolean; why: string }

const EMPTY: CatalogResult = { need_query: true, matched: 0, truncated: false, limit: 0, items: [] };

export function PlanAdd() {
  const planId = Number(useParams().planId);
  const [q, setQ] = useState('');
  const [result, setResult] = useState<CatalogResult>(EMPTY);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [report, setReport] = useState<Outcome[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  const key = (m: CatalogMsku) => `${m.seller_sku}/${m.sid}`;

  async function search() {
    try { setResult(await api.searchCatalog(q.trim() === '' ? {} : { q: q.trim() })); setErr(null); }
    catch (e) { setErr(e as ApiError); }
  }

  function toggle(m: CatalogMsku) {
    setPicked((s) => { const n = new Set(s); const k = key(m); n.has(k) ? n.delete(k) : n.add(k); return n; });
  }

  async function add() {
    const targets = result.items.flatMap((s) => s.mskus).filter((m) => picked.has(key(m)));
    const out: Outcome[] = [];
    for (const m of targets) {
      try {
        await api.claim(planId, { seller_sku: m.seller_sku, sid: m.sid });
        out.push({ seller_sku: m.seller_sku, sid: m.sid, ok: true, why: '已认领' });
      } catch (e) {
        const ae = e as ApiError;
        const who = ae.fields['title'] ? `${String(ae.fields['title'])} · ${String(ae.fields['actor'] ?? '')}` : '';
        // ★ 一次列全，不是修一条报一条 —— 被拒的继续往下做，最后一起交代
        out.push({ seller_sku: m.seller_sku, sid: m.sid, ok: false, why: `${ae.error} ${who}`.trim() });
      }
    }
    setReport(out);
    setPicked(new Set());
    await search();
    const bad = out.filter((o) => !o.ok).length;
    pushToast({ kind: bad === 0 ? 'ok' : 'warn', text: `成功 ${out.length - bad} · 被拒 ${bad}` });
  }

  const unbuildable = result.items.flatMap((s) => s.unbuildable_sellers.map((u) => ({ ...u, sku: s.sku })));

  return (
    <AppShell crumb="添加货品">
      <div className="head">
        <div className="head__main"><h1>添加货品</h1></div>
        <div className="head__act">
          <span className="muted" data-testid="picked">已选 {picked.size}</span>
          <button type="button" className="btn btn--primary" disabled={picked.size === 0} onClick={() => void add()}>添加</button>
          <a className="btn btn--ghost" href={`/plans/${planId}`}>返回网格</a>
        </div>
      </div>

      <form className="bar" onSubmit={(e) => { e.preventDefault(); void search(); }}>
        <label className="bar__grp"><span className="bar__lbl">搜货号</span>
          <input className="inp inp--text" aria-label="搜货号" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <button type="submit" className="btn">搜索</button>
      </form>

      {/* ★ 两种空屏，两套字 —— 合成一种就分不清「还没搜」和「搜了没有」 */}
      {result.need_query && (
        <div className="empty" data-testid="need-query"><p className="empty__title">输入货号后搜索</p></div>
      )}
      {/* ★ 判空只用 items.length：matched 是可选字段，缺了就会把「搜了没有」显示成「还没搜」 */}
      {!result.need_query && result.items.length === 0 && (
        <div className="empty" data-testid="no-hit"><p className="empty__title">没有命中的货号</p></div>
      )}

      {result.truncated && (
        <div className="flash flash--bad">
          命中{result.matched === undefined ? '' : ` ${result.matched} 条`}，只列前 {result.limit} 条 —— 缩小搜索条件
        </div>
      )}

      {unbuildable.length > 0 && (
        <div className="sec" data-testid="unbuildable">
          {unbuildable.map((u) => (
            <div className="dropline" key={`${u.sku}-${u.sid}`}>
              <span className="k">建不出格子</span>
              <span>{u.sku}</span>
              <span>sid {u.sid}</span>
              <span className="gate__code">{u.reason}</span>
            </div>
          ))}
        </div>
      )}

      {result.items.map((item) => (
        <div className="sec" key={item.sku}>
          <div className="sec__title">
            <span>{item.sku}</span>
            <span className="muted">{item.name}</span>
            {item.claimed_by === null && item.claimed_by_plans.length > 1 && (
              // ★ 占用方不唯一时挑一个显示就是编 —— 列名单
              <span className="chip chip--warn">
                {item.claimed_by_plans.map((p) => p.title).join(' · ')}
              </span>
            )}
          </div>
          <div className="table-scroll">
            <table className="table table--dense">
              <thead><tr><th /><th>msku</th><th>店铺</th><th>sid</th><th>占用</th></tr></thead>
              <tbody>
                {item.mskus.map((m) => (
                  <tr key={key(m)} className={m.selectable ? undefined : 'off'} data-testid={`msku-${m.seller_sku}`}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`认领 ${m.seller_sku}`}
                        disabled={!m.selectable}
                        checked={picked.has(key(m))}
                        onChange={() => toggle(m)}
                      />
                    </td>
                    <td>{m.seller_sku}</td>
                    <td>{m.seller_name}</td>
                    <td>{m.sid}</td>
                    <td>
                      {m.claimed_by
                        ? <span className="i-red">{m.claimed_by.title} · <span>{m.claimed_by.actor}</span></span>
                        : <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {report && (
        <div className="sec" data-testid="claim-report">
          <div className="gate__sum">
            成功 {report.filter((r) => r.ok).length} · 被拒 {report.filter((r) => !r.ok).length}
          </div>
          <ul>
            {report.map((r) => (
              <li className={`gate__item gate__item--${r.ok ? 'pass' : 'fail'}`} key={`${r.seller_sku}/${r.sid}`}>
                <span>{r.seller_sku}</span> <span className="gate__detail">{r.why}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {err && <ErrorDetail err={err} />}
    </AppShell>
  );
}
```

- [ ] **Step 4: 跑测试确认绿**

```bash
cd web && npx vitest run src/pages/PlanAdd.test.tsx
```
Expected: PASS（6 个用例）。

- [ ] **Step 5: 把「被占用不过滤」弄失败一次**

```bash
cd web
sed -i 's/{item.mskus.map((m) => (/{item.mskus.filter((m) => m.selectable).map((m) => (/' src/pages/PlanAdd.tsx
npx vitest run src/pages/PlanAdd.test.tsx
```
Expected: FAIL —— `★ 被占用的行留在表里标出来并点名占用方`：`Unable to find an element by: [data-testid="msku-MSKU-C"]`。
★ 这正是「静默丢失」的形状：过滤掉之后屏幕上一切正常，只是那一行永远不出现。

```bash
cd web && git checkout -- src/pages/PlanAdd.tsx && npx vitest run src/pages/PlanAdd.test.tsx
```
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 批量添加 —— 逐 msku 勾，占用的行标红不过滤并点名占用方

「还没搜」与「搜了没有」两套空屏；一条被拒不阻断其余，最后一起交代。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## Task 6: 版本编辑

**Files:**
- Create: `web/src/components/Modal.tsx`
- Modify（整体替换占位）: `web/src/pages/PlanRevs.tsx`
- Test: `web/src/components/Modal.test.tsx` `web/src/pages/PlanRevs.test.tsx`

**Interfaces:**
- Consumes: `api.listRevs` `api.setCurrentRev` `api.diff` `api.cancelRev`（Task 2）· 类型 `RevList` `Rev` `PlanDiff` `DiffMoved` `DiffChanged` · `<AppShell>` `<Qty>` `pushToast` `<ErrorDetail>`
- Produces:
  - `<Modal title danger onClose>{children}</Modal>` —— ★ 只用 `.modal-backdrop > .modal` 这一套（`shell.css` 里 `dialog.modal` 与 div 版并存，React 只留一套）
  - `PlanRevs`（路由组件）

★ 本屏必须把两个标记分开显示（`06` §1.3）：**流转中**（采购/排货消费它）与**当前使用**（算需求时读它）。
合成一个标记，就再也分不清「为什么改了当前使用，采购那边还是老的」。

- [ ] **Step 1: 写 Modal 的失败测试**

```tsx
// web/src/components/Modal.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from './Modal';

describe('Modal', () => {
  it('★ 只用一套结构：.modal-backdrop > .modal，页面里不出现 <dialog>', () => {
    const { container } = render(<Modal title="撤销 rev 2" onClose={() => undefined}><p>x</p></Modal>);
    expect(container.querySelector('.modal-backdrop > .modal')).not.toBeNull();
    expect(container.querySelector('dialog')).toBeNull();
  });

  it('danger 的弹窗长得不一样（原则四）', () => {
    const { container } = render(<Modal title="撤销 rev 2" danger onClose={() => undefined}><p>x</p></Modal>);
    expect(container.querySelector('.modal__warn')).not.toBeNull();
  });

  it('Esc 关闭', async () => {
    const onClose = vi.fn();
    render(<Modal title="撤销 rev 2" onClose={onClose}><p>x</p></Modal>);
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: 跑它确认红，然后写 Modal**

```bash
cd web && npx vitest run src/components/Modal.test.tsx
```
Expected: FAIL —— `Failed to resolve import "./Modal"`。

```tsx
// web/src/components/Modal.tsx
import { useEffect, type ReactNode } from 'react';

export function Modal({
  title, danger = false, onClose, children,
}: { title: string; danger?: boolean; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal__head">{title}</div>
        {danger && <div className="modal__warn irreversible">⛔ 这一步会撤销已提交的记录</div>}
        <div className="modal__body">{children}</div>
      </div>
    </div>
  );
}
```

```bash
cd web && npx vitest run src/components/Modal.test.tsx
```
Expected: PASS（3 个用例）。

- [ ] **Step 3: 写版本页的失败测试**

```tsx
// web/src/pages/PlanRevs.test.tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { PlanDiff, RevList } from '../api/types';

const REVS: RevList = {
  revs: [
    { rev: 2, content_digest: '3b7e5d1c', is_current: true, in_flight: true,
      submitted_by: 'ops.zhang', submitted_at: '2026-09-20T10:31:00+08:00', lines: 3 },
    { rev: 1, content_digest: '8f1c2a0b', is_current: false, in_flight: false,
      submitted_by: 'ops.zhang', submitted_at: '2026-09-19T14:03:00+08:00', lines: 2 },
  ],
  in_flight_rev: 2, current_rev: 2,
};
const DIFF: PlanDiff = {
  added: [{ sku: 'SKU-1', period: '2026-11', total_units: 300 }],
  removed: [],
  changed: [{ sku: 'SKU-1', period: '2026-10',
              total_units: { from: 500, to: 600 }, demand_at_submit: { from: 160, to: 180 } }],
};

beforeEach(() => { vi.resetModules(); });

async function renderRevs() {
  const { ApiError } = await import('../api/client');
  const state: RevList = JSON.parse(JSON.stringify(REVS));
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      listRevs: async () => JSON.parse(JSON.stringify(state)) as RevList,
      setCurrentRev: async (_p: number, rev: number) => {
        state.revs.forEach((r) => { r.is_current = r.rev === rev; });
        state.current_rev = rev;          // ★ in_flight_rev 故意不动：它由采购侧决定
        return { current_rev: rev };
      },
      diff: async () => DIFF,
      cancelRev: async (_p: number, rev: number, reason: string) => {
        if (reason.trim() === '') throw new ApiError(400, 'reason_required', '撤销必须填理由', { rev });
        return { cancelled: [101, 102, 103], reason };
      },
    },
  }));
  const { PlanRevs } = await import('./PlanRevs');
  return render(
    <MemoryRouter initialEntries={['/plans/2/revs']}>
      <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
    </MemoryRouter>,
  );
}

describe('版本编辑', () => {
  it('★ 流转中与当前使用分开显示 —— 两个标记不是一回事', async () => {
    await renderRevs();
    const row2 = await screen.findByTestId('rev-2');
    expect(within(row2).getByText('流转中')).toBeInTheDocument();
    expect(within(row2).getByText('当前使用')).toBeInTheDocument();
    const row1 = screen.getByTestId('rev-1');
    expect(within(row1).queryByText('流转中')).toBeNull();
    expect(within(row1).queryByText('当前使用')).toBeNull();
  });

  it('设为当前使用：标记搬过去，而流转中留在原地', async () => {
    await renderRevs();
    await screen.findByTestId('rev-1');
    await userEvent.click(within(screen.getByTestId('rev-1')).getByRole('button', { name: '设为当前使用' }));
    expect(await within(screen.getByTestId('rev-1')).findByText('当前使用')).toBeInTheDocument();
    expect(within(screen.getByTestId('rev-2')).queryByText('当前使用')).toBeNull();
    expect(within(screen.getByTestId('rev-2')).getByText('流转中')).toBeInTheDocument();
  });

  it('★ 版本差异：新增 / 改动分在两处，改动给两个数不给增减', async () => {
    await renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.selectOptions(screen.getByLabelText('比较自'), '1');
    await userEvent.click(screen.getByRole('button', { name: '比较' }));

    const changed = within(await screen.findByTestId('diff-changed')).getByTestId('changed-SKU-1-2026-10');
    expect(changed).toHaveTextContent('500');
    expect(changed).toHaveTextContent('600');
    expect(changed).not.toHaveTextContent('+100');      // ★ 不合成增减
    const added = within(screen.getByTestId('diff-added')).getByTestId('added-SKU-1-2026-11');
    expect(added).toHaveTextContent('300');
    // ★ 三个数组都有自己的区块，空的也要说「无」，不许整块消失
    expect(screen.getByTestId('diff-removed')).toHaveTextContent('无');
  });

  it('★ 撤销理由必填：空理由拿到 400 reason_required 并点名', async () => {
    await renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.click(screen.getByRole('button', { name: '确认撤销' }));
    expect(await screen.findByText('400 reason_required')).toBeInTheDocument();
  });

  it('填了理由就撤得掉，并交代撤了哪几条记录', async () => {
    await renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.type(screen.getByLabelText('理由'), '需求口径变了');
    await userEvent.click(screen.getByRole('button', { name: '确认撤销' }));
    const report = await screen.findByTestId('cancel-report');
    expect(report).toHaveTextContent('撤销 3 条记录');
    expect(report).toHaveTextContent('需求口径变了');    // ★ 理由要回显：它被记在每条记录上
  });
});
```

- [ ] **Step 4: 跑它确认红**

```bash
cd web && npx vitest run src/pages/PlanRevs.test.tsx
```
Expected: FAIL —— 5 条全红，首条报 `Failed to resolve import "./PlanRevs"`（占位组件没有这些 testid）。

- [ ] **Step 5: 写版本页**

```tsx
// web/src/pages/PlanRevs.tsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Modal } from '../components/Modal';
import { api, ApiError } from '../api';
import type { PlanDiff, RevList } from '../api/types';

export function PlanRevs() {
  const planId = Number(useParams().planId);
  const [list, setList] = useState<RevList | null>(null);
  const [from, setFrom] = useState('');
  const [diff, setDiff] = useState<PlanDiff | null>(null);
  const [cancelling, setCancelling] = useState<number | null>(null);
  const [reason, setReason] = useState('');
  const [done, setDone] = useState<{ lines: number; reason: string } | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  const load = () => api.listRevs(planId).then(setList).catch((e: ApiError) => setErr(e));
  useEffect(() => { void load(); }, [planId]);

  if (!list) return <AppShell crumb="版本">{err ? <ErrorDetail err={err} /> : <div className="empty" />}</AppShell>;

  async function setCurrent(rev: number) {
    try { await api.setCurrentRev(planId, rev); await load(); pushToast({ kind: 'ok', text: `rev ${rev} 已设为当前使用` }); }
    catch (e) { setErr(e as ApiError); }
  }

  async function compare() {
    try { setDiff(await api.diff(planId, Number(from), list!.current_rev ?? 0)); setErr(null); }
    catch (e) { setErr(e as ApiError); }
  }

  async function doCancel() {
    if (cancelling === null) return;
    try {
      const r = await api.cancelRev(planId, cancelling, reason);
      setDone({ lines: r.cancelled.length, reason: r.reason });
      setCancelling(null);
      setReason('');
      setErr(null);
      await load();
    } catch (e) { setErr(e as ApiError); }
  }

  return (
    <AppShell crumb="版本">
      <div className="head">
        <div className="head__main"><h1>版本</h1></div>
        <div className="head__act"><a className="btn btn--ghost" href={`/plans/${planId}`}>返回网格</a></div>
      </div>

      <div className="table-scroll">
        <table className="table table--dense">
          <thead><tr><th className="r">rev</th><th>提交人</th><th>提交时间</th><th className="r">记录</th><th>digest</th><th>标记</th><th /></tr></thead>
          <tbody>
            {list.revs.map((r) => (
              <tr key={r.rev} data-testid={`rev-${r.rev}`}>
                <td className="r">{r.rev}</td>
                <td>{r.submitted_by}</td>
                <td className="muted">{r.submitted_at.slice(0, 16).replace('T', ' ')}</td>
                <td className="r">{r.lines}</td>
                <td className="gate__code">{r.content_digest}</td>
                <td>
                  {/* ★ 两个标记分开：流转中由采购/排货消费，当前使用由算需求读 */}
                  {r.rev === list.in_flight_rev && <span className="chip chip--ours">流转中</span>}{' '}
                  {r.rev === list.current_rev && <span className="chip chip--ok">当前使用</span>}
                </td>
                <td>
                  {r.rev !== list.current_rev && (
                    <button type="button" className="btn btn--sm" onClick={() => void setCurrent(r.rev)}>设为当前使用</button>
                  )}{' '}
                  <button type="button" className="btn btn--sm btn--danger" onClick={() => setCancelling(r.rev)}>撤销</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="bar">
        <label className="bar__grp"><span className="bar__lbl">比较自</span>
          <select className="inp" aria-label="比较自" value={from} onChange={(e) => setFrom(e.target.value)}>
            <option value="">选一版</option>
            {list.revs.map((r) => <option key={r.rev} value={r.rev}>rev {r.rev}</option>)}
          </select></label>
        <button type="button" className="btn" disabled={from === ''} onClick={() => void compare()}>比较</button>
      </div>

      {diff && (
        <>
          <DiffBlock title="新增" testId="diff-added" rows={diff.added.map((r) => ({
            id: `added-${r.sku}-${r.period}`, sku: r.sku, period: r.period,
            cells: [String(r.total_units)],
          }))} head={['货号', '月', '数量']} />
          <DiffBlock title="移除" testId="diff-removed" rows={diff.removed.map((r) => ({
            id: `removed-${r.sku}-${r.period}`, sku: r.sku, period: r.period,
            cells: [String(r.total_units)],
          }))} head={['货号', '月', '数量']} />
          <DiffBlock title="改动" testId="diff-changed" rows={diff.changed.map((r) => ({
            id: `changed-${r.sku}-${r.period}`, sku: r.sku, period: r.period,
            // ★ 两个数并排 + 需求两个数，不合成一个增减
            cells: [String(r.total_units.from), String(r.total_units.to),
                    String(r.demand_at_submit.from), String(r.demand_at_submit.to)],
          }))} head={['货号', '月', `rev ${diff ? from : ''} 数量`, '当前数量', '原需求', '当前需求']} />
        </>
      )}

      {done && (
        <div className="flash flash--good" data-testid="cancel-report">
          撤销 {done.lines} 条记录 · 理由 {done.reason}
        </div>
      )}

      {cancelling !== null && (
        <Modal title={`撤销 rev ${cancelling}`} danger onClose={() => setCancelling(null)}>
          <label className="field"><span className="lbl">理由</span>
            <input className="inp inp--text" aria-label="理由" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
          <div className="btn-row">
            <button type="button" className="btn btn--danger" onClick={() => void doCancel()}>确认撤销</button>
            <button type="button" className="btn btn--ghost" onClick={() => setCancelling(null)}>取消</button>
          </div>
          {err && <ErrorDetail err={err} />}
        </Modal>
      )}

      {err && cancelling === null && <ErrorDetail err={err} />}
    </AppShell>
  );
}

interface DiffRowView { id: string; sku: string; period: string; cells: string[] }

/** ★ 三个数组各自成块。空的写「无」—— 整块消失会让人以为这一类不存在 */
function DiffBlock({ title, testId, head, rows }: {
  title: string; testId: string; head: string[]; rows: DiffRowView[];
}) {
  return (
    <div className="sec" data-testid={testId}>
      <div className="sec__title"><span>{title}</span><span className="muted">{rows.length}</span></div>
      {rows.length === 0 ? <div className="todos__empty">无</div> : (
        <div className="table-scroll">
          <table className="table table--dense">
            <thead><tr>{head.map((h) => <th key={h} className="r">{h}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} data-testid={r.id}>
                  <td>{r.sku}</td>
                  <td>{r.period}</td>
                  {r.cells.map((c, i) => <td className="r num" key={i}>{c}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: 跑测试确认绿**

```bash
cd web && npx vitest run src/pages/PlanRevs.test.tsx src/components/Modal.test.tsx
```
Expected: PASS（8 个用例）。

- [ ] **Step 7: 把「两个标记分开」弄失败一次**

```bash
cd web
sed -i 's/{r.rev === list.in_flight_rev \&\& <span className="chip chip--ours">流转中<\/span>}/{r.rev === list.current_rev \&\& <span className="chip chip--ours">流转中<\/span>}/' src/pages/PlanRevs.tsx
npx vitest run src/pages/PlanRevs.test.tsx
```
Expected: FAIL —— `设为当前使用：标记搬过去，而流转中留在原地`：rev 2 上的「流转中」不见了，rev 1 上多了一个。
★ 这就是「用一个轴掩盖真相」的形状：合成之后屏幕依然自洽，只是它说的不再是事实。

```bash
cd web && git checkout -- src/pages/PlanRevs.tsx && npx vitest run src/pages/PlanRevs.test.tsx
```
Expected: PASS。

- [ ] **Step 8: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 版本编辑 —— 流转中与当前使用分开显示，撤销理由必填并回显

diff 的新增/移除/改动各自成块，空的写「无」；改动给两个数不合成增减。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## Task 7: 提交流程

**Files:**
- Modify: `web/src/pages/PlanGrid.tsx`（加提交按钮与结果面板；★ 不改 Task 4 的任何导出名与 `data-testid`）
- Test: `web/src/pages/PlanGrid.submit.test.tsx`

**Interfaces:**
- Consumes: `api.submit`（Task 2）· 类型 `SubmitResult` `SkippedCell` `SkipReason` · `<ErrorDetail>` `pushToast`
- Produces: 无新导出。屏上新增 `data-testid`：`submit-report` · `skipped-list` · `rev-in-flight` · `empty-rev`

**这一步的三条硬规矩**

```
① ★ 判据②：被跳过的格子逐条列出，一条都不许合成成「部分跳过」
② ★ 409 rev_in_flight 要点名旧版号 —— 不点名，人只能去库里查「到底是哪一版卡着」
③ ★ in_flight=false 的空版本要说出来：它铸出 0 条、且**没有占在流转位**。
   不说，人会以为提交成功了，然后奇怪为什么采购那边什么都没有
```

★ 提交成功后**不立刻跳转**：`skipped[]` 只在这一次响应里存在，跳走就永远看不到了。
面板上给「去版本」按钮，由人点。

- [ ] **Step 1: 写失败测试**

```tsx
// web/src/pages/PlanGrid.submit.test.tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { GridResponse, PlanSummary, Seller, SubmitResult } from '../api/types';

const PLAN: PlanSummary = {
  plan_id: 1, title: '2026 Q4 销售计划', period_start: '2026-10-01', months: 3,
  owner_actor: 'ops.zhang', archived_at: null, state: null, state_rev: null,
};
const SELLERS: Seller[] = [
  { seller_id: '11072', name: 'A4Pet-US', market: 'US', has_fba: true, platform: 'amazon' },
];
const GRID: GridResponse = {
  plan_id: 1, periods: ['2026-10', '2026-11', '2026-12'],
  demand: [{ seller_sku: 'MSKU-A', sid: '11072', sku: 'SKU-1', period: '2026-10',
             system_units: 100, system_extrapolated: false, expected_units: 100,
             effective_units: 100, basis: 'human' }],
  purchase: [{ sku: 'SKU-1', period: '2026-10', planned_units: 500 },
             { sku: 'SKU-1', period: '2026-11', planned_units: null },
             { sku: 'SKU-1', period: '2026-12', planned_units: null }],
  inventory: ['2026-10', '2026-11', '2026-12'].map((period, n) => ({
    sku: 'SKU-1', sid: '11072', period,
    onhand: n === 0 ? 300 : 200, inbound: null as null, closing: n === 0 ? 200 : null,
    basis: { source: 'ch' as const, as_of: '2026-09-21', includes_plan_purchase: false as const,
             demand: n === 0 ? 100 : null, reason: 'no_seller_attribution' as const,
             closing_reason: n === 0 ? null : ('unknown_demand' as const),
             sku_level_in_transit: null, sources: [] },
  })),
  sku_pipeline: [],
};

beforeEach(() => { vi.resetModules(); });

async function renderGrid(submit: () => Promise<SubmitResult>) {
  const { ApiError } = await import('../api/client');
  vi.doMock('../api', () => ({
    ApiError,
    api: {
      getPlan: async () => PLAN,
      listSellers: async () => SELLERS,
      getGrid: async () => JSON.parse(JSON.stringify(GRID)) as GridResponse,
      putDemand: async () => GRID.demand[0]!,
      putPurchase: async () => GRID.purchase[0]!,
      releaseClaim: async () => ({ released: { seller_sku: '', sid: '' }, dropped_cells: [], stranded_purchase_cells: [] }),
      submit,
    },
  }));
  const { PlanGrid } = await import('./PlanGrid');
  return render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
}

const OK: SubmitResult = {
  rev: 1, lines: 1, in_flight: true, content_digest: 'abc',
  skipped: [{ sku: 'SKU-1', period: '2026-11', reason: 'zero_purchase' },
            { sku: 'SKU-1', period: '2026-12', reason: 'zero_purchase' }],
};

describe('提交', () => {
  it('★ 逐条列出被跳过的格子，并交代两边的数', async () => {
    await renderGrid(async () => OK);
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    const report = await screen.findByTestId('submit-report');
    expect(report).toHaveTextContent('rev 1');
    // ★ 铸出几条 + 跳过几条，两个数都在屏上 —— 只报一个等于藏起另一半
    expect(report).toHaveTextContent('铸出 1 条');
    expect(report).toHaveTextContent('跳过 2 条');

    const skipped = within(report).getByTestId('skipped-list');
    expect(within(skipped).getAllByRole('listitem')).toHaveLength(2);
    expect(skipped).toHaveTextContent('zero_purchase');
    expect(skipped).toHaveTextContent('2026-11');
    expect(skipped).toHaveTextContent('2026-12');
  });

  it('提交成功不自动跳走 —— skipped[] 只有这一次机会被看见', async () => {
    await renderGrid(async () => OK);
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    await screen.findByTestId('submit-report');
    expect(screen.getByRole('link', { name: '去版本' })).toHaveAttribute('href', '/plans/1/revs');
  });

  it('★ 空版本要说出来：铸出 0 条，且没有占在流转位', async () => {
    await renderGrid(async () => ({
      rev: 1, lines: 0, in_flight: false, content_digest: 'abc',
      skipped: [{ sku: 'SKU-1', period: '2026-10', reason: 'zero_purchase' },
                { sku: 'SKU-1', period: '2026-11', reason: 'zero_purchase' },
                { sku: 'SKU-1', period: '2026-12', reason: 'no_claimed_msku' }],
    }));
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    const empty = await screen.findByTestId('empty-rev');
    expect(empty).toHaveTextContent('没有占在流转位');
    expect(screen.getByTestId('submit-report')).toHaveTextContent('铸出 0 条');
    // ★ 两种跳过理由都要出现，不许只报第一种
    const skipped = screen.getByTestId('skipped-list');
    expect(skipped).toHaveTextContent('zero_purchase');
    expect(skipped).toHaveTextContent('no_claimed_msku');
  });

  it('★ 409 rev_in_flight 的面板点名旧版号，且给的下一步是去看那一版', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(409, 'rev_in_flight', '先处理 rev 7', { in_flight_rev: 7 });
    });
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    const box = await screen.findByTestId('rev-in-flight');
    expect(box).toHaveTextContent('rev 7');
    expect(box).toHaveTextContent('409 rev_in_flight');
    // ★ 409 不是「你写错了」：下一步是去看那一版，不是改表单
    expect(within(box).getByRole('link', { name: '去看 rev 7' })).toHaveAttribute('href', '/plans/1/revs');
    expect(screen.queryByTestId('submit-report')).toBeNull();
  });

  it('★ 400 与 409 分支不同：400 落到表单错误区，不给「去看那一版」', async () => {
    const { ApiError } = await import('../api/client');
    await renderGrid(async () => {
      throw new ApiError(400, 'bad_request', '起始月必须是月初', { period_start: '2026-10-15' });
    });
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('400 bad_request')).toBeInTheDocument();
    expect(screen.queryByTestId('rev-in-flight')).toBeNull();
  });
});
```

- [ ] **Step 2: 跑它确认红**

```bash
cd web && npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: FAIL —— 5 条全红，首条报 `Unable to find an accessible element with the role "button" and name "提交"`。

- [ ] **Step 3: 在 PlanGrid 里加提交**

把 import 的类型一行改成：

```tsx
import type { GridResponse, PlanSummary, Seller, SkipReason, SubmitResult } from '../api/types';
```

在 `const [err, setErr] = useState<ApiError | null>(null);` 之后追加两个状态：

```tsx
  const [report, setReport] = useState<SubmitResult | null>(null);
  const [inFlight, setInFlight] = useState<{ rev: number; err: ApiError } | null>(null);
```

在 `async function removeMsku(...)` 之后追加：

```tsx
  async function submit() {
    setReport(null);
    setInFlight(null);
    setErr(null);
    try {
      const r = await api.submit(planId);
      setReport(r);
      // ★ 不自动跳走：skipped[] 只在这一次响应里存在
      pushToast({
        kind: r.skipped.length === 0 ? 'ok' : 'warn',
        text: `rev ${r.rev} · 铸出 ${r.lines} · 跳过 ${r.skipped.length}`,
      });
      await load();
    } catch (e) {
      const ae = e as ApiError;
      // ★ 409 是「你没写错，但现在不行」—— 点名旧版号，下一步是去看那一版
      if (ae.status === 409 && ae.error === 'rev_in_flight') {
        setInFlight({ rev: Number(ae.fields['in_flight_rev']), err: ae });
        return;
      }
      setErr(ae);
    }
  }
```

把 `head__act` 里的按钮组改成（★「提交」排在「重置」之后，与原理图一致）：

```tsx
        <div className="head__act">
          <a className="btn" href={`/plans/${planId}/add`}>添加货品</a>
          <button type="button" className="btn btn--ghost" onClick={() => void load()}>重置</button>
          <button type="button" className="btn btn--primary" onClick={() => void submit()}>提交</button>
          <a className="btn" href={`/plans/${planId}/revs`}>版本</a>
        </div>
```

在 `{model.orphans.length > 0 && (...)}` 之前插入两块面板：

```tsx
      {inFlight && (
        <div className="flash flash--bad" data-testid="rev-in-flight">
          <div>rev {inFlight.rev} 还在流转，这一版不能提交</div>
          <div className="gate__code">{inFlight.err.status} {inFlight.err.error}</div>
          <a className="btn btn--sm" href={`/plans/${planId}/revs`}>去看 rev {inFlight.rev}</a>
        </div>
      )}

      {report && (
        <div className="panel sec" data-testid="submit-report">
          <div className="panel__head">
            rev {report.rev} · 铸出 {report.lines} 条 · 跳过 {report.skipped.length} 条
          </div>
          <div className="panel__body">
            {/* ★ 空版本：说出「没有占在流转位」，否则人会以为提交成功了，
                 然后奇怪为什么采购那边什么都没有 */}
            {report.lines === 0 && (
              <div className="flash flash--bad" data-testid="empty-rev">
                这一版是空的{report.in_flight === false && '，没有占在流转位'}
              </div>
            )}
            {/* ★ 判据②：逐条列出，不静默丢 */}
            <ul data-testid="skipped-list">
              {report.skipped.map((s) => (
                <li className="dropline" key={`${s.sku}-${s.period}-${s.reason}`}>
                  <span className="k">{s.sku}</span>
                  <span>{s.period}</span>
                  <span className="gate__code">{s.reason}</span>
                  <span className="gate__detail">{SKIP_WHY[s.reason]}</span>
                </li>
              ))}
            </ul>
            <a className="btn btn--sm" href={`/plans/${planId}/revs`}>去版本</a>
          </div>
        </div>
      )}
```

在文件末尾（`stateOf` 之后）追加：

```tsx
/** S-14 的值域只有这两个。★ 这里写的是「当场发生的事」，不是功能解说 */
const SKIP_WHY: Record<SkipReason, string> = {
  zero_purchase: '计划采购量为空或 0',
  no_claimed_msku: '这个货号下没有已认领的 msku',
};
```

- [ ] **Step 4: 跑提交测试**

```bash
cd web && npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: PASS（5 个用例）。

- [ ] **Step 5: 回归 Task 4 的网格测试，确认没被改坏**

```bash
cd web && npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: PASS（13 个用例）。★ 若列头那条红，说明按钮区的改动波及了表头结构 —— 去修实现，不要改断言。

- [ ] **Step 6: 把「逐条列出」弄失败一次**

```bash
cd web
sed -i 's/跳过 {report.skipped.length} 条/跳过若干条/' src/pages/PlanGrid.tsx
npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: FAIL —— `★ 逐条列出被跳过的格子，并交代两边的数`：`expected element to have text content '跳过 2 条'`。
★「部分跳过」正是 `10` §1 原则一点名的反例：300 里跳了多少看不见。

```bash
cd web && git checkout -- src/pages/PlanGrid.tsx && npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 提交流程 —— skipped[] 逐条列出，409 点名旧版号，空版本说出未占流转位

提交成功不自动跳走：skipped[] 只在这一次响应里存在。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## Task 8: 端到端 —— 两种数据源跑出同一屏

**Files:**
- Create: `web/src/e2e/sameScreen.test.tsx`（判据⑥）
- Create: `web/src/e2e/planFlow.test.tsx`（判据①）
- Create: `web/README.md`

**Interfaces:**
- Consumes: 前七个 Task 的全部产物
- Produces: 无代码导出。产出的是**两条门禁**：
  - `sameScreen`：同一份 fixture，`mock` 与 `http`（fetch 桩）渲染出的网格 DOM 文本**逐字相同**
  - `planFlow`：加货品 → 填两种量 → 提交 → 铸出 rev，全程可复现

★ 判据⑥ 为什么必须这么测：`mock.ts` 与 `http.ts` 是**两份实现**，两份实现必然分叉。
让它们读同一份 fixture、比同一屏的文本，是唯一能让分叉**当场红**的办法。
只跑 mock 会一直绿着 —— 而那正是「不执行的东西不会失败」。

★ 这两个文件**不写死后端 fixture 里的数字**（那些由后端 seed 决定），只认形态与恒等关系。

- [ ] **Step 1: 写同屏门禁**

```tsx
// web/src/e2e/sameScreen.test.tsx
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../api/fixtures/grid-1.json';
import plansFixture from '../api/fixtures/plans.json';
import sellersFixture from '../api/fixtures/sellers.json';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

/** ★ 桩只认这三条路由，其余一律抛 —— 防止「桩把没实现的调用悄悄喂成空数组」 */
function stubFetch() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes('/grid') ? gridFixture
      : url.includes('/sellers') ? sellersFixture
      : url.includes('/plans') ? plansFixture
      : null;
    if (body === null) throw new Error(`桩没有覆盖这个调用：${url}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  });
}

async function screenText(source: 'mock' | 'api'): Promise<string> {
  vi.resetModules();
  const { ApiError } = await import('../api/client');
  if (source === 'api') {
    stubFetch();
    const { createHttpApi } = await import('../api/http');
    vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  } else {
    const { createMockApi } = await import('../api/mock');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
  }
  const { PlanGrid } = await import('../pages/PlanGrid');
  const { unmount } = render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
  const blocks = await screen.findAllByTestId(/^block-/);
  // ★ 展开全部：折叠着比，等于只比了两行字
  for (const b of blocks) await userEvent.click(within(b).getByRole('button', { name: '展开' }));
  const text = (document.body.textContent ?? '').replace(/\s+/g, ' ').trim();
  unmount();
  return text;
}

describe('判据⑥ · 两种数据源跑出同一屏', () => {
  it('mock 与 api 渲染出的整屏文本逐字相同', async () => {
    const a = await screenText('mock');
    const b = await screenText('api');
    expect(b).toBe(a);
    // ★ 顺带守住「这一屏确实有内容」—— 两边都空也会相等，那是假绿
    expect(a).toContain('未计本计划采购');
    expect(a).toContain('未分摊到店铺');      // ★ 货号级在途那一行也画出来了
    expect(a.length).toBeGreaterThan(200);
  });
});
```

- [ ] **Step 2: 跑它**

```bash
cd web && npx vitest run src/e2e/sameScreen.test.tsx
```
Expected: 第一次很可能 FAIL。两种常见分叉与处置：
- `桩没有覆盖这个调用：/v1/...` —— `http.ts` 发了 `mock.ts` 不需要的请求：补桩或对齐路径；
- 文本不等 —— 多半是 `mock.ts` 与 fixture 的字段名或顺序不一致。★ **改实现，不改断言**。

修到 PASS 为止。Expected 最终: PASS（1 个用例）。

- [ ] **Step 3: 故意让两边分叉一次，确认这条门禁真在守**

```bash
cd web
sed -i "s/const grids = new Map<number, GridResponse>(\[\[1, clone(gridFixture) as GridResponse\]\]);/const grids = new Map<number, GridResponse>([[1, { ...(clone(gridFixture) as GridResponse), periods: [(clone(gridFixture) as GridResponse).periods[0]!] }]]);/" src/api/mock.ts
npx vitest run src/e2e/sameScreen.test.tsx
```
Expected: FAIL —— 两段文本不等（mock 只剩一列月份）。

```bash
cd web && git checkout -- src/api/mock.ts && npx vitest run src/e2e/sameScreen.test.tsx
```
Expected: PASS。

- [ ] **Step 4: 写全流程门禁（判据①）**

```tsx
// web/src/e2e/planFlow.test.tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../api/fixtures/grid-1.json';
import type { GridResponse } from '../api/types';

const G = gridFixture as GridResponse;
/** ★ 不写死后端 seed 出来的名字与数字，只从 fixture 里认形态 */
const FIRST_INV = G.inventory.find((i) => i.basis.closing_reason !== 'not_applicable' && i.onhand !== null)!;
const SIBLINGS = G.demand.filter((d) =>
  d.sku === FIRST_INV.sku && d.sid === FIRST_INV.sid && d.period === FIRST_INV.period);
const EDITED = SIBLINGS[0]!;
const OTHERS = SIBLINGS.slice(1);
const FIRST_SKU = G.purchase[0]!.sku;
const LAST_PERIOD = G.periods[G.periods.length - 1]!;

beforeEach(() => { vi.resetModules(); });

async function app(entry: string) {
  const { ApiError } = await import('../api/client');
  const { createMockApi } = await import('../api/mock');
  vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
  const [{ OpsHome }, { PlanGrid }, { PlanAdd }, { PlanRevs }] = await Promise.all([
    import('../pages/OpsHome'), import('../pages/PlanGrid'),
    import('../pages/PlanAdd'), import('../pages/PlanRevs'),
  ]);
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/" element={<OpsHome />} />
        <Route path="/plans/:planId" element={<PlanGrid />} />
        <Route path="/plans/:planId/add" element={<PlanAdd />} />
        <Route path="/plans/:planId/revs" element={<PlanRevs />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('判据① · 建 → 加货品 → 填两种量 → 提交 → 铸出 rev', () => {
  it('① 建：新建后走到网格', async () => {
    await app('/');
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    await userEvent.type(screen.getByLabelText('标题'), '2027 Q1 销售计划');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(await screen.findByTestId('nav-to')).toHaveTextContent(/^\/plans\/\d+$/);
  });

  it('② 加货品：认领 msku 后可返回网格', async () => {
    await app('/plans/1/add');
    await userEvent.type(screen.getByLabelText('搜货号'), 'MSKU');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    const boxes = await screen.findAllByRole('checkbox');
    const free = boxes.find((b) => !(b as HTMLInputElement).disabled)!;
    await userEvent.click(free);
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    expect(await screen.findByTestId('claim-report')).toHaveTextContent(/成功 \d+/);
    expect(screen.getByRole('link', { name: '返回网格' })).toHaveAttribute('href', '/plans/1');
  });

  it('③④ 填两种量 → 提交 → 铸出 rev，且两种量各自生效', async () => {
    await app('/plans/1');
    await screen.findByTestId('purchase-block');
    const block = await screen.findByTestId(`block-${FIRST_INV.sid}-${FIRST_INV.sku}`);
    await userEvent.click(within(block).getByRole('button', { name: '展开' }));

    // 期望销量（msku 级输入）
    const cell = within(block).getByTestId(`cell-${EDITED.seller_sku}-${FIRST_INV.period}`);
    const demandInput = within(cell).getByRole('textbox');
    await userEvent.clear(demandInput);
    await userEvent.type(demandInput, '10');
    await userEvent.tab();

    // ★ 库存画在店铺·货号行上；期望值从 fixture 现算，不写死
    const othersUnknown = OTHERS.some((d) => d.effective_units === null);
    const expected = othersUnknown
      ? '—'
      : String(FIRST_INV.onhand! - (10 + OTHERS.reduce((a, d) => a + (d.effective_units as number), 0)));
    expect(await within(block).findByTestId(`sku-cell-${FIRST_INV.period}`)).toHaveTextContent(expected);

    // 计划采购量（货号 × 月，不带店铺）
    const purchase = within(screen.getByTestId('purchase-block'))
      .getByLabelText(`计划采购量 ${FIRST_SKU} ${LAST_PERIOD}`);
    await userEvent.clear(purchase);
    await userEvent.type(purchase, '300');
    await userEvent.tab();

    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    const report = await screen.findByTestId('submit-report');
    expect(report).toHaveTextContent('rev 1');
    // ★ 恒等式，不是写死的数：铸出 + 跳过 = 货号×月 的格子数
    const lines = Number(/铸出 (\d+) 条/.exec(report.textContent!)![1]);
    const skipped = Number(/跳过 (\d+) 条/.exec(report.textContent!)![1]);
    expect(lines + skipped).toBe(G.purchase.length);
    expect(lines).toBeGreaterThanOrEqual(1);   // ★ 刚填的 300 至少让一个月铸得出来
  });

  it('⑤ 版本页看得到流转中与当前使用', async () => {
    await app('/plans/2/revs');
    const row = await screen.findByTestId('rev-2');
    expect(within(row).getByText('当前使用')).toBeInTheDocument();
    expect(within(row).getByText('流转中')).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: 跑它**

```bash
cd web && npx vitest run src/e2e/planFlow.test.tsx
```
Expected: 初次可能 FAIL 于第三条的恒等式 —— 若红，先确认 `mock.submit` 的跳过判据是否把刚填的 300 读了进去；
这是**实现问题**，不许把断言改成一个写死的数。修到 PASS。

★ 四条用例**各自重建 mock 单例**（每条都 `vi.resetModules()` + 新 `createMockApi()`），
所以**互不依赖、顺序无关**。这一点与 `sameScreen` 相同，可以放心开 `--shuffle`。

Expected 最终: PASS（4 个用例）。

- [ ] **Step 6: 全量跑 + 构建**

```bash
cd web && npx vitest run && npx tsc -b && npm run build
```
Expected: `Test Files 16 passed` —— tokens · AppShell · fixtures · mock · http · ErrorDetail · Qty · OpsHome ·
planGridModel · PlanGrid · PlanGrid.submit · PlanAdd · Modal · PlanRevs · sameScreen · planFlow（16 个）。
其中 `fixtures` 需后端 Task 15 的产物到位；未到位时它是唯一的红。tsc 静默；`vite build` 输出 `dist/`。

★ 数字对不上就是信号：少跑了哪个文件，看不出来才是问题。

- [ ] **Step 7: 写 README（★ 只写怎么跑，不写它是什么）**

```markdown
<!-- web/README.md -->
# web

```bash
npm install
cp .env.example .env          # VITE_DATA_SOURCE=mock | api
npm run dev                   # 5173
npm run test                  # vitest
npm run build                 # → dist/，由 FastAPI StaticFiles 托管
```

| 变量 | 取值 | 说明 |
|---|---|---|
| `VITE_DATA_SOURCE` | `mock` / `api` | 非法值在启动时抛错，不等第一个请求 |
| `VITE_API_BASE` | 默认 `/v1` | 只打 `api/ui/` |
| `VITE_API_TIMEOUT_MS` | 默认 `8000` | 超时抛 `ApiError(error='timeout')`，与连不上分得开 |

`src/api/fixtures/grid-1.json` 是 `tests/fixtures/grid_response.json` 的副本，
由 `src/api/fixtures.test.ts` 逐字节盯着。后端重新固化后要一起复制过来。

门禁：`src/e2e/sameScreen.test.tsx`（判据⑥）· `src/e2e/planFlow.test.tsx`（判据①）。
```

★ 起服务由**我自己**在另一个终端执行（`CLAUDE.md` 全局铁律：服务由我来启动）。
执行本计划的 agent **不要**替我起 `npm run dev`；只读的 `vitest` / `tsc` / `vite build` 可以自己跑。

- [ ] **Step 8: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
test(web): 两条门禁 —— 判据⑥ 同屏对比、判据① 全流程

同一份 fixture 分别喂 mock 与 http 桩，展开后整屏文本逐字比对。
两个文件都不写死后端 seed 的数字，只认形态与恒等式。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## 附录 A · 与后端契约的对齐记录

> 2026-09-22 二稿：后端计划（`docs/superpowers/plans/2026-09-22-stage-a-backend.md`）已写完并带测试，
> **它是契约的权威**。本附录记的是我一稿写错、按它改过来的地方 —— 留着是为了让下一个人知道
> 「为什么前端的形状长这样」，以及**哪些还需要回头改文档**。

### A.1 一稿写错、已按后端改正的（8 处）

| # | 一稿（错） | 后端（对） | 错了的代价 |
|---|---|---|---|
| 1 | 错误形状 `{code, message, detail}`（抄自 `08` §0） | ★ `{error, hint, …点名字段**平铺在顶层**}` | 每一处错误分支都读不到字段，屏幕退化成「操作失败」 |
| 2 | 月份 `"2026-10-01"` | ★ 对外一律 `"2026-10"` | PUT 的路径全是 404 |
| 3 | 列表返回裸数组 | ★ 带包裹：`{plans}` `{sellers}` `{items}` `{revs}` | `.map` 直接炸，或者更糟：渲染成空列表 |
| 4 | `purchase[].purchase_units` · `submit.line_count` | ★ `planned_units` · `lines` | 读到 `undefined`，合计与计数全成 NaN |
| 5 | grid 带 `plan` 抬头 | ★ 只有 `plan_id`；抬头要另取 `GET /plans` | 标题永远空 |
| 6 | 漏了 `demand[].system_extrapolated` 与 `effective_units` | ★ 都有 | 外推没有角标（`14` §5 要求标记随结果走）；库存用的是生效值而界面显示人填值，两处对不上 |
| 7 | 漏了 `sku_pipeline[]` | ★ 有，货号 × 月的在途总量 `{sku, period, in_transit}` | 采购在途在屏上彻底消失 —— 「没有在途」和「没算进来」长得一样 |
| 8 | catalog 自造 `{kind:'need_query'\|'ok'}` 与 `seller_id/seller_name` | ★ `{need_query, matched, truncated, limit, items}`，msku 带 `selectable` / `claimed_by` | 搜索屏整屏渲染不出来 |

### A.1b 三稿：库存的身份（2026-09-22 第三次裁定，锚在本体上）

| 稿 | `inventory[]` 的键 | 结局 |
|---|---|---|
| 一稿 | `(seller_sku, sid)` + `on_hand/purchase_in_transit` | 我自己猜的，作废 |
| 二稿 | `(seller_sku, sid, sku)` msku × 月，带 `not_applicable` 布尔与 `opening` 链 | team-lead 撤回，作废 |
| 三稿 | `(sku, sid)`，但 `basis.reason` 一个字段兼管三种成因 | 与后端接口形状块不符，作废 |
| ★ **四稿（终版）** | **`(sku, sid)`** + `basis` 里 **`reason` 与 `closing_reason` 分开** | 现行，逐字照 `2026-09-22-stage-a-backend.md` 的 `### ★★ 接口形状` |

四处结构性变化（不是改名，是改身份）：

| # | 变化 | 为什么 |
|---|---|---|
| 1 | 库存**挂在块上**（`SkuBlock.inventory`），不再挂在 `MskuCell` 上 | 挂在 msku 上就会被画两遍、加两遍 —— 而 `02` §3.1a 说它的身份只有一个 |
| 2 | ★ **`reason` 与 `closing_reason` 拆成两个字段** | `reason` 恒为 `no_seller_attribution`（解释 `inbound` 为什么 null），`closing_reason ∈ {null, not_applicable, unknown_demand}`。一个字段兼管，就会出现「这一格既未知又恒定」而只说得出一件 |
| 3 | `inbound` 从 `0` 变成 `null`；在途走 `sku_pipeline[].units` 与 `basis.sku_level_in_transit` | `0` 是「有这一层但没有货」，`null` 是「这一层在阶段 A 根本不成立」。两者处置相反 |
| 4 | 合计列的存量从「不可求和」改成 **「期末」** | `onhand` 是一条链（接口形状块写死：其后是上月期末）⇒ 三个月相加是把同一批货数三遍；而「期末」是一个有意义的真数 |

### A.2 仍要回头改**文档**的（后端计划也列了，两边同一份）

| # | 事情 | 现状 |
|---|---|---|
| 1 | `08` §0 的错误形状 `{code,message,detail}` 与裁定的 `{error,hint,…}` 冲突 | ★ **两份只能有一份生效**，实现按裁定；`08` 待订正 |
| 2 | `08` §3 的 `another_rev_in_flight` | ★ 统一为 `rev_in_flight` |
| 3 | `08` §1.1 未写 `sku_pipeline` / `system_extrapolated` / `not_applicable` / `basis` 的字段级形状 | 实现已定，`08` 待补 |
| 4 | `POST /plans/{id}/archive` 与 `/v1/plan-lines*` | ★ 后端照 `08` 实现，**阶段 A 无界面入口**（原理图没画）。我没有自己发明入口 —— 属不属于阶段 A 的界面，待负责人定 |

### A.2b ★ 权威块与后端自己的实现/测试**不一致**的三处（我按什么处理的）

> 规则：权威块是唯一权威；但它**是简写**，列出来的字段类型不许改，**没列到的不等于不存在**。
> 凡后端的实现与测试里确实有、而权威块没写的，我按「保留 + 登记」处理，并在类型上标成可选。

| # | 不一致 | 我怎么处理 | 若处理错了会怎样 |
|---|---|---|---|
| 1 | 权威块的 `demand[]` 只有 6 个键，缺 `sku` / `system_extrapolated` / `effective_units`；后端 Task 12 的实现与断言里三个都有，且 team-lead 第 2 条明确要求渲染 `system_extrapolated` 角标 | **保留**（它们是必需的：`sku` 用来分块、`system_extrapolated` 是 `14` §5 的强制标记、`effective_units` 是库存公式的入参） | 去掉 `sku` 就分不出块；去掉角标就违反 `14` §5 |
| 2 | 权威块的 `submit` 响应是 `{rev, lines, skipped}`，后端 Task 13 的断言用 `minted` 与 `in_flight` | 字段名按权威块取 **`lines`**（team-lead 第 4 条明确裁定）；`in_flight` 标为**可选**，缺了就不渲染「没有占在流转位」那一行 | 取 `minted` 会读到 `undefined`，计数全成 NaN |
| 3 | 权威块的 `catalog` 没有 `matched`，后端实现里有 | 标为**可选**，且**判空一律用 `items.length`** | 拿可选字段判空，缺了就会把「搜了没有」显示成「还没搜」——而这正是 P11 要分开的那两件事 |

### A.3 本计划自己定的、需要登记的两条

| # | 事 | 定成什么 | 为什么不算「自己发明」 |
|---|---|---|---|
| 1 | 按钮「重置」的语义 | 丢弃未落盘的输入并重新拉 `GET /grid` | 原理图列了这个按钮但没定义它做什么；这里选了**唯一不产生副作用**的那种解释 |
| 2 | `closing` 公式里的「期望销量」取哪个 | ★ 取**生效值** `effective_units`（`basis` 记来源） | 取原始 `expected_units` 的话，「采用了系统预估」的格子会被当成未知 —— 与 S-15「提交冻结取生效值并记 basis」冲突 |
| 3 | `closing` 的**跨月口径** | ★ 已定：接口形状块的 `onhand` 注释写着「首月是在仓事实，其后是上月期末」⇒ 累计链。mock 按链实现，并有一条测试盯着「本月期初 = 上月期末」，fixture 里没有「有消耗的月份」时**硬失败** | 不再是猜的 —— 出处在权威块里 |
| 4 | 「不可求和」字形在阶段 A 无落点 | `Qty` 保留 `nosum` 分支（有单测），但**网格里一处都不用**，且有一条断言 `queryByText('不可求和') === null` 盯着 | 阶段 C 的跨货号/跨市场合计要用。★ 它现在是「不执行的东西不会失败」的一例，登记于此 |
| 5 | `demand`/`matched`/`in_flight` 等可选字段 | `SubmitResult.in_flight` 与 `CatalogResult.matched` 标为**可选**，判空一律用 `items.length` / `lines` 而不是它们 | 权威块没列这两个字段（实现里有）。依赖一个可能缺的字段去判空，缺了就会把「搜了没有」显示成「还没搜」 |

---

## 附录 B · 前端侧遗留缺口（本计划**不做**，登记备查）

| # | 缺口 | 本计划的处置 | 来源 |
|---|---|---|---|
| B-1 | 「模拟外部」抽屉在设计系统里没有任何类或 token | ★ 阶段 A 没有任何外部写接口 ⇒ **不做**。留到阶段 B 与 `.chip--ext` 一起设计 | frontend-spec ③ · ⑤ |
| B-2 | `x-actor` 下拉里的人从哪来（`scm.actor` 谁维护、有没有管理界面） | ★ 前端用 `src/shell/actors.ts` 常量，**两种数据源都读它**；屏上标「留痕可伪造」。后端会校验 actor（不在表里 → 400 `unknown_actor`），所以常量与库里的 actor 对不上时会当场报错，不会静默 | frontend-spec ⑤ 缺口 11 · Q-5 |
| B-3 | `10` §1 标题写「七条交互原则」而实际列了八条（第八条是 P15 根因） | 本计划按**八条**执行；文档的数字要改，属 `docs/` 的活 | frontend-spec ⑤ 缺口 9 |
| B-4 | 名称分叉：「期望销量」vs「计划销量」 | 界面标签一律用 **期望销量**（`02`/`14`/`16`/`00e` 口径，也是 `00e:41` 的粒度定义用词） | frontend-spec ⑤ 缺口 7 |
| B-5 | 首页「进度概览」与版本页下钻 `trace` | 阶段 A 两面承重墙都没有 ⇒ **不渲染**（不是空面板） | frontend-spec ⑤ 缺口 6 |
| B-6 | `unbuildable_sellers` 阶段 A 恒空 | 前端照样实现渲染分支，并用**注入数据**测它（Task 5）—— 不执行的分支不会失败，等渠道码进表那天才发现画不出来 | 后端计划 §接口形状 c |

---

## Self-Review

按 `superpowers:writing-plans` 的三项自检。二稿（对齐后端契约）后重跑了一遍，结果如下。

**1. 规格覆盖**

| 规格条目 | 落在 |
|---|---|
| ① 四屏 + 全局外壳 | Task 1（外壳）· 3（`ops`）· 4（`plan`）· 5（`plan-add`）· 6（`plan-rev`） |
| ② 网格每格的墨与可编辑性 | Task 4 Step 7：库存画在**店铺·货号行**（`02` §3.1a），msku 行只有预估与输入；合计列给「期末」不给和；外推角标 `sup.ext` 补上了一稿漏掉的 `14` §5 |
| ③ `shell.css` 的 token 与类名原样带走 | Task 1 Step 3~4（sha256 + 六个 token 逐个断言） |
| ④ 两种人工输入 | Global Constraints 的「不做」一节：阶段 A 无 ② 类输入 ⇒ 不做抽屉，登记 B-1 |
| ⑤ 卡开工的四条缺口 | 已被 S 组 + S-21 裁定：两表粒度（S-1/S-5）· 带预测不落库（S-10/S-19）· 不渲染够不着的态（S-20）· 库存粒度与 basis（S-21）· fixture 同源（Task 2 Step 3） |
| `10` §1 八条原则 | 一 → Task 7（铸出/跳过两个数并排）· 四 → Task 6 Modal danger · 五 → Task 2 ErrorDetail + Task 5 claim-report · 六 → Task 3 hidden-rows / Task 4 orphans + transit-row（货号级在途只读一行） / Task 5 unbuildable / Task 7 skipped · 七 → `.stale`（Task 5 品类，后端未给 `refreshed_at` ⇒ 见下方「没修」）· 八 → 「不做」一节。★ 二、三属排货域，阶段 A 无对应物 |
| 后端契约的 15 个端点 | 阶段 A 用到的 13 个全在 `SupplyChainApi` 里；`archive` 与 `plan-lines*` 无界面入口 → 附录 A.2 第 4 条 |
| 判据 ①②③⑥ | Task 8 planFlow（①）· Task 7（②）· Task 5 claim 409（③）· Task 8 sameScreen（⑥）。判据 ④⑤ 是库层的事，前端测不到 |

**2. 占位符扫描**：全文搜 `TBD` / `TODO` / `待补` / `类似 Task` / `适当` —— 命中的只有本行自己与
Task 1 那句「不是 TODO」的说明。`in_transit:` 的 5 处命中全是 `sku_level_in_transit:`，不是残留的旧字段名。
每个代码步骤都给了可直接粘贴的完整代码；Task 1 的四个页面占位明确标注「在各自 Task 里被整体替换」并给了完整占位代码。

**3. 类型一致性 + 找到并修掉的问题**

一稿（13 条）：

| # | 发现 | 改法 |
|---|---|---|
| 1 | `Modal.tsx` 归属 Task 错位一位 | 移到 Task 6，File Structure 同步改 |
| 2 | 自造后端不存在的计数字段 | 三个计数改为全部指得到出处 |
| 3 | 首页用 `?state=` 发未定义取值 | 只发 `{archived:false}`，筛选排序全在前端 |
| 4 | `has_fba=false` 返回 0 | 改「不适用」 |
| 5 | `sumUnits([])` 返回 0 | 改「未知」 |
| 6 | 提交成功直接跳版本页 | 改为停在网格出结果面板 |
| 7 | 409 code 两份写法 | 统一 `rev_in_flight` |
| 8 | 三处「未知」共用一个字形 | 拆成 `—` / `不适用` / `不可求和` |
| 9~13 | 网格测试漏展开、不适用计数 3 应为 4、折叠行 Σ 无断言落点、planFlow 漏展开与不存在的 `unmount`、测试文件数写错 | 逐条改正 |

二稿（对齐后端契约，新发现 9 条）：

| # | 发现 | 改法 |
|---|---|---|
| 14 | ★ **错误形状整个写错**：一稿按 `08` §0 的 `{code,message,detail}`，而裁定并已落测试的是 `{error,hint,…顶层点名字段}` | `ApiError` / `http.ts` 解析 / `ErrorDetail` / 全部错误断言重写。★ 这是最贵的一条：不改的话每个错误分支都读到 `undefined`，屏幕退化成「操作失败」 |
| 15 | ★ **月份格式写错**：一稿到处用 `"2026-10-01"` | 对外一律 `"YYYY-MM"`；只有建计划的 `period_start` 仍是月初日期 |
| 16 | ★ **列表响应都带包裹**，一稿当裸数组 | `listPlans` 返回 `PlanList`（含 `excluded.archived`），`listSellers` 在数据层拆包 |
| 17 | 字段名错三处：`purchase_units` / `line_count` / `catalog` 的自造判别式 | 改为 `planned_units` / `lines` / `{need_query,matched,truncated,limit,items}` |
| 18 | ★ **grid 不带计划抬头**，一稿假设它带 | 新增 `api.getPlan()`（数据层内部拉 `GET /plans` 再挑），页面不必知道 |
| 19 | ★ 漏了 `system_extrapolated` —— 而 `14` §5 明写「标记必须随结果一起返回」 | 网格渲染 `sup.ext` 角标并加断言；`.stage-a-frontend-spec.md` ② 也要求了，一稿整条漏掉 |
| 20 | ★ 漏了 `sku_pipeline[]` | 模型层原样带过来、**不并入库存**，并有一条断言盯着「并进去会得到的那个数不许出现」；总量放折叠区（附录 A.3 第 2 条） |
| 21 | ★ 一稿的页面测试**写死了 fixture 的数字**，而 fixture 现在由后端 seed 生成 | 页面测试改为自造 grid 注入；只留一条用真 fixture、**只认形态不认数字**；planFlow 同样改成恒等式断言 |
| 22 | 二稿把库存改到 msku 级后，折叠行只剩「不可求和」，**断货信号在折叠态消失了** | 当时补了 `outageCount()`；三稿库存回到店铺·货号行后这个补丁**不再需要**，已连同 `blockInventory` / `closingOf` 一起删掉 —— ★ 留着就是一段永不执行的代码 |

三稿（库存身份回到 `(sku, sid)`，新发现 6 条）：

| # | 发现 | 改法 |
|---|---|---|
| 23 | ★ **库存挂错了层**：二稿把它挂在 `MskuCell.inventory` 上，于是同一格库存会被画两遍、跨 msku 还会被加一遍 | 移到 `SkuBlock.inventory`；模型测试里加一条 `Object.keys(MskuCell)` **不含** `inventory` 的断言 —— 挂回去就红 |
| 24 | ★ `inbound` 从 `0` 改成 `null` 不是改名 | `0` = 「有这一层但没有货」，`null` = 「这一层在阶段 A 根本不成立」。mock 测试改成断言 `inbound === null`，并加一条「在途没有并进任何一格」的反向断言 |
| 25 | ★ 「不适用」从布尔降成 `basis.reason` 的一个取值 | `inventoryAt()` 改按 `reason` 判；fixtures 的形态断言从 `not_applicable === true` 改成 `reason === 'not_applicable'`，并要求 **null 成因至少两种**（只有一种等于这条断言没跑） |
| 26 | ★ 同一个在途数字现在有**两处来源**（`basis.sku_level_in_transit` 与 `sku_pipeline[].in_transit`） | 新增 orphan `in_transit_disagrees`：两处不一致就点名，不许挑一个显示。★ 两个真相不报，最后就会有人拿其中一个去对账 |
| 27 | ★ `closing` 的**跨月口径**在裁定文字里没有月份下标 | 不自己定：Task 2 新增一条测试**从 fixture 反推规则**再断言 mock 一致；fixture 分不开两种规则时**硬失败**（证人不在场要报错，不是默认通过）——附录 A.3 第 3 条 |
| 28 | 折叠态**没有输入框**（输入是 msku 级的），一眼看去像功能缺失 | 不是缺失，是原理图本来的样子。写进 Task 4 的口径表并加断言 `折叠态 queryByRole('textbox') === null`，免得后来有人「顺手补上」而把人填粒度悄悄降一层 |

四稿（逐字对照后端权威块 `### ★★ 接口形状`，新发现 6 条）：

| # | 发现 | 改法 |
|---|---|---|
| 29 | ★★ **`reason` 与 `closing_reason` 是两个字段，我三稿把它们合成了一个** | 全线拆开：`inventoryAt()` / orphan 判定 / mock recompute / 三个测试文件都改看 `closing_reason`；并加两条断言盯住 `reason` **恒定**。★ 合着的时候「这一格既未知又恒定」只说得出一件 —— 而这正是后端计划 b 条点名要防的 |
| 30 | ★ 合计列的存量三稿写「不可求和」，而权威块的 `onhand` 注释写死了「其后是**上月期末**」⇒ 它是一条链 | 改成 **「期末」**（最后一个月的 `closing`），并加断言「200 + (−30) 这种和一个字都不许出现」。★ 「不可求和」在阶段 A 从此没有落点，登记在附录 A.3 第 4 条 |
| 31 | ★ `basis.demand` 是后端算的那份 Σ，与前端自己算的是**两个证人** | 新增 orphan `demand_disagrees`：不一致就点名。★ 两个真相不报，最后就会有人拿其中一个去对账 |
| 32 | `sku_pipeline[]` 的字段是 `units` 不是我三稿写的 `in_transit`，且带 `sources[]` / `no_seller_attribution: true` | 全部改正；`in_transit_disagrees` 的比对对象随之改成 `units` |
| 33 | ★ 权威块与后端自己的实现/测试**有三处不一致**（`demand[]` 缺三个键 · `submit` 的 `minted`/`lines` · `catalog` 的 `matched`） | 不自己挑：`lines` 按 team-lead 裁定取；缺的字段**保留并标可选**；★ 判空一律不依赖可选字段。三条全部登记进新增的附录 A.2b |
| 34 | `outageCount()` 三稿被我删了，而 team-lead 要求保留 | 按「折叠行的 `closing`」重写，挂到块头显示「断货 N 个月」—— 折起来也看得见，且未知/不适用都不算断货 |

★ 三条**没修**的，记在这里而不是悄悄处理：

1. **`.stale` 品类陈旧标注无处可挂** —— 后端的 `catalog` item 没有 `category` / `refreshed_at`（`08` §1.4 的 `/v1/categories` 才有）。
   原则七在阶段 A 的这一屏**落不了地**，我没有自己发明字段。→ 应登记进 `00`。
2. **`archive` 与 `plan-lines*` 无界面入口** —— 后端照 `08` 实现了，原理图没画。不自己发明入口（附录 A.2 第 4 条）。
3. **`unbuildable_sellers` 阶段 A 恒空** —— 渲染分支靠注入数据测（B-6），但它在生产里**一次都不会执行**。
   这是「不执行的东西不会失败」的一个已知实例，后端那边有一条盯着 `seller` 表列的测试兜底。
4. **`basis.reason` 恒为 `no_seller_attribution`，屏上没有任何地方渲染它。**
   它解释的是 `inbound` 为什么是 null，而 `inbound` 这一层在阶段 A 根本不画。
   ★ 不渲染是刻意的：给一个恒定值造一个屏幕形态，等于给屏幕加一句永远为真的话。
   但它必须**被断言盯住**（`fixtures.test.ts` 与模型测试各一条断言它恒定）——
   ⚠️ 阶段 B 接上采购在途、这个字段开始变化时，那两条断言会转红，逼人回来补渲染。
   这是「恒定的东西也要有人守」与「不给恒定值造 UI」之间的取法。
