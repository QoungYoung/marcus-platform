# -*- coding: utf-8 -*-
"""验证：给定同一情形，AI 的动作是否与狼大一致？（§9.804）

方法（三层，都是只读 ✓ 不下单 ✗）：
 A. 用**生产同一套**唤醒消息构造器（`t_bridge.wake_agent`，打桩 urlopen 捕获报文）⇒ 拿到 AI 真正看到的原文 ✓
 B. 把该报文原样喂给**同一个 LLM**（本地隧道 13001 → 服务器 marcus-dsh ✓，就是生产用的那个 ✓）
 C. 对照狼大语料里的 ground truth（情形 → 他当时会做的动作 ✓），算一致率 ✓
另外跑第二个变体：**删掉「默认动作=exec（执行）」**这句 ⇒ 量化"提示词默认偏向"带来的偏差 ✓

用例来源（docs/wolf-daily-log-*.md 原话，逐条见 CASES 的 `src` 字段 ✓）
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

LLM = "http://127.0.0.1:13001/chat"

# ── ground truth：情形（我们系统能看到的数据）→ 狼大的动作 ────────────────────
CASES = [
    {"id": "C1", "leg": "low_buy", "expect": "wait",
     "why": "缩量（量比0.6）+ 板块不动 + 无恐慌 ⇒ 他的原话：这种打架的方向「不玩就对了」",
     "src": "xls: 14:45 缩量下大光动都不动…这种打架的方向你别管基本面 不玩就对了",
     "snap": {"vol_ratio": 0.62, "day_quantile": 48.0, "day_rise_pct": -0.6, "prev_low": 12.30,
              "today_low": 12.28, "amplitude": 1.9, "turnover_rate": 1.1}},
    {"id": "C2", "leg": "low_buy", "expect": "wait",
     "why": "缓跌（未放量、未创新低）⇒ 他的话：急杀可以买，**缓跌不买**",
     "src": "xls: 「急杀可以买 缓跌不买」",
     "snap": {"vol_ratio": 0.95, "day_quantile": 22.0, "day_rise_pct": -1.4, "prev_low": 20.10,
              "today_low": 19.95, "amplitude": 2.4, "turnover_rate": 1.6}},
    {"id": "C3", "leg": "low_buy", "expect": "exec",
     "why": "急杀（放量 + 跌破前低 + 恐慌）⇒ 他的话：急杀可以买 ✓",
     "src": "xls: 「急杀可以买 缓跌不买」+「放量跌破 3828 是底」",
     "snap": {"vol_ratio": 2.8, "day_quantile": 4.0, "day_rise_pct": -4.2, "prev_low": 18.60,
              "today_low": 17.90, "amplitude": 5.6, "turnover_rate": 3.9}},
    {"id": "C4", "leg": "low_buy", "expect": "wait",
     "why": "缩量、量能不够（取量不足）⇒ 他会「等明天更低再打」，不是现在追 ⇒ 现在 wait",
     "src": "xls: 「如果这里量不够…明天我会把剩下正T仓位都打进去」",
     "snap": {"vol_ratio": 0.71, "day_quantile": 35.0, "day_rise_pct": 0.3, "prev_low": 9.80,
              "today_low": 9.72, "amplitude": 2.1, "turnover_rate": 0.9}},
    {"id": "C5", "leg": "high_sell", "expect": "exec",
     "why": "冲高滞涨（日内自高点回落 + 超买 + 高分位）⇒ 他的话：追进来的一震荡就被洗 ⇒ 卖给他",
     "src": "nga: 「接下来就是看你们追不追了…追了 送给你」",
     "snap": {"vol_ratio": 1.35, "day_quantile": 94.9, "day_rise_pct": 3.6, "prev_low": 8.10,
              "today_low": 8.05, "amplitude": 4.8, "turnover_rate": 6.2}},
    {"id": "C6", "leg": "high_sell", "expect": "exec",
     "why": "4-4 顶部区间（浪型 t_only）+ 缩量滞涨 ⇒ 他的话：接近 4-4 高位要震荡 ⇒ 兑现",
     "src": "xls: 「接近4-4第一波高位附近，要震荡一下再冲击高位」",
     "snap": {"vol_ratio": 0.88, "day_quantile": 88.0, "day_rise_pct": 1.2, "prev_low": 15.20,
              "today_low": 15.05, "amplitude": 3.1, "turnover_rate": 2.4}},
    {"id": "C7", "leg": "low_buy", "expect": "wait",
     "why": "当日早盘已大涨的方向去追 ⇒ 他的话：谁去追早盘涨的方向「我会直接耻笑」",
     "src": "xls: 「如果这里去追早上涨的方向的 我会直接耻笑」",
     "snap": {"vol_ratio": 1.1, "day_quantile": 96.0, "day_rise_pct": 6.8, "prev_low": 30.00,
              "today_low": 29.60, "amplitude": 7.4, "turnover_rate": 8.1}},
    {"id": "C8", "leg": "high_sell", "expect": "exec",
     "why": "浮盈 + 高抛卖腿兑现（他的话：这笔钱利用率高 ⇒ 到位就兑现做T）",
     "src": "xls: 「又T入进去 又等下一个3-5个点的机会」",
     "snap": {"vol_ratio": 1.6, "day_quantile": 91.0, "day_rise_pct": 4.4, "prev_low": 11.40,
              "today_low": 11.35, "amplitude": 5.2, "turnover_rate": 5.5}},
]


def _build_message(case: dict) -> str:
    """用生产同一套消息构造器生成唤醒原文（打桩 urlopen 捕获 ✓）。"""
    import urllib.request as _ur
    cap = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"reply": json.dumps({"action": "wait", "reason": "harness"})}).encode()

    def _fake(req, *a, **k):
        try:
            cap["body"] = (req.data or b"").decode("utf-8", "replace")
        except Exception:
            cap["body"] = ""
        return _R()

    orig = _ur.urlopen
    _ur.urlopen = _fake
    try:
        from app.services import t_bridge as B
        # ★ 修正（首轮 8/8 全被「建议价脱节 / 裸空」污染 ✗ ⇒ 是**测试数据**的问题，不是 AI 的问题 ✓）：
        #   现价与建议价对齐同一条腿的价位 ✓；卖腿给 T 仓弹药 ✓（否则必判"裸空 ⇒ abandon"✗）
        # ★ 第二处修正：**现价必须与本用例的前低/今日最低同一价位带** ✓
        #   （首轮/二轮都被"建议价脱节"污染 ✗ —— AI 判得没错 ✓，是测试数据自相矛盾 ✗）
        _px = round(float(case["snap"]["today_low"]) * 1.002, 3)
        _pos = ({"volume": 800, "sellable": 800, "avg_price": round(_px / 1.042, 3), "pnl_pct": 0.042}
                if case["leg"].startswith("high_sell") else
                {"volume": 0, "sellable": 0, "avg_price": 0.0, "pnl_pct": 0.0})
        trig = {"symbol": "SH600000", "event_type": case["leg"], "trigger_kind": case["leg"],
                "current": _px, "suggest_ask_price": _px, "suggest_bid_price": _px,
                "snapshot": {"fields": {"wave": {"level": "d4", "sub_level": "4-2",
                                                 "operation": ("t_only" if case["leg"].startswith("high_sell") else "side"),
                                                 "date": "2026-09-30", "gate": "ALLOWED", "mode": "auto",
                                                 "allowed": True, "why": "卖类不受环境门限制 ✓",
                                                 "text": "d4/4-2 · 档位判定：允许自动 ✓"}}}}
        # 情形数据注入（走 snapshot 的证据通道 ✓）
        trig["snapshot"]["vol_ratio"] = case["snap"]["vol_ratio"]
        trig["snapshot"].update(case["snap"])
        try:
            B.wake_agent(trig, {"position": _pos})     # ★ 持仓弹药必须给 ⇒ 否则卖腿被判"裸空"✗
        except Exception:
            pass
    finally:
        _ur.urlopen = orig
    body = cap.get("body") or ""
    try:
        return json.loads(body).get("message") or ""
    except Exception:
        return ""


def _ask(message: str) -> dict:
    # ★ 重试：隧道/DSH 会话 TTL 会中途掐断（实测 ConnectionResetError ✗）⇒ 重试 2 次 ✓
    import time as _t
    txt = ""
    last = None
    for _try in range(3):
        try:
            req = urllib.request.Request(LLM, data=json.dumps({"message": message}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8", "replace")
            break
        except Exception as _e:
            last = _e
            _t.sleep(3)
    if not txt:
        return {"action": "", "reply": "LLM 调用失败: %s" % str(last)[:80]}
    try:
        reply = json.loads(txt).get("reply") or ""
    except Exception:
        reply = txt
    act = ""
    m = None
    try:
        import re
        m = re.search(r"\{.*\}", reply, re.S)
        if m:
            act = str(json.loads(m.group(0)).get("action") or "").strip().lower()
    except Exception:
        act = ""
    if not act:
        for k in ("exec", "wait", "abandon"):
            if k in (reply or "").lower():
                act = k
                break
    return {"action": act, "reply": (reply or "")[:150]}


def main():
    rows = []
    for c in CASES:
        msg = _build_message(c)
        if not msg:
            rows.append((c["id"], c["expect"], "?", "?", "构造消息失败"))
            continue
        neutral = msg.replace("默认动作=exec（执行）", "").replace("**默认动作=exec**", "")
        r1 = _ask(msg)
        r2 = _ask(neutral)
        rows.append((c["id"], c["expect"], r1["action"] or "?", r2["action"] or "?",
                     (r1["reply"] or "").replace("\n", " ")))
    ok1 = sum(1 for r in rows if r[1] == r[2])
    ok2 = sum(1 for r in rows if r[1] == r[3])
    print("用例数 =", len(rows))
    print("一致率（现行消息，含「默认动作=exec」）: %d/%d = %.0f%%" % (ok1, len(rows), 100.0 * ok1 / len(rows)))
    print("一致率（去掉默认 exec 的中性版）      : %d/%d = %.0f%%" % (ok2, len(rows), 100.0 * ok2 / len(rows)))
    print()
    print("%-4s %-8s %-8s %-8s %s" % ("id", "狼大", "AI现行", "AI中性", "AI 理由（截断）"))
    for r in rows:
        print("%-4s %-8s %-8s %-8s %s" % (r[0], r[1], r[2], r[3], r[4][:70]))


if __name__ == "__main__":
    main()
