# -*- coding: utf-8 -*-
"""p2_entry_gate.py — P2 统一入场闸门(Step1): wave/systemic/macro 的代码层硬/软拦。
供 check_entry_filters / trade_graph 共用, 避免各通道判定不一致。
模式: P2_GATE_MODE=1(默认硬拦) / 0(dry-run 只记录不拦)
注意: rotation(crowding_blacklist)与 risk_flags 已在 indicator 内实现, 本模块暂不重复;
      Step2 再收方向感知(海外链/红利核心 map)与日志。
"""
import os, json

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

def p2_gate_check(symbol=None, ts_code=None):
    """返回 {hard_block, multiplier, reasons, wave, macro, systemic}。"""
    hard=_mode()
    out={"hard_block":False,"multiplier":1.0,"reasons":[],"wave":None,"macro":None,"systemic":None}
    # 1) 浪型: defense/exit → 新开仓硬拦(与 trade_graph.node_check_safety 同源)
    w=_wave_decision()
    out["wave"]=w
    if w and w["gate"] in ("defense","exit"):
        msg="P2浪型=%s/%s op=%s: defense/exit 禁新开仓"%(w["level"],w["sub_level"],w["operation"])
        if hard:
            out["hard_block"]=True; out["reasons"].append(msg)
        else:
            out["multiplier"]=min(out["multiplier"],0.0); out["reasons"].append(msg+"(dry-run)")
    # 2) 系统性风险: level>=2 → 硬拦新开
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
    # 3) 宏观开关: margin_burst 硬拦新开; 其余软降
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
            out["reasons"].append("P2宏观lhb_foreign_sell(外资净卖): 降级0.5(Step2再方向感知)")
            out["multiplier"]=min(out["multiplier"],0.5)
        if "gjd_withdraw" in flags:
            out["reasons"].append("P2宏观gjd_withdraw(GJD撤退/护盘减弱): 降级0.5, 不抢反弹")
            out["multiplier"]=min(out["multiplier"],0.5)
        if "us_yield_spike" in flags or "cn30_spike" in flags:
            out["reasons"].append("P2宏观债市异动(yield_spike): 降级0.5")
            out["multiplier"]=min(out["multiplier"],0.5)
    return out

if __name__=="__main__":
    import json as _j
    print(_j.dumps(p2_gate_check(),ensure_ascii=False,indent=1))
