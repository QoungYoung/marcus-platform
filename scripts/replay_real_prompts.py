# -*- coding: utf-8 -*-
"""② 真实 prompt 回放：WOLF_T_BUY_CORPUS_RULES 的改前/改后一致率（§9.809）

数据：`data/_bt_llm_year_t35d/t_leg/<day>.json` 的 `calls[]`（**真实**生产唤醒报文 13,919 条 ✓）
口径：只取**低吸类**触发（`wolf_zheng_t_buy` 等 ✓），从报文里解析真实字段（量比/日内分位/涨幅/前低/今日最低 ✓）
 · 改前 = **缓存里的真实 reply**（当时没有新判据 ✓）
 · 改后 = 把**生产同一个** `t_bridge._corpus_buy_flags()` 渲染出的【语料判据·低吸】行接进同一报文后重问 LLM ✓
 · oracle = 语料口径（verdict=buy ⇒ exec ✓；verdict=skip ⇒ wait ✓；无结论 ⇒ 不参评 ✓）
"""
import glob
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, os.path.join(HERE, ".."))
LLM = "http://127.0.0.1:13001/chat"
MAX_CASES = int(os.getenv("REPLAY_MAX", "60"))


def _num(pat, text, cast=float):
    m = re.search(pat, text)
    if not m:
        return None
    try:
        return cast(m.group(1))
    except Exception:
        return None


def _parse(prompt):
    out = {}
    out["vol_ratio"] = _num(r"量比=([0-9.]+)", prompt)
    out["day_quantile"] = _num(r"日内分位=([0-9.]+)", prompt)
    out["day_rise_pct"] = _num(r"当日涨幅=([+-]?[0-9.]+)%", prompt)
    out["prev_low"] = _num(r"前低=([0-9.]+)", prompt)
    out["today_low"] = _num(r"今日最低=([0-9.]+)", prompt)
    out["current"] = _num(r"现价=([0-9.]+)", prompt)
    return out


def _action(reply):
    if not reply:
        return ""
    try:
        m = re.search(r"\{.*\}", reply, re.S)
        if m:
            return str(json.loads(m.group(0)).get("action") or "").strip().lower()
    except Exception:
        pass
    low = reply.lower()
    for k in ("exec", "wait", "abandon"):
        if k in low:
            return k
    return ""


def _ask(msg):
    import time as _t
    for _ in range(3):
        try:
            req = urllib.request.Request(LLM, data=json.dumps({"message": msg}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8", "replace")
            try:
                return json.loads(txt).get("reply") or ""
            except Exception:
                return txt
        except Exception:
            _t.sleep(3)
    return ""


def main():
    from app.services import t_bridge as B
    os.environ["WOLF_T_BUY_CORPUS_RULES"] = "1"
    cases = []
    for p in sorted(glob.glob("data/_bt_llm_year_t35d/t_leg/*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for c in (d.get("calls") or []):
            pr = str(c.get("prompt") or "")
            m = re.search(r"【做T触发】\S+\s+(\S+)", pr)
            et = m.group(1) if m else ""
            if not any(k in et for k in ("buy", "dip", "253", "254")):
                continue
            f = _parse(pr)
            if f.get("vol_ratio") is None:
                continue
            trig = {"event_type": et, "trigger_kind": et, "snapshot": f}
            cf = B._corpus_buy_flags(trig)
            if not cf or cf.get("verdict") not in ("buy", "skip"):
                continue
            cases.append({"day": d.get("as_of"), "event_type": et, "fields": f, "cf": cf,
                          "prompt": pr, "reply": str(c.get("reply") or "")})
            if len(cases) >= MAX_CASES:
                break
        if len(cases) >= MAX_CASES:
            break
    print("参评真实用例 = %d 条（低吸类且有语料判据 ✓）" % len(cases))
    rows = []
    for c in cases:
        oracle = "exec" if c["cf"]["verdict"] == "buy" else "wait"
        before = _action(c["reply"])
        block = "【语料判据·低吸】%s ⇒ %s\n" % (
            c["cf"]["why"], "**可接 ✓**（仍按你判断）" if c["cf"]["verdict"] == "buy"
            else "**本次不接 ✗ ⇒ 只能 wait（保留条件等更好位置 ✓，不要 abandon ✗）**")
        # ★ 修正（首轮把判据**追加在报文末尾** ✗ ⇒ 落在"输出决策 JSON"之后 ✓ 与生产位置不符 ✗，
        #   且首轮 40 条 LLM 调用**全部失败**（隧道又不通 ✗）⇒ 改后数字无效 ✗）
        #   生产位置 = 【规则原文】之后、【决策口径】之前 ✓（等价于 checklist 之前 ✓）
        _m = re.search(r"(你是做T决策者：)", c["prompt"])
        msg2 = (c["prompt"][:_m.start()] + block + c["prompt"][_m.start():]) if _m else (block + c["prompt"])
        after = _action(_ask(msg2))
        rows.append((c["day"], c["event_type"], oracle, before or "?", after or "?", c["cf"]["why"][:56]))
    nb = sum(1 for r in rows if r[2] == r[3])
    na = sum(1 for r in rows if r[2] == r[4])
    n = max(1, len(rows))
    print("改前一致率（缓存真实 reply） = %d/%d = %.0f%%" % (nb, len(rows), 100.0 * nb / n))
    print("改后一致率（接入语料判据后重问）= %d/%d = %.0f%%" % (na, len(rows), 100.0 * na / n))
    print("\n%-10s %-22s %-7s %-7s %-7s %s" % ("day", "event", "oracle", "改前", "改后", "语料判据"))
    for r in rows[:40]:
        print("%-10s %-22s %-7s %-7s %-7s %s" % (r[0], r[1], r[2], r[3], r[4], r[5]))
    json.dump([{"day": r[0], "event": r[1], "oracle": r[2], "before": r[3], "after": r[4], "why": r[5]}
               for r in rows], open("docs/replay-real-cases.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n明细 ⇒ docs/replay-real-cases.json")


if __name__ == "__main__":
    main()
