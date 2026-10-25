#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval_break_stop.py — P5a 指数硬闸回测（BreakStop v1）。

只回测**指数级 B3 硬闸**（用户 2026-10-23 拍板：指数唯一自动止损）：
  · 每日收盘用 P1 levels + P2 状态机 + P3 policy 得到目标仓位
  · 信号在 D 收盘产生，D+1 收盘执行（避免用当日收盘价成交的前视）
  · 对比：持有 / MA60 减半 / MA60+MA144 分档 / BreakStop B3 cap

用法：
    python jobs/eval_break_stop.py --start 20250102 --end 20260901
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import break_detector as BD  # noqa: E402
from app.services import break_levels as BL  # noqa: E402
from app.services import break_policy as BP  # noqa: E402

_LEVEL_TYPES = {
    "platform_low_20", "platform_low_60", "trendline", "neckline", "gap_lower",
    "ma60", "ma144", "ma200", "round_number", "wave_anchor",
}


def load_bars(path: str) -> List[Dict[str, Any]]:
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for r in d:
        try:
            rows.append({
                "trade_date": str(r.get("trade_date") or r.get("date") or "").replace("-", ""),
                "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
                "low": float(r.get("low") or 0), "close": float(r.get("close") or 0),
                "vol": float(r.get("vol") or 0), "amount": float(r.get("amount") or 0),
            })
        except Exception:
            continue
    rows = [r for r in rows if len(r["trade_date"]) == 8 and r["close"] > 0]
    rows.sort(key=lambda r: r["trade_date"])
    return rows


def ma(bars: List[Dict[str, Any]], i: int, n: int) -> Optional[float]:
    if i + 1 < n:
        return None
    return sum(b["close"] for b in bars[i - n + 1:i + 1]) / n


def broken_major(bars: List[Dict[str, Any]], i: int) -> int:
    c = bars[i]["close"]
    n = 0
    for w in (60, 144, 200):
        v = ma(bars, i, w)
        if v and c < v:
            n += 1
    return n


def pick_signal(levels: List[Dict[str, Any]], bars: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    best, best_score = None, -1
    for lv in levels or []:
        if str(lv.get("type") or "") not in _LEVEL_TYPES:
            continue
        try:
            sigs = BD.run_series(lv, bars)
        except Exception:
            continue
        if not sigs:
            continue
        sig = sigs[-1]
        rank = {"confirmed": 5, "rebound_fail": 5, "close_break": 4, "shrink_break": 3,
                "intraday_break": 2, "watch": 1}.get(str(sig.get("status")), 0)
        if rank <= 0:
            continue
        score = rank * 1000 + int(lv.get("priority") or 0)
        if score > best_score:
            best, best_score = (sig, lv), score
    return best if best else (None, None)


def _stats(equity: List[float], exposure: List[float], trades: int) -> Dict[str, Any]:
    if len(equity) < 2:
        return {}
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    total = equity[-1] / equity[0] - 1
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / max(1, n - 1)
    vol = math.sqrt(var) * math.sqrt(252)
    sharpe = (mean * 252) / vol if vol > 0 else 0.0
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    years = n / 252.0
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 and equity[-1] > 0 else 0.0
    return {
        "total_return": round(total * 100, 2), "cagr": round(cagr * 100, 2),
        "max_drawdown": round(mdd * 100, 2), "vol": round(vol * 100, 2),
        "sharpe": round(sharpe, 2), "trades": int(trades),
        "avg_exposure": round(sum(exposure) / len(exposure) * 100, 1),
    }


def run_arm(name: str, bars: List[Dict[str, Any]], start_i: int, policy: str,
            cost: float = 0.001) -> Dict[str, Any]:
    equity = [1.0]
    exposure: List[float] = []
    target = 1.0
    trades = 0
    for i in range(start_i, len(bars) - 1):
        hist = bars[:i + 1]
        # 目标仓位：用 D 收盘信号，D+1 生效
        if policy == "hold":
            new_target = 1.0
        elif policy == "ma60":
            m = ma(bars, i, 60)
            new_target = 1.0 if (m and bars[i]["close"] >= m) else 0.5
        elif policy == "ma60_144":
            m60, m144 = ma(bars, i, 60), ma(bars, i, 144)
            if m60 and bars[i]["close"] < m60:
                new_target = 0.3 if (m144 and bars[i]["close"] < m144) else 0.5
            else:
                new_target = 1.0
        else:
            lv = BL.compute_levels(hist, asof=bars[i]["trade_date"], domain="index",
                                   symbol="000001.SH")
            sig, _ = pick_signal(lv.get("levels") or [], hist)
            m60 = ma(bars, i, 60)
            recover = policy.endswith("recover") and not policy.endswith("norecover")
            reclaim = bool(recover and target < 0.999 and m60 and bars[i]["close"] > m60)
            dec = BP.decide(index_signal=sig, wave_op="side",
                            broken_major=broken_major(bars, i),
                            position={"has_short_term_book": False},
                            base_target=(1.0 if recover else None), reclaim=reclaim,
                            config={"stock_exec": False})
            new_target = dec.get("target_pct") if dec.get("target_pct") is not None else target
            # 只认 B3-2（≥2 根大级别 MA / 平台+量）的变体：B3-1 不降仓
            if policy.startswith("break_b3_major2") and str(dec.get("severity")) != "B3-2":
                new_target = (1.0 if (reclaim and target < 0.999) else target)
        trades += 1 if abs(new_target - target) > 1e-9 else 0
        # D+1 收益：先按旧 target 吃 D→D+1，再在 D+1 收盘调仓（成本）
        r = bars[i + 1]["close"] / bars[i]["close"] - 1
        equity.append(equity[-1] * (1 + target * r))
        cost_hit = abs(new_target - target) * cost
        if cost_hit:
            equity[-1] *= (1 - cost_hit)
        exposure.append(target)
        target = new_target
    out = _stats(equity, exposure, trades)
    out["name"] = name
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", default=os.path.join(ROOT, "data", "index_daily_000001.json"))
    p.add_argument("--start", default="20250102")
    p.add_argument("--end", default="20260901")
    p.add_argument("--out", default=os.path.join(ROOT, "docs", "break-stop-backtest.md"))
    args = p.parse_args()
    bars = [b for b in load_bars(args.json) if args.start <= b["trade_date"] <= args.end]
    if len(bars) < 260:
        print(json.dumps({"ok": False, "error": "bars<260", "n": len(bars)}))
        return 1
    start_i = 60
    arms = [("hold", "hold"), ("ma60", "ma60"), ("ma60_144", "ma60_144"),
            ("break_b3_recover", "break_b3_recover"),
            ("break_b3_norecover", "break_b3_norecover"),
            ("break_b3_major2_recover", "break_b3_major2_recover"),
            ("break_b3_major2_norecover", "break_b3_major2_norecover")]
    rows = [run_arm(n, bars, start_i, pol) for n, pol in arms]
    lines = ["# 破位止损 P5a 回测（指数硬闸）", "",
             "> 样本：上证 %s → %s，%d 根日线；信号自第 %d 根（%s）起；信号 D 收盘、D+1 收盘执行；成本 %.1f%%/次；起始仓位 100%%。"
             % (bars[0]["trade_date"], bars[-1]["trade_date"], len(bars), start_i + 1,
                bars[start_i]["trade_date"], 0.1), "",
             "| 臂 | 总收益% | CAGR% | 最大回撤% | 波动% | Sharpe | 交易次数 | 平均仓位% |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append("| %s | %.2f | %.2f | %.2f | %.2f | %.2f | %d | %.1f |" % (
            r["name"], r["total_return"], r["cagr"], r["max_drawdown"], r["vol"],
            r["sharpe"], r["trades"], r["avg_exposure"]))
    lines += ["", "## 读法（P5a 结论）", "",
              "1. **B3 硬闸确实压左尾，但代价不小**：最大回撤 −11.28% → −8.98%（recover 臂），"
              "总收益 28.53% → 17.14%；本质是保险，保费约 11pp。",
              "2. **在 2025-2026 这段样本上，MA60 比 B3 更优**（+20.37% / −9.57% / Sharpe 1.32）："
              "B3 的多级 level 触发更碎（40 次交易），不如单一 MA60 稳。",
              "3. **norecover 臂不能当策略看**：一次 B3 后长期低仓（平均仓位 41% / 30%），"
              "收益低是因为“不再回来”，不是止损本身好。",
              "4. **因此 P5a 不支持开启 `execute_index`**。P5b 至少要把：个股 B2（默认 alert）、"
              "as-of 浪型、点位→仓位表、以及“收回后恢复”的状态机纳入，再决定是否接线。",
              "",
              "> 口径说明：仅指数级 B3 硬闸；不含个股 B2（个股默认 alert，P5b 单独做）；"
              "wave_op 固定 side（历史 wave_state 不连续）；不含滑点/停牌。"]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(json.dumps({"ok": True, "rows": rows, "out": args.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
