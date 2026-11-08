# -*- coding: utf-8 -*-
"""p2_entry_gate.py — P2 统一入场闸门(Step1+Step2): wave/systemic/macro 代码层硬/软拦。
供 check_entry_filters / trade_graph 共用, 避免各通道判定不一致。
模式: P2_GATE_MODE=1(默认硬拦) / 0(dry-run 只记录不拦)
Step2: lhb_foreign_sell 方向感知(海外链/红利核心 map) + p2_gate_log 命中日志。
注意: crowding_blacklist 硬拦已于 2026-09-13 真删(D15 证拦反 + 用户指令); risk_flags 仍在 indicator 内实现。
"""

# ── DSN 收口（scripts/migrate_hardcoded_dsn.py 自动插入，见 core/dsn.py）──
import os as _os_dsn, sys as _sys_dsn
_ROOT_DSN = _os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.abspath(__file__)))))
if _ROOT_DSN not in _sys_dsn.path:
    _sys_dsn.path.insert(0, _ROOT_DSN)
from core import dsn as _dsn_src  # noqa: E402
import os, json, datetime as _dt


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
    from app.services import wave_store as _ws   # ★ 2026-10-21 步 3：统一读入口（PG 优先 ✓ 文件兜底 ✓）
    st=_ws.get_wave()
    if not st: return None
    op=(st.get("operation") or "side").lower()
    try:
        from app.services.wave_ladder_gate import adopt_wave_op
        op = adopt_wave_op(op)          # ★ 浪型链影子/接管（WOLF_WAVE_LADDER 默认 0 ⇒ 原样 ✓）
    except Exception:
        pass
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
        except Exception as _e_sil1:
            _silent_alert("p2_entry_gate.py:45", _e_sil1)
    return _load("p2_macro_direction_map.json") or {}

def _candidate_dirs(ts_code):
    """候选属于哪类外资主导方向: 海外链/红利核心(用 stock_concept_map 当前成分匹配)。"""
    if not ts_code: return []
    dirs=[]
    try:
        import psycopg2 as _pg
        conn=_pg.connect(_dsn_src.main())
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

def _b4b5_gate(symbol=None, ts_code=None):
    """B4 主线响应 / B5 避险抢戏（数据层 apps/main_line/breakout_context.py）。失败 fail-open。"""
    try:
        import sys as _sys
        _root = os.environ.get("WORKSPACE_PATH") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        _p = os.path.join(_root, "apps", "main_line")
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
        import breakout_context as _bc
        if not _bc.b4b5_gate_enabled():
            return None
        _theme = _bc.theme_of_symbol(ts_code) or _bc.theme_of_symbol(symbol)
        if not _theme:
            return {"action": "unknown", "allow": True, "reasons": ["B4/B5无主题"], "theme": None}
        return _bc.gate(_theme, _bc.prev_trade_day())
    except Exception as _e:
        _silent_alert("p2_entry_gate.py:_b4b5_gate", _e)
        return None


def _b8_gate(symbol=None, ts_code=None):
    """B8 回踩三态：跌破确认=block；跌破待确认=downgrade。失败 fail-open。"""
    try:
        import sys as _sys
        _root = os.environ.get("WORKSPACE_PATH") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        _p = os.path.join(_root, "apps", "main_line")
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
        import breakout_context as _bc
        if not _bc.b8_gate_enabled():
            return None
        _code = ts_code or symbol
        if not _code:
            return None
        return _bc.b8_gate(_code, _bc.prev_trade_day())
    except Exception as _e:
        _silent_alert("p2_entry_gate.py:_b8_gate", _e)
        return None


def _log(symbol, ts_code, out):
    try:
        row={"at":_dt.datetime.now().isoformat(),"symbol":symbol or ts_code,"ts_code":ts_code,
             "hard_block":bool(out.get("hard_block")),"multiplier":round(float(out.get("multiplier") or 1.0),2),
             "reasons":out.get("reasons") or [], "b4b5":(out.get("b4b5") or {}).get("action"),
             "b8":(out.get("b8") or {}).get("action")}
        p=_path("p2_gate_log.jsonl")
        with open(p,"a",encoding="utf-8") as f:
            f.write(json.dumps(row,ensure_ascii=False)+"\n")
    except Exception as _e_sil2:
        _silent_alert("p2_entry_gate.py:77", _e_sil2)

def p2_gate_check(symbol=None, ts_code=None):
    """返回 {hard_block, multiplier, reasons, wave, macro, systemic}。"""
    hard=_mode()
    out={"hard_block":False,"multiplier":1.0,"reasons":[],"wave":None,"macro":None,"systemic":None}
    w=_wave_decision()
    out["wave"]=w
    sr=_load("systemic_risk.json")
    if sr:
        out["systemic"]={"level":sr.get("level",0),"advice":sr.get("advice","")}
        lv=int(sr.get("level") or 0)
        if lv>=2:
            # 2026-10-26：level=2 是“大光/光模块个股止盈”，不是组合防御，不再全面硬拦
            out["reasons"].append("P2系统性风险level=2: 大光/光模块止盈信号（非全面防御），不硬拦")
    ms=_load("macro_state.json")
    if ms:
        flags=(ms.get("macro_switches") or {}).get("flags") or []
        out["macro"]={"date":ms.get("date"),"flags":flags}
        if "margin_burst" in flags:
            # 2026-10-25 纯删除：G2 不再硬拦 T 回补；只降级，不硬 block
            msg="P2宏观margin_burst(杀杠杆/两融净卖): 降级0.5, 不接飞刀"
            out["multiplier"]=min(out["multiplier"],0.5); out["reasons"].append(msg)
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
    # B8：突破后回踩三态（跌破确认=不买；跌破待确认=等次日）
    try:
        _g8 = _b8_gate(symbol, ts_code)
        if _g8:
            out["b8"] = {k: _g8.get(k) for k in ("state","level","breakout","retest",
                                                 "retest_low","retest_close","confirm_close",
                                                 "action","reasons")}
            _a8 = _g8.get("action")
            if _a8 == "block":
                _hard8 = str(os.getenv("WOLF_B8_HARD", "1")).strip().lower() in ("1","true","yes","on")
                if hard and _hard8:
                    out["hard_block"] = True
                else:
                    out["multiplier"] = min(out["multiplier"], 0.5)
                out["reasons"].append("B8门: " + "；".join(_g8.get("reasons") or []))
            elif _a8 == "downgrade":
                out["multiplier"] = min(out["multiplier"], 0.5)
                out["reasons"].append("B8门: " + "；".join(_g8.get("reasons") or []))
            elif _a8 == "unknown":
                out["reasons"].append("B8门: 数据缺失(fail-open)")
    except Exception as _e_b8:
        _silent_alert("p2_entry_gate.py:b8", _e_b8)

    # B4/B5：主线响应 / 避险抢戏（开关 WOLF_B4B5_GATE 默认 1；失败 fail-open）
    try:
        _g = _b4b5_gate(symbol, ts_code)
        if _g:
            out["b4b5"] = {k: _g.get(k) for k in ("theme","mainline_ret","bank_ret","gold_ret",
                                                  "mainline_ok","hedge_flag","action","reasons")}
            _act = _g.get("action")
            if _act == "block":
                _hard = str(os.getenv("WOLF_B4B5_HARD", "0")).strip().lower() in ("1","true","yes","on")
                if hard and _hard:
                    out["hard_block"] = True
                else:
                    out["multiplier"] = min(out["multiplier"], 0.5)
                out["reasons"].append("B4/B5门: " + "；".join(_g.get("reasons") or []))
            elif _act == "downgrade":
                out["multiplier"] = min(out["multiplier"], 0.5)
                out["reasons"].append("B4/B5门: " + "；".join(_g.get("reasons") or []))
            elif _act == "unknown":
                out["reasons"].append("B4/B5门: 数据缺失(fail-open)")
    except Exception as _e_b45:
        _silent_alert("p2_entry_gate.py:b4b5", _e_b45)
    if out["reasons"]:
        _log(symbol,ts_code,out)
    return out

if __name__=="__main__":
    import json as _j
    print(_j.dumps(p2_gate_check(),ensure_ascii=False,indent=1))
