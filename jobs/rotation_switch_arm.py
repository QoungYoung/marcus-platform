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

def _relay():
    """加载 core/tushare_relay.py（2026-09-13 起 datahubco 基础接口 + promax 聚合接口，
    替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def _gz(api, params, fields):
    """Tushare 中继单次查询。返回 items 列表（中继内部含重试/分页/双源降级）。"""
    _fields, items = _relay().relay_items(api, fields=fields, **(params or {}))
    return items or []


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


def board_ok(xq_or_ts):
    """账户交易权限板块过滤（**唯一实现**）：无创业板权限（默认），`WOLF_PICK_BOARD_EXCLUDE` 可加 kcb/bj。

    F2（2026-09-15）：此前这段判据在 `main()` 里有一份内联实现、**选股侧完全没有** →
    "选股域（全市场）⊃ 执行域（主板）"，被砍掉的腿不会用次优票补位。现在收敛成一个函数，
    `pick_buy`（选股）与 `main()`（布腿）共用，避免又一次"两条路各一套口径"。
    接受 `SZ300189`（xq）与 `300189.SZ`（ts_code）两种写法。
    """
    s = str(xq_or_ts or "").strip().upper()
    if not s:
        return True
    if "." in s:                                   # ts_code → xq
        _code, _mkt = s.split(".")[0], s.split(".")[-1]
        s = _mkt + _code
    ex = [x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()]
    p, c = s[:2], s[2:8]
    if "cyb" in ex and p == "SZ" and (c.startswith("300") or c.startswith("301")):
        return False
    if "kcb" in ex and p == "SH" and c.startswith("688"):
        return False
    if "bj" in ex and (p == "BJ" or c.startswith(("4", "8", "920"))):
        return False
    return True


def board_prefilter_enabled():
    """F2 开关：`WOLF_PICK_BOARD_PREFILTER=1` → **选股时**就剔除无权限板块（会改选票）。

    默认 0 = 现行为不变（选股不限板块、布腿前统一砍）；此时仍会写影子文件
    `data/board_prefilter_shadow_<date>.json`，记录"若在选择时剔除，会改成选谁"。
    狼大语料不涉及账户权限（这是账户事实、不是策略参数）→ 不进参数总账的"自设/代理"计数。
    """
    return os.getenv("WOLF_PICK_BOARD_PREFILTER", "0").strip() == "1"


def board_prefilter_shadow(chain, cur, alt):
    """F2 影子（只记录，不改决策）：把"剔除无权限板块后会选谁"写入当日文件。"""
    try:
        fn = os.path.join(DATA, "board_prefilter_shadow_%s.json" % _today())
        rec = {}
        if os.path.exists(fn):
            try:
                rec = json.load(open(fn, encoding="utf-8")) or {}
            except Exception:
                rec = {}
        rec.setdefault("date", _today())
        rec.setdefault("chains", {})
        rec["chains"][chain] = {
            "mode": "shadow",
            "current": [x.get("symbol") for x in (cur or [])],
            "with_prefilter": [x.get("symbol") for x in (alt or [])],
            "dropped": [x.get("symbol") for x in (cur or []) if x.get("symbol") not in
                        {y.get("symbol") for y in (alt or [])}],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        rec["updated_at"] = rec["chains"][chain]["ts"]
        os.makedirs(DATA, exist_ok=True)
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        print("BOARD_PREFILTER shadow chain=%s 现=%s 剔后=%s"
              % (chain, rec["chains"][chain]["current"], rec["chains"][chain]["with_prefilter"]),
              file=sys.stderr)
    except Exception as e:
        print("BOARD_PREFILTER_WRITE_ERR", str(e)[:80], file=sys.stderr)


def _select_by_gate(stats, limit, use_board_prefilter=False):
    """按 leader 降序过位置闸取前 `limit`（原 `pick_buy` ③ 段抽出，便于单测）。

    `use_board_prefilter=True` → 位置闸之前先剔除无权限板块（F2）。
    """
    out = []
    for s in stats:
        if len(out) >= limit:
            break
        if use_board_prefilter and not board_ok(s.get("xq")):
            continue
        try:
            import pandas as pd
            import position_class as pc
            f = pc.position_features(pd.Series(s["closes"]))
            pos = pc.classify(f)["position"] if f else None
        except Exception:
            pos = None
        if pos in ("LOW", "MID"):
            leg = {"symbol": s["xq"], "ts_code": s["ts"], "position": pos,
                   "chain": s.get("chain"), "leader": s["leader"],
                   "r60": round(s["r60"], 1) if s["r60"] is not None else None,
                   "amt20": round(s["amt20"], 2)}
            # ⑪ 均线挂单（2026-09-15 round 12）：狼大「跌到 13/34/60/144 线上挂单买」——
            #    closes 已在手，零额外取数；这里只算"收盘下方最近的一条线"，供影子记录（不改决策）。
            try:
                import importlib as _il2
                _p2 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line")
                if _p2 not in sys.path:
                    sys.path.insert(0, _p2)
                _MLE = _il2.import_module("wolf_ma_line_entry")
                _line = _MLE.nearest_line_below(s.get("closes") or [])
                if _line:
                    leg["ma_line_k"] = _line["k"]
                    leg["ma_line_v"] = _line["v"]
                    leg["ma_line_dist_pct"] = _line["dist_pct"]
            except Exception:
                pass
            out.append(leg)
    return out


def _banned_from_service():
    """G3 删票黑名单（`app.services.wolf_ticket_ban`）—— 容器/宿主两种布局都要能找到。

    2026-09-14 修复（审计 E3）：原实现只把 `<repo>/backend` 插进 sys.path，但**容器里 backend/app
    是挂到 `/app/app`**（`docker inspect marcus-worker`：/opt/marcus-platform/backend/app -> /app/app），
    所以 `from app.services...` 在生产实测报 `No module named 'app'` → 删票过滤**静默失效**。
    这里同时插入 `<repo>` 与 `<repo>/backend`（宿主布局），并把失败留成 fail-open（返回空黑名单）。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _p in (root, os.path.join(root, "backend")):
        if _p and _p not in sys.path:
            sys.path.insert(0, _p)
    from app.services.wolf_ticket_ban import banned_symbols as _bs
    return _bs(["stock"])


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
    # 拥挤黑名单已于 2026-09-13 真删(D15 证拦反 + 用户指令)：不再读、不再静默排除候选。勿重新引入。
    # G3(2026-09-14)：**破线卖出后"删票"**（狼大 2025-02-06「卖出然后删票」/2025-04-03「破之前新低的直接删票」）
    # —— 带 TTL（默认 13 交易日）；与已删的拥挤黑名单不同：① 触发条件是**他自己的破线**，② **有日志**不静默。
    banned, _skipped = {}, []
    try:
        banned = _banned_from_service()
    except Exception as _be:
        print(f"[rotation] 删票黑名单读取失败: {str(_be)[:80]}")
    cands = []
    for ts, names in cm.items():
        if any(norm(k) in norm(n) for n in names for k in kws):
            xq = "SH" + ts[:6] if ts.endswith(".SH") else ("SZ" + ts[:6] if ts.endswith(".SZ") else ts)
            if ts in banned:
                _skipped.append(ts)
                continue
            if xq not in exclude and ts not in bad:
                cands.append((ts, xq))
    if _skipped:
        print(f"[rotation] G3 删票过滤 {len(_skipped)} 只: {_skipped[:8]}")
    if not cands:
        return []
    # ① 市值预筛(单次全市场调用): 龙头优先于字典序
    # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
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
    # U9 主题容量约束（2026-09-11, 用户决策"相对分位"）: 与路径B(pick_v2) **同一函数、同一 env**,
    # 避免又一次"两条路各一套口径"（本项目反复踩的坑）。理由与阈值出处见 wolf_confirm_pick.theme_quantile_keep。
    try:
        from wolf_confirm_pick import theme_quantile_keep as _tqk
        _qcap = float(os.getenv("WOLF_THEME_QUANTILE_PCT", "50") or 0)
        if _qcap > 0:
            _kept, _qdrop, _qcut = _tqk(stats, pct=_qcap, key="leader")
            if _qdrop:
                print(f"WOLF_THEME_CAP {chain} 容量约束剔除 {_qdrop} 只"
                      f"(只取前 {_qcap:.0f}%, 门槛 leader={_qcut})", file=sys.stderr)
            stats = _kept
    except Exception as _qe:
        print("[pick_buy] 容量约束跳过:", str(_qe)[:80], file=sys.stderr)
    stats.sort(key=lambda s: (-s["leader"], s["ts"]))
    for s in stats:
        s.setdefault("chain", chain)
    # ③ 按 leader 降序过位置闸（抽成 `_select_by_gate`，便于单测）
    # F2（2026-09-15）：选股域必须 ⊆ 执行域。默认**不改行为**（`WOLF_PICK_BOARD_PREFILTER=0`），
    #   但把"若在选择时就剔除无权限板块、会改成选谁"记入 data/board_prefilter_shadow_<date>.json。
    _pf_on = board_prefilter_enabled()
    out = _select_by_gate(stats, limit, use_board_prefilter=_pf_on)
    # ⑪ 均线挂单影子（2026-09-15 round 12）：只记录"如果挂在最近均线上会怎样"，**不改任何决策**。
    #    离线验收（总账 §18）：成交率 25.9% vs 254 的 52%，每条已挂腿期望略优于 254 但不如 253
    #    → 属"同类替换"候选，先影子攒数据再拍板是否替换 254。
    try:
        import importlib as _il3
        _p3 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line")
        if _p3 not in sys.path:
            sys.path.insert(0, _p3)
        _MLE2 = _il3.import_module("wolf_ma_line_entry")
        if _MLE2.shadow_enabled():
            _rows = [{"symbol": l["symbol"], "ma_line_k": l.get("ma_line_k"),
                      "ma_line_v": l.get("ma_line_v"), "ma_line_dist_pct": l.get("ma_line_dist_pct")}
                     for l in out]
            _MLE2.shadow_record(chain, _rows)
    except Exception as _e_ml:
        print("MA_LINE_SHADOW_ERR", str(_e_ml)[:80], file=sys.stderr)
    if not _pf_on:
        try:
            _alt = _select_by_gate(stats, limit, use_board_prefilter=True)
            if [x["symbol"] for x in _alt] != [x["symbol"] for x in out]:
                board_prefilter_shadow(chain, out, _alt)
        except Exception as _pfe:
            print("BOARD_PREFILTER_SHADOW_ERR", str(_pfe)[:80], file=sys.stderr)
    return out


def pick_health(theme, source, status="ok", err="", n=0):
    """③ 可见性（2026-09-14）：把"选择层这次到底走的哪条路"落成文件 + 一行 stderr。

    起因（审计 B11）：pick_v2 抛错时旧代码只打一行 `WOLF_PICK_V2_ERR` 就**静默回落 legacy 扫描序**
    （2026-09-10 实测两次：`HTTP Error 307` / `cannot locate _key`）→「选择层已上线」是假象，
    连续 6 个交易日没人发现。
      ① 每次确认产出 `data/pick_health_<date>.json`（按 theme 覆盖，保留 last_error）并打印 `PICK_HEALTH` 行；
      ② 开关 `WOLF_PICK_FAIL_MODE`：**默认 legacy**（=旧行为，零变化）；置 `wait` 则报错时**不回落、不布腿**（等数据恢复）。
    不改任何选股判据；只做"看得见"。
    """
    rec = {"date": _today(), "theme": theme, "source": source, "status": status,
           "err": (err or "")[:300], "n": int(n or 0), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        fn = os.path.join(DATA, "pick_health_%s.json" % rec["date"])
        cur = {}
        if os.path.exists(fn):
            try:
                cur = json.load(open(fn, encoding="utf-8")) or {}
            except Exception:
                cur = {}
        cur.setdefault("date", rec["date"])
        cur.setdefault("themes", {})
        cur["themes"][theme] = rec
        if source in ("err", "err_wait"):
            cur["last_error"] = rec
        cur["updated_at"] = rec["ts"]
        os.makedirs(DATA, exist_ok=True)
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
    except Exception as _e:
        print("PICK_HEALTH_WRITE_ERR", str(_e)[:80], file=sys.stderr)
    print("PICK_HEALTH theme=%s source=%s status=%s n=%d err=%s"
          % (theme, source, status, rec["n"], rec["err"][:80]), file=sys.stderr)
    return rec


def confirm_pick(theme, exclude, limit=2, concepts=None):
    """曾确认主题低吸选股: THEME_CONCEPTS 成分 -> 过滤 bad/blacklist/held -> position LOW/MID, 至多 limit 只

    返回的每条 pick 带 `pick_source`（v2 / legacy / legacy_after_err / legacy_datagap / legacy_empty），
    供 `PICK_PATH_SUMMARY` 与审计文件回答"这次到底走的哪条路"（③ 可见性，2026-09-14）。
    """
    # 2026-09-09 狼大化 v2 (A=确认链/B=等权leader/C=容量提示/D=20日成交额>=1亿硬切); WOLF_PICK_LEGACY=1 回退旧版
    if os.getenv("WOLF_PICK_LEGACY", "0") != "1":
        _st = {}
        try:
            from wolf_confirm_pick import pick_v2 as _v2
            _p = _v2(theme, exclude=list(exclude or []), limit=limit, concepts=concepts, status_out=_st)
        except Exception as _e:
            print("WOLF_PICK_V2_ERR", theme, str(_e)[:200], file=sys.stderr)
            _p = None
            pick_health(theme, "err", "error", str(_e)[:200])
        if _p is not None:
            if _p:
                for _x in _p:
                    _x.setdefault("pick_source", "v2")
                pick_health(theme, "v2", str(_st.get("status") or "ok"), "", len(_p))
                return _p
            # 2026-09-10(P0-4): v2 返回空列表有两种截然不同的语义, 旧代码用 `if _p: return _p`
            # 把它们混为一谈并静默回落 legacy DB 扫描序 → 位置闸否掉全部时反而去买后排。
            #   ① no_universe / no_scored = 确认域/候选数据缺失 → 允许回落 legacy(否则数据问题=全天不布腿)
            #   ② ok(位置闸/选择层闸否掉全部) = 狼大"买不到位置就等" → 必须等待, 不得回落扫描序
            if _st.get("status") in ("no_universe", "no_scored"):
                print("WOLF_PICK_V2_DATAGAP_FALLBACK", theme, _st.get("status"), "-> legacy", file=sys.stderr)
                pick_health(theme, "legacy_datagap", str(_st.get("status")), "确认域/候选缺失 → legacy", 0)
            elif os.getenv("WOLF_PICK_EMPTY_WAIT", "1") == "1":
                print("WOLF_PICK_V2_EMPTY_WAIT", theme, "rs_rej=%s" % _st.get("rs_rejected"),
                      "t1=%s" % _st.get("t1"), "-> 空窗等待(不回落 legacy)", file=sys.stderr)
                pick_health(theme, "wait", str(_st.get("status") or "ok"), "空窗等待", 0)
                return []
            else:
                print("WOLF_PICK_V2_EMPTY_FALLBACK", theme, "(WOLF_PICK_EMPTY_WAIT=0) -> legacy", file=sys.stderr)
                pick_health(theme, "legacy_empty", "ok", "WOLF_PICK_EMPTY_WAIT=0", 0)
        elif os.getenv("WOLF_PICK_FAIL_MODE", "legacy").strip().lower() == "wait":
            # ③ 失败语义可选：报错时不静默买 legacy 扫描序的票，改为"今天不布这只主题的腿"
            print("WOLF_PICK_V2_FAIL_WAIT", theme, "→ 不回落 legacy(WOLF_PICK_FAIL_MODE=wait)", file=sys.stderr)
            pick_health(theme, "err_wait", "error", "WOLF_PICK_FAIL_MODE=wait → 不布腿")
            return []
    _picks_legacy = _legacy_confirm_pick(theme, exclude, limit, concepts)
    for _x in _picks_legacy:
        _x.setdefault("pick_source", "legacy")
    pick_health(theme, "legacy", "ok", "", len(_picks_legacy))
    return _picks_legacy


def _legacy_confirm_pick(theme, exclude, limit=2, concepts=None):
    """旧版（DB 扫描序）低吸选股 —— pick_v2 之前的线上实现，现作为显式回退分支保留。

    顺序 = `SELECT DISTINCT ts_code ... WHERE concept_name IN (...)`（无 ORDER BY，实测≈表内 rowid 序），
    取扫描到的前 `limit` 只 position ∈ LOW/MID（最多扫 60 只）。
    2026-09-14 审计：它的实测超额 ≈ 0（5 日 +0.014%，块状 t −0.14；10/20 日 +0.12%/+0.50%），
    **不劣于** pick_v2 的 tier1（−0.98%/−2.37%/−1.20%）→ 保留为显式回退基线，不做静默替换。
    """
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
    # 拥挤黑名单已于 2026-09-13 真删(D15 证拦反 + 用户指令)：不再读、不再静默排除。
    out = []
    scanned = 0
    for ts in members:
        if len(out) >= limit or scanned >= 60: break
        if ts in bad: continue
        xq = ("SH" if ts.endswith('.SH') else 'SZ') + ts[:6]
        if xq in exclude: continue
        scanned += 1
        try:
            import pandas as pd
            import position_class as pc
            # P1-5a 修复: 原硬编码 "20260908" 冻结日线窗口
            # 2026-09-13: 数据源改走 datahubco/promax 中继（gzcloud 代理 token 已失效）
            rows = _gz("daily", {"ts_code": ts, "start_date": "20250101", "end_date": _today()},
                       "ts_code,trade_date,close")
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

def mainline_today(today=None):
    """方向层主线（2026-09-13 起取代 gate）：读 wolf_mainline_select.json 的 主线 ∪ 池；无则空。

    为什么换：gate 单独命中他方向 ≈ 随机（top1 28%/precision 12%），且作约束净负
    （见 docs/wolf-structural-pool.md §十一）；方向层池判定对齐 recall 75%/top1 54%/top3 85%。
    """
    p = os.path.join(DATA, 'wolf_mainline_select.json')
    try:
        st = json.load(open(p, encoding='utf-8'))
    except Exception:
        return set()
    if not st.get('mainline'):
        return set()
    return {t for t in ([st.get('mainline')] + list(st.get('pool') or [])) if t}


def gate_confirmed_today(today):
    """（已弃用，2026-09-13）最近(<=today) mainline_gate json 的 confirmed_candidate 主题集; 无则空。
    保留仅为回退对比；生产路径请用 mainline_today()。"""
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
BUY_253_EXPR = {"and": [{"op": ">=", "field": "index.m5_dump", "value": 0.4},   # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)(253 跌幅阈值)
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

MA_LINE_EXPR_VOL_MAX = 0.9      # 温和缩量（与 254 同口径）
MA_LINE_MIN_CLOSES = 144         # 至少要够最长的那条线


def ma_line_expr(price, vol_max=MA_LINE_EXPR_VOL_MAX):
    """**均线挂单**的条件表达式（他的话：跌到 13/34/60/144 线上挂单买）。

    与 254 的唯一区别 = 触发价：254 用 `quote.dip_prev_low`（触前一日最低 ×(1+tol)），
    这里用**收盘下方最近一条均线的价格**（字面量写进表达式，t_monitor 无需改动）。
    其余条件（温和缩量、均价/现价有效）与 254 保持一致。
    """
    px = round(float(price), 3)
    return {"and": [
        {"op": "<=", "field": "quote.current", "value": px},
        {"op": ">", "field": "quote.current", "value": 0},
        {"op": "<=", "field": "vol_ratio", "value": float(vol_max)},
        {"op": ">", "field": "vol_ratio", "value": 0},
        {"op": ">", "field": "quote.average", "value": 0},
    ]}


def ma_line_price(symbol, as_of):
    """→ {"k":13/34/60/144, "v":均线价, "dist_pct":距线%} 或 None（收盘下方最近的那条线）。"""
    try:
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line")
        if _p not in sys.path:
            sys.path.insert(0, _p)
        import datetime as _dt
        import wolf_confirm_pick as _WCP
        import wolf_ma_line_entry as _MLE
        s = str(symbol)
        ts = (s[2:] + "." + s[:2]) if s[:2] in ("SH", "SZ") else s
        # ⚠️ 必须拉足够长的历史：`fetch_daily` 只从 20260301 起（≈136 根）→ 算不出 144 线，
        #    且高位股的 MA13/34/60 可能都在收盘价上方 → nearest_line_below 返回 None（2026-09-15 实测踩到）。
        closes = []
        try:
            start = (_dt.date.today() - _dt.timedelta(days=540)).strftime("%Y%m%d")
            rows = _WCP.gz("daily", {"ts_code": ts, "start_date": start, "end_date": as_of},
                           "ts_code,trade_date,close,low,amount,high") or []
            rows = sorted(rows, key=lambda x: str(x[1]))
            closes = [float(x[2]) for x in rows if len(x) > 2 and x[2] not in (None, "")]
        except Exception as _e1:
            print("MA_LINE_LONG_FETCH_ERR", symbol, str(_e1)[:60], file=sys.stderr)
        if len(closes) < MA_LINE_MIN_CLOSES:
            rows2 = _WCP.fetch_daily(ts, as_of) or []
            closes = [r[1] for r in rows2 if r and r[1]]
        if len(closes) < MA_LINE_MIN_CLOSES:
            return None
        return _MLE.nearest_line_below(closes)
    except Exception as _e:
        print("MA_LINE_PRICE_ERR", symbol, str(_e)[:70], file=sys.stderr)
        return None


def ma_line_enabled():
    """`WOLF_MA_LINE_ENTRY`（默认 0；2026-09-15 起生产置 1）→ 真挂腿时用均线价。"""
    return os.getenv("WOLF_MA_LINE_ENTRY", "0").strip().lower() in ("1", "true", "yes")


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
    # 2026-09-13：主线低吸放行集合改用**方向层主线（池判定）**；缺失时回退旧 gate（过渡保护）
    confirmed_today_set = mainline_today(today)
    _src_tag = "MAINLINE_POOL"
    if not confirmed_today_set:
        confirmed_today_set = gate_confirmed_today(today)
        _src_tag = "GATE_FALLBACK"
    if confirmed_today_set:
        print(_src_tag + "_TODAY", sorted(confirmed_today_set), file=sys.stderr)
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
    _gate_blocked = []
    if pool:
        try:
            from wolf_context import theme_buyable
            _ok_pool = []
            _blocked_themes = []
            for th in pool:
                _ok, _why = theme_buyable(th)
                if _ok:
                    _ok_pool.append(th)
                else:
                    print("SKIP_THEME_NOT_BUYABLE", th, _why, file=sys.stderr)
                    _blocked_themes.append({"theme": th, "why": str(_why)[:200]})
                    pick_health(th, "gate_blocked", "blocked", str(_why)[:200])
            pool = _ok_pool
            _gate_blocked.extend(_blocked_themes)
        except Exception as _e:
            print("THEME_BUYABLE_ERR(放行)", str(_e)[:120], file=sys.stderr)
    if qualify and pool:
        # B(2026-09-09): 等待池分批——tier1严格前2 + tier2接近档补位至 ROT_POOL_LEGS(默认4);
        # 成交节奏由资金闸兜底(probe<=5%预算尽自动停), 狼大'埋伏一批等位置'
        pool_legs = int(os.getenv("ROT_POOL_LEGS", "4"))   # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)（他的话『分了4个方向』是调仓方向数）
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
    # ⑩ 「144 线」大级别资格（2026-09-15 round 10）：**默认只记录（影子）**，`WOLF_MA144_GATE=1` 才真拦买腿。
    #   狼大 2016-07-07「当K线盘整144线稍微走平，那就表明可以做一个波段趋势了」/ 2016-08-15「144都已经走平向上了…找买点进大波段」
    #   / 2026-03-20「我的牛熊分界线是 日K144线…那是我的最后底线」。
    #   离线验收（jobs/eval_ma144_regime.py，总账 §17）：**证据不足以开闸** —— 斜率口径被拦日全在一个连续时段（伪显著）、
    #   收盘口径跨 4 段方向一致但不显著且未与现有大盘门做增量对照 → 先影子攒跨时段样本，再决定是否做成门。
    #   影子额外记 `wave_op` / `gate_blocked` / `buy_legs`，正是为了下一轮做**增量对照**（144 拦掉的日子有多少是现有门已在拦的）。
    try:
        import importlib as _il
        _M144 = None
        for _p in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line"),):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        try:
            _M144 = _il.import_module("wolf_ma144_regime")
        except Exception as _me:
            print("MA144_IMPORT_ERR", str(_me)[:80], file=sys.stderr)
        if _M144 is not None:
            _st144 = _M144.state()
            if _M144.shadow_enabled():
                _M144.shadow_record({"wave_op": wop, "gate_blocked": [t.get("theme") for t in _gate_blocked],
                                     "pool": pool, "buy_legs": [b.get("symbol") for b in buy_legs],
                                     "raw_legs_n": len(buy_legs)})
            print("MA144 index=%s state=%s gate=%s allow=%s"
                  % (_M144.index_code(), _st144, _M144.gate_enabled(), _M144.allow(_st144)), file=sys.stderr)
            if _M144.gate_enabled() and buy_legs and not _M144.allow(_st144):
                print("MA144_GATE 拦下 %d 条买腿（144 走平/向上资格不满足）: %s"
                      % (len(buy_legs), [b.get("symbol") for b in buy_legs]), file=sys.stderr)
                buy_legs = []
    except Exception as _e144:
        print("[rotation_switch_arm] ma144 err:", str(_e144)[:80], file=sys.stderr)
    # ⑫ 「白线在上 ∧ 缩量 → 没有买点」（2026-09-15 round 17）：**默认只记录（影子）**，WOLF_LINE_REGIME_GATE=1 才拦买腿。
    #   狼大 2025-07-15「缩量 白线在上千万别没事加仓…60%仓位内」/ 2026-04-09「今天白线在上 肯定没有买点…减回50%-55%仓位」。
    #   离线验收（jobs/eval_line_regime.py，总账 §26）：253 腿在他说"没买点"的日子 −1.556%（n=608，周块状 t −1.35）
    #   vs 放行 +0.157%；周内配对 16/24 周为负（周级 t −1.19，不显著）→ 先影子攒样本。
    try:
        import importlib as _il5
        _p5 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line")
        if _p5 not in sys.path:
            sys.path.insert(0, _p5)
        _MLR = _il5.import_module("wolf_line_regime")
        _st_lr = _MLR.state()
        if _MLR.shadow_enabled():
            _MLR.shadow_record({"wave_op": wop, "gate_blocked": [t.get("theme") for t in _gate_blocked],
                                "pool": pool, "buy_legs": [b.get("symbol") for b in buy_legs],
                                "raw_legs_n": len(buy_legs)})
        print("LINE_REGIME side=%s shrink=%s no_buy=%s gate=%s allow=%s"
              % (_st_lr.get("side"), _st_lr.get("shrink"), _st_lr.get("no_buy"),
                 _MLR.gate_enabled(), _MLR.allow(_st_lr)), file=sys.stderr)
        if _MLR.gate_enabled() and buy_legs and not _MLR.allow(_st_lr):
            print("LINE_REGIME_GATE 拦下 %d 条买腿（白线在上 ∧ 缩量 → 他说的没有买点）: %s"
                  % (len(buy_legs), [b.get("symbol") for b in buy_legs]), file=sys.stderr)
            buy_legs = []
    except Exception as _e_lr:
        print("[rotation_switch_arm] line_regime err:", str(_e_lr)[:80], file=sys.stderr)
    # ⑬ 大盘级"缩量/地量"（2026-09-15 round 22）：他的量能口径在**大盘级**（1WE/1.5WE/2WE 成交额），
    #   而我们的 254 用的是"个股 5 分钟量比"（总账 §31 发现 #11：「层级错」）。
    #   **本项只记录（影子）**：把当日两市成交额与他的档位落盘，供后续评估"地量才买"该怎么落。
    try:
        import importlib as _il6
        _p6 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apps", "main_line")
        if _p6 not in sys.path:
            sys.path.insert(0, _p6)
        _MMV = _il6.import_module("wolf_market_volume")
        if _MMV.shadow_enabled():
            _MMV.shadow_record({"wave_op": wop, "buy_legs": [b.get("symbol") for b in buy_legs]})
            _st_mv = _MMV.state()
            print("MARKET_VOL we=%s bucket=%s shrink=%s" % (_st_mv.get("we"), _st_mv.get("bucket"),
                                                            _st_mv.get("shrink_vs_prev")), file=sys.stderr)
    except Exception as _e_mv:
        print("[rotation_switch_arm] market_vol err:", str(_e_mv)[:80], file=sys.stderr)
    # 2026-09-09 账户权限: 无创业板权限 → 布腿统一剔除(SZ300/SZ301); WOLF_PICK_BOARD_EXCLUDE 可加 kcb/bj
    _ex_b = [x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()]
    _board_ok = board_ok      # F2(2026-09-15)：与选股侧共用**唯一实现**（原内联版已删，避免两套口径分叉）
    _nb = len(buy_legs)
    buy_legs = [b for b in buy_legs if _board_ok(b.get("symbol"))]
    if len(buy_legs) != _nb:
        print("BOARD_FILTER removed", _nb - len(buy_legs), "个无权限板块买腿", file=sys.stderr)
    # ③ 可见性（2026-09-14）：把"这次买腿分别来自哪条路"显式打出来 + 落审计文件。
    # 起因：pick_v2 报错会静默回落 legacy 扫描序，路径 A/B 的腿在日志里无法区分 → 上线 6 天无人发现。
    _src_cnt = {}
    for _b in buy_legs:
        _src = _b.get("pick_source") or ("pathA" if _b.get("side") in ("mainline", "defensive_resource") else "?")
        _src_cnt[_src] = _src_cnt.get(_src, 0) + 1
    print("PICK_PATH_SUMMARY legs_by_source=%s pool=%s gate_blocked=%s pool_legs=%s"
          % (_src_cnt, pool, [b.get("theme") for b in _gate_blocked], os.getenv("ROT_POOL_LEGS", "4")),
          file=sys.stderr)
    try:
        _health_fn = os.path.join(DATA, "pick_path_%s.json" % today)
        _hp = {}
        if os.path.exists(_health_fn):
            try:
                _hp = json.load(open(_health_fn, encoding="utf-8")) or {}
            except Exception:
                _hp = {}
        _hp.update({"date": today, "dry": bool(dry), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "legs_by_source": _src_cnt, "legs": buy_legs,
                    "confirmed_pool": pool, "gate_blocked": _gate_blocked,
                    "theme_buyable_checked": bool(pool or _gate_blocked)})
        with open(_health_fn, "w", encoding="utf-8") as _f:
            json.dump(_hp, _f, ensure_ascii=False, indent=1)
    except Exception as _he:
        print("PICK_PATH_WRITE_ERR", str(_he)[:80], file=sys.stderr)
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
    _ma_on = ma_line_enabled()
    ma_rows = []
    for b in buy_legs:
        rid253 = arm(conn, cur, b["symbol"], "custom_m5dump", "buy", BUY_253_EXPR, today)
        # ⑯ 均线挂单真挂腿（2026-09-15，用户指示上线）：254 的挂单价 = 收盘下方最近均线（13/34/60/144）；
        #    取不到均线 → 回退 254 原条件（触前低），绝不因此丢腿。
        _ln = ma_line_price(b["symbol"], today) if _ma_on else None
        if _ln and _ln.get("v"):
            rid254 = arm(conn, cur, b["symbol"], "custom_prevlow", "buy",
                         ma_line_expr(_ln["v"]), today)
            ma_rows.append({"symbol": b["symbol"], "k": _ln.get("k"),
                            "price": round(float(_ln["v"]), 3),
                            "dist_pct": _ln.get("dist_pct"), "pricing": "ma_line"})
        else:
            rid254 = arm(conn, cur, b["symbol"], "custom_prevlow", "buy", BUY_254_EXPR, today)
            ma_rows.append({"symbol": b["symbol"], "pricing": "prevlow_fallback"})
        armed.append({"type": "buy_253", "symbol": b["symbol"], "id": rid253})
        armed.append({"type": "buy_254", "symbol": b["symbol"], "id": rid254})
    try:
        json.dump({"date": today, "mode": "enforce" if _ma_on else "shadow",
                   "legs": ma_rows},
                  open(os.path.join(DATA, "ma_line_arm_%s.json" % today), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception as _mle:
        print("MA_LINE_ARM_WRITE_ERR", str(_mle)[:70], file=sys.stderr)
    print("MA_LINE_ARMED", [r for r in ma_rows if r.get("pricing") == "ma_line"])
    cur.close(); conn.close()
    print("ARMED", armed)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
