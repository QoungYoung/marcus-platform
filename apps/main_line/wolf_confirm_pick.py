# -*- coding: utf-8 -*-
"""wolf_confirm_pick.py — confirm_pick 狼大化 v2.1 (三层: 等待池+位置闸+ETF兜底+风向标监控)
拍板(2026-09-09): A=确认链成分候选域 / B=老龙头等权标签 / C=主题容量提示 / D=20日成交额<1亿硬切
v2.1(完整三层): 等待池=leader topN(含回调触发价=前一日低×(1+WOLF_DIP_PREVLOW_TOL), 语料值 tol=0) → 当日布腿=池∩低吸位置闸(距前一日低<=X%)
                池∩闸=0(空窗) → 等待(不硬做); ETF兜底腿默认关闭(S1) ; 风向标(池第一)收破前日低 → 硬拦(P1-1)
环境变量: WOLF_PICK_LEGACY=1 回退旧版 / WOLF_PICK_POOL_N / WOLF_PICK_DIST_PCT / WOLF_PICK_ETF_FALLBACK(默认0)
         / WOLF_PICK_WIND_HARD(默认1, 狼大"风向标死了就不做") / WOLF_RS_GATE(默认1) / WOLF_PICK_EMPTY_WAIT(默认1)
用法: python3 apps/main_line/wolf_confirm_pick.py --theme 农业 --as-of 20260902 [--limit 2]
"""
import os, sys, json, time, glob, urllib.request, statistics
sys.path.insert(0, "/app/app"); sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "/app/data")
MIN_AMT20_YI = 1.0      # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
LIMITUP_PCT = 9.7         # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
RANK_WIN = 60             # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)(窗口)


def _relay():
    """加载 core/tushare_relay.py —— 2026-09-13: gzcloud 代理 token 失效，统一改走
    datahubco（基础接口，快）+ promax（聚合接口）中继。"""
    import importlib
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    cur = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.exists(os.path.join(cur, "core", "tushare_relay.py")):
            core_dir = os.path.join(cur, "core")
            if core_dir not in sys.path:
                sys.path.insert(0, core_dir)
            return importlib.import_module("tushare_relay")
        cur = os.path.dirname(cur)
    raise ImportError("core/tushare_relay.py 未找到（仓库根目录 core/ 需随代码部署）")


def gz(api, params, fields="", tries=3):
    """Tushare 查询（返回 items 行列表）。中继内部已含重试/分页/双源降级。"""
    try:
        _fields, items = _relay().relay_items(api, fields=fields, **(params or {}))
        return items or []
    except Exception as e:
        print("TUSHARE_FAIL", api, str(e)[:120], file=sys.stderr)
        return []

def board_allowed(ts_code):
    """账户交易权限板块过滤: 无创业板权限(默认); env WOLF_PICK_BOARD_EXCLUDE 追加 kcb(688)/bj(4/8/920)"""
    ex = [x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()]
    code = ts_code.split(".")[0] if "." in ts_code else ts_code
    mkt = ts_code.split(".")[-1].upper() if "." in ts_code else ""
    if "cyb" in ex and mkt == "SZ" and code[:3] in ("300", "301"):
        return False
    if "kcb" in ex and mkt == "SH" and code.startswith("688"):
        return False
    if "bj" in ex and (mkt == "BJ" or code[:3] in ("920",) or code[:1] in ("4", "8")):
        return False
    return True

def names_map():
    return {str(x[0]): str(x[1]) for x in gz("stock_basic", {"list_status": "L"}, "ts_code,name")}

def confirm_universe(theme="农业"):
    try:
        sc = json.load(open(os.path.join(DATA, "stock_confirm_result.json"), encoding="utf-8"))
    except Exception:
        return {}
    uni = {}
    for cname, v in sc.items():
        if isinstance(v, dict) and v.get("theme") == theme:
            uni[cname] = [str(s.get("code", "")) for s in (v.get("stocks") or []) if s.get("code")]
    return uni

def bad_set():
    bad = set()
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        cur.execute("SELECT ts_code FROM stock_pool WHERE is_st=1 OR name LIKE 'ST%' OR name LIKE '*ST%'")
        bad |= {str(r[0]) for r in cur.fetchall()}
        cur.execute("SELECT symbol FROM risk_flags WHERE (flag_type='is_st' AND value='1') OR flag_type='earnings_bad'")
        bad |= {str(r[0]) for r in cur.fetchall()}
        cur.close(); conn.close()
    except Exception:
        pass
    return bad

def cross_concepts():
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        cur.execute("SELECT ts_code, concept_name FROM stock_concept_map")
        cm = {}
        for ts, cn in cur.fetchall():
            cm.setdefault(str(ts), []).append(str(cn))
        cur.close(); conn.close(); return cm
    except Exception:
        return {}

def fetch_daily(ts, end):
    rows = gz("daily", {"ts_code": ts, "start_date": "20260301", "end_date": end},
              "ts_code,trade_date,close,low,amount,high")
    # rows: (trade_date, close, low, amount, high)  —— high 为 2026-09-15 v3 排序新增（flat_low_days）
    return sorted((str(x[1]), float(x[2]), float(x[3]), float(x[4]),
                   (float(x[5]) if len(x) > 5 and x[5] not in (None, "") else None)) for x in rows)

def theme_etf(theme):
    """主题ETF兜底: etf_theme_map_pi.json primary[0] -> (xq, ts_code, name)"""
    try:
        d = json.load(open(os.path.join(DATA, "etf_theme_map_pi.json"), encoding="utf-8"))
        for t in d.get("themes", []):
            if t.get("theme") == theme:
                p = (t.get("primary") or [])
                if p:
                    ts = str(p[0].get("ts_code", ""))
                    if ts:
                        xq = ("SH" if ts.endswith(".SH") else "SZ") + ts[:6]
                        return {"symbol": xq, "ts_code": ts, "name": p[0].get("name", ""),
                                "reason": p[0].get("reason", "")}
    except Exception:
        pass
    return None

def _dip_tol():
    """254 触发容差（语料值 0.0；历史自设 0.005）。见 docs/wolf-buy-parameter-ledger.md §2-C1。"""
    try:
        return max(float(os.getenv("WOLF_DIP_PREVLOW_TOL", "0.0") or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _banned_from_service():
    """G3 删票黑名单 —— 容器/宿主两种布局都要能找到 `app.services.wolf_ticket_ban`。

    2026-09-14 修复（审计 E3）：原实现只插 `<repo>/backend`；但**容器里 backend/app 挂到 `/app/app`**
    （`docker inspect marcus-worker`：`/opt/marcus-platform/backend/app -> /app/app`），
    生产实测报 `WOLF_PICK BAN_LIST_ERR No module named 'app'` → 删票过滤静默失效。
    同时插 `<repo>`（容器布局：/app + /app/app）与 `<repo>/backend`（宿主仓库布局）。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for _p in (root, os.path.join(root, "backend")):
        if _p and _p not in sys.path:
            sys.path.insert(0, _p)
    from app.services.wolf_ticket_ban import banned_symbols as _bs
    return _bs(["stock"])


def theme_quantile_keep(rows, pct=None, key="leader"):
    """**U9 主题容量约束**（2026-09-11，用户拍板用"相对分位"而非绝对名额）。

    狼大原话:
      2026-09-02 楼729「**小票就太多了 不好判断**」（说半导体细分太散、小票过多）;
      2026-09-02 楼733「农业拉10个点带动的资金量不过100E」（主题体量不够就不值得参与）。
    审计 U9 记录: 此前 capacity_amt20_yi **只输出提示、不拦截**（stderr / 审计 json）。

    口径（用户决策）: 主题内按强度(leader)取**分位前 pct%** 才可买。
      选相对分位而非绝对名额的理由: 绝对名额在大主题上过度压制、在小主题上等于无约束;
      分位口径对主题规模免疫, 且与狼大「**绝不后排**」(2026-01-16「后排反倒不能去 要看好龙头那些」)同向。

    阈值来源: 语料**没给数**（只有"小票太多了"这个定性说法）→ 默认 WOLF_THEME_QUANTILE_PCT=50
      （= 不落后于主题内一半同伴, 与已验证的 rs>0 分层[51% vs 41%]同向）,
      **水平由回测校准**（见 docs/backtest-plan.md）。置 0 关闭。

    返回 (kept_rows, dropped_n, cut)；rows 需含数值型 key（默认 leader）。
    边界口径: 取 ceil(n×pct%) 名（"前 X%"的直读），**与第 k 名并列的一并保留**（不按序位切并列,
    否则又把序位噪声当强度差 —— 与 P1-5b 修 pct_rank 是同一个错）。
    """
    _p = float(pct) if pct is not None else float(os.getenv("WOLF_THEME_QUANTILE_PCT", "50") or 0)
    arr = list(rows or [])
    if _p <= 0 or len(arr) < 2:
        return arr, 0, None
    _p = min(100.0, _p)
    vals = [r.get(key) if r.get(key) is not None else -1e9 for r in arr]
    n = len(vals)
    k = max(1, int(-(-(n * _p) // 100)))          # ceil(n * pct/100)
    kth = sorted(vals, reverse=True)[k - 1]       # 第 k 名的强度
    kept = [r for r, v in zip(arr, vals) if v >= kth]
    cut = kth
    return kept, n - len(kept), cut


def latest_gate_date():
    best = ""
    for f in glob.glob(os.path.join(DATA, "mainline_gate_*.json")):
        d = os.path.basename(f)[14:22]
        if d > best: best = d
    return best or time.strftime("%Y%m%d")

def pick_v2(theme="农业", exclude=None, limit=2, concepts=None, as_of=None, debug=False,
            pool_n=None, dist_pct=None, dist5_pct=None, etf_fallback=None, tier2_gap=None, max_legs=None,
            pick_mode=None, rs_gate=None, rs_min=None, status_out=None):
    """v2.1 完整三层。返回兼容 rotation_switch_arm 的 list; 空窗时含 ETF 兜底腿(etf:True)。
    附加信息(容量/风向标/等待池)进 stderr + json 审计文件。

    2026-09-10(P0-4): status_out 为可选 dict 出参, 回填 {"status","reason"} 供调用方区分
    「位置闸否掉全部=应等待」与「确认域/候选数据缺失=可回落 legacy」。status 取值:
      no_universe / no_scored / ok。调用方不得再用"返回值为空"当作"v2 不可用"。
    """
    exclude = set(exclude or [])
    AS = as_of or latest_gate_date()
    # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)（他的话『就6个票』是持仓数，语义不等价）
    pool_n = int(pool_n if pool_n is not None else os.getenv("WOLF_PICK_POOL_N", "6"))
    # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)（他只有『挂前一天低点』）
    dist_pct = float(dist_pct if dist_pct is not None else os.getenv("WOLF_PICK_DIST_PCT", "5.0"))
    # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)（他未给 r20 阈值；实测该闸净负）
    min_r20 = float(os.getenv("WOLF_PICK_MIN_R20", "0"))
    # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
    tier2_gap = float(tier2_gap if tier2_gap is not None else os.getenv("WOLF_PICK_TIER2_GAP", "8.0"))
    # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
    max_legs = int(max_legs if max_legs is not None else os.getenv("WOLF_PICK_MAX_LEGS", "4"))
    # S1(2026-09-10 清理自造机制): ETF 兜底腿默认关闭。
    # 原逻辑"空窗(位置闸/选择层闸否掉全部)且主题有 ETF → 必买 ETF"与狼大「买不到位置就等」相反,
    # 且其前置条件 not wind_broken 因 wind_broken 恒 False(见 :213 d1>=0)而恒真 → 空窗必触发,
    # 直接抵消了 rotation_switch_arm 的空窗等待语义(P0-4)。狼大用 ETF 是主动选择(2026-08-21 楼435
    # 「选半导体仅仅只是因为他波动大 ETF都有3个点以上的波动」), 语料无"买不到个股就买ETF"这条规则。
    # WOLF_PICK_ETF_FALLBACK=1 可恢复旧行为。
    etf_fb = bool(etf_fallback if etf_fallback is not None else os.getenv("WOLF_PICK_ETF_FALLBACK", "0") == "1")
    # P1-1(2026-09-10): 默认改为 1(启用硬拦) —— 依狼大 2026-01-12「龙头风向标死了就不能做了」。
    # 原默认 0 使"风向标死了就不做"形同未实现(即便判据修对了也不会生效)。置 0 可回退。
    wind_hard = os.getenv("WOLF_PICK_WIND_HARD", "1") == "1"
    # P0-2(2026-09-10): 选择层闸 —— 个股相对主题强度 rs>=rs_min 才入低吸池。
    # 依据: "选择层+兑现风格"回测 rs>0 胜率 51% vs rs<=0 41%; 触发条件本身相对同池基线不提升胜率。
    # 关闭: WOLF_RS_GATE=0 (回到修复前的"只看绝对 r20"行为)。
    rs_gate = bool(rs_gate if rs_gate is not None else os.getenv("WOLF_RS_GATE", "1") == "1")
    rs_min = float(rs_min if rs_min is not None else os.getenv("WOLF_RS_MIN", "0"))
    def _st(status, reason=""):
        if isinstance(status_out, dict):
            status_out["status"] = status; status_out["reason"] = reason; status_out["theme"] = theme
        return []
    uni = confirm_universe(theme)
    if not uni:
        print("WOLF_PICK NO_CONFIRM_UNIVERSE", theme, file=sys.stderr); return _st("no_universe", "confirm_universe 无该主题")
    members = sorted({ts for lst in uni.values() for ts in lst})
    names = names_map(); bad = bad_set(); cm = cross_concepts()
    theme_cons = set(uni.keys())
    # 拥挤黑名单(crowding_blacklist.json)已于 2026-09-13 真删(D15 事件研究证"拦反" + 用户指令)：
    # 原此处会 ts in detail → continue，即无日志、无提示的静默排除。勿重新引入。
    # G3(2026-09-14)：**破线卖出后"删票"**（狼大 2025-02-06 / 2025-04-03）——带 TTL（默认 13 交易日），
    # 且**打日志**（不静默）；只影响买入侧候选，不影响卖出。
    banned = {}
    try:
        banned = _banned_from_service()
    except Exception as _be2:
        print(f"WOLF_PICK BAN_LIST_ERR {str(_be2)[:80]}", file=sys.stderr)
    if banned:
        print(f"WOLF_PICK BAN_LIST n={len(banned)} symbols={sorted(banned)[:8]}", file=sys.stderr)
    kl, mv = {}, {}
    for i in range(0, len(members), 20):
        chunk = members[i:i+20]
        for ts in chunk:
            rows = fetch_daily(ts, AS)
            if rows and rows[-1][0] == AS and len(rows) >= 80:
                kl[ts] = rows
        time.sleep(0.1)
    try:
        for x in gz("daily_basic", {"trade_date": AS}, "ts_code,total_mv"):
            mv[str(x[0])] = float(x[1])
    except Exception:
        pass
    scored = []
    for ts, rows in kl.items():
        nm = names.get(ts, ts)
        xq = ("SH" if ts.endswith(".SH") else "SZ") + ts[:6]
        if not board_allowed(ts) or xq in exclude or ts in bad or "ST" in nm or ts in banned:
            continue
        closes = [r[1] for r in rows]; lows = [r[2] for r in rows]; amts = [r[3] for r in rows]
        amt20 = statistics.mean(amts[-20:]) / 1e5
        if amt20 < MIN_AMT20_YI:
            continue
        r60 = (closes[-1] / closes[-61] - 1) * 100 if len(closes) > 60 else None
        r20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else None
        lim = sum(1 for i in range(max(1, len(closes) - RANK_WIN), len(closes))
                  if closes[i] / closes[i-1] - 1 >= LIMITUP_PCT / 100.0)
        cc = len([c for c in cm.get(ts, []) if c in theme_cons]) if ts in cm else 0
        # 距前一日低(254 可达性): as_of收盘 相对 as_of当日低(前一日低即 as_of 交易日低, 供次日盘中回踩)
        d1 = (closes[-1] / lows[-1] - 1) * 100 if lows[-1] > 0 else 99.0
        # P1-1(2026-09-10 修): 风向标"死了"的判据 —— 收盘 vs **前一交易日**最低。
        # 原 wind_broken 误用 dist_prevlow(=收盘 vs 当日最低, 恒 >=0) → 判据数学上不可能成立, 属死代码。
        # 此处另立字段, 不动 dist_prevlow(它同时是位置闸口径, 语义不同)。
        d1p = (closes[-1] / lows[-2] - 1) * 100 if len(lows) >= 2 and lows[-2] > 0 else 99.0
        d5 = 99.0
        if len(lows) >= 5 and min(lows[-5:]) > 0:
            d5 = (closes[-1] / min(lows[-5:]) - 1) * 100
        # v3 排序次键「低位横盘多时」（2026-04-07「低位横盘多时的就是好 超跌都没有低位走平多时的好」）：
        #   近 20 日中「日振幅<2% ∧ 收盘位于 20 日区间下半」的天数
        highs = [r[4] for r in rows]
        flat_low = 0
        seg_c, seg_h, seg_l = closes[-20:], [h for h in highs[-20:]], lows[-20:]
        if len(seg_c) >= 10 and all(h is not None for h in seg_h):
            hi20, lo20 = max(seg_c), min(seg_c)
            rng = (hi20 - lo20) or 1.0
            for k in range(len(seg_c)):
                try:
                    if seg_c[k] <= 0:
                        continue
                    amp = (float(seg_h[k]) - float(seg_l[k])) / float(seg_c[k])
                    if amp < 0.02 and (seg_c[k] - lo20) <= 0.5 * rng:
                        flat_low += 1
                except (TypeError, ValueError):
                    continue
        scored.append({"ts": ts, "name": nm, "xq": xq, "amt20": amt20, "r60": r60, "r20": r20,
                       "flat_low_days": flat_low, "hist": len(closes),
                       "lim": lim, "cross": cc, "mv": (mv.get(ts, 0) or 0) / 1e4,
                       "dist_prevlow": round(d1, 2), "dist_low5": round(d5, 2),
                       "dist_prevlow_prev": round(d1p, 2),
                       # 254 触发价：语料值 = 前一日低点本身（狼大 2025-03-06「就是挂前一天的低点」）；
                       # `WOLF_DIP_PREVLOW_TOL` 默认 0.0（历史自设值 0.005）。与 t_monitor._stock_dip_prev_low 同源。
                       "trig_price": round(lows[-1] * (1.0 + _dip_tol()), 3)})
    if not scored:
        return _st("no_scored", "成分域经板块/ST/成交额过滤后为空")
    def pct_rank(vals, key):
        """升序 → 百分位 0-1。**并列取平均名次**(P1-5b 配套修复)。

        原实现并列时按列表位置定序 → 并列多的因子(lim 在多数票上恒 0、amt20 相近)会把序位噪声
        注入 leader, 盖过真正区分龙头的 r60, 与狼大"龙头优先"相悖。改为并列同名次。
        """
        arr = [r[key] if r[key] is not None else -1e9 for r in vals]
        n = len(arr)
        order = sorted(range(n), key=lambda i: arr[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and arr[order[j + 1]] == arr[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg_rank / n
            i = j + 1
        pos_of = {r["ts"]: rk[idx] for idx, r in enumerate(vals)}
        return lambda ts: pos_of[ts]
    f_r60 = pct_rank(scored, "r60"); f_amt = pct_rank(scored, "amt20"); f_lim = pct_rank(scored, "lim")
    for r in scored:
        r["leader"] = round((f_r60(r["ts"]) + f_amt(r["ts"]) + f_lim(r["ts"])) / 3.0, 4)
    # P0-2: 主题20日涨幅 = 本主题可比成分 r20 等权均值(PIT: 收盘均截至 AS), 再算个股相对强度 rs
    _r20s = [r["r20"] for r in scored if r["r20"] is not None]
    theme_r20 = statistics.mean(_r20s) if _r20s else 0.0
    for r in scored:
        r["rs"] = round(r["r20"] - theme_r20, 2) if r["r20"] is not None else None
    import pandas as pd, position_class as pc
    for r in scored:
        closes = [x[1] for x in kl[r["ts"]]]
        try:
            f = pc.position_features(pd.Series(closes))
            r["pos"] = pc.classify(f)["position"] if f else "?"
        except Exception:
            r["pos"] = "?"
    cap_yi = sum(r["amt20"] for r in scored)     # 主题体量(全成分口径, 提示用; 见 U9 台账)
    scored.sort(key=lambda r: (-r["leader"], -r["cross"], r["ts"]))
    wait_pool = scored[:pool_n]                                   # ①等待池(老龙头榜, 含HIGH等回调)
    # 风向标: 等待池第一(辨识度最高龙头)状态
    # P1-1(2026-09-10 修, 依狼大原话 2026-01-12):「龙头风向标死了就不能做了…麻溜的跑就行」
    #   → 判据 = 风向标**收盘**跌穿其**前一交易日最低** 0.5% 以上(收盘口径, 与狼大"看收盘"一致)。
    #   原实现用当日低(`dist_prevlow`)对比, 该值恒 >=0 故判据永不成立。
    wind = wait_pool[0] if wait_pool else None
    wind_broken = bool(wind and (wind.get("dist_prevlow_prev") is not None)
                       # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)（风向标“死”的 -0.5% 阈值；他只有『死了就不做』）
                       and wind["dist_prevlow_prev"] <= -0.5)
    # ②当日布腿 = 池(LOW/MID) ∩ 位置闸(距前一日低<=dist_pct)
    # 2026-09-10 狼大'分类龙头/龙2'(WOLF_PICK_MODE=concept, 默认): 全主题统一口径算 leader(可比)
    # → 按子概念分组 → 每组内 leader 前2 入池(rank1=分类龙头, rank2=龙2) → 再走位置闸与 tier 分批。
    # '绝不后排'=只取组内前2, 不做扫描序; WOLF_PICK_MODE=theme 可回退旧'主题一张榜'
    _mode = (pick_mode or os.getenv("WOLF_PICK_MODE", "concept")).strip().lower()
    if _mode == "concept":
        ts2cons = {}
        for _c, _lst in uni.items():
            for _ts in _lst:
                ts2cons.setdefault(_ts, []).append(_c)
        by_con = {}
        for r in scored:
            for _c in ts2cons.get(r["ts"], []):
                by_con.setdefault(_c, []).append(r)
        _pool = {}
        for _c, _arr in by_con.items():
            _srt = sorted(_arr, key=lambda r: (-r["leader"], r["ts"]))
            for _i, r in enumerate(_srt[:2], 1):
                cur = _pool.get(r["ts"])
                if cur is None or _i < cur["rank_in_concept"]:
                    _pool[r["ts"]] = dict(r, concept=_c, rank_in_concept=_i)
        cand_pool = list(_pool.values())
    else:
        cand_pool = [dict(r, concept="", rank_in_concept=1) for r in scored]
    # ── U9 主题容量约束（2026-09-11, 用户决策"相对分位"）──
    # 主题内按 leader 取分位前 WOLF_THEME_QUANTILE_PCT%(默认 50) 才可买。
    # 位置有两个讲究:
    #   ① 放在**组内前2 之后**（作用于 cand_pool 而非 scored）—— 否则会把小规模子概念的"龙2"一起砍掉,
    #      与狼大「买不到龙头买分类龙头/龙2」(2026-01-16)相冲突; 作用于 cand_pool = 只收窄已选出的龙头/龙2 池。
    #   ② 放在 theme_r20(主题均值)算完之后 —— 主题均值必须按**全体成分**算, 否则会连带改掉 rs 闸的基准。
    _qcap = float(os.getenv("WOLF_THEME_QUANTILE_PCT", "50") or 0)
    if _qcap > 0:
        _kept, _qdrop, _qcut = theme_quantile_keep(cand_pool, pct=_qcap, key="leader")
        if _qdrop:
            print(f"[WOLF_THEME_CAP] {theme} 容量约束剔除 {_qdrop} 只"
                  f"(只取前 {_qcap:.0f}%, 门槛 leader={_qcut})", file=sys.stderr)
        cand_pool = _kept
    lowmid = [r for r in cand_pool if r["pos"] in ("LOW", "MID")
             and r["r20"] is not None and r["r20"] >= min_r20
             # P0-2 选择层闸: 只在强于主题的票上低吸(rs>=rs_min); WOLF_RS_GATE=0 关闭
             and (not rs_gate or (r.get("rs") is not None and r["rs"] >= rs_min))]
    _rs_rej = 0
    if rs_gate:
        _rs_rej = sum(1 for r in cand_pool if r["pos"] in ("LOW", "MID")
                      and r["r20"] is not None and r["r20"] >= min_r20
                      and not (r.get("rs") is not None and r["rs"] >= rs_min))
        if _rs_rej:
            print(f"[WOLF_PICK_RS] {theme} 选择层闸剔除 {_rs_rej} 只(rs<{rs_min}), theme_r20={theme_r20:.2f}",
                  file=sys.stderr)
    # B(2026-09-09): 分批——tier1=位置闸(距前日低<=dist_pct)严格档前 limit 只; tier2=接近档(<=tier2_gap)补位至 max_legs;
    # 组内 rank1(分类龙头)优先于 rank2(龙2) = 狼大'买不到龙头买分类龙头/龙2, 绝不后排'
    _key = lambda r: (r.get("rank_in_concept", 1), -r["leader"], -r["cross"], r["ts"])
    t1 = sorted([r for r in lowmid if r["dist_prevlow"] <= dist_pct], key=_key)[:limit]
    t1keys = {r["ts"] for r in t1}
    t2 = sorted([r for r in lowmid if r["ts"] not in t1keys and r["dist_prevlow"] <= tier2_gap],
                key=_key)[:max(0, max_legs - len(t1))]
    _rows = [(r, "tier1") for r in t1] + [(r, "tier2") for r in t2]
    picks = [{"symbol": r["xq"], "ts_code": r["ts"], "position": r["pos"], "theme": theme,
              "leader": r["leader"], "amt20": round(r["amt20"], 2), "r20": round(r["r20"], 1),
              "rs": r.get("rs"), "theme_r20": round(theme_r20, 2),
              "r60": round(r["r60"], 1), "lim": r["lim"], "cross": r["cross"],
              "dist_prevlow": r["dist_prevlow"], "trig_price": r["trig_price"], "tier": tier,
              "concept": r.get("concept", ""), "rank_in_concept": r.get("rank_in_concept", 1),
              "reason": {"r60_rank": round(f_r60(r["ts"]), 3), "amt_rank": round(f_amt(r["ts"]), 3),
                         "lim_rank": round(f_lim(r["ts"]), 3), "cross_concepts": r["cross"]}}
             for r, tier in _rows]
    # ① 排序/域对齐（2026-09-15）：**v3 条件化分层排序**（离线验收见 docs/wolf-pick-rank-v3-eval.md §6）
    #   已验收配置：域 = 候选池 ∩ LOW/MID（**去掉 r20≥0 与 rs≥0 两个实测净负的闸**）；
    #   排序 = LOW 优先，并列次键 = flat_low_days；阶段 = 主题 r5 **跨主题分位** ≥0.5 → 强 → 走"二供"
    #   （跳过组内 r20 最高的一只，2026-04-13「已经涨起来的板块的龙头不做 做他的二供」）；只取 1 只。
    #   离线（含费率）：5 日 +0.213%→+0.083%（现行 −0.982%~−1.112%），配对 Δ +1.120pp（t 2.29），H1/H2 两段都更优。
    #   开关：WOLF_PICK_RANK_V3=1 生效；WOLF_PICK_RANK_V3_SHADOW=1 只记录不生效（默认都关）。
    _v3_on = os.getenv("WOLF_PICK_RANK_V3", "0").strip() == "1"
    _v3_shadow = os.getenv("WOLF_PICK_RANK_V3_SHADOW", "0").strip() == "1"
    if _v3_on or _v3_shadow:
        try:
            import wolf_pick_rank_v3 as _V3
            # 域 = 候选池（组内前2 ∩ 容量分位）∩ LOW/MID —— 与离线验收的 domain=cand_low 同口径
            _domain = [r for r in cand_pool if str(r.get("pos")) in ("LOW", "MID")]
            _r20s = [r["r20"] for r in _domain if r.get("r20") is not None]
            if _r20s:
                _lo, _hi = min(_r20s), max(_r20s)
                for r in _domain:
                    r["rank_in_theme"] = (1.0 if _hi == _lo else (r["r20"] - _lo) / (_hi - _lo)) \
                        if r.get("r20") is not None else None
            _q = _V3.theme_r5_quantile(theme, AS)
            _v3picks = _V3.pick_top(_domain, theme_r5_qtile=_q, n=1, variant="V1",
                                    components="pos", tiebreak="flat",
                                    stage_mode="qtile", diergong=True, qtile_hi=0.5, qtile_lo=0.5)
            _v3out = [{"symbol": r["xq"], "ts_code": r["ts"], "position": r["pos"], "theme": theme,
                       "r20": round(r["r20"], 1) if r["r20"] is not None else None,
                       "rs": r.get("rs"), "dist_prevlow": r["dist_prevlow"],
                       "flat_low_days": r.get("flat_low_days"), "trig_price": r["trig_price"],
                       "tier": "v3", "pick_source": "v3",
                       "v3_score": r.get("v3_score"), "reason": {"v3": r.get("v3_reasons", "")[:120],
                                                                 "theme_r5_qtile": _q}}
                      for r in _v3picks]
            try:
                _sj = os.path.join(DATA, "rank_v3_%s.json" % AS)
                _cur = {}
                if os.path.exists(_sj):
                    try:
                        _cur = json.load(open(_sj, encoding="utf-8")) or {}
                    except Exception:
                        _cur = {}
                _cur.setdefault("date", AS)
                _cur.setdefault("themes", {})
                _cur["themes"][theme] = {"mode": "on" if _v3_on else "shadow",
                                         "theme_r5_qtile": _q,
                                         "v3": [{"symbol": p.get("symbol"), "v3_score": p.get("v3_score"),
                                                 "why": (p.get("reason") or {}).get("v3")} for p in _v3out],
                                         "leader": [{"symbol": p.get("symbol"), "tier": p.get("tier"),
                                                     "leader": p.get("leader")} for p in picks],
                                         "domain_n": len(_domain)}
                json.dump(_cur, open(_sj, "w"), ensure_ascii=False, indent=1)
            except Exception as _sje:
                print("RANK_V3_SHADOW_WRITE_ERR", str(_sje)[:80], file=sys.stderr)
            print("RANK_V3 %s theme=%s domain=%d q=%s v3=%s | leader=%s"
                  % ("ON" if _v3_on else "SHADOW", theme, len(_domain), _q,
                     [p["symbol"] for p in _v3out], [p["symbol"] for p in picks]), file=sys.stderr)
            if _v3_on:
                picks = _v3out
        except Exception as _v3e:
            print("RANK_V3_ERR", theme, str(_v3e)[:150], file=sys.stderr)
    # ③ETF兜底(空窗: 个股0布 且 风向标未破 且 主题有ETF)
    etf = theme_etf(theme)
    etf_used = False
    if etf_fb and not picks and not wind_broken and etf:
        picks.append({"symbol": etf["symbol"], "ts_code": etf["ts_code"], "position": "ETF",
                      "theme": theme, "etf": True, "name": etf["name"], "reason": {"fallback": etf["reason"][:120]}})
        etf_used = True
    if wind_broken and wind_hard:
        picks = []
    # 审计输出
    info = {"as_of": AS, "theme": theme, "capacity_amt20_yi": round(cap_yi, 1),
            "wind_flag": {"symbol": wind["xq"] if wind else None, "name": wind["name"] if wind else None,
                          "broken": wind_broken,
                          "dist_prevlow": wind["dist_prevlow"] if wind else None,
                          "dist_prevlow_prev": wind.get("dist_prevlow_prev") if wind else None,
                          "wind_hard": wind_hard},
            "etf_fallback": etf_used, "etf": etf,
            "wait_pool_top": [{k: r[k] for k in ("name", "ts", "pos", "leader", "r20", "r60",
                                                  "amt20", "dist_prevlow", "dist_prevlow_prev",
                                                  "dist_low5", "trig_price")} for r in wait_pool[:6]]}
    try:
        json.dump(info, open(os.path.join(DATA, f"pick_wolf_{theme}_{AS}_v21.json"), "w"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass
    print(f"[WOLF_PICK_C] {theme} 容量≈{cap_yi:.0f}亿 | 风向标={info['wind_flag']} | "
          f"位置闸命中 {len(t1)} 只 | ETF兜底={etf_used}", file=sys.stderr)
    if debug:
        print("--- 等待池 top", pool_n, "---")
        for r in wait_pool[:pool_n]:
            print(f"  {r['name']} {r['ts'][:6]} pos={r['pos']:4s} leader={r['leader']:.3f} "
                  f"dist_prevlow={r['dist_prevlow']}% trig={r['trig_price']} r20={r['r20']}")
        print("--- 布腿 ---")
        for p in picks: print(json.dumps(p, ensure_ascii=False))
    # P0-4: v2 正常跑完 → status=ok。此时 picks 为空表示"位置闸/选择层闸否掉全部 = 应等待",
    # 调用方据此不得回落 legacy 扫描序(除非数据缺失时 status 为 no_universe/no_scored)。
    if isinstance(status_out, dict):
        status_out.update({"status": "ok", "theme": theme, "rs_gate": rs_gate, "rs_min": rs_min,
                           "theme_r20": round(theme_r20, 2), "rs_rejected": _rs_rej,
                           "picks": len(picks), "t1": len(t1), "etf_fallback": etf_used,
                           # P1-1 卖侧: 把风向标状态透出给 rotation_switch_arm(原先只在审计 json 里)
                           "wind_broken": wind_broken, "wind_hard": wind_hard,
                           "wind_symbol": (wind or {}).get("xq"), "wind_name": (wind or {}).get("name"),
                           "wind_dist_prevlow_prev": (wind or {}).get("dist_prevlow_prev")})
    return picks

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="农业"); ap.add_argument("--as-of", default=None)
    ap.add_argument("--limit", type=int, default=2)
    a = ap.parse_args()
    picks = pick_v2(a.theme, limit=a.limit, as_of=a.as_of, debug=True)
    print("PICKS:", len(picks))

if __name__ == "__main__":
    main()
