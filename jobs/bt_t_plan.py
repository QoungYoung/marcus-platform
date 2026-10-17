# -*- coding: utf-8 -*-
"""bt_t_plan.py — **盘前 T 计划**（用户 2026-10-18 方案 ✓）

用户的原始要求 ✓：
  「不是上涨就不能做T，也不是必须卖出去买回来才叫T，有**正T和倒T**，
    只要**感觉振幅比较大**就能T。**每天盘前先让 agent 去挂当天的 T出/T入价格**，
    就判断我们的持仓，**还能并发**，给 agent 足够的历史信息和数据。」

本脚本 ✓（盘前跑一次，逐票并发 ✓）：
  1. 读持仓（as-of 沙箱账本 / PG 二选一 ✓）
  2. 每票算特征 ✓：近 10 日平均振幅、ATR%、近 5 日高低、昨收、成本、持仓量
  3. **数值带**（不依赖 LLM ✓，永远有值 ✓）：
       k = clamp(近 10 日平均振幅 × 0.5, WOLF_TP_MIN_PCT=1.0, WOLF_TP_MAX_PCT=3.5)
       T入价 = min(昨收 × (1 − k), 近 5 日低 × 1.002)
       T出价 = max(昨收 × (1 + k), 成本 × (1 + 0.01))
  4. （可选 ✓）**agent 精修**：把上面这些特征喂给 LLM，让它返回 JSON 的
       {"sell": 价, "buy": 价, "why": "…"} ⇒ 若解析失败**保留数值带** ✓（绝不空手 ✓）
  5. 落盘 ✓：`<sandbox>/t_plan_<YYYYMMDD>.json`（监控端读它 ✓）

开关 ✓：
  · `WOLF_T_PLAN`            （1 = 启用，默认 0 ✓）
  · `WOLF_T_PLAN_AGENT`      （1 = 让 agent 精修，默认 0 ✓ 先只跑数值 ✓）
  · `WOLF_TP_MIN_PCT/MAX_PCT`（k 的夹逼 ✓ 1.0 / 3.5）
  · `WOLF_T_PLAN_URL`        （LLM 端点，默认 http://127.0.0.1:3001/chat ✓）
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional


def _pct(name: str, dflt: float) -> float:
    try:
        return float(os.getenv(name, str(dflt)) or dflt)
    except Exception:
        return dflt


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _bars(symbol: str, day: str, n: int = 15) -> List[Dict[str, Any]]:
    """取近 n 日日线（先用 as-of 沙箱 bars，再退 PG ✓）"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))
        import bt_asof_store as _store  # type: ignore
        rows = _store.daily(symbol, day, n) or []
        if rows:
            return rows
    except Exception:
        pass
    try:
        import sqlite3
        db = os.path.join(os.path.dirname(__file__), "..", "data", "_bt_full", "bars.sqlite")
        c = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db), uri=True)
        # ⚠️ `bars` 表的代码列是 **`ts_code`**（形如 `605589.SH` ✓），不是 `symbol` ✗
        #   （实测列：ts_code/trade_date/open/high/low/close/pre_close/pct_chg/vol/amount/…）
        _ts = symbol
        try:
            _d = "".join(ch for ch in symbol if ch.isdigit())[:6]
            _ex = "SH" if symbol.upper().startswith("SH") else ("SZ" if symbol.upper().startswith("SZ") else "")
            if _d and _ex:
                _ts = "%s.%s" % (_d, _ex)
        except Exception:
            _ts = symbol
        cur = c.execute("SELECT trade_date, open, high, low, close, vol FROM bars "
                        "WHERE ts_code=? AND trade_date < ? ORDER BY trade_date DESC LIMIT ?",
                        (_ts, str(day), int(n)))
        out = [dict(date=r[0], open=r[1], high=r[2], low=r[3], close=r[4], vol=r[5]) for r in cur.fetchall()]
        c.close()
        return list(reversed(out))
    except Exception:
        return []


def _features(symbol: str, day: str, pos: int, cost: float) -> Optional[Dict[str, Any]]:
    bs = _bars(symbol, day, 15)
    if len(bs) < 6:
        return None
    closes = [float(b.get("close") or 0) for b in bs if b.get("close")]
    highs = [float(b.get("high") or 0) for b in bs if b.get("high")]
    lows = [float(b.get("low") or 0) for b in bs if b.get("low")]
    if not closes or not highs or not lows:
        return None
    amps = []
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        if pc > 0:
            amps.append((highs[i] - lows[i]) / pc * 100.0)
    amp10 = statistics.mean(amps[-10:]) if amps else 0.0
    prev = closes[-1]
    lo5 = min(lows[-5:]) if len(lows) >= 5 else min(lows)
    hi5 = max(highs[-5:]) if len(highs) >= 5 else max(highs)
    atr = statistics.mean([(highs[i] - lows[i]) for i in range(len(highs))][-10:]) if highs else 0.0
    # ★ 结构位 ✓（用户：他按**图形**评估 T 点 ✓ ⇒ agent 必须看到压力/支撑 ✓）
    def _ma(k: int) -> float:
        xs = closes[-k:]
        return round(sum(xs) / len(xs), 3) if xs else 0.0
    hi20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    lo20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    gap = round(float(bs[-1].get("open") or 0) - prev, 3) if bs else 0.0
    return {"symbol": symbol, "day": str(day), "position": int(pos), "cost": float(cost),
            "prev_close": round(prev, 3), "low5": round(lo5, 3), "high5": round(hi5, 3),
            "amp10_pct": round(amp10, 2), "atr": round(atr, 3),
            "amp_atr_pct": round(atr / prev * 100.0, 2) if prev else 0.0,
            "high20": round(hi20, 3), "low20": round(lo20, 3),
            "ma5": _ma(5), "ma10": _ma(10), "ma20": _ma(20),
            "last_open_gap": gap,
            "recent": [round(x, 2) for x in closes[-6:]]}


def numeric_band(f: Dict[str, Any]) -> Dict[str, Any]:
    # ★ 2026-10-18 ✓（圣泉集团 SH605589 / 20260227 实证 ✗）：
    #   旧公式 `buy=min(昨收×(1−k), 低5×1.002)` / `sell=max(昨收×(1+k), 成本×1.01)` 太宽 ✗
    #     ⇒ 0227 算出 T入 31.373 / T出 35.435 ✗，而当天区间只有 **33.35~34.25** ✗ ⇒ 永不触发 ✗
    #   修法 ✓：**以昨收为中心对称** ✓（用户：「只要**振幅**比较大就能T」✓）
    #     k = clamp(近10日平均振幅 × 0.45, MIN=0.8, MAX=2.5)  ⇒ T入 = 昨收×(1−k) ✓ T出 = 昨收×(1+k) ✓
    #     成本只是"参考"✗，**不再参与定价**（否则盈利票的 T 出价被抬到天上 ✗）
    kmin, kmax = _pct("WOLF_TP_MIN_PCT", 0.8), _pct("WOLF_TP_MAX_PCT", 2.5)
    k = _clamp(float(f.get("amp10_pct") or 0) * 0.45, kmin, kmax)
    prev = float(f.get("prev_close") or 0)
    if prev <= 0:
        return {"sell": 0.0, "buy": 0.0, "k_pct": round(k, 2), "src": "numeric(无昨收)"}
    buy = round(prev * (1 - k / 100.0), 3)
    sell = round(prev * (1 + k / 100.0), 3)
    return {"sell": sell, "buy": buy, "k_pct": round(k, 2), "src": "numeric"}


_LLM_STATE = {"fails": 0, "day": ""}


def _llm_breaker_ok(day: str) -> bool:
    """★ 2026-10-18 ✓（用户问「这个超时是什么」）：
    本地 LLM（`127.0.0.1:3001`，Qwen 27B）忙时会 >25 秒不回 ✗
      ⇒ 每次白等 ✗（实测 `bt_t_plan.py:136 timed out` ✓）
    熔断 ✓：**当天**连续失败达 `WOLF_T_PLAN_FAIL_MAX`(默认 3 ✓) ⇒ 当天**不再调 agent** ✓
      直接走数值带 ✓（正确性不变 ✓，只是不再浪费时间 ✗）
    """
    if str(_LLM_STATE.get("day") or "") != str(day):
        _LLM_STATE["day"] = str(day)
        _LLM_STATE["fails"] = 0
    try:
        mx = int(os.getenv("WOLF_T_PLAN_FAIL_MAX", "3") or 3)
    except Exception:
        mx = 3
    return int(_LLM_STATE.get("fails") or 0) < mx


def _llm_note(day: str, ok: bool) -> None:
    if str(_LLM_STATE.get("day") or "") != str(day):
        _LLM_STATE["day"] = str(day)
        _LLM_STATE["fails"] = 0
    if ok:
        _LLM_STATE["fails"] = 0
    else:
        _LLM_STATE["fails"] = int(_LLM_STATE.get("fails") or 0) + 1
        try:
            mx = int(os.getenv("WOLF_T_PLAN_FAIL_MAX", "3") or 3)
        except Exception:
            mx = 3
        if int(_LLM_STATE["fails"]) == mx:
            print("[t-plan] LLM 连续失败 %d 次 ⇒ **当天不再调用 agent** ✓（改用数值带 ✓）"
                  % mx, flush=True)


def agent_refine(f: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """让 agent 精修价格带 ✓（失败 ⇒ 原样返回数值带 ✓，绝不空手 ✗）"""
    if not _llm_breaker_ok(str(f.get("day") or "")):
        return dict(base, src="numeric(LLM 熔断: 当日失败过多)")
    url = os.getenv("WOLF_T_PLAN_URL", "http://127.0.0.1:3001/chat")
    prompt = (
        "你是A股做T操盘手。请只输出 JSON，不要解释。\n"
        "标的 %s ｜ 日期 %s ｜ 持仓 %d 股 ｜ 成本 %.3f\n"
        "昨收 %.3f ｜ 近10日平均振幅 %.2f%% ｜ ATR %.3f(%.2f%%) ｜ 近5日低 %.3f 高 %.3f\n"
        "结构位：20日高 %.3f 低 %.3f ｜ MA5 %.3f MA10 %.3f MA20 %.3f ｜ 最近6日收盘 %s\n"
        "参考数值带：T入价 %.3f ｜ T出价 %.3f\n"
        "要求：按当日**振幅**给出 T出价/T入价（正T：先在T入价买、再在T出价卖；倒T：反过来）。\n"
        "价格必须落在 [低5×0.98, 高5×1.02] 内，且 T出价 > T入价。\n"
        '输出格式：{"sell": 数字, "buy": 数字, "why": "不超过20字"}\n'
        % (f["symbol"], f["day"], f["position"], f["cost"], f["prev_close"], f["amp10_pct"],
           f["atr"], f["amp_atr_pct"], f["low5"], f["high5"],
           f.get("high20", 0), f.get("low20", 0), f.get("ma5", 0), f.get("ma10", 0), f.get("ma20", 0),
           f.get("recent"), base["buy"], base["sell"])
    )
    try:
        body = json.dumps({"message": prompt}).encode()   # 实测：端点只认 `message` ✓
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=float(os.getenv("WOLF_T_PLAN_TIMEOUT", "25") or 25)) as r:
            txt = r.read().decode("utf-8", "replace")
        # ⚠️ 端点返回的是 `{"reply": "<模型输出>", ...}` ✗ —— 直接 json.loads 外层会把
        #   `cfg.get("sell")` 取成 None ⇒ 恒判"越界被弃" ✗（实测 ✓）
        #   修法 ✓：先取 `reply`，再从 reply 里抠 JSON ✓
        try:
            _env = json.loads(txt)
            _inner = str(_env.get("reply") or _env.get("content") or _env.get("message") or txt)
        except Exception:
            _inner = txt
        i, j = _inner.find("{"), _inner.rfind("}")
        cfg = json.loads(_inner[i:j + 1])
        sell, buy = float(cfg.get("sell") or 0), float(cfg.get("buy") or 0)
        lo, hi = float(f["low5"]) * 0.98, float(f["high5"]) * 1.02
        if not (lo <= buy < sell <= hi):
            return dict(base, src="numeric(agent 越界被弃)")
        _llm_note(str(f.get("day") or ""), True)
        return {"sell": round(sell, 3), "buy": round(buy, 3), "k_pct": base["k_pct"],
                "src": "agent", "why": str(cfg.get("why") or "")[:40]}
    except Exception as e:
        _llm_note(str(f.get("day") or ""), False)
        return dict(base, src="numeric(agent 异常: %s)" % str(e)[:30])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--sandbox", default="")
    ap.add_argument("--account", default=os.getenv("T_MONITOR_ACCOUNT", "drabq1"))
    ap.add_argument("--held", default="", help="SYMBOL:POS:COST,SYMBOL:POS:COST,...")
    ap.add_argument("--workers", type=int, default=int(os.getenv("WOLF_T_PLAN_WORKERS", "6") or 6))
    a = ap.parse_args()

    if str(os.getenv("WOLF_T_PLAN", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        print("[t-plan] WOLF_T_PLAN=0 ⇒ 跳过")
        return 0

    holdings: List[tuple] = []
    for item in (a.held or "").split(","):
        p = item.strip().split(":")
        if len(p) >= 2 and p[0]:
            holdings.append((p[0], int(float(p[1] or 0)), float(p[2] or 0) if len(p) > 2 else 0.0))
    if not holdings:
        try:
            from sqlalchemy import text
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
            from app.database import SessionLocal
            with SessionLocal() as db:
                for sym, vol, avg in db.execute(text(
                        "SELECT symbol, SUM(CASE WHEN direction LIKE '买%' THEN volume ELSE -volume END), "
                        "AVG(price) FROM paper_trades WHERE account_id=:a GROUP BY symbol"),
                        {"a": a.account}).fetchall():
                    if int(vol or 0) > 0:
                        holdings.append((str(sym), int(vol), float(avg or 0)))
        except Exception as e:
            print("[t-plan] 读持仓失败: %s" % str(e)[:100])

    if not holdings:
        print("[t-plan] 无持仓 ⇒ 空计划")
    use_agent = str(os.getenv("WOLF_T_PLAN_AGENT", "0")).strip().lower() in ("1", "true", "yes", "on")

    def work(h):
        sym, pos, cost = h
        f = _features(sym, a.date, pos, cost)
        if not f:
            return None
        base = numeric_band(f)
        plan = agent_refine(f, base) if use_agent else base
        return {"symbol": sym, "position": pos, "cost": cost,
                "sell": plan["sell"], "buy": plan["buy"], "src": plan.get("src"),
                "why": plan.get("why", ""), **f}

    out: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:      # 并发 ✓（用户要求 ✓）
        for r in ex.map(work, holdings):
            if r:
                out.append(r)

    path = os.path.join(a.sandbox or os.getenv("DATA_DIR", "data"), "t_plan_%s.json" % a.date)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump({"date": a.date, "plan": out}, fp, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[t-plan] 写计划失败: %s" % str(e)[:100])
        return 1
    print("[t-plan] %s ⇒ %d 只（%s）→ %s" % (
        a.date, len(out), ",".join(sorted({str(x.get("src")) for x in out})) or "-", path))
    for x in out[:8]:
        print("   %-10s 入%.3f 出%.3f（振幅%.2f%% 成本%.3f %s）"
              % (x["symbol"], x["buy"], x["sell"], x["amp10_pct"], x["cost"], x.get("why") or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def agent_refresh(f: Dict[str, Any], base: Dict[str, Any], cur_px: float,
                  tried: int = 0) -> Dict[str, Any]:
    """**盘中改价** ✓（用户：他"缩转放那一瞬间"会改判 ✓，挂单价只是参考 ✓）

    触发时机（由监控端决定 ✓）：现价已贴近/突破原带 ✓，或每 N 次评估 ✓
    失败 ⇒ 原样返回旧带 ✓（绝不空手 ✗）
    """
    url = os.getenv("WOLF_T_PLAN_URL", "http://127.0.0.1:3001/chat")
    prompt = (
        "你是A股做T操盘手，现在**盘中**。只输出 JSON，不要解释。\n"
        "标的 %s ｜ %s ｜ 现价 %.3f ｜ 昨收 %.3f ｜ 成本 %.3f ｜ 持仓 %d\n"
        "近10日振幅 %.2f%% ｜ ATR%.2f%% ｜ 20日高 %.3f 低 %.3f ｜ MA5 %.3f MA10 %.3f MA20 %.3f\n"
        "最近6日收盘 %s ｜ 原计划带：T入 %.3f / T出 %.3f（已评估 %d 次）\n"
        "按**当下图形**判断：是继续等、还是把 T出/T入 改成更贴近现价的位置？\n"
        "约束：T出 > T入，且都落在 [20日低×0.97, 20日高×1.03] 内。\n"
        '输出格式：{"sell": 数字, "buy": 数字, "why": "不超过20字"}\n'
        % (f["symbol"], f["day"], cur_px, f["prev_close"], f["cost"], f["position"],
           f["amp10_pct"], f["amp_atr_pct"], f.get("high20", 0), f.get("low20", 0),
           f.get("ma5", 0), f.get("ma10", 0), f.get("ma20", 0), f.get("recent"),
           base["buy"], base["sell"], tried)
    )
    try:
        body = json.dumps({"message": prompt}).encode()   # 实测：端点只认 `message` ✓
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=float(os.getenv("WOLF_T_PLAN_TIMEOUT", "25") or 25)) as r:
            txt = r.read().decode("utf-8", "replace")
        # ⚠️ 端点返回的是 `{"reply": "<模型输出>", ...}` ✗ —— 直接 json.loads 外层会把
        #   `cfg.get("sell")` 取成 None ⇒ 恒判"越界被弃" ✗（实测 ✓）
        #   修法 ✓：先取 `reply`，再从 reply 里抠 JSON ✓
        try:
            _env = json.loads(txt)
            _inner = str(_env.get("reply") or _env.get("content") or _env.get("message") or txt)
        except Exception:
            _inner = txt
        i, j = _inner.find("{"), _inner.rfind("}")
        cfg = json.loads(_inner[i:j + 1])
        sell, buy = float(cfg.get("sell") or 0), float(cfg.get("buy") or 0)
        lo = float(f.get("low20") or f["low5"]) * 0.97
        hi = float(f.get("high20") or f["high5"]) * 1.03
        if not (lo <= buy < sell <= hi):
            return dict(base, src="numeric(改价越界被弃)")
        return {"sell": round(sell, 3), "buy": round(buy, 3), "k_pct": base.get("k_pct", 0),
                "src": "agent-refresh", "why": str(cfg.get("why") or "")[:40]}
    except Exception as e:
        return dict(base, src="numeric(改价异常: %s)" % str(e)[:24])
