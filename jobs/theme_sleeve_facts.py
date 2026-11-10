#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_facts.py — 题材 sleeve 证据卡 v2（实验版，不交易）。

从 L2 non-mainline trial/watch 生成：
  · 题材强度：ret1/ret5/excess5/breadth/amount_ratio
  · 题材成员：全成员（dc_member/index_weight，缓存）
  · B4：按题材成员直接聚合涨停/炸板/最高板/梯队
  · 套牢：cyq_perf 的 winner_rate / price_vs_cost85
  · 角色：1 龙头 / 2 风向 / 3 个 10CM 中流砥柱
输出 data/mainline/{date}/theme_sleeve_facts.json。
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "core"), os.path.join(ROOT, "jobs")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query  # noqa: E402
from stock_agent_facts import members_for, _fetch_bars_multi  # noqa: E402


def load_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _i(v):
    f = _f(v)
    return 0 if f != f else int(f)


def _strict_open_times(symbol: str, date8: str, limit_price: float) -> dict:
    """从 1 分钟线识别“真开板”：连续 >=2 分钟收在涨停价下方，算一次。"""
    try:
        from tushare_relay import relay_query
        x = relay_query("stk_mins", ts_code=symbol, freq="1min",
                        start_date=f"{date8[:4]}-{date8[4:6]}-{date8[6:]} 09:30:00",
                        end_date=f"{date8[:4]}-{date8[4:6]}-{date8[6:]} 15:00:00", limit=1000)
        if x is None or len(x) == 0: return {}
        x = x.sort_values("trade_time")
        closes = [float(c) for c in x["close"]]
        # 只看首次触及涨停之后的分钟，之前是拉升段
        first = next((i for i, c in enumerate(closes) if c >= float(limit_price) - 1e-6), None)
        if first is None: return {}
        below = [c < float(limit_price) - 1e-6 for c in closes[first:]]
        episodes = 0; i = 0
        while i < len(below):
            if below[i]:
                j = i
                while j < len(below) and below[j]: j += 1
                if j - i >= 2: episodes += 1
                i = j
            else:
                i += 1
        lows = [float(v) for v in x["low"]][first:]
        return {"open_times_strict": episodes, "open_minutes_strict": sum(below),
                "max_dip_strict": round((min(lows) / float(limit_price) - 1) * 100, 3) if lows else None}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--skip-intraday", action="store_true")
    a = ap.parse_args()
    base = os.path.join(ROOT, "data", "mainline", a.date)
    try:
        from app.services.theme_snapshot_store import load as load_snapshot
        theme = load_snapshot(a.date)
    except Exception:
        theme = None
    if not theme:
        theme = json.load(open(os.path.join(base, "theme.json"), encoding="utf-8"))
    ml = json.load(open(os.path.join(base, "mainline.json"), encoding="utf-8"))
    mcfg = load_yaml(os.path.join(ROOT, "config", "mainline", "universe_mainline.yaml"))
    try:
        from app.services.theme_universe_store import load_theme_config
        tcfg = load_theme_config() or {}
    except Exception:
        tcfg = {}
    if not tcfg:
        tcfg = load_yaml(os.path.join(ROOT, "config", "mainline", "generated", "universe_theme.yaml"))
    ts = {x["id"]: x for x in (tcfg.get("themes") or []) + (tcfg.get("satellite_themes") or [])}
    main_tracks = set()
    for m in mcfg.get("mainlines") or []:
        if m.get("id") == ml.get("mainline"):
            main_tracks = set(m.get("tracks") or [])
            break

    # 当日行情（一次全市场）
    daily = {}
    try:
        d = relay_query("daily", trade_date=a.date, limit=6000)
        if d is not None:
            for _, r in d.iterrows():
                daily[str(r.get("ts_code"))] = {"pct": _f(r.get("pct_chg")), "close": _f(r.get("close")),
                                                "amount": _f(r.get("amount"))}
    except Exception as e:
        print("[theme] daily fail:", str(e)[:100], flush=True)

    # 涨停梯队：当天没有就退上一交易日，并标记 stale
    lrows, b4_date, b4_stale = [], a.date, False
    try:
        d = relay_query("limit_list_d", trade_date=a.date, limit=6000)
        if d is not None and len(d):
            lrows = d.to_dict("records")
        else:
            from mainline_common import last_trade_date
            prev = last_trade_date((datetime.strptime(a.date, "%Y%m%d") - __import__("datetime").timedelta(days=1)).strftime("%Y%m%d"))
            d = relay_query("limit_list_d", trade_date=prev, limit=6000)
            if d is not None and len(d):
                lrows = d.to_dict("records"); b4_date = prev; b4_stale = True
    except Exception as e:
        print("[theme] limit_list fail:", str(e)[:100], flush=True)
    lim = {str(r.get("ts_code")): r for r in lrows}

    # 筹码：当天没出就回退上一交易日（cyq_perf 常晚于 18:45）
    chips = {}
    chips_date = a.date
    try:
        from mainline_common import trade_dates as _tds
        _dates = [str(x) for x in _tds(a.date)][-8:]
        for _d in reversed(_dates):
            try:
                d = relay_query("cyq_perf", trade_date=_d, limit=6000)
            except Exception:
                d = None
            if d is not None and len(d):
                chips_date = _d
                for _, r in d.iterrows():
                    chips[str(r.get("ts_code"))] = {"winner": _f(r.get("winner_rate")),
                                                    "cost85": _f(r.get("cost_85pct")),
                                                    "cost15": _f(r.get("cost_15pct")),
                                                    "cost50": _f(r.get("cost_50pct"))}
                break
    except Exception as e:
        print("[theme] cyq fail:", str(e)[:100], flush=True)

    full = {z.get("id"): z for z in (theme.get("themes") or [])}
    ihist_path = os.path.join(ROOT, "data", "theme_intraday_history.json")
    try:
        ihist = json.load(open(ihist_path, encoding="utf-8"))
    except Exception:
        ihist = {}
    hist_path = os.path.join(ROOT, "data", "theme_b4_history.json")
    try:
        hist = json.load(open(hist_path, encoding="utf-8"))
    except Exception:
        hist = {}
    lk_path = os.path.join(ROOT, "data", "theme_linkage_history.json")
    try:
        lk = json.load(open(lk_path, encoding="utf-8"))
    except Exception:
        lk = {}
    # 题材候选策略：include 可覆盖“主线 parent 排除”，exclude 最高优先
    _policy = {}
    try:
        _policy = load_yaml(os.path.join(ROOT, "config", "mainline", "theme_candidate_policy.yaml"))
    except Exception:
        pass
    _inc = set(_policy.get("include_themes") or [])
    _exc = set(_policy.get("exclude_themes") or [])
    _active_ids = []
    for _x in (theme.get("focus") or []) + (theme.get("trial") or []) + (theme.get("watch") or []):
        _tid = _x.get("id")
        if _tid in _exc:
            continue
        _t = tcfg.get(_tid) or {}
        if _tid not in _inc and main_tracks and _t.get("parent_track") in main_tracks:
            continue
        _active_ids.append(_tid)
    for _tid in _inc:
        if _tid not in _active_ids and _tid in tcfg:
            _active_ids.append(_tid)
    # 只做指定题材（A 也从这几个里找）
    try:
        _allow_cfg=load_yaml(os.path.join(ROOT,'config','mainline','theme_allowed.yaml')) or {}
        _allow_ids=set(_allow_cfg.get('allowed_theme_ids') or [])
        if _allow_ids:
            _active_ids=[x for x in _active_ids if x in _allow_ids]
    except Exception as _e:
        print('[theme] allow filter skip:',str(_e)[:80],flush=True)
    # 源题材/持仓题材即使 verdict=avoid 也要保留，否则 A 看不到源
    for _tid in ('robot_actuator','humanoid_robot'):
        if _tid in ts and _tid not in _active_ids:
            _active_ids.append(_tid)
    try:
        import psycopg2
        c=psycopg2.connect(os.environ['DATABASE_URL']); cur=c.cursor()
        cur.execute("select symbol from paper_positions where account_id='stock' and volume>0")
        held=[r[0] for r in cur.fetchall()]
        if held:
            cur.execute("select distinct theme_id from theme_membership where symbol=any(%s) and as_of_date=(select max(as_of_date) from theme_membership)",(held,))
            for (tid,) in cur.fetchall():
                if tid in ts and tid not in _active_ids: _active_ids.append(tid)
        c.close()
    except Exception as e:
        print('[theme] held theme skip:',str(e)[:80],flush=True)
    out = []
    _active = set(_active_ids)
    try:
        _scfg=load_yaml(os.path.join(ROOT,'config','mainline','theme_subchain.yaml')) or {}
        _subchain=_scfg.get('themes') or {}
    except Exception:
        _subchain={}
    _items = list((theme.get("focus") or []) + (theme.get("trial") or []) + (theme.get("watch") or []))
    _seen = {x.get("id") for x in _items}
    for _tid in _active_ids:
        if _tid not in _seen:
            _items.append({"id": _tid, "name": (full.get(_tid) or {}).get("name") or _tid})
    for x in _items:
        tid = x.get("id")
        if tid not in _active:
            continue
        t = ts.get(tid) or {}
        fx = full.get(tid) or {}
        mem = []
        try:
            _srcs = list(t.get("sources") or []) + list(((t.get("resolution") or {}).get("selected")) or [])
            _t2 = dict(t); _t2["sources"] = _srcs
            mem = sorted(set(members_for(_t2, a.date)))
        except Exception:
            mem = []
        # 历史人气：成员 120 日内进入主题涨幅前 3 的次数
        pop = {}
        try:
            from datetime import timedelta as _td
            d120 = (datetime.strptime(a.date, "%Y%m%d") - _td(days=200)).strftime("%Y%m%d")
            bm = _fetch_bars_multi(mem, d120, a.date, a.date) if mem else {}
            pcts = {}
            for c, df in bm.items():
                if df is None or len(df) < 2:
                    continue
                pcts[c] = [float(v or 0) for v in df.sort_values("trade_date")["pct_chg"].tail(120)]
            n = min([len(v) for v in pcts.values()]) if pcts else 0
            for c in pcts:
                pop[c] = 0
            for off in range(1, n + 1):
                vals = sorted(((c, v[-off]) for c, v in pcts.items()), key=lambda x: -x[1])
                for c, _ in vals[:3]:
                    pop[c] = pop.get(c, 0) + 1
        except Exception as e:
            print("[theme] popular fail:", str(e)[:100], flush=True)
        # 角色
        cand = []
        for c in mem:
            q = daily.get(c) or {}
            lr = lim.get(c) or {}
            times = _i(lr.get("limit_times"))
            cand.append({"symbol": c, "pct": q.get("pct"), "amount": q.get("amount"), "close": q.get("close"),
                         "limit": str(lr.get("limit") or ""), "limit_times": times, "popular_120": pop.get(c, 0),
                         "first_time": str(lr.get("first_time") or ""), "open_times": _i(lr.get("open_times")),
                         "fd_amount": _f(lr.get("fd_amount"))})
        def _key(z):
            ft = z.get("first_time") or "999999"
            return (z["limit_times"], -int(ft) if str(ft).isdigit() else -999999,
                    z.get("fd_amount") or 0, z["pct"] or -99, z["amount"] or 0)
        cand.sort(key=_key, reverse=True)
        # 狼大口径：先按板块内总市值前5 + 非配套段选核心候选，再用老龙头/人气确认
        try:
            _mv={}; _ind={}
            with __import__('psycopg2').connect(os.environ['DATABASE_URL'],connect_timeout=3) as _c:
                with _c.cursor() as _cur:
                    _cur.execute("select ts_code,total_mv from mkt_bars_daily where trade_date=(select max(trade_date) from mkt_bars_daily where trade_date<=%s) and ts_code=any(%s)",(a.date,[z['symbol'] for z in cand]))
                    _mv={str(r[0]):float(r[1] or 0) for r in _cur.fetchall()}
                    _cur.execute("select ts_code,industry from stock_pool where ts_code=any(%s)",([z['symbol'] for z in cand],))
                    _ind={str(r[0]):str(r[1] or '') for r in _cur.fetchall()}
            for z in cand:
                z['total_mv']=_mv.get(z['symbol'],0); z['industry']=_ind.get(z['symbol'],'')
            _ranked=sorted(cand,key=lambda z:-(z.get('total_mv') or 0))
            for i,z in enumerate(_ranked): z['mv_rank']=i+1
            from app.services.wolf_leader_rules import SUPPORT_INDUSTRY, SUPPORT_NAME
            def _support(z):
                if z.get('industry') in SUPPORT_INDUSTRY: return True
                return any(k in str(z.get('industry') or '') for k in SUPPORT_NAME)
            # 历史老龙头/上一波 leader：即使当日市值不在前5，也保留进核心候选
            _prior=[]
            try:
                _rp=os.path.join(ROOT,'data','asof',f'{a.date}_roles.json')
                if os.path.exists(_rp):
                    _pr=json.load(open(_rp,encoding='utf-8'))
                    _prior=[((_pr.get('roles') or {}).get('leader'))]
            except Exception: pass
            core=[z for z in cand if z.get('mv_rank',999)<=5 and not _support(z)]
            for _ps in _prior:
                if _ps and not any(z['symbol']==_ps for z in core):
                    _z=next((z for z in cand if z['symbol']==_ps),None)
                    if _z and not _support(_z): core.append(_z)
            # 营收拐点/增速：只查核心候选，避免全市场请求
            for z in core:
                try:
                    _fin=relay_query('fina_indicator',ts_code=z['symbol'],limit=8)
                    if _fin is not None and len(_fin):
                        _fin=_fin.sort_values('end_date')
                        _ys=[float(x) for x in _fin.get('tr_yoy',[]) if x==x]
                        if len(_ys)>=2:
                            z['revenue_yoy_latest']=round(_ys[-1],2); z['revenue_yoy_prev']=round(_ys[-2],2)
                            z['revenue_inflection']=bool(_ys[-1] > _ys[-2] and _ys[-1] > 0)
                        elif _ys:
                            z['revenue_yoy_latest']=round(_ys[-1],2); z['revenue_inflection']=bool(_ys[-1]>0)
                except Exception:
                    pass
            core.sort(key=lambda z:(bool(z.get('revenue_inflection')), z.get('popular_120') or 0, z.get('pct') or -99, z.get('amount') or 0),reverse=True)
            _pl=next((z for z in core if _prior and z['symbol']==_prior[0]),None)
            leader=_pl or (core[0] if core else None)
            _core=[{k:z.get(k) for k in ('symbol','total_mv','mv_rank','industry','pct','popular_120','revenue_yoy_latest','revenue_inflection')} for z in core]
        except Exception as e:
            print('[theme] core leader fail:',str(e)[:100],flush=True)
            core=[]; leader=None; _core=[]
        rest=[z for z in cand if not leader or z['symbol']!=leader['symbol']]
        wind = [z for z in rest if z["symbol"].startswith(("300", "301", "688")) and (z["pct"] or 0) > 0][:2]
        wind_syms = {z["symbol"] for z in wind}
        mid = [z for z in rest if z["symbol"] not in wind_syms and not z["symbol"].startswith(("300", "301", "688")) and (z["pct"] or 0) > 0][:3]
        # B4 直接按成员聚合
        u = [z for z in cand if z["limit"] == "U"]
        zz = [z for z in cand if z["limit"] == "Z"]
        dd = [z for z in cand if z["limit"] == "D"]
        times = [z["limit_times"] for z in u]
        ladder = {"1": sum(1 for v in times if v == 1), "2": sum(1 for v in times if v == 2), "3+": sum(1 for v in times if v >= 3)}
        mx = max(times) if times else 0
        open_sum = sum(int(z.get("open_times") or 0) for z in u)
        open_max = max([int(z.get("open_times") or 0) for z in u] or [0])
        tag = "plain"
        if u or zz:
            if open_max >= 2 or (zz and (len(zz) / max(1, len(u) + len(zz))) >= 0.4 and len(zz) >= 2):
                # 炸板只作证据；不再打 failed 结论
                tag = "open_heavy"
            elif len(u) >= 4 and mx <= 1:
                tag = "graduation"
            elif ladder["1"] >= 1 and ladder["2"] >= 1 and (ladder["3+"] >= 1 or ladder["2"] >= 2):
                tag = "complete"
        # 套牢：成员平均
        ov = []
        for z in cand:
            ch = chips.get(z["symbol"])
            if ch and ch.get("cost85") and z.get("close"):
                ov.append({"winner": ch["winner"], "pv85": z["close"] / ch["cost85"] - 1})
        overhang = None
        if ov:
            overhang = {"winner_rate": round(sum(v["winner"] for v in ov) / len(ov), 2),
                        "price_vs_cost85": round(sum(v["pv85"] for v in ov) / len(ov), 4),
                        "n": len(ov)}
        rec = {"theme_id": tid, "name": t.get("name") or x.get("name"), "parent_track": t.get("parent_track"),
               "verdict": x.get("verdict"), "ret1": fx.get("ret1"), "ret5": fx.get("ret5"),
               "excess5": fx.get("excess5"), "breadth": fx.get("breadth"), "amount_ratio": fx.get("amount_ratio"),
               "members": len(mem), "leader": ({"code": leader["symbol"], "name": leader.get("name")} if leader else {}), "leader_source": "wolf_core",
               "roles": {"leader": (leader or {}).get("symbol"), "wind": [z["symbol"] for z in wind],
                         "mid": [z["symbol"] for z in mid]},
               "b4": {"date": b4_date, "stale": b4_stale, "u": len(u), "z": len(zz), "d": len(dd),
                      "max_times": mx, "ladder": ladder, "tag": tag, "open_sum": open_sum, "open_max": open_max},
               "overhang": overhang, "overhang_date": chips_date,
               "popular_120": {x["symbol"]: x.get("popular_120", 0)
                                  for x in ([leader] + wind + mid if leader else [])
                                  if x},
               "popular_top3": [x["symbol"] for x in sorted(cand, key=lambda z: -(z.get("popular_120") or 0))[:3]],
               "core_candidates": _core,
               "role_detail": {
                   "leader": ({k: leader.get(k) for k in ("symbol", "first_time", "open_times", "fd_amount", "limit_times", "pct")} if leader else None),
                   "wind": [{k: x.get(k) for k in ("symbol", "first_time", "open_times", "fd_amount", "limit_times", "pct")} for x in wind],
                   "mid": [{k: x.get(k) for k in ("symbol", "first_time", "open_times", "fd_amount", "limit_times", "pct")} for x in mid],
               }}
        if leader:
            _st = _strict_open_times(leader["symbol"], a.date, leader.get("close") or 0)
            if _st:
                rec["b4"].update(_st)
                _ld = rec["role_detail"].get("leader") or {}
                _ld.update(_st); rec["role_detail"]["leader"] = _ld
                leader["open_times_strict"] = _st.get("open_times_strict")
        try:
            from app.services.theme_bellwether_bridge import attach as attach_bellwether
            attach_bellwether(rec,set(mem),a.date)
        except Exception as _be:
            rec["bellwether_error"]=str(_be)[:80]

        tier = "watch"
        # B4 只作为证据，不再单凭 open_max/z 否决整个题材
        if tag == "complete" and (fx.get("breadth") or 0) >= 0.5:
            tier = "probe_candidate"
        elif (fx.get("ret1") or 0) > 0.03 and (fx.get("breadth") or 0) > 0.5:
            tier = "watch_strong"
        rec["tier"] = tier
        rec["sub_chain"] = _subchain.get(tid, tid)
        # 龙头带动 + 阶段（可观测代理；不是终局）
        try:
            _lp=(leader or {}).get('pct')
            _winds=[x for x in wind]; _mids=[x for x in mid]
            _wu=sum(1 for x in _winds if (x.get('pct') or 0)>0)/max(1,len(_winds)) if _winds else 0
            _mh=sum(1 for x in _mids if (x.get('pct') or 0)>0)/max(1,len(_mids)) if _mids else 0
            _drive=sum([1 if (_lp or 0)>0 else 0, 1 if _wu>=0.5 else 0, 1 if _mh>=0.5 else 0, 1 if (fx.get('breadth') or 0)>=0.5 else 0])
            rec['leader_drive']={'leader_pct':_lp,'wind_up':round(_wu,2),'mid_hold':round(_mh,2),'breadth':fx.get('breadth'),'score':_drive}
            _u=rec['b4'].get('u',0); _br=fx.get('breadth') or 0
            if _lp is not None and _lp<0 and _u==0 and _br<0.5: _phase='distribution'
            elif _u==0 and _br<0.5: _phase='late'
            elif _u>=3 and _br>=0.6: _phase='launch'
            elif _u>0: _phase='acceleration'
            else: _phase='late'
            rec['source_phase']=_phase; rec['source_phase_source']='facts_proxy'
            _b4h=[z for z in (hist.get(tid) or []) if int(z.get('u') or 0)>=3]
            rec['last_launch_date']=_b4h[-1].get('date') if _b4h else None
        except Exception as _e:
            rec['source_phase']='unknown'; rec['leader_drive']={}

        if not a.skip_intraday:
            try:
                from app.services.t_data_sources import fetch_tencent_mkline
                syms = [rec["roles"].get("leader")] + list(rec["roles"].get("wind") or []) + list(rec["roles"].get("mid") or [])
                syms = [x for x in dict.fromkeys(syms) if x]
                vals = []
                for sym in syms[:6]:
                    _s = sym.split(".")[0]
                    _sym = ("sh" if sym.endswith(".SH") else "sz") + _s
                    bars = fetch_tencent_mkline(_sym, freq="m1", count=500) or []
                    v1330 = 0.0; vopen = 0.0
                    for b in bars:
                        t = str(b.get("time") or "")
                        if len(t) >= 16:
                            hm = t[11:16]
                        elif len(t) >= 12:
                            hm = t[-4:-2] + ":" + t[-2:]
                        else:
                            hm = ""
                        if "13:30" <= hm < "13:35":
                            v1330 += float(b.get("vol") or 0)
                        elif "09:30" <= hm < "10:00":
                            vopen += float(b.get("vol") or 0)
                    if v1330 or vopen:
                        vals.append({"symbol": sym, "v1330": round(v1330, 0), "vopen": round(vopen, 0),
                                     "r": round(v1330 / vopen, 3) if vopen else None})
                rec["intraday"] = {"n": len(vals), "roles": vals}
                old_i = ihist.get(tid) or []
                prev_i = [x for x in old_i if str(x.get("date")) < str(a.date)][-5:]
                if prev_i:
                    # ★ 2026-10-30 修：这里原来叫 `base`（dict）⇒ **覆盖了同函数上面的
                    #   `base = data/mainline/<date>` 路径** ⇒ 文末 os.path.join(base, ...) 抛
                    #   TypeError: expected str, bytes or os.PathLike object, not dict
                    #   （16:45 链就是这样挂的：前几日 intraday 历史一有 prev_i 就触发 ✗）。
                    base_map = {}
                    for pv in prev_i:
                        for z in pv.get("roles") or []:
                            base_map.setdefault(z["symbol"], []).append(z.get("r"))
                    rec["intraday"]["baseline"] = {k: round(sum([v for v in vs if isinstance(v, (int, float))]) / max(1, len([v for v in vs if isinstance(v, (int, float))])), 3) for k, vs in base_map.items()}
                old_i = [x for x in old_i if str(x.get("date")) != str(a.date)]
                old_i.append({"date": a.date, "roles": vals})
                ihist[tid] = old_i[-60:]
            except Exception as e:
                rec["intraday"] = {"error": str(e)[:120]}
        # 板块内联动：大哥先涨 / 二哥不掉 / 中流砥柱承接 / 分时同步
        _lp = (daily.get((leader or {}).get("symbol")) or {}).get("pct") if leader else None
        _wind_up = sum(1 for z in wind if (daily.get(z["symbol"]) or {}).get("pct", -99) > 0)
        _mid_hold = sum(1 for z in mid if (daily.get(z["symbol"]) or {}).get("pct", -99) >= -1.0)
        _sync = None
        if rec.get("intraday") and rec["intraday"].get("roles"):
            rs = [z.get("r") for z in rec["intraday"]["roles"] if z.get("r") is not None]
            if rs:
                # burst 没有狼大出处，只保留逐票 r 与均值描述，不生成硬阈值字段
                _sync = {"mean": round(sum(rs) / len(rs), 2), "n": len(rs),
                         "roles": [{"symbol": z.get("symbol"), "r": z.get("r")} for z in rec["intraday"]["roles"]]}
        _state = "weak"
        _flags = []
        if _lp is not None and _lp <= 0:
            _state = "broken"; _flags.append("leader_down")
        elif _lp is not None and _wind_up >= 1 and _mid_hold >= 2:
            _state = "linked"; _flags.append("leader_up+wind+mid")
        elif _lp is not None and _lp > 0:
            _flags.append("leader_up")
        rec["internal_linkage"] = {"leader_pct": _lp, "wind_up": _wind_up, "mid_hold": _mid_hold,
                                    "sync": _sync, "state": _state, "flags": _flags}
        _oldl = lk.get(tid) or []
        _prev_linked = any(str(x.get("date")) < str(a.date) and x.get("state") == "linked" for x in _oldl)
        rec["first_linkage"] = bool(_state == "linked" and not _prev_linked)
        if _state == "broken":
            rec["tier"] = "reject"
        elif _state == "linked" and rec["first_linkage"] and (fx.get("ret1") or 0) > 0.03 and (fx.get("breadth") or 0) >= 0.5:
            rec["tier"] = "probe_candidate"
        elif _state == "linked" and rec.get("tier") == "watch" and (fx.get("ret1") or 0) > 0 and (fx.get("breadth") or 0) >= 0.4:
            rec["tier"] = "watch_strong"
        _oldl = [x for x in _oldl if str(x.get("date")) != str(a.date)]
        _oldl.append({"date": a.date, "state": _state, "leader_pct": _lp, "wind_up": _wind_up, "mid_hold": _mid_hold})
        lk[tid] = _oldl[-120:]
        old = hist.get(tid) or []
        cur = {"date": b4_date, "u": len(u), "z": len(zz), "max_times": mx, "tag": tag}
        prev = next((x for x in reversed(old) if str(x.get("date")) < str(cur["date"])), None)
        if not old or old[-1].get("date") != cur["date"]:
            old.append(cur)
            hist[tid] = old[-120:]
        rec["b4_history"] = {"prev": prev, "n": len(old)}
        out.append(rec)
    out.sort(key=lambda z: (z["tier"] != "probe_candidate", z["tier"] != "watch_strong", -(z.get("ret1") or 0)))
    try:
        json.dump(lk, open(lk_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[theme] linkage history save fail:", str(e)[:100], flush=True)
    try:
        json.dump(ihist, open(ihist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[theme] intraday history save fail:", str(e)[:100], flush=True)
    try:
        json.dump(hist, open(hist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[theme] b4 history save fail:", str(e)[:100], flush=True)
    res = {"as_of": a.date, "sleeve": "theme", "mainline": ml.get("mainline"), "tracks": sorted(main_tracks), "themes": out}
    p = os.path.join(base, "theme_sleeve_facts.json")
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:
        from theme_sleeve_store import save as save_sleeve
        save_sleeve(a.date, "facts", res)
    except Exception as e:
        print("[theme] PG facts save fail:", str(e)[:100], flush=True)
    print("written", p, "themes", len(out))
    for z in out:
        print(z["tier"], z["theme_id"], "| b4", z["b4"]["tag"], z["b4"]["u"], "/", z["b4"]["z"],
              "| overhang", z["overhang"], "| roles", z["roles"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
