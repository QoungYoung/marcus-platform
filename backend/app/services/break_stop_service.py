# -*- coding: utf-8 -*-
"""break_stop_service.py — 破位编排层（BreakStop v1 / P4）。

职责：
  1. 取事实：上证日线、浪型、持仓、指数大级别破位数
  2. 调 P1 levels → P2 detector → P3 policy
  3. 写 `break_events` / `break_actions`（幂等）
  4. 推 QQ（**不是 shadow**：alert 模式真实落库 + 真实推送）
  5. 返回结构化摘要，供 API/任务/回测复用

模式：
  · `off`            不运行
  · `alert`（默认）  真实计算 + 落库 + QQ；**不下单**（P5 回测前）
  · `execute_index`  预留；P5 回测通过 + 用户批准前**不执行**
  · `shadow` 不存在（按 2026-10-23 用户要求去掉）

设计约束：
  · 纯计算在 P1/P2/P3；本模块只做编排与 IO
  · 所有写库幂等；重复跑同一天不产生重复事件/动作
  · 数据缺失 fail-closed 对卖出：不猜、不卖、写 data_warning
  · QQ 只在 `state_key` 变化时推送（避免每天同一条）
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services import break_detector as BD
from app.services import break_levels as BL
from app.services import break_policy as BP

INDEX_SYMBOL = "000001.SH"
# 指数硬闸只认“大级别结构”（P3 B3 定义）：平台/趋势/颈线/缺口/MA60-144-200/整数关/外部锚点
_INDEX_LEVEL_TYPES = {
    "platform_low_20", "platform_low_60", "trendline", "neckline", "gap_lower",
    "ma60", "ma144", "ma200", "round_number", "wave_anchor",
}
# 个股/板块 B2 可以更细：MA5/13/34、前低、前高都算
_STOCK_LEVEL_TYPES = _INDEX_LEVEL_TYPES | {
    "ma5", "ma13", "ma34", "prior_day_low", "prior_day_high",
}


def enabled() -> bool:
    """总开关；默认关，开启后也只是 alert（不下单）。"""
    return os.getenv("WOLF_BREAK_STOP", "0").strip().lower() in ("1", "true", "yes", "on")


def mode() -> str:
    m = os.getenv("WOLF_BREAK_MODE", "alert").strip().lower()
    return m if m in ("off", "alert", "execute_index") else "alert"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_index_bars(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """优先 index_daily_000001.json（有 OHLC），退回 CSV（close-only）。"""
    root = _repo_root()
    p = Path(path) if path else Path(os.getenv(
        "WOLF_BREAK_INDEX_JSON", str(root / "data" / "index_daily_000001.json")))
    rows: List[Dict[str, Any]] = []
    if p.exists():
        try:
            d = json.load(open(p, encoding="utf-8"))
            for r in d:
                rows.append({
                    "trade_date": str(r.get("trade_date") or r.get("date") or "").replace("-", ""),
                    "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
                    "low": float(r.get("low") or 0), "close": float(r.get("close") or 0),
                    "vol": float(r.get("vol") or 0), "amount": float(r.get("amount") or 0),
                })
        except Exception:
            rows = []
    if not rows:
        csvp = Path(os.getenv("WOLF_INDEX_CSV",
                              str(root / "data" / "指数数据" / "index_daily" / "000001.SH.csv")))
        try:
            import csv as _csv
            for r in _csv.DictReader(open(csvp, encoding="utf-8")):
                try:
                    rows.append({
                        "trade_date": str(r["trade_date"]).replace("-", ""),
                        "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
                        "low": float(r.get("low") or 0), "close": float(r["close"]),
                        "vol": float(r.get("vol") or 0), "amount": float(r.get("amount") or 0),
                    })
                except Exception:
                    continue
        except Exception:
            rows = []
    rows = [r for r in rows if len(r["trade_date"]) == 8 and r["close"] > 0]
    rows.sort(key=lambda r: r["trade_date"])
    return rows


def _append_live_quote(bars: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
    """盘中把上证当前 quote 追加为当日 bar（仅 live 模式；失败不改变 bars）。"""
    try:
        from app.services.t_data_sources import fetch_quote_one
        q = fetch_quote_one("SH000001") or {}
        px = float(q.get("current") or q.get("close") or 0)
        if px <= 0:
            return bars, "live_quote_unavailable"
        today = datetime.now().strftime("%Y%m%d")
        if bars and bars[-1]["trade_date"] >= today:
            return bars, "live_quote_already_present"
        bar = {
            "trade_date": today,
            "open": float(q.get("open") or px), "high": float(q.get("high") or px),
            "low": float(q.get("low") or px), "close": px,
            "vol": float(q.get("vol") or 0), "amount": float(q.get("amount") or 0),
        }
        return bars + [bar], "live_quote_appended"
    except Exception as e:
        return bars, "live_quote_error:%s" % str(e)[:60]


def _load_wave(asof: Optional[str] = None) -> Dict[str, Any]:
    """优先按 asof 取历史档；取不到再退回当前档（PIT）。"""
    try:
        from app.services import wave_store
        if asof:
            w = wave_store.get_wave_asof(asof)
            if w:
                return w
        return wave_store.get_wave() or {}
    except Exception:
        return {}


def _broken_major(asof: str) -> int:
    try:
        from app.services.point_engine import index_breakdown
        j = index_breakdown(asof) or {}
        return len(j.get("below") or [])
    except Exception:
        return 0


def _target_override(close: Optional[float]) -> Optional[float]:
    try:
        from app.services import t_capacity
        return t_capacity.level_target_pct(close)
    except Exception:
        return None


def _load_positions() -> List[Dict[str, Any]]:
    """t/stock 两个账户的持仓 + 当前价/浮盈；取不到价就跳过该票。"""
    out: List[Dict[str, Any]] = []
    try:
        from app.database import SessionLocal
        from app.models.paper_trade import PaperPosition
        from app.services.t_data_sources import fetch_quote_one, _normalize_symbol
        with SessionLocal() as db:
            rows = (db.query(PaperPosition)
                    .filter(PaperPosition.account_id.in_(("t", "stock")))
                    .all())
        for r in rows:
            if not r.symbol or int(r.volume or 0) <= 0:
                continue
            sym = _normalize_symbol(r.symbol)
            q = fetch_quote_one(sym) or {}
            cur = float(q.get("current") or 0)
            cost = float(r.avg_price or 0)
            if cur <= 0 or cost <= 0:
                continue
            out.append({
                "symbol": sym, "account_id": r.account_id, "volume": int(r.volume or 0),
                "avg_price": cost, "current_price": cur,
                "pnl_pct": round((cur / cost - 1.0) * 100.0, 2),
                "pos_class": None, "has_short_term_book": False,
            })
    except Exception:
        return []
    return out


def _stock_bars(symbol: str) -> List[Dict[str, Any]]:
    try:
        from app.services.support_resistance import get_daily_bars
        return get_daily_bars(symbol, 70) or []
    except Exception:
        return []


def _pick_signal(levels: Sequence[Dict[str, Any]], bars: Sequence[Dict[str, Any]],
                 phase: str = "close",
                 types: Optional[set] = None) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """从 levels 中选“最该报”的信号：confirmed > close/shrink > intraday > watch。"""
    best = None
    best_rank = -1
    for lv in levels or []:
        if str(lv.get("type") or "") not in (types or _INDEX_LEVEL_TYPES):
            continue
        try:
            sigs = BD.run_series(lv, bars, phase=phase)
        except Exception:
            continue
        if not sigs:
            continue
        sig = sigs[-1]
        st = str(sig.get("status") or "none")
        rank = {"confirmed": 5, "rebound_fail": 5, "close_break": 4, "shrink_break": 3,
                "intraday_break": 2, "watch": 1}.get(st, 0)
        if rank <= 0:
            continue
        pri = int(lv.get("priority") or 0)
        score = rank * 1000 + pri
        if score > best_rank:
            best_rank = score
            best = (sig, lv)
    return best if best else (None, None)


def _event_row(sig: Dict[str, Any], lv: Dict[str, Any], domain: str,
               mode_: str, wave_op: str = "", pnl: Optional[float] = None,
               action: str = "", target_pct: Optional[float] = None,
               warning: str = "") -> Dict[str, Any]:
    return {
        "asof": sig.get("asof"), "symbol": sig.get("symbol") or lv.get("symbol"),
        "domain": domain, "level_type": sig.get("level_type") or lv.get("type"),
        "level_price": sig.get("level_price"), "phase": sig.get("phase"),
        "status": sig.get("status"), "close": sig.get("close"),
        "high": sig.get("high"), "low": sig.get("low"),
        "vol_ratio": sig.get("vol_ratio"), "volume_branch": sig.get("volume_branch"),
        "wave_op": wave_op, "position_pnl": pnl, "action": action,
        "target_pct": target_pct, "reason": sig.get("reason"),
        "mode": mode_, "data_warning": warning,
    }


def _action_row(dec: Dict[str, Any], asof: str, symbol: str, domain: str,
                level_type: Optional[str], mode_: str) -> Dict[str, Any]:
    return {
        "asof": asof, "symbol": symbol, "domain": domain,
        "severity": dec.get("severity"), "level_type": level_type,
        "action": dec.get("action"), "target_pct": dec.get("target_pct"),
        "target_is_cap": dec.get("target_is_cap", True),
        "block_new_buy": dec.get("block_new_buy", False),
        "allow_zero": dec.get("allow_zero", False),
        "reason": dec.get("reason"), "invalidation": dec.get("invalidation"),
        "status": "alerted" if mode_ == "alert" else "pending",
    }


def _format_card(asof: str, index_sig: Optional[Dict[str, Any]],
                 index_dec: Dict[str, Any], stocks: List[Dict[str, Any]],
                 warning: str = "") -> str:
    lines = ["【破位卡 %s】" % asof]
    if index_sig:
        lines.append("上证 %.2f | %s %s | 关键位 %s %.2f | 量比 %s" % (
            float(index_sig.get("close") or 0), index_sig.get("status"),
            index_sig.get("level_type") or "-", index_sig.get("level_type") or "-",
            float(index_sig.get("level_price") or 0), index_sig.get("vol_ratio")))
    else:
        lines.append("上证：无破位信号")
    cap = index_dec.get("target_pct")
    lines.append("动作 %s | %s | cap %s | 禁新开 %s" % (
        index_dec.get("severity"), index_dec.get("action"),
        ("%.0f%%" % (cap * 100)) if cap is not None else "-",
        "是" if index_dec.get("block_new_buy") else "否"))
    if stocks:
        lines.append("持仓提示 %d 条：" % len(stocks))
        for s in stocks[:5]:
            lines.append("· %s %s %s" % (s.get("symbol"), s.get("severity"),
                                         s.get("action")))
    if warning:
        lines.append("⚠ " + warning[:180])
    return "\n".join(lines)[:900]


class BreakStopService:
    def __init__(self, repo=None, qq=None, index_bars_provider=None,
                 positions_provider=None, wave_provider=None, stock_bars_provider=None,
                 state_path: Optional[str] = None):
        self.repo = repo
        self.qq = qq
        self.index_bars_provider = index_bars_provider
        self.positions_provider = positions_provider
        self.wave_provider = wave_provider
        self.stock_bars_provider = stock_bars_provider
        self.state_path = state_path or os.path.join(
            os.getenv("DATA_DIR", str(_repo_root() / "data")), "break_last_key.json")

    def _repo(self):
        if self.repo is not None:
            return self.repo
        from app.services.break_repository import BreakRepository
        return BreakRepository()

    def _qq(self, text: str) -> bool:
        if self.qq is not None:
            try:
                return bool(self.qq(text))
            except Exception:
                return False
        try:
            from app.services.alert_hub import push_qq
            return bool(push_qq(text))
        except Exception:
            return False

    def _should_push(self, key: str) -> bool:
        try:
            old = json.load(open(self.state_path, encoding="utf-8"))
        except Exception:
            old = {}
        if str(old.get("key") or "") == key:
            return False
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            json.dump({"key": key, "at": datetime.now().isoformat()},
                      open(self.state_path, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
        return True

    def run(self, mode_: Optional[str] = None, asof: Optional[str] = None,
            index_bars: Optional[Sequence[Dict[str, Any]]] = None,
            positions: Optional[Sequence[Dict[str, Any]]] = None,
            wave: Optional[Dict[str, Any]] = None,
            live_quote: bool = False, push_qq: bool = True,
            anchors: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
        m = (mode_ or mode()).lower()
        if m == "off":
            return {"ok": False, "skipped": "mode=off"}
        if m == "execute_index":
            return {"ok": False, "error": "execute_index disabled until P5"}
        bars = list(index_bars) if index_bars is not None else (
            self.index_bars_provider() if self.index_bars_provider else _load_index_bars())
        warning = ""
        if live_quote:
            bars, warning = _append_live_quote(bars)
        bars = [dict(b, trade_date=str(b.get("trade_date") or "").replace("-", "")) for b in bars]
        bars = [b for b in bars if b["trade_date"].isdigit()]
        bars.sort(key=lambda b: str(b["trade_date"]))
        if not bars:
            return {"ok": False, "error": "no_index_bars", "mode": m}
        day = str(asof or bars[-1]["trade_date"])
        bars = [b for b in bars if str(b["trade_date"]) <= day]
        if not bars:
            return {"ok": False, "error": "no_index_bars_before_asof", "asof": day}

        # 1) levels + signal
        anchors = list(anchors or [])
        w = dict(wave) if wave is not None else (
            self.wave_provider() if self.wave_provider else _load_wave(day))
        if not anchors and w.get("anchor_low"):
            anchors.append({"type": "wave_anchor", "price": w["anchor_low"],
                            "source": "wave_state"})
        res = BL.compute_levels(bars, asof=day, domain="index", symbol=INDEX_SYMBOL,
                                anchors=anchors)
        sig, lv = _pick_signal(res.get("levels") or [], bars, phase="close")
        if sig and warning:
            sig.setdefault("data_warning", warning)

        # 2) policy
        wave_op = str(w.get("operation") or w.get("wave_operation") or "side")
        wave_level = str(w.get("level") or "")
        has_short = any(bool(p.get("has_short_term_book")) for p in (positions or []))
        dec = BP.decide(index_signal=sig, wave_op=wave_op, wave_level=wave_level,
                        position={"has_short_term_book": has_short},
                        broken_major=_broken_major(day),
                        target_override=_target_override(res.get("close")),
                        config={"stock_exec": False})

        # 3) positions
        poss = list(positions) if positions is not None else (
            self.positions_provider() if self.positions_provider else _load_positions())
        stock_decs: List[Dict[str, Any]] = []
        repo = self._repo()
        n_ev = n_act = 0
        if sig:
            ev = _event_row(sig, lv or {}, "index", m, wave_op=wave_op,
                            action=dec.get("action"), target_pct=dec.get("target_pct"),
                            warning=warning)
            if repo.record_event(ev) is not None:
                n_ev += 1
        act = _action_row(dec, day, INDEX_SYMBOL, "index",
                          (sig or {}).get("level_type"), m)
        if repo.record_action(act) is not None:
            n_act += 1
        for p in poss:
            sym = str(p.get("symbol") or "")
            if not sym:
                continue
            sbars = (self.stock_bars_provider(sym) if self.stock_bars_provider
                     else _stock_bars(sym))
            if len(sbars) < 20:
                continue
            sres = BL.compute_levels(sbars, asof=day, domain="stock", symbol=sym)
            ssig, slv = _pick_signal(sres.get("levels") or [], sbars, phase="close",
                                     types=_STOCK_LEVEL_TYPES)
            if not ssig:
                continue
            sdec = BP.decide(stock_signal=ssig, wave_op=wave_op, wave_level=wave_level,
                             position={"pnl_pct": p.get("pnl_pct"),
                                       "pos_class": p.get("pos_class"),
                                       "has_short_term_book": p.get("has_short_term_book")},
                             config={"stock_exec": str(os.getenv(
                                 "WOLF_BREAK_STOCK_EXEC", "0")).lower() in
                                 ("1", "true", "yes", "on")})
            sdec["symbol"] = sym
            stock_decs.append(sdec)
            ev = _event_row(ssig, slv or {}, "stock", m, wave_op=wave_op,
                            pnl=p.get("pnl_pct"), action=sdec.get("action"),
                            target_pct=sdec.get("target_pct"))
            if repo.record_event(ev) is not None:
                n_ev += 1
            a = _action_row(sdec, day, sym, "stock", (ssig or {}).get("level_type"), m)
            if repo.record_action(a) is not None:
                n_act += 1

        # 4) QQ（state_key 变化才推）
        text = _format_card(day, sig, dec, stock_decs, warning)
        key = "%s|%s|%s|%s|%s" % (day, (sig or {}).get("level_type"),
                                  dec.get("severity"), dec.get("action"),
                                  dec.get("target_pct"))
        pushed = False
        should_alert = bool(stock_decs) or bool(
            sig and str(sig.get("status") or "") not in ("none", "watch"))
        if push_qq and should_alert:
            if self._should_push(key):
                pushed = self._qq(text)
        return {"ok": True, "asof": day, "mode": m, "index_signal": sig,
                "index_action": dec, "stock_actions": stock_decs,
                "events_written": n_ev, "actions_written": n_act,
                "qq": pushed, "card": text, "data_warning": warning}
