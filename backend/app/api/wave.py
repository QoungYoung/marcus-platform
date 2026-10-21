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


@router.get("/state")
def get_wave_state(asof: Optional[str] = Query(None, description="YYYYMMDD；给了就取 session_date<=asof 的最近一档（回测口径 ✓）")):
    """**浪型状态的统一出口**（2026-10-21 步 3 ✓）。

    读 `app.services.wave_store`（PG 优先 ✓ 文件兜底 ✓）；前端/外部/脚本一律走这里，
    不要再各读各的 `data/wave_state.json` ✗。
    """
    from app.services import wave_store as _ws
    w = _ws.get_wave_asof(asof) if asof else _ws.get_wave()
    return {"ok": bool(w), "store": _ws.mode(), "asof": (asof or ""), "state": w or {},
            "note": "store=both ⇒ 先 PG 再文件兜底 ✓；读不到返回 ok=false（调用方须 fail-open ✓）"}

_EM_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
           "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt={lmt}")

_CACHE: Dict[str, Any] = {}
_TTL = 300.0
_LAST_ERR: Dict[str, str] = {"waves": "", "marks": ""}


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


def _display_patterns(code: str, bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """**显示用**形态标注（W底/双头）—— 刻意用 K线这段长序列算，**不碰浪型链的状态数据** ✓。

    为什么必须分开：生产上浪型链的指数序列只有 600 根（2024-04 起），把它的窗口接长会**挪动 1 浪顶**
    （实测 3674.40 → 3174.27 ✗✗ —— 那是铁律②的失效线，不能动 ✓）；而形态标注只是画图，
    用 K线缓存（约 950 根 ✓）就能把 2024-02-05 + 2024-09-13 那种双底凑齐 ✓。
    """
    if len(bars) < 60:
        return []
    try:
        W = _ladder()
        if W is None:
            return []
        rows = [(b["date"], b["close"], b["high"], b["amount"]) for b in bars]
        pv = W.pivots(rows, rows[-1][0])
        # 显示专用回看窗（默认 900 根 ≈ 3.5 年 ✓）：**与状态判据的 WB_WIN 分开** ——
        # 状态要"新鲜"（180 根内）才有操作意义 ✓；但画图希望看到历史形态（如 2024-09-30 那次 W底 ✓）
        win = int(os.environ.get("WOLF_WAVE_LADDER_MARKS_WIN", "900") or 900)
        since = rows[-min(win, len(rows) - 60)][0] if len(rows) > 60 else ""
        amt20 = sum(r[3] for r in rows[-21:-1]) / 20 if len(rows) >= 21 and sum(r[3] for r in rows[-21:-1]) else 0.0
        vr = (bars[-1]["amount"] / amt20) if amt20 else 0.0
        hi_excl = max(r[1] for r in rows[-21:-1]) if len(rows) >= 21 else 0.0
        def _confirm_date(l2: str, neck: float, above: bool) -> str:
            """确认日 = l2 之后**第一根**以放量（≥WB_VOL×前20日均额）收在颈线正确一侧的 K 线 ✓。

            必须按**历史当日**的量价判 ✗ 不能拿今天的量价（首版就这么写错了：今天量比 1.1 < 1.2 ⇒ 历史形态全丢 ✗）。
            """
            if not neck:
                return ""
            for i, b in enumerate(bars):
                if b["date"] <= l2 or i < 21:
                    continue
                prev = bars[i - 20:i]
                amt = sum(x["amount"] for x in prev) / 20 if prev else 0.0
                ok_side = (b["close"] > neck) if above else (b["close"] < neck)
                if ok_side and amt and b["amount"] >= W.WB_VOL * amt:
                    return b["date"]
            return ""

        def _scan(kind: str) -> List[Dict[str, Any]]:
            """扫描**所有配对**（不能只看最后两个端点 ✗ —— 实测：最近两个低点是 2024-09-13 + 2025-04-07，
            相差 14% 不成形；真正成对的是更早的 2024-02-05 + 2024-09-13 ✓）。

            返回每个成立形态的 {kind, neck, points, first_date(确认日)} ✓；显示专用，不动链的状态 ✓。
            """
            is_low = (kind == "w_bottom")
            pts = [p for p in pv if str(p[3]).upper() == ("L" if is_low else "H")
                   and (not since or str(p[1]) >= since)]
            near = W.WB_NEAR if is_low else W.DT_NEAR
            res = []
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    v1, v2 = pts[i][2], pts[j][2]
                    if not v1 or abs(v2 / v1 - 1) > near:
                        continue
                    if is_low and v2 > v1 * (1 + near):
                        continue                              # 双底右低点应大致不低于左低点 ✓
                    d1, d2 = str(pts[i][1]), str(pts[j][1])
                    mids = [p for p in pv if d1 < str(p[1]) < d2
                            and str(p[3]).upper() == ("H" if is_low else "L")]
                    if not mids:
                        continue
                    neck = (max if is_low else min)(p[2] for p in mids)
                    cfd = _confirm_date(d2, neck, is_low)
                    if not cfd:
                        continue
                    res.append({"kind": kind, "neck": round(neck, 2), "confirmed": True, "first_date": cfd,
                                "points": [{"date": d1, "value": round(v1, 2)}, {"date": d2, "value": round(v2, 2)}]})
            return res

        out = []
        out.extend(_scan("w_bottom"))
        out.extend(_scan("dtop"))
        return out
    except Exception as e:
        _LAST_ERR["marks"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        return []


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
            out.append({"date": ev["date"], "session_date": W.next_session_date(ev["date"]),
                        "rung": ev.get("rung_index"), "name": ev.get("name"),
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
    disp = _display_patterns(code, bars)
    return {"code": code, "asof": today, "bars": bars, "waves": waves, "display_patterns": disp,
            "last": wmap.get(today or "", None), "ohlc_source": src,
            "wave_source": "wave_ladder.walk" if waves else "none", "note": note}


@router.get("/low-position-card")
def low_position_card():
    """Portfolio 卡片：低位超额反弹线（paper-only）状态。"""
    import datetime as _dt
    try:
        from app.services.wolf_low_position_line import _load_candidates, enabled
        today = _dt.datetime.now().strftime("%Y%m%d")
        c = _load_candidates(today) if enabled() else {"status": "disabled", "stocks": []}
        # 最后一个有效样本：PostgreSQL（不再读 eval_v3_fixed.json）
        last_date, last_ret = "-", None
        try:
            from app.services.low_position_store import last_valid
            lv = last_valid()
            last_date = str(lv.get("trade_date") or "-")
            last_ret = lv.get("ret")
        except Exception:
            pass
        return {
            "status": c.get("status"),
            "candidates": len(c.get("stocks") or []),
            "signal_date": c.get("signal_date"),
            "mainline": c.get("mainline"),
            "mainline_r20": c.get("mainline_r20"),
            "mainline_sideways": c.get("mainline_sideways"),
            "reasons": c.get("reasons") or [],
            "last_valid_date": last_date,
            "last_return_pct": round(last_ret, 2) if last_ret is not None else None,
            "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        return {"status": "error", "candidates": 0, "reasons": [str(e)[:120]],
                "last_valid_date": "-", "last_return_pct": None}
