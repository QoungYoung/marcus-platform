# -*- coding: utf-8 -*-
"""P0.5：科技子主题风向标（proxy + 待确认）。

输出：
  data/core_p05/subtheme_bellwethers.csv  每个 chain_map 日期 × 子主题
  data/core_p05/latest.json               最新日期 13 个子主题
  docs/core-p05-subtheme-bellwethers.md   最新 Top5 审阅表
"""
from __future__ import annotations
import argparse, glob, json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "app", "services"))
from wolf_card.subthemes import SUBTEMES, stocks as sub_stocks, parent
from wolf_card.main_business import load as mb_load

BIZ_KW = {
    "AI软": ["软件", "云", "数据", "互联网", "信息", "智能", "平台", "系统", "应用"],
    "光模块/CPO": ["光", "模块", "通信", "器件"],
    "IDC/算力租赁": ["数据中心", "IDC", "算力", "服务器", "云"],
    "AI硬件/PCB/液冷": ["PCB", "电路板", "覆铜板", "服务器", "液冷", "散热"],
    "AI芯片": ["芯片", "GPU", "处理器", "集成电路", "半导体"],
    "半导体设备": ["设备", "刻蚀", "薄膜", "光刻", "清洗", "沉积"],
    "半导体材料": ["材料", "硅片", "光刻胶", "靶材", "气体", "化学品"],
    "芯片设计": ["芯片设计", "集成电路设计", "设计"],
    "存储芯片": ["存储", "内存", "闪存", "DRAM", "NAND"],
    "封测": ["封装", "测试", "封测"],
    "第三代半导体": ["碳化硅", "氮化镓", "SiC", "GaN", "第三代"],
    "人形机器人": ["机器人", "伺服", "减速器", "自动化", "执行器"],
    "工业机器人": ["机器人", "伺服", "减速器", "自动化", "运动控制"],
}


def _biz_ratio(code: str, sub: str):
    try:
        # 只用已有缓存，避免回测批量触发 fina_mainbz 网络请求
        import json as _json
        p = os.path.join("data/main_business", code.replace(".", "_") + ".json")
        if not os.path.exists(p):
            return None
        d = _json.load(open(p, encoding="utf-8")); items = d.get("items") or []
        total = sum(x.get("sales", 0) for x in items)
        if total <= 0: return None
        kws = BIZ_KW.get(sub) or []
        if not kws:
            return None
        hit = sum(x.get("sales", 0) for x in items if any(k.lower() in str(x.get("item", "")).lower() for k in kws))
        return hit / total
    except Exception:
        return None

OUT = "data/core_p05"
os.makedirs(OUT, exist_ok=True)
SUBS = list(SUBTEMES.keys())


def chain_files():
    return sorted([p for p in glob.glob("data/chain_map_2026*.json") if "_v2_cmp" not in p])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest-only", action="store_true", help="只刷新最新 chain_map 日期（定时任务用）")
    args = ap.parse_args()
    bars = pd.read_parquet("data/bars_combined.parquet", columns=["ts_code", "trade_date", "close", "amount"])
    piv = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
    names = dict(zip(bars.ts_code, bars.ts_code))
    # 用 stock_pool 名字
    import sqlite3
    con = sqlite3.connect(".dsh-tmp/buyside/prod_data/stock_pool.db")
    names = dict(con.execute("select ts_code,name from stock_pool"))

    rows = []
    latest = {}
    files = chain_files()
    if args.latest_only and files:
        files = [files[-1]]
    for cf in files:
        d = str(json.load(open(cf))["date"])
        if d < "20260615" or d > "20261015":
            continue
        cm = json.load(open(cf))
        lead = {}
        for t in cm.get("themes", []):
            for seg in t.get("segments", []):
                for i, x in enumerate(seg.get("leading_verified") or [], 1):
                    lead[x.get("ts")] = {"rank": i, "segment": seg.get("label"), "role": "leader"}
        ret = piv.pct_change()
        for sub in SUBS:
            th = parent(sub)
            codes = [c for c in sub_stocks(sub) if c in piv.columns]
            if not codes:
                continue
            subp = piv.loc[:d, codes].tail(80)
            if len(subp) < 25:
                continue
            sub_ret = subp.pct_change().dropna()
            idx = sub_ret.mean(axis=1)
            beta = sub_ret.corrwith(idx)
            last_close = piv.loc[:d, codes].iloc[-1]
            last_amt = amt.loc[:d, codes].tail(20).mean()
            r5 = last_close / piv.loc[:d, codes].iloc[-6] - 1 if len(subp) >= 6 else None
            r20 = last_close / piv.loc[:d, codes].iloc[-21] - 1 if len(subp) >= 21 else None
            r60 = last_close / piv.loc[:d, codes].iloc[-61] - 1 if len(subp) >= 61 else None
            cand = []
            for c in codes:
                b = beta.get(c, np.nan)
                a = last_amt.get(c, np.nan)
                r5v = r5.get(c, np.nan); r20v = r20.get(c, np.nan); r60v = r60.get(c, np.nan)
                if pd.isna(a) or a <= 0: continue
                ratio = _biz_ratio(c, sub)
                if ratio is not None and ratio < 0.25: continue
                cand.append({"code": c, "name": names.get(c, c), "core_beta": b,
                             "r5": r5v, "r20": r20v, "r60": r60v, "amt20": a,
                             "chain_rank": (lead.get(c) or {}).get("rank"),
                             "biz_ratio": ratio,
                             "chain_segment": (lead.get(c) or {}).get("segment")})
            if len(cand) < 2: continue
            df = pd.DataFrame(cand)
            df["beta_pct"] = df.core_beta.rank(pct=True)
            df["r5_pct"] = df.r5.rank(pct=True)
            df["amt_pct"] = df.amt20.rank(pct=True)
            df["chain_bonus"] = df.chain_rank.apply(lambda x: 0.2 if x == 1 else (0.1 if x and x <= 4 else 0.0))
            df["score"] = 0.40 * df.beta_pct + 0.30 * df.r5_pct + 0.20 * df.amt_pct + df.chain_bonus
            df = df.dropna(subset=["score"]).sort_values(["score", "amt20"], ascending=False)
            if df.empty: continue
            top = df.head(5)
            best = top.iloc[0]
            row = {"date": d, "subtheme": sub, "parent": th, "n": len(df),
                   "bellwether_code": best.code, "bellwether_name": best["name"],
                   "score": round(float(best.score), 4),
                   "core_beta": round(float(best.core_beta), 3) if pd.notna(best.core_beta) else None,
                   "r5": round(float(best.r5) * 100, 2) if pd.notna(best.r5) else None,
                   "r20": round(float(best.r20) * 100, 2) if pd.notna(best.r20) else None,
                   "amt20": round(float(best.amt20), 1),
                   "chain_rank": int(best.chain_rank) if pd.notna(best.chain_rank) else None,
                   "top5": " / ".join("%s(%s)" % (r["name"], round(float(r.score), 2)) for _, r in top.iterrows()),
                   "needs_review": True}
            rows.append(row)
            if d == max(x["date"] for x in rows):
                latest[sub] = {**row, "top5_rows": top[["code","name","score","core_beta","r5","amt20","chain_rank"]].to_dict("records")}
    df = pd.DataFrame(rows)
    out_csv = os.path.join(OUT, "subtheme_bellwethers.csv")
    if args.latest_only and os.path.exists(out_csv) and len(df):
        old = pd.read_csv(out_csv, dtype={"date": str})
        latest_d = str(df.date.max())
        old = old[old.date != latest_d]
        df = pd.concat([old, df], ignore_index=True).sort_values(["date", "subtheme"])
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    latest_date = str(df.date.max()) if len(df) else ""
    latest_rows = df[df.date == latest_date].sort_values("subtheme")
    json.dump({"date": latest_date, "items": latest_rows.to_dict("records")},
              open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # review markdown
    lines = ["# 科技子主题风向标（P0.5 proxy，待确认）", "",
             f"> 最新日期：{latest_date}；数据：`subthemes.stocks` + chain_map + 日线；**proxy，需人工/ Panel 确认**。", "",
             "| 子主题 | 风向标（proxy） | 代码 | score | core_beta | r5 | chain rank | Top5 |",
             "|---|---|---|---|---|---|---|---|"]
    for _, r in latest_rows.iterrows():
        lines.append(f"| {r['subtheme']} | {r['bellwether_name']} | {r['bellwether_code']} | {r['score']} | {r['core_beta']} | {r['r5']}% | {r['chain_rank']} | {r['top5']} |")
    lines += ["", "## 说明", "",
              "- 这是代理排序，不是最终风向标；`needs_review=True`。",
              "- 风向标会换：语料 2025-12-06「短时期板块内最强走势个股」。",
              "- 下一步：每个子主题确认唯一 bellwether，确认后才接 P1 的风向标闸。"]
    open("docs/core-p05-subtheme-bellwethers.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("rows", len(df), "dates", df.date.nunique(), "latest", latest_date, "subs", df.subtheme.nunique())
    print(latest_rows[["subtheme", "bellwether_name", "score", "core_beta", "r5"]].to_string(index=False))


if __name__ == "__main__":
    main()
