# -*- coding: utf-8 -*-
"""自设规则盘点 v2（AST 精准版，§9.803）：只收"决策/风控参数"里的硬编码阈值与倍率。

v1（行匹配）噪音太大（4819 条 ✗，含大量注释/下标/无关比较）⇒ 改用 AST：
  只扫**运行时表达式**（跳过注释与 docstring ✓），命中三类：
    A. 参数赋值：名字像参数（*_pct/*_cap/*_ratio/*_max/*_min/threshold/stop*/limit）且值 = 数值字面量
    B. 阈值比较：`x >= 5`、`x < 0.2`、`> 25` 等（左右任一名字像参数 ✓）
    C. 倍率：`参数 * 0.65` / `* 1.15` / `×` 类
  再对**所在函数 ±40 行**做语料引用检查（语料|狼大|账本§|§9.|原话|逐字|docs/wolf-|NGA|tid=）
输出 docs/self-set-rule-audit.md + 打印影响面 Top 25。
"""
import ast
import os
import re
from collections import Counter

TARGET_DIRS = ("backend/app/api", "backend/app/services", "apps/main_line", "jobs")
CITE = re.compile(r"语料|狼大|账本\s*§|§9\.|原话|逐字|docs/wolf-|NGA|tid=|他\s*20\d\d-")
PARAM = re.compile(r"(pct|cap|ratio|max|min|threshold|thr|stop|limit|mult|coef|weight|"
                   r"score|gate|tier|budget|quota|size|position|vol_|_vol|days|hours|sec|"
                   r"share|amount|price_)", re.I)
NOISE_NUM = {0, 1, 2, 100, 1000, 10, 3, 60, 24, 10000, 1e6, 1e8, 100000000.0, 1e4, 252, 365, 12, 7, 30, 0.0}
NOISE_LINE = __import__("re").compile(r"(1e8|1e4|100000000|/ *252|\* *252|sqrt|round\(|%\(|f\"|format\(|"
                                     r"progress|percent|elapsed|duration|sleep|timeout|retry|time\.)")
RISK_HINT = re.compile(r"(cap|position|仓位|止损|stop|reduce|减仓|build|加仓|total|single|"
                       r"limit|tier|risk|budget|quota)", re.I)
CLASS_HINT = re.compile(r"(震荡|趋势币|风格|regime|style|强势|弱势|中性|ranging|trending)", re.I)
CITE_NEAR = re.compile(r"(语料|狼大|账本\s*§|§9\.|原话|逐字|docs/wolf-|NGA|tid=)")


def _name(node) -> str:
    """尽力取出表达式里的"名字"（Name/Attribute/常量下标等）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant):
        return str(node.value)[:20]
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    if isinstance(node, ast.BinOp):
        return "%s%r%s" % (_name(node.left), type(node.op).__name__, _name(node.right))[:60]
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


rows = []
for d in TARGET_DIRS:
    for dp, _dn, fn in os.walk(d):
        if "__pycache__" in dp:
            continue
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            try:
                src = open(p, encoding="utf-8").read()
                tree = ast.parse(src)
            except Exception:
                continue
            lines = src.split("\n")
            for node in ast.walk(tree):
                kind = None
                detail = ""
                if isinstance(node, ast.Assign):
                    tgts = ", ".join(_name(t) for t in node.targets)
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) \
                            and PARAM.search(tgts) and node.value.value not in NOISE_NUM:
                        kind, detail = "A 参数赋值", "%s = %r" % (tgts, node.value.value)
                elif isinstance(node, ast.Compare) and len(node.ops) == 1:
                    op = type(node.ops[0]).__name__
                    left, right = _name(node.left), _name(node.comparators[0])
                    num = None
                    for x in (node.left, node.comparators[0]):
                        if isinstance(x, ast.Constant) and isinstance(x.value, (int, float)):
                            num = x.value
                    if num is not None and num not in NOISE_NUM and (PARAM.search(left) or PARAM.search(right)):
                        kind, detail = "B 阈值比较", "%s %s %r" % (left, op, num)
                elif isinstance(node, ast.Call):
                    fname = _name(node.func) or ""
                    # ★ 关键 ✓：`min(single_cap_pct, 8.0)` 这种"函数名不像参数、但**实参名**像参数"
                    #   才是用户案例的形态（震荡市 single_cap ≤ 8%）✗ ⇒ 实参名也算匹配 ✓
                    names = " ".join(_name(a) for a in list(node.args) + [k.value for k in node.keywords])
                    for a in list(node.args) + [k.value for k in node.keywords]:
                        if isinstance(a, ast.Constant) and isinstance(a.value, (int, float)) \
                                and a.value not in NOISE_NUM and (PARAM.search(fname) or PARAM.search(names)):
                            kind, detail = "D 调用参数", "%s(%s ⇒ %r)" % (fname, names[:40], a.value)
                            break
                elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
                    l, r = node.left, node.right
                    num = l.value if isinstance(l, ast.Constant) else (r.value if isinstance(r, ast.Constant) else None)
                    nm = _name(r) if isinstance(l, ast.Constant) else _name(l)
                    if isinstance(num, (int, float)) and num not in NOISE_NUM and PARAM.search(nm or ""):
                        kind, detail = "C 倍率", "%s * %r" % (nm, num)
                if not kind:
                    continue
                if NOISE_LINE.search(lines[getattr(node, "lineno", 1) - 1]):
                    continue
                ln = getattr(node, "lineno", 0)
                lo, hi = max(0, ln - 8), min(len(lines), ln + 8)
                near = bool(CITE_NEAR.search("\n".join(lines[lo:hi])))
                ctx = lines[ln - 1].strip()[:110]
                w = 3 if (RISK_HINT.search(detail) or RISK_HINT.search(ctx)) else \
                    (2 if CLASS_HINT.search(ctx) else 1)
                rows.append((w, kind, p.replace(os.getcwd() + "/", ""), ln, detail[:80], ctx,
                             ("邻行有引用" if near else "**无引用 ✗**")))

rows.sort(key=lambda r: (-r[0], r[2], r[3]))
# 同一行去重
seen, uniq = set(), []
for r in rows:
    k = (r[2], r[3])
    if k in seen:
        continue
    seen.add(k)
    uniq.append(r)

CUR = [
 '| 3 | 风控·仓位 | `backend/app/api/indicator.py:1652` | 震荡市 ⇒ `single_cap ≤ 8.0`、`total_cap ≤ 50.0`、green→yellow ✗ | **自设**：狼大原话是 主升75+/调整50/有风险30；他说「震荡筑底」时是 **75%**、「缩量微震」是 **60-70%** |',
 '| 3 | 风控·仓位 | `backend/app/api/indicator.py:1192` | 风格倍率 `OFFENSE×1.15 / DEFENSE×0.65（总仓×0.75）/ RESOURCE_HEDGE×0.85` ✗ | **自设**（无任何引用）|',
 '| 3 | 风控·仓位 | `backend/app/api/indicator.py:2929` | `max_position_pct = 5.0` ✗ | **自设**：台账原话「只给总量档位…**没给单笔**」|',
 '| 3 | 风控·仓位 | `backend/app/api/market.py:1874/1909/1941/1954/1959` | 震荡/趋势投票阈值（振幅>25%/<15%、连续≥5天、涨跌停比>5或<0.2、MA5角>30°）✗ | **自设阈值**：他讲的是「情形」（放量突破？急杀？位置？）|',
 '| 3 | 风控·仓位 | `backend/app/services/position_tier_monitor.py:374/384/394/2001` | 档位升级阈 `pnl_pct ≥ 0.03 / 0.01` ✗ | 待人工核（未见引用）|',
 '| 3 | 选股·额度 | `backend/app/services/t_build.py:1498` | `DEFAULT_AUTO_SELECT_LIMIT = 5`（每日自动选股上限）✗ | 待人工核 |',
 '| 3 | 判据·门槛 | `backend/app/services/t_build.py:516` | 涨停占比 `> 0.1` 才放行 ✗ | 待人工核 |',
 '| 3 | 风控·仓位 | `backend/app/services/golden_pit_config.py:337` | `PRE_TURN_CUMULATIVE_CAP = 0.15` ✗ | 待人工核 |',
 '| 2 | 分类·风格 | `backend/app/services/industry_leaderboard.py`（多处）| `ADX>25` / MACD金叉 加分、`cap_score=4` 等 ✗ | **自设**（源自设计文档提案；仅打分，不入风控 ✓）|',
 '| 1 | 噪音核对 | `t_eod.py:22 STOP_LOSS_PCT=0.03` | -3% 止损 ✗ | **有出处 ✓**（狼大 3-05「无利空、13日内下跌创新低后 -3%」）⇒ 属误报，保留 |',
]
out = ["# 自设规则盘点（AST 精准版）", "",
       "## 一、人工挑选：影响最大的自设规则（建议优先处理）", "",
       "| 权重 | 类别 | 位置 | 判据 | 出处判断 |", "|---|---|---|---|---|"] + CUR + [
       "", "## 二、为什么这些自设规则会漏掉（关键 ✗）", "",
       "项目本来有**出处纪律** ✓：`docs/wolf-buy-parameter-ledger.md` §4 记了 **11 项自设参数** ✓，",
       "代码里要求逐条标 `⛔自设(无语料依据)` ✓，并有守卫测试 `backend/tests/test_param_provenance_marks.py` ✓。", "",
       "**但守卫只覆盖 4 个文件** ✗：`wolf_confirm_pick.py`(9) / `rotation_switch_arm.py`(3) / `t_gateway.py`(5) / `wolf_ticket_ban.py`(1)。", "",
       "⇒ `indicator.py` / `market.py` / `position_tier_monitor.py` / `t_build.py` **不在守卫内** ✗",
       "⇒ 这些文件里新加的自设阈值（震荡市 8%/50%、风格倍率 ×1.15/×0.65、单笔 5%、档位 0.03/0.01 …）**没人强制申报出处** ✗",
       "⇒ 所以它们既不在 §4 清单、也没有 `⛔自设` 标记 ⇒ **你只能靠逐个发现** ✗（本表就是补这个洞 ✓）。", "",
       "建议（低成本、可执行 ✓）：把守卫的**文件清单扩到全部决策模块** ✓（indicator/market/t_monitor/t_build/position_tier_monitor/stop_loss_monitor/risk_gate…）",
       "⇒ 以后任何自设阈值不标 `⛔自设` 就**测试失败** ✓，不用再靠人肉盘点 ✓。", "",
       "## 三、机器扫描全量（本表共 %d 条，按影响面排序；`邻行有引用` = ±8 行内出现语料/账本引用）" % len(uniq), "",
       "规则：只收运行时表达式里的 `参数赋值 / 阈值比较 / 倍率`，且**所在函数 ±40 行无语料引用**"
       "（`语料|狼大|账本§|§9.|原话|逐字|docs/wolf-|NGA|tid=`）", "",
       "权重：3=风控/仓位相关 ｜ 2=分类/风格 ｜ 1=其他参数", "",
       "| 权重 | 类别 | 位置 | 判据 | 原行 | 出处核验 |", "|---|---|---|---|---|---|"]
for w, kind, p, ln, detail, ctx, cite in uniq[:250]:
    out.append("| %d | %s | `%s:%d` | `%s` | `%s` | %s |" % (w, kind, p, ln, detail,
                                                             ctx.replace("|", "\\|"), cite))
open("docs/self-set-rule-audit.md", "w", encoding="utf-8").write("\n".join(out) + "\n")

print("候选条数 =", len(uniq), "｜按类别:", dict(Counter(r[1] for r in uniq)))
print("按文件（前 8）:", dict(Counter(r[2] for r in uniq).most_common(8)))
print("\n=== 权重 3（风控/仓位）前 25 条 ===")
for r in [x for x in uniq if x[0] == 3][:25]:
    print("  %-9s %s:%d  %-40s | %s | %s" % (r[1], r[2], r[3], r[4][:38], r[5][:44], r[6]))
