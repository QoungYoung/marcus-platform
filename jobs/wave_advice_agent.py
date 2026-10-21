# -*- coding: utf-8 -*-
"""浪型/策略建议 agent（2026-10-21 ✓）—— 取事实 → 让 DSH agent 结合**狼大的规则**给建议 → 推 QQ。

为什么这么做（用户 2026-10-21：「接入 agent，让他结合工具分析给出建议，推送 QQ」）：
  · 事实由**工具**取（确定性 ✓ 可复算 ✓）：三指数量价/缺口、银行与红利被压程度、候选池位置、
    当前浪型档位、今日结构信号；
  · 建议由 **agent** 出（DSH /chat ✓ 与 event_briefing_agent 同一套路 ✓），prompt 里**必须**带他的规则原文 ✓；
  · **DSH 不可用 ⇒ 走确定性规则版兜底** ✓（绝不静默 ✗，并注明 ✓）。

定位（务必守住 ✓）：**只读分析 + 提示** ✓ —— 不下单 ✗、不改 `wave_state` ✗、不参与闸门 ✗。

用法::
    python jobs/wave_advice_agent.py --dry       # 只打印（含 agent 原文 ✓）
    python jobs/wave_advice_agent.py             # 推 QQ ✓
    python jobs/wave_advice_agent.py --no-llm    # 只跑规则版兜底 ✓
"""
import argparse
import json
import os
import statistics
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "jobs")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query          # noqa: E402
from wave_structure_alert import structures    # noqa: E402  结构判定同源 ✓

INDEXES = [("000001.SH", "上证"), ("399006.SZ", "创业板"), ("000688.SH", "科创50")]
CAND = [("002463.SZ", "沪电股份", "PCB"), ("603228.SH", "景旺电子", "PCB"), ("600183.SH", "生益科技", "PCB"),
        ("002281.SZ", "光迅科技", "二线光"), ("000988.SZ", "华工科技", "二线光"),
        ("600487.SH", "亨通光电", "二线光"), ("002409.SZ", "雅克科技", "材料"),
        ("603688.SH", "石英股份", "材料"), ("603938.SH", "三孚股份", "材料"),
        ("600206.SH", "有研新材", "材料")]

RULES = '''狼大（-阿狼-）的现行规则（原话，务必照此，不要自创）：
1) 定位：这里是**自救段、不是反转**——「要么别做，要么别追。要做就要早做」（10-21 10:18）；「不能 fomo，等右侧第一根黑 K」（10-21 10:03）。
2) 方向：自救资金会在 **PCB、二线光、半导体材料** 三个方向里反复做成本（10-21 10:18）；设备 ETF 逐渐换成这三类（10-21 09:55）。
3) 日内：**下跌不割肉**；**指数放量 2000E + 银行下跌** 时才补仓做正 T（10-21 12:25）。
4) 卖：等指数**反抽到上方未回补缺口**且**量能萎靡**才开始卖（10-21 12:25）。
5) 权重：银行/红利是**被用来压指数**的工具（10-21 10:09/10:18）——「抗完下跌，能涨时就换到更强的」（13:17）。
6) 分型按**位置**翻转：**高位**底分型=减法位 ✗；**破低点之后的**顶分型=加法位 ✓（2026-04-08 原话）。
7) 联动：宁德破位放巨量 ⇒ 半导体和光不可能大跌（保指数的资金会保）（10-21 13:22）。
8) 纪律：结构只是「这一段结束」的观察点，**不是买卖信号**；卖侧永远可以做。'''


def load(code, fn="index_daily", start="20260101"):
    df = relay_query(fn, ts_code=code, start_date=start, end_date="20261031")
    rs = []
    for _, r in (df.iterrows() if df is not None else []):
        rs.append({"d": str(r["trade_date"]), "o": float(r["open"]), "h": float(r["high"]),
                   "l": float(r["low"]), "c": float(r["close"]), "amt": float(r.get("amount") or 0)})
    return sorted(rs, key=lambda x: x["d"])


def open_gaps(rs, n=60):
    out = []
    for k in range(max(1, len(rs) - n), len(rs)):
        if rs[k]["h"] < rs[k - 1]["l"]:
            lo, hi = rs[k]["h"], rs[k - 1]["l"]
            if not any(x["h"] >= hi for x in rs[k + 1:]):
                out.append({"date": rs[k]["d"], "lo": round(lo, 2), "hi": round(hi, 2)})
    return out


def facts():
    f = {"date": "", "indexes": {}, "candidates": [], "structures": [], "wave": None}
    sh = None
    for code, name in INDEXES:
        rs = load(code)
        if len(rs) < 65:
            continue
        f["date"] = rs[-1]["d"]
        amt20 = statistics.mean([x["amt"] for x in rs[-21:-1]])
        hi60 = max(x["h"] for x in rs[-60:])
        f["indexes"][name] = {
            "close": round(rs[-1]["c"], 2), "amt_yi": round(rs[-1]["amt"] / 1e5, 0),
            "volx": round(rs[-1]["amt"] / amt20, 2) if amt20 else None,
            "dist_hi60_pct": round((rs[-1]["c"] / hi60 - 1) * 100, 1),
            "open_gaps": open_gaps(rs)[-3:],
            "structures": [{"kind": k, "action": a} for k, a, _ in structures(rs, len(rs) - 1)],
        }
        if code == "000001.SH":
            sh = rs
    try:
        bk, hl = load("399986.SZ"), load("000922.CSI")
        if sh and len(bk) > 6 and len(hl) > 6:
            r5 = lambda a: (a[-1]["c"] / a[-6]["c"] - 1) * 100
            f["bank_rel5"] = round(r5(bk) - r5(sh), 2)
            f["dividend_rel5"] = round(r5(hl) - r5(sh), 2)
    except Exception:
        pass
    for code, name, grp in CAND:
        try:
            rs = load(code, "daily")
            ma20 = statistics.mean([x["c"] for x in rs[-20:]])
            amt20 = statistics.mean([x["amt"] for x in rs[-21:-1]])
            hi60 = max(x["h"] for x in rs[-60:])
            f["candidates"].append({
                "name": name, "grp": grp, "close": round(rs[-1]["c"], 2),
                "dist_ma20_pct": round((rs[-1]["c"] / ma20 - 1) * 100, 2),
                "volx": round(rs[-1]["amt"] / amt20, 2) if amt20 else None,
                "dist_hi60_pct": round((rs[-1]["c"] / hi60 - 1) * 100, 1)})
        except Exception:
            continue
    f["candidates"].sort(key=lambda x: -(x.get("dist_ma20_pct") or -99))
    # 当前浪型档位（生产接口 ✓ 读不到就缺）
    try:
        base = (os.environ.get("MARCUS_API_URL") or "http://localhost:8000/api/v1").rstrip("/")
        with urllib.request.urlopen(base + "/wave/state", timeout=20) as r:
            f["wave"] = (json.loads(r.read().decode("utf-8")) or {}).get("state")
    except Exception as e:
        f["wave"] = {"_error": str(e)[:80]}
    return f


def rules_advice(f):
    """确定性规则版（DSH 不可用时的兜底 ✓ 也用于和 agent 结果并列展示 ✓）"""
    sh = (f.get("indexes") or {}).get("上证") or {}
    vx, gaps = sh.get("volx"), (sh.get("open_gaps") or [])
    cl = sh.get("close")
    out = []
    in_gap = [g for g in gaps if cl and g["lo"] <= cl <= g["hi"]]
    near_gap = [g for g in gaps if cl and cl < g["lo"] and (g["lo"] / cl - 1) * 100 <= 2.0]
    if in_gap and (vx or 9) < 0.9:
        out.append("**减**：上证已反抽进缺口 %s 且量能萎靡（量比 %.2f）✓ 对应他『反抽到缺口+量萎靡就卖』" %
                   ("/".join("%.0f~%.0f" % (g["lo"], g["hi"]) for g in in_gap), vx or 0))
    elif near_gap:
        out.append("**盯卖点**：上方缺口 %s 就在 %.1f%% 内 ✓ 反抽进去且量比 <0.9 ⇒ 减" % (
            "/".join("%.0f~%.0f" % (g["lo"], g["hi"]) for g in near_gap),
            min((g["lo"] / cl - 1) * 100 for g in near_gap)))
    else:
        out.append("卖侧：没有触及上方缺口 ✓ 按纪律继续持有/借反抽减")
    bk, dv = f.get("bank_rel5"), f.get("dividend_rel5")
    if dv is not None and dv <= -1:
        out.append("**换强**：红利 5 日相对 %+.2f%%（被压 ✓）⇒ 可换到他说的三方向；银行 %s" % (
            dv, ("%+.2f%%（没被压，先不动）" % bk) if bk is not None else "缺"))
    cond_fang = (vx or 0) >= 1.2 and (bk is not None and bk <= -1.0)
    if cond_fang:
        out.append("**可补 T**：指数放量（量比 %.2f）+ 银行下跌（%+.2f%%）⇒ 两个条件都满足 ✓（注：闸门现为 defense，买类需你手动确认）" % (vx, bk))
    else:
        out.append("**不补 T**：他要求『放量 2000E + 银行下跌』—— 今天量比 %s、银行 %s ⇒ 不满足 ✗" % (
            ("%.2fx" % vx) if vx else "缺", ("%+.2f%%" % bk) if bk is not None else "缺"))
    top = [c for c in (f.get("candidates") or []) if (c.get("dist_ma20_pct") or -99) > -3]
    if top:
        out.append("**观察首选**：%s（距 MA20 %+.2f%%，量比 %.2f）—— 站上 MA20 且量比 ≥1.2 才谈右侧" % (
            "、".join("%s" % c["name"] for c in top[:2]), top[0]["dist_ma20_pct"], top[0].get("volx") or 0))
    else:
        out.append("**候选全在 MA20 下方** ⇒ 按他『要么别做、要么别追』⇒ 只观察 ✗")
    return out


def chat_url():
    return (os.environ.get("WAVE_ADVICE_CHAT_URL") or os.environ.get("EVENT_BRIEFING_CHAT_URL")
            or os.environ.get("MAIN_LINE_CHAT_URL") or os.environ.get("PI_SERVER_URL")
            or "http://localhost:3001/chat")


def http_json(url, payload=None, timeout=300):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def llm_advice(f):
    prompt = [
        "你是严谨的 A 股复盘助手。请**严格按下面这位交易者的规则**给建议，不要自创逻辑，不要预测点位。",
        RULES,
        "",
        "=== FACTS（工具取数，确定性 ✓ 缺失就写「缺」）===",
        json.dumps(f, ensure_ascii=False)[:4000],
        "",
        "输出要求（中文，纯文本，不要 markdown 表格）：",
        "1) 第一行写『结论：』+ 一句话（今天该偏防守还是可小仓试探 ✓ 按他的规则判断）；",
        "2) 『今天该做』：最多 3 条，每条必须引用他的规则编号（如 [4]）和目标价位/量能条件；",
        "3) 『不该做』：最多 2 条（尤其是否满足『补 T』的两个条件、是否该追高）；",
        "4) 『止损/失效位』：给出上证与首选候选的关键位；",
        "5) 最后单独一行：『以上为按既定规则的复盘，不是投资建议。』",
        "总字数 500 字以内，不要寒暄。",
    ]
    last = None
    for attempt in (1, 2):
        try:
            out = http_json(chat_url(), {"message": "\n".join(prompt),
                                         "session_id": "waveadvice_%s" % (f.get("date") or "x")})
            txt = str((out or {}).get("reply") or "").strip()
            if txt:
                return txt
            last = RuntimeError("empty reply")
        except Exception as e:
            last = e
            print("[wave-advice] agent 第 %d 次失败: %s" % (attempt, str(e)[:100]))
    raise last


def push(text):
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[wave-advice] 推送跳过：未配置 QQ_BOT_RECIPIENT ✗")
            return False
        ok = True
        for i in range(0, len(text), 1100):
            ok = bool(send_c2c_message(openid, text[i:i + 1100])) and ok
        print("[wave-advice] 推送%s（%d 字）" % ("成功 ✓" if ok else "失败 ✗", len(text)))
        return ok
    except Exception as e:
        print("[wave-advice] 推送异常: %s" % str(e)[:120])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    f = facts()
    rules = rules_advice(f)
    print("事实：指数 %d 个 ✓ 候选 %d 个 ✓ 浪型 %s" % (
        len(f.get("indexes") or {}), len(f.get("candidates") or []),
        "有" if (f.get("wave") or {}).get("operation") else "缺"))
    print("\n【规则版】\n- " + "\n- ".join(rules))

    body, used = None, False
    if not args.no_llm:
        try:
            body = llm_advice(f)
            used = True
            print("\n【agent 版】\n" + body)
        except Exception as e:
            print("\n[wave-advice] agent 不可用（%s）⇒ 走规则版兜底 ✓" % str(e)[:100])

    wave = (f.get("wave") or {})
    head = "🐺 策略建议（%s）｜ 浪型 %s·%s·%s" % (
        f.get("date"), wave.get("level") or "缺", wave.get("sub_level") or "", wave.get("operation") or "")
    if used:
        text = head + "\n\n" + body
    else:
        text = head + "\n\n" + "\n".join("- " + x for x in rules) + \
               "\n\n⚠️ agent（DSH）本次不可用，以上为确定性规则版 ✓（口径同他的原话）\n" \
               "以上为按既定规则的复盘，不是投资建议。"
    print("\n" + text)
    if args.dry:
        print("\n（--dry ✓ 不推送）")
        return 0
    push(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
