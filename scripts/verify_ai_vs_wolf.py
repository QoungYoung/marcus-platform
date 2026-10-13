# -*- coding: utf-8 -*-
"""验证 v2：AI 的动作 vs 狼大成文判据（§9.805）

相比 v1（§9.804）的三处修正：
 ① 用例**逐条来自成文规则表**（`docs/PRODUCTION_PIPELINE.md` §11.1 的 23 条规则族 ✓），
    每条带 `src`（原话+日期 ✓）、`scope`（适用范围：大盘级/板块级/个股级 ✓）——
    v1 的错误就是**把「大盘级急杀可买」套到「个股级急杀」** ✗ ⇒ 现在强制写明 ✓
 ② 现价/建议价与用例价位**同一带** ✓；卖腿给 T 仓弹药 ✓（v1 前两轮全被「脱节/裸空」污染 ✗）
 ③ 三变体对比：off（现状）／on（`WOLF_T_BUY_CORPUS_RULES=1`，新增【语料判据·低吸】）／中性（去掉默认 exec）
"""
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, os.path.join(HERE, ".."))

LLM = "http://127.0.0.1:13001/chat"

CASES = [
    {"id": "B1", "leg": "low_buy", "expect": "exec", "scope": "个股级",
     "rule": "R12 低吸：挂前一天低点、缩量才接",
     "src": "2025-03-06「挂前一天的低点 能买进去就做正T」（缩量 ✓）",
     "snap": {"vol_ratio": 0.62, "day_quantile": 30.0, "day_rise_pct": -0.6, "prev_low": 12.30,
              "today_low": 12.28, "amplitude": 1.9, "turnover_rate": 1.1, "sh_drop": -0.2}},
    {"id": "B2", "leg": "low_buy", "expect": "wait", "scope": "个股级",
     "rule": "R12 反面：放量低吸不接",
     "src": "2025-03-06「缩量才接」⇒ 量比>0.9 即放量 ✗",
     "snap": {"vol_ratio": 1.75, "day_quantile": 34.0, "day_rise_pct": -0.4, "prev_low": 20.10,
              "today_low": 20.05, "amplitude": 2.6, "turnover_rate": 2.2, "sh_drop": -0.1}},
    {"id": "B3", "leg": "low_buy", "expect": "wait", "scope": "个股级",
     "rule": "不追早盘已涨的方向",
     "src": "语料「如果这里去追早上涨的方向的 我会直接耻笑」",
     "snap": {"vol_ratio": 1.1, "day_quantile": 96.0, "day_rise_pct": 6.8, "prev_low": 30.00,
              "today_low": 29.60, "amplitude": 7.4, "turnover_rate": 8.1, "sh_drop": 0.6}},
    {"id": "B4", "leg": "low_buy", "expect": "exec", "scope": "大盘+个股级",
     "rule": "R16 急杀可以买（大盘与个股同时急杀）",
     "src": "2025-06-05「急杀可以买，缓跌不买」＋账本：该组合中位 +1.57%、为正 61% ✓",
     "snap": {"vol_ratio": 2.8, "day_quantile": 4.0, "day_rise_pct": -4.2, "prev_low": 18.60,
              "today_low": 17.90, "amplitude": 5.6, "turnover_rate": 3.9, "sh_drop": -1.3}},
    {"id": "B5", "leg": "low_buy", "expect": "wait", "scope": "只个股级",
     "rule": "④ 只个股恐慌放量追跌 ⇒ 不接（≠ 大盘级急杀）",
     "src": "账本：只个股急杀 −0.51%／47% ✗（唯一正期望是大盘+个股同时 ✓）",
     "snap": {"vol_ratio": 2.9, "day_quantile": 6.0, "day_rise_pct": -3.1, "prev_low": 15.20,
              "today_low": 14.80, "amplitude": 4.4, "turnover_rate": 3.3, "sh_drop": -0.1}},
    {"id": "B6", "leg": "low_buy", "expect": "wait", "scope": "个股级",
     "rule": "R16 缓跌不买",
     "src": "2025-06-05「急杀可以买，缓跌不买」",
     "snap": {"vol_ratio": 0.95, "day_quantile": 22.0, "day_rise_pct": -1.4, "prev_low": 20.10,
              "today_low": 20.15, "amplitude": 2.4, "turnover_rate": 1.6, "sh_drop": -0.3}},
    {"id": "S1", "leg": "high_sell", "expect": "exec", "scope": "个股级",
     "rule": "冲高滞涨 ⇒ 兑现（追了 送给你）",
     "src": "nga「接下来就是看你们追不追了…追了 送给你」",
     "snap": {"vol_ratio": 1.35, "day_quantile": 94.9, "day_rise_pct": 3.6, "prev_low": 8.10,
              "today_low": 8.05, "amplitude": 4.8, "turnover_rate": 6.2, "sh_drop": 0.2}},
    {"id": "S2", "leg": "high_sell", "expect": "exec", "scope": "个股级",
     "rule": "4-4 顶部区间 + 缩量滞涨 ⇒ 兑现",
     "src": "xls「接近4-4第一波高位附近，要震荡一下再冲击高位」",
     "snap": {"vol_ratio": 0.88, "day_quantile": 88.0, "day_rise_pct": 1.2, "prev_low": 15.20,
              "today_low": 15.05, "amplitude": 3.1, "turnover_rate": 2.4, "sh_drop": -0.4}},
    {"id": "S3", "leg": "high_sell", "expect": "exec", "scope": "个股级",
     "rule": "R10 兑现：T 目标 3-5 个点",
     "src": "2026-08-13「至少能有吃 3-5个点 的幅度吧」",
     "snap": {"vol_ratio": 1.6, "day_quantile": 91.0, "day_rise_pct": 4.4, "prev_low": 11.40,
              "today_low": 11.35, "amplitude": 5.2, "turnover_rate": 5.5, "sh_drop": 0.1}},
]


def _build_message(case):
    import urllib.request as _ur
    cap = {}

    class _R(object):
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
        _px = round(float(case["snap"]["today_low"]) * 1.002, 3)
        _pos = ({"volume": 800, "sellable": 800, "avg_price": round(_px / 1.042, 3), "pnl_pct": 0.042}
                if case["leg"].startswith("high_sell") else
                {"volume": 0, "sellable": 0, "avg_price": 0.0, "pnl_pct": 0.0})
        _snap = {k: v for k, v in case["snap"].items() if k != "sh_drop"}
        _snap["fields"] = {"wave": {"level": "d4", "sub_level": "4-2",
                                    "operation": ("t_only" if case["leg"].startswith("high_sell") else "side"),
                                    "date": "2026-09-30", "gate": "ALLOWED", "mode": "auto",
                                    "allowed": True, "why": "卖类不受环境门限制 ✓",
                                    "text": "d4/4-2 · 档位判定：允许自动 ✓"},
                           "index": {"sh_drop": case["snap"].get("sh_drop")}}
        trig = {"symbol": "SH600000", "event_type": case["leg"], "trigger_kind": case["leg"],
                "current": _px, "suggest_ask_price": _px, "suggest_bid_price": _px,
                "snapshot": _snap}
        try:
            B.wake_agent(trig, {"position": _pos})
        except Exception:
            pass
    finally:
        _ur.urlopen = orig
    try:
        return json.loads(cap.get("body") or "").get("message") or ""
    except Exception:
        return ""


def _ask(message):
    import time as _t
    txt = ""
    for _ in range(3):
        try:
            req = urllib.request.Request(LLM, data=json.dumps({"message": message}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8", "replace")
            break
        except Exception:
            txt = ""
            _t.sleep(3)
    if not txt:
        return ""
    try:
        reply = json.loads(txt).get("reply") or ""
    except Exception:
        reply = txt
    try:
        m = re.search(r"\{.*\}", reply, re.S)
        if m:
            return str(json.loads(m.group(0)).get("action") or "").strip().lower()
    except Exception:
        pass
    for k in ("exec", "wait", "abandon"):
        if k in (reply or "").lower():
            return k
    return ""


def _run(variant):
    if variant == "on":
        os.environ["WOLF_T_BUY_CORPUS_RULES"] = "1"
    else:
        os.environ.pop("WOLF_T_BUY_CORPUS_RULES", None)
    out = []
    for c in CASES:
        msg = _build_message(c)
        if variant == "neutral":
            msg = msg.replace("默认动作=exec（执行）", "")
        act = _ask(msg) if msg else ""
        out.append({"id": c["id"], "expect": c["expect"], "act": act or "?"})
    return out


def main():
    res = dict((v, _run(v)) for v in ("off", "on", "neutral"))
    n = len(CASES)
    print("用例数 = %d（低吸 6 + 卖腿 3；每条带语料出处与适用范围 ✓）" % n)
    for v, label in (("off", "现状（开关 off）"), ("on", "开关 on（新增语料判据）"),
                     ("neutral", "去掉「默认动作=exec」")):
        ok = sum(1 for r in res[v] if r["expect"] == r["act"])
        print("  %-24s 一致率 %d/%d = %3.0f%%" % (label, ok, n, 100.0 * ok / n))
    print()
    print("%-4s %-8s %-9s %-9s %-9s %s" % ("id", "语料口径", "off", "on", "neutral", "判据 / 出处"))
    for i, c in enumerate(CASES):
        r = [res[v][i]["act"] for v in ("off", "on", "neutral")]
        mk = lambda a: a + (" ✓" if a == c["expect"] else " ✗")
        print("%-4s %-8s %-9s %-9s %-9s %s" % (c["id"], c["expect"], mk(r[0]), mk(r[1]), mk(r[2]),
                                              (c["rule"] + "｜" + c["src"])[:76]))


if __name__ == "__main__":
    main()
