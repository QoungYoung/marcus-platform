# -*- coding: utf-8 -*-
"""B反 的**分钟级提前信号**：「缩量 → 放量砸盘」（2026-10-22 ✓）。

由来（用户 2026-10-22「加"缩量→放量"分钟级信号」✓）：
  · 他的话：「**量能开始缩转放**」（08-12 ✓）、「趁**小跳**买」（08-12 ✓）、
    「破了是好事啊，15 分钟之后只要开始放量就证明钱进来了」（08-22 10:22 ✓）
  · 分钟级实测：588170 在 08-03~08-14 **8/10 天**能出现该信号 ✓（见 .dsh-tmp 实验 ✓）
  · 日线判据 `b_rebound` 要等**离底 3~4%** 才亮 ✗ ⇒ 分钟级可在**当天**就提示 ✓

信号定义（事前写死 ✓）：
  某分钟成交量 ≥ 前 30 分钟均量 × 3  **且** 该分钟收盘 ≤ 前一分钟收盘（砸盘 ✓）
  ⇒ 「缩量后突然放量下跌」= 恐慌盘被接住的瞬间 ✓

用途：**只提示** ✓（QQ 推一条 ✓ 一天一个标的只推一次 ✓）—— 不参与闸门 ✗、不改 wave_state ✗。

用法::
    python jobs/wave_breb_watch.py --backtest 20260803 20260814   # 回测：哪些天有信号 ✓
    python jobs/wave_breb_watch.py --dry                          # 看今天有没有信号（不推 ✓）
    python jobs/wave_breb_watch.py                                # 有信号就推 QQ ✓
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query   # noqa: E402

DATA = os.path.dirname(os.path.join(os.environ.get("DATA_DIR", "").strip() or "data", "x"))
SIGNALS = os.path.join(DATA, "breb_signals.json")
# ★ 2026-10-22：标的改为**配置化**（用户：要监控个股，不是只有 ETF ✓）
WATCHLIST = (os.environ.get("BREB_WATCHLIST") or "").strip() or "config/breb_watchlist.json"


def load_symbols():
    """→ [(code, name)] ✓ 读不到配置就用内置两只 ETF ✓（fail-open ✓）"""
    try:
        d = json.load(open(WATCHLIST, encoding="utf-8"))
        items = [(str(x["code"]), str(x.get("name") or x["code"])) for x in (d.get("items") or [])]
        if items:
            return items
    except Exception as e:
        print("[breb] 读观察池失败（%s）⇒ 用内置 ETF ✓" % str(e)[:80])
    return [("588170.SH", "科创半导体设备(170)"), ("512480.SH", "半导体(512480)")]


SYMBOLS = load_symbols()
VOL_MULT = 3.0          # 放量倍数（相对前 30 分钟均量）
# ★ 2026-10-22 实测结论（用户：「尾盘能检测到 B反 也可以」✓）：
#   早盘窗口（09:30-11:00）命中 0~6/11 ✗ 且总是**晚于当日低点** ✗（低点 35% 在 09:30-10:00 ✓）；
#   尾盘窗口（13:30 之后）在 588170 上 **8/10 天命中** ✓，且 2026-08-03 当天 14:07 就给出信号 ✓
#   （当日低 0.828 / 信号价 0.837 = 差 1% ✓）⇒ **比日线 down_tag 早 2 个交易日** ✓ ⇒ 采用尾盘 ✓
# ★ 2026-10-22 二次修正：窗口从 13:30 → **13:00** ✓
#   13:30 会把 13:01/13:05 那批**真正可执行**的信号过滤掉 ✗（用户实测 13:39 才是可进场时刻 ✓，
#   而 14:00 那根已经是离低 +5~6% ✗）。13:00 起 = 午后开盘第一波 ✓
WIN_START, WIN_END = "13:00", "15:00"
COOL_MIN = 30           # 同一标的两次提示的最小间隔（分钟）


def _tencent_bars(code):
    """腾讯实时分钟（**盘中可用** ✓）→ 增量成交量 ✓

    ★ 2026-10-22 生产实测：`stk_mins` 盘中返回 `minute_data_pending` ✗（分钟数据收盘后才有 ✗）
    ⇒ 盘中必须用实时源 ✓；返回格式 "HHMM price cum_vol cum_amount" ⇒ 增量 = 后减前 ✓
    """
    import urllib.request
    sym = ("sh" if code.endswith(".SH") else "sz") + code[:6]
    u = "https://ifzq.gtimg.cn/appstock/app/minute/query?code=%s" % sym
    raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}),
                                 timeout=20).read().decode("utf-8", "ignore")
    node = (json.loads(raw).get("data") or {}).get(sym) or {}
    arr = ((node.get("data") or {}).get("data")) or []
    out, prev = [], 0.0
    for line in arr:
        f = str(line).split()
        if len(f) < 3:
            continue
        t = f[0]
        t = t[:2] + ":" + t[2:4] if len(t) == 4 else t
        c, cum = float(f[1]), float(f[2])
        out.append({"t": t, "c": c, "v": max(0.0, cum - prev)})
        prev = cum
    return out


def minute_bars(code, day):
    import datetime as _dt
    if day == _dt.datetime.now().strftime("%Y%m%d"):
        try:
            bars = _tencent_bars(code)
            if bars:
                return bars
        except Exception as e:
            print("   腾讯分钟失败 %s ⇒ 回退 stk_mins" % str(e)[:60])
    m = relay_query("stk_mins", ts_code=code, trade_date=day, freq="1min")
    if m is None or not len(m):
        return []
    m = m.sort_values("trade_time").reset_index(drop=True)
    return [{"t": str(r["trade_time"])[11:16], "c": float(r["close"]),
             "v": float(r["vol"] or 0)} for _, r in m.iterrows()]


def detect(bars):
    """→ [(时刻, 价格, 放量倍数)]  —— 缩量后的第一根放量砸盘 ✓"""
    out = []
    last = -10 ** 9
    for i in range(31, len(bars)):
        avg = sum(b["v"] for b in bars[i - 30:i]) / 30.0
        if avg <= 0:
            continue
        x = bars[i]
        if not (WIN_START <= x["t"] <= WIN_END):
            continue
        if x["v"] >= avg * VOL_MULT and x["c"] <= bars[i - 1]["c"]:
            idx = i
            if idx - last < COOL_MIN:
                continue
            last = idx
            out.append((x["t"], x["c"], round(x["v"] / avg, 1)))
    return out


def load_seen():
    try:
        return json.load(open(SIGNALS, encoding="utf-8"))
    except Exception:
        return {}


def save_seen(d):
    try:
        os.makedirs(DATA, exist_ok=True)
        json.dump(d, open(SIGNALS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[breb] 存档失败: %s" % str(e)[:80])


def push(text):
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[breb] 推送跳过：未配置 QQ_BOT_RECIPIENT ✗")
            return False
        ok = bool(send_c2c_message(openid, text[:1100]))
        print("[breb] 推送%s" % ("成功 ✓" if ok else "失败 ✗"))
        return ok
    except Exception as e:
        print("[breb] 推送异常: %s" % str(e)[:100])
        return False


def backtest(d0, d1):
    df = relay_query("fund_daily", ts_code="588170.SH", start_date=d0, end_date=d1)
    days = sorted(str(r["trade_date"]) for _, r in df.iterrows())
    print("回测 %s ~ %s（%d 个交易日 ✓）判据：量 ≥ 前30分钟均量×%.0f 且 该分钟下跌 ✓" % (
        d0, d1, len(days), VOL_MULT))
    hit = 0
    for d in days:
        try:
            bars = minute_bars("588170.SH", d)
        except Exception as e:
            print("  %s 分钟失败 %s" % (d, str(e)[:40]))
            continue
        sig = detect(bars)
        if sig:
            hit += 1
            px0 = bars[0]["c"]
            print("  %s 信号 %d 次 ｜ 首次 %s @ %.3f（日内首 %+.1f%%）｜ 当日收盘 %.3f" % (
                d, len(sig), sig[0][0], sig[0][1], (sig[0][1] / px0 - 1) * 100, bars[-1]["c"]))
        else:
            print("  %s 无信号 ✗" % d)
    print("\n命中 %d/%d 天（%.0f%%）" % (hit, len(days), 100.0 * hit / max(1, len(days))))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", nargs=2, metavar=("D0", "D1"))
    ap.add_argument("--date")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--symbols", help="临时覆盖观察池，逗号分隔，如 002156.SZ,600584.SH ✓")
    args = ap.parse_args()
    global SYMBOLS
    if args.symbols:
        SYMBOLS = [(c.strip(), c.strip()) for c in args.symbols.split(",") if c.strip()]
    if args.backtest:
        return backtest(args.backtest[0], args.backtest[1])

    import datetime as dt
    day = args.date or dt.datetime.now().strftime("%Y%m%d")
    print("检查 %s ｜ 标的 %d 个" % (day, len(SYMBOLS)))
    seen = load_seen()
    new = []
    for code, name in SYMBOLS:
        try:
            bars = minute_bars(code, day)
        except Exception as e:
            print("  %s 取数失败 %s" % (name, str(e)[:50]))
            continue
        if len(bars) < 40:
            print("  %s 数据不足（%d 根）✗" % (name, len(bars)))
            continue
        sig = detect(bars)
        key = "%s|%s" % (day, code)
        if not sig:
            print("  %s 无信号 ✗" % name)
            continue
        t, px, mult = sig[0]   # ★ 用**第一次**（最早可执行 ✓ 2026-10-22 修：原来用 sig[-1] ✗ 报的是 13:59 而首次是 13:01 ✗）
        if key in seen and not args.dry:
            print("  %s 今天已推过 ✓（首次 %s @ %.3f）" % (name, t, px))
            continue
        new.append((name, t, px, mult, sig))
    if not new:
        print("无新信号 ✓")
        return 0
    lines = ["🐺 B反 分钟级提示（%s）" % day]
    for name, t, px, mult, sig in new:
        lines.append("· %s：**首次 %s** 放量 %.1fx 下跌 @ %.3f（今天共 %d 次，最新 %s）" % (
            name, t, mult, px, len(sig), sig[-1][0]))
    lines.append("含义：**尾盘放量砸盘** = 恐慌盘被接住的瞬间（他 08-12「量能开始缩转放」✓）｜实测比日线标签早 0~2 天 ✓ 且价位贴着当日低点 ✓")
    lines.append("⚠️ 只作**提前提示** ✓ 不是买入指令 ✗（B反 确认仍以日线 down_tag 为准 ✓）")
    text = "\n".join(lines)
    print(text)
    if args.dry:
        print("\n（--dry ✓ 不推送）")
        return 0
    push(text)
    for name, t, px, mult, sig in new:
        for code, nm in SYMBOLS:
            if nm == name:
                seen["%s|%s" % (day, code)] = {"t": t, "px": px, "n": len(sig)}
    save_seen(seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
