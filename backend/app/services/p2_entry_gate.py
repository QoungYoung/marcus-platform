# -*- coding: utf-8 -*-
"""p2_entry_gate.py — P2 统一入场闸门(Step1+Step2): wave/systemic/macro 代码层硬/软拦。
供 check_entry_filters / trade_graph 共用, 避免各通道判定不一致。
模式: P2_GATE_MODE=1(默认硬拦) / 0(dry-run 只记录不拦)
Step2: lhb_foreign_sell 方向感知(海外链/红利核心 map) + p2_gate_log 命中日志。
注意: rotation(crowding_blacklist)与 risk_flags 已在 indicator 内实现。
"""
import os, json, datetime as _dt

def _path(name):
    try:
        from app.config import get_settings
        ws = get_settings().workspace_path
    except Exception:
        ws = os.environ.get("WORKSPACE_PATH", "/app")
    return os.path.join(str(ws), "data", name)

def _load(name):
    try:
        p=_path(name)
        if not os.path.exists(p): return None
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None

def _mode():
    return os.getenv("P2_GATE_MODE", "1").strip() not in ("0", "false", "no")

def _wave_decision():
    st=_load("wave_state.json")
    if not st: return None
    op=(st.get("operation") or "side").lower()
    lvl=st.get("level") or "未知"; sub=st.get("sub_level") or ""
    return {"level":lvl,"sub_level":sub,"operation":op,"gate":"defense" if op in ("defense","exit") else op}

def _direction_map():
    # 配置为权威(config/挂载), data/ 兼容旧路径
    for sub in ("config","data"):
        try:
            from app.config import get_settings
            fp=os.path.join(str(get_settings().workspace_path), sub, "p2_macro_direction_map.json")
            if os.path.exists(fp):
                return json.load(open(fp, encoding="utf-8"))
        except Exception:
            pass
    return _load("p2_macro_direction_map.json") or {}

def _candidate_dirs(ts_code):
    """候选属于哪类外资主导方向: 海外链/红利核心(用 stock_concept_map 当前成分匹配)。"""
    if not ts_code: return []
    dirs=[]
    try:
        import psycopg2 as _pg
        conn=_pg.connect(os.environ.get("DATABASE_URL","postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur=conn.cursor()
        cur.execute("SELECT concept_name FROM stock_concept_map WHERE ts_code=%s",(ts_code,))
        names=[str(r[0]) for r in cur.fetchall()]
        cur.close(); conn.close()
    except Exception:
        names=[]
    dm=_direction_map()
    for key in ("foreign_chain","red_core"):
        cfg=(dm or {}).get(key) or {}
        if any(kw in n for n in names for kw in (cfg.get("kws") or [])):
            dirs.append(key)
    return dirs

def _log(symbol, ts_code, out):
    try:
        row={"at":_dt.datetime.now().isoformat(),"symbol":symbol or ts_code,"ts_code":ts_code,
             "hard_block":bool(out.get("hard_block")),"multiplier":round(float(out.get("multiplier") or 1.0),2),
             "reasons":out.get("reasons") or []}
        p=_path("p2_gate_log.jsonl")
        with open(p,"a",encoding="utf-8") as f:
            f.write(json.dumps(row,ensure_ascii=False)+"\n")
    except Exception:
        pass

def p2_gate_check(symbol=None, ts_code=None):
    """返回 {hard_block, multiplier, reasons, wave, macro, systemic}。"""
    hard=_mode()
    out={"hard_block":False,"multiplier":1.0,"reasons":[],"wave":None,"macro":None,"systemic":None}
    w=_wave_decision()
    out["wave"]=w
    if w and w["gate"] in ("defense","exit"):
        msg="P2浪型=%s/%s op=%s: defense/exit 禁新开仓"%(w["level"],w["sub_level"],w["operation"])
        if hard:
            out["hard_block"]=True; out["reasons"].append(msg)
        else:
            out["multiplier"]=min(out["multiplier"],0.0); out["reasons"].append(msg+"(dry-run)")
    sr=_load("systemic_risk.json")
    if sr:
        out["systemic"]={"level":sr.get("level",0),"advice":sr.get("advice","")}
        lv=int(sr.get("level") or 0)
        if lv>=2:
            msg="P2系统性风险level=%s: %s"%(lv,str(sr.get("advice") or "")[:80])
            if hard:
                out["hard_block"]=True; out["reasons"].append(msg)
            else:
                out["multiplier"]=min(out["multiplier"],0.0); out["reasons"].append(msg+"(dry-run)")
    ms=_load("macro_state.json")
    if ms:
        flags=(ms.get("macro_switches") or {}).get("flags") or []
        out["macro"]={"date":ms.get("date"),"flags":flags}
        if "margin_burst" in flags:
            msg="P2宏观margin_burst(杀杠杆/两融净卖): 不接飞刀, 禁新开仓"
            if hard:
                out["hard_block"]=True; out["reasons"].append(msg)
            else:
                out["multiplier"]=min(out["multiplier"],0.3); out["reasons"].append(msg+"(dry-run降0.3)")
        if "lhb_foreign_sell" in flags:
            dirs=_candidate_dirs(ts_code)
            if any(d in dirs for d in ("foreign_chain","red_core")):
                out["reasons"].append("P2宏观lhb_foreign_sell(外资净卖)命中方向(%s): 降级0.5" % "/".join(dirs))
                out["multiplier"]=min(out["multiplier"],0.5)
            else:
                out["reasons"].append("P2宏观lhb_foreign_sell(外资净卖): 候选非海外链/红利核心, 不拦")
        if "gjd_withdraw" in flags:
            out["reasons"].append("P2宏观gjd_withdraw(GJD撤退/护盘减弱): 降级0.5, 不抢反弹")
            out["multiplier"]=min(out["multiplier"],0.5)
        if "us_yield_spike" in flags or "cn30_spike" in flags:
            out["reasons"].append("P2宏观债市异动(yield_spike): 降级0.5")
            out["multiplier"]=min(out["multiplier"],0.5)
    if out["reasons"]:
        _log(symbol,ts_code,out)
    return out

if __name__=="__main__":
    import json as _j
    print(_j.dumps(p2_gate_check(),ensure_ascii=False,indent=1))
