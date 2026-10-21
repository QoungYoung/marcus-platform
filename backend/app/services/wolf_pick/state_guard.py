# -*- coding: utf-8 -*-
"""状态新鲜度护栏（P0-2）。

为什么：`main_line_state.json` / `wave_state.json` 可能落后数天甚至一个月；
决策卡如果直接读，会拿过期状态做今天的买卖。本模块只做**只读检查**，不改任何状态。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, Optional


def parse_day(value: Any) -> Optional[_dt.date]:
    """兼容 20261014 / 2026-10-14 / 2026/10/14 / 2026-10-14 13:40。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return _dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def check_state(state: Optional[Dict[str, Any]], as_of: Any, name: str = "state",
                max_stale_days: int = 3) -> Dict[str, Any]:
    """检查单个状态是否新鲜。

    返回：{name, present, date, age_days, stale, missing, reason}
    - 缺日期 / 解析失败 ⇒ stale=True（宁可降级，不拿未知状态做决策）
    - age < 0（状态来自未来）⇒ stale=True
    """
    if not isinstance(state, dict) or not state:
        return {"name": name, "present": False, "date": None, "age_days": None,
                "stale": True, "missing": True, "reason": "missing"}
    raw = state.get("date") or state.get("as_of")
    d = parse_day(raw)
    ref = parse_day(as_of) or _dt.date.today()
    if d is None:
        return {"name": name, "present": True, "date": raw, "age_days": None,
                "stale": True, "missing": False, "reason": "no_parseable_date"}
    age = (ref - d).days
    if age < 0:
        reason = "future_state"
    elif age > max_stale_days:
        reason = "stale_%dd" % age
    else:
        reason = "fresh"
    return {"name": name, "present": True, "date": d.isoformat(), "age_days": age,
            "stale": bool(age < 0 or age > max_stale_days), "missing": False, "reason": reason}


def load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def latest_market_day(data_dir: str) -> Optional[str]:
    """读本地上证日线的最后一个交易日（用于“状态比行情旧”的检查）。"""
    obj = load_json(os.path.join(data_dir, "index_daily_000001_SH.json"))
    rows = obj.get("rows") or []
    if not rows:
        return None
    d = "".join(ch for ch in str(rows[-1][0]) if ch.isdigit())[:8]
    return d if len(d) == 8 else None


def check_files(data_dir: str, as_of: Any, max_stale_days: int = 3) -> Dict[str, Any]:
    """检查主线/波浪两个状态文件；返回 {mainline, wave, market_day, any_stale}。

    除日历年龄外，再查“状态是否比本地行情旧”：若 wave_state.date < 最新行情日，
    即使只差 1-2 天也标记 stale（2026-10-15 盘中破 3764、state 仍停在 10-14 的实例）。
    """
    ms = load_json(os.path.join(data_dir, "main_line_state.json"))
    from app.services import wave_store as _ws   # ★ 步 3：PG 优先 ✓
    ws = _ws.get_wave() or load_json(os.path.join(data_dir, "wave_state.json"))
    out = {
        "mainline": check_state(ms, as_of, "main_line_state", max_stale_days),
        "wave": check_state(ws, as_of, "wave_state", max_stale_days),
        "market_day": latest_market_day(data_dir),
    }
    mkt = out["market_day"]
    for name in ("mainline", "wave"):
        st = out[name]
        if mkt and st.get("date") and str(st["date"]).replace("-", "") < mkt:
            st["stale"] = True
            st["reason"] = "market_data_newer(%s>%s)" % (mkt, str(st["date"]).replace("-", ""))
    out["any_stale"] = bool(out["mainline"]["stale"] or out["wave"]["stale"])
    return out
