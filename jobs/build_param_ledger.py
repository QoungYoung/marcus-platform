# -*- coding: utf-8 -*-
"""build_param_ledger.py — 买入层**参数总账**：代码参数 × 狼大语料依据。

用户红线（2026-09-15）：**开发时参数必须与狼大语料一致，不能自己设定参数；未知参数要标记出来报告**。
本脚本做三件事：
  1. 固化**代码侧参数清单**（每个参数：名称 / 位置 / 当前值 / 来源）；
  2. 从语料取证产物里**检索候选原话**（`wolf_buy_params_evidence.json` = 带数字的参数抽取；
     `wolf_buy_evidence.json` = B1–B7 定性抽取），供人工/模型判定；
  3. 按 `JUDGEMENTS`（人工判定，写在下方）生成 `docs/wolf-buy-parameter-ledger.md` + JSON。

判定口径：
  ✅ 一致：他的话里有**明确数值/口径**，且我们的取值与之一致
  🟡 代理：他只是定性（或没提），我们取了值 → **必须标注为我们的代理**（不是"一致"）
  ⛔ 自设无依据：他完全没提，我们自造 → **报告，不假装对齐**
  ❓ 未知：他提了但没给数值/口径不明 → **报告待补**
  ⛔冲突：我们的取值或方向与他明确相反

用法：`.venv/bin/python jobs/build_param_ledger.py [--dump-candidates P1]`
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV = os.path.join(ROOT, ".dsh-tmp", "buyside")
PARAMS = os.path.join(EV, "wolf_buy_params_evidence.json")
BUYEV = os.path.join(EV, "wolf_buy_evidence.json")
OUT_MD = os.path.join(ROOT, "docs", "wolf-buy-parameter-ledger.md")
OUT_JSON = os.path.join(EV, "wolf_buy_parameter_ledger.json")

# ── 1) 代码侧参数清单（value = 生产实际生效值；source = 生产 .env / 代码默认 / DB 配置） ──
INVENTORY = [
    # 选股域与过滤
    dict(id="pick.universe", cat="域", name="候选域 = 确认链成分（confirm_universe）",
         loc="apps/main_line/wolf_confirm_pick.py:confirm_universe", value="stock_confirm_result.json 里该主题子概念成分",
         source="代码默认", note="回放不可用（文件每日覆盖）"),
    dict(id="pick.board_exclude", cat="域", name="剔除无权限板块（创业板/科创板/北交所）",
         loc="wolf_confirm_pick.board_allowed / rotation_switch_arm._board_ok",
         value="cyb,bj,kcb（剔 300/301/688/北交所）", source="生产 .env WOLF_PICK_BOARD_EXCLUDE"),
    dict(id="pick.min_amt20", cat="域", name="20 日均成交额下限（亿元）",
         loc="wolf_confirm_pick.MIN_AMT20_YI", value="1.0", source="代码默认"),
    dict(id="pick.buy_shortlist", cat="域", name="路径A 市值预筛只数（按 total_mv 降序取前 N 再算 leader）",
         loc="rotation_switch_arm.pick_buy", value="80", source="env WOLF_PICK_BUY_SHORTLIST 默认"),
    dict(id="pick.mode", cat="选股", name="选股模式（concept=按子概念组内前2 / theme=主题一张榜）",
         loc="wolf_confirm_pick.pick_v2", value="concept", source="env WOLF_PICK_MODE 默认"),
    dict(id="pick.pool_n", cat="选股", name="等待池宽度（leader 榜前 N，用于风向标）",
         loc="wolf_confirm_pick.POOL_N", value="6", source="env WOLF_PICK_POOL_N 默认"),
    dict(id="pick.limit", cat="选股", name="tier1 每日每主题布腿只数",
         loc="wolf_confirm_pick.LIMIT / ROT_POOL_LEGS", value="2（tier1）；ROT_POOL_LEGS=4（全池上限）",
         source="代码默认 + env ROT_POOL_LEGS"),
    dict(id="pick.max_legs", cat="选股", name="tier1+tier2 上限（只）", loc="wolf_confirm_pick.MAX_LEGS",
         value="4", source="代码默认"),
    dict(id="pick.dist_pct", cat="买点", name="位置闸：距前一日低 ≤ x%（%）",
         loc="wolf_confirm_pick.DIST_PCT", value="5.0", source="env WOLF_PICK_DIST_PCT 默认"),
    dict(id="pick.tier2_gap", cat="买点", name="tier2 接近档：距前一日低 ≤ x%（%）",
         loc="wolf_confirm_pick.TIER2_GAP", value="8.0", source="代码默认"),
    dict(id="pick.min_r20", cat="闸门", name="r20 下限（%）", loc="wolf_confirm_pick.MIN_R20",
         value="0", source="env WOLF_PICK_MIN_R20 默认"),
    dict(id="pick.rs_min", cat="闸门", name="个股相对主题强度 rs 下限（%）", loc="wolf_confirm_pick.RS_MIN",
         value="0", source="env WOLF_RS_MIN 默认"),
    dict(id="pick.quantile_pct", cat="域", name="主题容量约束：按 leader 取前 x%（%）",
         loc="wolf_confirm_pick.theme_quantile_keep", value="50", source="env WOLF_THEME_QUANTILE_PCT 默认"),
    dict(id="pick.wind_break", cat="闸门", name="风向标死：收盘较前一交易日低 ≤ x%（%）",
         loc="wolf_confirm_pick.WIND_BREAK", value="-0.5", source="代码默认（硬拦 WOLF_PICK_WIND_HARD=1）"),
    dict(id="pick.limitup_pct", cat="因子", name="涨停判定阈值（%）", loc="wolf_confirm_pick.LIMITUP_PCT",
         value="9.7", source="代码默认"),
    dict(id="pick.rank_win", cat="因子", name="涨停次数统计窗口（交易日）", loc="wolf_confirm_pick.RANK_WIN",
         value="60", source="代码默认"),
    dict(id="pick.etf_fallback", cat="选股", name="空窗买 ETF 兜底", loc="wolf_confirm_pick.etf_fb",
         value="0（关）", source="env 默认"),
    dict(id="pick.empty_wait", cat="选股", name="位置闸否掉全部 → 空窗等待（不回落 legacy）",
         loc="rotation_switch_arm.confirm_pick", value="1（等待）", source="env WOLF_PICK_EMPTY_WAIT 默认"),
    dict(id="pick.fail_mode", cat="可见性", name="pick_v2 报错时的行为", loc="rotation_switch_arm.confirm_pick",
         value="legacy（回落旧扫描序）", source="env WOLF_PICK_FAIL_MODE 默认（2026-09-15 新增）"),
    dict(id="ban.ttl_td", cat="闸门", name="破线删票 TTL（交易日）", loc="wolf_ticket_ban.ttl_td",
         value="13", source="env WOLF_BAN_TTL_TD 默认（自述为我们的代理）"),
    # 排序（v3，未上线）
    dict(id="rank.v3.enabled", cat="排序", name="v3 条件化分层排序", loc="wolf_confirm_pick（未接）",
         value="未接线", source="—", note="离线已验收（5/10 日达标）"),
    dict(id="rank.v3.qtile", cat="排序", name="强/弱主题阈值（主题 r5 跨主题分位）",
         loc="wolf_pick_rank_v3.QTILE_HI/LO", value="0.5 / 0.5", source="离线验收最优（0.66/0.33 亦可达标）"),
    dict(id="rank.v3.buy_n", cat="排序", name="每主题取 1 只（limit=1）", loc="wolf_pick_rank_v3.pick_top",
         value="1", source="离线验收最优"),
    dict(id="rank.v3.tiebreak", cat="排序", name="并列次键", loc="wolf_pick_rank_v3.DEFAULT_TIEBREAK",
         value="flat（低位横盘多时）", source="有 2026-04-07 原话依据"),
    dict(id="rank.v3.diergong", cat="排序", name="强主题走二供（跳过组内 r20 第 1 名）",
         loc="wolf_pick_rank_v3.DEFAULT_DIERGONG", value="True", source="2026-04-13 原话"),
    # 执行（腿/触发）
    dict(id="legs.per_theme", cat="执行", name="每主题布腿候选数上限", loc="rotation_switch_arm.main",
         value="ROT_POOL_LEGS=4", source="env"),
    dict(id="legs.pick_buy_n", cat="执行", name="路径A 每条链选股只数", loc="rotation_switch_arm.pick_buy",
         value="3", source="代码默认"),
    dict(id="trig.253.dump", cat="触发", name="253：上证 5min 单根跌幅 ≥ x%（%）",
         loc="rotation_switch_arm.BUY_253_EXPR", value="0.4", source="DB 表达式/代码常量"),
    dict(id="trig.254.volratio", cat="触发", name="254：量比区间（缩量）",
         loc="rotation_switch_arm.BUY_254_EXPR", value="0 < vol_ratio ≤ 0.9", source="DB 表达式（2026-09-07 由 0.7 放宽）"),
    dict(id="trig.254.prevlow", cat="触发", name="254：触发价 = 前一日最低 × k", loc="t_monitor/quote.dip_prev_low",
         value="×1.005", source="代码/腿表达式"),
    dict(id="guard.refill_per_day", cat="护栏", name="同一标的每日回补次数上限", loc="t_gateway.MAX_WOLF_REFILL_PER_DAY",
         value="2", source="env WOLF_REFILL_MAX_PER_DAY 默认"),
    dict(id="guard.trades_per_day", cat="护栏", name="同一标的每日成交次数上限", loc="t_gateway",
         value="2", source="env WOLF_MAX_TRADES_PER_SYMBOL_PER_DAY 默认"),
    dict(id="guard.single_order_pct", cat="护栏", name="单笔 ≤ 净值 x%（%）", loc="t_gateway.MAX_SINGLE_ORDER_PCT",
         value="5", source="代码默认（建议层）"),
    dict(id="guard.daily_loss_breaker", cat="护栏", name="日亏熔断（%）", loc="t_gateway.DAILY_LOSS_BREAKER_PCT",
         value="2.0", source="代码默认"),
    dict(id="guard.max_turnover", cat="护栏", name="日累计回转额 ≤ x×净值", loc="t_gateway.MAX_DAILY_TURNOVER_RATIO",
         value="3.0", source="代码默认"),
    # 环境门
    dict(id="gate.theme_stage", cat="环境门", name="主题结构门允许的 stage", loc="wolf_context.THEME_STAGE_ALLOW",
         value="{confirmed}", source="代码默认"),
    dict(id="gate.theme_fund_days", cat="环境门", name="主题主力净流入连续流出天数阈值", loc="wolf_context.theme_fund_danger",
         value="3", source="env WOLF_THEME_FUND_DAYS 默认"),
    dict(id="gate.decision", cat="环境门", name="L5 准入闸（新开仓）", loc="daily_decision._l5",
         value="1（开）", source="env WOLF_DECISION_GATE"),
    dict(id="gate.volume_gate", cat="环境门", name="G10 量能门槛（攻关口需突破级量能）",
         loc="wolf_volume_gate.get_levels", value="关口 3800/3900/4000 点 + 量能档（2WE/3WE）",
         source="env WOLF_VG_KEY_LEVELS 默认 + DB 配置"),
    dict(id="gate.weekend_hedge", cat="环境门", name="G9 周末避险时点", loc="wolf_weekend_hedge.CUTOFF_DEFAULT",
         value="14:30（他原话「2点半」）", source="env WOLF_WH_TIME 默认"),
    # 仓位
    dict(id="tier.caps", cat="仓位", name="各档位各意图上限（%）", loc="position_tier.DEFAULTS",
         value="build: new_base 10 / add_base 10 / refill 8 / t_refill 5；t_only: 5/8/5；side: 3/3/5/5；defense: 3/3/5；exit: 仅 t_refill 5",
         source="代码默认（DB 可覆写）"),
    dict(id="tier.cash_floor", cat="仓位", name="各档位现金底线（%）", loc="position_tier.DEFAULTS",
         value="build 25 / t_only 35 / side 30 / defense 45 / exit 50", source="代码默认"),
    dict(id="tier.base_keep", cat="仓位", name="底仓保留下限比例", loc="t_gateway.T_BASE_KEEP_RATIO / FLOOR_LOWER_RATIO",
         value="0.5", source="env 默认"),
    dict(id="rank.fusion.weights", cat="排序", name="leader 三因子权重（r60/amt20/涨停 等权）",
         loc="wolf_confirm_pick.pick_v2 / rotation_switch_arm.pick_buy", value="各 1/3（等权）", source="代码默认"),
    # ── 函数字面量档（2026-09-15 round 32 补扫发现：函数默认值/局部字面量不在任何扫描面内，见总账 §41）──
    dict(id="pick.pick_v2_limit_literal", cat="选股", name="pick_v2 默认每主题只数（字面量默认值）",
         loc="wolf_confirm_pick.pick_v2(limit=2)", value="2", source="代码默认（函数字面量）",
         note="语义同 pick.limit；生产由调用方传 ROT_POOL_LEGS=4，字面量只在 CLI 直调时生效"),
    dict(id="tpool.scan_limit", cat="执行", name="做T候选扫描上限（字面量默认值）",
         loc="t_build.scan_t_candidates(limit=20)", value="20", source="代码默认（函数字面量）",
         note="影响候选扫描广度，不直接决定买什么"),
    dict(id="tai.select_limit", cat="执行", name="AI 选股建仓上限（字面量默认值）",
         loc="t_ai_agent.ai_select_and_build(select_limit=5)", value="5", source="代码默认（函数字面量）",
         note="语料无对应数值；他的口径是每方向 2-3 只（§40.3a）"),
    # ── ④ 字典型默认值容器 + DB 运行时可调（2026-09-15 round 32 补扫发现，见总账 §42）──
    dict(id="tbuild.params_default", cat="执行", name="做T建仓参数字典（50 键：门槛/权重/趋势闸/时机/规模分档）",
         loc="t_build.BUILD_PARAMS_DEFAULT", value="cand_score_min 0.78 / build_score_min 0.78 / vol_ratio_max 2.0 / "
         "drawdown_min_pct 1.0 / quiet_end 09:45 / afternoon_ban_from 13:00 / max_daily_auto 3（全部自设）",
         source="代码默认（**字典型常量内部键**）；DB `t_build_params.params_json` 可覆盖"),
    dict(id="tbuild.params_db", cat="执行", name="做T建仓参数的 DB 覆盖（优先级最高）",
         loc="t_build_params.params_json（DB）", value="no_rebuild_symbols=['SH515880','SZ002409']",
         source="DB 生效值（代码默认只有 ['SH515880']）", note="生效值≠代码默认，§42.3 实测"),
    dict(id="trend.confirm_cfg", cat="买点", name="趋势确认参数（10 键：新高窗/确认新鲜度/回踩幅度/前低窗）",
         loc="trend_confirm.TREND_CFG", value="见 jobs/scan_dict_params.py --keys TREND_CFG",
         source="代码默认（字典型常量内部键）", note="全部自设，待语料判定（§42.4）"),
    dict(id="pick.position_class_cfg", cat="买点", name="位置分类参数（11 键：低位/高位 × 量能/箱体/均线）",
         loc="position_class.CONFIG", value="见 jobs/scan_dict_params.py --keys CONFIG",
         source="代码默认（字典型常量内部键）", note="全部自设，待语料判定（§42.4）"),
    dict(id="cfg.wolf_discipline_db", cat="仓位", name="狼大纪律配置的 DB 生效值（档位/止盈/板块半数/周末避险）",
         loc="wolf_discipline_config.cfg_json（DB，读序 DB→文件→内置默认）",
         value="tier_targets build75/side50/t_only50/defense30/exit50；tier_floor build55；"
               "profit_take.enabled=**true**（代码默认 false）；board_half 19.5%/9.5%；weekend_de_risk 0.5",
         source="DB 生效值", note="§42.3：`profit_take.enabled` 是真实覆盖，断言必须看生效值"),
]

# ── 2) 语料判定（人工/模型判定，写在这里；quote 必须来自取证产物并已逐字校验） ──
JUDGEMENTS = {}     # 由 build() 之外的审阅步骤填充；见 docs 正文


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}


def candidates(param, items, topn=4):
    """粗检索（**只用于给人看**）：按 param/value 的字面 token 与 quote 重叠度排序。"""
    txt = (param["name"] + " " + str(param.get("value", "")) + " " + param.get("note", "")).lower()
    toks = [t for t in re.split(r"[^\w\u4e00-\u9fff]+", txt) if len(t) >= 2]
    scored = []
    for it in items:
        q = (it.get("quote") or "")
        s = sum(1 for t in toks if t in q.lower())
        s += sum(1 for t in toks if t in (it.get("param") or "").lower())
        if s:
            scored.append((s, it))
    scored.sort(key=lambda x: -x[0])
    return [x[1] for x in scored[:topn]]



# ── 2) 覆盖率体检（2026-09-15 round 13）：代码里的买入侧旋钮 vs 总账 INVENTORY ──
# 目的：objective ① 要求"列出买入链**全部**可调参数"。人工清单会漏 → 这里用 AST 扫一遍，
# 报「代码里有、总账没收录」与「总账提了、代码里找不到」（后者=条目过期）。
BUY_MODULES = [
    # 选股/方向（apps/main_line）
    "apps/main_line/wolf_confirm_pick.py",
    "apps/main_line/wolf_context.py",
    "apps/main_line/wolf_pick_rank_v3.py",
    "apps/main_line/wolf_theme_vol_fund.py",
    "apps/main_line/wolf_ma144_regime.py",
    "apps/main_line/wolf_ma_line_entry.py",
    "apps/main_line/wolf_mainline_select.py",
    "apps/main_line/wolf_wave_gate.py",
    "apps/main_line/wolf_volume_gate.py",
    "apps/main_line/wolf_weekend_hedge.py",
    "apps/main_line/wave_alloc.py",
    "apps/main_line/rotation_universe.py",
    # 布腿/执行（jobs + services）
    "jobs/rotation_switch_arm.py",
    "jobs/shadow_daily.py",
    "backend/app/services/t_gateway.py",
    "backend/app/services/t_monitor.py",
    "backend/app/services/wolf_t_rules.py",
    "backend/app/services/position_tier.py",
    "backend/app/services/wolf_etf_vol.py",
    "backend/app/services/wolf_ticket_ban.py",
    "backend/app/services/wolf_hedge_refill.py",
    "backend/app/api/indicator.py",
]
# 策略类键名前缀（其余 env 键归"非策略类"，不计入覆盖率缺口）
STRAT_PREFIX = ("WOLF_", "T_", "P3_", "ROT_", "SWITCH_", "BUY_", "DIP_", "ETF_")
# 生产买入路径**之外**的模块（回测/数据源/桥接/纪律账本等）从覆盖率里排除：
# 它们不是"买入决策参数"，混进来会把缺口数字灌水（2026-09-15 round 13 实测：不排除时 291 个）
NON_PROD_MODULES = {
    "backtest", "t_backtest", "t_backtest_data", "t_backtest_runner", "t_backtest_report",
    "t_data_sources", "t_bridge", "t_expr", "t_db", "t_build", "t_wake", "t_external_risk",
    "discipline", "direction", "portfolio", "t_scan", "t_scan_coarse", "t_sim", "t_replay",
}


def _walk_buy_sources():
    """扫描面 = 显式 BUY_MODULES + 通配的 wolf_*/t_* 模块（买入路径全部实现处，排除临时脚本）。"""
    import glob as _g
    pats = ["apps/main_line/wolf_*.py", "apps/main_line/t_*.py", "apps/main_line/*.py",
            "backend/app/services/wolf_*.py", "backend/app/services/t_*.py",
            "backend/app/services/daily_decision.py", "backend/app/services/*decision*.py",
            "backend/app/services/position_tier.py", "backend/app/services/stop_loss_monitor.py",
            "backend/app/api/*.py"]
    out = {m for m in BUY_MODULES if os.path.exists(os.path.join(ROOT, m))}
    for pat in pats:
        for f in _g.glob(os.path.join(ROOT, pat)):
            rel = os.path.relpath(f, ROOT)
            if os.path.basename(rel).startswith("_tmp"):
                continue
            out.add(rel)
    return sorted(out)


def scan_knobs(rel_paths=None):
    """扫模块：`os.getenv/os.environ.get` 的键 + 模块级全大写常量（数值/字符串）。"""
    import ast
    out = []
    for rel in (rel_paths or _walk_buy_sources()):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            out.append({"name": rel, "kind": "module_missing", "module": rel, "value": None, "line": 0})
            continue
        src = open(path, encoding="utf-8").read()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            out.append({"name": rel, "kind": "syntax_error", "module": rel, "value": str(e)[:60], "line": e.lineno or 0})
            continue
        mod = os.path.basename(rel).replace(".py", "")
        if mod in ("agent",) or "prompt" in mod or mod.endswith("_config"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
                fn = node.func
                is_getenv = (fn.attr == "getenv" and isinstance(fn.value, ast.Name) and fn.value.id == "os")
                is_env_get = (fn.attr == "get" and isinstance(fn.value, ast.Attribute)
                              and fn.value.attr == "environ"
                              and isinstance(fn.value.value, ast.Name) and fn.value.value.id == "os")
                if not (is_getenv or is_env_get):
                    continue
                a0 = node.args[0]
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    default = None
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        default = node.args[1].value
                    out.append({"name": a0.value, "kind": "env", "module": mod,
                                "value": default, "line": node.lineno})
        for node in tree.body:                       # 只看模块级赋值（函数内的局部阈值另行人工看）
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                nm = node.targets[0].id
                if not (nm.isupper() and len(nm) >= 3 and isinstance(node.value, ast.Constant)):
                    continue
                v = node.value.value
                # 只收**策略数值/短标识**：排除 prompt 文本、URL、路径等
                if isinstance(v, str) and (len(v) > 24 or "\n" in v or "/" in v or ":" in v):
                    continue
                if not isinstance(v, (int, float, str, bool)):
                    continue
                out.append({"name": nm, "kind": "const", "module": mod,
                            "value": v, "line": node.lineno})
    # 去重（同一个键在多处出现只留一条）
    seen, uniq = set(), []
    for k in out:
        key = (k["name"], k["module"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(k)
    return uniq


def coverage_report():
    """→ (未收录, 条目过期, 全部旋钮)。收录判据：名字出现在 INVENTORY 的任意字段文本里。"""
    text = json.dumps(INVENTORY, ensure_ascii=False)
    knobs = scan_knobs()
    missing = [k for k in knobs if k["name"] not in text and k["kind"] != "module_missing"
               and k.get("module") not in NON_PROD_MODULES
               and (k["kind"] == "const" or str(k["name"]).startswith(STRAT_PREFIX))]
    other = [k for k in knobs if k["name"] not in text and k["kind"] == "env"
             and not str(k["name"]).startswith(STRAT_PREFIX)]
    # 总账提到的标识符在代码里找不到 → 条目可能过期（只查带点的 loc 里的 NAME 与 WOLF_* 键）
    import re as _re
    mentioned = set(_re.findall(r"\bWOLF_[A-Z0-9_]+", text)) | set(_re.findall(r"[.\s(]([A-Z][A-Z0-9_]{3,})\b", text))
    names = {k["name"] for k in knobs}
    stale = sorted(n for n in mentioned if n.startswith("WOLF_") and n not in names)
    return missing, stale, knobs, other


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-candidates", default="", help="打印某类参数（P1..P5）的候选原话")
    ap.add_argument("--coverage", action="store_true", help="覆盖率体检：代码旋钮 vs 总账条目")
    ap.add_argument("--json", default="", help="覆盖率体检结果落盘")
    args = ap.parse_args()
    pe = load(PARAMS)
    be = load(BUYEV)
    items = (pe.get("items") or []) + (be.get("items") or [])
    print("[ledger] 语料候选池：参数抽取 %d 条 + 定性抽取 %d 条" % (len(pe.get("items") or []), len(be.get("items") or [])))
    if args.coverage:
        missing, stale, knobs, other = coverage_report()
        env = [k for k in knobs if k["kind"] == "env"]
        const = [k for k in knobs if k["kind"] == "const"]
        print("[coverage] 扫 %d 个买入路径模块：env 键 %d 个 / 模块级常量 %d 个 | 总账收录 %d 条"
              % (len(_walk_buy_sources()), len(env), len(const), len(INVENTORY)))
        print("\n❌ 代码里有、总账**未收录**（%d 个）：" % len(missing))
        for k in sorted(missing, key=lambda x: (x["module"], x["name"])):
            print("   %-38s %-6s %-24s = %s" % (k["name"], k["kind"], k["module"], k["value"]))
        print("\n（另有 %d 个**非策略类** env 键（路径/令牌/超时等）不在统计内）" % len(other))
        print("\n⚠️ 总账提到、代码里找不到的 WOLF_* 键（%d 个，可能条目过期或已改名）：" % len(stale))
        for n in stale:
            print("   " + n)
        if args.json:
            json.dump({"n_modules": len(BUY_MODULES), "n_knobs": len(knobs),
                       "missing": missing, "stale_env_keys": stale},
                      open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("\n[coverage] 写出 %s" % args.json)
        return 0
    if args.dump_candidates:
        for it in (pe.get("items") or []):
            if (it.get("kind") or "").startswith(args.dump_candidates):
                print("%s | %s | %s | %s" % (it.get("date"), it.get("param"), it.get("value"), (it.get("quote") or "")[:90]))
        return 0
    for p in INVENTORY:
        cands = candidates(p, items)
        print("\n=== %s | %s | 当前值=%s" % (p["id"], p["name"], p.get("value")))
        for c in cands:
            print("    候选: %s | %s | %s" % (c.get("date"), (c.get("param") or c.get("kind")), (c.get("quote") or "")[:80]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
