"""反算狼大「目标价」的算法归属（只读研究脚本）。

问题：他给过的指数点位，能不能被某个固定公式解释？「半分位」是不是通用算法？
方法：① 从语料抽 4 位点位 → ② 取首次出现且距收盘 ≥0.3% 的点位 → ③ 对每个点位，
      在「近 20/60/120/250 日的斐波那契/半分位/前高低/均线/整数关口」里找最近锚，
      容差 ±0.5% → ④ 对照 = 同一点位**随机换日期**（±120 交易日，每个 150 次），
      检验「他是不是在结构位出现的时点选了那个数」。
输出：data/research/b_rebound_target_attribution.csv（含每点的归属与逐点基线）。

用法：
    python scripts/research_brebound_targets.py                 # 用已有抽取结果
    python scripts/research_brebound_targets.py --extract       # 先重抽点位（需 xlsx）
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "research")
XLSX = os.path.join(ROOT, "狼大回复汇总 20260814-1457&往期.xlsx")
SHEETS = ["2025", "2026", "小时代狼大楼（历史存档）", "小时代鱼大楼", "小时代狼大楼（最新一周）"]
TOL = 0.005
FAMILIES = ("半分位", "斐波(38.2/61.8)", "前高/前低", "均线", "整数百", "缺口")


def extract() -> pd.DataFrame:
    import openpyxl

    lvl = re.compile(r"(?<![\d.])(\d{4})(?![\d.])(?![Ee万亿Ww%倍点个手股])")
    rows = []
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    for sheet in SHEETS:
        for r in wb[sheet].iter_rows(values_only=True):
            ts = str(r[0]) if r and r[0] else ""
            txt = str(r[1]) if r and r[1] else ""
            if not ts or not txt:
                continue
            body = re.sub(r"\[quote\].*?\[/quote\]", "", txt, flags=re.S)  # 去掉引用别人的话
            for m in lvl.finditer(body):
                v = int(m.group(1))
                if not (3000 <= v <= 4600) or v in (2024, 2025, 2026, 2027, 2028):
                    continue
                s, e = max(0, m.start() - 40), min(len(body), m.end() + 40)
                rows.append((ts[:16], v, body[s:e].replace("\n", " ")))
    return pd.DataFrame(rows, columns=["ts", "level", "ctx"])


class Attr:
    def __init__(self, csv: str):
        d = pd.read_csv(csv, dtype={"trade_date": str}).sort_values("trade_date")
        self.T = list(d.trade_date)
        self.C, self.H, self.L = d.close.values, d.high.values, d.low.values

    def pos(self, date: str) -> int | None:
        k = date[:10].replace("-", "")
        p = [i for i, x in enumerate(self.T) if x <= k]
        return p[-1] if p else None

    def groups(self, i: int, level: float, tol: float = TOL) -> dict:
        g = {k: [] for k in FAMILIES}
        for w in (20, 60, 120, 250):
            if i - w + 1 < 0:
                continue
            hi, lo = self.H[i - w + 1: i + 1].max(), self.L[i - w + 1: i + 1].min()
            g["半分位"].append((hi + lo) / 2)
            g["斐波(38.2/61.8)"].extend([lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618])
            g["前高/前低"].extend([hi, lo])
        for w in (20, 60, 120):
            if i - w + 1 >= 0:
                g["均线"].append(self.C[i - w + 1: i + 1].mean())
        g["整数百"].append(round(self.C[i] / 100) * 100)
        for j in range(max(1, i - 59), i + 1):
            if self.L[j] > self.H[j - 1]:
                g["缺口"].extend([self.H[j - 1], self.L[j]])
            if self.H[j] < self.L[j - 1]:
                g["缺口"].extend([self.H[j], self.L[j - 1]])
        return {k: any(abs(level - x) / x <= tol for x in v) for k, v in g.items()}

    def best(self, i: int, level: float, tol: float = TOL) -> str | None:
        best, bd = None, None
        for k, vals in {
            "半分位": [sum(self.H[i - w + 1: i + 1].max() + 0 for w in ())] ,
        }.items():
            pass
        cands = {}
        for w in (20, 60, 120, 250):
            if i - w + 1 < 0:
                continue
            hi, lo = self.H[i - w + 1: i + 1].max(), self.L[i - w + 1: i + 1].min()
            cands[f"半分位{w}d"] = (hi + lo) / 2
            cands[f"38.2%/{w}d"] = lo + (hi - lo) * 0.382
            cands[f"61.8%/{w}d"] = lo + (hi - lo) * 0.618
            cands[f"前高{w}d"] = hi
            cands[f"前低{w}d"] = lo
        for w in (20, 60, 120):
            if i - w + 1 >= 0:
                cands[f"MA{w}"] = self.C[i - w + 1: i + 1].mean()
        cands["整数百"] = round(self.C[i] / 100) * 100
        for k, v in cands.items():
            e = abs(level - v) / level
            if e <= tol and (bd is None or e < bd):
                best, bd = k, e
        return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    args = ap.parse_args()

    raw_path = os.path.join(OUT, "levels_raw.csv")
    if args.extract or not os.path.exists(raw_path):
        ex = extract()
        os.makedirs(OUT, exist_ok=True)
        ex.to_csv(raw_path, index=False, encoding="utf-8-sig")
        print(f"抽取点位 {len(ex)} 条 → {raw_path}")

    raw = pd.read_csv(raw_path, dtype={"ts": str})
    A = Attr(os.path.join(OUT, "index_daily_000001_long.csv"))

    cnt = Counter(raw.level)
    first = raw.drop_duplicates("level", keep="first").set_index("level")
    kw = re.compile(r"(目标|看到|看高|看低|压力|支撑|到\d|会到|应该到|位置|关口|收费站|上方|下方|跌破|站上|收复)")
    rng = np.random.default_rng(3)

    rows = []
    for v, r in first.iterrows():
        i = A.pos(r.ts)
        if i is None or i < 250 or abs(v / A.C[i] - 1) < 0.003:
            continue
        if cnt[v] < 3 and not kw.search(str(r.ctx)):
            continue
        g = A.groups(i, float(v))
        js = rng.integers(max(250, i - 120), min(len(A.T) - 1, i + 120), 150)
        base = {k: float(np.mean([A.groups(int(j), float(v))[k] for j in js])) for k in FAMILIES}
        b = A.best(i, float(v))
        rows.append({"date": r.ts[:10], "level": int(v), "close": round(float(A.C[i]), 1),
                     "dist_pct": round((v / A.C[i] - 1) * 100, 2), "n": cnt[v],
                     "nearest": b or "未解释", "ctx": str(r.ctx)[:50],
                     **g, **{f"base_{k}": round(base[k], 3) for k in FAMILIES}})
    df = pd.DataFrame(rows)
    path = os.path.join(OUT, "b_rebound_target_attribution.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")

    print(f"\nn={len(df)}  容差 ±{TOL*100:.1f}%")
    print(f"{'族':<16}{'命中':>6}{'命中率':>8}{'日期置换基线':>14}{'超额':>9}")
    for k in FAMILIES:
        r, b = df[k].mean(), df[f"base_{k}"].mean()
        print(f"{k:<16}{int(df[k].sum()):>6}{r*100:>7.1f}%{b*100:>13.1f}%{(r-b)*100:>+8.1f}pp")
    for name, sub in (("下看", df[df.level < df.close]), ("上看", df[df.level > df.close])):
        cells = []
        for k in ("斐波(38.2/61.8)", "半分位", "均线", "前高/前低"):
            o, b = sub[k].mean(), sub[f"base_{k}"].mean()
            cells.append(f"{k} {o*100:.0f}%/{b*100:.0f}%({(o-b)*100:+.0f})")
        print(f"  {name} n={len(sub)}: " + " | ".join(cells))
    print(f"\n→ {path}")


if __name__ == "__main__":
    main()
