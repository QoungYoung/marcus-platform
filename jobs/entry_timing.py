# -*- coding: utf-8 -*-
"""入场时机层（entry_timing）—— 2026-10-22 建立 ✓ 依据唯一的正面实证 ✓

## 为什么只有这一条
把狼大的"入场"机械化并逐一检验（2016~2026 上证 ✓）后，**只有市场级共振有正超额** ✓：
  · 个股级单因子（突破/回踩/放量砸盘/流动性事件/结构）⇒ 全部≈0 或跑输随机 ✗
  · **市场级：上证放量 ∧ 当日收阳** ⇒ 5 日超额 **+0.37%** / 20 日 **+0.51%**，
    中位数为正 ✓ 胜率 **61%** ✓ n=222 ✓（2019 年后 8 年全正 ✓ 2016~2018 为负 ✗ 已知局限）
  · 三条同时（放量+银行大跌+指数拉升）⇒ 10 年只有 **3 次** ✗ 无法用

## 规则（事前写死 ✓ 不许调）
  ① 放量：上证当日成交额 / 前 20 日均额 ≥ **1.20**
  ② 收阳：收盘 > 开盘 ∧ 收盘 > 昨收 ∧ (收盘−最低)/(最高−最低) ≥ **0.50**
  ③ 两者同时 ⇒ **触发**
  动作：买宽基 ETF（默认 510300 沪深300ETF；可用 --etf 覆盖）✓
  持有：**20 个交易日** ✓ 到期提示了结（5 日超额扣成本后≈0 ✗ 故取 20 日 ✓）
  仓位：按浪型档位上限（读 data/wave_state.json 的 operation ⇒ defense 30% 等 ✓）

## 输出
  data/entry_timing.json —— 当前持仓信号 + 历史 ✓
  QQ：触发时推一条 ✓ 到期时推一条 ✓

用法::
    python jobs/entry_timing.py --dry             # 只看今天是否触发（不推不写）
    python jobs/entry_timing.py                   # 正常：检查 + 推送 + 写状态
    python jobs/entry_timing.py --backfill 2019   # 回填验证：列出该年起全部信号 + 收益
"""
import argparse
import datetime as dt
import json
import os
import statistics
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query   # noqa: E402

DATA = os.environ.get("DATA_DIR", "").strip() or "data"
STATE = os.path.join(DATA, "entry_timing.json")
VOL_MULT = 1.20      # 放量门槛
UPPER_HALF = 0.50    # 收盘在日内上半区
HOLD_DAYS = 20       # 持有交易日
DEFAULT_ETF = ("510300.SH", "沪深300ETF")


def hist_index(days=80):
    """上证历史日线（不含今天 ✓ 因为 index_daily 当日数据要次日才到 ✗）"""
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=days * 2)).strftime("%Y%m%d")
    df = relay_query("index_daily", ts_code="000001.SH", start_date=start, end_date=end)
    rs = sorted([{"d": str(r["trade_date"]), "o": float(r["open"]), "h": float(r["high"]),
                  "l": float(r["low"]), "c": float(r["close"]), "a": float(r.get("amount") or 0)}
                 for _, r in df.iterrows()], key=lambda x: x["d"])
    today = dt.date.today().strftime("%Y%m%d")
    return [x for x in rs if x["d"] != today]


def today_snapshot():
    """今日上证实时快照（腾讯 ✓ 收盘后即当日收盘 ✓）→ 字段同 hist_index ✓（amt 单位换算成元）"""
    u = "http://qt.gtimg.cn/q=sh000001"
    raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}),
                                 timeout=20).read().decode("gbk", "ignore")
    v = raw.split("=", 1)[1].strip().strip('";').split("~")
    return {"d": v[30][:8], "o": float(v[5]), "h": float(v[33]), "l": float(v[34]),
            # ★ 单位统一：腾讯 v[37] 是**万元** ✓，而 index_daily.amount 是**千元** ✗（差 10 倍）
            #   ⇒ 统一换算成**千元** ✓ 否则量比会错 10 倍（2026-10-22 实测踩坑 ✗：算出 891x ✗）
            "c": float(v[3]), "prev": float(v[4]), "a": float(v[37]) * 10.0}


def evaluate(h, t):
    """→ (volx, ok_volume, ok_close_up, triggered) ✓"""
    if len(h) < 21:
        return None
    a20 = statistics.mean([x["a"] for x in h[-20:]])
    volx = (t["a"] / a20) if a20 else 0.0
    rng = t["h"] - t["l"]
    ok_v = volx >= VOL_MULT
    ok_u = (t["c"] > t["o"]) and (t["c"] > t["prev"]) and (rng > 0 and (t["c"] - t["l"]) / rng >= UPPER_HALF)
    return volx, ok_v, ok_u, bool(ok_v and ok_u)


def wave_cap():
    """浪型档位 → 仓位上限%（读 wave_state.json ✓ 失败给 defense 30 ✓）"""
    try:
        st = json.load(open(os.path.join(DATA, "wave_state.json"), encoding="utf-8"))
        op = str((st or {}).get("operation") or "").strip()
        return {"build": 75, "t_only": 50, "side": 40, "defense": 30, "exit": 0}.get(op, 30), op
    except Exception:
        return 30, "缺"


def etf_quote(code):
    sym = ("sh" if code.endswith(".SH") else "sz") + code[:6]
    u = "http://qt.gtimg.cn/q=" + sym
    raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}),
                                 timeout=20).read().decode("gbk", "ignore")
    v = raw.split("=", 1)[1].strip().strip('";').split("~")
    return {"name": v[1], "close": float(v[3]), "pct": float(v[32])}


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"open": None, "history": []}


def save_state(st):
    try:
        os.makedirs(DATA, exist_ok=True)
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[entry] 写状态失败: %s" % str(e)[:80])


def push(text):
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[entry] 推送跳过：未配置 QQ_BOT_RECIPIENT ✗")
            return False
        ok = bool(send_c2c_message(openid, text[:1100]))
        print("[entry] 推送%s" % ("成功 ✓" if ok else "失败 ✗"))
        return ok
    except Exception as e:
        print("[entry] 推送异常: %s" % str(e)[:100])
        return False


def backfill(year):
    df = relay_query("index_daily", ts_code="000001.SH", start_date="%d0101" % year, end_date="20261231")
    rs = sorted([{"d": str(r["trade_date"]), "o": float(r["open"]), "h": float(r["high"]),
                  "l": float(r["low"]), "c": float(r["close"]), "a": float(r.get("amount") or 0)}
                 for _, r in df.iterrows()], key=lambda x: x["d"])
    n, hit = 0, []
    for i in range(20, len(rs) - 21):
        t = dict(rs[i]); t["prev"] = rs[i-1]["c"]
        r = evaluate(rs[:i], t)
        if not r:
            continue
        n += 1
        if r[3]:
            f5 = (rs[i+5]["c"]/t["c"]-1)*100
            f20 = (rs[i+20]["c"]/t["c"]-1)*100
            hit.append((t["d"], round(r[0], 2), round(f5, 2), round(f20, 2)))
    print("%d 年起：检查 %d 天 ✓ 触发 %d 次" % (year, n, len(hit)))
    for d, vx, f5, f20 in hit:
        print("   %s 量比 %.2fx ⇒ 5日 %+6.2f%% ｜ 20日 %+6.2f%%" % (d, vx, f5, f20))
    if hit:
        a5 = statistics.mean([x[2] for x in hit]); a20 = statistics.mean([x[3] for x in hit])
        w5 = 100.0*sum(1 for x in hit if x[2] > 0)/len(hit)
        print("   平均：5日 %+.2f%%（胜率 %.0f%%）｜ 20日 %+.2f%%" % (a5, w5, a20))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--backfill", type=int)
    ap.add_argument("--etf", default="%s:%s" % DEFAULT_ETF)
    args = ap.parse_args()
    if args.backfill:
        return backfill(args.backfill)

    code, name = args.etf.split(":", 1)
    h = hist_index()
    t = today_snapshot()
    r = evaluate(h, t)
    if not r:
        print("[entry] 历史不足 ✗")
        return 1
    volx, ok_v, ok_u, trig = r
    cap, op = wave_cap()
    print("[entry] %s 上证 %.2f（%+.2f%%）｜成交 %.0f 亿 = 20日均 %.2fx %s｜收阳 %s %s" % (
        t["d"], t["c"], (t["c"]/t["prev"]-1)*100, t["a"]/1e5, volx,
        "✓" if ok_v else "✗", "✓" if ok_u else "✗", "⇒ **触发 ✓**" if trig else "⇒ 不触发 ✗"))
    print("[entry] 浪型档位 %s ⇒ 仓位上限 %d%%" % (op or "缺", cap))

    st = load_state()
    msgs = []
    # ① 到期检查
    op_pos = st.get("open")
    if op_pos:
        try:
            d0 = dt.datetime.strptime(op_pos["signal_date"], "%Y%m%d").date()
            held = len([x for x in h if x["d"] > op_pos["signal_date"]])
            if t["d"] and t["d"] > op_pos["signal_date"]:
                held += 1
            if held >= HOLD_DAYS:
                msgs.append("⏰ 入场持有**到期**（%s 触发，已 %d 个交易日）⇒ 按规则**了结** ✓" % (
                    op_pos["signal_date"], held))
                st["history"].append(dict(op_pos, exit_date=t["d"], exit_close=t["c"], hold_days=held))
                st["open"] = None
            else:
                print("[entry] 有持仓中的信号：%s 触发，已持有 %d/%d 日" % (op_pos["signal_date"], held, HOLD_DAYS))
        except Exception as e:
            print("[entry] 到期检查失败: %s" % str(e)[:80])
    # ② 新信号
    if trig and not st.get("open"):
        try:
            q = etf_quote(code)
        except Exception:
            q = {"name": name, "close": None, "pct": None}
        st["open"] = {"signal_date": t["d"], "etf": code, "etf_name": q.get("name") or name,
                      "etf_close": q.get("close"), "index_close": t["c"], "volx": round(volx, 2),
                      "cap_pct": cap, "hold_days": HOLD_DAYS}
        msgs.append("🎯 **入场信号触发**（放量 %.2fx ∧ 收阳 ✓）\n"
                    "· 标的：%s（%s）现价 %s\n"
                    "· 仓位上限：**%d%%**（浪型 %s）\n"
                    "· 持有：**%d 个交易日**后了结 ✓（20 日口径，5 日超额扣成本≈0 ✗）" % (
                        volx, q.get("name") or name, code, q.get("close"),
                        cap, op or "缺", HOLD_DAYS))
    if msgs:
        text = "🐺 入场时机（%s）\n\n%s\n\n（依据：上证放量≥1.2x ∧ 收阳 ⇒ 5/20 日正超额 ✓ 胜率 61% ✓ n=222 ✓）" % (
            t["d"], "\n\n".join(msgs))
        print("\n" + text)
        if args.dry:
            print("\n（--dry ✓ 不推不写）")
            return 0
        push(text)
        save_state(st)
    else:
        print("[entry] 无新信号 ✓")
        if not args.dry:
            save_state(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
