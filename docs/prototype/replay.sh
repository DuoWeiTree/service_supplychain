#!/bin/bash
# ============================================================
#  变异回放器 —— 三个页面 agent 共用一套闸
#
#  ★ 为什么要有这个文件：
#    变异证据的用处是回答一句话 ——「这条断言当初是被什么打红的」。
#    可它有**四种长得像证据、其实不是证据的形态**，而且都很难一眼看出来：
#
#    ① 全绿型（变异没生效）
#       from 串没匹配上，文件根本没改 → 测试全绿。
#       看上去像「断言太弱」，其实是**什么都没改**。
#
#    ② 连坐型（改动碰倒了别的东西）
#       变异生效了，但顺带打红一批无关断言 → 分不清「靶子中了」还是「旁边塌了」。
#       ★ 这一种要靠人读红的清单，闸挡不住 —— 但闸能保证你读到的是真清单。
#
#    ③ ★ 大片红但没打中目标型（变异把页面打炸了）
#       替换串自己有语法错（最常见：shell 引号把转义吃掉），注进去页面整个不执行
#       → 红的是「网格长出 N 个块」这类**装载级**断言，而**目标断言反而没红**。
#       ★★ 红得越多越像「我的测试很强」，而实际上一条都没打中。
#
#    ④ ★ 测试根本没跑（po-detail 2026-09-18 发现）
#       依赖缺失（jsdom 没装 / NODE_PATH 没设）时测试**明说跳过、exit 0、
#       一条断言都不打**。这时前两道闸全过（文件确实改了、语法也没问题），
#       回放器会打印「全绿 —— 这条路没有断言守着」。
#       ★★ 那是一句**反话**，而且说得斩钉截铁。「没有断言守着」的下一步是
#          去补断言、甚至把那段代码删掉；而真相是断言活得好好的，只是没执行。
#          方向正好反了 —— **一个自信的错误结论比沉默贵得多。**
#       ★ 前两道闸看的是**文件**（改没改、语法对不对），这一种的病灶在**运行**
#         （跑没跑、跑了几条）—— 不同维，所以挡不住，得单独一道。
#
#    三道闸，**拒绝出分时必须说清是哪一道** —— 它们的下一步完全不同：
#
#      闸 1  from 命中数 ≠ 1          → 分开 ①「没生效」与「断言够强」
#      闸 2  变异后脚本 node --check 不过 → 分开 ③「页面炸了」与「抓到了」
#      闸 3  基线/变异后断言条数为 0     → 分开 ④「没跑」与「没断言守着」
#            测试没打印收尾行           → 分开「半路崩了」与「被打红了」
#            （基线顺带还证明了「改之前是绿的」—— 否则本来就红的会被算进战果）
#
#    ★ 三道闸各配一条**对照变异**（--self-test）：
#      否则「闸在不在」本身不可证伪 —— 我们只是把信任从断言转移到了闸上。
#
#  ------------------------------------------------------------
#  ★ 第一个例子：闸抓到了**造闸的人**（2026-09-18，落地当天）
#
#    这台回放器写完，拿它回放第一张证据表，其中一条被闸 2 拒了。
#    查下来不是页面的毛病，是**回放器自己的**：
#
#      heredoc 结尾必然多一个换行，而它被接到了 to 串的尾巴上。
#      整行替换时无害；**行内片段替换**（例如把 ` data-din="1" ` 换成
#      ` data-din="1" data-touched="1" `）就是往一个 JS 字符串字面量中间
#      插了一个换行 —— 字符串没闭合，整页语法错。
#
#    ★ 没有闸 2 的话，这一条会表现成「一大片装载级的红」，
#      而它看起来正好像「我的断言很强」—— 多半就被当成证据报上去了。
#
#    ★ 这件事证明的不是「写的人要细心」，而是**这类错误细心防不住**：
#      它不在你改的那一行上，它在你用来改那一行的工具上。
#      所以闸不是给粗心的人准备的，是给所有人准备的。
#
#  ★ 同一条道理的另一面，在接口上：from / to 之所以走 stdin + 带引号的
#    heredoc 而不是命令行参数，是因为命令行参数要过一层 shell 引号，
#    而「引号把转义吃掉」正是假证据 ③ 的来源。
#    **把接口设计成过不了那道坎，比提醒人小心有用** ——
#    闸是事后发现，接口形状是事前排除。
#
#    ★ 实证（2026-09-18）：有人用 perl 做「两段措辞写成一样」那条变异，
#      替换串里的引号被 shell 吃掉 —— 回放器报「没打中」，
#      **那条变异从来没有回放成功过，而没人知道**。
#      换成本脚本的带引号 heredoc，一次就过。
#      ★ 闸 2 是事后把这类错误**认出来**；heredoc 是让它**根本发不出来**。
#        两者不是替代关系 —— 但能事前排除的，就不要留给事后。
#
#  ------------------------------------------------------------
#  ★ 证据表会过期，而且是**逐条、静默**地过期（buyer-home 2026-09-18 实测）
#
#    一次**零行为变更**的改版（换 token、加个 class、把说明收进 fold）
#    就废掉了一张 18 行证据表里的 7 行 —— 断言一条都没失效，
#    只是 from 串的字面漂了。那 7 条重新推完，转红清单和改版前一模一样。
#
#  ------------------------------------------------------------
#  ★★ 闸也会**拦错**：整表被同一道闸全拒时，先怀疑判据本身
#
#    实证（ui-dispatch 2026-09-18）：`test_dispatch_page.js` 的收尾行写的是
#    「✗ N 项**断言**失败」，而闸 3 认的是全仓统一的「✗ N 项失败」——
#    于是那张表的 20 条**全部**被判成「半路崩了」，一条也出不了分。
#    而测试本身一直是绿的，页面也好好的。**措辞漂一个字，整张表就回放不动。**
#
#    ★ 这条同时证明了两件事：闸 3 确实在拦（它没放过），
#      以及**它拦错原因时会把整张表废掉而不吭声**。
#      我们此前只防过「闸放过了不该放的」；这一种是反面 ——
#      **闸拦住了全部，而拦的理由是假的**，看起来还特别像尽职。
#
#    ★ 和下面「多数被闸 1 拒 ≈ 页面改版」同族，但成因相反、处置也相反：
#        闸 1 那种是**被测的东西**漂了（from 串锚在源码字面上）→ 刷证据表
#        这一种是**闸的判据**漂了（它锚在测试的输出措辞上）  → 修判据/测试
#    `--table` 的汇总里已经按这条打印提示。
#
#  ------------------------------------------------------------
#    ★ 所以闸 1 的「命中 0 次」有**两种成因，处置相反**：
#        · from 串写错了      → 改这一条的 from 串，表本身没问题
#        · 页面改版、串漂了   → 表**整体**过期了，要重跑全表一起刷新
#      只修撞上的那一条，剩下的会继续烂在表里，下一个人再撞一次。
#
#    ★ 证据表锚在「源码字面」上，这是最容易烂的锚（锚在函数名/行号上更烂）。
#      暂时没有更好的锚，所以退而求其次：给它一个**整表重跑**的入口，
#      让过期一次性可见，而不是等人逐条撞上 → 见 `--table`。
#
#  ------------------------------------------------------------
#  用法
#
#    单条：
#      ./replay.sh <页面> <测试> <标签>  <<'MUT'
#      要被替换掉的原文（可以多行，原样贴，不用转义）
#      --
#      替换成什么（留空就是删掉这段）
#      MUT
#
#    整表：
#      ./replay.sh --table <表文件>
#
#      表文件格式（`page:` / `test:` 可写在表头，也可在某条里单独覆盖）：
#
#        page: plan.html
#        test: test_shift.js
#
#        === A refreshBlock 不调 drawShifts
#            drawShifts(sku);
#        --
#
#        === B resize 重画被掐掉
#        shiftTimer = window.setTimeout
#        --
#        shiftTimer = (function(f){return 0;})
#
#      跑完给一份汇总：出分几条、被哪道闸拒了几条、全绿几条。
#      ★ 被闸 1 拒的条数**单独报**，并提醒「你要是没动过 from 串，多半是页面改版了」。
#
#    自检：
#      ./replay.sh --self-test [页面] [测试]      默认 plan.html / test_page.js
#
#  ★ from 与 to 都按**整行块**取，不带结尾换行 —— 所以行内片段也能替换。
#    留空的 to 表示把那几行的内容删掉（行本身变成空行，不影响解析）。
#
#  ★ 并发安全：回放器会把页面改回去，但**只在文件内容确实还是它写进去的那一份时**
#    才改。半路有别人写了同一个文件，它会**拒绝还原**、把这一条判成「不作数」、
#    并把备份路径喊出来 —— 静默盖掉别人半小时前的改动，比任何假证据都贵。
#    ★ 拒过一次之后，后面任何一次 restore 都不再碰这个文件（EXIT trap 也算）：
#      第一版就是栽在这儿 —— 拒绝时顺手删了比对用的标记，
#      trap 再调一次 restore 时没了依据，照样 cp 了回去。
#      **「守卫执行过了」和「守卫还在守」是两回事。**
#
#  ★ --table 还会核对「表里有几条 / 实际跑了几条」。第一版的 `while read`
#    静默吞掉了最后一行：10 条的表跑出 9 条，汇总写「共 9 条」，一切自洽 ——
#    只有对着表数一遍才看得出来。
#
#  退出码： 0 = 全部出分   1 = 用法错 / 自检失败 / 表里有条目被丢掉
#           2 = 闸 1 拒    3 = 闸 2 拒    4 = 闸 3 拒    5 = 页面没能还原，结论不作数
#           （--table 时取最大者）
# ============================================================
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

TMP="${TMPDIR:-/tmp}/replay.$$"
mkdir -p "$TMP"
PAGE=""
KEEP_TMP=0
RESTORE_REFUSED=0
restore () {
  # ★ 一旦拒过一次，后面任何一次 restore 都不许再碰这个文件。
  #   （EXIT trap 会再调一次 restore —— 第一版就是在这儿把别人的改动盖掉的：
  #     第一次拒绝时顺手删了 page.mut，第二次没了比对依据，直接 cp 了回去。
  #     ★ 「守卫执行过了」和「守卫还在守」是两回事。）
  [ "$RESTORE_REFUSED" = "1" ] && return 0
  [ -n "$PAGE" ] || return 0
  [ -f "$TMP/page.bak" ] || return 0
  # ★ 只还原**我们自己改出来的那一份**。别人在这中间写过 → 不碰，喊出来。
  if [ -f "$TMP/page.mut" ] && ! cmp -s "$PAGE" "$TMP/page.mut"; then
    RESTORE_REFUSED=1
    KEEP_TMP=1
    echo "  ⚠⚠ $PAGE 在回放期间被**别人**改过了 —— 回放器拒绝还原，免得静默盖掉那次改动。"
    echo "     变异前的原样留在：$TMP/page.bak （这个目录这次不会被清掉）"
    echo "     ★ 文件现在是**脏的**（既有别人的改动，也有这次的变异）：先人工合并，再继续回放。"
    return 0
  fi
  cp "$TMP/page.bak" "$PAGE"
  rm -f "$TMP/page.mut"
}
cleanup () { restore; [ "$KEEP_TMP" = "1" ] || rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

usage () { sed -n '/^#  用法/,/^# ===/p' "$0" | sed 's/^#\s\?//'; exit 1; }

# ── 跑一次测试，回显「断言总数 红的条数」 ────────────────
#    ★ 断言行是缩进的 ✓/✗；末尾那句 "✗ N 项失败" 是汇总，不算断言。
test_stats () {
  local out total red
  out=$(node "$1" 2>&1)
  total=$(printf '%s\n' "$out" | grep -E '^[[:space:]]+[✓✗][[:space:]]' | grep -vc '项失败')
  red=$(printf '%s\n' "$out" | grep -E '^[[:space:]]+✗[[:space:]]' | grep -vc '项失败')
  echo "$total $red"
}

# 基线只跑一次，按 页面+测试 缓存
baseline_of () {
  local key="$TMP/base.$(printf '%s' "$1$2" | tr -c 'A-Za-z0-9' '_')"
  [ -f "$key" ] || test_stats "$2" > "$key"
  cat "$key"
}

# ── 闸 2：变异之后，页面里的脚本还必须能解析 ──────────────
syntax_ok () {
  local f="$1"
  case "$f" in
    *.html)
      python3 - "$f" "$TMP" <<'PY' || return 1
import io, re, sys, os
src, tmp = sys.argv[1], sys.argv[2]
s = io.open(src, encoding='utf-8').read()
blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>', s)
if not blocks:
    sys.stderr.write('replay: 页面里一个内联 <script> 都没有 —— 闸 2 无从判断\n')
    sys.exit(1)
for i, b in enumerate(blocks):
    io.open(os.path.join(tmp, 'inline%d.js' % i), 'w', encoding='utf-8').write(b)
PY
      local n i=0
      n=$(python3 -c "import re,io;s=io.open('$f',encoding='utf-8').read();print(len(re.findall(r'<script(?![^>]*\\bsrc=)[^>]*>',s)))")
      while [ "$i" -lt "$n" ]; do
        node --check "$TMP/inline$i.js" >/dev/null 2>&1 || return 1
        i=$((i+1))
      done
      return 0;;
    *) node --check "$f" >/dev/null 2>&1; return $?;;
  esac
}

# ── 跑一条变异 ────────────────────────────────────────────
#    $1 页面  $2 测试  $3 标签  $4 规格文件（from -- to）
run_one () {
  PAGE="$1"; local TEST="$2" LABEL="$3" SPEC="$4"
  [ -f "$PAGE" ] || { echo "replay: 找不到页面 $PAGE"; return 1; }
  [ -f "$TEST" ] || { echo "replay: 找不到测试 $TEST"; return 1; }
  rm -f "$TMP/page.mut"
  cp "$PAGE" "$TMP/page.bak"

  # ★ 闸 3（上半）：基线。测试得**真的跑起来**，而且改之前是绿的。
  local bt br
  read -r bt br <<EOF
$(baseline_of "$PAGE" "$TEST")
EOF
  if [ "${bt:-0}" -eq 0 ]; then
    printf '  ✗ %-42s\n' "$LABEL"
    printf '      ★ 闸 3 拒绝出分：基线跑下来**一条断言都没执行**。\n'
    printf '        测试根本没跑起来（jsdom 没装？NODE_PATH 没设？），不是「没有断言守着」。\n'
    printf '        ★ 这两句话的下一步正好相反 —— 一个是去装依赖，一个是去补断言甚至删代码。\n'
    return 4
  fi
  if [ "${br:-0}" -gt 0 ]; then
    printf '  ✗ %-42s\n' "$LABEL"
    printf '      ★ 闸 3 拒绝出分：**改之前**就有 %s 条红的（共 %s 条断言）。\n' "$br" "$bt"
    printf '        本来就坏的东西会被算进这次变异的战果里。先把基线弄绿再回放。\n'
    return 4
  fi

  # ★ 闸 1：from 必须命中，且只命中一次。
  local hits
  hits=$(PAGE="$PAGE" SPEC="$SPEC" python3 <<'PY'
import io, os, sys
raw = io.open(os.environ['SPEC'], encoding='utf-8').read()
# ★ heredoc 结尾必然多一个换行。不剥掉的话它会被接到 to 串尾巴上 ——
#   对**整行**替换无害，对**行内片段**替换就是在字符串中间插一个换行，
#   页面直接语法错。（这个 bug 本身就是被闸 2 抓出来的，2026-09-18。）
if raw.endswith('\n'):
    raw = raw[:-1]
spec = raw.split('\n')
try:
    i = spec.index('--')
except ValueError:
    sys.stderr.write('MISSING_SEP'); sys.exit(9)
frm = '\n'.join(spec[:i])
page = io.open(os.environ['PAGE'], encoding='utf-8').read()
print(page.count(frm) if frm else -1)
PY
) || { printf '  ✗ %-42s 规格里没有 `--` 分隔行 —— from 与 to 分不开\n' "$LABEL"; return 1; }

  if [ "$hits" != "1" ]; then
    printf '  ✗ %-42s\n' "$LABEL"
    printf '      ★ 闸 1 拒绝出分：from 串命中 %s 次（要 1 次）。两种成因，处置相反：\n' "$hits"
    printf '        · from 串写错了    → 改这一条的 from 串，证据表本身没问题\n'
    printf '        · 页面改版、串漂了 → ★ 你要是**没动过** from 串而它突然不命中，\n'
    printf '          多半是页面改版了：**别单修这一条**，先整表重跑（--table），\n'
    printf '          把所有串一起刷新。只修撞上的那条，剩下的会继续烂在表里。\n'
    return 2
  fi

  PAGE="$PAGE" SPEC="$SPEC" python3 <<'PY'
import io, os
raw = io.open(os.environ['SPEC'], encoding='utf-8').read()
if raw.endswith('\n'):      # 同上：剥掉 heredoc 多出来的那个换行
    raw = raw[:-1]
spec = raw.split('\n')
i = spec.index('--')
frm, to = '\n'.join(spec[:i]), '\n'.join(spec[i + 1:])
p = os.environ['PAGE']
s = io.open(p, encoding='utf-8').read()
io.open(p, 'w', encoding='utf-8').write(s.replace(frm, to, 1))
PY
  cp "$PAGE" "$TMP/page.mut"          # 记下我们写进去的那一份，还原时比对

  if ! syntax_ok "$PAGE"; then
    restore
    printf '  ✗ %-42s\n' "$LABEL"
    printf '      ★ 闸 2 拒绝出分：变异之后脚本解析不过，页面整个不会执行。\n'
    printf '        接着跑只会得到一片**装载级**的红，而目标断言根本没跑到 ——\n'
    printf '        「红得多」在这里不是证据，是噪声。去改 to 串，别改断言。\n'
    return 3
  fi

  local out mt red
  out=$(node "$TEST" 2>&1)
  mt=$(printf '%s\n' "$out" | grep -E '^[[:space:]]+[✓✗][[:space:]]' | grep -vc '项失败')
  red=$(printf '%s\n' "$out" | grep -E '^[[:space:]]+✗[[:space:]]' | grep -v '项失败' \
        | sed 's/^[[:space:]]*✗[[:space:]]*//')
  restore
  # ★ 还原被拒 = 文件现在是脏的，这一条的结论不作数，也不许接着往下跑
  if [ "$RESTORE_REFUSED" = "1" ]; then
    printf '  ✗ %-42s\n' "$LABEL"
    printf '      ★ 这次回放的结论**不作数**：页面没能还原，现在混着别人的改动。\n'
    return 5
  fi
  echo scored > "$TMP/last.verdict"

  # ★ 闸 3（下半）：变异之后测试也得真的跑到底
  if [ "$mt" -eq 0 ]; then
    printf '  ✗ %-42s\n' "$LABEL"
    printf '      ★ 闸 3 拒绝出分：变异之后**一条断言都没执行**（基线有 %s 条）。\n' "$bt"
    printf '        测试没跑到底，不是「没有断言守着」。\n'
    return 4
  fi

  # ★ 闸 3（第三档）：测试必须打印**收尾行**。
  #
  #   实证（2026-09-18）：一条测试里有句无保护的 `el[0].click()` ——
  #   变异让 items 长不出来 → `undefined.click()` 抛异常 → **测试死在第 27 条**，
  #   后面 29 条（含整整一节）一条没跑，而回放器照样给它出了分「转红 10 条」。
  #   ★ 那 10 条红是真的，但**中断和「被打红」长得一模一样** ——
  #     要是崩在目标断言之前，就会得到「红得挺多、目标一条没中」。
  #   ★ 闸 2 看不见它：页面语法好得很，崩的是**测试自己**。
  #
  #   为什么用「有没有收尾行」而不是「断言数掉了多少」：
  #     早退型测试（`if (!x.length) return finish()`）也会让条数掉，
  #     但它**会走到收尾行**；崩掉的不会。★ 这个判据分得开，比对条数分不开。
  if ! printf '%s\n' "$out" | grep -qE '★ 全绿|✗ [0-9]+ 项失败'; then
    printf '  ✗ %-42s\n' "$LABEL"
    printf '      ★ 闸 3 拒绝出分：测试**没有打印收尾行** —— 它是半路崩的，不是跑完的。\n'
    printf '        跑到第 %s 条（基线 %s 条），后面的根本没执行。\n' "$mt" "$bt"
    printf '        ★ 已经红的那些是真的，但「红得多」在这里说明不了断言强 ——\n'
    printf '          目标断言可能排在崩掉的位置之后。先修测试里那处崩点。\n'
    return 4
  fi

  if [ -z "$red" ]; then
    echo green > "$TMP/last.verdict"
    printf '  · %-42s 全绿（基线 %s 条断言都跑到了）\n' "$LABEL" "$bt"
    printf '      ★ 三道闸都过、测试确实跑完了，却一条都没打红 ——\n'
    printf '        这才是真的「这条路没有断言守着」。\n'
    return 0
  fi
  printf '  ✓ %-42s 转红 %s 条 / 共 %s 条' "$LABEL" "$(printf '%s\n' "$red" | wc -l | tr -d ' ')" "$mt"
  # ★ 总数比基线矮一截时报一句 —— **只报，不判**。
  #
  #   为什么不拒绝出分：早退型断言（`if (!x.length) return finish()`）也会让
  #   总数掉，那是**正常形态**。判成故障就会误报，而★ **误报的代价是有人
  #   把整道闸关掉** —— 一道会误报的闸比没有这道闸更糟，它会连真报警一起带走。
  #
  #   那为什么还要报：有一种情况没有红、只有总数悄悄矮一截 ——
  #   某条断言被放宽之后，排在它后面的几条**静默地再也不跑**。
  #   ★ 红的条数会骗人，总数不会。两者在「总数掉了」这一个信号上长得一样，
  #     所以这个信号只能交给人，不能交给闸。
  if [ "$mt" -lt "$bt" ]; then
    printf '   ⚠ 比基线少跑 %s 条' "$((bt - mt))"
  fi
  printf '\n'
  printf '%s\n' "$red" | sed 's/^/      · /'
  if [ "$mt" -lt "$bt" ]; then
    printf '      ⚠ 基线 %s 条、这次 %s 条。少跑的那些是**跳过**不是**通过** ——\n' "$bt" "$mt"
    printf '        红是因、跳过是果的话正常；要是哪天第一条被放宽了，\n'
    printf '        后面那几条会静默地再也不跑。这一句只是提醒，不作判定。\n'
  fi
  return 0
}

# ── 整表重跑 ──────────────────────────────────────────────
#    ★ 证据表的过期是逐条、静默的 —— 这个入口让它一次性可见。
run_table () {
  local tbl="$1"
  [ -f "$tbl" ] || { echo "replay: 找不到表文件 $tbl"; exit 1; }
  python3 - "$tbl" "$TMP" <<'PY' || { echo "replay: 表文件解析失败"; exit 1; }
import io, os, sys
tbl, tmp = sys.argv[1], sys.argv[2]
lines = io.open(tbl, encoding='utf-8').read().split('\n')
page = test = None
blocks, cur = [], None
for ln in lines:
    if ln.startswith('==='):
        if cur: blocks.append(cur)
        cur = {'label': ln[3:].strip(), 'page': page, 'test': test, 'body': []}
        continue
    if cur is None:
        if ln.startswith('page:'): page = ln.split(':', 1)[1].strip()
        elif ln.startswith('test:'): test = ln.split(':', 1)[1].strip()
        continue
    if not cur['body'] and ln.startswith('page:'): cur['page'] = ln.split(':', 1)[1].strip(); continue
    if not cur['body'] and ln.startswith('test:'): cur['test'] = ln.split(':', 1)[1].strip(); continue
    cur['body'].append(ln)
if cur: blocks.append(cur)
if not blocks:
    sys.stderr.write('replay: 表里一条 `=== 标签` 都没有\n'); sys.exit(1)
idx = []
for i, b in enumerate(blocks):
    body = b['body']
    while body and body[-1].strip() == '': body.pop()   # 块尾空行不算 to 的一部分
    if '--' not in body:
        sys.stderr.write('replay: 「%s」里没有 `--` 分隔行\n' % b['label']); sys.exit(1)
    io.open(os.path.join(tmp, 'tbl%d.spec' % i), 'w', encoding='utf-8').write('\n'.join(body))
    idx.append('%s\t%s\t%s' % (b['page'] or '', b['test'] or '', b['label']))
# ★ 结尾那个换行不是格式洁癖：没有它，`while read` 会**静默吞掉最后一行** ——
#   10 条的表跑出 9 条、汇总写「共 9 条」，一切看起来都正常。
#   （2026-09-18 真的发生过，J 那条整条消失。）
io.open(os.path.join(tmp, 'tbl.idx'), 'w', encoding='utf-8').write('\n'.join(idx) + '\n')
io.open(os.path.join(tmp, 'tbl.count'), 'w', encoding='utf-8').write(str(len(idx)))
PY

  local i=0 ok=0 green=0 g1=0 g2=0 g3=0 worst=0
  while IFS=$'\t' read -r p t label; do
    [ -n "$p" ] && [ -n "$t" ] || { echo "replay: 「$label」没有 page/test"; exit 1; }
    rm -f "$TMP/last.verdict"
    run_one "$p" "$t" "$label" "$TMP/tbl$i.spec"
    case $? in
      0) if [ "$(cat "$TMP/last.verdict" 2>/dev/null)" = "green" ]; then
           green=$((green+1)); else ok=$((ok+1)); fi;;
      2) g1=$((g1+1)); [ $worst -lt 2 ] && worst=2;;
      3) g2=$((g2+1)); [ $worst -lt 3 ] && worst=3;;
      4) g3=$((g3+1)); [ $worst -lt 4 ] && worst=4;;
      5) echo "  ✗✗ 页面没能还原 —— 后面的条目不跑了，先人工合并"; return 5;;
    esac
    i=$((i+1))
  done < "$TMP/tbl.idx"

  # ★ 表里有几条，就必须跑几条。少跑的那几条是**静默**消失的：
  #   汇总照样打印，数字照样自洽，只有对着表数一遍才看得出来。
  local want; want=$(cat "$TMP/tbl.count")
  if [ "$i" != "$want" ]; then
    echo
    echo "  ✗✗ 表里有 $want 条，只跑了 $i 条 —— 有条目被**静默丢掉**了，这次汇总不作数。"
    return 1
  fi

  echo
  echo "── 汇总（共 $i 条 / 表里 $want 条）──"
  printf '  出分 %s 条   全绿（没断言守着）%s 条\n' "$ok" "$green"
  printf '  闸 1 拒 %s 条   闸 2 拒 %s 条   闸 3 拒 %s 条\n' "$g1" "$g2" "$g3"
  if [ "$g1" -gt 0 ]; then
    echo
    echo "  ★ 有 $g1 条被闸 1 拒（from 串不命中）。如果你**没动过**这些串，"
    echo "    那多半是页面改版了 —— 这张表整体过期了，请把不命中的串按当前文件"
    echo "    重新推一遍**一起刷新**，别只修撞上的那几条。"
    echo "    ★ 断言可能一条都没失效：改版是外观的，守的东西还守着，只是字面漂了。"
  fi

  # ★★ 整表被**同一道闸**全拒：先怀疑判据本身对不上，别去逐条改证据。
  #
  #   实证（ui-dispatch 2026-09-18）：test_dispatch_page.js 的收尾行写的是
  #   「✗ N 项**断言**失败」，而闸 3 认的是全仓统一的「✗ N 项失败」——
  #   于是这张表的 20 条**全部**被判成「半路崩了」，一条也出不了分，
  #   而测试本身一直是绿的、页面也好好的。
  #
  #   ★ 这和上面那条「多数被闸 1 拒 ≈ 页面改版」是同族、但成因相反：
  #       闸 1 那种是**被测的东西**漂了（from 串锚在源码字面上）
  #       这一种是**闸的判据**漂了（它锚在测试的输出措辞上）
  #     两者的下一步正好相反 —— 一个去刷证据表，一个去修判据/测试。
  #
  #   ★ 我们此前只防过「闸放过了不该放的」。这一种是反面：
  #     **闸拦住了全部，而拦的理由是假的** —— 它不吭声，看起来还特别像尽职。
  if [ "$i" -gt 2 ]; then
    local allg=0 which=""
    [ "$g1" = "$i" ] && { allg=1; which="闸 1（from 串不命中）"; }
    [ "$g2" = "$i" ] && { allg=1; which="闸 2（变异后语法不过）"; }
    [ "$g3" = "$i" ] && { allg=1; which="闸 3（没跑起来 / 半路崩 / 没打收尾行）"; }
    if [ "$allg" = "1" ]; then
      echo
      echo "  ★★ 这张表 $i 条**全部**被同一道闸拒了：$which"
      echo "     先怀疑**判据本身对不上**，别去逐条改证据 —— 一条都出不了分，"
      echo "     通常不是二十个变异同时写错了，而是闸认的那个东西漂了。先查这三样："
      echo "       · 测试的收尾行措辞是不是还是「★ 全绿」/「✗ N 项失败」（闸 3 认的就是这两句）"
      echo "       · 依赖装没装、NODE_PATH 设没设（测试会「明说跳过」，一条断言都不打）"
      echo "       · 页面是不是整体改版了，表里的 from 串全漂了（这种归闸 1）"
      echo "     ★ 闸拦住全部、而理由是假的 —— 它不吭声，看起来还特别像尽职。"
    fi
  fi
  return $worst
}

# ── 自检：三道闸各验一次 ──────────────────────────────────
self_test () {
  local page="${1:-plan.html}" test="${2:-test_page.js}" bad=0
  echo "── replay.sh 自检（$page / $test）──"
  echo "★ 目的不是测页面，是证明**三道闸都是活的** —— 否则我们只是把信任从断言转移到了闸上。"

  printf '%s\n' '__replay_self_test_definitely_absent__' '--' > "$TMP/s1"
  echo
  echo "对照 1 · 故意写一个文件里没有的 from 串（该被闸 1 拒）"
  run_one "$page" "$test" '对照 1' "$TMP/s1"
  [ $? -eq 2 ] || { echo "  ✗✗ 闸 1 没拦住 —— 假证据 ①（全绿型）会被当成结论"; bad=1; }

  # ★ 这条 from 必须是每个页面都有的东西，否则自检会退化成闸 1 的测试
  printf '%s\n' "  'use strict';" '--' "  'use strict'; )(" > "$TMP/s2"
  echo
  echo "对照 2 · 故意让替换串带语法错（该被闸 2 拒）"
  run_one "$page" "$test" '对照 2' "$TMP/s2"
  local rc=$?
  if [ $rc -eq 2 ]; then
    echo "  ✗✗ 被闸 1 拦下了 —— 这个页面里没有那条锚点，闸 2 这次**没验到**"; bad=1
  elif [ $rc -ne 3 ]; then
    echo "  ✗✗ 闸 2 没拦住 —— 假证据 ③（大片红但没打中目标）会被当成结论"; bad=1
  fi

  # ★ 对照 3：造一个「明说跳过、exit 0、一条断言都不打」的测试 ——
  #   这正是 jsdom 缺失时的形状。不靠环境复现，靠一个替身，免得自检结果取决于机器。
  printf '%s\n' \
    "console.log('⚠ 依赖没装，这个测试跳过（不是通过）。');" \
    "process.exit(0);" > "$TMP/skipper.js"
  printf '%s\n' "  'use strict';" '--' "  'use strict';" > "$TMP/s3"
  echo
  echo "对照 3 · 测试明说跳过、一条断言都不打（该被闸 3 拒）"
  run_one "$page" "$TMP/skipper.js" '对照 3' "$TMP/s3"
  [ $? -eq 4 ] || { echo "  ✗✗ 闸 3 没拦住 —— 假证据 ④ 会被报成「这条路没有断言守着」，而那是反话"; bad=1; }

  # ★ 对照 4：造一个「先打几条断言、然后抛异常、不打收尾行」的替身 ——
  #   这是闸 3 **第三档**的靶子。它和对照 3 是两回事：
  #     对照 3 = 一条都没跑（依赖没装）
  #     对照 4 = 跑了一半崩了（测试自己里有崩点）
  #   ★ 两者在「断言数掉了」上长得一样，在「有没有收尾行」上才分得开。
  #
  #   ★ 这条对照是补上来的：第三档先落地、自检却一直只有三条对照，
  #     而末尾照样打印「三道闸都是活的」—— **跑的是三条，担保的是四档**。
  #     结论碰巧对，证据不覆盖。这正是「没人兑现的承诺不会失败」。
  printf '%s\n' \
    "console.log('  ✓ 第 1 条');" \
    "console.log('  ✓ 第 2 条');" \
    "var CRASH_HERE = false;" \
    "if (CRASH_HERE) { var a = []; a[0].click(); }" \
    "console.log('  ✓ 第 3 条');" \
    "console.log('★ 全绿');" > "$TMP/crasher.js"
  printf '%s\n' "var CRASH_HERE = false;" '--' "var CRASH_HERE = true;" > "$TMP/s4"
  echo
  echo "对照 4 · 测试跑到一半崩了、不打收尾行（该被闸 3 第三档拒）"
  # ★ 变异要打在**测试自己**身上 —— 病灶在测试里，不在页面里
  run_one "$TMP/crasher.js" "$TMP/crasher.js" '对照 4' "$TMP/s4"
  local rc4=$?
  if [ $rc4 -ne 4 ]; then
    echo "  ✗✗ 闸 3 第三档没拦住（退出码 $rc4）—— 「测试半路崩了」会被当成「断言抓到了」"
    bad=1
  fi

  # ★ 结论句按**实际覆盖的分支数**说话。
  #
  #   判据（po-detail 提的，直接采纳）：
  #     **一句「都活着」能覆盖的分支数，不能多于对照数。**
  #   这之前这里写的是「三道闸都是活的」，而当时只有三条对照，
  #   闸 3 却有四个拒绝分支 —— 一句话替四个分支作了担保，其中三个从没被走到过。
  #   ★ 结论碰巧对，证据不覆盖。而下一个人只会读到那句担保。
  local branches; branches=$(grep -c '闸 [123] 拒绝出分' "$0")
  local controls=4
  echo
  if [ $bad -eq 0 ]; then
    printf '★ %s 条对照全部如预期被拒 —— 覆盖 %s 个拒绝分支中的 %s 个\n' \
      "$controls" "$branches" "$controls"
    [ "$controls" -lt "$branches" ] && \
      printf '  ⚠ 还有 %s 个分支没有对照 —— 这句结论只担保验过的那些\n' \
        "$((branches - controls))"
  else
    echo "✗ 闸有问题，这台回放器出的分暂时不可信"
  fi
  return $bad
}

# ── 入口 ──────────────────────────────────────────────────
[ $# -ge 1 ] || usage
case "$1" in
  --self-test) shift; self_test "${1:-}" "${2:-}"; exit $?;;
  --table) shift; [ $# -eq 1 ] || usage; run_table "$1"; exit $?;;
  -h|--help) usage;;
esac
[ $# -eq 3 ] || usage
cat > "$TMP/spec"
run_one "$1" "$2" "$3" "$TMP/spec"
exit $?
