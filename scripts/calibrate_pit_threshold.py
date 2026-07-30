# -*- coding: utf-8 -*-
"""黄金坑阈值校准 v2 — 使用全量历史数据 (range=full)。

数据来源: https://arkvol.com/api/funds-greed/alla/series?range=full
返回每个指数的完整 greed + close 时间序列 (最早到 2021 年)。

分析目标:
  1. 找到每个指数的最优固定贪婪阈值 (回测小额定投)
  2. 找到最优入场偏移 (跌破阈值后第几天入场)
  3. 提炼共同规律，转化为可配置规则
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Dict, List, Tuple, Optional

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

FULL_SERIES_URL = "https://arkvol.com/api/funds-greed/alla/series?range=full"

INDEX_PARAMS = {
    "588000": {"name": "科创50"},
    "510500": {"name": "中证500"},
    "159845": {"name": "中证1000"},
    "159915": {"name": "创业板指"},
    "510300": {"name": "沪深300"},
    "510050": {"name": "上证50"},
    "513400": {"name": "道琼斯指数"},
    "159632": {"name": "纳斯达克"},
    "513600": {"name": "恒生指数"},
}

MIN_HISTORY = 120       # 最少需要的历史天数 (用于分布计算)
MIN_TRADES = 3           # 最少交易笔数
HOLD_DAYS = [5, 10, 15, 20, 30]
ENTRY_OFFSETS = [0, 1, 2, 3, 4, 5]


def read_api_key() -> str:
    env_key = os.environ.get("ARKVOL_API_KEY", "").strip()
    if env_key:
        return env_key
    for env_path in [Path(__file__).parent.parent / ".env", Path(".env")]:
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ARKVOL_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    raise RuntimeError("未配置 ARKVOL_API_KEY")


def fetch_full_series(api_key: str) -> Dict[str, List[Dict]]:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    req = Request(FULL_SERIES_URL, headers={
        "X-API-Key": api_key,
        "Accept": "application/json",
    }, method="GET")
    for attempt in range(3):
        try:
            with urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
            # code 为 None 或 0 都算成功
            if isinstance(data, dict):
                d = data.get("data", data)
                if isinstance(d, dict):
                    return d
                raise RuntimeError(f"Unexpected response: {str(data)[:200]}")
            raise RuntimeError(f"Unexpected response: {str(data)[:200]}")
        except (HTTPError, URLError) as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise RuntimeError(f"请求失败: {e}")


def backtest_single(
    greeds: List[float],
    closes: List[float],
    threshold: float,
    offset: int,
    hold_days: int,
    min_history: int,
) -> Dict[str, Any]:
    """固定贪婪阈值 + 偏移回测。"""
    trades = []
    in_position_until = -1

    for i in range(min_history, len(greeds) - hold_days - max(ENTRY_OFFSETS)):
        if i < in_position_until:
            continue

        if greeds[i] <= threshold:
            entry_idx = i + offset
            if entry_idx >= len(greeds) - hold_days:
                continue

            # 假突破过滤: 偏移期间反弹回阈值以上 → 取消
            bounced = any(
                greeds[j] > threshold
                for j in range(i + 1, min(entry_idx + 1, len(greeds)))
            )
            if bounced:
                continue

            entry_price = closes[entry_idx]
            exit_price = closes[entry_idx + hold_days]
            ret = (exit_price - entry_price) / entry_price * 100
            trades.append({
                "entry_greed": greeds[entry_idx],
                "return": round(ret, 2),
            })
            in_position_until = entry_idx + hold_days

    if len(trades) < MIN_TRADES:
        return {"trades": len(trades), "win_rate": 0, "avg_return": 0,
                "median_return": 0, "total_return": 0, "score": -999,
                "max_drawdown": 0, "sharpe": 0}

    rets = [t["return"] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    win_rate = wins / len(rets)
    avg_ret = mean(rets)
    med_ret = median(rets)
    total_ret = sum(rets)
    max_dd = min(rets) if rets else 0

    # 交易密度 = 交易笔数 / 可交易天数
    tradable_days = len(greeds) - min_history - hold_days - max(ENTRY_OFFSETS)
    density = len(rets) / max(tradable_days, 1)

    # 综合评分: AvgReturn × Win% × trades^0.3
    score = avg_ret * win_rate * (len(rets) ** 0.3)

    # 简易 Sharpe (假设无风险利率=0)
    sharpe = avg_ret / max(stdev(rets), 0.01) if len(rets) >= 2 else 0

    return {
        "trades": len(rets),
        "win_rate": round(win_rate, 4),
        "avg_return": round(avg_ret, 2),
        "median_return": round(med_ret, 2),
        "total_return": round(total_ret, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "density": round(density, 4),
        "score": round(score, 4),
    }


def distribution(greeds: List[float]) -> Dict[str, float]:
    s = sorted(greeds)
    n = len(s)
    def p(pct): return round(s[int(n * pct / 100)], 4)
    return {
        "n": n, "min": s[0], "max": s[-1], "mean": round(mean(s), 4), "std": round(stdev(s), 4),
        "p1": p(1), "p3": p(3), "p5": p(5), "p8": p(8), "p10": p(10),
        "p15": p(15), "p20": p(20), "p25": p(25), "p50": p(50), "p75": p(75),
    }


def generate_candidates(dist: Dict[str, float]) -> List[float]:
    """在 P1~P25 范围内以 0.005 步长生成候选阈值。"""
    lo = max(0.10, dist["p1"] - 0.03)
    hi = min(0.70, dist["p25"] + 0.03)
    lo = round(lo / 0.005) * 0.005
    hi = round(hi / 0.005) * 0.005
    return [round(lo + i * 0.005, 3) for i in range(int((hi - lo) / 0.005) + 1)]


def run():
    api_key = read_api_key()
    print("加载全量数据...")
    all_data = fetch_full_series(api_key)
    print(f"获取到 {len(all_data)} 个指数的全量数据\n")

    results = {}

    for code, params in INDEX_PARAMS.items():
        raw = all_data.get(code, [])
        if not raw:
            print(f"  {params['name']} ({code}): 全量数据中无此代码")
            continue

        name = params["name"]
        series = sorted(raw, key=lambda x: x.get("date", ""))
        greeds = [float(s.get("greed", 0)) for s in series]
        closes = [float(s.get("close", 0)) for s in series]
        dates = [s.get("date", "") for s in series]

        dist = distribution(greeds)

        if dist["n"] < MIN_HISTORY + max(HOLD_DAYS) + max(ENTRY_OFFSETS):
            print(f"  {name} ({code}): 数据不足 ({dist['n']}天)")
            continue

        candidates = generate_candidates(dist)
        print(f"  {name} ({code})  {dates[0]} ~ {dates[-1]} ({dist['n']}天)")
        print(f"    P1={dist['p1']:.4f} P3={dist['p3']:.4f} P5={dist['p5']:.4f} "
              f"P10={dist['p10']:.4f} P15={dist['p15']:.4f} "
              f"μ={dist['mean']:.4f} σ={dist['std']:.4f}")
        print(f"    扫描: {len(candidates)}阈值 × {len(ENTRY_OFFSETS)}偏移 × {len(HOLD_DAYS)}持有期")

        # ── 穷举搜索 ──
        best = None
        best_score = -999
        scan_count = 0

        for hold in HOLD_DAYS:
            for thresh in candidates:
                for off in ENTRY_OFFSETS:
                    r = backtest_single(greeds, closes, thresh, off, hold, MIN_HISTORY)
                    scan_count += 1
                    if r["score"] > best_score:
                        best_score = r["score"]
                        best = {**r, "threshold": thresh, "offset": off, "hold_days": hold}

        results[code] = {"name": name, "best": best, "dist": dist}

        if best and best["trades"] >= MIN_TRADES:
            print(f"    ★ 最优: greed≤{best['threshold']:.3f}  第{best['offset']}天入场  "
                  f"持有{best['hold_days']}天")
            print(f"      交易{best['trades']}笔  Win {best['win_rate']:.0%}  "
                  f"Avg {best['avg_return']:+.2f}%  Med {best['median_return']:+.2f}%  "
                  f"MaxDD {best['max_drawdown']:+.2f}%  Sharpe {best['sharpe']:.2f}")

            # ── 阈值扫描 (最佳偏移+持有期) ──
            print(f"\n    ── 阈值扫描 (偏移{best['offset']}天, 持有{best['hold_days']}天) ──")
            print(f"    {'阈值':>8s} {'笔数':>5s} {'Win%':>7s} {'Avg':>8s} {'Med':>8s} "
                  f"{'MaxDD':>7s} {'Sharpe':>7s} {'Score':>8s}")

            scan = []
            for thresh in candidates:
                r = backtest_single(greeds, closes, thresh, best["offset"], best["hold_days"], MIN_HISTORY)
                if r["trades"] >= MIN_TRADES:
                    scan.append({**r, "threshold": thresh})
            scan.sort(key=lambda x: x["score"], reverse=True)
            for s in scan[:10]:
                m = " ◀" if s["threshold"] == best["threshold"] else ""
                print(f"    {s['threshold']:>8.3f} {s['trades']:>5d} {s['win_rate']:>6.1%} "
                      f"{s['avg_return']:>+7.2f}% {s['median_return']:>+7.2f}% "
                      f"{s['max_drawdown']:>+6.2f}% {s['sharpe']:>6.2f} {s['score']:>8.2f}{m}")

            # ── 偏移对比 ──
            print(f"\n    ── 偏移对比 (阈值{best['threshold']:.3f}, 持有{best['hold_days']}天) ──")
            print(f"    {'偏移':>5s} {'笔数':>5s} {'Win%':>7s} {'Avg':>8s} {'Med':>8s} {'Score':>8s}")
            for off in ENTRY_OFFSETS:
                r = backtest_single(greeds, closes, best["threshold"], off, best["hold_days"], MIN_HISTORY)
                if r["trades"] >= 1:
                    m = " ◀" if off == best["offset"] else ""
                    print(f"    第{off}天 {r['trades']:>5d} {r['win_rate']:>6.1%} "
                          f"{r['avg_return']:>+7.2f}% {r['median_return']:>+7.2f}% "
                          f"{r['score']:>8.2f}{m}")

            # ── 持有期对比 ──
            print(f"\n    ── 持有期对比 (阈值{best['threshold']:.3f}, 偏移{best['offset']}天) ──")
            print(f"    {'持有':>5s} {'笔数':>5s} {'Win%':>7s} {'Avg':>8s} {'Med':>8s} {'Score':>8s}")
            for hold in HOLD_DAYS:
                r = backtest_single(greeds, closes, best["threshold"], best["offset"], hold, MIN_HISTORY)
                if r["trades"] >= 1:
                    m = " ◀" if hold == best["hold_days"] else ""
                    print(f"    {hold:>4d}天 {r['trades']:>5d} {r['win_rate']:>6.1%} "
                          f"{r['avg_return']:>+7.2f}% {r['median_return']:>+7.2f}% "
                          f"{r['score']:>8.2f}{m}")
        else:
            print(f"    无满足条件的组合 (要求≥{MIN_TRADES}笔)")

        print()

    # ── 汇总 + 规律分析 ──
    print(f"\n{'=' * 110}")
    print(f"  汇总 & 规律分析")
    print(f"{'=' * 110}")
    header = (f"  {'指数':<10s} {'数据':>5s} {'最优阈值':>8s} {'P5':>8s} {'P10':>8s} "
              f"{'阈值/P5':>8s} {'阈值/P10':>8s} {'偏移':>5s} {'持有':>5s} "
              f"{'笔数':>5s} {'Win%':>7s} {'Avg':>8s} {'Sharpe':>7s}")
    print(header)
    print(f"  {'─' * 10} {'─' * 5} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8} "
          f"{'─' * 5} {'─' * 5} {'─' * 5} {'─' * 7} {'─' * 8} {'─' * 7}")

    for code, data in results.items():
        best = data["best"]
        dist = data["dist"]
        if not best or best["trades"] < MIN_TRADES:
            print(f"  {data['name']:<10s} {dist['n']:>5d} {'N/A':>8s}")
            continue
        ratio_p5 = best["threshold"] / dist["p5"]
        ratio_p10 = best["threshold"] / dist["p10"]
        print(f"  {data['name']:<10s} {dist['n']:>5d} {best['threshold']:>8.3f} "
              f"{dist['p5']:>8.4f} {dist['p10']:>8.4f} "
              f"{ratio_p5:>8.2f} {ratio_p10:>8.2f} "
              f"第{best['offset']}天 {best['hold_days']:>4d}天 "
              f"{best['trades']:>5d} {best['win_rate']:>6.1%} "
              f"{best['avg_return']:>+7.2f}% {best['sharpe']:>6.2f}")

    # ── 规律总结 ──
    valid = {c: d for c, d in results.items() if d["best"] and d["best"]["trades"] >= MIN_TRADES}
    if valid:
        ratios_p5 = [d["best"]["threshold"] / d["dist"]["p5"] for d in valid.values()]
        ratios_p10 = [d["best"]["threshold"] / d["dist"]["p10"] for d in valid.values()]
        offsets = [d["best"]["offset"] for d in valid.values()]
        holds = [d["best"]["hold_days"] for d in valid.values()]

        print(f"\n  ── 规律提炼 ──")
        print(f"  最优阈值/P5 比值:  mean={mean(ratios_p5):.2f}  median={median(ratios_p5):.2f}  "
              f"min={min(ratios_p5):.2f}  max={max(ratios_p5):.2f}  std={stdev(ratios_p5):.2f}")
        print(f"  最优阈值/P10 比值: mean={mean(ratios_p10):.2f}  median={median(ratios_p10):.2f}  "
              f"min={min(ratios_p10):.2f}  max={max(ratios_p10):.2f}  std={stdev(ratios_p10):.2f}")
        print(f"  最优入场偏移: {mean(offsets):.1f}天 (范围 {min(offsets)}~{max(offsets)})")
        print(f"  最优持有期: {mean(holds):.0f}天 (范围 {min(holds)}~{max(holds)})")

        # ── 推荐配置 ──
        print(f"\n  ── 推荐 pit_greed (黄金坑线) ──")
        print(f"  方案 A (保守, 阈值=P5×1.05):")
        for code, data in valid.items():
            pg = round(data["dist"]["p5"] * 1.05, 3)
            eg = round(data["dist"]["p10"] * 1.05, 3)
            print(f"    {data['name']:<10s} pit_greed={pg:.3f}  entry_greed={eg:.3f}")

        print(f"\n  方案 B (回测最优, 直接用最优阈值):")
        for code, data in valid.items():
            b = data["best"]
            eg = round(b["threshold"] * 1.3, 3)
            print(f"    {data['name']:<10s} pit_greed={b['threshold']:.3f}  entry_greed={eg:.3f}  "
                  f"offset={b['offset']}d  hold={b['hold_days']}d")

        # 保存
        output = {}
        for code, data in valid.items():
            b = data["best"]
            output[code] = {
                "name": data["name"],
                "n_days": data["dist"]["n"],
                "pit_greed_a": round(data["dist"]["p5"] * 1.05, 3),
                "entry_greed_a": round(data["dist"]["p10"] * 1.05, 3),
                "pit_greed_b": b["threshold"],
                "entry_greed_b": round(b["threshold"] * 1.3, 3),
                "offset": b["offset"],
                "hold_days": b["hold_days"],
                "win_rate": b["win_rate"],
                "avg_return": b["avg_return"],
                "trades": b["trades"],
                "sharpe": b["sharpe"],
                "distribution": data["dist"],
            }
        out_path = Path(__file__).parent.parent / "pit_threshold_calibration.json"
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  详细结果: {out_path}")


if __name__ == "__main__":
    run()
