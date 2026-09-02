# -*- coding: utf-8 -*-
"""systemic_risk.py — 系统性风险联动开关（docs/p2-risk-wolf-logic.md §3）
开关1 银行双头 + 科技不反(09-01"真完了") → 防御
开关2 大光破位大黑K(09-02楼726"彻底止盈就是大光破位大黑K") → 彻底止盈
开关3(预留) 缩量无新增+高位抱团 → 降仓(需两融/量能输入)
输入建议来源: 银行=512800银行ETF / 科技=科创50或半导体ETF / 大光=300308·300502·300394(腾讯qt实时或tushare日线)
输出: data/systemic_risk.json {level, alerts[], advice}
"""
import os, json, datetime

def double_top(close):
    """检测最近双头/M顶: 取序列内两段高点(近端窗口20, span10)相近±5%且现价<双高*0.97"""
    c = [float(x) for x in close if x is not None]
    n = len(c)
    if n < 40: return False, "数据不足"
    span = 10
    highs = []
    for i in range(span, n - 1):
        lo = max(0, i - span); hi = min(n, i + span + 1)
        if c[i] == max(c[lo:hi]) and c[i] >= c[i-1] and c[i] >= c[i+1]:
            highs.append((i, c[i]))
    ded = []
    for i, v in highs:
        if ded and i - ded[-1][0] <= 6:
            if v >= ded[-1][1]: ded[-1] = (i, v)
        else:
            ded.append((i, v))
    recent = [h for h in ded if h[0] >= n - 60]
    if len(recent) >= 2:
        h1, h2 = recent[-2], recent[-1]
        if h1[1] > 0 and 0.95 <= h2[1]/h1[1] <= 1.05 and 3 <= h2[0]-h1[0] <= 30:
            if c[-1] < max(h1[1], h2[1]) * 0.97:
                return True, "银行双头/M顶(现价跌破双高3%)"
    return False, "无双头"

def tech_weak(close, ma20=None, r5=None):
    c = float(close)
    if ma20 is not None and c < float(ma20): return True
    if r5 is not None and float(r5) < 0: return True
    return False

def big_black_break(close, open_, prev_low=None, ma20=None):
    """大阴线(跌幅≥3%)且破位(破前低或MA20)"""
    try:
        o = float(open_); c = float(close)
    except Exception:
        return False
    black = c < o and (o - c) / o >= 0.03
    broke = False
    if prev_low is not None and c < float(prev_low): broke = True
    if ma20 is not None and c < float(ma20): broke = True
    return black and broke

def evaluate(bank_close=None, tech_close=None, tech_ma20=None, tech_r5=None,
             optics=None):
    """optics: {symbol: {close, open, prev_low?, ma20?}}"""
    alerts = []
    level = 0
    if bank_close is not None and tech_close is not None:
        dt, reason = double_top(bank_close)
        tw = tech_weak(tech_close, tech_ma20, tech_r5)
        if dt and tw:
            level = max(level, 1)
            alerts.append("银行双头+科技不反 → 防御(09-01'真完了'): 不抄科技, 减仓防守")
        elif dt:
            alerts.append("银行双头出现但科技未破位: 观察")
    for sym, o in (optics or {}).items():
        if big_black_break(o.get("close"), o.get("open"), o.get("prev_low"), o.get("ma20")):
            level = max(level, 2)
            alerts.append("%s 破位大黑K → 彻底止盈(09-02狼大铁律)" % sym)
    advice = {0: "无系统性风险开关触发", 1: "防御模式: 减仓/不抄科技, 等银行或科技一方出方向", 2: "高优先级: 大光破位大黑K已现 → 彻底止盈离场"}.get(level, "")
    return {"level": level, "alerts": alerts, "advice": advice, "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

def main():
    p = os.path.join(os.environ.get("DATA_DIR", "data"), "systemic_inputs.json")
    if not os.path.exists(p):
        print("缺少 data/systemic_inputs.json（由行情采集写入）"); return 0
    d = json.load(open(p, encoding="utf-8"))
    out = evaluate(bank_close=d.get("bank_close"), tech_close=d.get("tech_close"),
                   tech_ma20=d.get("tech_ma20"), tech_r5=d.get("tech_r5"),
                   optics=d.get("optics"))
    json.dump(out, open(os.path.join(os.environ.get("DATA_DIR", "data"), "systemic_risk.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
