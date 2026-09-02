# -*- coding: utf-8 -*-
"""build_crowding.py — 真实公募拥挤度聚合器
输入: postgres fund_portfolio_holdings(Q2 20260630) + stock_concept_map(112625 股票↔概念)
输出: data/rotation_crowding.json  {end_date, stock, concept, universe, daxin_top, daguang_top}
拥挤度真实口径: 每只股票被多少基金重仓(n_funds) + Σstk_float_ratio + Σ持仓市值；聚到概念/子方向。
用法: python -u apps/main_line/build_crowding.py [end_date]
"""
import os, sys, json, collections
try:
    import psycopg2
except Exception as e:
    print("ERR psycopg2:", e); sys.exit(1)

DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
DATA = os.environ.get("DATA_DIR", "data")

SUB = {
    "国算/算力": ["算力概念", "数据中心", "云计算", "算力租赁"],
    "液冷": ["液冷概念", "液冷服务器"],
    "存储": ["存储芯片"],
    "材料": ["半导体材料", "光刻胶", "光刻机(胶)", "碳基材料"],
    "芯片/半导体": ["国产芯片", "半导体概念", "AI芯片", "数字芯片设计", "模拟芯片设计", "第三代半导体", "第四代半导体"],
    "光通信": ["光通信模块", "CPO概念", "光纤概念"],
    "铜缆/电源": ["铜缆高速连接"],
}

def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    if len(sys.argv) > 1:
        end = sys.argv[1]
    else:
        cur.execute("SELECT max(end_date) FROM fund_portfolio_holdings")
        end = cur.fetchone()[0]
    print("end_date:", end, flush=True)
    # 股票级基金拥挤
    cur.execute("SELECT symbol, count(DISTINCT fund_code), sum(coalesce(stk_float_ratio,0)), "
                "sum(coalesce(mkv,0)), sum(coalesce(amount,0)) FROM fund_portfolio_holdings "
                "WHERE end_date=%s GROUP BY symbol", (end,))
    stock = {}
    for sym, nf, sf, mkv, amt in cur.fetchall():
        stock[sym] = {"n_funds": int(nf or 0), "sum_float": float(sf or 0),
                      "sum_mkv": float(mkv or 0), "sum_amount": float(amt or 0)}
    # 概念成分(股票→概念集合 / 概念→股票集合)
    cur.execute("SELECT concept_name, ts_code FROM stock_concept_map")
    concept_stocks = collections.defaultdict(set)
    stock_concepts = collections.defaultdict(set)
    for cname, code in cur.fetchall():
        concept_stocks[cname].add(code)
        stock_concepts[code].add(cname)
    cur.execute("SELECT ts_code, name FROM stock_pool")
    stock_names = {r[0]: r[1] for r in cur.fetchall()}
    cur.close(); conn.close()
    concept = {}
    for cname, codes in concept_stocks.items():
        rows = [(s, stock.get(s)) for s in codes if stock.get(s)]
        if not rows: continue
        concept[cname] = {
            "n_members": len(codes), "n_held": len(rows),
            "n_funds_ties": sum(r[1]["n_funds"] for r in rows),
            "sum_float": round(sum(r[1]["sum_float"] for r in rows), 3),
            "sum_mkv_yi": round(sum(r[1]["sum_mkv"] for r in rows) / 1e8, 2),
        }
    # 子方向(概念名含关键词, 动态来自DB)
    universe = {}
    for sub, kws in SUB.items():
        names = [c for c in concept_stocks if any(k in c for k in kws)]
        codes = set()
        for n in names: codes |= concept_stocks[n]
        held = [(s, stock[s]) for s in codes if s in stock]
        sum_float = sum(v["sum_float"] for _, v in held)
        universe[sub] = {
            "n_concepts": len(names), "concepts": sorted(names)[:12],
            "n_stocks": len(codes), "n_held": len(held),
            "n_funds_ties": sum(v["n_funds"] for _, v in held),
            "sum_float": round(sum_float, 3),
            "avg_float_per_held": round(sum_float / max(len(held), 1), 4),
            "top_held": sorted(held, key=lambda x: (-x[1]["n_funds"], -x[1]["sum_float"]))[:6],
        }
    def top_by(sub_kws, n=6):
        cnames = [c for c in concept_stocks if any(k in c for k in sub_kws)]
        codes = set()
        for nm in cnames: codes |= concept_stocks[nm]
        held = [(s, stock[s]) for s in codes if s in stock]
        held = [(s, v) for s, v in held if v["sum_mkv"] >= 5e8 or (v["n_funds"] >= 4 and v["sum_float"] >= 1.0)]
        held.sort(key=lambda x: (-x[1]["sum_mkv"], -x[1]["n_funds"]))
        return [{"symbol": s, "name": stock_names.get(s, s), "n_funds": v["n_funds"], "sum_float": round(v["sum_float"], 3),
                 "mkv_yi": round(v["sum_mkv"] / 1e8, 2)} for s, v in held[:n]]
    out = {"end_date": end, "stock": stock, "concept": concept, "universe": universe,
           "chip_top": top_by(["芯片", "半导体"]),
           "optics_top": top_by(["光模块", "光通信", "CPO", "光纤"])}
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, "rotation_crowding.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print("WROTE", os.path.join(DATA, "rotation_crowding.json"))
    for sub, d in universe.items():
        print("%-14s n_con=%d held=%d ties=%d sum_float=%.1f avg=%.4f" % (
            sub, d["n_concepts"], d["n_held"], d["n_funds_ties"], d["sum_float"], d["avg_float_per_held"]))
    print("chip_top :", json.dumps(out["chip_top"], ensure_ascii=False))
    print("optics_top:", json.dumps(out["optics_top"], ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
