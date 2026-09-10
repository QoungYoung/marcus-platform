# -*- coding: utf-8 -*-
"""rotation_switch_arm.py — 主线内切换·动态布腿器（决策→TMonitor腿，非直接下单）

链：决策(规则/信号层，等价 auto_trade Pi 上下文) → 布腿到 t_conditions(account=stock)
  → TMonitor 30s 轮询触发卖旧/买新（卖=quote.vwap_break；买=index.m5_dump 253 / dip_prev_low 254）

动作：
  1) expire 昨日 publisher='switch' 的旧腿
  2) 卖侧：当前持仓落在 拥挤无空间/出货链 → 布 vwap_break 卖腿
  3) 买侧：room/holdT 且(主线 或 非build+健康 的防御/资源二线) → 选 LOW/MID 非拥挤 ≤3 只，
     每只布 m5dump(253) + dip_prev_low(254) 两条买腿
安全：本脚本只布腿不直接下单；执行由 TMonitor/t_gateway 完成。
用法: python -u jobs/rotation_switch_arm.py  （SWITCH_ARM_DRY=1 只输出不写库）
"""
import os, sys, json, time
sys.path.insert(0, "/app/app")
sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "data")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")

def load(name):
    try:
        return json.load(open(os.path.join(DATA, name), encoding="utf-8"))
    except Exception:
        return {}

def save(name, obj):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        return True
    except Exception as e:
        print("[switch_arm] save %s 失败: %s" % (name, str(e)[:80]), file=sys.stderr)
        return False

def norm(s):
    return str(s).replace(" ", "").replace("　", "")

def _today():
    from datetime import date
    return date.today().strftime('%Y%m%d')

def held_positions():
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT symbol, volume FROM paper_positions WHERE account_id='stock' AND volume>0")
        out = [{"symbol": str(r[0]), "volume": int(r[1] or 0)} for r in cur.fetchall()]
        cur.close(); conn.close(); return out
    except Exception:
        return []

def concepts_map():
    out = {}
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT ts_code, concept_name FROM stock_concept_map")
        for ts, cn in cur.fetchall():
            out.setdefault(str(ts), []).append(str(cn))
        cur.close(); conn.close()
    except Exception:
        pass
    return out

def chains_of(sym, cm):
    from rotation_universe import get_sub_universe
    SU = get_sub_universe()
    ts = sym[2:] + "." + sym[:2]
    names = cm.get(sym, []) + cm.get(ts, [])
    return [sub for sub, kws in SU.items() if any(norm(k) in norm(n) for n in names for k in kws)]

def bad_set():
    bad = set()
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT ts_code FROM stock_pool WHERE is_st=1 OR name LIKE 'ST%' OR name LIKE '*ST%'")
        bad |= {str(r[0]) for r in cur.fetchall()}
        cur.execute("SELECT symbol FROM risk_flags WHERE flag_type='is_st' AND value='1' OR flag_type='earnings_bad'")
        bad |= {str(r[0]) for r in cur.fetchall()}
        cur.close(); conn.close()
    except Exception:
        pass
    return bad

def _gz(api, params, fields):
    """gzcloud 单次查询(gzip/重定向容错)。返回 items 列表。"""
    import urllib.request, gzip
    body = {"api_name": api, "token": os.getenv("TUSHARE_TOKEN", ""),
            "params": params, "fields": fields}
    req = urllib.request.Request((os.getenv("TUSHARE_API_URL") or "https://ts.gyzcloud.top/api"),
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    if raw[:2] == bytes([0x1f, 0x8b]):
        raw = gzip.decompress(raw)
    return (json.loads(raw.decode()).get("data", {}) or {}).get("items") or []


def _pct_rank(vals):
    """升序 → 百分位 0-1。**并列取平均名次**(与 wolf_confirm_pick.pct_rank 同口径)。

    P1-5b 配套修复: 原实现并列时按列表位置定序, 导致大量并列的因子(如 lim 在多数票上恒 0、
    amt20 相近)会把"序位噪声"注入 leader, 反而盖过真正区分龙头的 r60 —— 与狼大"龙头优先"相悖。
    改为并列同名次后, 并列因子对排序不再产生方向性偏置。
    """
    n = len(vals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: vals[i])
    rk = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0          # 1-based 平均名次
        for k in range(i, j + 1):
            rk[order[k]] = avg_rank / n
        i = j + 1
    return rk


def pick_buy(chain, exclude, limit=3):
    """路径A 低吸选股(rotation 链关键词匹配的候选域)。

    P1-5b(2026-09-10 修, 依狼大 2026-01-16「后排反倒不能去 要看好龙头那些 /
    龙头和核心都救不起来 那其他后排还要死」):
      原实现直接取 stock_concept_map 扫描序的前 80 只(cands[:80]) —— 等于按字典序挑票,
      与狼大"龙头优先"相反, 也与路径B(pick_v2: leader 榜 → 组内前2 → 位置闸)口径不一致。
      现改为三段式, 与路径B 同口径:
        ① 市值预筛(一次 daily_basic 全市场调用, 廉价): 龙头通常是核心/大市值票;
        ② 对预筛集逐股取日线, 按 pick_v2 相同的三因子(r60/amt20/lim 分位均值)算 leader;
        ③ 按 leader 降序过位置闸(LOW/MID), 取前 limit。
    规模由 env WOLF_PICK_BUY_SHORTLIST 控制(默认 80, 即原来的逐股取数上限)。
    """
    from rotation_universe import get_sub_universe
    kws = get_sub_universe().get(chain) or []
    if not kws:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT concept_name, ts_code FROM stock_concept_map")
        cm = {}
        for cn, ts in cur.fetchall():
            cm.setdefault(str(ts), []).append(str(cn))
        cur.close(); conn.close()
    except Exception:
        return []
    bad = bad_set()
    bl = load("crowding_blacklist.json") or {}
    detail = bl.get("symbols_detail") or {}
    cands = []
    for ts, names in cm.items():
        if any(norm(k) in norm(n) for n in names for k in kws):
            xq = "SH" + ts[:6] if ts.endswith(".SH") else ("SZ" + ts[:6] if ts.endswith(".SZ") else ts)
            if xq not in exclude and ts not in detail and ts not in bad:
                cands.append((ts, xq))
    if not cands:
        return []
    # ① 市值预筛(单次全市场调用): 龙头优先于字典序
    shortlist_n = int(os.getenv("WOLF_PICK_BUY_SHORTLIST", "80"))
    try:
        mv = {str(x[0]): float(x[1] or 0) for x in _gz("daily_basic", {"trade_date": _today()}, "ts_code,total_mv")}
        if mv:
            cands.sort(key=lambda tx: -(mv.get(tx[0]) or 0))
    except Exception as _e:
        print("[pick_buy] daily_basic 预筛失败, 退回原序:", str(_e)[:80], file=sys.stderr)
    cands = cands[:shortlist_n]
    # ② 取日线 → 三因子
    stats = []
    for ts, xq in cands:
        try:
            rows = _gz("daily", {"ts_code": ts, "start_date": "20250101", "end_date": _today()},
                       "ts_code,trade_date,close,amount")
            rows = sorted(rows, key=lambda x: str(x[1]))
            if len(rows) < 61:
                time.sleep(0.1); continue
            closes = [float(x[2]) for x in rows]
            amts = [float(x[3] or 0) for x in rows]
            r60 = (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else None
            amt20 = sum(amts[-20:]) / 20.0
            lim = sum(1 for i in range(max(1, len(closes) - 60), len(closes))
                      if closes[i - 1] and closes[i] / closes[i - 1] - 1 >= 0.097)
            stats.append({"ts": ts, "xq": xq, "closes": closes,
                          "r60": r60, "amt20": amt20, "lim": lim})
        except Exception:
            pass
        time.sleep(0.1)
    if not stats:
        return []
    for key in ("r60", "amt20", "lim"):
        rk = _pct_rank([(s[key] if s[key] is not None else -1e9) for s in stats])
        for s, v in zip(stats, rk):
            s[key + "_p"] = v
    for s in stats:
        s["leader"] = round((s["r60_p"] + s["amt20_p"] + s["lim_p"]) / 3.0, 4)
    stats.sort(key=lambda s: (-s["leader"], s["ts"]))
    # ③ 按 leader 降序过位置闸
    out = []
    for s in stats:
        if len(out) >= limit:
            break
        try:
            import pandas as pd
            import position_class as pc
            f = pc.position_features(pd.Series(s["closes"]))
            pos = pc.classify(f)["position"] if f else None
        except Exception:
            pos = None
        if pos in ("LOW", "MID"):
            out.append({"symbol": s["xq"], "ts_code": s["ts"], "position": pos,
                        "chain": chain, "leader": s["leader"],
                        "r60": round(s["r60"], 1) if s["r60"] is not None else None,
                        "amt20": round(s["amt20"], 2)})
    return out


def confirm_pick(theme, exclude, limit=2, concepts=None):
    """曾确认主题低吸选股: THEME_CONCEPTS 成分 -> 过滤 bad/blacklist/held -> position LOW/MID, 至多 limit 只"""
    # 2026-09-09 狼大化 v2 (A=确认链/B=等权leader/C=容量提示/D=20日成交额>=1亿硬切); WOLF_PICK_LEGACY=1 回退旧版
    if os.getenv("WOLF_PICK_LEGACY", "0") != "1":
        _st = {}
        try:
            from wolf_confirm_pick import pick_v2 as _v2
            _p = _v2(theme, exclude=list(exclude or []), limit=limit, concepts=concepts, status_out=_st)
        except Exception as _e:
            print("WOLF_PICK_V2_ERR", theme, str(_e)[:200], file=sys.stderr)
            _p = None
        if _p is not None:
            if _p:
                return _p
            # 2026-09-10(P0-4): v2 返回空列表有两种截然不同的语义, 旧代码用 `if _p: return _p`
            # 把它们混为一谈并静默回落 legacy DB 扫描序 → 位置闸否掉全部时反而去买后排。
            #   ① no_universe / no_scored = 确认域/候选数据缺失 → 允许回落 legacy(否则数据问题=全天不布腿)
            #   ② ok(位置闸/选择层闸否掉全部) = 狼大"买不到位置就等" → 必须等待, 不得回落扫描序
            if _st.get("status") in ("no_universe", "no_scored"):
                print("WOLF_PICK_V2_DATAGAP_FALLBACK", theme, _st.get("status"), "-> legacy", file=sys.stderr)
            elif os.getenv("WOLF_PICK_EMPTY_WAIT", "1") == "1":
                print("WOLF_PICK_V2_EMPTY_WAIT", theme, "rs_rej=%s" % _st.get("rs_rejected"),
                      "t1=%s" % _st.get("t1"), "-> 空窗等待(不回落 legacy)", file=sys.stderr)
                return []
            else:
                print("WOLF_PICK_V2_EMPTY_FALLBACK", theme, "(WOLF_PICK_EMPTY_WAIT=0) -> legacy", file=sys.stderr)
    from fusion_mainline import THEME_CONCEPTS as TC
    from rotation_universe import get_sub_universe  # noqa (保持 universe 加载一致性)
    cons = concepts if concepts is not None else TC.get(theme, [])
    if not cons: return []
    import psycopg2
    ph = ','.join(['%s'] * len(cons))
    # 核心概念优先(2026-09-09: 种植/粮食/水产为核心跟涨分支, 农化/乳业为边缘补涨)
    core_cons = [x for x in cons if any(k in x for k in ('种植', '粮食', '水产'))]
    conn = psycopg2.connect(DB); cur = conn.cursor()
    if core_cons:
        ph2 = ','.join(['%s'] * len(core_cons))
        cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (" + ph2 + ")", core_cons)
        members = [str(r[0]) for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (" + ph + ") AND ts_code NOT IN (SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (" + ph2 + "))", cons + core_cons)
        members += [str(r[0]) for r in cur.fetchall()]
    else:
        cur.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name IN (" + ph + ")", cons)
        members = [str(r[0]) for r in cur.fetchall()]
    cur.close(); conn.close()
    bad = bad_set()
    bl = load("crowding_blacklist.json") or {}
    detail = bl.get("symbols_detail") or {}
    out = []
    scanned = 0
    for ts in members:
        if len(out) >= limit or scanned >= 60: break
        if ts in bad or ts in detail: continue
        xq = ("SH" if ts.endswith('.SH') else 'SZ') + ts[:6]
        if xq in exclude: continue
        scanned += 1
        try:
            import pandas as pd
            import position_class as pc
            body = {"api_name": "daily", "token": os.getenv("TUSHARE_TOKEN", ""),
                    "params": {"ts_code": ts, "start_date": "20250101",
                               "end_date": _today()},   # P1-5a 修复: 原硬编码 "20260908" 冻结日线窗口
                    "fields": "ts_code,trade_date,close"}
            import urllib.request
            req = urllib.request.Request((os.getenv("TUSHARE_API_URL") or "https://ts.gyzcloud.top/api"), data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
            raw = urllib.request.urlopen(req, timeout=30).read()
            import gzip
            if raw[:2] == bytes([0x1f, 0x8b]): raw = gzip.decompress(raw)
            d = json.loads(raw.decode())
            rows = d.get("data", {}).get("items") or []
            if len(rows) < 60: time.sleep(0.1); continue
            ser = pd.Series([float(x[2]) for x in rows], index=pd.to_datetime([str(x[1]) for x in rows], format="%Y%m%d"))
            f = pc.position_features(ser)
            pos = pc.classify(f)["position"] if f else None
            if pos in ("LOW", "MID"):
                out.append({"symbol": xq, "ts_code": ts, "position": pos, "theme": theme})
        except Exception:
            pass
        time.sleep(0.1)
    return out

def gate_confirmed_today(today):
    """最近(<=today) mainline_gate json 的 confirmed_candidate 主题集; 无则空"""
    import glob
    best = None; best_d = ''
    for f in glob.glob(os.path.join(DATA, 'mainline_gate_*.json')):
        d = os.path.basename(f)[14:22]
        if d <= today and d > best_d: best_d = d; best = f
    if not best: return set()
    try:
        g = json.load(open(best, encoding='utf-8'))
        return {r.get('theme') for r in g.get('rows', []) if r.get('verdict') == 'confirmed_candidate'}
    except Exception:
        return set()

def theme_of_chain(c):
    from fusion_mainline import THEME_CONCEPTS
    for th, cons in THEME_CONCEPTS.items():
        if c in cons: return th
    return None

SELL_EXPR = {"op": "==", "field": "quote.vwap_break", "value": True}
BUY_253_EXPR = {"and": [{"op": ">=", "field": "index.m5_dump", "value": 0.4},
                        {"op": ">", "field": "quote.average", "value": 0},
                        {"op": ">", "field": "quote.current", "value": 0}]}
BUY_254_EXPR = {"and": [{"op": "==", "field": "quote.dip_prev_low", "value": True},
                        {"op": ">", "field": "vol_ratio", "value": 0},
                        # 2026-09-07 对齐狼大温和缩量(≤0.9, 同 zheng_t_buy_quote)：0.7 档过严——
                        # 药明换手节奏比 0.71~0.85 属缩量却被挡, 触前低+温和缩量应放行
                        {"op": "<=", "field": "vol_ratio", "value": 0.9},
                        {"op": ">", "field": "quote.average", "value": 0},
                        {"op": ">", "field": "quote.current", "value": 0}]}

def expire_old(cur, today):
    cur.execute("UPDATE t_conditions SET status='expired' WHERE account_id='stock' AND publisher='switch' AND status='active' AND trade_date < %s", (today,))

def arm(db, cur, symbol, trigger_kind, direction, expr, trade_date):
    # 2026-09-10 账户权限: 无权限板块(创业板/科创板/北交所)不布腿
    _ex = [x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()]
    _s = str(symbol); _p = _s[:2]; _c = _s[2:8]
    if (("cyb" in _ex and _p == "SZ" and _c[:3] in ("300", "301"))
            or ("kcb" in _ex and _p == "SH" and _c.startswith("688"))
            or ("bj" in _ex and (_p == "BJ" or _c[:3] == "920" or _c[:1] in ("4", "8")))):
        print("ARM_SKIP_BOARD", symbol, file=sys.stderr)
        return None
    from app.services import t_db
    cond = {"account_id": "stock", "symbol": symbol, "trade_date": trade_date,
            "trigger_kind": trigger_kind, "direction": direction,
            "expression": expr, "status": "active", "armed": 1,
            "publisher": "switch"}
    # 2026-09-09 修复: arm 当日新布缺 benchmark -> vol_ratio 用 MIN_TURNOVER_BASE(0.5%) 兜底
    # 被放大~10x -> vol_ratio<=0.9 恒不成立 -> 254 腿布腿当天全程哑火(478-489 全 0 触发;
    # 对照 07:26 switch_builder 带 benchmark 的腿 254 正常触发)。与 TMonitor 跨日结转同函数补基准。
    try:
        from app.services.t_turnover_profile import compute_turnover_profile
        _p = compute_turnover_profile(symbol)
        if _p:
            cond["benchmark_turnover_profile"] = _p
    except Exception as _e:
        print("ARM_BENCH_ERR", symbol, str(_e)[:100], file=sys.stderr)
    return t_db.upsert_condition(cond)

def main():
    dry = os.getenv("SWITCH_ARM_DRY", "0").strip() in ("1", "true", "yes")
    wave = load("wave_state.json"); ru = load("rotation_universe_result.json")
    ml = load("main_line_state.json")
    wop = str(wave.get("operation") or "side").lower()
    crowded = ru.get("crowded_top") or []; room = ru.get("room_bottom") or []
    holdT = ru.get("holdT_top") or []
    healthy = bool(ru.get("rotation_healthy")); sucking = bool(ru.get("mainline_sucking"))
    positions = held_positions(); cm = concepts_map(); today = _today()
    # 卖侧 legs
    sell_legs = []
    for p in positions:
        chs = chains_of(p["symbol"], cm)
        hit = [c for c in chs if c in crowded or c in holdT]
        if hit:
            sell_legs.append({"symbol": p["symbol"], "chain": hit[0]})

    # ── P1-1 卖侧(2026-09-10): 狼大「龙头风向标死了就不能做了…千万不要想高切低, 麻溜的跑就行」──
    # 范围 = **该主题的全部持仓**, 不只是风向标那一只。
    #   依据 2026-01-16「后排反倒不能去 要看好龙头那些 龙头和核心都救不起来 那其他后排还要死」;
    #        2026-01-13「说的是主线题材 题材 题材」→ 风向标是**主题**级信号。
    # 时点: wind_broken 是**收盘口径**, 故只在 09:20 本处判定一次, 不放盘中(避免用旧数据反复触发假信号)。
    # 注意: 腿路径的所有卖腿都保留 100 股工程底仓 → L2「清底仓」走不了腿路径, 改为输出指令由 agent/人工执行。
    theme_holds = {}
    for p in positions:
        for c in chains_of(p["symbol"], cm):
            th = theme_of_chain(c)
            if th:
                theme_holds.setdefault(th, set()).add(p["symbol"])
    wind_dead = {}
    for th in sorted(theme_holds):
        try:
            from wolf_confirm_pick import pick_v2 as _v2
            _st = {}
            _v2(th, exclude=set(), limit=1, status_out=_st)
            if _st.get("wind_broken"):
                wind_dead[th] = {"wind_symbol": _st.get("wind_symbol"), "wind_name": _st.get("wind_name"),
                                 "dist_prevlow_prev": _st.get("wind_dist_prevlow_prev")}
        except Exception as _we:
            print("WIND_CHECK_ERR", th, str(_we)[:120], file=sys.stderr)
    _prev = load("wolf_wind_state.json") or {}
    _wn = {}
    for th, info in wind_dead.items():
        _d = _prev.get(th) or {}
        # 本任务每个交易日只跑一次 → 连续运行次数即连续交易日数(周末/节假日不跑, 天然跳过)
        _days = int(_d.get("dead_days") or 1) if str(_d.get("last_dead") or "") == today \
            else int(_d.get("dead_days") or 0) + 1
        _wn[th] = dict(info, dead_days=_days, first_dead=_d.get("first_dead") or today,
                       last_dead=today, level=2 if _days >= 2 else 1)
    if wind_dead or _prev:
        save("wolf_wind_state.json", _wn)     # 已收复的主题自动消失(不再禁补)
    for th, st in _wn.items():
        syms = sorted(theme_holds.get(th, []))
        for sym in syms:
            sell_legs.append({"symbol": sym, "chain": th, "wind_dead": True, "level": st["level"],
                              "reason": "风向标死[%s]%s L%d(%d日)"
                                        % (th, st.get("wind_name") or st.get("wind_symbol") or "", st["level"], st["dead_days"])})
        if st["level"] >= 2:
            print("WIND_DEAD_L2_BASE_EXIT", th, syms,
                  "→ L2: 次日收盘仍未收复, 应清底仓(腿路径保留100股工程底仓, 需 agent/人工执行)", file=sys.stderr)
        print("WIND_DEAD sell_legs", th, syms, "level", st["level"], file=sys.stderr)
    # 买侧候选链
    buy_chains = []
    # 2026-09-09 Wolf 低吸资格闸(见 docs/wolf-dip-entry-rule.md): 主线低吸只放行"曾确认"主题
    # (mainline_confirm_history 窗内 confirmed_candidate); MAINLINE_QUALIFY=0 回退旧 main_line_state 逻辑
    qualify = os.getenv("MAINLINE_QUALIFY", "1").strip() in ("1", "true", "yes")
    confirmed_today_set = gate_confirmed_today(today)
    if confirmed_today_set:
        print("GATE_CONFIRMED_TODAY", sorted(confirmed_today_set), file=sys.stderr)
    for c in room + holdT:
        old_is_main = any(t in c or c in t for x in [str(ml.get("main_line") or ""), " ".join(str(v) for v in (ml.get("candidates") or []))] for t in x.split("/"))
        is_main = old_is_main
        skip_reason = None
        if qualify:
            th0 = theme_of_chain(c)
            if th0 is not None:
                if th0 in confirmed_today_set:
                    is_main = True
                else:
                    is_main = False                 # 仅今日 confirmed 主题放行主线低吸; 曾确认但结构回落(watch)不布新建腿
                    skip_reason = "not_today_confirmed:" + th0
        if is_main:
            buy_chains.append((c, "mainline"))
        elif skip_reason:
            print("SKIP_MAINLINE_LOWBUY", c, skip_reason, file=sys.stderr)
        elif wop in ("t_only", "side", "defense", "exit") and healthy and not sucking:
            buy_chains.append((c, "defensive_resource"))
    held_syms = {p["symbol"] for p in positions}
    buy_legs = []
    for chain, side in buy_chains[:2]:
        for cand in pick_buy(chain, exclude=held_syms, limit=3):
            buy_legs.append({"symbol": cand["symbol"], "chain": chain, "side": side})
    # 2026-09-09 曾确认主题低吸候选池(实盘布腿, 全池<=2只; 农业 confirmed 优先):
    # 主线门结果(mainline_gate json) -> 今日 confirmed_candidate 主题优先, 其次曾确认窗内主题
    pool = [t for t in confirmed_today_set if t != "银行"]  # 今日 confirmed 主题低吸池(1-2只控制)
    # P2-2(2026-09-10): 主线确认池也要过"主题可买"门 = **结构**(P1-3) ∧ **资金**(P2-2 连续净流出)。
    # 狼大 2025-03-06「你首先得判断现在大盘行情没有危险 **板块没有危险** 那就可以做」。
    # 单一定义处: wolf_context.theme_buyable —— 253 的 m5dump_allowed 也调它, 两条路径不会分叉。
    # 注: 本门作用于 254/253 的**新开低吸腿**; 卖侧与已有持仓不受影响。
    if pool:
        try:
            from wolf_context import theme_buyable
            _ok_pool = []
            for th in pool:
                _ok, _why = theme_buyable(th)
                if _ok:
                    _ok_pool.append(th)
                else:
                    print("SKIP_THEME_NOT_BUYABLE", th, _why, file=sys.stderr)
            pool = _ok_pool
        except Exception as _e:
            print("THEME_BUYABLE_ERR(放行)", str(_e)[:120], file=sys.stderr)
    if qualify and pool:
        # B(2026-09-09): 等待池分批——tier1严格前2 + tier2接近档补位至 ROT_POOL_LEGS(默认4);
        # 成交节奏由资金闸兜底(probe<=5%预算尽自动停), 狼大'埋伏一批等位置'
        pool_legs = int(os.getenv("ROT_POOL_LEGS", "4"))
        print("CONFIRMED_POOL", pool, "pool_legs", pool_legs, file=sys.stderr)
        got = 0
        for th in pool:
            if got >= pool_legs: break
            _pick = confirm_pick(th, exclude=held_syms, limit=pool_legs - got)
            if not _pick and dry:
                print("CONFIRM_PICK_EMPTY", th, file=sys.stderr)
            for cand in _pick:
                if got >= pool_legs: break
                buy_legs.append({"symbol": cand["symbol"], "chain": th, "side": "mainline_confirmed", "theme": th})
                got += 1
    # 2026-09-07 wave 调档(回测 wave-tuned-v2): defense/exit 下按 invest 收窄买腿布设(控亏/只降不空),
    # build/t_only/side invest=1 不裁剪 —— 只影响布腿数量，不绕过浪gate/风控
    try:
        from wave_alloc import read_wave_alloc   # /app/apps/main_line 已在 sys.path(line 17)
        _alloc = read_wave_alloc()
        _inv = float(_alloc.get("invest") or 1.0)
        if _inv < 1.0 and buy_legs:
            _keep = max(int(len(buy_legs) * _inv + 0.5), 1 if _inv > 0.3 else 0)
            if _keep == 0:
                buy_legs = []
            else:
                # 按权重优先保留主链(前序即主线优先)
                buy_legs = buy_legs[:min(_keep, len(buy_legs))]
        print("WAVE_ALLOC op=", _alloc.get("operation"), "invest=", _inv,
              "top3=", (_alloc.get("top3") or [])[:3], file=sys.stderr)
    except Exception as e:
        print("[rotation_switch_arm] wave_alloc err:", str(e)[:80], file=sys.stderr)
    # 2026-09-09 账户权限: 无创业板权限 → 布腿统一剔除(SZ300/SZ301); WOLF_PICK_BOARD_EXCLUDE 可加 kcb/bj
    _ex_b = [x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()]
    def _board_ok(xq):
        _c = str(xq)[2:6] if len(str(xq)) >= 6 else ""
        _p = str(xq)[:2]
        if "cyb" in _ex_b and _p == "SZ" and (_c.startswith("300") or _c.startswith("301")):
            return False
        if "kcb" in _ex_b and _p == "SH" and _c.startswith("688"):
            return False
        if "bj" in _ex_b and (_p == "BJ" or _c.startswith(("4", "8", "920"))):
            return False
        return True
    _nb = len(buy_legs)
    buy_legs = [b for b in buy_legs if _board_ok(b.get("symbol"))]
    if len(buy_legs) != _nb:
        print("BOARD_FILTER removed", _nb - len(buy_legs), "个无权限板块买腿", file=sys.stderr)
    print("DECISION sell_legs", sell_legs, "buy_chains", buy_chains, "buy_legs", buy_legs)
    if dry:
        json.dump({"mode": "SWITCH_ARM_DRY", "date": today, "sell_legs": sell_legs,
                   "buy_legs": buy_legs, "wave": wop, "healthy": healthy, "sucking": sucking},
                  open(os.path.join(DATA, "rotation_switch_arm_dry.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("DRY-RUN 未写库，见 data/rotation_switch_arm_dry.json")
        return 0
    import psycopg2
    conn = psycopg2.connect(DB); conn.autocommit = True; cur = conn.cursor()
    expire_old(cur, today)
    armed = []
    for s in sell_legs:
        rid = arm(conn, cur, s["symbol"], "custom", "sell", SELL_EXPR, today)
        armed.append({"type": "sell_vwap_break", "symbol": s["symbol"], "id": rid})
    for b in buy_legs:
        rid253 = arm(conn, cur, b["symbol"], "custom_m5dump", "buy", BUY_253_EXPR, today)
        rid254 = arm(conn, cur, b["symbol"], "custom_prevlow", "buy", BUY_254_EXPR, today)
        armed.append({"type": "buy_253", "symbol": b["symbol"], "id": rid253})
        armed.append({"type": "buy_254", "symbol": b["symbol"], "id": rid254})
    cur.close(); conn.close()
    print("ARMED", armed)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
