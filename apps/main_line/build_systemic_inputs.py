# -*- coding: utf-8 -*-
"""build_systemic_inputs.py — 系统性风险开关行情采集器(日线)
源: Tushare 中继(2026-09-13 起 datahubco+promax, 替代已失效的 gzcloud 代理):
    银行ETF 512800.SH(fund_daily) / 科创50 000688.SH(index_daily) / 大光三股 300308·300502·300394(daily)
流程: 拉近150交易日 → 算 bank_close/tech_close·ma20·r5/optics(close,open,prev_low,ma20)
      → 写 data/systemic_inputs.json → 调 systemic_risk.main() 输出 data/systemic_risk.json
用法: python -u apps/main_line/build_systemic_inputs.py
"""
import os, sys, json, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", "data")


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


def call(api, params, fields):
    """Tushare 中继查询（返回 (fields, items)）。"""
    return _relay().relay_items(api, fields=fields, **(params or {}))

def rows_of(api, ts_code, start, end):
    _, items = call(api, {"ts_code": ts_code, "start_date": start, "end_date": end},
                    "trade_date,open,high,low,close")
    out = {}
    for it in items:
        try:
            out[str(it[0])] = {"open": float(it[1]), "high": float(it[2]), "low": float(it[3]), "close": float(it[4])}
        except Exception:
            continue
    return out

def to_list(m, key):
    ds = sorted(m)
    return [m[d][key] for d in ds]

def ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None

def main():
    end = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=160)).strftime("%Y%m%d")
    bank = rows_of("fund_daily", "512800.SH", start, end)
    if not bank:
        print("bank fund_daily empty, try 512800 index via fund? fallback none"); bank = {}
    tech = rows_of("index_daily", "000688.SH", start, end)
    bank_close = to_list(bank, "close")
    tech_close = to_list(tech, "close")
    optics = {}
    for code in ("300308.SZ", "300502.SZ", "300394.SZ"):
        d = rows_of("daily", code, start, end)
        if not d: continue
        ds = sorted(d)
        closes = [d[x]["close"] for x in ds]
        lows = [d[x]["low"] for x in ds]
        last = d[ds[-1]]
        prev_low = min(lows[:-1]) if len(lows) > 1 else None
        optics[code] = {"close": last["close"], "open": last["open"],
                        "prev_low": prev_low, "ma20": ma(closes, 20)}
    out = {"bank_close": bank_close[-90:] if bank_close else None,
           "tech_close": tech_close[-1] if tech_close else None,
           "tech_ma20": ma(tech_close, 20) if tech_close else None,
           "tech_r5": None,
           "optics": optics,
           "asof": end}
    if tech_close and len(tech_close) > 5 and tech_close[-6]:
        out["tech_r5"] = round((tech_close[-1] / tech_close[-6] - 1) * 100, 2)
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, "systemic_inputs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE systemic_inputs.json bank_days=", len(bank_close or []), "tech_close=", (tech_close or [None])[-1], "optics=", list(optics.keys()))
    import systemic_risk as sr
    return sr.main()

if __name__ == "__main__":
    raise SystemExit(main())
