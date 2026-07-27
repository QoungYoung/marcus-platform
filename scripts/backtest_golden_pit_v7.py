# -*- coding: utf-8 -*-
"""黄金坑回测 v7 — 完整入场→动态退出循环。

与 v6 的关键区别:
  v6: 固定持有 N 天后卖出 (5/10/15/20/30天)
  v7: 动态退出 — 贪婪值回升到 P30 卖一半, P50 全清, 拐点后连续回落止盈

模拟逻辑:
  1. 每个交易日扫描所有指数，用 expanding-window P10 检测入场信号
  2. 入场后跟踪趋势: 连续 N 天回升 → 拐点确认
  3. 拐点后检测退出信号: P30 → 半仓, P50 → 清仓, 2天回落 → 止盈
  4. 每个指数独立决策，使用分指数参数
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Tuple, Optional

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ARKVOL_BASE_URL = "https://arkvol.com"
API_PATH = "/api/data/alla?view=full"

# Per-index parameters (matching golden_pit_service.py CHINA_INDICES)
INDEX_PARAMS = {
    "588000": {"name": "科创50",  "entry_pct": 15, "pit_pct": 8, "turning_days": 1},
    "510500": {"name": "中证500", "entry_pct": 12, "pit_pct": 6, "turning_days": 1},
    "159845": {"name": "中证1000","entry_pct": 10, "pit_pct": 5, "turning_days": 2},
    "159915": {"name": "创业板指","entry_pct": 10, "pit_pct": 5, "turning_days": 2},
    "510300": {"name": "沪深300", "entry_pct": 5,  "pit_pct": 3, "turning_days": 2},
    "510050": {"name": "上证50",  "entry_pct": 5,  "pit_pct": 3, "turning_days": 2},
}

MIN_HISTORY = 120  # 至少 120 天历史（比 v6 多，因为需要跟踪趋势和退出）


def read_api_key() -> str:
    env_key = os.environ.get("ARKVOL_API_KEY", "").strip()
    if env_key:
        return env_key
    config_path = Path.home() / ".arkvol" / "arkvol-entry.json"
    if config_path.is_file():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        key = payload.get("api_key", "")
        if key.strip():
            return key.strip()
    raise RuntimeError("未配置 ARKVOL_API_KEY")


def fetch_alla(api_key: str) -> Dict[str, Any]:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    url = f"{ARKVOL_BASE_URL}{API_PATH}"
    req = Request(url, headers={
        "X-API-Key": api_key,
        "X-Arkvol-Skill-Version": "0.3.1",
        "Accept": "application/json",
    }, method="GET")
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("code") == 0:
                return payload.get("data", {})
            raise RuntimeError(f"API error: {payload.get('msg')}")
        except (HTTPError, URLError) as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise RuntimeError(f"请求失败: {e}")


def expanding_percentile(value: float, history: List[float]) -> float:
    if not history:
        return 50.0
    return sum(1 for h in history if h <= value) / len(history) * 100


def detect_trend(greeds: List[float], turning_days: int) -> Dict[str, Any]:
    """检测趋势方向 (移植自 golden_pit_service._detect_trend)。"""
    if len(greeds) < 5:
        return {"trend": "declining", "days_rising": 0, "turning_confirmed": False}

    days_rising = 0
    for i in range(len(greeds) - 1, 0, -1):
        if greeds[i] > greeds[i - 1]:
            days_rising += 1
        else:
            break

    if days_rising >= turning_days:
        return {"trend": "recovering", "days_rising": days_rising, "turning_confirmed": True}
    elif days_rising == turning_days - 1 and turning_days >= 2:
        return {"trend": "bottoming", "days_rising": days_rising, "turning_confirmed": False}
    elif days_rising >= 1 and turning_days == 1:
        return {"trend": "recovering", "days_rising": days_rising, "turning_confirmed": True}
    else:
        return {"trend": "declining", "days_rising": 0, "turning_confirmed": False}


def detect_exit_signal(
    greeds: List[float],
    percentile: float,
    turning_confirmed: bool,
) -> Tuple[Optional[str], str]:
    """检测退出信号 (移植自 golden_pit_service._detect_exit_signal)。"""
    if not turning_confirmed:
        return None, ""

    if percentile >= 50:
        return "full_exit", f"P{percentile:.0f} 清仓"

    if percentile >= 30:
        return "half_exit", f"P{percentile:.0f} 半仓"

    days_declining = 0
    for i in range(len(greeds) - 1, 0, -1):
        if greeds[i] < greeds[i - 1]:
            days_declining += 1
        else:
            break
    if days_declining >= 2:
        max_greed = max(greeds[-10:]) if len(greeds) >= 10 else max(greeds)
        all_vals = sorted(greeds)
        max_pct = sum(1 for g in all_vals if g <= max_greed) / len(all_vals) * 100
        if max_pct >= 30:
            return "stop_profit", f"回落止盈 (曾P{max_pct:.0f})"

    return None, ""


def run():
    api_key = read_api_key()
    alla_data = fetch_alla(api_key)
    as_of = alla_data.get("as_of", "")
    series_data = alla_data.get("original_page_data", {}).get("series", {}).get("data", {})

    print(f"{'=' * 130}")
    print(f"  黄金坑回测 v7 — 完整入场→动态退出循环")
    print(f"  信号: expanding-window 分指数阈值, 趋势跟踪, P30半仓/P50清仓/回落止盈")
    print(f"{'=' * 130}")
    print(f"\n  数据日期: {as_of}")

    all_trades = []
    all_exit_events = []

    for code, params in INDEX_PARAMS.items():
        raw = series_data.get(code, [])
        if not raw:
            continue

        name = params["name"]
        entry_pct = params["entry_pct"]
        turning_days = params["turning_days"]

        series = sorted(raw, key=lambda x: x.get("date", ""))
        dates = [s.get("date", "") for s in series]
        greeds = [float(s.get("greed", 0)) for s in series]
        closes = [float(s.get("close", 0)) for s in series]

        if len(series) < MIN_HISTORY + 30:
            continue

        # ── 模拟逐日扫描 ──
        trades = []       # 当前持仓
        completed = []    # 已平仓交易
        position = 0.0    # 0=空仓, 1=满仓, 0.5=半仓
        entry_price = 0.0

        for i in range(MIN_HISTORY, len(series)):
            current_greed = greeds[i]
            current_close = closes[i]
            current_date = dates[i]
            history_greeds = greeds[:i]
            pct = expanding_percentile(current_greed, history_greeds)

            # 趋势检测
            trend_window = greeds[max(0, i-60):i+1]
            trend_info = detect_trend(trend_window, turning_days)

            if position == 0:
                # ── 空仓: 检测入场信号 ──
                if pct <= entry_pct:
                    signal_pct = pct
                    position = 1.0
                    entry_price = current_close
                    trades.append({
                        "index": name,
                        "code": code,
                        "entry_date": current_date,
                        "entry_price": entry_price,
                        "entry_greed": current_greed,
                        "entry_pct": pct,
                        "position": 1.0,
                        "half_exit_date": None,
                        "half_exit_price": None,
                        "full_exit_date": None,
                        "full_exit_price": None,
                        "exit_signal": None,
                    })

            elif position > 0:
                # ── 持仓: 检测退出信号 ──
                exit_signal, exit_reason = detect_exit_signal(
                    trend_window, pct, trend_info["turning_confirmed"]
                )

                if exit_signal == "half_exit" and position >= 1.0:
                    # 卖一半
                    t = trades[-1]
                    t["half_exit_date"] = current_date
                    t["half_exit_price"] = current_close
                    t["half_exit_pct"] = pct
                    t["half_ret"] = round((current_close - entry_price) / entry_price * 100, 2)
                    position = 0.5

                elif exit_signal == "full_exit" and position >= 0.5:
                    # 全清
                    t = trades[-1]
                    t["full_exit_date"] = current_date
                    t["full_exit_price"] = current_close
                    t["full_exit_pct"] = pct
                    t["exit_signal"] = "full_exit"

                    # 计算总收益 (半仓部分 + 剩余部分)
                    half_ret = t.get("half_ret") or 0
                    if position == 1.0:
                        # 未半仓直接清仓
                        t["total_ret"] = round((current_close - entry_price) / entry_price * 100, 2)
                    else:
                        # 一半在半仓退出，一半在清仓退出
                        t["total_ret"] = round(half_ret * 0.5 + (current_close - entry_price) / entry_price * 100 * 0.5, 2)

                    t["holding_days"] = i - series.index(
                        next(s for s in series if s.get("date", "") == t["entry_date"])
                    )
                    completed.append(t)
                    trades.pop()
                    position = 0

                elif exit_signal == "stop_profit" and position > 0:
                    # 止盈全清
                    t = trades[-1]
                    t["full_exit_date"] = current_date
                    t["full_exit_price"] = current_close
                    t["full_exit_pct"] = pct
                    t["exit_signal"] = "stop_profit"

                    half_ret = t.get("half_ret") or 0
                    if position == 1.0:
                        t["total_ret"] = round((current_close - entry_price) / entry_price * 100, 2)
                    else:
                        t["total_ret"] = round(half_ret * 0.5 + (current_close - entry_price) / entry_price * 100 * 0.5, 2)

                    t["holding_days"] = i - series.index(
                        next(s for s in series if s.get("date", "") == t["entry_date"])
                    )
                    completed.append(t)
                    trades.pop()
                    position = 0

        # 未平仓交易不算入统计

        if not completed:
            continue

        # ── 打印结果 ──
        print(f"\n{'─' * 130}")
        print(f"  {name} ({code}) | 入场阈值: P{entry_pct} | 拐点确认: {turning_days}天")
        print(f"  数据: {dates[0]} ~ {dates[-1]} ({len(series)}天) | 已完成交易: {len(completed)}笔")

        # 按 exit_signal 分组统计
        by_signal = defaultdict(list)
        for t in completed:
            by_signal[t.get("exit_signal", "none")].append(t)

        hdr = f"\n  {'入场日':<12s} {'入场价':>8s} {'P%':>5s} {'半仓日':<12s} {'半仓P%':>6s} {'清仓日':<12s} {'清仓P%':>6s} {'信号':>12s} {'持有天':>6s} {'收益':>8s}"
        print(hdr)
        print(f"  {'─' * 12} {'─' * 8} {'─' * 5} {'─' * 12} {'─' * 6} {'─' * 12} {'─' * 6} {'─' * 12} {'─' * 6} {'─' * 8}")

        for t in completed:
            he_pct = t.get('half_exit_pct')
            fe_pct = t.get('full_exit_pct')
            hd = t.get('holding_days')
            row = (
                f"  {t['entry_date']:<12s} {t['entry_price']:>8.4f} {t['entry_pct']:>4.0f}% "
                f"{t.get('half_exit_date') or '':<12s} "
                f"{f'{he_pct:>5.0f}%' if he_pct is not None else '':>6s} "
                f"{t.get('full_exit_date') or '':<12s} "
                f"{f'{fe_pct:>5.0f}%' if fe_pct is not None else '':>6s} "
                f"{t.get('exit_signal') or '':>12s} "
                f"{f'{hd:>5d}d' if hd is not None else '':>6s} "
                f"{t['total_ret']:>+7.2f}%"
            )
            print(row)

        # 统计
        rets = [t["total_ret"] for t in completed]
        srt = sorted(rets)
        print(f"\n  总收益: Avg={mean(rets):+.1f}%  Med={median(rets):+.1f}%  "
              f"Min={srt[0]:+.1f}%  Max={srt[-1]:+.1f}%  "
              f"Win={sum(1 for r in rets if r>0)}/{len(rets)} "
              f"({sum(1 for r in rets if r>0)/len(rets)*100:.0f}%)")

        # 按退出信号分别统计
        for sig, sig_trades in sorted(by_signal.items()):
            sig_rets = [t["total_ret"] for t in sig_trades]
            if sig_rets:
                print(f"    [{sig}]: {len(sig_rets)}笔, Avg={mean(sig_rets):+.1f}%, Win={sum(1 for r in sig_rets if r>0)}/{len(sig_rets)}")

        all_trades.extend(completed)

    # ── 跨指数汇总 ──
    if not all_trades:
        print("\n无数据")
        return

    print(f"\n\n{'=' * 130}")
    print(f"  跨指数汇总 — 动态退出")
    print(f"{'=' * 130}")

    for code, params in INDEX_PARAMS.items():
        name = params["name"]
        idx_trades = [t for t in all_trades if t["code"] == code]
        if not idx_trades:
            continue
        rets = [t["total_ret"] for t in idx_trades]
        holdings = [t.get("holding_days", 0) for t in idx_trades if t.get("holding_days")]
        print(f"  {name:<8s} {len(rets):>3d}笔  "
              f"Avg={mean(rets):>+6.1f}%  Med={median(rets):>+6.1f}%  "
              f"Min={min(rets):>+6.1f}%  Max={max(rets):>+6.1f}%  "
              f"Win={sum(1 for r in rets if r>0)}/{len(rets)} "
              f"({sum(1 for r in rets if r>0)/len(rets)*100:.0f}%)  "
              f"持{mean(holdings):.0f}天" if holdings else "")

    # 与 v6 固定持有对比
    print(f"\n\n  ── 对比 v6 固定持有 20 天 ──")
    for code, params in INDEX_PARAMS.items():
        name = params["name"]
        raw = series_data.get(code, [])
        if not raw:
            continue
        series_list = sorted(raw, key=lambda x: x.get("date", ""))
        closes_list = [float(s.get("close", 0)) for s in series_list]
        greeds_list = [float(s.get("greed", 0)) for s in series_list]

        # v7 动态退出
        v7_trades = [t for t in all_trades if t["code"] == code]
        v7_rets = [t["total_ret"] for t in v7_trades] if v7_trades else []

        # v6 固定持有 20 天 (P10 入场)
        v6_rets = []
        for i in range(MIN_HISTORY, len(series_list) - 20):
            pct = expanding_percentile(greeds_list[i], greeds_list[:i])
            if pct <= params["entry_pct"]:
                ret = (closes_list[i + 20] - closes_list[i]) / closes_list[i] * 100
                v6_rets.append(ret)

        if v7_rets and v6_rets:
            print(f"  {name:<8s} v7动态: {mean(v7_rets):>+6.1f}% ({len(v7_rets)}笔)  |  "
                  f"v6固定20d: {mean(v6_rets):>+6.1f}% ({len(v6_rets)}笔)  |  "
                  f"差值: {mean(v7_rets)-mean(v6_rets):>+5.1f}%")


if __name__ == "__main__":
    run()
