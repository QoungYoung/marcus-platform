# -*- coding: utf-8 -*-
"""wave.py — 浪型链 · 指数 K线 + 历史浪型（portfolio 页卡片用）。

GET /api/v1/wave/index-history?days=250&code=000001.SH
  → {code, asof, bars:[{date,open,high,low,close,amount}],
     waves:[{date,rung,name,level,sub_level,operation,mode,expect,invest}],
     last:{...最新一天...}, ohlc_source, wave_source, note}

数据来源与**已知局限**（前端必须显式标注，不假装完整）：
  · K线（开高低收）：东财 push2his（klt=101 日线 / fqt=1 前复权），进程内 5 分钟缓存 ✓；
    失败退本地 CSV（data/指数数据/index_daily/000001.SH.csv）—— 该 CSV 近月**只有收盘**（开高低为空）⇒
    合成 O=前收 / H=L=收，并在响应里标 ohlc_source="local_csv(csv_close_only)" ⛔。
  · 浪型：apps/main_line/wave_ladder 的 walk（本地指数日线）⇒ **每个交易日一档**（不是稀疏快照 ✓）；
    缓存按"最后交易日"失效；算不出（缺数据/导入失败）⇒ 返回空 waves + note，前端只画 K 线 ✓ fail-open。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query

router = APIRouter(prefix="/wave", tags=["Wave Ladder"])

_EM_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
           "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt={lmt}")

_CACHE: Dict[str, Any] = {}
_TTL = 300.0
_LAST_ERR: Dict[str, str] = {"waves": ""}   # 浪型链失败原因（暴露到 note，生产可诊断 ✓）


def _data_dir() -> Path:
    """数据目录：DATA_DIR 可能"设了但为空"（生产 backend 容器即如此 ✗）⇒ 空串回落 "data" ✓。

    并**把解析结果写回环境变量**：浪型链 wave_ladder 在 import 时读 DATA_DIR（module 级常量），
    先设好环境再 import，两边才看到同一个目录 ✓。
    """
    cand = (os.environ.get("DATA_DIR") or "").strip()
    if not cand:
        for c in ("/app/data", str(Path(__file__).resolve().parents[3] / "data"), "data"):
            if Path(c).exists():
                cand = c
                break
    cand = cand or "data"
    os.environ["DATA_DIR"] = cand
    return Path(cand)


def _secid(code: str) -> str:
    num, mkt = code.split(".")
    return ("1." if mkt.upper() == "SH" else "0.") + num


def _em_bars(code: str, days: int) -> List[Dict[str, Any]]:
    url = _EM_URL.format(secid=_secid(code), lmt=max(int(days), 60) + 20)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    j = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
    out = []
    for line in ((j.get("data") or {}).get("klines") or []):
        p = line.split(",")
        out.append({"date": p[0].replace("-", ""), "open": float(p[1]), "close": float(p[2]),
                    "high": float(p[3]), "low": float(p[4]), "amount": float(p[6])})
    return out


def _csv_bars(code: str, days: int) -> List[Dict[str, Any]]:
    """本地 CSV：表头 trade_date,open,high,low,close,amount；近月开高低为空 ⇒ 合成并标注 ⛔。"""
    p = _data_dir() / "指数数据" / "index_daily" / ("%s.csv" % code)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    prev_close = 0.0
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines()):
        if i == 0 or not line.strip():
            continue
        c = line.split(",")
        if len(c) < 5:
            continue
        d = c[0].replace("-", "")

        def _f(x: str, dflt: float = 0.0) -> float:
            x = (x or "").strip()
            try:
                return float(x)
            except Exception:
                return dflt

        o, h, low, cl = _f(c[1]), _f(c[2]), _f(c[3]), _f(c[4])
        if not cl:
            continue
        if not o:
            o = prev_close or cl
        if not h:
            h = max(o, cl)
        if not low:
            low = min(o, cl)
        out.append({"date": d, "open": o, "high": h, "low": low, "close": cl, "amount": _f(c[5]) if len(c) > 5 else 0.0})
        prev_close = cl
    return out[-int(days):]


def _kline_cache(code: str, days: int) -> List[Dict[str, Any]]:
    """worker 侧同步的 K线缓存（jobs/sync_index_daily_multi.py 写；后端容器一般无外网 ✗ ⇒ 靠它 ✓）。

    行格式 [date, close, high, amount, open, low]（前 4 项与浪型链兼容 ✓）。
    """
    p = _data_dir() / ("index_kline_%s.json" % code.replace(".", "_"))
    if not p.exists():
        return []
    try:
        rows = ((json.loads(p.read_text(encoding="utf-8")) or {}).get("rows")) or []
    except Exception:
        return []
    out = []
    for r in rows[-int(days) - 20:]:
        try:
            d, cl, hi, am = str(r[0]), float(r[1]), float(r[2]), float(r[3])
            op = float(r[4]) if len(r) > 4 and r[4] else cl
            lo = float(r[5]) if len(r) > 5 and r[5] else min(op, cl)
        except Exception:
            continue
        out.append({"date": d, "open": op, "high": hi, "low": lo, "close": cl, "amount": am})
    return out[-int(days):]


def _bars(code: str, days: int) -> Tuple[List[Dict[str, Any]], str]:
    ck = "bars:%s:%s" % (code, days)
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1], hit[2]
    try:
        b = _em_bars(code, days)
        if b:
            b = b[-int(days):]
            _CACHE[ck] = (time.time(), b, "eastmoney:push2his")
            return b, "eastmoney:push2his"
    except Exception:
        pass                              # 容器内一般没有外网 ✗ ⇒ 走本地缓存/CSV ✓
    b = _kline_cache(code, days)          # ② 本地 K线缓存（jobs/sync_index_daily_multi.py --kline-cache 写）
    if b:
        _CACHE[ck] = (time.time(), b, "local_kline_cache")
        return b, "local_kline_cache"
    b = _csv_bars(code, days)             # ③ 末选本地 CSV（近月只有收盘 ⇒ 合成并标注 ⛔）
    src = "local_csv(csv_close_only)" if b else "none"
    _CACHE[ck] = (time.time(), b, src)
    return b, src


def _ladder() -> Optional[Any]:
    _data_dir()          # ★ 先把 DATA_DIR 落到环境里，再 import 浪型链（它 module 级读这个变量 ✓）
    ck = "ladder"
    if ck in _CACHE:
        return _CACHE[ck] or None
    root = Path(__file__).resolve().parents[3]
    for cand in (root / "apps" / "main_line", Path("/app/apps/main_line")):
        if (cand / "wave_ladder.py").exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    try:
        import wave_ladder as W       # type: ignore
        _CACHE[ck] = W
        return W
    except Exception:
        _CACHE[ck] = False
        return None


def _waves(code: str) -> List[Dict[str, Any]]:
    W = _ladder()
    if W is None:
        return []
    try:
        rows = W.load_rows(code)
        if not rows:
            return []
        last = rows[-1][0]
        ck = "waves:%s:%s" % (code, last)
        hit = _CACHE.get(ck)
        if hit and time.time() - hit[0] < _TTL:
            return hit[1]
        out = []
        for ev in W.walk(rows):
            ws = W.to_wave_state(ev)
            out.append({"date": ev["date"], "rung": ev.get("rung_index"), "name": ev.get("name"),
                        "marks": (ev.get("features") or {}).get("marks"),   # 图上标注：W底/双头的端点+颈线 ✓
                        "level": ws.get("level"), "sub_level": ws.get("sub_level"),
                        "operation": ws.get("operation"), "mode": ws.get("mode") or "",
                        "expect": ev.get("expect"), "invest": ev.get("invest"),
                        "main_up": ev.get("main_up") or "", "reasons": ev.get("reasons") or ""})
        _CACHE[ck] = (time.time(), out)
        return out
    except Exception as e:
        _LAST_ERR["waves"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return []


@router.get("/index-history")
def index_history(days: int = Query(250, ge=30, le=2000), code: str = Query("000001.SH")):
    bars, src = _bars(code, days)
    waves = _waves(code)
    wmap = {w["date"]: w for w in waves}
    today = bars[-1]["date"] if bars else None
    note = ""
    if src.startswith("local_csv"):
        note = "本地 CSV 近月只有收盘 ⇒ 开高低为合成值（O=前收/H=L=收）⛔"
    if not waves:
        note = (note + " ｜ " if note else "") + "浪型链不可用（" + (_LAST_ERR["waves"] or "缺指数数据") + "）⇒ 只画 K 线 ✓"
    return {"code": code, "asof": today, "bars": bars, "waves": waves,
            "last": wmap.get(today or "", None), "ohlc_source": src,
            "wave_source": "wave_ladder.walk" if waves else "none", "note": note}
