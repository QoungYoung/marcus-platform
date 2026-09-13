# -*- coding: utf-8 -*-
"""exec_replay_tech.py — 简化执行层回放: 狼大动作日 科技主线 verdict->买/卖+收盘价+简化盈亏。
价格源: Tencent ifzq 日线 kline(覆盖1-8月)。规则(简化): 等权建仓/按收盘成交; 非完整逐单模拟器。"""
import json, urllib.request
IFZQ = "https://ifzq.gtimg.cn/appstock/app/kline/kline"
UA = {"User-Agent": "Mozilla/5.0"}
SYMS = {"sz300308":"中际旭创(光)","sz002371":"北方华创(半导设备)","sh688981":"中芯国际(芯片)","sh601138":"工业富联(算力)","sz000977":"浪潮信息(算力)","sz301018":"申菱环境(液冷)"}
T = ["2026-01-12","2026-01-14","2026-08-12","2026-08-14","2026-08-28"]
def daykline(sym):
    url = IFZQ + "?param=" + sym + ",day,2026-01-01,2026-09-01,320"
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
        data = json.loads(raw)
        node = data.get("data", {}).get(sym, {})
        bars = node.get("day") or node.get("qfqday") or []
        out = {}
        for b in bars:
            try:
                out[str(b[0])] = float(b[2])  # date -> close
            except Exception: pass
        return out
    except Exception as e:
        print(sym, "ERR", str(e)[:80])
        return {}
def main():
    closes = {}
    for sym, nm in SYMS.items():
        closes[sym] = daykline(sym)
        print(sym, nm, "| " + " | ".join("%s=%s" % (t, closes[sym].get(t)) for t in T))
    def px(sym, t):
        return closes[sym].get(t)
    entry = sum(0.10*1000*px(s,"2026-01-12") for s in SYMS if px(s,"2026-01-12"))
    sell14 = sum(0.10*1000*px(s,"2026-01-14") for s in ["sz300308","sz002371"] if px(s,"2026-01-14"))
    rot_sell = 0.10*1000*px("sh688981","2026-08-12") if px("sh688981","2026-08-12") else 0
    val = sum(0.10*1000*px(s,"2026-08-28") for s in ["sh688981","sh601138","sz000977","sz301018"] if px(s,"2026-08-28"))
    val += sell14 + rot_sell
    print("[simplify] 01-12 建仓市值基准: %.0f 元" % entry)
    print("[simplify] 08-28 组合估值(现金+4只): %.0f 元" % val)
    print("[simplify] 相对盈亏: %+.1f%%" % (100.0*(val/entry-1) if entry else 0.0))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
