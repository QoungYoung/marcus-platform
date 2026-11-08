"""B反 闸门 v2：多标的 + 路径型结果 + 分位数阈值（只读回测）。

三项改造（对应 docs/b-rebound-reread-202610.md §5.5b 的结论）：
  1. 结果变量：不再用固定 ±8%/−5%，而是「先到该事件自己的目标区 vs 先收盘破失效位」；
  2. 样本：512480 / 159995 / 588000 / 588080 / 159516 / 588170 / 588380 / 512760 共 8 只；
     同一天多只亮灯只算 1 个独立事件（聚类），因为它们相关性 ≈0.95；
  3. 阈值：把「距60日高 ≤ −25%」「距目标 >15%」换成**自身历史分位数**（≤20% / ≥80%）。

目标区构造（按 §3.5 的方向结论：上看目标用「前高/前低」）：
  候选 = 近 120 日的前低（在价格之上者）+ 60 日高；取最近且空间 ≥6% 的一个。
失效位 = 20 日最低（收盘跌破即失效）。
结果 = 30 个交易日内，先触及目标（赢）还是先收盘破失效位（输）。

用法：python scripts/research_brebound_multietf.py
"""
from __future__ import annotations

import glob
import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(ROOT, "data", "research", "etf_pool")
OUT = os.path.join(ROOT, "data", "research")

PCT_WIN = 750      # 分位数回看窗口（约 3 年）
PCT_MIN = 250      # 最少历史
THRESH = 5         # 闸门分阈值（满分 9）
HORIZON = 30       # 路径期限（交易日）
MIN_ROOM = 0.06    # 目标至少 6% 空间


def load_ws() -> dict:
    ws = {}
    for f in glob.glob(os.path.join(ROOT, "data", "wave_state_history", "*.json")):
        try:
            j = json.load(open(f))
        except Exception:
            continue
        ws[j.get("as_of") or str(j.get("date", "")).replace("-", "")] = j
    return ws


def adjust_splits(df: pd.DataFrame) -> pd.DataFrame:
    """份额折算：pre_close 与上一根收盘的比值偏离 1 超过 2% 即视为折算，向前复权。"""
    d = df.sort_values("trade_date").reset_index(drop=True).copy()
    k = np.ones(len(d))
    for i in range(1, len(d)):
        pc, prev = d.pre_close.values[i], d.close.values[i - 1]
        if prev and pc and abs(pc / prev - 1) > 0.02:
            k[i] = pc / prev
    cum = np.cumprod(k[::-1])[::-1] / k  # 第 i 行生效于其之前的所有折算
    for c in ("open", "high", "low", "close"):
        d[c] = d[c].values * cum
    d["vol"] = d["vol"].values / cum
    d["n_split"] = (k != 1).cumsum()
    return d


def pctile(x: np.ndarray, i: int) -> float:
    """x[i] 在 x[i-PCT_WIN:i+1] 中的分位（0=最低）。"""
    a = max(0, i - PCT_WIN + 1)
    if i - a + 1 < PCT_MIN:
        return np.nan
    seg = x[a: i + 1]
    return float((seg < x[i]).mean())


def prep(code: str, ws: dict) -> pd.DataFrame | None:
    p = os.path.join(POOL, code.replace(".", "_") + ".csv")
    if not os.path.exists(p):
        return None
    d = adjust_splits(pd.read_csv(p, dtype={"trade_date": str}))
    if len(d) < PCT_MIN + 120:
        return None
    c, h, l, v = (d[x].values.astype(float) for x in ("close", "high", "low", "vol"))
    n = len(d)
    hi60 = np.full(n, np.nan)
    lo20 = np.full(n, np.nan)
    ma20 = np.full(n, np.nan)
    dd60 = np.full(n, np.nan)
    vr = np.full(n, np.nan)
    dd_pct = np.full(n, np.nan)
    vr_pct = np.full(n, np.nan)
    for i in range(60, n):
        hi60[i] = h[i - 59: i + 1].max()
        lo20[i] = l[i - 19: i + 1].min()
        ma20[i] = c[i - 19: i + 1].mean()
        dd60[i] = c[i] / hi60[i] - 1
        vr[i] = v[i] / v[i - 19: i + 1].mean()
        dd_pct[i] = pctile(dd60, i)
        vr_pct[i] = pctile(vr, i)
    # 前低（在 120 日内的 pivot low，取「在现价之上」的最近一个）
    piv_lo = np.full(n, np.nan)
    for i in range(120, n):
        cands = [l[j] for j in range(i - 119, i + 1)
                 if j <= i - 2 and l[j] == l[max(0, j - 2): j + 3].min() and l[j] > c[i]]
        piv_lo[i] = min(cands) if cands else np.nan
    # M 顶
    dbl = np.zeros(n, bool)
    for i in range(60, n):
        seg = h[i - 59: i + 1]
        k1 = int(seg.argmax())
        mask = np.ones(len(seg), bool)
        mask[max(0, k1 - 5): min(len(seg), k1 + 6)] = False
        if mask.sum() > 5:
            h2 = seg[mask].max()
            dbl[i] = abs(seg.max() / h2 - 1) <= 0.03 and c[i] < h2 * 0.95
    accel = np.zeros(n, bool)
    for i in range(3, n):
        accel[i] = (c[i] / c[i - 1] - 1 <= -0.05) or (c[i] / c[i - 3] - 1 <= -0.12)
    out = d.copy()
    for name, arr in (("hi60", hi60), ("lo20", lo20), ("ma20", ma20), ("dd60", dd60),
                      ("vr", vr), ("dd_pct", dd_pct), ("vr_pct", vr_pct),
                      ("piv_lo", piv_lo), ("dbl", dbl), ("accel", accel)):
        out[name] = arr
    out["code"] = code
    out["wsub"] = [ws.get(x, {}).get("sub_level") for x in d.trade_date]
    return out


def build_target(row) -> tuple[float, float] | None:
    """目标（最近的可达前低/前高）与失效位（20日低）。空间不足则丢弃。"""
    c = row.close
    tgt = np.nan
    for x in (row.piv_lo, row.hi60):
        if x is not None and not np.isnan(x) and x / c - 1 >= MIN_ROOM:
            tgt = x
            break
    inv = row.lo20
    if np.isnan(tgt) or not (inv < c):
        return None
    return float(tgt), float(inv)


def outcome(df: pd.DataFrame, i: int, tgt: float, inv: float) -> tuple[int, float]:
    """返回 (1=先到目标 / -1=先收盘破失效位 / 0=未决, 实际收益率%)。
    到目标→按目标价成交；破失效→按当日收盘价成交（含跳空风险）；未决→按期末收盘。"""
    h, c = df.high.values, df.close.values
    e = c[i]
    last = min(i + HORIZON, len(df) - 1)
    for j in range(i + 1, last + 1):
        if c[j] < inv:
            return -1, (c[j] / e - 1) * 100
        if h[j] >= tgt:
            return 1, (tgt / e - 1) * 100
    return 0, (c[last] / e - 1) * 100


def main() -> None:
    ws = load_ws()
    codes = [os.path.basename(p)[:-4].replace("_", ".") for p in glob.glob(os.path.join(POOL, "*.csv"))]
    frames = [prep(c_, ws) for c_ in sorted(codes)]
    frames = [f for f in frames if f is not None and len(f)]
    print(f"标的 {len(frames)} 只: " + ", ".join(f"{f.code.iloc[0]}({len(f)})" for f in frames))

    # 指数（Gate-0 用）
    idx = pd.read_csv(os.path.join(OUT, "index_daily_000001_long.csv"), dtype={"trade_date": str})
    idx = idx.sort_values("trade_date").reset_index(drop=True)
    ic, ih = idx.close.values.astype(float), idx.high.values.astype(float)
    iv = idx.vol.values.astype(float)
    idf = pd.DataFrame({"trade_date": idx.trade_date,
                        "idd60": ic / pd.Series(ih).rolling(60).max().values - 1,
                        "ivr": iv / pd.Series(iv).rolling(20).mean().values,
                        "ima20": ic / pd.Series(ic).rolling(20).mean().values - 1})
    idf["idd_pct"] = [pctile(idf.idd60.values, i) for i in range(len(idf))]
    idf["ivr_pct"] = [pctile(idf.ivr.values, i) for i in range(len(idf))]
    IM = idf.set_index("trade_date")

    rows = []
    last_i = {}
    for df in frames:
        for i in range(120, len(df) - 1):
            r = df.iloc[i]
            t = r.trade_date
            if t not in IM.index:
                continue
            im = IM.loc[t]
            g0 = g1 = g2 = 0
            if r.wsub == "4-3":
                g0 += 2
            elif r.wsub == "4-5":
                g0 += 1
            if not np.isnan(im.idd_pct) and im.idd_pct <= 0.20:
                g0 += 1
            if im.ima20 < 0:
                g0 += 1
            g0 = min(g0, 3)
            if r.dbl:
                g1 += 2
            if not np.isnan(r.dd_pct) and r.dd_pct <= 0.20:
                g1 += 1
            if r.accel:
                g1 += 1
            g1 = min(g1, 3)
            if not np.isnan(im.ivr_pct) and im.ivr_pct <= 0.20:
                g2 += 1
            if not np.isnan(r.vr_pct) and r.vr_pct >= 0.80:
                g2 += 1
            g2 = round(g2 * 1.5)
            if i - last_i.get(r.code, -10**9) < HORIZON:
                continue
            tg = build_target(r)
            if tg is None:
                continue
            last_i[r.code] = i
            tgt, inv = tg
            rows.append(dict(code=r.code, date=t, total=g0 + g1 + g2, g0=g0, g1=g1, g2=g2,
                             close=r.close, tgt=tgt, inv=inv,
                             room=(tgt / r.close - 1) * 100, risk=(1 - inv / r.close) * 100,
                             rr=(tgt - r.close) / max(1e-9, r.close - inv),
                             dd=r.dd60, dd_pct=r.dd_pct, vr_pct=r.vr_pct,
                             L1=(r.dd_pct <= 0.20) and (r.close < r.ma20) and (tgt / r.close - 1 >= 0.10),
                             log=outcome(df, i, tgt, inv)[0], ret=outcome(df, i, tgt, inv)[1]))
    S = pd.DataFrame(rows)
    S.to_csv(os.path.join(OUT, "b_rebound_multietf_events.csv"), index=False, encoding="utf-8-sig")

    def ev(sub, label):
        e = sub.drop_duplicates("date")           # 同日多只 → 1 个独立事件
        w = (e.log == 1).sum(); l = (e.log == -1).sum(); z = (e.log == 0).sum()
        wr = w / max(1, w + l)
        print(f"{label:<26} 事件{len(e):>4}  赢{w:>3} 输{l:>3} 未决{z:>3}  胜率{wr*100:>5.1f}%  "
              f"R:R中位{e.rr.median():>5.2f}  实际均值{e.ret.mean():>+6.2f}%  中位{e.ret.median():>+6.2f}%")
        return e

    print(f"\n总样本行 {len(S)}，日期跨度 {S.date.min()} → {S.date.max()}，标的 {S.code.nunique()}\n")
    base = ev(S, "基准（所有可构造日）")
    fire = ev(S[S.total >= THRESH], f"闸门分 ≥{THRESH}")
    fireL = ev(S[(S.total >= THRESH) & S.L1], f"闸门 ≥{THRESH} + L1")
    # 置换检验：抽同样数量的非亮灯日
    rng = np.random.default_rng(7)
    nf = S[S.total < THRESH].drop_duplicates("date")
    obs = fire.ret.mean()
    null = []
    for _ in range(3000):
        smp = nf.sample(n=min(len(fire), len(nf)), random_state=int(rng.integers(1e9)))
        null.append(smp.ret.mean())
    null = np.array(null)
    p = float(np.mean(null >= obs))
    print(f"\n置换检验（实际收益均值）：亮灯 {obs:+.2f}% vs 非亮灯抽样 {null.mean():+.2f}% "
          f"（95% 区间 {np.percentile(null,2.5):+.2f} ~ {np.percentile(null,97.5):+.2f}%）  p={p:.3f}")
    print(f"基准胜率 {((base.log==1).sum()/max(1,(base.log!=0).sum()))*100:.1f}%")


if __name__ == "__main__":
    main()
