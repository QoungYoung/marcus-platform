# -*- coding: utf-8 -*-
"""L1/L2 regime gate：只读 wave_state，不导入生产重服务。

数据优先级：
  1) data/wave_state_history/<YYYYMMDD>.json（回测）
  2) data/wave_state_<YYYY-MM-DD>.json / <YYYYMMDD>.json（复盘）
  3) data/wave_state.json（实时）
口径对齐 docs/wave-state-consumer-guide.md：
  operation = build/t_only/side/defense/exit；mode=自救。
  operation 是状态，不是买信号；side 需人工确认；卖类永不拦（卖侧由执行层负责）。
"""
from __future__ import annotations
import datetime as dt
import json, os
from typing import Any, Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")

SLEEVES = {
    "build": ["core", "T", "low_position", "theme"],
    "t_only": ["T", "low_position", "theme"],
    "side": ["T", "low_position"],
    "defense": ["rescue"],
    "exit": ["rescue"],
}
TARGET = {"build": 75, "t_only": 50, "side": 50, "defense": 30, "exit": 30}


def _read(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_wave(date8: Optional[str] = None) -> tuple[Optional[dict], str]:
    if date8 and date8 != "live":
        for p in (os.path.join(DATA, "wave_state_history", date8 + ".json"),
                  os.path.join(DATA, "wave_state_%s-%s-%s.json" % (date8[:4], date8[4:6], date8[6:8])),
                  os.path.join(DATA, "wave_state_%s.json" % date8)):
            w = _read(p)
            if w:
                return w, p
        # 当天还没有 history/dated 时，允许用 live（仅当 date8 就是今天）
        if date8 == dt.date.today().strftime("%Y%m%d"):
            w = _read(os.path.join(DATA, "wave_state.json"))
            if w:
                return w, "data/wave_state.json(live_today)"
        return None, "missing"
    w = _read(os.path.join(DATA, "wave_state.json"))
    return w, "data/wave_state.json"


def regime_label(w: dict) -> str:
    fam = str(w.get("family") or "").lower()
    stage = str(w.get("stage") or "").lower()
    sub = str(w.get("sub_level") or "")
    op = str(w.get("operation") or "").lower()
    if fam == "down" or "下跌" in sub or "down" in stage:
        return "主跌1浪"
    if "4-5" in stage or "4-5" in sub or "失败5" in sub:
        return "4-5"
    if "4-4" in stage or "4-4" in sub:
        return "4-4"
    if "4-3" in stage or "4-2" in stage or "4-1" in stage:
        return "4浪调整"
    if op == "build":
        return "主升"
    return "unknown"


def build_gate(date8: Optional[str] = None) -> Dict[str, Any]:
    w, src = load_wave(date8)
    if not w:
        return {"date": date8 or "live", "regime": "unknown", "operation": None,
                "allow_new_base": False, "allow_t": False, "confirm_needed": False,
                "max_position": 0, "active_sleeves": [], "no_trade": True,
                "data_quality": {"source": src, "status": "missing"}, "wave": None}
    op = str(w.get("operation") or "").lower()
    mode = str(w.get("mode") or "")
    self_rescue = (mode == "自救")
    # defense 不建新底仓，但允许 T/自救式战术；exit/自救 才是硬 no_trade
    rescue = self_rescue or op == "exit"
    if self_rescue or op == "exit":
        sleeves = ["rescue"]
    elif op == "defense":
        sleeves = ["T", "rescue"]
    else:
        sleeves = list(SLEEVES.get(op, []))
    # side = 观望/调仓，买类需人工确认（guide 语义），不是禁止
    allow_new = op in ("build", "side") and not self_rescue and op != "exit"
    confirm = op == "side" and not self_rescue
    # defense 允许 T，但不允许新底仓；exit/自救 不允许新买/T
    allow_t = op in ("build", "t_only", "side", "defense") and not self_rescue and op != "exit"
    target = TARGET.get(op, 0)
    # 只有 exit/自救 或没有 sleeve 才算硬 no_trade
    no_trade = self_rescue or op == "exit" or not sleeves
    return {
        "date": date8 or "live",
        "data_date": w.get("date"),
        "session_date": w.get("session_date"),
        "regime": regime_label(w),
        "operation": op or None,
        "mode": mode or None,
        "sub_level": w.get("sub_level"),
        "family": w.get("family"),
        "stage": w.get("stage"),
        "status": w.get("status"),
        "confidence": w.get("confidence"),
        "allow_new_base": bool(allow_new),
        "confirm_needed": bool(confirm),
        "allow_t": bool(allow_t),
        "max_position": target if not rescue else min(target, 30),
        "active_sleeves": sleeves,
        "no_trade": bool(no_trade),
        "data_quality": {"source": src, "wave_source": w.get("source"),
                         "status": "ok" if w.get("source") == "wave_ladder" else "untrusted"},
        "wave": w,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="live")
    a = ap.parse_args()
    g = build_gate(a.date)
    g.pop("wave", None)
    print(json.dumps(g, ensure_ascii=False, indent=1))
