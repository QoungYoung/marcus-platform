# -*- coding: utf-8 -*-
"""wolf_confirm_pick.py — confirm_pick 狼大化 v2.1 (三层: 等待池+位置闸+ETF兜底+风向标监控)
拍板(2026-09-09): A=确认链成分候选域 / B=老龙头等权标签 / C=主题容量提示 / D=20日成交额<1亿硬切
v2.1(完整三层): 等待池=leader topN(含回调触发价=前一日低x1.005) → 当日布腿=池∩低吸位置闸(距前一日低<=X%)
                池∩闸=0(空窗)且主题有ETF → ETF兜底腿(查 etf_theme_map_pi primary[0]); 风向标(池第一)破位可硬拦
环境变量: WOLF_PICK_LEGACY=1 回退旧版 / WOLF_PICK_POOL_N / WOLF_PICK_DIST_PCT / WOLF_PICK_ETF_FALLBACK / WOLF_PICK_WIND_HARD
用法: python3 apps/main_line/wolf_confirm_pick.py --theme 农业 --as-of 20260902 [--limit 2]
"""
import os, sys, json, time, gzip, glob, urllib.request, statistics
sys.path.insert(0, "/app/app"); sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "/app/data")
GZ = os.getenv("TUSHARE_API_URL") or "https://ts.gyzcloud.top/api"
TOK = os.getenv("TUSHARE_TOKEN", "a5c495cbe5e14729ad756381efe1fd72")
MIN_AMT20_YI = 1.0
LIMITUP_PCT = 9.7
RANK_WIN = 60

def gz(api, params, fields, tries=3):
    """gzcloud 查询. 2026-09-10 修复: 网关偶发 307 Temporary Redirect 会让 urllib 直接抛错
    (v2.1 pick 因此回退 legacy) —— 改用 requests(自动跟随307并重发POST) + 重试, urllib 仅兜底。"""
    body = {"api_name": api, "token": TOK, "params": params, "fields": fields}
    last = None
    for _ in range(max(1, tries)):
        try:
            import requests
            r = requests.post(GZ, json=body, timeout=60, headers={"Accept-Encoding": "identity"})
            if r.status_code == 200:
                d = r.json()
                return (d.get("data") or {}).get("items") or []
            last = "http %s" % r.status_code
        except Exception as e:
            last = str(e)[:100]
        time.sleep(0.8)
    try:
        req = urllib.request.Request(GZ, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
        raw = urllib.request.urlopen(req, timeout=60).read()
        if raw[:2] == bytes([0x1f, 0x8b]): raw = gzip.decompress(raw)
        d = json.loads(raw.decode()); return (d.get("data") or {}).get("items") or []
    except Exception as e:
        print("GZ_FAIL", api, last, str(e)[:80], file=sys.stderr)
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
              "ts_code,trade_date,close,low,amount")
    return sorted((str(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in rows)

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
    pool_n = int(pool_n if pool_n is not None else os.getenv("WOLF_PICK_POOL_N", "6"))
    dist_pct = float(dist_pct if dist_pct is not None else os.getenv("WOLF_PICK_DIST_PCT", "5.0"))
    min_r20 = float(os.getenv("WOLF_PICK_MIN_R20", "0"))
    tier2_gap = float(tier2_gap if tier2_gap is not None else os.getenv("WOLF_PICK_TIER2_GAP", "8.0"))
    max_legs = int(max_legs if max_legs is not None else os.getenv("WOLF_PICK_MAX_LEGS", "4"))
    # S1(2026-09-10 清理自造机制): ETF 兜底腿默认关闭。
    # 原逻辑"空窗(位置闸/选择层闸否掉全部)且主题有 ETF → 必买 ETF"与狼大「买不到位置就等」相反,
    # 且其前置条件 not wind_broken 因 wind_broken 恒 False(见 :213 d1>=0)而恒真 → 空窗必触发,
    # 直接抵消了 rotation_switch_arm 的空窗等待语义(P0-4)。狼大用 ETF 是主动选择(2026-08-21 楼435
    # 「选半导体仅仅只是因为他波动大 ETF都有3个点以上的波动」), 语料无"买不到个股就买ETF"这条规则。
    # WOLF_PICK_ETF_FALLBACK=1 可恢复旧行为。
    etf_fb = bool(etf_fallback if etf_fallback is not None else os.getenv("WOLF_PICK_ETF_FALLBACK", "0") == "1")
    wind_hard = os.getenv("WOLF_PICK_WIND_HARD", "0") == "1"
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
    try:
        bl = json.load(open(os.path.join(DATA, "crowding_blacklist.json"), encoding="utf-8")) or {}
    except Exception:
        bl = {}
    detail = bl.get("symbols_detail") or {}
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
        if not board_allowed(ts) or xq in exclude or ts in bad or ts in detail or "ST" in nm:
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
        d5 = 99.0
        if len(lows) >= 5 and min(lows[-5:]) > 0:
            d5 = (closes[-1] / min(lows[-5:]) - 1) * 100
        scored.append({"ts": ts, "name": nm, "xq": xq, "amt20": amt20, "r60": r60, "r20": r20,
                       "lim": lim, "cross": cc, "mv": (mv.get(ts, 0) or 0) / 1e4,
                       "dist_prevlow": round(d1, 2), "dist_low5": round(d5, 2),
                       "trig_price": round(lows[-1] * 1.005, 3)})
    if not scored:
        return _st("no_scored", "成分域经板块/ST/成交额过滤后为空")
    def pct_rank(vals, key):
        srt = sorted(vals, key=lambda r: r[key] if r[key] is not None else -1e9)
        rank = {r["ts"]: i + 1 for i, r in enumerate(srt)}
        n = len(srt)
        return lambda ts: rank[ts] / n
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
    cap_yi = sum(r["amt20"] for r in scored)
    scored.sort(key=lambda r: (-r["leader"], -r["cross"], r["ts"]))
    wait_pool = scored[:pool_n]                                   # ①等待池(老龙头榜, 含HIGH等回调)
    # 风向标: 等待池第一(辨识度最高龙头)状态
    wind = wait_pool[0] if wait_pool else None
    wind_broken = bool(wind and wind["dist_prevlow"] <= -0.5)     # 收破前日低0.5%=风向标走弱(v1判据,可校)
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
                          "broken": wind_broken, "dist_prevlow": wind["dist_prevlow"] if wind else None},
            "etf_fallback": etf_used, "etf": etf,
            "wait_pool_top": [{k: r[k] for k in ("name", "ts", "pos", "leader", "r20", "r60",
                                                  "amt20", "dist_prevlow", "dist_low5", "trig_price")} for r in wait_pool[:6]]}
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
                           "picks": len(picks), "t1": len(t1), "etf_fallback": etf_used})
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
