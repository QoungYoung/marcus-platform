#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mainline_agent_review.py — 主线宇宙 agent 复核（P0.6 试验）。

作用：
  - 读 generated/resolution_report.json + universe_theme.yaml 的候选事实；
  - 读 agent 复核结果 generated/agent_review.json；
  - 做确定性校验（代码必须在候选里、leader/ladder 必须在允许集合里、parent 必须存在）；
  - 输出 agent_review_validated.json / agent_review.md；
  - 默认 shadow：只报告，不改 generated/universe_theme.yaml；
  - --apply 才写 generated/agent_applied_theme.yaml（人工确认后再替换）。

注意：本脚本不调用模型；agent 结果由 subagent/PI 产生。这样 facts 与判断分离，
agent 不取数、不写库、不发明代码。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Set, Tuple

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "config", "mainline", "generated")
sys.path.insert(0, ROOT)
from core.tushare_relay import relay_query  # noqa: E402

_MEMBER_CACHE: Dict[Tuple[str, str], Set[str]] = {}
_ALLM_CACHE: Dict[str, Any] = {}


def load_json(name: str):
    with open(os.path.join(GEN, name), encoding="utf-8") as f:
        return json.load(f)


def load_yaml(name: str):
    with open(os.path.join(GEN, name), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def members_of_candidate(c: Dict[str, Any]) -> Set[str]:
    typ, code = str(c.get("type")), str(c.get("code"))
    key = (typ, code)
    if key in _MEMBER_CACHE:
        return _MEMBER_CACHE[key]
    out: Set[str] = set()
    try:
        if typ in {"sw_l3", "sw_l2"}:
            if "allm" not in _ALLM_CACHE:
                _ALLM_CACHE["allm"] = relay_query("index_member_all", limit=10000)
            allm = _ALLM_CACHE["allm"]
            col = "l3_code" if typ == "sw_l3" else "l2_code"
            out = set(allm[allm[col].astype(str) == code]["ts_code"].astype(str))
        elif typ == "csi":
            for wd in ("20261023", "20260930", "20260630", "20260331"):
                w = relay_query("index_weight", index_code=code, trade_date=wd)
                if w is not None and len(w):
                    out = set(w["con_code"].astype(str))
                    break
        elif typ == "eastmoney":
            m = relay_query("dc_member", ts_code=code, trade_date="20261023", limit=5000)
            if m is not None and len(m):
                out = set(m["con_code"].astype(str))
    except Exception:
        out = set()
    _MEMBER_CACHE[key] = out
    return out


def facts_for(theme_id: str, report: Dict[str, Any], theme_yaml: Dict[str, Any]) -> Dict[str, Any]:
    themes = {t["id"]: t for t in (theme_yaml.get("themes") or []) + (theme_yaml.get("satellite_themes") or [])}
    t = themes[theme_id]
    rr = next(x for x in (report["themes"]["themes"] + report["themes"]["satellite_themes"]) if x["id"] == theme_id)
    res = rr.get("resolution") or {}
    allowed = []
    # generated 里是 leader/ladder；旧字段 reference_* 也兼容
    for key in ("leader", "reference_leader", "derived_leader"):
        x = t.get(key) or res.get(key)
        if x:
            allowed.append(x)
    for key in ("ladder", "reference_ladder", "derived_ladder"):
        for x in (t.get(key) or res.get(key) or []):
            allowed.append(x)
    allowed_codes = {str(x.get("code")) for x in allowed if x.get("code")}
    allowed_names = {str(x.get("name")) for x in allowed if x.get("name")}
    # 允许集合加入“候选来源的成员”，避免把正确核心（如宇树在 BK1184、纯航天在 857411）误判
    for c in (res.get("candidates") or []):
        if int(c.get("members") or 0) > 0:
            allowed_codes |= members_of_candidate(c)
    return {
        "theme_id": theme_id,
        "name": t["name"],
        "parent_track": t.get("parent_track"),
        "parents": t.get("parents") or [],
        "candidates": res.get("candidates") or [],
        "selected": res.get("selected") or [],
        "reference_leader": t.get("leader") or {},
        "reference_ladder": t.get("ladder") or [],
        "derived_leader": res.get("derived_leader") or {},
        "derived_ladder": res.get("derived_ladder") or [],
        "allowed_codes": allowed_codes,
        "allowed_names": allowed_names,
        "caps": t.get("caps") or {},
    }


def validate(facts: Dict[str, Any], r: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """返回 (errors, warnings)。"""
    errs, warns = [], []
    tid = r.get("theme_id")
    if tid != facts["theme_id"]:
        errs.append(f"{tid}: theme_id 不匹配")
    cand_codes = {str(c.get("code")) for c in facts["candidates"]}
    # sources
    srcs = r.get("selected_sources") or []
    if not srcs and not r.get("keep_existing_source"):
        errs.append(f"{tid}: selected_sources 为空")
    if r.get("keep_existing_source"):
        warns.append(f"{tid}: 保留现有 source（无合格候选源）")
    for c in srcs:
        if str(c) not in cand_codes:
            errs.append(f"{tid}: source {c} 不在候选里")
    # parent
    allowed_parents = {str(p.get("track")) for p in facts["parents"] if p.get("track")}
    if facts.get("parent_track"):
        allowed_parents.add(str(facts["parent_track"]))
    if str(r.get("parent_track")) not in allowed_parents:
        errs.append(f"{tid}: parent {r.get('parent_track')} 不在 {sorted(allowed_parents)}")
    # leader/ladder
    leader = r.get("leader") or {}
    if not leader.get("code"):
        errs.append(f"{tid}: 缺 leader")
    else:
        if str(leader["code"]) not in facts["allowed_codes"]:
            errs.append(f"{tid}: leader {leader['code']} 不在允许集合")
    ladder = r.get("ladder") or []
    if not (3 <= len(ladder) <= 5):
        warns.append(f"{tid}: ladder 数量 {len(ladder)}（建议 3-5）")
    seen = set()
    for x in ladder:
        code = str(x.get("code"))
        if code == str(leader.get("code")):
            warns.append(f"{tid}: ladder 含 leader {code}")
        if code in seen:
            warns.append(f"{tid}: ladder 重复 {code}")
        seen.add(code)
        if code not in facts["allowed_codes"]:
            errs.append(f"{tid}: ladder {code} 不在允许集合")
    # confidence / flag
    try:
        conf = float(r.get("confidence"))
        if not (0 <= conf <= 1):
            errs.append(f"{tid}: confidence 越界 {conf}")
    except Exception:
        errs.append(f"{tid}: confidence 非数值")
    if r.get("needs_human"):
        warns.append(f"{tid}: needs_human=true")
    if r.get("data_conflict"):
        warns.append(f"{tid}: data_conflict=true（已按用户策略自动确认，仅审计）")
    return errs, warns


def apply_review(review: List[Dict[str, Any]], theme_yaml: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(theme_yaml))
    by = {t["id"]: t for t in (out.get("themes") or []) + (out.get("satellite_themes") or [])}
    cmap: Dict[str, Dict[str, Any]] = {}
    for x in (report["themes"]["themes"] + report["themes"]["satellite_themes"]):
        for c in (x.get("resolution") or {}).get("candidates") or []:
            if c.get("code"):
                cmap[str(c["code"])] = {"type": c.get("type"), "code": c.get("code"), "name": c.get("name")}
    for r in review:
        t = by.get(r["theme_id"])
        if not t:
            continue
        src_codes = set(r.get("selected_sources") or [])
        old = {s.get("code"): s for s in (t.get("sources") or []) if s.get("code")}
        new_src = []
        for c in (r.get("selected_sources") or []):
            if c in old and old[c].get("type") != "unknown":
                new_src.append(old[c])
            elif c in cmap:
                new_src.append({**cmap[c], "role": "primary" if not new_src else "secondary"})
            else:
                new_src.append({"type": "unknown", "code": c, "name": c, "role": "primary"})
        if new_src:
            t["sources"] = new_src
        t["parent_track"] = r.get("parent_track") or t.get("parent_track")
        t["leader"] = r.get("leader") or t.get("leader")
        t["ladder"] = r.get("ladder") or t.get("ladder")
        t["agent_reviewed"] = True
        t["agent_confidence"] = r.get("confidence")
        t["agent_needs_human"] = bool(r.get("needs_human"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", default=os.path.join(GEN, "agent_review.json"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--promote", action="store_true")
    a = ap.parse_args()
    report = load_json("resolution_report.json")
    theme_yaml = load_yaml("universe_theme.yaml")
    review = json.load(open(a.review, encoding="utf-8"))
    if not isinstance(review, list):
        print("agent_review.json 必须是数组", file=sys.stderr)
        sys.exit(1)

    policy_path = os.path.join(ROOT, "config", "mainline", "review_policy.yaml")
    policy = yaml.safe_load(open(policy_path, encoding="utf-8")) if os.path.exists(policy_path) else {}
    auto_confirm = bool((policy or {}).get("auto_confirm_needs_human"))
    if auto_confirm:
        for r in review:
            if r.get("needs_human"):
                r.setdefault("original_needs_human", True)
                r["needs_human"] = False
                r["confirmed"] = True
                r["confirmed_by"] = "auto_policy"

    all_errs, all_warns = [], []
    rows = []
    for r in review:
        tid = str(r.get("theme_id"))
        try:
            f = facts_for(tid, report, theme_yaml)
        except StopIteration:
            all_errs.append(f"{tid}: 找不到 facts")
            continue
        e, w = validate(f, r)
        all_errs += e
        all_warns += w
        rows.append((tid, r, f, e, w))

    print("theme | sources | parent | leader | conf | needs_human | validate")
    print("---|---|---|---|---:|---|---")
    for tid, r, f, e, w in rows:
        print("%s | %s | %s | %s | %s | %s | %s" % (
            tid, ",".join(r.get("selected_sources") or []), r.get("parent_track"),
            (r.get("leader") or {}).get("name"), r.get("confidence"),
            r.get("needs_human"), "OK" if not e else "FAIL"))
    if all_warns:
        print("\nWARNINGS:")
        for w in all_warns:
            print("  ⚠️", w)
    if all_errs:
        print("\nERRORS:")
        for e in all_errs:
            print("  ❌", e)
    print("\n%s: %d errors, %d warnings, %d themes" % (
        "OK" if not all_errs else "FAIL", len(all_errs), len(all_warns), len(rows)))

    vname = f"{a.prefix}_validated.json" if a.prefix else "agent_review_validated.json"
    aname = f"{a.prefix}_applied_theme.yaml" if a.prefix else "agent_applied_theme.yaml"
    with open(os.path.join(GEN, vname), "w", encoding="utf-8") as f:
        json.dump({"review": review, "errors": all_errs, "warnings": all_warns}, f, ensure_ascii=False, indent=1)
    if a.apply and not all_errs:
        applied = apply_review(review, theme_yaml, report)
        with open(os.path.join(GEN, aname), "w", encoding="utf-8") as f:
            yaml.safe_dump(applied, f, allow_unicode=True, sort_keys=False, width=120)
        print(f"written: generated/{aname}")
        if a.promote:
            src = os.path.join(GEN, aname)
            dst = os.path.join(GEN, "universe_theme.yaml")
            bak = os.path.join(GEN, "universe_theme.before_agent.yaml")
            if not os.path.exists(bak):
                shutil.copy(dst, bak)
            shutil.copy(src, dst)
            print("promoted: generated/universe_theme.yaml (backup: universe_theme.before_agent.yaml)")
    if all_errs:
        sys.exit(1)


if __name__ == "__main__":
    main()
