# -*- coding: utf-8 -*-
"""eval_event_windows.py — 「事件前减仓规避」到底有没有用？（先验证，后接入 ✓）

关键时区处理（易错 ✗）：FXMacroData 的美国事件按美东时间发布，**北京时间的晚上** ⇒ A 股当天已收盘 ⇒
  **冲击日 = 事件之后的第一个 A 股交易日**；用户说的「发布之前减仓」⇒ 观察窗 = 冲击日之前的 1~2 个交易日。

度量（先声明）：
  · pre2  = 冲击日之前 2 个交易日的指数累计收益（= 减仓能规避掉的那段 ✓）
  · imp   = 冲击日当日收益
  基线 = 全样本同长度窗口的分布 ✓；**安慰剂** = 把每个事件随机 ±k 交易日平移（k=±3/±7/±15 ✓）重算 ⇒ 噪声带 ✓
"""
import glob, json, os, random, statistics as st, sys
sys.path.insert(0, "apps/main_line")
import wave_ladder as W

rows = W.load_rows()
dates = [r[0] for r in rows]
closes = [r[1] for r in rows]
idx = {d: i for i, d in enumerate(dates)}
events = json.load(open("data/macro_calendar.json", encoding="utf-8"))["events"]
hi = [e for e in events if e["tier"] >= 3]
print("指数序列 %s → %s（%d 根）｜ tier≥3 事件 %d 条" % (dates[0], dates[-1], len(dates), len(hi)))


def beijing_date(utc: str) -> str:
    """事件 UTC 时间 → 北京日期（用于找冲击日 ✓）。"""
    import datetime as dt
    t = dt.datetime.fromisoformat(utc.replace("Z", "+00:00"))
    return (t + dt.timedelta(hours=8)).strftime("%Y%m%d")


def impact_i(ev):
    bj = beijing_date(ev["utc"])
    for d in dates:
        if d > bj:
            return idx[d]
    return None


def window_stats(offsets_shift=0, kind="tier3"):
    """返回 (pre2 收益列表, 冲击日收益列表, 事件数)。offsets_shift>0 ⇒ 安慰剂（平移）"""
    pre, imp = [], []
    evs = hi if kind == "tier3" else []
    for ev in evs:
        i = impact_i(ev)
        if i is None or i - 2 < 1 or i + 1 >= len(closes):
            continue
        j = i + offsets_shift
        if j - 2 < 0 or j >= len(closes):
            continue
        pre.append(closes[j - 1] / closes[j - 3] - 1)      # 冲击日前 2 日累计
        imp.append(closes[j] / closes[j - 1] - 1)          # 冲击日当日
    return pre, imp


def summarize(name, xs, base_mean, base_sd, n_all):
    if not xs:
        print("  %-22s 无样本" % name)
        return
    m = st.mean(xs)
    z = (m - base_mean) / (base_sd / (len(xs) ** 0.5)) if base_sd else 0.0
    print("  %-22s n=%-4d 均值 %+.3f%% ｜ 中位 %+.3f%% ｜ 负比例 %.0f%% ｜ vs 基线 z=%+.2f" % (
        name, len(xs), 100 * m, 100 * st.median(xs), 100 * sum(1 for x in xs if x < 0) / len(xs), z))


base_pre = [closes[i] / closes[i - 2] - 1 for i in range(2, len(closes))]
base_imp = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
bp_m, bp_s = st.mean(base_pre), st.pstdev(base_pre)
bi_m, bi_s = st.mean(base_imp), st.pstdev(base_imp)
print("\n基线：2 日窗口 均值 %+.3f%%（sd %.3f%%）｜ 单日 均值 %+.3f%%（sd %.3f%%）" % (
    100 * bp_m, 100 * bp_s, 100 * bi_m, 100 * bi_s))

pre, imp = window_stats()
print("\n=== tier≥3 事件（CPI/FOMC/非农）===")
summarize("事件前 2 日（可规避段）", pre, bp_m, bp_s, len(base_pre))
summarize("冲击日当日", imp, bi_m, bi_s, len(base_imp))

random.seed(7)
print("\n=== 安慰剂（把事件日随机平移，看噪声带 ✓）===")
for shift in (3, 7, 15):
    ps = []
    for k in range(30):
        s = random.choice([-1, 1]) * shift + random.randint(-2, 2)
        a, _ = window_stats(s)
        if a:
            ps.append(st.mean(a))
    if ps:
        print("  平移±%2d 日：30 次重抽 均值 %.3f%% ~ %.3f%%（中位 %.3f%%）" % (
            shift, 100 * min(ps), 100 * max(ps), 100 * st.median(ps)))

pos = glob.glob("data/_wolf_pos*.json") + glob.glob("data/*pos_series*.json")
print("\n他报出的仓位序列文件：", pos or "（未找到 ✗ —— E1d 的 mine_wolf_pos_series 可能输出到别处）")
