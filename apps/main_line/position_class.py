# -*- coding: utf-8 -*-
"""
position_class.py — 东财概念级高低位分类器（狼大: 低位看逻辑、高位看量价）。
读 data/concept_hist.json + data/wave_state.json + data/concept_vol.json(可选) + wolf_theme_features/latest_hot_sectors。
每概念输出 {position(HIGH/MID/LOW/UNKNOWN), trend, op, features, fund_flow, vol, catalyst, hot, macro_level, confirm}。
运行于 worker 容器（需 tushare 抓概念矩阵；缓存增量更新）。
用法: python apps/main_line/position_class.py
"""
import os, sys, json, math
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # confirm_chain 同目录
import pandas as pd, numpy as np
from confirm_chain import confirm_chain, mainline_act   # P1 确定性门槛: 狼大确认链(S1-S4/F1-F4)+F3主线响应
DATA=os.environ.get("DATA_DIR","/app/data")
HIST=os.path.join(DATA,"concept_hist.json"); WAVE=os.path.join(DATA,"wave_state.json")
VOL=os.path.join(DATA,"concept_vol.json"); THEME=os.path.join(DATA,"wolf_theme_features_v5.csv"); HOT=os.path.join(DATA,"latest_hot_sectors.json")
INDEX_DAILY=os.path.join(DATA,"index_daily_000001.json")   # 指数K线(close+vol/amount), 确认链宏观gate用

_INDEX_CONFIRM_CACHE=None
def live_hedge_act():
    """避险方向响应度 0-1 (F4诱多): 银行+贵金属(黄金)行业净流入强度 — 缩量拉避险=诱多(2026-08-05)
    抓 moneyflow_ind_dc(行业) 当日, 银行/黄金净流入为正且排名靠前 → 高"""
    try:
        import requests, urllib3
        urllib3.disable_warnings()
        from datetime import datetime, timedelta
        K="tsr_1FjRkziz3M7m0aLcTk0ZgnK03__xO3EYq0ZdwQqdwSE"
        d0=datetime.now().strftime("%Y%m%d")
        items=[]
        for _ in range(5):
            try:
                r=requests.get("https://pcd.mobcvb.cn/tushare/pro/moneyflow_ind_dc",
                               params={"content_type":"行业","trade_date":d0},
                               headers={"X-API-Key":K}, verify=False, timeout=30)
                items=(r.json().get("data") or {}).get("items") or []
                if items: break
            except Exception: pass
            d0=(datetime.now()-timedelta(days=1)).strftime("%Y%m%d")
        if not items: return None
        fields=(requests.get("https://pcd.mobcvb.cn/tushare/pro/moneyflow_ind_dc",
                             params={"content_type":"行业","trade_date":d0},
                             headers={"X-API-Key":K}, verify=False, timeout=30).json().get("data") or {}).get("fields") or []
        all_na=[]; hedge=[]
        for it in items:
            z=dict(zip(fields, it))
            nm=str(z.get("name") or "")
            if z.get("net_amount") is not None:
                all_na.append((nm, float(z["net_amount"])))
            if "银行" in nm or "黄金" in nm or "贵金属" in nm:
                if z.get("net_amount") is not None:
                    hedge.append(float(z["net_amount"]))
        if not all_na or not hedge: return None
        hedge_net=sum(hedge)
        rank=sum(1 for _,n in all_na if n<hedge_net)
        fund=(rank+1)/len(all_na)
        # 避险净流入为正 且 排名靠前 → 高响应
        return float(min(1.0, fund*1.5)) if hedge_net>0 else float(fund*0.3)
    except Exception:
        return None

def read_index_confirm():
    """指数级确认链状态(宏观gate): 狼大'指数企稳后再做'——指数确认未触发, 概念LOW埋伏降级"""
    global _INDEX_CONFIRM_CACHE
    if _INDEX_CONFIRM_CACHE is not None:
        return _INDEX_CONFIRM_CACHE
    try:
        rows=_load_json(INDEX_DAILY)
        if not rows: _INDEX_CONFIRM_CACHE="下跌中"; return _INDEX_CONFIRM_CACHE
        df=pd.DataFrame(rows)
        df["trade_date"]=pd.to_datetime(df["trade_date"])
        df=df.sort_values("trade_date")
        ser=df.set_index("trade_date")["close"].apply(pd.to_numeric,errors="coerce").dropna().astype(float)
        vol=df.set_index("trade_date")["vol"].apply(pd.to_numeric,errors="coerce").dropna().astype(float)
        cc=confirm_chain(ser, None, vol, hedge_act=live_hedge_act())
        _INDEX_CONFIRM_CACHE=cc.get("stage","下跌中")
    except Exception:
        _INDEX_CONFIRM_CACHE="下跌中"
    return _INDEX_CONFIRM_CACHE

# 高低位阈值(可配置, 6.2) —— 初值, 后续回测校准
CONFIG={"low_vh":-30,"low_box":35,"low_vma60":0,      # LOW: 距高<=-30 且 箱体<=35 且 vma60<0
        "high_vh":10,"high_box_hi":80,"high_vma60":0,"high_vh_floor":20,  # HIGH(正值, 代码用 vh>=-high_vh)
        "mid_box_lo":35,"mid_box_hi":65,
        "top_inflow_n":40,"vol_confirm_pct":0.15}

def _load_json(p):
    try: return json.load(open(p,encoding="utf-8"))
    except Exception: return {}

def read_wave():
    st=_load_json(WAVE)
    return {"level":st.get("level"),"sub_level":st.get("sub_level"),"op":st.get("operation")}

# 主题→概念 别名映射(theme关键词→概念名匹配) — 让 wolf_theme_features catalyst 命中东财概念
THEME_ALIAS={"AI/算力/科技":["人工智能","算力","AIGC","AI应用","DeepSeek","英伟达","AI眼镜","AI智能体","云计算","边缘计算","东数西算","液冷","CPO","PCB","光通信","信创","数据","半导体","芯片"],
            "半导体/芯片":["半导体","芯片","存储","光刻","先进封装","集成电路","第三代半导体","封测"],
            "军工/航天":["军工","航天","国防","卫星","核聚变","无人机","导弹","低空"],
            "资源/周期":["有色","黄金","锂","钴","稀土","煤炭","石油","钢铁","化工","资源"]}
def _theme_of(name):
    for theme,kws in THEME_ALIAS.items():
        if any(k in name for k in kws): return theme
    return None
def load_theme_hot():
    theme_cat={}; hot=set()
    # 优先用 main_line_state.catalyst(theme->catalyst, 在线)，其次 wolf_theme_features(已迁移, 若在则读)
    st=_load_json(ML) if "ML" in globals() else _load_json(os.path.join(DATA,"main_line_state.json"))
    for k,v in (st.get("catalyst") or {}).items(): theme_cat[k]=float(v or 0)
    if not theme_cat and os.path.exists(THEME):
        try:
            df=pd.read_csv(THEME)
            for _,r in df.iterrows():
                nm=str(r.get("theme") or r.get("name") or "")
                if nm: theme_cat[nm]=float(r.get("catalyst",0) or 0)
        except Exception: pass
    h=_load_json(HOT)
    for k in h.get("hot_concepts",[]) if isinstance(h,dict) else []:
        hot.add(str(k))
    return theme_cat, hot
def catalyst_of(name, theme_cat):
    th=_theme_of(name)
    return theme_cat.get(th) if th else None

def read_logic():
    return _load_json(os.path.join(DATA,"low_logic.json"))  # name->{logic_score,verdict,reason}

def build_rel_map(hist, upto=None):
    """G2 相对主线位置 (2026-09-01): 概念 vs 全体概念 的 250日(或可用窗口)涨幅百分位。
    low(<0.4)/mid(0.4~0.6)/high(>0.6)。upto: 历史时点(回测用), None=最新。"""
    rs = {}
    for code, a in hist.items():
        ser = close_series(a)
        if upto is not None:
            ser = ser[ser.index <= pd.Timestamp(upto)]
        if len(ser) < 60: continue
        N = min(250, len(ser))
        if N < 30: continue
        b0 = float(ser.iloc[-N])
        if b0 <= 0: continue
        rs[code] = (float(ser.iloc[-1]) / b0 - 1) * 100
    if not rs: return {}
    arr = np.array(sorted(rs.values()))
    rel = {}
    for code, r in rs.items():
        pct = (np.searchsorted(arr, r) + 1) / len(arr)
        rel[code] = {"pct": round(float(pct), 3),
                     "level": "low" if pct < 0.4 else ("high" if pct > 0.6 else "mid")}
    return rel

def structure_of(ser, span=20):
    """概念级结构形态识别 (G1, 2026-09-01) — 狼大'结构高位'语义。
    输入: close Series(≥80点)。输出: {'m_top','new_high_pullback','c_break','b_rebound','desc'}。
    原理: 波段高点(span日局部最高)→ 双头/M顶(两高相近且现价低于双高) / 新高后回落 /
          破前低(C杀) / 低点后反弹未过前高(B反)。与 build_wave_pivots 同思路, 窗口改小适配 250 日概念序列。
    """
    c = ser.values.astype(float); n = len(c)
    out = {"m_top": False, "new_high_pullback": False, "c_break": False, "b_rebound": False, "desc": "flat"}
    if n < 80: return out
    # 波段高点: span 窗口局部最大 且 右侧不创新高
    highs = []
    for i in range(span, n - 1):
        lo = max(0, i - span); hi = min(n, i + span + 1)
        if c[i] == c[lo:hi].max() and c[i] >= c[i - 1] and c[i] >= c[i + 1]:
            highs.append((i, float(c[i])))
    # 相邻≤10日合并取更高
    ded = []
    for i, v in highs:
        if ded and i - ded[-1][0] <= 10:
            if v >= ded[-1][1]: ded[-1] = (i, v)
        else:
            ded.append((i, v))
    recent = [h for h in ded if h[0] >= n - 120]
    lows = []
    for i in range(span, n - 1):
        lo = max(0, i - span); hi = min(n, i + span + 1)
        if c[i] == c[lo:hi].min() and c[i] <= c[i - 1] and c[i] <= c[i + 1]:
            lows.append((i, float(c[i])))
    lded = []
    for i, v in lows:
        if lded and i - lded[-1][0] <= 10:
            if v <= lded[-1][1]: lded[-1] = (i, v)
        else:
            lded.append((i, v))
    lrecent = [l for l in lded if l[0] >= n - 120]
    last = float(c[-1])
    # 双头/M顶: 最近两个波段高点相近(±5%)、间隔 5~40 日, 且现价低于双高
    if len(recent) >= 2:
        h1, h2 = recent[-2], recent[-1]
        if h1[1] > 0 and 0.95 <= h2[1] / h1[1] <= 1.05 and 5 <= h2[0] - h1[0] <= 40:
            if last < max(h1[1], h2[1]) * 0.97:
                out["m_top"] = True
                out["desc"] = "双头M顶"
    # 新高后回落: 最近波段高点后回落>10% 且 未形成双头
    if recent and not out["m_top"]:
        h = recent[-1]
        if last < h[1] * 0.90 and (n - 1 - h[0]) >= 5:
            out["new_high_pullback"] = True
            out["desc"] = "新高回落" if out["desc"] == "flat" else out["desc"] + "+新高回落"
    # 破前低(C杀): 现价跌破最近波段低点
    if lrecent and last < lrecent[-1][1]:
        out["c_break"] = True
        out["desc"] = "破位C杀" if out["desc"] == "flat" else out["desc"] + "+破位"
    # B反: 从最近低点反弹>3% 且 未过最近高点
    if lrecent and recent:
        if last > lrecent[-1][1] * 1.03 and last < recent[-1][1]:
            out["b_rebound"] = True
            if out["desc"] == "flat": out["desc"] = "B反"
    return out

def resonance(pos,fe,fs,cat,logic,macro_op,vol=None):
    """多信号共振 → 只在同向汇聚时给操作, 否则观望。返回 (action, signals)。vol: 概念量能代理(可选)"""
    vol=vol or {}
    sig={}
    fsdir=fs.get("dir","flat")
    sig["position"]=pos; sig["fund"]=fsdir
    lscore=None; lv=""
    if logic:
        lscore=logic.get("logic_score"); lv=logic.get("verdict","")
    sig["logic_strong"]=bool((lscore is not None and lscore>=0.6) or lv=="值得埋伏低吸")
    sig["catalyst"]=bool(cat is not None and cat>=0.7)
    # 量能: 放量(z20高)或缩量滞涨(vz120低且近高) → 顶; 地量缩量(vz120低) → 底
    vz=vol.get("vol_z20"); vp=vol.get("vol_pct120")
    sig["volume_top"]=bool((vz is not None and vz>1.5) or (vp is not None and vp<0.15 and fe.get("vs_1y_high_pct",0)>-10))
    sig["volume_bottom"]=bool(vp is not None and vp<0.15)
    macro_danger=macro_op not in ("build",)   # 非主升 → 高位谨慎(高位+非主升=只做T/减仓)
    low_block=macro_op in ("defense","exit")  # 低位: 防御/兑现期不抄底(下跌中继/派发); 筑底/调整(side/t_only)允许埋伏
    sig["macro"]=macro_op
    # G1 结构高位: 双头/M顶/新高回落 — 狼大'结构高位'语义(价格可深跌但结构未走完, 如 2026-08 科技B反)
    st=fe.get("structure") or {}
    struct_high=st.get("m_top") or st.get("new_high_pullback")
    sig["structure"]=st.get("desc","flat")
    # G2 相对主线位置: 狼大'低位方向'=相对主线(科技)涨幅落后的板块(如 2026-08-07 医药/电力/航天)
    rel=fe.get("rel_mainline") or "mid"
    sig["rel_mainline"]=rel
    if pos=="LOW":
        ok=(fsdir=="in" and (sig["logic_strong"] or sig["catalyst"]) and not low_block)
        sig["n"]=int(fsdir=="in")+int(sig["logic_strong"] or sig["catalyst"])
        sig["low_block"]=low_block
        # P1 确定性门槛: LOW 只是埋伏候选, confirm_chain 触发(S3突破候选/S4确认)才是入场(狼大: 买在确定)
        cc=fe.get("confirm_stage") or "下跌中"
        sig["confirm"]=cc
        confirm_ok=cc in ("确认","突破候选")
        # 指数级宏观gate: 狼大'指数企稳后再做'——指数确认链未触发, 概念LOW埋伏降级(等指数确认)
        idx_cc=fe.get("index_confirm") or "下跌中"
        sig["index_confirm"]=idx_cc
        idx_ok=idx_cc in ("确认","突破候选")
        # 结构高位叠加低位(深跌后的B反) → 不埋伏(等结构走完), 只做T
        if struct_high and macro_danger: return "减仓/只做T", sig
        if ok and confirm_ok and idx_ok: return "低吸埋伏", sig
        if ok and confirm_ok and not idx_ok: return "观望(埋伏候选,等指数确认)", sig
        if ok: return "观望(埋伏候选,等确认链)", sig
        return "观望", sig
    if pos=="HIGH":
        if fe.get("vs_ma60_pct",0)<-5: return "防御清仓", sig
        # G2 相对主线低位豁免: 自身箱顶但相对主线涨幅落后(刚启动的低位方向) + 资金流入 → 持仓等, 不按高位减仓
        if rel=="low" and fsdir=="in" and macro_op!="defense":
            return "观望(相对低位启动)", sig
        # 主线强势回踩低吸: 主升期(build) + 非结构高位 + 缩量回踩(-8%~0) + 资金未流出
        r20v=fe.get("r20")
        if macro_op=="build" and not struct_high and r20v is not None and -8<=r20v<0 and fsdir!="out":
            return "回踩低吸", sig
        if fsdir=="out" or sig["volume_top"] or macro_danger: return "减仓/只做T", sig
        return "观望(高位健康可持有)", sig
    vm60=fe.get("vs_ma60_pct",0)
    r20v=fe.get("r20")
    # v2缺口1: 趋势破位减仓 — MID + 明显跌破MA60(<-5%) + 资金未流入 → 减仓/只做T (狼大: 跌破趋势线反抽减仓, 2025-11-23 AI硬)
    if pos=="MID" and vm60<-5 and fsdir!="in":
        return "减仓/只做T", sig
    # v2缺口2: 长期趋势回踩低吸 — MID + 趋势未破(vm60>0) + 回踩(-10~0) + 非防御 + 非结构高位 → 回踩低吸 (狼大: 长期趋势上游低位埋伏, 2026-03-03 AI硬)
    if pos=="MID" and macro_op!="defense" and not struct_high and vm60>0 and r20v is not None and -10<=r20v<0:
        return "回踩低吸", sig
    if struct_high:   # MID/LOW + 结构高位(双头/M顶/新高回落)
        # v2缺口3: 深跌+资金流出 → 跌透不追杀, 转观望 (狼大: 连跌十几天错杀不追减, 2026-03-03 软件; 资金流入仍减仓=诱多, 2026-08 半导体)
        if r20v is not None and r20v<-8 and fsdir=="out": return "观望", sig
        if fsdir=="out" or macro_danger: return "减仓/只做T", sig
        return "观望", sig
    return "观望", sig

def close_series(a):
    idx=pd.to_datetime(a["dates"]); return pd.Series(a["close"],index=idx).dropna()

def position_features(ser):
    if len(ser)<30: return None
    c=float(ser.iloc[-1]); hi=float(ser.iloc[-250:].max()); lo=float(ser.iloc[-250:].min())
    box=ser.iloc[-30:]; bhi,blo=float(box.max()),float(box.min()); rng=bhi-blo or 1
    ma60=float(ser.iloc[-60:].mean()) if len(ser)>=60 else float(ser.mean())
    ma200=float(ser.iloc[-200:].mean()) if len(ser)>=200 else float(ser.iloc[-120:].mean())
    m60a=float(ser.iloc[-60:].mean()) if len(ser)>=60 else float(ser.mean())
    m60b=float(ser.iloc[-30:].mean()) if len(ser)>=30 else float(ser.mean())
    return {"close":round(c,1),"vs_1y_high_pct":round((c/hi-1)*100,1),"vs_1y_low_pct":round((c/lo-1)*100,1),
            "box_pos_pct":round((c-blo)/rng*100,1),"vs_ma60_pct":round((c/ma60-1)*100,1),"vs_ma200_pct":round((c/ma200-1)*100,1),
            "ma60_slope":round((m60a-m60b)/abs(m60b or 1)*100,3),
            "r20":round((c/float(ser.iloc[-21])-1)*100,1) if len(ser)>=21 else None}

def classify(f):
    if f is None: return {"position":"UNKNOWN","trend":"?","op":"side"}
    C=CONFIG
    vh=f["vs_1y_high_pct"]; pos=f["box_pos_pct"]; vm60=f["vs_ma60_pct"]; slope=f["ma60_slope"]; r20=f["r20"]
    trend="UP" if (slope>0.3 and vm60>0) else ("DOWN" if (slope<-0.3 and vm60<0) else "ADJUST")
    if vh<=C["low_vh"] and pos<=C["low_box"] and vm60<0: position="LOW"; 
    elif (vh>=-C["high_vh"] and pos>=65 and vm60>0) or (pos>=C["high_box_hi"] and vm60>0 and trend=="UP" and vh>=-C["high_vh_floor"]): position="HIGH"
    else: position="MID"
    if position=="LOW": op="side"
    elif position=="HIGH":
        if vh>=-C["high_vh"] and vm60>0 and r20 is not None and r20>8: op="t_only"
        elif vm60<-5: op="defense"
        else: op="exit"
    else: op="side"
    return {"position":position,"trend":trend,"op":op,"features":f}

def fund_sig(a):
    tail=[x for x in a.get("net_amount",[])[-10:] if x is not None]
    if not tail: return {"dir":"flat","conv":0,"strength":0}
    last=tail[-1]; conv=0
    for x in reversed(tail):
        if x>0: conv+=1
        else: break
    return {"dir":"in" if last>0 else ("out" if last<0 else "flat"),"conv":conv,"strength":round(last/1e8,1)}

def classify_one(code,a,wave,vol_map,cat,hot,logic,rel_map=None,hist=None):
    ser=close_series(a); f=position_features(ser)
    res=classify(f); fs=fund_sig(a)
    res["name"]=a["name"]; res["fund_flow"]=fs; res["vol"]=vol_map.get(code,{})
    res["catalyst"]=catalyst_of(a["name"], cat); res["hot"]=a["name"] in hot
    macro_op=wave.get("op")
    res["macro_level"]=wave
    if f:
        f["structure"]=structure_of(ser)   # G1 结构形态
        rel=(rel_map or {}).get(code)      # G2 相对主线位置
        if rel: f["rel_mainline"]=rel["level"]; f["rel_mainline_pct"]=rel["pct"]
        # P1 确定性门槛: LOW 概念跑 confirm_chain(缩量止跌→结构→放量突破→站稳+F3主线响应), 输出确认状态
        try:
            net_s=pd.Series(a["net_amount"], index=pd.to_datetime(a["dates"])).apply(pd.to_numeric, errors="coerce")
            ml_state=_load_json(os.path.join(DATA,"main_line_state.json"))
            cc=confirm_chain(ser, net_s, mainline_act=mainline_act(hist or {}, str(ser.index[-1].date()), ml_state))
            f["confirm_stage"]=cc.get("stage","下跌中")
            res["confirm_chain"]={"stage":cc.get("stage"),"signals":cc.get("signals",{}),"desc":cc.get("desc","")}
        except Exception as e:
            res["confirm_chain"]={"stage":"下跌中","signals":{},"desc":"confirm_chain err: %s"%str(e)[:60]}
            print("[position_class] confirm_chain err %s: %s"%(a["name"], str(e)[:100]), file=sys.stderr)
        f["index_confirm"]=read_index_confirm()   # 指数确认链宏观gate(全概念共享)
    action,sig=resonance(res["position"],f or {},fs,res["catalyst"],logic.get(a["name"]),macro_op,res["vol"])
    res["action"]=action; res["signals"]=sig
    if res["position"]=="LOW" and fs["dir"]=="in": res["confirm"]="资金确认"
    if res["position"]=="HIGH" and fs["dir"]=="out": res["confirm"]="资金流出"
    return res

def main():
    hist=_load_json(HIST); wave=read_wave(); vol_map=_load_json(VOL); cat,hot=load_theme_hot()
    if not hist: print("NO concept_hist.json; run build_concept_matrix.py first"); return
    logic=read_logic(); out={}
    rel_map=build_rel_map(hist)   # G2 相对主线位置(全量概念)
    for code,a in hist.items():
        try: out[code]=classify_one(code,a,wave,vol_map,cat,hot,logic,rel_map,hist)
        except Exception as e: out[code]={"name":a.get("name"),"err":str(e)[:60]}
    json.dump(out, open(os.path.join(DATA,"position_class_result.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
    # 轮动/风控过滤接口 (5.2): 输出 高位应减/只做T 与 低位应埋伏 的 top 概念
    def by(dir_, pos):
        r=[v for v in out.values() if v.get("position")==pos and v.get("fund_flow",{}).get("dir")==dir_ and "features" in v]
        r.sort(key=lambda v:-(v["fund_flow"].get("strength") or 0))
        return r[:8]
    print("概念数:", len(out), "| 量能代理概念:", len(vol_map), "| 主题催化/热度命中:", sum(1 for v in out.values() if v.get("catalyst") or v.get("hot")))
    print("--- 高位+资金流出(应减/只做T)top ---")
    for v in by("out","HIGH"): print(" ", v["name"], v.get("op"), "净流出", v["fund_flow"]["strength"],"亿")
    print("--- 低位+资金流入(可埋伏/低吸)top ---")
    for v in by("in","LOW"): print(" ", v["name"], "净流入", v["fund_flow"]["strength"],"亿", v.get("confirm",""))
if __name__=="__main__": main()
