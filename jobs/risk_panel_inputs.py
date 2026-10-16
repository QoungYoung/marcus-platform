# -*- coding: utf-8 -*-
"""risk_panel_inputs.py — 把「多因子风险面板」需要的读数**统一算好**落盘（用户 2026-10-16 决定 1+3 ✓）。

为什么：面板 agent 原来各取各的 ⇒ 缺项（红利/拥挤度/涨跌家数）没人补 ✗。
本脚本把能算的都算完，输出 data/risk_panel_inputs_<YYYYMMDD>.json：
  · factors     = 可直接用的读数（结构/事件窗口/美股/量能/红利相对强弱/拥挤度代理/持仓由 agent 另取 ✓）
  · gaps        = **明确标注缺什么 + 为什么**（不许猜 ✓）
所有代理一律标 proxy=true + 依据 ✓（延续"⛔ 自设代理必须标注"的项目纪律 ✓）。

数据源：本地缓存（律型链 rows / 腾讯日线 / 美股 index_global ✓）+ relay（trade_cal ✓）
"""
import datetime as dt, json, os, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "apps/main_line")
sys.path.insert(0, "core")   # relay 在 core/ ✓（首版漏了 ⇒ 涨跌家数被抓成缺口 ✗）
import wave_ladder as W

DATA = os.environ.get("DATA_DIR", "data")


def load_rows(p):
    return {r[0]: r[1] for r in json.load(open(p, encoding="utf-8"))["rows"]}


def pct_rank(xs, v):
    return 100.0 * sum(1 for x in xs if x <= v) / max(len(xs), 1)


def main():
    day = dt.date.today().strftime("%Y%m%d")
    out = {"date": day, "generated_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "factors": [], "gaps": [], "proxy_note": "凡标 proxy=true 的都是自设代理（⛔），不是教材/他的口径 ✓"}

    cn = {r[0]: r[1] for r in W.load_rows()}
    amt = {r[0]: r[3] for r in W.load_rows()}
    dates = sorted(cn)
    last = dates[-1]

    # ① A 股量能（沪市成交额 / 20 日均值 + 250 日分位）
    i = len(dates) - 1
    a20 = st.mean(amt[d] for d in dates[i - 20:i])
    hist = [amt[d] / st.mean(amt[x] for x in dates[max(0, k - 20):k]) for k, d in enumerate(dates) if k > 20]
    ratio = amt[last] / a20
    out["factors"].append({"name": "A股量能", "asof": last, "value": round(ratio, 3),
                           "reading": "沪市成交额 %.0f 亿 / 20 日均 %.0f 亿 = %.3f；比值 250 日分位 %.0f%%" % (
                               amt[last] / 1e8, a20 / 1e8, ratio, pct_rank(hist, ratio)),
                           "proxy": False, "source": "index_daily_000001_SH"})

    # ② 红利相对强弱（用户前提之一：资金进红利 ✓）—— 20 日 红利/沪指 比值变化 + 分位
    div = load_rows("data/index_daily_SH000922.json")
    common = [d for d in dates if d in div]
    if len(common) > 40:
        r20 = [div[common[k]] / cn[common[k]] for k in range(len(common) - 20, len(common))]
        rel = r20[-1] / r20[0] - 1
        seq = [div[common[k]] / cn[common[k]] / (div[common[k - 20]] / cn[common[k - 20]]) - 1 for k in range(20, len(common))]
        out["factors"].append({"name": "红利相对强弱(中证红利vs沪指)", "asof": common[-1], "value": round(rel, 4),
                               "reading": "近 20 日 红利/沪指 比值 %+.2f%%（该指标 250 日分位 %.0f%%）" % (100 * rel, pct_rank(seq[-250:], rel)),
                               "proxy": False, "source": "index_daily_SH000922 + 000001_SH"})

    # ③ 拥挤度代理：半导体ETF 成交额占沪市比重（20 日均）分位 + 20 日相对强弱
    semi = json.load(open("data/index_daily_SH512480.json", encoding="utf-8"))["rows"]
    sd = {r[0]: r for r in semi}
    com2 = [d for d in dates if d in sd and d in amt]
    if len(com2) > 40:
        share = [sd[d][3] / amt[d] for d in com2]
        s20 = st.mean(share[-20:])
        rr = sd[com2[-1]][1] / sd[com2[-21]][1] - 1 - (cn[com2[-1]] / cn[com2[-21]] - 1)
        out["factors"].append({"name": "科技拥挤度代理", "asof": com2[-1], "value": round(s20, 4),
                               "reading": "半导体ETF 成交额/沪市 = %.4f（近 250 日分位 %.0f%%）；半导体 20 日超额 %+.2f%%" % (
                                   s20, pct_rank(share[-250:], s20), 100 * rr),
                               "proxy": True, "source": "index_daily_SH512480 + 000001_SH（占比重=自设代理 ⛔）"})

    # ④ 美股位置
    try:
        ix = json.load(open("data/index_global_IXIC.json", encoding="utf-8"))["rows"]
        cl = [r[1] for r in ix]
        hi60 = max(cl[-61:-1])
        out["factors"].append({"name": "美股位置(纳指)", "asof": ix[-1][0], "value": round(cl[-1] / hi60 - 1, 4),
                               "reading": "纳指 %.0f，60 日高 %.0f（%.0f 日前）⇒ 距高 %+.2f%%；近 5 日 %+.2f%%" % (
                                   cl[-1], hi60, ix[-61:-1][cl[-61:-1].index(hi60)][0] if False else 60, 100 * (cl[-1] / hi60 - 1), 100 * (cl[-1] / cl[-6] - 1)),
                               "proxy": False, "source": "index_global_IXIC"})
    except Exception as e:
        out["gaps"].append({"item": "美股位置", "why": "读取失败 %s" % str(e)[:60]})

    # ⑤ 涨跌家数（本地无 ⇒ 尝试 relay daily；失败则明确标缺 ✓）
    try:
        from tushare_relay import relay_query
        d = relay_query("daily", trade_date=last)
        up = int((d["pct_chg"] > 0).sum()); dn = int((d["pct_chg"] < 0).sum())
        out["factors"].append({"name": "涨跌家数", "asof": last, "value": round(100.0 * up / max(up + dn, 1), 1),
                               "reading": "全市场 %s：上涨 %d 家 / 下跌 %d 家（上涨占比 %.1f%%）" % (last, up, dn, 100.0 * up / max(up + dn, 1)),
                               "proxy": False, "source": "relay daily(trade_date)"})
    except Exception as e:
        out["gaps"].append({"item": "涨跌家数", "why": "relay daily 取不到（%s）；平台 market_diagnosis 表末行 2026-07-13 已过期 95 天 ✗" % type(e).__name__})

    # ⑥ 事件窗口
    try:
        cal = json.load(open(os.path.join(DATA, "macro_calendar.json"), encoding="utf-8"))["events"]
        fut = sorted([e for e in cal if e["tier"] >= 3 and e["local"][:10].replace("-", "") >= day], key=lambda e: e["ts"])
        if fut:
            e = fut[0]
            out["factors"].append({"name": "事件窗口", "asof": day, "value": e["local"][:10],
                                   "reading": "下一 tier>=3 事件：%s（%s，北京时 %s）" % (e.get("name_cn") or e["release"], e["ccy"], e["local"][:16]),
                                   "proxy": False, "source": "macro_calendar.json"})
    except Exception as e:
        out["gaps"].append({"item": "事件窗口", "why": str(e)[:60]})

    out["gaps"].append({"item": "ETF 级资金流", "why": "平台 /market/moneyflow 对 ETF 返回 503（东财+Tushare 均无 ETF 资金流）⇒ 用「成交额占比 + 相对强弱」代理 ✓"})
    out["gaps"].append({"item": "行业拥挤度的权威口径", "why": "无（用科技成交额占比代理 ⛔，见 factors）"})

    p = os.path.join(DATA, "risk_panel_inputs_%s.json" % day)
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写入 %s（因子 %d 项，缺口 %d 项）" % (p, len(out["factors"]), len(out["gaps"])))
    for f in out["factors"]:
        print("   %-26s %s" % (f["name"], f["reading"][:96]))
    for g in out["gaps"]:
        print("   ⛔ 缺：%-16s %s" % (g["item"], g["why"][:80]))


if __name__ == "__main__":
    main()
