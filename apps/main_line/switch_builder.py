# -*- coding: utf-8 -*-
"""switch_builder.py — 狼大式切换卖旧建新·DRY 评估 (2026-09-07, openspec add-wolf-trial-ladder)

- sell_old: 持仓方向掉出 fusion TOP3 且 stage∈{下跌中,证伪} → 布卖腿(quote.vwap_break, publisher=switch)
- keep   : 掉出但仍强/筑底 → 留
- buy_new: fusion TOP1∪TOP2 内 stock_confirm 突破候选/确认活跃股 → 布 253(custom_m5dump)+254(custom_prevlow) 低吸腿
SWITCH_AUTO_EXEC=1 → 直接布腿到 t_conditions(TMonitor 低吸/卖腿触发执行); 默认 DRY 只写清单。
"""
import json, os, sys


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/app/jobs")   # rotation_switch_arm(生产)
DATA = os.environ.get("DATA_DIR", "/app/data")
PLAN_FILE = os.path.join(DATA, "switch_builder_plan.json")
WEAK_STAGES = ("下跌中", "证伪")
_BOARD_STATS = {"dropped": 0, "codes": set()}      # 板块权限前置过滤统计（供日志）


def board_stats():
    return {"dropped": _BOARD_STATS["dropped"], "codes": sorted(_BOARD_STATS["codes"])}


BASE_STAGES = ("突破候选", "确认")            # 旧口径：只做"已确认/突破候选"（B2 未开时）
DIP_STAGES = ("结构到位", "缩量止跌")          # B2 放宽档：语料"确认后回调低吸"的候选池
HOLD_STAGES = ("缩量止跌", "结构到位", "突破候选", "确认")


def _load(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def fusion_top3():
    # 2026-09-13: 主题来源＝方向层池判定（mainline_top_themes）
    try:
        from mainline_confirm_state import mainline_top_themes
        _gt = mainline_top_themes(3)
        if _gt: return _gt[0]
    except Exception as _e_sil1:
        _silent_alert("switch_builder.py:42", _e_sil1)
    ml = _load("main_line_state.json")
    fus = ml.get("fusion") or {}
    rank = sorted(fus.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))
    return [k for k, _ in rank[:3]]


def held_positions():
    import psycopg2
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()
        # ⚠️ 2026-09-19 修：原先写死 account_id='stock' ⇒ 回放里 build_plan() 会拿**生产账户**的持仓
        #   当"自己的持仓"（实测 drabjan6 空仓，却因此把快克智能/科瑞技术/世纪华通 当成持仓算 sell_old/keep）。
        #   生产 T_MONITOR_ACCOUNT 默认就是 'stock' ⇒ 本修在生产逐位不变，只是回测不再串账户。
        _acct = os.getenv("T_MONITOR_ACCOUNT", "stock")
        cur.execute("SELECT symbol, volume FROM paper_positions WHERE account_id=%s AND volume>0", (_acct,))
        out = [{"symbol": str(r[0]), "volume": int(r[1] or 0)} for r in cur.fetchall()]
        cur.close(); conn.close()
        return out
    except Exception:
        return []


def stock_stage(code6):
    sc = _load("stock_confirm_result.json")
    for v in sc.values():
        if not isinstance(v, dict):
            continue
        for s in (v.get("stocks") or []):
            if str(s.get("code", "")).split(".")[0] == code6:
                return str(s.get("stage") or "")
    return ""


def board_early_on():
    """板块权限**前置过滤**开关（2026-09-19 用户拍板 A：jan10 跑完后另起一版）。

    背景：08:18 switch 布腿路径**完全没有板块过滤**（leg_gate ③ 也没拿到 board 回调）⇒ 写出的腿里
    约一半是账户买不到的板（创业板 300/301、科创板 688、北交所），只能在 09:20/arm 那一步被
    rotation_switch_arm.arm() 以 ARM_SKIP_BOARD 砍掉（实测 0106 写 41 条、其中 18 只买不到）。
    前置后：腿清单只剩能买的，日志/漏斗不再被幻影腿污染（**不改任何判据**，只是把账户已有的
    权限过滤挪到选股阶段）。

    ⚠️ 默认 **0（关）**，且**不跟随 BT_ASOF_FETCH** —— 正在跑的 jan10（B-only 单臂）必须逐位不变；
    要启用请显式 WOLF_PICK_BOARD_EARLY=1（jan11 版）。
    """
    return str(os.getenv("WOLF_PICK_BOARD_EARLY", "0")).strip().lower() in ("1", "true", "yes", "on")


def board_ok(symbol_or_code):
    """账户板块权限（复用唯一实现 rotation_switch_arm.board_ok；退路 wolf_confirm_pick.board_allowed）。

    三处共用同一条口径：选股（wolf_confirm_pick，前置在 max_legs 之前）、09:20 布腿
    （bt_day_legs:397 arm.board_ok）、08:18 switch（本函数，受 WOLF_PICK_BOARD_EARLY 控制）。
    """
    def _xq(x):
        s = str(x or "").strip().upper()
        if not s:
            return ""
        if "." in s:                                  # 300502.SZ → SZ300502
            _c, _m = s.split(".")[0], s.split(".")[-1]
            return _m + _c
        if len(s) == 6 and s.isdigit():               # 裸 6 位 → 推市场（否则创业板会被 fail-open 放过）
            _p = "SH" if s[0] in "569" else ("BJ" if s[:3] == "920" or s[0] in "48" else "SZ")
            return _p + s
        return s
    s = _xq(symbol_or_code)
    if not s:
        return True
    try:
        import sys as _s6
        _jr = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # <repo>/jobs
        if _jr not in _s6.path:
            _s6.path.append(_jr)
        from rotation_switch_arm import board_ok as _bk
        return bool(_bk(s))
    except Exception as _e_sil2:
        _silent_alert("switch_builder.py:119", _e_sil2)
    try:
        from wolf_confirm_pick import board_allowed as _ba
        return bool(_ba(s[2:] + "." + s[:2]))
    except Exception:
        return True


def _dip_on():
    """B2 开关（库内默认关 / 回测 BT_ASOF_FETCH 默认开）。"""
    return str(os.getenv("WOLF_QUALIFIED_THEME_DIP",
                         "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")


def _stages_for_theme_base(theme, dip=None):
    # ⚠️ 原名 `stages_for_theme` ✓ —— 已由下方同名包装函数包一层 ✓（账本 §9.445 ✓）
    """B2（2026-09-19 用户拍板）：**B 合格（结构未破 ∧ 指数未破位）的主题里**，把个股 stage 白名单从
    {确认,突破候选} 放宽到含 {结构到位,缩量止跌} —— 依据语料「再 3-3 还是确认的情况下…把握每一次的
    低吸机会」+ 254「破前低+缩量」买点。**买点本身不变**（仍由 253/254 表达式触发），只是候选宽度回到
    他"确认后回调低吸"的实际做法。开关 WOLF_QUALIFIED_THEME_DIP（库内默认关 / 回测开）。
    """
    on = _dip_on() if dip is None else bool(dip)
    if not (on and theme):
        return BASE_STAGES
    # 丙（WOLF_STAGE_S1_RELAX）：把「止跌待确认」并进低吸腿候选白名单（自设代理，依据见 confirm_chain.s1_relax_on）
    _extra = ()
    try:
        from confirm_chain import s1_relax_on as _s1r
        if _s1r():
            _extra = ("止跌待确认",)
    except Exception:
        _extra = ()
    try:
        import sys as _s5
        _p5 = os.path.dirname(os.path.abspath(__file__))
        if _p5 not in _s5.path:
            _s5.path.insert(0, _p5)
        import wolf_context as _wc
        _ok, _why = _wc.theme_qualify_struct(theme)
        if _ok:
            return BASE_STAGES + DIP_STAGES + _extra
    except Exception as _e_sil3:
        _silent_alert("switch_builder.py:161", _e_sil3)
    return BASE_STAGES



def stages_for_theme(theme, dip=None):
    """账本 §9.445 ✓：在基础白名单上追加「**回踩急杀**」✓（确认链同开关产出 ✓）。

    量化 ✓（§9.444 ✓ 49,942 个「下跌中」票日 ✓）：当日涨幅 ≥ +3% ⇒ T+5 **+1.09%**/54% ✓；
    距 20 日高 ≤ −15% ⇒ **+1.24%**/58% ✓；量比 ≥ 3 倍 ⇒ −0.07% ⇒ 仍拦 ✗
    ⚠️ 不依赖主题资格 ✓（他的「急杀可以买」是行情规则 ✓）；开关默认关 ⇒ 生产逐字不变 ✓
    """
    _base = _stages_for_theme_base(theme, dip)
    try:
        if str(os.getenv("WOLF_STAGE_PULLBACK_REFINE", "0")).strip().lower() in ("1", "true", "yes", "on"):
            if "回踩急杀" not in _base:
                return tuple(_base) + ("回踩急杀",)
    except Exception as _e_sil4:
        _silent_alert("switch_builder.py:179", _e_sil4)
    return _base

def theme_top12(n=2):
    """候选主题来源（top-n）。

    ⚠️ 2026-09-19 发现的历史 bug：本函数原先只 `from mainline_confirm_state import gate_top_themes`，
      却调用**从未导入**的 `mainline_top_themes` ⇒ NameError 被 except 吞掉 ⇒ **一直静默走 fusion 兜底**
      （读 main_line_state.json 的 fusion 排序），文档口径「方向层池判定」其实没生效。
      这里显式开关化，避免"改了来源却以为是同一个"：
        WOLF_THEME_SRC=fusion（默认，= 与历史逐位一致）| pool（= mainline_confirm_state.mainline_top_themes）
    """
    src = str(os.getenv("WOLF_THEME_SRC", "fusion")).strip().lower()
    # ── 乙（2026-09-22 用户拍板"都做"）：主题源改用**逐日 as-of 的 mainline_select** ────────────
    #   问题：默认的 `fusion` 来自 `main_line_state.json` 的 fusion 子字典，而在回放里它是**粘住的**
    #   （实测 data/_bt_year 的 170 个日目录里 fusion 分数向量只有 8 种取值；T5 抽查的 6 天 TOP2
    #    一字不变 = AI/算力/科技 + 半导体/芯片）⇒ 主题门在回放里近乎**常数**，且是**未来快照**。
    #   而同一个文件里的 `mainline_select{mainline, second}` 是**逐日变的**（170 天里 115 天不同，
    #    日期链也对得上：0409 的文件 select_date=20260408）⇒ 拿它当 TOP2 才是 as-of。
    #   开关 `WOLF_THEME_SRC=mainline_select`（库内默认仍是 fusion ⇒ 生产零影响）。
    if src in ("mainline_select", "ms"):
        try:
            _ms = (_load("main_line_state.json") or {}).get("mainline_select") or {}
            _out = [t for t in (_ms.get("mainline"), _ms.get("second")) if t]
            if _out:
                return _out[:n], "mainline_select"
        except Exception as _mse:
            print("[switch_builder] mainline_select 取数失败(回落 fusion): %s" % str(_mse)[:70], flush=True)
    if src == "pool":
        try:
            from mainline_confirm_state import mainline_top_themes
            _gt = mainline_top_themes(n)
            _t = _gt[0] if _gt else None
            if _t:
                return list(_t), "pool"
        except Exception as _e_sil5:
            _silent_alert("switch_builder.py:215", _e_sil5)
    ml = _load("main_line_state.json"); fus = ml.get("fusion") or {}
    return [k for k, _ in sorted(fus.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))[:n]], "fusion"


def active_stocks_by(stages, stages_for=None):
    top12, _src = theme_top12(2)
    sc = _load("stock_confirm_result.json")
    out = {}
    for cname, v in sc.items():
        if not isinstance(v, dict) or v.get("theme") not in top12:
            continue
        _th_here = v.get("theme")
        try:
            _stages = stages_for(_th_here) if callable(stages_for) else stages
        except Exception:
            _stages = stages
        for s in (v.get("stocks") or []):
            st = str(s.get("stage") or "")
            c6 = str(s.get("code", "")).split(".")[0]
            if st in _stages:
                if board_early_on() and not board_ok(s.get("code") or c6):
                    _BOARD_STATS["dropped"] += 1
                    _BOARD_STATS["codes"].add(c6)
                    continue
                out.setdefault(c6, (v.get("theme"), st))
    # ── G3「卖出后删票」黑名单（语料 2025-02-06「卖出然后删票」/ 2025-04-03「破之前新低的直接删票」）──
    #   2026-09-21 用户追问「天津普林/苏州科达这种能挡住吗」时实测发现：本函数（253/254 **低吸腿**的候选域）
    #   **从不读**删票名单 —— 名单只接在 pick_buy(pathA) 与 pick_v2(pathB) 上。
    #   实测后果：苏州科达 drabt3:SH603660 于 20260115 已被删票，T3 在 0122/0123 继续买。
    #   开关 WOLF_TICKET_BAN_FIX（库内默认 0 ⇒ 生产零影响）；失败 fail-open（与修复前一致）。
    try:
        import ban_filter as _bf
        if _bf.enabled():
            _bc = _bf.banned_codes()
            if _bc:
                _drop = [c6 for c6 in out if c6 in _bc]
                for _c6 in _drop:
                    out.pop(_c6, None)
                if _drop:
                    print("[switch_builder] G3 删票过滤 %d 只（账户=%s）: %s"
                          % (len(_drop), _bf.current_account(), sorted(_drop)[:8]), flush=True)
    except Exception as _e:
        print("[switch_builder] 删票过滤异常(fail-open): %s" % str(_e)[:80], flush=True)
    # ── 低吸腿「窄杂毛门」：业绩 bad ∧ PE 高（交集）2026-09-21 用户拍板 ────────────────
    #   量化（台账「续5」）：影响面仅 4%、剔除集净 −9,216（=剔除会赚）、6/8 臂改善；
    #   命中票：天津普林 13 条 −8,987、国星光电 2 条 −1,195、格尔软件 6 条 +967。
    #   开关 WOLF_LOWDIP_JUNK_GATE（库内默认 0 ⇒ 生产零影响）；缺业绩/PE 数据一律放行（fail-open）。
    try:
        import lowdip_junk_gate as _jg
        if _jg.enabled():
            _d = os.getenv("WOLF_ASOF_DAY") or ""
            _drop = []
            for c6 in list(out.keys()):
                _sym = ("SH" if c6[:1] == "6" else "SZ") + c6
                _ok, _why = _jg.blocked(_sym, day=(_d or None))
                if _ok:
                    out.pop(c6, None); _drop.append((c6, _why))
            if _drop:
                print("[switch_builder] 窄杂毛门剔除 %d 只: %s" % (len(_drop), _drop[:6]), flush=True)
    except Exception as _e2:
        print("[switch_builder] 窄杂毛门异常(fail-open): %s" % str(_e2)[:80], flush=True)
    return out, top12


def symbol_themes_of(symbol):
    try:
        from sector_g3 import symbol_themes
        return symbol_themes(symbol)
    except Exception:
        return []


def _arm_legs(plan):
    """SWITCH_AUTO_EXEC=1: 卖旧→custom(vwap_break)卖腿; 买新→253/254 低吸腿(t_conditions, publisher=switch)"""
    import psycopg2
    from datetime import datetime
    try:
        from rotation_switch_arm import expire_old, arm, SELL_EXPR, BUY_253_EXPR, BUY_254_EXPR
    except Exception:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jobs"))
            from rotation_switch_arm import expire_old, arm, SELL_EXPR, BUY_253_EXPR, BUY_254_EXPR
        except Exception as ex:
            return [{"err": str(ex)[:120]}]
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = True
    cur = conn.cursor(); today = datetime.now().strftime("%Y%m%d")
    expire_old(cur, today)
    armed = []
    for s in plan.get("sell_old") or []:
        rid = arm(conn, cur, s["symbol"], "custom", "sell", SELL_EXPR, today)
        armed.append({"type": "sell_vwap_break", "symbol": s["symbol"], "id": rid})
    # 2026-09-26：读「埋伏候选」（判级侧写的 as-of 文件 ✓）⇒ 这些票发**独立腿型** `wolf_ambush_buy`
    #   语义：低位埋伏**先手建仓**（语料「低位方向：找辨识度最高老龙头埋伏」✓）
    #   ⇒ 必须与普通低吸腿区分开：它**豁免**「低吸只在趋势腿已建仓的票上做」那道闸 ✓
    #     （否则埋伏腿会被自己的闸挡死 ✗ —— 实测 0202/0203 因此 0 交易 ✗）
    _ambush = {str(x) for x in (plan.get("ambush") or [])}      # 主路径：plan 携带 ✓
    if not _ambush:
        try:
            import json as _js3
            _ap2 = os.path.join(os.environ.get("DATA_DIR") or ".", "ambush_candidates.json")
            if os.path.exists(_ap2):
                _ambush = {str(x) for x in (_js3.load(open(_ap2, encoding="utf-8")) or {}).get("symbols") or []}
        except Exception as _ae2:
            print("[switch_builder] 埋伏候选读取失败(按无埋伏处理): %s" % str(_ae2)[:70], flush=True)
    print("[switch_builder] _arm_legs 埋伏名单 %d 只" % len(_ambush), flush=True)
    for b in plan.get("buy_new") or []:
        code = b["code"]
        sym = ("SH" if str(code)[0] == "6" else "SZ") + str(code)
        if sym in _ambush or str(code) in _ambush:
            rid_a = arm(conn, cur, sym, "wolf_ambush_buy", "buy", BUY_253_EXPR, today)
            armed.append({"type": "wolf_ambush_buy", "symbol": sym, "253": rid_a})
            continue
        rid1 = arm(conn, cur, sym, "custom_m5dump", "buy", BUY_253_EXPR, today)
        rid2 = arm(conn, cur, sym, "custom_prevlow", "buy", BUY_254_EXPR, today)
        armed.append({"type": "buy_253/254", "symbol": sym, "253": rid1, "254": rid2})
    cur.close(); conn.close()
    return armed


def _timing_on() -> bool:
    return str(os.getenv("WOLF_STEP_TIMING", "0")).strip().lower() in ("1", "true", "yes", "on")


def build_plan():
    import time as _tt
    _T0 = _tt.time()
    def _tp(_lab):
        if _timing_on():
            print("[timing] build_plan.%-22s %.2fs" % (_lab, _tt.time() - _T0), flush=True)
    top3 = fusion_top3()
    _tp("fusion_top3")
    tiers = {}
    sell_old, keep = [], []
    for p in held_positions():
        sym = p["symbol"]; c6 = "".join(ch for ch in sym if ch.isdigit())[:6]
        ths = symbol_themes_of(sym)
        stage = stock_stage(c6)
        try:
            from tranche_ladder import tier_for
            tier, why = tier_for(sym)
        except Exception:
            tier, why = "?", "err"
        tiers[sym] = {"tier": tier, "themes": ths, "stage": stage, "why": why}
        dropped = not (set(ths) & set(top3))
        if dropped:
            if stage in WEAK_STAGES:
                sell_old.append({"symbol": sym, "stage": stage, "themes": ths})
            elif stage in HOLD_STAGES or tier in ("ambush", "trial", "normal"):
                keep.append({"symbol": sym, "stage": stage, "themes": ths})
            else:
                keep.append({"symbol": sym, "stage": stage or "unknown(留观察)", "themes": ths})
        else:
            keep.append({"symbol": sym, "stage": stage or "主线内", "themes": ths})
    _tp("held/tiers")
    buy_new, _ = active_stocks_by(BASE_STAGES, stages_for=stages_for_theme)
    # 2026-09-26 用户「做掉」：把「**埋伏候选**」显式并入买入列表 ✓
    #   为什么必须显式并：它们的判级结果可能**不在 BASE_STAGES 内**（如"下跌中"✗）
    #     ⇒ `active_stocks_by` 会漏掉它们 ✗ —— 实测：埋伏候选文件每天都写 ✓ 但
    #       `t_conditions` 里 **0 条** `wolf_ambush_buy` ✗，就是这里没接 ✓
    #   语义：低位方向「找辨识度最高老龙头**埋伏**」✓ = **先手建仓**（不是摊薄 ✗）
    #   防呆：只并入**确属主线主题**（top3 ∩ 该票自身主题）的票 ✓ ——
    #     否则执行口的「主营/主类校验」会把它挡掉 ✗（白排一条腿 ✓）
    try:
        import json as _js4
        _ap3 = os.path.join(os.environ.get("DATA_DIR") or ".", "ambush_candidates.json")
        if os.path.exists(_ap3):
            _amb = (_js4.load(open(_ap3, encoding="utf-8")) or {}).get("symbols") or []
            print("[switch_builder] 埋伏候选文件读到 %d 只" % len(_amb), flush=True)
            _added = []
            for _s in _amb:
                _c6 = "".join(ch for ch in str(_s) if ch.isdigit())[:6]
                if not _c6 or _c6 in buy_new:
                    continue
                # 主题必须取**判级结果里的真实主题** ✓ —— 不能用"该票第一个概念" ✗：
                #   实测（T35 二月）`LEG_REJECT … ambush: 主营/主类校验 主类=基础化工
                #   不属主题[AI/算力/科技]` ✗ ⇒ **391 条埋伏腿被写腿闸拒**（其中 ~105 条是此因 ✓）
                #   正规候选的主题正是来自 `stock_confirm_result.json` ✓ ⇒ 这里也用同一来源 ✓
                #   取不到 ⇒ **不并**（宁可不发腿，也不发一条必被拒的 ✗）
                _judged_theme = None
                try:
                    _sc = _load("stock_confirm_result.json") or {}
                    for _cn2, _v2 in _sc.items():
                        if not isinstance(_v2, dict):
                            continue
                        for _s2 in (_v2.get("stocks") or []):
                            _c2 = "".join(ch for ch in str(_s2.get("code") or _s2.get("symbol") or "") if ch.isdigit())[:6]
                            if _c2 == _c6:
                                _judged_theme = _v2.get("theme") or _cn2
                                break
                        if _judged_theme:
                            break
                except Exception as _e2:
                    _judged_theme = None
                if not _judged_theme:
                    continue
                # 2026-09-26 修（用户「都按你说的来」✓）：主题必须**同时是该票自己的主题** ✓
                #   实测 SZ002028：判级归入[半导体/芯片] ✗，而**执行口**按 `wolf_context.theme_of_symbol`
                #   判它属[电网设备] ⇒ `rejected 腿批准闸(执行口): 主营/主类校验` ✗（写腿口放行、执行口拒绝 ✗）
                #   统一为「**判级主题 ∩ 该票自身主题**」⇒ 交集为空则**不并** ✓
                try:
                    _own = set(symbol_themes_of(("SH" if _c6[0] == "6" else "SZ") + _c6) or [])
                except Exception:
                    _own = set()
                if _own and _judged_theme not in _own:
                    print("[switch_builder] 埋伏候选跳过（主题口径不一致）：%s 判级=%s 自身=%s"
                          % (_c6, _judged_theme, ",".join(sorted(_own))[:40]), flush=True)
                    continue
                buy_new[_c6] = [_judged_theme, "ambush"]
                _added.append(_c6)
            if _added:
                _added_ambush = list(_added)
                print("[switch_builder] 埋伏候选并入买入列表 %d 只：%s"
                      % (len(_added), ", ".join(_added[:6])), flush=True)
    except Exception as _ae3:
        print("[switch_builder] 埋伏候选并入失败: %s" % str(_ae3)[:70], flush=True)
    _tp("active_stocks_by")
    try:
        from tranche_ladder import escalate_signal, sync_normal_upgrade
        esc, trig, upgraded = sync_normal_upgrade()
    except Exception:
        esc, trig, upgraded = False, [], []
    _tp("escalate/normal_upgrade")
    auto_exec = os.getenv("SWITCH_AUTO_EXEC", "0") == "1"
    plan = {"top3": top3,
            # 2026-09-26：埋伏名单**随 plan 传递** ✓（比让 `_arm_legs` 再读文件可靠 ✓ ——
            #   实测两处进程的 DATA_DIR 不一致 ⇒ 读文件读空 ✗ ⇒ 全按普通低吸布腿 ✗）
            "ambush": list(locals().get("_added_ambush") or []),
            "sell_old": sell_old, "keep": keep,
            "buy_new": [{"code": k, "theme": v[0], "stage": v[1]} for k, v in buy_new.items()],
            "tiers": tiers,
            "escalate": {"ok": esc, "triggers": trig, "upgraded": upgraded},
            "auto_exec": auto_exec, "dry": not auto_exec, "armed": []}
    if auto_exec:
        _t_arm = _tt.time()
        plan["armed"] = _arm_legs(plan)
        if _timing_on():
            print("[timing] build_plan.%-22s %.2fs（%d 条腿）"
                  % ("_arm_legs(布条件单)", _tt.time() - _t_arm, len(plan["armed"] or [])), flush=True)
    _tp("总计")
    try:
        with open(PLAN_FILE, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
    except Exception as _e_sil6:
        _silent_alert("switch_builder.py:459", _e_sil6)
    return plan


if __name__ == "__main__":
    plan = build_plan()
    print("TOP3:", plan["top3"])
    print("sell_old:", plan["sell_old"])
    print("keep:", plan["keep"])
    print("buy_new:", plan["buy_new"])
    print("escalate:", plan["escalate"])
    print("armed:", plan["armed"])
    print("WROTE", PLAN_FILE)
