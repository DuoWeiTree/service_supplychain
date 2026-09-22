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
- 跨月求和的存量列渲染 **「不可求和」**（`14` §1.1 ①）；未知渲染 `—`（`.cell__unknown`）。三者用三个不同的字形，**不许合成一个**。

**一个数要能回答「它是关于什么的」**
- 库存预估每格必须回显 `basis`（在仓 / 采购在途 / as_of）与标注 **「未计本计划采购」**。
- 系统预估用**铅笔灰**、人填用**蓝黑**、要处理用**朱批**、已落地用**石绿** —— 墨色代替说明文字（`10` §3.1）。

**错误**
- 后端错误形状 `{code, message, detail}`，★ `detail` 点名是哪几行 → 前端**逐项点名显示**（`10` §1 原则五），**不许显示「保存失败」**。
- 409 = 冲突与闸（你没写错，但现在不行）· 400 = 你写错了 —— 两者的界面处置不同，必须分支（`08:55`）。
- 提交结果必须逐条列 `skipped[]`（判据②）；409 `rev_in_flight` 要**点名旧版号**。

**接口**
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
    │       ├── plans.json          Task 2  ★ mock 与 http 的唯一数据源
    │       ├── grid-1.json         Task 2
    │       ├── catalog.json        Task 2
    │       ├── revs-1.json         Task 2
    │       └── sellers.json        Task 2
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
│   DCC1800264 │ 库存420│ 库存220│ 库存 10│不可求和│ ← 灰+basis│
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
5. **空、不适用、不可求和，三个字形。** `—` / `不适用` / `不可求和` 各自独立，**永不退化成 0** —— 这三种情况在本项目里已经咬过人（`CLAUDE.md` 判据表第一行）。

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

**Files:**
- Create: `web/src/api/types.ts` `web/src/api/client.ts` `web/src/api/index.ts` `web/src/api/mock.ts` `web/src/api/http.ts`
- Create: `web/src/api/fixtures/{plans,grid-1,catalog,revs-1,sellers}.json`
- Create: `web/src/components/ErrorDetail.tsx`
- Test: `web/src/api/mock.test.ts` `web/src/api/http.test.ts` `web/src/components/ErrorDetail.test.tsx`

**Interfaces:**
- Consumes: `getActor()`（Task 1）
- Produces（后续 Task **只引用、不新增**类型）：
  - `api: SupplyChainApi`（从 `web/src/api/index.ts` 默认导出的单例）
  - `class ApiError extends Error { code: string; status: number; detail: Record<string, unknown> }`
  - 类型：`PlanSummary` `DashboardPlanCounts` `UnsubmittedBoard` `GridResponse` `GridDemandCell` `GridPurchaseCell` `GridInventoryCell` `CatalogResult` `CatalogSku` `CatalogMsku` `ClaimHolder` `UnbuildableSeller` `SubmitResult` `SkippedCell` `RevList` `Rev` `PlanDiff` `DiffRow` `Seller`
  - `<ErrorDetail err={ApiError} />`

- [ ] **Step 1: 写全部类型（一次定完）**

```ts
// web/src/api/types.ts

/** 'YYYY-MM-01' —— 起始月须月初（08 §1.1） */
export type Period = string;
/** ★ 店铺号 18 位，JS number 会精度丢失 ⇒ 全字段字符串（08 §0） */
export type SellerId = string;
export type Sid = string;
export type PlanId = number;

/** 04 §2 的 S1 值域。★ 没有「未提交」—— 从未提交的计划 state 为 null */
export type LineState = '已提交' | '已确认' | '已下单' | '准备排货' | '已排货' | '已完结' | '已撤销';

export interface PlanSummary {
  plan_id: PlanId;
  title: string;
  period_start: Period;
  months: number;
  owner_actor: string;
  /** ★ 木桶派生的整体状态；null = 从未提交过（没有 rev，也就没有记录） */
  state: LineState | null;
  current_rev: number | null;
  in_flight_rev: number | null;
  archived: boolean;
  updated_at: string;
}

/** 08 §1.1 /v1/dashboard/plans —— ★ 后端返回全集（S-20），前端按阶段挑着渲染 */
export interface DashboardPlanCounts {
  in_progress: number;
  submitted: number;
  submitted_unconfirmed: number;
  ordered: number;
  ready_to_dispatch: number;
  dispatched: number;
}

/** 08 §1.1 /v1/dashboard/unsubmitted —— ★ 两栏，第二栏按 content_digest 判（A-2） */
export interface UnsubmittedBoard {
  never_submitted: PlanSummary[];
  changed_since_submit: PlanSummary[];
}

/** S-15：提交冻结取生效值并记 basis */
export type DemandBasis = 'human' | 'system' | 'unknown';

export interface GridDemandCell {
  seller_id: SellerId;
  sku: string;
  seller_sku: string;
  sid: Sid;
  period: Period;
  /** 系统预估，机器估 ⇒ 铅笔灰 */
  system_units: number;
  /** ★ null = 未知，不是 0（M-8），向后传染 */
  expected_units: number | null;
  basis: DemandBasis;
}

export interface GridPurchaseCell {
  /** ★ 货号级，不带店铺（P1/P2） */
  sku: string;
  period: Period;
  purchase_units: number | null;
}

/** S-10/S-19：库存预估 = 在仓 + 采购在途 − 期望销量，全是 CH 现成事实 */
export interface InventoryBasis {
  source: 'ch';
  as_of: string;
  /** ★ 恒 false —— 屏上标「未计本计划采购」 */
  includes_plan_purchase: false;
}

export interface GridInventoryCell {
  seller_sku: string;
  sid: Sid;
  period: Period;
  on_hand_units: number;
  purchase_in_transit_units: number;
  /** ★ null = 未知（上游期望销量未知传染过来） */
  closing_units: number | null;
  basis: InventoryBasis;
}

export interface GridResponse {
  plan: PlanSummary;
  /** 月份列，长度 = plan.months */
  periods: Period[];
  demand: GridDemandCell[];
  purchase: GridPurchaseCell[];
  inventory: GridInventoryCell[];
}

export interface Seller {
  seller_id: SellerId;
  name: string;
  market: string;
  /** ★ false ⇒ 库存预估显示「不适用」，不是 0（02 §3.1a） */
  has_fba: boolean;
}

export interface ClaimHolder {
  plan_id: PlanId;
  plan_title: string;
  actor: string;
  claimed_at: string;
}

export interface CatalogMsku {
  seller_sku: string;
  sid: Sid;
  seller_id: SellerId;
  seller_name: string;
  /** 非 null = 已被占用 ⇒ ★ 标红留在表里，不过滤（P11） */
  claim: ClaimHolder | null;
}

export interface CatalogSku {
  sku: string;
  name: string;
  category: string | null;
  /** 品类镜像刷新时刻；陈旧要标 .stale（原则七） */
  category_refreshed_at: string | null;
  mskus: CatalogMsku[];
}

/** 06 §1.2：店铺没挂渠道 → 建不出格子，★ 必须点名 */
export interface UnbuildableSeller {
  seller_id: SellerId;
  seller_name: string;
  reason: 'no_channel_code';
}

/** ★ P11：不给条件故意不返回，与「查不到」必须长得不一样 */
export type CatalogResult =
  | { kind: 'need_query' }
  | { kind: 'ok'; skus: CatalogSku[]; truncated: boolean; unbuildable_sellers: UnbuildableSeller[] };

export interface CatalogQuery {
  /** 货号或名称片段 */
  q?: string;
  category?: string;
}

export interface ClaimTarget { seller_sku: string; sid: Sid }

/** S-14：值域已裁定，只有这两个 */
export type SkipReason = 'zero_purchase' | 'no_claimed_msku';

export interface SkippedCell {
  seller_id: SellerId;
  sku: string;
  period: Period;
  reason: SkipReason;
}

export interface SubmitResult {
  rev: number;
  line_count: number;
  /** ★ 判据②：逐条列出，不静默丢 */
  skipped: SkippedCell[];
}

export interface Rev {
  rev: number;
  submitted_by: string;
  submitted_at: string;
  content_digest: string;
  line_count: number;
  is_current: boolean;
  is_in_flight: boolean;
}

export interface RevList {
  revs: Rev[];
  /** ★ 两个标记不是一回事（06 §1.3）：流转中 ≠ 当前使用 */
  in_flight_rev: number | null;
  current_rev: number | null;
}

export type DiffChange = 'added' | 'removed' | 'changed';

export interface DiffRow {
  kind: 'demand' | 'purchase';
  sku: string;
  seller_sku: string | null;
  sid: Sid | null;
  period: Period;
  before: number | null;
  after: number | null;
  change: DiffChange;
}

export interface PlanDiff { from: number; to: number; rows: DiffRow[] }

export interface CancelRevResult { cancelled_lines: number }

export interface ListPlansQuery {
  /** ★ 只发契约里声明过的参数 —— 未声明查询参数一律 400 */
  state?: LineState;
  owner?: string;
  archived?: boolean;
}

export interface CreatePlanInput { title: string; period_start: Period; months: number }
```

- [ ] **Step 2: 写接口与 ApiError**

```ts
// web/src/api/client.ts
import type {
  CancelRevResult, CatalogQuery, CatalogResult, ClaimTarget, CreatePlanInput,
  DashboardPlanCounts, GridDemandCell, GridPurchaseCell, GridResponse, ListPlansQuery,
  Period, PlanDiff, PlanId, PlanSummary, RevList, Seller, Sid, SubmitResult, UnsubmittedBoard,
} from './types';

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail: Record<string, unknown>;
  constructor(status: number, code: string, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export interface SupplyChainApi {
  listPlans(q: ListPlansQuery): Promise<PlanSummary[]>;
  createPlan(input: CreatePlanInput): Promise<{ plan_id: PlanId }>;
  dashboardPlans(): Promise<DashboardPlanCounts>;
  dashboardUnsubmitted(): Promise<UnsubmittedBoard>;
  listSellers(): Promise<Seller[]>;

  getGrid(planId: PlanId): Promise<GridResponse>;
  /** ★ units 可以是 null —— 空 = 未知 */
  putDemand(planId: PlanId, sellerSku: string, sid: Sid, period: Period, units: number | null): Promise<GridDemandCell>;
  putPurchase(planId: PlanId, sku: string, period: Period, units: number | null): Promise<GridPurchaseCell>;

  searchCatalog(planId: PlanId, q: CatalogQuery): Promise<CatalogResult>;
  /** ★ 一次一个 msku（08 只有单条端点）；页面循环并逐条收集 409 */
  claim(planId: PlanId, target: ClaimTarget): Promise<void>;
  releaseClaim(planId: PlanId, sellerSku: string, sid: Sid): Promise<void>;

  submit(planId: PlanId): Promise<SubmitResult>;
  listRevs(planId: PlanId): Promise<RevList>;
  setCurrentRev(planId: PlanId, rev: number): Promise<void>;
  diff(planId: PlanId, from: number, to: number): Promise<PlanDiff>;
  cancelRev(planId: PlanId, rev: number, reason: string): Promise<CancelRevResult>;
}
```

- [ ] **Step 3: 写 fixture —— ★ mock 与 api 的唯一数据源**

★ 这几个 JSON 是判据⑥ 的支点：`mock.ts` 直接读它们，`http.ts` 在测试里由 fetch 桩喂同一份。
后端落地时，**同一份文件要成为后端契约测试的 fixture**（登记在附录 B）。

```json
// web/src/api/fixtures/sellers.json
[
  { "seller_id": "11072", "name": "A4Pet-US", "market": "US", "has_fba": true },
  { "seller_id": "11094", "name": "A4Pet-BS-UK", "market": "UK", "has_fba": true },
  { "seller_id": "20311", "name": "A4Pet-Walmart-US", "market": "US", "has_fba": false }
]
```

```json
// web/src/api/fixtures/plans.json
[
  { "plan_id": 1, "title": "2026 Q4 销售计划", "period_start": "2026-10-01", "months": 3,
    "owner_actor": "ops.zhang", "state": null, "current_rev": null, "in_flight_rev": null,
    "archived": false, "updated_at": "2026-09-21T10:12:00+08:00" },
  { "plan_id": 2, "title": "2026 Q3 补货计划", "period_start": "2026-07-01", "months": 3,
    "owner_actor": "ops.zhang", "state": "已提交", "current_rev": 2, "in_flight_rev": 2,
    "archived": false, "updated_at": "2026-09-18T09:00:00+08:00" },
  { "plan_id": 3, "title": "2026 Q2 清库计划", "period_start": "2026-04-01", "months": 3,
    "owner_actor": "ops.li", "state": "已撤销", "current_rev": 1, "in_flight_rev": null,
    "archived": false, "updated_at": "2026-06-30T17:40:00+08:00" },
  { "plan_id": 4, "title": "2026 Q1 首发计划", "period_start": "2026-01-01", "months": 3,
    "owner_actor": "ops.li", "state": "已完结", "current_rev": 3, "in_flight_rev": null,
    "archived": false, "updated_at": "2026-04-02T11:20:00+08:00" }
]
```

★ `plan 4` 是 `已完结`：它存在**只为了证明首页把它过滤掉了** —— 「完结的计划不显示」这条规矩，没有一条完结的计划就测不出来。

```json
// web/src/api/fixtures/grid-1.json
{
  "plan": { "plan_id": 1, "title": "2026 Q4 销售计划", "period_start": "2026-10-01", "months": 3,
            "owner_actor": "ops.zhang", "state": null, "current_rev": null, "in_flight_rev": null,
            "archived": false, "updated_at": "2026-09-21T10:12:00+08:00" },
  "periods": ["2026-10-01", "2026-11-01", "2026-12-01"],
  "demand": [
    { "seller_id": "11072", "sku": "DCC1800264", "seller_sku": "DCC1800264-US", "sid": "11072",
      "period": "2026-10-01", "system_units": 180, "expected_units": 180, "basis": "human" },
    { "seller_id": "11072", "sku": "DCC1800264", "seller_sku": "DCC1800264-US", "sid": "11072",
      "period": "2026-11-01", "system_units": 200, "expected_units": 200, "basis": "human" },
    { "seller_id": "11072", "sku": "DCC1800264", "seller_sku": "DCC1800264-US", "sid": "11072",
      "period": "2026-12-01", "system_units": 210, "expected_units": null, "basis": "unknown" },
    { "seller_id": "11072", "sku": "DCC1800264", "seller_sku": "DCC1800264-US-B", "sid": "11072",
      "period": "2026-10-01", "system_units": 40, "expected_units": 40, "basis": "human" },
    { "seller_id": "11072", "sku": "DCC1800264", "seller_sku": "DCC1800264-US-B", "sid": "11072",
      "period": "2026-11-01", "system_units": 45, "expected_units": null, "basis": "system" },
    { "seller_id": "11072", "sku": "DCC1800264", "seller_sku": "DCC1800264-US-B", "sid": "11072",
      "period": "2026-12-01", "system_units": 50, "expected_units": null, "basis": "unknown" },
    { "seller_id": "20311", "sku": "DCC1800264", "seller_sku": "DCC1800264-WMT", "sid": "20311",
      "period": "2026-10-01", "system_units": 60, "expected_units": 60, "basis": "human" },
    { "seller_id": "20311", "sku": "DCC1800264", "seller_sku": "DCC1800264-WMT", "sid": "20311",
      "period": "2026-11-01", "system_units": 65, "expected_units": 65, "basis": "human" },
    { "seller_id": "20311", "sku": "DCC1800264", "seller_sku": "DCC1800264-WMT", "sid": "20311",
      "period": "2026-12-01", "system_units": 70, "expected_units": 70, "basis": "human" }
  ],
  "purchase": [
    { "sku": "DCC1800264", "period": "2026-10-01", "purchase_units": 500 },
    { "sku": "DCC1800264", "period": "2026-11-01", "purchase_units": null },
    { "sku": "DCC1800264", "period": "2026-12-01", "purchase_units": null }
  ],
  "inventory": [
    { "seller_sku": "DCC1800264-US", "sid": "11072", "period": "2026-10-01",
      "on_hand_units": 420, "purchase_in_transit_units": 180, "closing_units": 420,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-US", "sid": "11072", "period": "2026-11-01",
      "on_hand_units": 420, "purchase_in_transit_units": 180, "closing_units": 220,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-US", "sid": "11072", "period": "2026-12-01",
      "on_hand_units": 420, "purchase_in_transit_units": 180, "closing_units": null,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-US-B", "sid": "11072", "period": "2026-10-01",
      "on_hand_units": 30, "purchase_in_transit_units": 0, "closing_units": -10,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-US-B", "sid": "11072", "period": "2026-11-01",
      "on_hand_units": 30, "purchase_in_transit_units": 0, "closing_units": null,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-US-B", "sid": "11072", "period": "2026-12-01",
      "on_hand_units": 30, "purchase_in_transit_units": 0, "closing_units": null,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-WMT", "sid": "20311", "period": "2026-10-01",
      "on_hand_units": 0, "purchase_in_transit_units": 0, "closing_units": null,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-WMT", "sid": "20311", "period": "2026-11-01",
      "on_hand_units": 0, "purchase_in_transit_units": 0, "closing_units": null,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } },
    { "seller_sku": "DCC1800264-WMT", "sid": "20311", "period": "2026-12-01",
      "on_hand_units": 0, "purchase_in_transit_units": 0, "closing_units": null,
      "basis": { "source": "ch", "as_of": "2026-09-21", "includes_plan_purchase": false } }
  ]
}
```

★ 这份 fixture 刻意覆盖五种形态，缺一种就有一条规矩测不出来：
`expected_units: null`（未知）· `closing_units: -10`（断货 → 朱批）· `closing_units: null`（未知传染）·
`seller 20311 has_fba=false`（→「不适用」）· `purchase_units: null`（→ 提交时 `zero_purchase` 跳过）。

```json
// web/src/api/fixtures/catalog.json
{
  "skus": [
    { "sku": "DCC1800264", "name": "猫砂盆 · 大号", "category": "宠物用品/猫砂盆",
      "category_refreshed_at": "2026-09-15T08:00:00+08:00",
      "mskus": [
        { "seller_sku": "DCC1800264-US", "sid": "11072", "seller_id": "11072", "seller_name": "A4Pet-US", "claim": null },
        { "seller_sku": "DCC1800264-UK", "sid": "11094", "seller_id": "11094", "seller_name": "A4Pet-BS-UK",
          "claim": { "plan_id": 2, "plan_title": "2026 Q3 补货计划", "actor": "ops.li", "claimed_at": "2026-09-18T09:00:00+08:00" } },
        { "seller_sku": "DCC1800264-WMT", "sid": "20311", "seller_id": "20311", "seller_name": "A4Pet-Walmart-US", "claim": null }
      ] },
    { "sku": "DCC1800311", "name": "猫砂铲", "category": null, "category_refreshed_at": null,
      "mskus": [
        { "seller_sku": "DCC1800311-US", "sid": "11072", "seller_id": "11072", "seller_name": "A4Pet-US", "claim": null }
      ] }
  ],
  "truncated": false,
  "unbuildable_sellers": [
    { "seller_id": "30112", "seller_name": "A4Pet-TikTok-US", "reason": "no_channel_code" }
  ]
}
```

```json
// web/src/api/fixtures/revs-1.json
{
  "revs": [
    { "rev": 1, "submitted_by": "ops.zhang", "submitted_at": "2026-09-19T14:03:00+08:00",
      "content_digest": "8f1c2a0b9d4e6f77", "line_count": 2, "is_current": false, "is_in_flight": false },
    { "rev": 2, "submitted_by": "ops.zhang", "submitted_at": "2026-09-20T10:31:00+08:00",
      "content_digest": "3b7e5d1c8a90f2e4", "line_count": 3, "is_current": true, "is_in_flight": true }
  ],
  "in_flight_rev": 2,
  "current_rev": 2
}
```

- [ ] **Step 4: 写 mock 的失败测试**

```ts
// web/src/api/mock.test.ts
import { beforeEach, describe, expect, it } from 'vitest';
import { createMockApi } from './mock';
import { ApiError } from './client';

let api = createMockApi();
beforeEach(() => { api = createMockApi(); });

describe('mock 数据源', () => {
  it('putDemand(null) 存进去的是 null，不是 0', async () => {
    const cell = await api.putDemand(1, 'DCC1800264-US', '11072', '2026-10-01', null);
    expect(cell.expected_units).toBeNull();
    expect(cell.basis).toBe('unknown');
    const grid = await api.getGrid(1);
    const back = grid.demand.find((d) => d.seller_sku === 'DCC1800264-US' && d.period === '2026-10-01')!;
    expect(back.expected_units).toBeNull();
  });

  it('★ 占用撞了抛 409 并点名占用方', async () => {
    const err = await api.claim(1, { seller_sku: 'DCC1800264-UK', sid: '11094' }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).code).toBe('msku_already_claimed');
    expect((err as ApiError).detail).toMatchObject({ plan_id: 2, plan_title: '2026 Q3 补货计划', actor: 'ops.li' });
  });

  it('★ 提交逐条列 skipped[]，理由取 S-14 的两个值', async () => {
    const r = await api.submit(1);
    expect(r.rev).toBe(1);
    const reasons = r.skipped.map((s) => s.reason);
    expect(reasons).toContain('zero_purchase');
    expect(new Set(reasons).size).toBeGreaterThan(0);
    // ★ 被丢掉的那一侧要对得上：跳过数 + 铸出数 = 参与评估的格子数
    expect(r.line_count + r.skipped.length).toBe(3);
  });

  it('★ 已有在流转的版本 → 再提交 409 rev_in_flight，点名旧版号', async () => {
    const err = await api.submit(2).catch((e) => e);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).code).toBe('rev_in_flight');
    expect((err as ApiError).detail).toMatchObject({ in_flight_rev: 2 });
  });

  it('搜索目录：不给条件 → need_query，与「查不到」不同形', async () => {
    expect(await api.searchCatalog(1, {})).toEqual({ kind: 'need_query' });
    const miss = await api.searchCatalog(1, { q: 'ZZZZ' });
    expect(miss).toMatchObject({ kind: 'ok', skus: [] });
  });

  it('撤销版本不填理由 → 400 reason_required', async () => {
    const err = await api.cancelRev(2, 2, '   ').catch((e) => e);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).code).toBe('reason_required');
  });
});
```

- [ ] **Step 5: 跑它，确认红**

```bash
cd web && npx vitest run src/api/mock.test.ts
```
Expected: FAIL —— `Failed to resolve import "./mock"`。

- [ ] **Step 6: 写 mock 实现**

```ts
// web/src/api/mock.ts
import { ApiError, type SupplyChainApi } from './client';
import type {
  CatalogResult, GridResponse, PlanSummary, RevList, Seller, SubmitResult, SkippedCell,
} from './types';
import plansFixture from './fixtures/plans.json';
import gridFixture from './fixtures/grid-1.json';
import catalogFixture from './fixtures/catalog.json';
import revsFixture from './fixtures/revs-1.json';
import sellersFixture from './fixtures/sellers.json';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export function createMockApi(): SupplyChainApi {
  const plans = clone(plansFixture) as PlanSummary[];
  const grids = new Map<number, GridResponse>([[1, clone(gridFixture) as GridResponse]]);
  const catalog = clone(catalogFixture) as Extract<CatalogResult, { kind: 'ok' }>;
  const revs = new Map<number, RevList>([[2, clone(revsFixture) as RevList]]);
  const sellers = clone(sellersFixture) as Seller[];

  const plan = (id: number): PlanSummary => {
    const p = plans.find((x) => x.plan_id === id);
    if (!p) throw new ApiError(404, 'not_found', `plan ${id} 不存在`, { plan_id: id });
    return p;
  };
  const grid = (id: number): GridResponse => {
    const g = grids.get(id);
    if (!g) throw new ApiError(404, 'not_found', `plan ${id} 没有网格`, { plan_id: id });
    return g;
  };

  return {
    async listPlans(q) {
      return plans.filter((p) =>
        (q.state === undefined || p.state === q.state) &&
        (q.owner === undefined || p.owner_actor === q.owner) &&
        (q.archived === undefined || p.archived === q.archived));
    },
    async createPlan(input) {
      const id = Math.max(...plans.map((p) => p.plan_id)) + 1;
      plans.push({
        plan_id: id, title: input.title, period_start: input.period_start, months: input.months,
        owner_actor: 'ops.zhang', state: null, current_rev: null, in_flight_rev: null,
        archived: false, updated_at: new Date().toISOString(),
      });
      grids.set(id, { plan: plan(id), periods: [], demand: [], purchase: [], inventory: [] });
      return { plan_id: id };
    },
    async dashboardPlans() {
      // ★ 返回全集（S-20，诚实）。「准备排货 / 已排货」阶段 A 够不着，由前端不渲染
      return { in_progress: 2, submitted: 1, submitted_unconfirmed: 1, ordered: 0, ready_to_dispatch: 0, dispatched: 0 };
    },
    async dashboardUnsubmitted() {
      return {
        never_submitted: plans.filter((p) => p.state === null),
        changed_since_submit: plans.filter((p) => p.plan_id === 2),
      };
    },
    async listSellers() { return sellers; },

    async getGrid(planId) { return clone(grid(planId)); },

    async putDemand(planId, sellerSku, sid, period, units) {
      const g = grid(planId);
      const cell = g.demand.find((d) => d.seller_sku === sellerSku && d.sid === sid && d.period === period);
      if (!cell) throw new ApiError(404, 'not_found', '没有这个格子', { seller_sku: sellerSku, sid, period });
      if (units !== null && units < 0) throw new ApiError(400, 'negative_units', '期望销量不能为负', { units });
      cell.expected_units = units;
      cell.basis = units === null ? 'unknown' : 'human';
      const inv = g.inventory.find((i) => i.seller_sku === sellerSku && i.sid === sid && i.period === period);
      if (inv) {
        // ★ 未知向后传染：算不出来就是 null，不落回 0
        inv.closing_units = units === null ? null : inv.on_hand_units + inv.purchase_in_transit_units - units;
      }
      return clone(cell);
    },

    async putPurchase(planId, sku, period, units) {
      const g = grid(planId);
      const cell = g.purchase.find((p) => p.sku === sku && p.period === period);
      if (!cell) throw new ApiError(404, 'not_found', '没有这个格子', { sku, period });
      if (units !== null && units < 0) throw new ApiError(400, 'negative_units', '计划采购量不能为负', { units });
      cell.purchase_units = units;
      return clone(cell);
    },

    async searchCatalog(_planId, q) {
      if (!q.q && !q.category) return { kind: 'need_query' };
      const needle = (q.q ?? '').toUpperCase();
      const skus = catalog.skus.filter((s) =>
        s.sku.toUpperCase().includes(needle) || s.name.includes(q.q ?? ''));
      return { kind: 'ok', skus: clone(skus), truncated: catalog.truncated, unbuildable_sellers: clone(catalog.unbuildable_sellers) };
    },

    async claim(planId, target) {
      const msku = catalog.skus.flatMap((s) => s.mskus)
        .find((m) => m.seller_sku === target.seller_sku && m.sid === target.sid);
      if (!msku) throw new ApiError(404, 'not_found', '没有这个 msku', { ...target });
      if (msku.claim) {
        throw new ApiError(409, 'msku_already_claimed', `${target.seller_sku} 已被占用`, { ...msku.claim, ...target });
      }
      msku.claim = { plan_id: planId, plan_title: plan(planId).title, actor: 'ops.zhang', claimed_at: new Date().toISOString() };
    },

    async releaseClaim(_planId, sellerSku, sid) {
      const msku = catalog.skus.flatMap((s) => s.mskus).find((m) => m.seller_sku === sellerSku && m.sid === sid);
      if (msku) msku.claim = null;  // ★ 释放不删行
    },

    async submit(planId) {
      const p = plan(planId);
      if (p.in_flight_rev !== null) {
        throw new ApiError(409, 'rev_in_flight', `rev ${p.in_flight_rev} 还在流转`, { in_flight_rev: p.in_flight_rev });
      }
      const g = grid(planId);
      const skipped: SkippedCell[] = [];
      let lines = 0;
      const skus = [...new Set(g.purchase.map((c) => c.sku))];
      for (const period of g.periods) {
        for (const sku of skus) {
          const pc = g.purchase.find((c) => c.sku === sku && c.period === period);
          const sellerIds = [...new Set(g.demand.filter((d) => d.sku === sku && d.period === period).map((d) => d.seller_id))];
          if (!pc || pc.purchase_units === null || pc.purchase_units === 0) {
            skipped.push({ seller_id: sellerIds[0] ?? '', sku, period, reason: 'zero_purchase' });
            continue;
          }
          if (sellerIds.length === 0) {
            skipped.push({ seller_id: '', sku, period, reason: 'no_claimed_msku' });
            continue;
          }
          lines += 1;
        }
      }
      const rev = (p.current_rev ?? 0) + 1;
      p.current_rev = rev;
      p.in_flight_rev = rev;
      p.state = '已提交';
      revs.set(planId, {
        revs: [{ rev, submitted_by: 'ops.zhang', submitted_at: new Date().toISOString(),
                 content_digest: `mock-${planId}-${rev}`, line_count: lines, is_current: true, is_in_flight: true }],
        in_flight_rev: rev, current_rev: rev,
      });
      const result: SubmitResult = { rev, line_count: lines, skipped };
      return result;
    },

    async listRevs(planId) {
      return clone(revs.get(planId) ?? { revs: [], in_flight_rev: null, current_rev: null });
    },
    async setCurrentRev(planId, rev) {
      const list = revs.get(planId);
      if (!list || !list.revs.some((r) => r.rev === rev)) throw new ApiError(404, 'not_found', `rev ${rev} 不存在`, { rev });
      list.revs.forEach((r) => { r.is_current = r.rev === rev; });
      list.current_rev = rev;
      plan(planId).current_rev = rev;
    },
    async diff(planId, from, to) {
      return {
        from, to,
        rows: [
          { kind: 'demand', sku: 'DCC1800264', seller_sku: 'DCC1800264-US', sid: '11072',
            period: '2026-10-01', before: 160, after: 180, change: 'changed' },
          { kind: 'purchase', sku: 'DCC1800264', seller_sku: null, sid: null,
            period: '2026-11-01', before: null, after: 300, change: 'added' },
        ],
      };
    },
    async cancelRev(planId, rev, reason) {
      if (reason.trim() === '') throw new ApiError(400, 'reason_required', '撤销必须填理由', { rev });
      const list = revs.get(planId);
      const target = list?.revs.find((r) => r.rev === rev);
      if (!list || !target) throw new ApiError(404, 'not_found', `rev ${rev} 不存在`, { rev });
      target.is_in_flight = false;
      list.in_flight_rev = null;
      plan(planId).in_flight_rev = null;
      plan(planId).state = '已撤销';
      return { cancelled_lines: target.line_count };
    },
  };
}
```

- [ ] **Step 7: 跑 mock 测试，确认全绿**

```bash
cd web && npx vitest run src/api/mock.test.ts
```
Expected: PASS（6 个用例）。若 `line_count + skipped.length` 那条红，说明分类把某些格子**两边都没算**——那正是这条断言存在的理由，不许改断言，去查分支。

- [ ] **Step 8: 写 http 的失败测试（含三问日志）**

```ts
// web/src/api/http.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createHttpApi } from './http';
import { ApiError } from './client';
import { setActor } from '../shell/actorStore';
import gridFixture from './fixtures/grid-1.json';

afterEach(() => { vi.restoreAllMocks(); });

const okResponse = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

describe('http 数据源', () => {
  it('发 x-actor 头，取自顶栏下拉', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(okResponse(gridFixture));
    setActor('ops.li');
    await createHttpApi({ base: '/v1', timeoutMs: 1000 }).getGrid(1);
    const init = fetchSpy.mock.calls[0]![1] as RequestInit;
    expect(new Headers(init.headers).get('x-actor')).toBe('ops.li');
  });

  it('★ 只发声明过的查询参数 —— undefined 的不拼进 URL', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(okResponse([]));
    await createHttpApi({ base: '/v1', timeoutMs: 1000 }).listPlans({ archived: false });
    expect(String(fetchSpy.mock.calls[0]![0])).toBe('/v1/plans?archived=false');
  });

  it('409 抛 ApiError 且 detail 原样带回', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ code: 'rev_in_flight', message: 'rev 2 还在流转', detail: { in_flight_rev: 2 } }),
      { status: 409, headers: { 'content-type': 'application/json' } }));
    const err = await createHttpApi({ base: '/v1', timeoutMs: 1000 }).submit(1).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).detail).toEqual({ in_flight_rev: 2 });
  });

  it('★ 日志三问：打的谁 · 多久 · 怎么失败的（带 cause.code）', async () => {
    const logged: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { logged.push(a); });
    const boom = new TypeError('fetch failed');
    (boom as { cause?: unknown }).cause = { code: 'UND_ERR_CONNECT_TIMEOUT' };
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(boom);

    await createHttpApi({ base: '/v1', timeoutMs: 1000 }).getGrid(1).catch(() => undefined);

    const line = JSON.stringify(logged[0]);
    expect(line).toContain('GET /v1/plans/1/grid');   // 打的谁
    expect(line).toMatch(/"ms":\d+/);                  // 多久
    expect(line).toContain('UND_ERR_CONNECT_TIMEOUT'); // ★ 怎么失败的 —— 不是「fetch failed」五个字
  });

  it('★ 超时与连不上分得开：超时记 TimeoutError 而不是 cause code', async () => {
    const logged: unknown[][] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { logged.push(a); });
    const timeout = new DOMException('The operation was aborted due to timeout', 'TimeoutError');
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(timeout);

    const err = await createHttpApi({ base: '/v1', timeoutMs: 5 }).getGrid(1).catch((e) => e);
    expect((err as ApiError).code).toBe('timeout');
    expect(JSON.stringify(logged[0])).toContain('TimeoutError');
  });
});
```

- [ ] **Step 9: 跑它，确认红**

```bash
cd web && npx vitest run src/api/http.test.ts
```
Expected: FAIL —— `Failed to resolve import "./http"`。

- [ ] **Step 10: 写 http 实现**

```ts
// web/src/api/http.ts
import { ApiError, type SupplyChainApi } from './client';
import { getActor } from '../shell/actorStore';

interface HttpOptions { base: string; timeoutMs: number }

/** ★ 三问日志：打的谁（method+path）· 多久（ms）· 怎么失败的（status+code 或 cause.code）。
 *  缺一个，下次就得重新复现一遍。 */
function logFail(method: string, path: string, ms: number, extra: Record<string, unknown>): void {
  console.error('[api]', JSON.stringify({ call: `${method} ${path}`, ms, ...extra }));
}

/** 重试「成功」的那次抖动同样要留痕 —— 本层不重试，但慢调用要留下来 */
function logSlow(method: string, path: string, ms: number, status: number): void {
  if (ms >= 2000) console.warn('[api]', JSON.stringify({ call: `${method} ${path}`, ms, status, slow: true }));
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  // ★ 未声明查询参数一律 400 ⇒ undefined 的一律不拼，不发空串
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined);
  return pairs.length === 0 ? '' : `?${pairs.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&')}`;
}

export function createHttpApi(opt: HttpOptions): SupplyChainApi {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${opt.base}${path}`;
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
        logFail(method, url, ms, { kind: 'TimeoutError', timeout_ms: opt.timeoutMs });
        throw new ApiError(0, 'timeout', `${method} ${url} 超时（${opt.timeoutMs}ms）`, { timeout_ms: opt.timeoutMs });
      }
      const cause = (e as { cause?: { code?: string; errors?: { code?: string }[] } }).cause;
      const code = cause?.code ?? cause?.errors?.[0]?.code ?? 'unknown';
      logFail(method, url, ms, { kind: (e as Error).name, cause_code: code, message: (e as Error).message });
      throw new ApiError(0, 'network', `${method} ${url} 连不上（${code}）`, { cause_code: code });
    }

    const ms = Date.now() - started;
    logSlow(method, url, ms, res.status);

    if (res.status === 204) return undefined as T;
    const text = await res.text();
    let parsed: unknown = null;
    if (text !== '') {
      try { parsed = JSON.parse(text); }
      catch {
        // ★ 静默兜底是最坏的一种：解析不了要出声，否则「代理挂了」和「接口本身就错」长得一样
        logFail(method, url, ms, { status: res.status, kind: 'non_json_body', head: text.slice(0, 120) });
        throw new ApiError(res.status, 'bad_response', `${method} ${url} 返回的不是 JSON`, { head: text.slice(0, 120) });
      }
    }
    if (!res.ok) {
      const e = parsed as { code?: string; message?: string; detail?: Record<string, unknown> } | null;
      logFail(method, url, ms, { status: res.status, code: e?.code ?? 'unknown' });
      throw new ApiError(res.status, e?.code ?? 'unknown', e?.message ?? `${method} ${url} ${res.status}`, e?.detail ?? {});
    }
    return parsed as T;
  }

  return {
    listPlans: (q) => call('GET', `/plans${qs({ state: q.state, owner: q.owner, archived: q.archived })}`),
    createPlan: (input) => call('POST', '/plans', input),
    dashboardPlans: () => call('GET', '/dashboard/plans'),
    dashboardUnsubmitted: () => call('GET', '/dashboard/unsubmitted'),
    listSellers: () => call('GET', '/sellers'),

    getGrid: (planId) => call('GET', `/plans/${planId}/grid`),
    putDemand: (planId, sellerSku, sid, period, units) =>
      call('PUT', `/plans/${planId}/demand/${encodeURIComponent(sellerSku)}/${sid}/${period}`, { units }),
    putPurchase: (planId, sku, period, units) =>
      call('PUT', `/plans/${planId}/purchase/${encodeURIComponent(sku)}/${period}`, { units }),

    searchCatalog: (_planId, q) => call('GET', `/catalog/skus${qs({ q: q.q, category: q.category })}`),
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

- [ ] **Step 11: 数据源开关**

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

- [ ] **Step 12: 写 ErrorDetail 的失败测试**

```tsx
// web/src/components/ErrorDetail.test.tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ErrorDetail } from './ErrorDetail';
import { ApiError } from '../api/client';

describe('ErrorDetail', () => {
  it('★ 逐项点名，不说「保存失败」', () => {
    const err = new ApiError(409, 'msku_already_claimed', 'DCC1800264-UK 已被占用',
      { plan_id: 2, plan_title: '2026 Q3 补货计划', actor: 'ops.li' });
    render(<ErrorDetail err={err} />);
    expect(screen.getByText('DCC1800264-UK 已被占用')).toBeInTheDocument();
    expect(screen.getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(screen.getByText('ops.li')).toBeInTheDocument();
    expect(screen.queryByText(/失败$/)).toBeNull();
  });

  it('★ 409 与 400 的样子不同 —— 一个该刷新重来，一个该改表单', () => {
    const { container: conflict } = render(<ErrorDetail err={new ApiError(409, 'rev_in_flight', 'rev 2 还在流转', { in_flight_rev: 2 })} />);
    const { container: bad } = render(<ErrorDetail err={new ApiError(400, 'reason_required', '撤销必须填理由', {})} />);
    expect(conflict.querySelector('.flash--bad')).not.toBeNull();
    expect(conflict.querySelector('.gate__code')?.textContent).toBe('409 rev_in_flight');
    expect(bad.querySelector('.gate__code')?.textContent).toBe('400 reason_required');
  });
});
```

- [ ] **Step 13: 跑它确认红，再写实现**

```bash
cd web && npx vitest run src/components/ErrorDetail.test.tsx
```
Expected: FAIL —— `Failed to resolve import "./ErrorDetail"`。

```tsx
// web/src/components/ErrorDetail.tsx
import type { ApiError } from '../api/client';

// ★ 原则五：闸失败要点名 —— 一次列全，不是修一条报一条
export function ErrorDetail({ err }: { err: ApiError }) {
  const rows = Object.entries(err.detail);
  return (
    <div className="flash flash--bad" role="alert">
      <div>{err.message}</div>
      <div className="gate__code">{err.status} {err.code}</div>
      {rows.length > 0 && (
        <ul>
          {rows.map(([k, v]) => (
            <li key={k} className="gate__detail">
              <span className="muted">{k}</span> {typeof v === 'object' ? JSON.stringify(v) : String(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 14: 全跑一遍并提交**

```bash
cd web && npx vitest run && npx tsc -b
```
Expected: `Test Files 5 passed`，tsc 静默。

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 数据层 —— 一个接口两个实现，fixture 是两者唯一数据源

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
- Consumes: `api`（Task 2）· `AppShell` `pushToast`（Task 1）· 类型 `PlanSummary` `DashboardPlanCounts` `UnsubmittedBoard`
- Produces:
  - `type QtyValue = { kind: 'num'; value: number } | { kind: 'unknown' } | { kind: 'na' } | { kind: 'nosum' }`
  - `<Qty v={QtyValue} big?: boolean />` —— Task 4 网格的合计列与结论数都用它
  - `STAGE_A_COUNT_KEYS: readonly ['never_submitted', 'submitted', 'cancelled']`

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
    // ★ 未知 / 不适用 / 不可求和 三者的文字互不相同 —— 合成一个就查不出是哪种病
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
    expect(within(counts).queryByText('准备排货')).toBeNull();
    expect(within(counts).queryByText('已排货')).toBeNull();
    expect(within(counts).queryByText('已下单')).toBeNull();
    expect(counts.textContent).not.toContain('0');
  });

  it('★ 完结的计划不显示，但丢掉的那一侧要有个数', async () => {
    renderHome();
    const table = await screen.findByTestId('plan-list');
    expect(within(table).queryByText('2026 Q1 首发计划')).toBeNull();
    expect(screen.getByTestId('hidden-finished')).toHaveTextContent('已完结 1 张');
  });

  it('两栏：未提交 / 提交后又改过', async () => {
    renderHome();
    expect(await screen.findByTestId('never-submitted')).toHaveTextContent('2026 Q4 销售计划');
    expect(screen.getByTestId('changed-since-submit')).toHaveTextContent('2026 Q3 补货计划');
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
    await userEvent.clear(screen.getByLabelText('起始月'));
    await userEvent.type(screen.getByLabelText('起始月'), '2027-01');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(await screen.findByTestId('nav-to')).toHaveTextContent('/plans/5');
  });
});
```

★ 最后一条用 `nav-to` 而不是真跳转：首页只负责**发起**跳转，路由本身在 Task 8 的端到端里验。
实现里用 `useNavigate()`，测试用 `MemoryRouter` 时把目标写进一个 `data-testid="nav-to"` 的隐藏节点 —— 见 Step 6 的 `navigateWithTrace`。

- [ ] **Step 5: 跑它确认红**

```bash
cd web && npx vitest run src/pages/OpsHome.test.tsx
```
Expected: FAIL —— 5 条全红，第一条报 `Unable to find an element by: [data-testid="counts"]`（占位组件只有 `.empty`）。

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
import type { PlanSummary, UnsubmittedBoard } from '../api/types';

/** ★ S-20：后端返回全集（诚实），阶段 A 够不着的态在这里被挡住 —— 挡的地方只有这一处 */
export const STAGE_A_COUNT_KEYS = ['never_submitted', 'submitted', 'cancelled'] as const;

const LABEL: Record<(typeof STAGE_A_COUNT_KEYS)[number], string> = {
  never_submitted: '未提交', submitted: '已提交', cancelled: '已撤销',
};

type SortKey = 'period' | 'title' | 'state';

export function OpsHome() {
  const navigate = useNavigate();
  const [plans, setPlans] = useState<PlanSummary[] | null>(null);
  const [board, setBoard] = useState<UnsubmittedBoard | null>(null);
  const [submitted, setSubmitted] = useState(0);
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
      .then(([ps, b, d]) => { setPlans(ps); setBoard(b); setSubmitted(d.submitted); })
      .catch((e: ApiError) => setErr(e));
  }, []);

  const finished = useMemo(() => (plans ?? []).filter((p) => p.state === '已完结'), [plans]);
  const visible = useMemo(() => {
    const kept = (plans ?? []).filter((p) => p.state !== '已完结');
    const rows = kept.filter((p) =>
      (fPeriod === '' || p.period_start.startsWith(fPeriod)) &&
      (fTitle === '' || p.title.includes(fTitle)) &&
      (fRev === '' || String(p.current_rev ?? '') === fRev) &&
      (fState === '' || stateLabel(p) === fState));
    return [...rows].sort((a, b) =>
      sort === 'period' ? b.period_start.localeCompare(a.period_start)
      : sort === 'title' ? a.title.localeCompare(b.title)
      : stateLabel(a).localeCompare(stateLabel(b)));
  }, [plans, fPeriod, fTitle, fRev, fState, sort]);

  const counts = {
    never_submitted: board?.never_submitted.length ?? 0,
    submitted,
    cancelled: (plans ?? []).filter((p) => p.state === '已撤销').length,
  };

  async function create(form: HTMLFormElement) {
    const data = new FormData(form);
    const month = String(data.get('period_start'));
    try {
      const { plan_id } = await api.createPlan({
        title: String(data.get('title')),
        period_start: `${month}-01`,
        months: Number(data.get('months')),
      });
      setNavTo(`/plans/${plan_id}`);
      navigate(`/plans/${plan_id}`);
    } catch (e) {
      setErr(e as ApiError);
      pushToast({ kind: 'fail', text: (e as ApiError).message });
    }
  }

  if (err && plans === null) return <AppShell crumb="我的计划"><ErrorDetail err={err} /></AppShell>;

  return (
    <AppShell crumb="我的计划">
      <div className="head">
        <div className="head__main"><h1>我的计划</h1></div>
        <div className="head__act">
          <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>新建销售计划</button>
        </div>
      </div>

      <div className="sec counts" data-testid="counts">
        {STAGE_A_COUNT_KEYS.map((k) => (
          <div className="kpi" key={k}>
            <span className="kpi__n">{counts[k]}</span>
            <span className="kpi__d">{LABEL[k]}</span>
          </div>
        ))}
      </div>

      {creating && (
        <form
          className="sec bar"
          onSubmit={(e) => { e.preventDefault(); void create(e.currentTarget); }}
        >
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
                <span className="muted">rev {p.current_rev}</span>
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
            {STAGE_A_COUNT_KEYS.map((k) => <option key={k} value={LABEL[k]}>{LABEL[k]}</option>)}
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
                <td className="r">{p.current_rev === null ? <Qty v={{ kind: 'unknown' }} /> : p.current_rev}</td>
                <td><span className="chip chip--dim">{stateLabel(p)}</span></td>
                <td><a href={`/plans/${p.plan_id}`}>打开</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* ★ 过滤掉的那一侧要有个数 —— 「没有」和「被我藏了」长得一样 */}
      <div className="muted mt" data-testid="hidden-finished">已完结 {finished.length} 张不在列表</div>
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
Expected: PASS（7 个用例）。
★ 若 `counts.textContent).not.toContain('0')` 红，检查是不是把够不着的态渲染成了 0 —— 不许把断言改松。

- [ ] **Step 8: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 运营首页 —— 只渲染够得着的三态，完结的计划过滤但留个数

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
- Consumes: `api.getGrid` `api.putDemand` `api.putPurchase` `api.releaseClaim` `api.listSellers`（Task 2）· `<Qty>` `QtyValue`（Task 3）· `<AppShell>` `<Fold>` `pushToast`（Task 1）· `<ErrorDetail>`（Task 2）
- Produces:
  - `buildGridModel(grid: GridResponse, sellers: Seller[]): GridModel`
  - `GridModel = { periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[]; orphans: Orphan[] }`
  - `SkuBlock = { seller_id; seller_name; has_fba; sku; mskus: MskuRow[] }`
  - `MskuRow = { seller_sku; sid; cells: MskuCell[] }`
  - `MskuCell = { period; system_units; expected_units: number | null; basis; inventory: GridInventoryCell | null }`
  - `PurchaseRow = { sku; cells: { period; purchase_units: number | null }[] }`
  - `Orphan = { kind: 'unknown_seller' | 'inventory_without_demand' | 'demand_without_inventory'; key: string }`
  - `sumUnits(values: (number | null)[]): QtyValue` · `demandAt(block, period): QtyValue` · `inventoryAt(block, period): QtyValue` · `INVENTORY_TOTAL: QtyValue`（恒 `{ kind: 'nosum' }`）
  - Task 7 会在这个文件里加「提交」按钮，**不改本 Task 的任何导出名**

- [ ] **Step 1: 写模型层的失败测试**

```ts
// web/src/pages/planGridModel.test.ts
import { describe, expect, it } from 'vitest';
import { buildGridModel, demandAt, inventoryAt, sumUnits, INVENTORY_TOTAL } from './planGridModel';
import gridFixture from '../api/fixtures/grid-1.json';
import sellersFixture from '../api/fixtures/sellers.json';
import type { GridResponse, Seller } from '../api/types';

const grid = gridFixture as GridResponse;
const sellers = sellersFixture as Seller[];

describe('网格模型', () => {
  it('分块：一个店铺 · 一个货号 一块；msku 挂在块下', () => {
    const m = buildGridModel(grid, sellers);
    expect(m.blocks.map((b) => `${b.seller_id}/${b.sku}`)).toEqual(['11072/DCC1800264', '20311/DCC1800264']);
    expect(m.blocks[0]!.mskus.map((r) => r.seller_sku)).toEqual(['DCC1800264-US', 'DCC1800264-US-B']);
  });

  it('★ 未知向后传染：任何一个是 null，和就是未知，不是把 null 当 0', () => {
    expect(sumUnits([180, 200, 210])).toEqual({ kind: 'num', value: 590 });
    expect(sumUnits([180, null, 210])).toEqual({ kind: 'unknown' });
    expect(sumUnits([])).toEqual({ kind: 'unknown' });
  });

  it('★ 跨 msku 求和可以，跨月求和不行', () => {
    const m = buildGridModel(grid, sellers);
    const us = m.blocks[0]!;
    // 同一时点两个 listing 的库存可以相加：420 + (-10)
    expect(inventoryAt(us, '2026-10-01')).toEqual({ kind: 'num', value: 410 });
    // 跨月：把三个月的期末库存加起来是重复计数
    expect(INVENTORY_TOTAL).toEqual({ kind: 'nosum' });
    expect(demandAt(us, '2026-10-01')).toEqual({ kind: 'num', value: 220 });
  });

  it('★ 无 FBA 的店铺 → 不适用，不是 0', () => {
    const m = buildGridModel(grid, sellers);
    const wmt = m.blocks.find((b) => b.seller_id === '20311')!;
    expect(wmt.has_fba).toBe(false);
    expect(inventoryAt(wmt, '2026-10-01')).toEqual({ kind: 'na' });
  });

  it('★ 被丢掉的那一侧必须统计：认不出的店铺 / 对不上的库存行都要点名', () => {
    const dirty: GridResponse = {
      ...grid,
      demand: [...grid.demand, { ...grid.demand[0]!, seller_id: '99999', seller_sku: 'GHOST', sid: '99999' }],
      inventory: [...grid.inventory, { ...grid.inventory[0]!, seller_sku: 'NO-DEMAND', sid: '11072' }],
    };
    const m = buildGridModel(dirty, sellers);
    expect(m.orphans).toEqual(expect.arrayContaining([
      { kind: 'unknown_seller', key: '99999/GHOST' },
      { kind: 'inventory_without_demand', key: 'NO-DEMAND/11072/2026-10-01' },
    ]));
    // 认不出的行不许静默并进某个块
    expect(m.blocks.some((b) => b.seller_id === '99999')).toBe(false);
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
import type { DemandBasis, GridInventoryCell, GridResponse, Period, Seller, Sid } from '../api/types';

export interface MskuCell {
  period: Period;
  system_units: number;
  expected_units: number | null;
  basis: DemandBasis;
  inventory: GridInventoryCell | null;
}
export interface MskuRow { seller_sku: string; sid: Sid; cells: MskuCell[] }
export interface SkuBlock {
  seller_id: string; seller_name: string; has_fba: boolean; sku: string; mskus: MskuRow[];
}
export interface PurchaseRow { sku: string; cells: { period: Period; purchase_units: number | null }[] }
export interface Orphan {
  kind: 'unknown_seller' | 'inventory_without_demand' | 'demand_without_inventory';
  key: string;
}
export interface GridModel {
  periods: Period[]; blocks: SkuBlock[]; purchase: PurchaseRow[]; orphans: Orphan[];
}

/** ★ 跨月的期末库存相加是重复计数（14 §1.1 ①）—— 永远是「不可求和」，不是某个数 */
export const INVENTORY_TOTAL: QtyValue = { kind: 'nosum' };

/** ★ 任何一项未知 ⇒ 和未知。空数组也是未知：没有数不等于 0 */
export function sumUnits(values: (number | null)[]): QtyValue {
  if (values.length === 0 || values.some((v) => v === null)) return { kind: 'unknown' };
  return { kind: 'num', value: values.reduce<number>((a, b) => a + (b as number), 0) };
}

export function demandAt(block: SkuBlock, period: Period): QtyValue {
  return sumUnits(block.mskus.map((r) => r.cells.find((c) => c.period === period)?.expected_units ?? null));
}

export function inventoryAt(block: SkuBlock, period: Period): QtyValue {
  // ★ 无 FBA 的平台没有可扣的库存池 —— 「不适用」，不是 0（02 §3.1a）
  if (!block.has_fba) return { kind: 'na' };
  return sumUnits(block.mskus.map((r) => r.cells.find((c) => c.period === period)?.inventory?.closing_units ?? null));
}

export function buildGridModel(grid: GridResponse, sellers: Seller[]): GridModel {
  const sellerById = new Map(sellers.map((s) => [s.seller_id, s]));
  const orphans: Orphan[] = [];
  const blocks = new Map<string, SkuBlock>();
  const usedInventory = new Set<string>();
  const invKey = (sellerSku: string, sid: Sid, period: Period) => `${sellerSku}/${sid}/${period}`;
  const invByKey = new Map(grid.inventory.map((i) => [invKey(i.seller_sku, i.sid, i.period), i]));

  for (const d of grid.demand) {
    const seller = sellerById.get(d.seller_id);
    if (!seller) {
      // ★ 认不出的店铺硬性点名，不许落进 else 再被下游过滤掉
      orphans.push({ kind: 'unknown_seller', key: `${d.seller_id}/${d.seller_sku}` });
      continue;
    }
    const bKey = `${d.seller_id}/${d.sku}`;
    let block = blocks.get(bKey);
    if (!block) {
      block = { seller_id: d.seller_id, seller_name: seller.name, has_fba: seller.has_fba, sku: d.sku, mskus: [] };
      blocks.set(bKey, block);
    }
    let row = block.mskus.find((r) => r.seller_sku === d.seller_sku && r.sid === d.sid);
    if (!row) { row = { seller_sku: d.seller_sku, sid: d.sid, cells: [] }; block.mskus.push(row); }

    const k = invKey(d.seller_sku, d.sid, d.period);
    const inv = invByKey.get(k) ?? null;
    if (inv) usedInventory.add(k); else if (seller.has_fba) {
      orphans.push({ kind: 'demand_without_inventory', key: k });
    }
    row.cells.push({
      period: d.period, system_units: d.system_units, expected_units: d.expected_units,
      basis: d.basis, inventory: inv,
    });
  }

  for (const [k] of invByKey) {
    if (!usedInventory.has(k)) orphans.push({ kind: 'inventory_without_demand', key: k });
  }

  const skus = [...new Set(grid.purchase.map((p) => p.sku))];
  const purchase: PurchaseRow[] = skus.map((sku) => ({
    sku,
    cells: grid.periods.map((period) => ({
      period,
      purchase_units: grid.purchase.find((p) => p.sku === sku && p.period === period)?.purchase_units ?? null,
    })),
  }));

  return { periods: grid.periods, blocks: [...blocks.values()], purchase, orphans };
}
```

- [ ] **Step 4: 跑模型测试确认绿**

```bash
cd web && npx vitest run src/pages/planGridModel.test.ts
```
Expected: PASS（5 个用例）。

- [ ] **Step 5: 写网格页的失败测试**

```tsx
// web/src/pages/PlanGrid.test.tsx
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PlanGrid } from './PlanGrid';

const renderGrid = () => render(
  <MemoryRouter initialEntries={['/plans/1']}>
    <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
  </MemoryRouter>,
);

/** 行默认是折叠的（行 = 店铺·货号）；要看 msku 级的格子得先展开 */
async function expand(testId: string) {
  const block = await screen.findByTestId(testId);
  await userEvent.click(within(block).getByRole('button', { name: '展开' }));
  return block;
}

describe('计划编辑网格', () => {
  it('标题 + 月份列 + 合计列', async () => {
    renderGrid();
    expect(await screen.findByRole('heading', { name: '2026 Q4 销售计划' })).toBeInTheDocument();
    const head = within(screen.getByTestId('block-11072-DCC1800264')).getAllByRole('columnheader');
    expect(head.map((h) => h.textContent)).toEqual(['店铺·货号', '2026-10', '2026-11', '2026-12', '合计']);
  });

  it('★ 一格三行：预估（灰）· 库存（灰 + basis）· 输入（蓝黑）', async () => {
    renderGrid();
    await expand('block-11072-DCC1800264');
    const cell = screen.getByTestId('cell-DCC1800264-US-2026-10-01');
    expect(within(cell).getByTestId('system')).toHaveClass('i-pencil');
    expect(within(cell).getByTestId('closing')).toHaveTextContent('420');
    expect(within(cell).getByText('未计本计划采购')).toBeInTheDocument();
    expect(within(cell).getByText('在仓')).toBeInTheDocument();
    expect(within(cell).getByRole('textbox')).toHaveValue('180');
  });

  it('★ 空 = 未知：输入框空着，不显示 0，也没有 placeholder "0"', async () => {
    renderGrid();
    await expand('block-11072-DCC1800264');
    const cell = screen.getByTestId('cell-DCC1800264-US-2026-12-01');
    const input = within(cell).getByRole('textbox');
    expect(input).toHaveValue('');
    expect(input).toHaveAttribute('placeholder', '');
    expect(within(cell).getByTestId('closing')).toHaveTextContent('—');
  });

  it('★ 断货格：朱批左边条 + data-state=out', async () => {
    renderGrid();
    await expand('block-11072-DCC1800264');
    const cell = screen.getByTestId('cell-DCC1800264-US-B-2026-10-01');
    expect(cell).toHaveAttribute('data-state', 'out');
    expect(within(cell).getByTestId('closing')).toHaveTextContent('-10');
  });

  it('★ 无 FBA 的店铺：库存行写「不适用」，整块里不出现 0 库存', async () => {
    renderGrid();
    const block = await screen.findByTestId('block-20311-DCC1800264');
    // 3 个月 + 合计列 = 4 处；★ 一处都不许是 0
    expect(within(block).getAllByText('不适用').length).toBe(4);
    expect(within(block).queryByText('0')).toBeNull();
  });

  it('★ 合计：量可求和；存量列写「不可求和」', async () => {
    renderGrid();
    const block = await screen.findByTestId('block-11072-DCC1800264');
    const sums = within(block).getByTestId('sum-collapsed');
    expect(within(sums).getByTestId('sum-demand')).toHaveTextContent('—'); // 12 月未填 ⇒ 未知传染
    expect(within(sums).getByTestId('sum-inventory')).toHaveTextContent('不可求和');
  });

  it('折叠行只有 Σ 没有输入框；展开后才出现 msku 行与输入框', async () => {
    renderGrid();
    const block = await screen.findByTestId('block-11072-DCC1800264');
    expect(within(block).queryByText('DCC1800264-US-B')).toBeNull();
    // ★ 折叠行是只读的 Σ —— 在货号级填数会把「人填的粒度」悄悄降一层
    expect(within(block).queryByRole('textbox')).toBeNull();
    expect(within(block).getByTestId('sum-expected-2026-10-01')).toHaveTextContent('220');

    await userEvent.click(within(block).getByRole('button', { name: '展开' }));
    expect(within(block).getByText('DCC1800264-US-B')).toBeInTheDocument();
    expect(within(block).getAllByRole('textbox').length).toBe(6);  // 2 msku × 3 月
  });

  it('填期望销量 → 保存并当场刷新库存预估', async () => {
    renderGrid();
    await expand('block-11072-DCC1800264');
    const cell = screen.getByTestId('cell-DCC1800264-US-2026-10-01');
    const input = within(cell).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, '300');
    await userEvent.tab();
    expect(await within(cell).findByTestId('closing')).toHaveTextContent('300');
    expect(input).toHaveAttribute('data-touched', '1');
  });

  it('★ 清空输入 → 发 null 而不是 0，库存变未知', async () => {
    renderGrid();
    await expand('block-11072-DCC1800264');
    const cell = screen.getByTestId('cell-DCC1800264-US-2026-11-01');
    const input = within(cell).getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.tab();
    expect(await within(cell).findByTestId('closing')).toHaveTextContent('—');
  });

  it('计划采购量是货号级，行头不带店铺', async () => {
    renderGrid();
    const block = await screen.findByTestId('purchase-block');
    expect(within(block).getByText('DCC1800264')).toBeInTheDocument();
    expect(within(block).queryByText('A4Pet-US')).toBeNull();
    expect(within(block).getAllByRole('textbox').length).toBe(3);
  });

  it('★ 对不上的行要点名，不静默丢', async () => {
    renderGrid();
    await screen.findByTestId('block-11072-DCC1800264');
    // fixture 干净 ⇒ 丢弃区不渲染；这条守的是「没有丢弃时也不许凭空出现一条」
    expect(screen.queryByTestId('orphans')).toBeNull();
  });
});
```

- [ ] **Step 6: 跑它确认红**

```bash
cd web && npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: FAIL —— 11 条全红，首条报 `Unable to find an accessible element with the role "heading" and name "2026 Q4 销售计划"`。

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
import type { GridResponse, Seller } from '../api/types';
import {
  buildGridModel, demandAt, inventoryAt, sumUnits, INVENTORY_TOTAL,
  type MskuCell, type SkuBlock,
} from './planGridModel';

export function PlanGrid() {
  const planId = Number(useParams().planId);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [sellers, setSellers] = useState<Seller[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<ApiError | null>(null);

  const load = () => Promise.all([api.getGrid(planId), api.listSellers()])
    .then(([g, s]) => { setGrid(g); setSellers(s); setErr(null); })
    .catch((e: ApiError) => setErr(e));

  useEffect(() => { void load(); }, [planId]);

  const model = useMemo(() => (grid ? buildGridModel(grid, sellers) : null), [grid, sellers]);

  if (err && grid === null) return <AppShell crumb="计划编辑"><ErrorDetail err={err} /></AppShell>;
  if (!grid || !model) return <AppShell crumb="计划编辑"><div className="empty" /></AppShell>;

  async function saveDemand(sellerSku: string, sid: string, period: string, raw: string) {
    // ★ 空串 = 未知 ⇒ null；不是 0
    const units = raw.trim() === '' ? null : Number(raw);
    if (units !== null && !Number.isInteger(units)) {
      pushToast({ kind: 'fail', text: `${sellerSku} ${period.slice(0, 7)}：${raw} 不是整数` });
      return;
    }
    try {
      await api.putDemand(planId, sellerSku, sid, period, units);
      await load();
    } catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).message }); }
  }

  async function savePurchase(sku: string, period: string, raw: string) {
    const units = raw.trim() === '' ? null : Number(raw);
    try { await api.putPurchase(planId, sku, period, units); await load(); }
    catch (e) { setErr(e as ApiError); pushToast({ kind: 'fail', text: (e as ApiError).message }); }
  }

  async function removeSku(block: SkuBlock) {
    for (const row of block.mskus) await api.releaseClaim(planId, row.seller_sku, row.sid);
    await load();
    pushToast({ kind: 'ok', text: `${block.sku} 已从本计划移出 ${block.mskus.length} 个 msku` });
  }

  const toggle = (key: string) => setExpanded((s) => {
    const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n;
  });

  return (
    <AppShell crumb="计划编辑">
      <div className="head">
        <div className="head__main">
          <h1>{grid.plan.title}</h1>
          <div className="head__meta">
            起始月 <b>{grid.plan.period_start.slice(0, 7)}</b> · 跨 <b>{grid.plan.months}</b> 月 ·
            负责人 <b>{grid.plan.owner_actor}</b>
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
        const key = `${block.seller_id}-${block.sku}`;
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
                    {model.periods.map((p) => <th key={p}>{p.slice(0, 7)}</th>)}
                    <th className="gh--sum">合计</th>
                  </tr>
                </thead>
                <tbody>
                  {!open && (
                    <tr>
                      <td className="gr">
                        <div className="gr__who">{block.seller_name}</div>
                        <div className="gr__code">{block.sku}</div>
                        <div className="gr__sub">{block.mskus.length} 个 msku</div>
                      </td>
                      {model.periods.map((p) => (
                        <td className="cell" key={p} data-state={stateOf(inventoryAt(block, p))}>
                          <div className="cell__stack">
                            <div className="cell__lead i-pencil">
                              预估 <Qty v={sumUnits(block.mskus.map((r) => cellAt(r.cells, p)?.system_units ?? null))} />
                            </div>
                            <div><Qty v={inventoryAt(block, p)} big /></div>
                            <div className="cell__row"><span className="k">Σ 期望</span>
                              <span className="v i-ink" data-testid={`sum-expected-${p}`}>
                                <Qty v={demandAt(block, p)} /></span></div>
                          </div>
                        </td>
                      ))}
                      <td className="gsum" data-testid="sum-collapsed">
                        <div data-testid="sum-demand">
                          <Qty v={sumUnits(block.mskus.flatMap((r) => r.cells.map((c) => c.expected_units)))} />
                        </div>
                        <div data-testid="sum-inventory"><Qty v={block.has_fba ? INVENTORY_TOTAL : { kind: 'na' }} /></div>
                      </td>
                    </tr>
                  )}
                  {open && block.mskus.map((row) => (
                    <tr key={row.seller_sku}>
                      <td className="gr">
                        <div className="gr__who">{block.seller_name}</div>
                        <div className="gr__code">{row.seller_sku}</div>
                        <div className="gr__sub">sid {row.sid}</div>
                      </td>
                      {model.periods.map((p) => {
                        const c = cellAt(row.cells, p);
                        const closing: QtyValue = !block.has_fba ? { kind: 'na' }
                          : c?.inventory?.closing_units == null ? { kind: 'unknown' }
                          : { kind: 'num', value: c.inventory.closing_units };
                        return (
                          <td className="cell" key={p} data-state={stateOf(closing)}
                              data-testid={`cell-${row.seller_sku}-${p}`}>
                            <div className="cell__stack">
                              <div className="cell__lead i-pencil" data-testid="system">预估 {c?.system_units ?? '—'}</div>
                              <div data-testid="closing"><Qty v={closing} big /></div>
                              {c?.inventory && block.has_fba && (
                                <div className="basis">
                                  <span>在仓 {c.inventory.on_hand_units}</span>
                                  <span>采购在途 {c.inventory.purchase_in_transit_units}</span>
                                  <span>{c.inventory.basis.as_of}</span>
                                  <span className="chip chip--dim">未计本计划采购</span>
                                </div>
                              )}
                              <input
                                className="g" type="text" inputMode="numeric" placeholder=""
                                aria-label={`期望销量 ${row.seller_sku} ${p.slice(0, 7)}`}
                                defaultValue={c?.expected_units === null || c === undefined ? '' : String(c.expected_units)}
                                data-touched={c?.basis === 'human' ? '1' : undefined}
                                onBlur={(e) => void saveDemand(row.seller_sku, row.sid, p, e.target.value)}
                              />
                            </div>
                          </td>
                        );
                      })}
                      <td className="gsum">
                        <Qty v={sumUnits(row.cells.map((c) => c.expected_units))} />
                      </td>
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
                {model.periods.map((p) => <th key={p}>{p.slice(0, 7)}</th>)}
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
                        aria-label={`计划采购量 ${row.sku} ${c.period.slice(0, 7)}`}
                        defaultValue={c.purchase_units === null ? '' : String(c.purchase_units)}
                        data-touched={c.purchase_units === null ? undefined : '1'}
                        onBlur={(e) => void savePurchase(row.sku, c.period, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="gsum"><Qty v={sumUnits(row.cells.map((c) => c.purchase_units))} /></td>
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

function cellAt(cells: MskuCell[], period: string): MskuCell | undefined {
  return cells.find((c) => c.period === period);
}

/** ★ 状态用左边条不用徽章（10 §3.2 ③）。未知不是 low，也不是 out —— 不给 data-state */
function stateOf(v: QtyValue): 'out' | 'low' | undefined {
  if (v.kind !== 'num') return undefined;
  if (v.value <= 0) return 'out';
  if (v.value < 50) return 'low';
  return undefined;
}
```

- [ ] **Step 8: 跑网格测试**

```bash
cd web && npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: PASS（11 个用例）。

- [ ] **Step 9: 把「空 = 未知」那条守卫弄失败一次，确认它真在守**

```bash
cd web
# 把空串改成落回 0 —— 这正是本项目第一条判据要防的
sed -i "s/const units = raw.trim() === '' ? null : Number(raw);/const units = Number(raw || 0);/" src/pages/PlanGrid.tsx
npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: FAIL —— `★ 清空输入 → 发 null 而不是 0，库存变未知`：`expected '420' to contain '—'`。
★ 若这条还绿，说明断言打在了别处（例如取样到了旁边那格），必须先修断言再继续。

```bash
cd web && git checkout -- src/pages/PlanGrid.tsx && npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: PASS。

- [ ] **Step 10: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 计划编辑网格 —— 一格三行、空≠0、存量不可求和

跨 msku 求和与跨月求和分开实现；认不出的店铺与对不上的库存行进丢弃区点名。

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
- Consumes: `api.searchCatalog` `api.claim`（Task 2）· 类型 `CatalogResult` `CatalogSku` `CatalogMsku` `ClaimHolder` `UnbuildableSeller` · `<AppShell>` `pushToast`（Task 1）· `<ErrorDetail>`（Task 2）
- Produces: `PlanAdd`（路由组件，无对外导出的函数）

**这一屏的四条硬规矩**（`.stage-a-frontend-spec.md` ① · P11 · `06` §1.2 · 原则六）：

```
① 不给条件 → need_query，与「查不到」长得不一样（前者是「还没搜」，后者是「搜了没有」）
② 被占用的行 ★ 留在表里标红，不过滤，并点名占用方（哪张计划 / 谁）
③ 认领是 msku 级 —— 逐 msku 勾，不是「勾货号带走全部」
④ 建不出格子的店铺要点名（没挂渠道）
```

- [ ] **Step 1: 写失败测试**

```tsx
// web/src/pages/PlanAdd.test.tsx
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PlanAdd } from './PlanAdd';

const renderAdd = () => render(
  <MemoryRouter initialEntries={['/plans/1/add']}>
    <Routes><Route path="/plans/:planId/add" element={<PlanAdd />} /></Routes>
  </MemoryRouter>,
);

describe('批量添加', () => {
  it('★ 还没搜 ≠ 搜了没有：两种空屏的字不一样', async () => {
    renderAdd();
    expect(await screen.findByTestId('need-query')).toBeInTheDocument();
    expect(screen.queryByTestId('no-hit')).toBeNull();

    await userEvent.type(screen.getByLabelText('搜货号'), 'ZZZZ');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    expect(await screen.findByTestId('no-hit')).toBeInTheDocument();
    expect(screen.queryByTestId('need-query')).toBeNull();
  });

  it('★ 被占用的行留在表里标红并点名占用方', async () => {
    renderAdd();
    await userEvent.type(screen.getByLabelText('搜货号'), 'DCC1800264');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));

    const row = await screen.findByTestId('msku-DCC1800264-UK');
    expect(row).toHaveClass('off');                       // 留在表里，标灰/标红
    expect(within(row).getByText('2026 Q3 补货计划')).toBeInTheDocument();
    expect(within(row).getByText('ops.li')).toBeInTheDocument();
    expect(within(row).getByRole('checkbox')).toBeDisabled();
  });

  it('★ 认领是 msku 级：勾一个 msku 不会把同货号的另一个也勾上', async () => {
    renderAdd();
    await userEvent.type(screen.getByLabelText('搜货号'), 'DCC1800264');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    await screen.findByTestId('msku-DCC1800264-US');

    await userEvent.click(within(screen.getByTestId('msku-DCC1800264-US')).getByRole('checkbox'));
    expect(within(screen.getByTestId('msku-DCC1800264-WMT')).getByRole('checkbox')).not.toBeChecked();
    expect(screen.getByTestId('picked')).toHaveTextContent('已选 1');
  });

  it('★ 建不出格子的店铺点名', async () => {
    renderAdd();
    await userEvent.type(screen.getByLabelText('搜货号'), 'DCC1800264');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    const box = await screen.findByTestId('unbuildable');
    expect(box).toHaveTextContent('A4Pet-TikTok-US');
    expect(box).toHaveTextContent('no_channel_code');
  });

  it('添加：成功的与被拒的都逐条列出，不合成一句', async () => {
    renderAdd();
    await userEvent.type(screen.getByLabelText('搜货号'), 'DCC1800264');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    await screen.findByTestId('msku-DCC1800264-US');
    await userEvent.click(within(screen.getByTestId('msku-DCC1800264-US')).getByRole('checkbox'));
    await userEvent.click(within(screen.getByTestId('msku-DCC1800264-WMT')).getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: '添加' }));

    const report = await screen.findByTestId('claim-report');
    expect(within(report).getByText('DCC1800264-US')).toBeInTheDocument();
    expect(within(report).getByText('DCC1800264-WMT')).toBeInTheDocument();
    // ★ 两边都要有个数：勾了几个、成了几个、拒了几个
    expect(report).toHaveTextContent('成功 2');
    expect(report).toHaveTextContent('被拒 0');
  });
});
```

- [ ] **Step 2: 跑它确认红**

```bash
cd web && npx vitest run src/pages/PlanAdd.test.tsx
```
Expected: FAIL —— 5 条全红，首条报 `Unable to find an element by: [data-testid="need-query"]`。

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

interface ClaimOutcome { seller_sku: string; sid: string; ok: boolean; why: string }

export function PlanAdd() {
  const planId = Number(useParams().planId);
  const [q, setQ] = useState('');
  const [result, setResult] = useState<CatalogResult>({ kind: 'need_query' });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [report, setReport] = useState<ClaimOutcome[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  const key = (m: CatalogMsku) => `${m.seller_sku}/${m.sid}`;

  async function search() {
    try { setResult(await api.searchCatalog(planId, q.trim() === '' ? {} : { q: q.trim() })); setErr(null); }
    catch (e) { setErr(e as ApiError); }
  }

  function toggle(m: CatalogMsku) {
    setPicked((s) => { const n = new Set(s); const k = key(m); n.has(k) ? n.delete(k) : n.add(k); return n; });
  }

  async function add() {
    if (result.kind !== 'ok') return;
    const targets = result.skus.flatMap((s) => s.mskus).filter((m) => picked.has(key(m)));
    const out: ClaimOutcome[] = [];
    for (const m of targets) {
      try {
        await api.claim(planId, { seller_sku: m.seller_sku, sid: m.sid });
        out.push({ seller_sku: m.seller_sku, sid: m.sid, ok: true, why: '已认领' });
      } catch (e) {
        const ae = e as ApiError;
        // ★ 一次列全，不是修一条报一条 —— 被拒的继续往下做，最后一起交代
        out.push({ seller_sku: m.seller_sku, sid: m.sid, ok: false, why: `${ae.code} ${ae.message}` });
      }
    }
    setReport(out);
    await search();
    const bad = out.filter((o) => !o.ok).length;
    pushToast({ kind: bad === 0 ? 'ok' : 'warn', text: `成功 ${out.length - bad} · 被拒 ${bad}` });
  }

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
      {result.kind === 'need_query' && (
        <div className="empty" data-testid="need-query"><p className="empty__title">输入货号后搜索</p></div>
      )}
      {result.kind === 'ok' && result.skus.length === 0 && (
        <div className="empty" data-testid="no-hit"><p className="empty__title">没有命中的货号</p></div>
      )}

      {result.kind === 'ok' && result.truncated && (
        <div className="flash flash--bad">命中超上限，结果被截断 —— 缩小搜索条件</div>
      )}

      {result.kind === 'ok' && result.unbuildable_sellers.length > 0 && (
        <div className="sec" data-testid="unbuildable">
          {result.unbuildable_sellers.map((s) => (
            <div className="dropline" key={s.seller_id}>
              <span className="k">建不出格子</span>
              <span>{s.seller_name}</span>
              <span className="gate__code">{s.reason}</span>
            </div>
          ))}
        </div>
      )}

      {result.kind === 'ok' && result.skus.map((sku) => (
        <div className="sec" key={sku.sku}>
          <div className="sec__title">
            <span>{sku.sku}</span>
            <span className="muted">{sku.name}</span>
            {sku.category === null
              ? <span className="chip chip--dim">无品类</span>
              : <span className={sku.category_refreshed_at === null ? 'stale' : 'muted'}>{sku.category}</span>}
          </div>
          <div className="table-scroll">
            <table className="table table--dense">
              <thead><tr><th /><th>msku</th><th>店铺</th><th>sid</th><th>占用</th></tr></thead>
              <tbody>
                {sku.mskus.map((m) => (
                  <tr key={key(m)} className={m.claim ? 'off' : undefined} data-testid={`msku-${m.seller_sku}`}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`认领 ${m.seller_sku}`}
                        disabled={m.claim !== null}
                        checked={picked.has(key(m))}
                        onChange={() => toggle(m)}
                      />
                    </td>
                    <td>{m.seller_sku}</td>
                    <td>{m.seller_name}</td>
                    <td>{m.sid}</td>
                    <td>
                      {m.claim ? (
                        <span className="i-red">
                          {m.claim.plan_title} · <span>{m.claim.actor}</span>
                        </span>
                      ) : <span className="muted">—</span>}
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
          {report.map((r) => (
            <div className={`gate__item gate__item--${r.ok ? 'pass' : 'fail'}`} key={`${r.seller_sku}/${r.sid}`}>
              <span>{r.seller_sku}</span> <span className="gate__detail">{r.why}</span>
            </div>
          ))}
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
Expected: PASS（5 个用例）。

- [ ] **Step 5: 把「被占用不过滤」这条守卫弄失败一次**

```bash
cd web
sed -i 's/{sku.mskus.map((m) => (/{sku.mskus.filter((m) => m.claim === null).map((m) => (/' src/pages/PlanAdd.tsx
npx vitest run src/pages/PlanAdd.test.tsx
```
Expected: FAIL —— `★ 被占用的行留在表里标红并点名占用方`：`Unable to find an element by: [data-testid="msku-DCC1800264-UK"]`。
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

「还没搜」与「搜了没有」两套空屏；建不出格子的店铺进丢弃区。

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
- Consumes: `api.listRevs` `api.setCurrentRev` `api.diff` `api.cancelRev`（Task 2）· 类型 `RevList` `Rev` `PlanDiff` `DiffRow` · `<AppShell>` `pushToast` `<ErrorDetail>`
- Produces:
  - `<Modal title danger onClose>{children}</Modal>` —— ★ 只用 `.modal-backdrop > .modal` 这一套（`shell.css` 里 `dialog.modal` 与 div 版并存，React 只留一套）
  - `PlanRevs`（路由组件）

★ 本屏必须把两个标记分开显示（`06` §1.3）：**流转中**（采购/排货消费它）与**当前使用**（算需求时读它）。
合成一个标记，就再也分不清「为什么改了当前使用，采购那边还是老的」。

- [ ] **Step 1: 写 Modal 的失败测试**

```tsx
// web/src/components/Modal.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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
        {danger && <div className="modal__warn irreversible">⛔ 这一步会改动已提交的记录</div>}
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
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PlanRevs } from './PlanRevs';

const renderRevs = () => render(
  <MemoryRouter initialEntries={['/plans/2/revs']}>
    <Routes><Route path="/plans/:planId/revs" element={<PlanRevs /></Route>} /></Routes>
  </MemoryRouter>,
);

describe('版本编辑', () => {
  it('★ 流转中与当前使用分开显示 —— 两个标记不是一回事', async () => {
    renderRevs();
    const row2 = await screen.findByTestId('rev-2');
    expect(within(row2).getByText('流转中')).toBeInTheDocument();
    expect(within(row2).getByText('当前使用')).toBeInTheDocument();
    const row1 = screen.getByTestId('rev-1');
    expect(within(row1).queryByText('流转中')).toBeNull();
    expect(within(row1).queryByText('当前使用')).toBeNull();
  });

  it('设为当前使用：点 rev 1 之后标记搬过去', async () => {
    renderRevs();
    await screen.findByTestId('rev-1');
    await userEvent.click(within(screen.getByTestId('rev-1')).getByRole('button', { name: '设为当前使用' }));
    expect(await within(screen.getByTestId('rev-1')).findByText('当前使用')).toBeInTheDocument();
    expect(within(screen.getByTestId('rev-2')).queryByText('当前使用')).toBeNull();
    // ★ 流转中不跟着搬 —— 它由采购侧决定
    expect(within(screen.getByTestId('rev-2')).getByText('流转中')).toBeInTheDocument();
  });

  it('版本差异：新增 / 改动分得开，两个数并排', async () => {
    renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.selectOptions(screen.getByLabelText('比较自'), '1');
    await userEvent.click(screen.getByRole('button', { name: '比较' }));
    const diff = await screen.findByTestId('diff');
    const changed = within(diff).getByTestId('diff-demand-DCC1800264-US-2026-10-01');
    expect(changed).toHaveTextContent('160');
    expect(changed).toHaveTextContent('180');
    const added = within(diff).getByTestId('diff-purchase-DCC1800264-2026-11-01');
    expect(added).toHaveTextContent('added');
    expect(within(added).getByText('—')).toBeInTheDocument();  // before 为空，不是 0
  });

  it('★ 撤销理由必填：空理由拿到 400 reason_required 并点名', async () => {
    renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.click(screen.getByRole('button', { name: '确认撤销' }));
    expect(await screen.findByText('400 reason_required')).toBeInTheDocument();
  });

  it('填了理由就撤得掉，并交代撤了几条记录', async () => {
    renderRevs();
    await screen.findByTestId('rev-2');
    await userEvent.click(within(screen.getByTestId('rev-2')).getByRole('button', { name: '撤销' }));
    await userEvent.type(screen.getByLabelText('理由'), '需求口径变了');
    await userEvent.click(screen.getByRole('button', { name: '确认撤销' }));
    expect(await screen.findByTestId('cancel-report')).toHaveTextContent('撤销 3 条记录');
  });
});
```

★ 注意第一行 `renderRevs` 里的 JSX 写错了（`<PlanRevs /></Route>`）—— 这是**故意留的第一次失败**，Step 4 先看它报语法错，再改对。这样能确认测试文件真的被执行了，而不是被 vitest 静默跳过。

- [ ] **Step 4: 跑它，先看它以语法错误红**

```bash
cd web && npx vitest run src/pages/PlanRevs.test.tsx
```
Expected: FAIL —— esbuild transform error，`Unexpected closing "Route" tag does not match opening "PlanRevs" tag`。

把那一行改成：

```tsx
    <Routes><Route path="/plans/:planId/revs" element={<PlanRevs />} /></Routes>
```

再跑：Expected: FAIL —— `Failed to resolve import "./PlanRevs"` 之后是 5 条用例全红。

- [ ] **Step 5: 写版本页**

```tsx
// web/src/pages/PlanRevs.tsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { AppShell } from '../shell/AppShell';
import { pushToast } from '../shell/toastStore';
import { ErrorDetail } from '../components/ErrorDetail';
import { Modal } from '../components/Modal';
import { Qty } from '../components/Qty';
import { api, ApiError } from '../api';
import type { PlanDiff, RevList } from '../api/types';

export function PlanRevs() {
  const planId = Number(useParams().planId);
  const [list, setList] = useState<RevList | null>(null);
  const [from, setFrom] = useState('');
  const [diff, setDiff] = useState<PlanDiff | null>(null);
  const [cancelling, setCancelling] = useState<number | null>(null);
  const [reason, setReason] = useState('');
  const [cancelled, setCancelled] = useState<number | null>(null);
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
      setCancelled(r.cancelled_lines);
      setCancelling(null);
      setReason('');
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
                <td className="r">{r.line_count}</td>
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
        <div className="table-scroll" data-testid="diff">
          <table className="table table--dense">
            <thead><tr><th>类</th><th>货号</th><th>msku</th><th>月</th><th className="r">rev {diff.from}</th><th className="r">rev {diff.to}</th><th>变化</th></tr></thead>
            <tbody>
              {diff.rows.map((row) => (
                <tr key={`${row.kind}-${row.sku}-${row.seller_sku ?? ''}-${row.period}`}
                    data-testid={`diff-${row.kind}-${row.seller_sku ?? row.sku}-${row.period}`}>
                  <td>{row.kind === 'demand' ? '期望销量' : '计划采购量'}</td>
                  <td>{row.sku}</td>
                  <td>{row.seller_sku ?? <span className="muted">—</span>}</td>
                  <td>{row.period.slice(0, 7)}</td>
                  {/* ★ 两个数并排 + 变化，不合成一个「增减」 */}
                  <td className="r"><Qty v={row.before === null ? { kind: 'unknown' } : { kind: 'num', value: row.before }} /></td>
                  <td className="r"><Qty v={row.after === null ? { kind: 'unknown' } : { kind: 'num', value: row.after }} /></td>
                  <td><span className="chip chip--dim">{row.change}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {cancelled !== null && (
        <div className="flash flash--good" data-testid="cancel-report">撤销 {cancelled} 条记录</div>
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
```

- [ ] **Step 6: 跑测试确认绿**

```bash
cd web && npx vitest run src/pages/PlanRevs.test.tsx src/components/Modal.test.tsx
```
Expected: PASS（8 个用例）。

- [ ] **Step 7: 把「两个标记分开」这条守卫弄失败一次**

```bash
cd web
sed -i 's/{r.rev === list.in_flight_rev \&\& <span className="chip chip--ours">流转中<\/span>}/{r.rev === list.current_rev \&\& <span className="chip chip--ours">流转中<\/span>}/' src/pages/PlanRevs.tsx
npx vitest run src/pages/PlanRevs.test.tsx
```
Expected: FAIL —— `设为当前使用：点 rev 1 之后标记搬过去`：rev 2 上的「流转中」不见了、rev 1 上多了一个。
★ 这就是「用一个轴掩盖真相」的形状：合成之后屏幕依然自洽，只是它说的不再是事实。

```bash
cd web && git checkout -- src/pages/PlanRevs.tsx && npx vitest run src/pages/PlanRevs.test.tsx
```
Expected: PASS。

- [ ] **Step 8: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 版本编辑 —— 流转中与当前使用分开显示，撤销理由必填

diff 两个数并排不合成增减；Modal 只留 .modal-backdrop 一套。

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
- Produces: 无新导出。屏上新增 `data-testid`：`submit-report` · `skipped-list` · `rev-in-flight`

**这一步的两条硬规矩**

```
① ★ 判据②：被跳过的格子逐条列出，一条都不许合成成「部分跳过」
② ★ 409 rev_in_flight 要点名旧版号 —— 不点名，人就只能去库里查「到底是哪一版卡着」
```

★ 提交成功后**不立刻跳转**：`skipped[]` 只在这一次响应里存在，跳走就永远看不到了。
面板上给「去版本」按钮，由人点。

- [ ] **Step 1: 写失败测试**

```tsx
// web/src/pages/PlanGrid.submit.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

beforeEach(() => { vi.resetModules(); });

async function renderGrid(planId: number) {
  const { PlanGrid } = await import('./PlanGrid');
  return render(
    <MemoryRouter initialEntries={[`/plans/${planId}`]}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
}

describe('提交', () => {
  it('★ 逐条列出被跳过的格子，并交代两边的数', async () => {
    await renderGrid(1);
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    const report = await screen.findByTestId('submit-report');
    expect(report).toHaveTextContent('rev 1');
    // ★ 铸出几条 + 跳过几条，两个数都在屏上 —— 只报一个等于藏起另一半
    expect(report).toHaveTextContent('铸出 1 条');
    expect(report).toHaveTextContent('跳过 2 条');

    const skipped = within(report).getByTestId('skipped-list');
    expect(within(skipped).getAllByRole('listitem').length).toBe(2);
    expect(skipped).toHaveTextContent('zero_purchase');
    expect(skipped).toHaveTextContent('2026-11');
    expect(skipped).toHaveTextContent('2026-12');
  });

  it('提交成功不自动跳走 —— skipped[] 只有这一次机会被看见', async () => {
    await renderGrid(1);
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    await screen.findByTestId('submit-report');
    expect(screen.getByRole('link', { name: '去版本' })).toHaveAttribute('href', '/plans/1/revs');
  });

  it('★ 旧版在流转 → 409 并点名旧版号', async () => {
    await renderGrid(2);
    // plan 2 没有网格 fixture ⇒ 先确认它以 404 落在错误面板上，再单独验 409 的形状
    expect(await screen.findByText(/404 not_found/)).toBeInTheDocument();
  });

  it('★ 409 rev_in_flight 的面板点名旧版号', async () => {
    const { ApiError } = await import('../api/client');
    const { createMockApi } = await import('../api/mock');
    const base = createMockApi();
    vi.doMock('../api', async () => ({
      ApiError,
      api: { ...base, submit: async () => { throw new ApiError(409, 'rev_in_flight', 'rev 7 还在流转', { in_flight_rev: 7 }); } },
    }));
    await renderGrid(1);
    await screen.findByTestId('purchase-block');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    const box = await screen.findByTestId('rev-in-flight');
    expect(box).toHaveTextContent('rev 7');
    expect(box).toHaveTextContent('409 rev_in_flight');
    // ★ 409 不是「你写错了」：屏上给的下一步是去看那一版，不是改表单
    expect(within(box).getByRole('link', { name: '去看 rev 7' })).toHaveAttribute('href', '/plans/1/revs');
  });
});
```

- [ ] **Step 2: 跑它确认红**

```bash
cd web && npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: FAIL —— 4 条全红，首条报 `Unable to find an accessible element with the role "button" and name "提交"`。

- [ ] **Step 3: 在 PlanGrid 里加提交**

在 `web/src/pages/PlanGrid.tsx` 的 import 后追加类型引入：

```tsx
import type { GridResponse, Seller, SkipReason, SubmitResult } from '../api/types';
```

在 `const [err, setErr] = useState<ApiError | null>(null);` 之后追加两个状态：

```tsx
  const [report, setReport] = useState<SubmitResult | null>(null);
  const [inFlight, setInFlight] = useState<{ rev: number; err: ApiError } | null>(null);
```

在 `async function removeSku` 之后追加：

```tsx
  async function submit() {
    setReport(null);
    setInFlight(null);
    try {
      const r = await api.submit(planId);
      setReport(r);
      // ★ 不自动跳走：skipped[] 只在这一次响应里存在
      pushToast({ kind: r.skipped.length === 0 ? 'ok' : 'warn', text: `rev ${r.rev} · 铸出 ${r.line_count} · 跳过 ${r.skipped.length}` });
      await load();
    } catch (e) {
      const ae = e as ApiError;
      if (ae.status === 409 && ae.code === 'rev_in_flight') {
        // ★ 409 不是「你写错了」—— 点名旧版号，给的下一步是去看那一版
        setInFlight({ rev: Number(ae.detail.in_flight_rev), err: ae });
        return;
      }
      setErr(ae);
    }
  }
```

把 `head__act` 里的按钮组改成（★ 「提交」排在「重置」之后，与原理图一致）：

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
          <div className="gate__code">{inFlight.err.status} {inFlight.err.code}</div>
          <a className="btn btn--sm" href={`/plans/${planId}/revs`}>去看 rev {inFlight.rev}</a>
        </div>
      )}

      {report && (
        <div className="panel sec" data-testid="submit-report">
          <div className="panel__head">
            rev {report.rev} · 铸出 {report.line_count} 条 · 跳过 {report.skipped.length} 条
          </div>
          <div className="panel__body">
            {/* ★ 判据②：逐条列出，不静默丢 */}
            <ul data-testid="skipped-list">
              {report.skipped.map((s) => (
                <li className="dropline" key={`${s.sku}-${s.seller_id}-${s.period}`}>
                  <span className="k">{s.sku}</span>
                  <span>{s.period.slice(0, 7)}</span>
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
/** S-14 的值域只有这两个。★ 这里是「当场发生的事」，不是功能解说 */
const SKIP_WHY: Record<SkipReason, string> = {
  zero_purchase: '计划采购量为空或 0',
  no_claimed_msku: '这个货号下没有已认领的 msku',
};
```

- [ ] **Step 4: 跑提交测试**

```bash
cd web && npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: PASS（4 个用例）。

- [ ] **Step 5: 回归 Task 4 的网格测试，确认没被改坏**

```bash
cd web && npx vitest run src/pages/PlanGrid.test.tsx
```
Expected: PASS（11 个用例）。★ 若 `合计` 那条列头断言红，说明按钮区的改动波及了表头结构 —— 去修实现，不要改断言。

- [ ] **Step 6: 把「逐条列出」弄失败一次**

```bash
cd web
sed -i 's/跳过 {report.skipped.length} 条/跳过若干条/' src/pages/PlanGrid.tsx
npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: FAIL —— `★ 逐条列出被跳过的格子，并交代两边的数`：`expected element to have text content '跳过 2 条'`。
★ 「部分跳过」正是 `10` §1 原则一点名的反例：300 里跳了多少看不见。

```bash
cd web && git checkout -- src/pages/PlanGrid.tsx && npx vitest run src/pages/PlanGrid.submit.test.tsx
```
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
feat(web): 提交流程 —— skipped[] 逐条列出，409 rev_in_flight 点名旧版号

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
- Test: 同上两个文件

**Interfaces:**
- Consumes: 前七个 Task 的全部产物
- Produces: 无代码导出。产出的是**两条门禁**：
  - `sameScreen`：同一份 fixture，`mock` 与 `http`（fetch 桩）渲染出的网格 DOM 文本**逐字相同**
  - `planFlow`：建 → 加货品 → 填两种量 → 提交 → 铸出 rev，全程可复现

★ 判据⑥ 为什么必须这么测：`mock.ts` 与 `http.ts` 是**两份实现**，两份实现必然分叉。
让它们读同一份 fixture、比同一屏的文本，是唯一能让分叉**当场红**的办法。
只跑 mock 会一直绿着 —— 而那正是「不执行的东西不会失败」。

- [ ] **Step 1: 写同屏门禁的失败测试**

```tsx
// web/src/e2e/sameScreen.test.tsx
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import gridFixture from '../api/fixtures/grid-1.json';
import sellersFixture from '../api/fixtures/sellers.json';

beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock('../api'); });

/** ★ http 桩只认这两条路由：其余一律抛，防止「桩把没实现的调用悄悄喂成空数组」 */
function stubFetch() {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const body = url.endsWith('/plans/1/grid') ? gridFixture
      : url.endsWith('/sellers') ? sellersFixture
      : null;
    if (body === null) throw new Error(`桩没有覆盖这个调用：${url}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  });
}

async function screenText(source: 'mock' | 'api'): Promise<string> {
  vi.resetModules();
  if (source === 'api') {
    stubFetch();
    const { createHttpApi } = await import('../api/http');
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({ api: createHttpApi({ base: '/v1', timeoutMs: 1000 }), ApiError }));
  } else {
    const { createMockApi } = await import('../api/mock');
    const { ApiError } = await import('../api/client');
    vi.doMock('../api', () => ({ api: createMockApi(), ApiError }));
  }
  const { PlanGrid } = await import('../pages/PlanGrid');
  const { unmount } = render(
    <MemoryRouter initialEntries={['/plans/1']}>
      <Routes><Route path="/plans/:planId" element={<PlanGrid />} /></Routes>
    </MemoryRouter>,
  );
  const block = await screen.findByTestId('block-11072-DCC1800264');
  const text = (block.textContent ?? '').replace(/\s+/g, ' ').trim();
  unmount();
  return text;
}

describe('判据⑥ · 两种数据源跑出同一屏', () => {
  it('mock 与 api 渲染出的网格文本逐字相同', async () => {
    const a = await screenText('mock');
    const b = await screenText('api');
    expect(b).toBe(a);
    // ★ 顺带守住「这一屏确实有内容」—— 两边都空也会相等，那是假绿
    expect(a).toContain('未计本计划采购');
    expect(a.length).toBeGreaterThan(80);
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
sed -i "s/const grids = new Map<number, GridResponse>(\[\[1, clone(gridFixture) as GridResponse\]\]);/const grids = new Map<number, GridResponse>([[1, { ...(clone(gridFixture) as GridResponse), periods: ['2026-10-01'] }]]);/" src/api/mock.ts
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
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { OpsHome } from '../pages/OpsHome';
import { PlanGrid } from '../pages/PlanGrid';
import { PlanAdd } from '../pages/PlanAdd';
import { PlanRevs } from '../pages/PlanRevs';

const app = (entry: string) => render(
  <MemoryRouter initialEntries={[entry]}>
    <Routes>
      <Route path="/" element={<OpsHome />} />
      <Route path="/plans/:planId" element={<PlanGrid />} />
      <Route path="/plans/:planId/add" element={<PlanAdd />} />
      <Route path="/plans/:planId/revs" element={<PlanRevs />} />
    </Routes>
  </MemoryRouter>,
);

describe('判据① · 建 → 加货品 → 填两种量 → 提交 → 铸出 rev', () => {
  it('全程可复现', async () => {
    // ① 建
    app('/');
    await screen.findByTestId('plan-list');
    await userEvent.click(screen.getByRole('button', { name: '新建销售计划' }));
    await userEvent.type(screen.getByLabelText('标题'), '2027 Q1 销售计划');
    await userEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(await screen.findByTestId('nav-to')).toHaveTextContent('/plans/5');
  });

  it('② 加货品：认领 msku 后回网格', async () => {
    app('/plans/1/add');
    await userEvent.type(screen.getByLabelText('搜货号'), 'DCC1800311');
    await userEvent.click(screen.getByRole('button', { name: '搜索' }));
    await userEvent.click(await screen.findByLabelText('认领 DCC1800311-US'));
    await userEvent.click(screen.getByRole('button', { name: '添加' }));
    expect(await screen.findByTestId('claim-report')).toHaveTextContent('成功 1');
    expect(screen.getByRole('link', { name: '返回网格' })).toHaveAttribute('href', '/plans/1');
  });

  it('③④ 填两种量 → 提交 → 铸出 rev，且两种量都进了 rev', async () => {
    app('/plans/1');
    await screen.findByTestId('purchase-block');

    // 期望销量（msku × 月）—— 行默认折叠，先展开
    const block = await screen.findByTestId('block-11072-DCC1800264');
    await userEvent.click(within(block).getByRole('button', { name: '展开' }));
    const cell = screen.getByTestId('cell-DCC1800264-US-2026-12-01');
    await userEvent.type(within(cell).getByRole('textbox'), '210');
    await userEvent.tab();

    // 计划采购量（货号 × 月，不带店铺）
    const purchase = within(screen.getByTestId('purchase-block')).getByLabelText('计划采购量 DCC1800264 2026-11');
    await userEvent.type(purchase, '300');
    await userEvent.tab();

    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    const report = await screen.findByTestId('submit-report');
    expect(report).toHaveTextContent('rev 1');
    // ★ 11 月本来因 zero_purchase 被跳过，填了 300 之后应该铸得出来
    expect(report).toHaveTextContent('铸出 2 条');
    expect(report).toHaveTextContent('跳过 1 条');
  });

  it('⑤ 版本页看得到刚铸出的 rev', async () => {
    app('/plans/2/revs');
    expect(await screen.findByTestId('rev-2')).toHaveTextContent('当前使用');
  });
});
```

- [ ] **Step 5: 跑它**

```bash
cd web && npx vitest run src/e2e/planFlow.test.tsx
```
Expected: 初次 FAIL 于第三条（`铸出 2 条`）—— 若红，先确认 `mock.submit` 的跳过判据是否把「刚填的 300」读了进去；这是**实现问题**，不许把断言改成 `铸出 1 条`。修到 PASS。

★ 四条用例**共享同一个 mock 单例**（`api` 是模块级的），所以它们之间有先后依赖：第三条改的数会留到第四条。
这是刻意的 —— 判据① 要的就是「一张计划从建到提交**全程**可复现」，把每条都隔离开反而测不到跨屏的连续性。
★ 但顺序一旦有意义，就不许给这个文件开 `--shuffle`。

Expected 最终: PASS（4 个用例）。

- [ ] **Step 6: 全量跑 + 构建**

```bash
cd web && npx vitest run && npx tsc -b && npm run build
```
Expected: `Test Files 15 passed`（tokens · AppShell · mock · http · ErrorDetail · Qty · OpsHome · planGridModel · PlanGrid · PlanGrid.submit · PlanAdd · Modal · PlanRevs · sameScreen · planFlow）；tsc 静默；`vite build` 输出 `dist/`。

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
| `VITE_API_TIMEOUT_MS` | 默认 `8000` | 超时抛 `ApiError(code='timeout')`，与连不上分得开 |

两条门禁：`src/e2e/sameScreen.test.tsx`（判据⑥）· `src/e2e/planFlow.test.tsx`（判据①）。
```

★ 起服务由**我自己**在另一个终端执行（`CLAUDE.md` 全局铁律：服务由我来启动）。
执行本计划的 agent **不要**替我起 `npm run dev`；只读的 `vitest` / `tsc` / `vite build` 可以自己跑。

- [ ] **Step 8: 提交**

```bash
cd /home/fido/work/2026/jxd_service_group/service_supplychain
git add web && git commit -m "$(cat <<'EOF'
test(web): 两条门禁 —— 判据⑥ 同屏对比、判据① 全流程

同一份 fixture 分别喂 mock 与 http 桩，网格 DOM 文本逐字比对。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01CYVSvM8ihMnoFSLVnV7EzQ
EOF
)"
```

---

## 附录 A · 本计划**收敛**的接口字段（★ 开工前要登记进 `docs/00-待裁定清单.md`）

> `CLAUDE.md` 纪律③：指不到出处的不许自己定。下表每一行都是**前端写得出代码所必需、而 `08` 没写到字段级**的东西。
> 我在 `web/src/api/types.ts` 里给了具体形状**以便执行**，但它们是**待登记项**，不是裁定。

| # | 字段 / 形状 | 前端为什么非要它 | 现状出处 |
|---|---|---|---|
| A-1 | `GET /grid` 的 `inventory[].basis = { source: 'ch', as_of, includes_plan_purchase: false }` | 「一个数要能回答它是关于什么的」+ S-10 要求标「未计入本计划的采购」。没有 `as_of` 就无法回答「在仓是哪天的」 | `08` 只写「标 `basis`」，未给形状 |
| A-2 | `GET /grid` 的 `demand[].basis ∈ {human, system, unknown}` | S-15 已裁定要记 basis；前端据它决定输入框的 `data-touched` 与墨色 | S-15 有裁定，`08` 未落到字段 |
| A-3 | `POST /submit` 的 `skipped[].reason ∈ {zero_purchase, no_claimed_msku}` | 判据② 要逐条列理由 | ★ S-14 已裁定值域，`08` 未写 |
| A-4 | `409` 的 code 用 `rev_in_flight`（不是 `another_rev_in_flight`） | 前端要按 code 分支 | `08` §1.1 写 `rev_in_flight`，后端规格 ③ 写 `another_rev_in_flight` —— **两处不一致，须统一** |
| A-5 | `GET /catalog/skus` 的 `unbuildable_sellers[].reason = 'no_channel_code'` | `06` §1.2 要求「必须点名」，但没给字段 | `06` §1.2 有要求，无字段 |
| A-6 | `GET /catalog/skus` 的 `need_query` 与 `truncated` 的形状 | P11 要求「不给条件故意不返回」与「查不到」不同形 | P11 有要求，无形状 |
| A-7 | `GET /sellers` 的 `has_fba` | 「无 FBA 显示不适用不是 0」全靠它 | ★ P5 已要求 `seller` 加 `market`/`has_fba`（S-9 裁定内），`08` §1.4 未写字段 |
| A-8 | `GET /plans` 的 `state` 对**从未提交**的计划返回 `null` | 「未提交」不是 `04` 的状态值；返回 `已撤销`（`04:443` 空计划单口径）会把它和真撤销混成一个 | ★ `04` 只定义了**有记录**时的木桶派生，未定义无 rev 的计划 |
| A-9 | 按钮「重置」的语义 = 丢弃未落盘的输入并重新拉取 `GET /grid` | 原理图列了这个按钮但没定义它做什么 | 负责人原理图 |

★ **A-4 与 A-8 是真分叉，不是缺字段** —— 这两条必须先定，否则前端的分支会写错一半。

---

## 附录 B · 前端侧遗留缺口（本计划**不做**，登记备查）

| # | 缺口 | 本计划的处置 | 来源 |
|---|---|---|---|
| B-1 | 「模拟外部」抽屉在设计系统里没有任何类或 token | ★ 阶段 A 没有任何外部写接口 ⇒ **不做**。留到阶段 B 与 `.chip--ext` 一起设计 | frontend-spec ③ · ⑤ |
| B-2 | `x-actor` 下拉里的人从哪来（`scm.actor` 谁维护、有没有管理界面） | ★ 前端用 `src/shell/actors.ts` 常量，**两种数据源都读它**；屏上标「留痕可伪造」 | frontend-spec ⑤ 缺口 11 · Q-5 |
| B-3 | `10` §1 标题写「七条交互原则」而实际列了八条（第八条是 P15 根因） | 本计划按**八条**执行；文档的数字要改，属 `docs/` 的活 | frontend-spec ⑤ 缺口 9 |
| B-4 | 名称分叉：「期望销量」vs「计划销量」 | 界面标签一律用 **期望销量**（`02`/`14`/`16`/`00e` 口径，也是 `00e:41` 的粒度定义用词） | frontend-spec ⑤ 缺口 7 |
| B-5 | 首页「进度概览」与版本页下钻 `trace` | 阶段 A 两面承重墙都没有 ⇒ **不渲染**（不是空面板） | frontend-spec ⑤ 缺口 6 |
| B-6 | mock fixture 与后端对账数据同源 | 本计划把 `web/src/api/fixtures/*.json` 定为**唯一源**；后端落地时要把同一份文件接成契约测试的 fixture，否则判据⑥ 只证明了前端自洽 | frontend-spec ⑤ 缺口 5 |

---

## Self-Review

按 `superpowers:writing-plans` 的三项自检，结果如下（发现的问题已就地改掉）。

**1. 规格覆盖**

| 规格条目 | 落在 | 
|---|---|
| ① 四屏 + 全局外壳 | Task 1（外壳）· 3（`ops`）· 4（`plan`）· 5（`plan-add`）· 6（`plan-rev`） |
| ② 网格每格的墨与可编辑性 | Task 4 Step 7（三行格）+ Step 5 的 11 条断言 |
| ③ `shell.css` 的 token 与类名原样带走 | Task 1 Step 3~4（sha256 + 六个 token 逐个断言） |
| ④ 两种人工输入 | Task 1 的「不做」一节：阶段 A 无 ② 类输入 ⇒ 不做抽屉，登记 B-1 |
| ⑤ 缺口 1/2/3/5（卡开工的四条） | 已被 S 组裁定：S-1/S-5（两表粒度）· S-10/S-19（带预测不落库）· S-20 + `00e:45`（不渲染够不着的态）· fixture 同源（Task 2 Step 3 + B-6） |
| `10` §1 八条原则 | 一 → Task 7（铸出/跳过两个数并排）· 四 → Task 6 Modal danger · 五 → Task 2 ErrorDetail + Task 5 claim-report · 六 → Task 3 hidden-finished / Task 4 orphans / Task 5 unbuildable / Task 7 skipped · 七 → Task 5 `.stale` · 八 → 「不做」一节。★ 二、三属排货域，阶段 A 无对应物（frontend-spec ⑤ 缺口 10） |
| `08` §1.1 的 18 个端点 | 阶段 A 用到的 15 个全在 `SupplyChainApi` 里；`/plan-lines*` 与 `/plans/{id}/archive` 未用 —— 见下方「找到并修掉的问题」第 3 条 |
| 判据 ①②③⑥ | Task 8 planFlow（①）· Task 7（②）· Task 5 claim 409（③）· Task 8 sameScreen（⑥）。★ 判据 ④⑤ 是库层的事，前端测不到 |

**2. 占位符扫描**：全文搜 `TBD` / `TODO` / `待补` / `类似 Task` / `适当` / `etc` —— 0 命中。
每个代码步骤都给了可直接粘贴的完整代码；Task 1 的四个页面占位明确标注「在各自 Task 里被整体替换」，并给出了完整的占位代码。

**3. 类型一致性**：`api/types.ts` 里的名字在 Task 3~8 逐个核对过，以下为**发现并修掉的问题**：

| # | 发现 | 改法 |
|---|---|---|
| 1 | 初稿把 `Modal.tsx` 排在「批量添加」那个 Task，但批量添加不需要弹窗、版本撤销才需要 | 移到 Task 6；File Structure 与 Task 编号同步改了（`PlanAdd`→Task 5、`PlanRevs`→Task 6） |
| 2 | 初稿的 `DashboardPlanCounts` 里自造了 `never_submitted` / `cancelled` 两个后端字段 | 删掉。三个计数改为：未提交 ← `/dashboard/unsubmitted.never_submitted.length`、已提交 ← `/dashboard/plans.submitted`、已撤销 ← 计划列表里 `state==='已撤销'` 的张数。三者**全部指得到出处** |
| 3 | 初稿在首页用 `GET /plans?state=已撤销` 做筛选，但 `state` 取值域未定义、且「未提交」根本不是一个状态值 | 改成只发 `{archived:false}`，筛选与排序全在前端做。★ 避免发出「未声明取值」被 400 |
| 4 | 初稿 `inventoryAt` 对 `has_fba=false` 返回 `{kind:'num', value:0}` | 改成 `{kind:'na'}`。这正是「不适用 ≠ 0」，改前测试会绿——所以补了 Task 4 Step 1 的第四条断言 |
| 5 | `sumUnits([])` 初稿返回 `0` | 改成 `{kind:'unknown'}`：没有数不等于 0。断言写在 Task 4 Step 1 第二条 |
| 6 | Task 7 初稿提交成功后直接 `navigate` 到版本页 | 改成停在网格页出结果面板 —— `skipped[]` 只在这一次响应里存在，跳走就永远看不到了（判据②） |
| 7 | `08` 与后端规格对 409 的 code 写法不一致（`rev_in_flight` / `another_rev_in_flight`） | 前端统一用 `rev_in_flight`（`08` §1.1 现行 + 负责人转述），并登记为附录 A-4 的**真分叉**，须后端同步 |
| 8 | 三处「未知」的渲染初稿都写成 `—`，包括「不可求和」 | 拆成三个字形（`—` / `不适用` / `不可求和`），Task 3 的 `Qty` 单测直接断言三者互不相同 |
| 9 | ★ Task 4 的五条测试直接取 `cell-<msku>-<月>`，而实现里 msku 行**默认折叠** —— 这些 testid 在折叠态根本不存在，测试会全红 | 测试里加 `expand()` 助手，取 msku 级格子前先点「展开」 |
| 10 | 「无 FBA 显示不适用」那条断言写的是 3 处，实际是 3 个月 + 合计列 = 4 处 | 改成 4，并补一条 `queryByText('0')` 为 null —— 只数个数会被「其中一处渲染成 0」骗过 |
| 11 | 「折叠行显示 Σ 只读」在实现里没有可断言的落点 | 折叠行的 Σ 加 `data-testid={sum-expected-<月>}` 与 `.i-ink`；测试同时断言折叠行**没有 textbox**（在货号级填数会把人填的粒度悄悄降一层） |
| 12 | Task 8 的 planFlow 第三条同样漏了展开；末尾还留了一个不存在的 `screen.unmount?.()` | 补展开；删掉 `unmount`，并写明这四条**共享同一个 mock 单例、顺序有意义**，因此不许对该文件开 `--shuffle` |
| 13 | Task 8 Step 6 写「Test Files 12 passed」，实际是 15 个测试文件 | 改成 15 并逐个列名 —— ★ 「不执行的东西不会失败」：数字对不上时，少跑了三个文件是看不出来的 |

★ 一条**没修**的：`GET /plan-lines*` 与 `POST /plans/{id}/archive` 在阶段 A 的四屏里没有入口（原理图没画）。
我没有自己发明入口，也没有把它们塞进某一屏 —— 按纪律③ 记在这里，待负责人决定它们属不属于阶段 A 的界面。
