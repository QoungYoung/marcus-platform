# -*- coding: utf-8 -*-
"""用 DSH agent 代人工做每日确认风向标（确认锚 + 接力）。

流程：
  1. 生成 data/core_p05/agent_review_<date>.json（当前 confirmed + proxy top5）
  2. 调用 `dsh --profile headless -`，让 agent 输出 keep/switch
  3. 校验：switch 的目标必须在当日 proxy top5；否则拒绝
  4. 写 confirmed_daily.json / confirmed_changes.csv / agent_confirm_<date>.json
失败时保持 confirmed 不变，不阻塞后续任务。
"""
from __future__ import annotations
import json, os, re, subprocess, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/core_p05")
DAILY = os.path.join(OUT, "confirmed_daily.json")
PROXY = os.path.join(OUT, "subtheme_bellwethers.csv")
CHANGES = os.path.join(OUT, "confirmed_changes.csv")
DSH = os.environ.get("DSH_BIN", "dsh")
TIMEOUT = int(os.environ.get("DSH_AGENT_TIMEOUT", "600"))


def _load_json(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def _proxy_rows():
    import pandas as pd
    try:
        df = pd.read_csv(PROXY, dtype={"date": str, "bellwether_code": str})
    except Exception:
        return None, []
    if df.empty:
        return None, []
    d = str(df.date.max())
    rows = df[df.date == d].to_dict("records")
    return d, rows


def _top5_codes(top5):
    # top5 字段格式：名称(score) / 名称(score)；返回名称，供 agent 参考
    return [re.sub(r"\(.*$", "", x).strip() for x in str(top5 or "").split("/") if x.strip()]


def main():
    daily = _load_json(DAILY) or _load_json(os.path.join(ROOT, "config/core_p05_confirmed.json"), {})
    d, p = _proxy_rows()
    if not d:
        print("no proxy"); return
    items = []
    for it in daily.get("items", []):
        sub = it.get("subtheme")
        row = next((x for x in p if x.get("subtheme") == sub), None)
        if not row:
            continue
        items.append({"subtheme": sub, "current_code": it.get("code"), "current_name": it.get("name"),
                      "proxy_code": row.get("bellwether_code"), "proxy_name": row.get("bellwether_name"),
                      "top5": row.get("top5"), "top5_names": _top5_codes(row.get("top5"))})
    review = {"date": d, "items": items,
              "criterion": "只做信号；默认keep；只有真接力（同子主题、创新高、相对强、旧龙头走弱、广度）才switch"}
    rp = os.path.join(OUT, f"agent_review_{d}.json")
    json.dump(review, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    prompt = f"""你是 Marcus core 线的每日风向标确认员，代人工做确认。工作区：{ROOT}。
读取：{rp}
规范：docs/core-p0-role-spec.md、docs/core-p05-nontech-confirmed.md。
任务：对每个子主题默认 keep；只有明确满足“真接力”才 switch：
- 同子主题/链段
- 连续2日 proxy top1
- 创20日新高
- 5日相对旧风向标更强
- 旧风向标走弱
- 广度没有崩
不要因为单日涨幅、成交额大、跨链大票就 switch。
只输出 JSON，不要 markdown：
{{"date":"{d}","items":[{{"subtheme":"AI芯片","decision":"keep|switch","code":"...","name":"...","reason":"不超过40字"}}]}}
必须覆盖给出的每个子主题；不确定就 keep。
"""
    try:
        proc = subprocess.run([DSH, "--profile", "headless", "-"], input=prompt, text=True,
                              capture_output=True, timeout=TIMEOUT, cwd=ROOT)
        raw = (proc.stdout or "").strip()
        txt = re.sub(r"^```(?:json)?\s*", "", raw); txt = re.sub(r"\s*```\s*$", "", txt)
        ans = json.loads(txt)
    except Exception as e:
        ans = {"items": [], "error": str(e)}
    decisions = {x.get("subtheme"): x for x in ans.get("items", []) if isinstance(x, dict)}
    changes, rejected = [], []
    for it in daily.get("items", []):
        sub = it.get("subtheme"); dec = decisions.get(sub) or {}
        dcs = str(dec.get("decision", "keep")).lower()
        code = dec.get("code") or it.get("code")
        row = next((x for x in p if x.get("subtheme") == sub), None)
        top5_names = _top5_codes(row.get("top5")) if row else []
        allowed = bool(row and code == row.get("bellwether_code")) or (dec.get("name") in top5_names) or (code == it.get("code"))
        if dcs == "switch" and code != it.get("code"):
            if not allowed:
                rejected.append({"subtheme": sub, "code": code, "reason": "not in top5"})
                dec = {"decision": "keep", "code": it.get("code"), "name": it.get("name"), "reason": "agent switch rejected: not in top5"}
            else:
                old = {"code": it.get("code"), "name": it.get("name")}
                it.update({"code": code, "name": dec.get("name") or code})
                changes.append({"date": d, "subtheme": sub, "old_code": old["code"], "old_name": old["name"],
                                "new_code": code, "new_name": dec.get("name") or code,
                                "reason": dec.get("reason", "agent switch")})
    daily = {"date": d, "source": "dsh_agent_confirmation", "items": daily.get("items", [])}
    json.dump(daily, open(DAILY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    audit = {"date": d, "agent_raw": ans, "changes": changes, "rejected": rejected}
    json.dump(audit, open(os.path.join(OUT, f"agent_confirm_{d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if changes:
        import pandas as pd
        df = pd.DataFrame(changes)
        if os.path.exists(CHANGES):
            df = pd.concat([pd.read_csv(CHANGES), df], ignore_index=True)
        df.to_csv(CHANGES, index=False, encoding="utf-8-sig")
    print("date", d, "subs", len(items), "changes", len(changes), "rejected", len(rejected))
    if ans.get("error"): print("agent_error", ans["error"])


if __name__ == "__main__":
    main()
