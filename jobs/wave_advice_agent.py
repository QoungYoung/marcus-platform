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

# ★ 2026-10-21：**彻底去掉帖子/个人内容** ✗（用户：万一他哪天不更新，我们就完蛋了 ✓）
#   改为读 `config/wave_advice_policy.json` —— **我们自己的操作纪律** ✓（入库 ✓ 版本化 ✓ 每条带出处 ✓）。
#   每条的来源都是本项目自己的产物（档位表 / tier_target_pct / E1~E5 实验结果 ✓），不依赖任何论坛 ✓。
POLICY_FILE = (os.environ.get("WAVE_ADVICE_POLICY") or "").strip() or "config/wave_advice_policy.json"

POLICY_FALLBACK = """（内置兜底 · 无外部文件）
P1 operation=defense ⇒ 买类禁、卖类不受限；总仓 ≤30%
P2 买侧：候选池（PCB/二线光/半导体材料）且 距MA20 ≥ -3% ∧ 量比 ≥1.2 才小仓试探，否则观察
P3 卖侧：反抽进上方未回补缺口且量比<0.9 ⇒ 减；跌破 MA20/分型低点 ⇒ 减
P4 结构只作观察锚（实测无方向优势 ✗），红三兵 10 日唯一弱正向 ✓
P5 取不到就写「缺」✗ 不猜 P6 只读分析 ✓ 不是投资建议"""


def load_policy():
    """→ (policy_text, meta{version, n, src}) ✓"""
    try:
        d = json.load(open(POLICY_FILE, encoding="utf-8"))
        items = d.get("policy") or []
        if not items:
            raise RuntimeError("empty policy")
        lines = ["**本项目自己的操作纪律**（不引用任何个人/论坛 ✓ 每条带编号便于引用）："]
        for it in items:
            lines.append("  [%s] %s：%s" % (it.get("id"), it.get("topic") or "", it.get("text") or ""))
        lines.append("  （来源：%s）" % ("；".join(d.get("sources") or [])[:400]))
        return "\n".join(lines), {"version": d.get("version") or "", "n": len(items)}
    except Exception as e:
        print("[wave-advice] 读政策文件失败（%s）⇒ 用内置兜底 ✗" % str(e)[:80])
        return POLICY_FALLBACK, {"version": "", "n": 0}


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
    f = {"date": "", "indexes": {}, "candidates": [], "wave": None}
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
            "ma20": round(statistics.mean([x["c"] for x in rs[-20:]]), 2),
            "low5": round(min(x["l"] for x in rs[-5:]), 2),
            "hi5": round(max(x["h"] for x in rs[-5:]), 2),
            "structures": [{"kind": k, "action": a, "note": n}
                           for k, a, n in structures(rs, len(rs) - 1)]}
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
                "ma20": round(ma20, 2),
                "low5": round(min(x["l"] for x in rs[-5:]), 2),
                "hi5": round(max(x["h"] for x in rs[-5:]), 2),
                "volx": round(rs[-1]["amt"] / amt20, 2) if amt20 else None,
                "dist_hi60_pct": round((rs[-1]["c"] / hi60 - 1) * 100, 1),
                # ★ 2026-10-21 修：原来**没给结构** ✗ ⇒ agent 只能说"分型低点缺"✗（它守 P5 没猜 ✓ 是对的 ✓）；
                #    now 每个候选都带 structures（note 里有底分/顶分的关键位 ✓）
                "structures": [{"kind": k, "action": a, "note": n}
                               for k, a, n in structures(rs, len(rs) - 1)]})
        except Exception:
            continue
    f["candidates"].sort(key=lambda x: -(x.get("dist_ma20_pct") or -99))
    f["wolf_recent"] = wolf_recent()
    try:
        base = (os.environ.get("MARCUS_API_URL") or "http://localhost:8000/api/v1").rstrip("/")
        with urllib.request.urlopen(base + "/wave/state", timeout=20) as r:
            f["wave"] = (json.loads(r.read().decode("utf-8")) or {}).get("state")
    except Exception as e:
        f["wave"] = {"_error": str(e)[:80]}
    return f


def wolf_recent(days=2, limit=8):
    """读 `data/wolf_posts.json`（由 jobs/wolf_thread_sync.py 抓 ✓）→ 他最近几日发言 ✓。

    ★ 定位（用户 2026-10-21 ✓）：**只作参考** ✓ —— 纪律仍是 `config/wave_advice_policy.json`（我们自己的 ✓）；
      他断更/帖子没了 ⇒ 这里返回空 ✓ **不影响任何东西** ✓（用户：「等他帖子什么时候没了再说」✓）。
    """
    try:
        d = json.load(open(os.path.join(os.environ.get("DATA_DIR", "").strip() or "data",
                                        "wolf_posts.json"), encoding="utf-8"))
        floors = [f for f in (d.get("floors") or []) if f.get("is_op") and f.get("text")]
        import datetime as _dt
        cut = (_dt.date.today() - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
        recent = [f for f in floors if (f.get("date") or "")[:10] >= cut]
        recent = recent[-limit:]
        return [{"date": (f.get("date") or "")[:16], "text": (f.get("text") or "")[:150]} for f in recent]
    except Exception:
        return []


def rules_advice(f):
    """确定性规则版（DSH 不可用时的兜底 ✓ 也用于和 agent 结果并列展示 ✓）"""
    sh = (f.get("indexes") or {}).get("上证") or {}
    vx, gaps = sh.get("volx"), (sh.get("open_gaps") or [])
    cl = sh.get("close")
    out = []
    in_gap = [g for g in gaps if cl and g["lo"] <= cl <= g["hi"]]
    near_gap = [g for g in gaps if cl and cl < g["lo"] and (g["lo"] / cl - 1) * 100 <= 2.0]
    if in_gap and (vx or 9) < 0.9:
        out.append("**减**：上证已反抽进缺口 %s 且量能萎靡（量比 %.2f）✓ 依据 [P3] ①" %
                   ("/".join("%.0f~%.0f" % (g["lo"], g["hi"]) for g in in_gap), vx or 0))
    elif near_gap:
        out.append("**盯卖点**：上方缺口 %s 就在 %.1f%% 内 ✓ 反抽进去且量比 <0.9 ⇒ 减" % (
            "/".join("%.0f~%.0f" % (g["lo"], g["hi"]) for g in near_gap),
            min((g["lo"] / cl - 1) * 100 for g in near_gap)))
    else:
        out.append("卖侧：没有触及上方缺口 ✓ 按纪律继续持有/借反抽减")
    bk, dv = f.get("bank_rel5"), f.get("dividend_rel5")
    if dv is not None and dv <= -1:
        out.append("**换强**：红利 5 日相对 %+.2f%%（弱于大盘 ✓）⇒ 可换到候选方向（PCB/二线光/材料 ✓）；银行 %s" % (
            dv, ("%+.2f%%（没被压，先不动）" % bk) if bk is not None else "缺"))
    cond_fang = (vx or 0) >= 1.2 and (bk is not None and bk <= -1.0)
    if cond_fang:
        out.append("**可加仓（小仓）**：量比 %.2f + 银行 %+.2f%% ⇒ 两个条件满足 ✓（注：闸门现为 defense，买类需你手动确认 [P1]）" % (vx, bk))
    else:
        out.append("**不补 T**：加仓需 量比 ≥1.2 且 银行下跌 —— 今天量比 %s、银行 %s ⇒ 不满足 [P1][P2] ✗" % (
            ("%.2fx" % vx) if vx else "缺", ("%+.2f%%" % bk) if bk is not None else "缺"))
    top = [c for c in (f.get("candidates") or []) if (c.get("dist_ma20_pct") or -99) > -3]
    if top:
        out.append("**观察首选**：%s（距 MA20 %+.2f%%，量比 %.2f）—— [P2] 站上 MA20 且量比 ≥1.2 才谈试探" % (
            "、".join("%s" % c["name"] for c in top[:2]), top[0]["dist_ma20_pct"], top[0].get("volx") or 0))
    else:
        out.append("**候选全在 MA20 下方** ⇒ 按 [P2] 只观察 ✗（宁可错过，不追高）")
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


def llm_advice(f, policy_text=None):
    if policy_text is None:
        policy_text, _m = load_policy()
    prompt = [
        "你是严谨的 A 股复盘助手。请**严格按下面这套纪律**给建议，不要自创逻辑，不要预测点位。",
        "每条建议必须标注引用的纪律编号（如 [P2]）✓；取不到的字段写「缺」✗ 不要猜。",
        "",
        "=== 你可以使用的**只读**工具（2026-10-21 实测生产 DSH 有工具 ✓）===",
        "· `GET http://backend:8000/api/v1/wave/state` —— 当前浪型档位（operation/mode ✓）",
        "· `GET http://backend:8000/api/v1/wave/index-history?days=60` —— 指数K线+逐日浪型（含缺口/结构参考 ✓）",
        "· 读文件 `/app/data/wolf_posts.json` —— 他最近发言（**只作参考** ✓ 与纪律冲突以纪律为准 ✓）",
        "· 分子数据（量价/缺口/候选池）**已经**在下面的 FACTS 里 ✓ 不需要你再取 ✓",
        ("· 持仓：`GET http://backend:8000/api/v1/portfolio/positions` —— **只读** ✓（用户已授权 ✓）"
         if os.environ.get("WAVE_ADVICE_ALLOW_POSITIONS") == "1" else
         "· 持仓：**不要查**（未授权 ✗）—— 涉及「你持有的票」时只说「若持有则…」✓"),
        "⚠️ **严禁任何写操作** ✗：不许 POST/PUT/DELETE、不下单、不改任何档位或文件 ✗。",
        "⚠️ 工具失败就**写「缺」** ✓ 不要重试超过一次 ✗ 不要改用别的接口碰运气 ✗。",
        policy_text,
        "",
        "以下是他**最近的公开发言摘录**（只作参考 ✓；与上面的纪律冲突时**以纪律为准** ✓；为空就是没抓到 ✓）：",
        json.dumps(f.get("wolf_recent") or [], ensure_ascii=False)[:1800],
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
    policy_text, pmeta = load_policy()
    rules = rules_advice(f)
    print("纪律：%d 条 ✓ 版本 %s ｜（**不依赖任何论坛/个人** ✓）" % (pmeta["n"], pmeta["version"] or "-"))
    print("事实：指数 %d 个 ✓ 候选 %d 个 ✓ 浪型 %s" % (
        len(f.get("indexes") or {}), len(f.get("candidates") or []),
        "有" if (f.get("wave") or {}).get("operation") else "缺"))
    print("\n【规则版】\n- " + "\n- ".join(rules))

    body, used = None, False
    if not args.no_llm:
        try:
            body = llm_advice(f, policy_text)
            used = True
            print("\n【agent 版】\n" + body)
        except Exception as e:
            print("\n[wave-advice] agent 不可用（%s）⇒ 走规则版兜底 ✓" % str(e)[:100])

    wave = (f.get("wave") or {})
    head = "🐺 策略建议（%s）｜ 浪型 %s·%s·%s" % (
        f.get("date"), wave.get("level") or "缺", wave.get("sub_level") or "", wave.get("operation") or "")
    head += "\n（纪律：本项目自有 %d 条 ✓ 不依赖任何个人/论坛）" % pmeta["n"]
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
