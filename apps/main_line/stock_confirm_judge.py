# -*- coding: utf-8 -*-
"""stock_confirm_judge.py — 个股级确认链(三层联动之三): fusion TOP3 主题全量概念成分 confirm_chain → 确认比例
2026-09-07 数据层重构: 逐只 ts_code daily → 逐交易日(trade_cal) 全市场批量(daily trade_date, 5548行/次 0.1s)
2026-09-13: 数据源由 gzcloud 代理改为 datahubco(基础接口)+promax(聚合) 中继 (core/tushare_relay.py)
70个交易日×0.1s≈1分钟拉全缓存, 取代 360+ 次逐只请求(原~30min); 概念优先级(光模块/CPO/算力/AI应用 先行)+全量概念。
输出: data/stock_confirm_result.json (平铺 {概念:{theme,n,confirm,ratio,stocks}})
"""
import json, os, sys, time
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from confirm_chain import confirm_chain

DATA = os.environ.get("DATA_DIR", "/app/data")
DB = os.path.join(DATA, "stock_pool.db")
MAX_STOCKS = int(os.getenv("STOCK_CONFIRM_MAX", "10"))
MAX_CONCEPTS = int(os.getenv("STOCK_CONFIRM_CONCEPTS", "99"))
PRIORITY_CONCEPTS = [x.strip() for x in os.getenv(
    "STOCK_CONFIRM_PRIORITY",
    "光通信模块,CPO概念,算力概念,AI应用,人工智能,DeepSeek概念,液冷概念,数据中心,ChatGPT概念,AI智能体").split(",") if x.strip()]


def _relay():
    """加载 core/tushare_relay.py（datahubco + promax，替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def _gz(api, **p):
    """Tushare 中继查询（返回 items 行列表，字段顺序 = fields 参数）。"""
    try:
        _fields, items = _relay().relay_items(api, fields="ts_code,trade_date,close,vol", **p)
        return items or []
    except Exception as e:
        print("TUSHARE_FAIL", api, str(e)[:120], file=sys.stderr)
        return []


def _trade_days():
    _fields, items = _relay().relay_items(
        "trade_cal", exchange="SSE", start_date="20260601",
        end_date=time.strftime("%Y%m%d"), fields="cal_date,is_open")
    days = sorted(x[0] for x in (items or []) if x[1] == 1)
    return days[-90:] if len(days) > 90 else days


def _fetch_market(days):
    """逐交易日全市场批量 → (close_df, vol_df) index=datetime 交易日期, columns=ts_code"""
    recs = []
    for d in days:
        for it in _gz("daily", trade_date=d):
            recs.append({"d": d, "ts": it[0], "close": float(it[2] or 0), "vol": float(it[3] or 0)})
        if len(recs) % 10000 == 0:
            print(f"[stock_confirm] fetched {len(recs)} rows", file=sys.stderr)
    df = pd.DataFrame(recs)
    df["dt"] = pd.to_datetime(df["d"], format="%Y%m%d")
    close = df.pivot_table(index="dt", columns="ts", values="close", aggfunc="last").sort_index()
    vol = df.pivot_table(index="dt", columns="ts", values="vol", aggfunc="last").sort_index()
    return close, vol


def main():
    CONFIRM_TOP_N = int(os.getenv("CONFIRM_TOP_N", "3"))
    try:
        import fusion_mainline as fm
        st = json.load(open(os.path.join(DATA, "main_line_state.json"), encoding="utf-8"))
        # 2026-09-09 主线判定统一: gate(heat_v2+结构)优先, 旧 fusion conc 分仅参考
        themes = None
        try:
            from mainline_confirm_state import gate_top_themes
            _gt = gate_top_themes(CONFIRM_TOP_N)
            if _gt: themes = _gt[0]
        except Exception:
            pass
        if not themes:
            fus = st.get("fusion") or {}
            rank = sorted(fus.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))
            themes = [k for k, _ in rank[:CONFIRM_TOP_N]] or [st.get("main_line") or "AI/算力/科技"]
    except Exception:
        fm = None
        themes = ["AI/算力/科技"]
    print("[stock_confirm] TOP确认主题:", themes, file=sys.stderr)
    if not os.path.exists(DB):
        print("[stock_confirm] NO stock_pool.db", file=sys.stderr)
        return
    t0 = time.time()
    days = _trade_days()
    close, vol = _fetch_market(days)
    print(f"[stock_confirm] 全市场缓存 {len(days)}日 x {close.shape[1]}票  {time.time()-t0:.0f}s", file=sys.stderr)
    con = None
    import sqlite3
    con = sqlite3.connect(DB)
    out = {}
    for mt in themes:
        try:
            names = fm.THEME_CONCEPTS.get(mt, []) if fm is not None else []
        except Exception:
            names = []
        if not names:
            names = ["人工智能", "算力概念", "CPO概念", "光通信模块", "液冷概念"]
        names = [n for n in PRIORITY_CONCEPTS if n in names] + [n for n in names if n not in PRIORITY_CONCEPTS]
        for cname in names[:MAX_CONCEPTS]:
            try:
                cur = con.cursor()
                cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?", (cname, MAX_STOCKS))
                codes = [r[0] for r in cur.fetchall()]
            except Exception as ex:
                print("[stock_confirm] concept err", cname, str(ex)[:60], file=sys.stderr)
                continue
            stocks = []
            for ts_code in codes:
                if ts_code not in close.columns:
                    continue
                ser = close[ts_code].dropna()
                if len(ser) < 40:
                    continue
                v = vol[ts_code].reindex(ser.index)
                cc = confirm_chain(ser, None, v)
                stocks.append({"code": ts_code, "stage": cc["stage"]})
            n_confirm = sum(1 for s in stocks if s["stage"] in ("确认", "突破候选"))
            out[cname] = {"theme": mt, "n": len(stocks), "confirm": n_confirm,
                          "ratio": round(n_confirm / max(len(stocks), 1), 2), "stocks": stocks}
            print(f"[stock_confirm] {mt} > {cname} n={len(stocks)} 确认 {n_confirm}", file=sys.stderr)
    if con:
        con.close()
    json.dump(out, open(os.path.join(DATA, "stock_confirm_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[stock_confirm] WROTE stock_confirm_result.json 概念:{len(out)} ({time.time()-t0:.0f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
