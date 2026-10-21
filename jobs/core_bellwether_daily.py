# -*- coding: utf-8 -*-
"""统一每日风向标流水线：agent确认 → 全45状态 → 权限上下文。

proxy 不再进入每日调度，仅保留 build_core_p05.py 做离线研究。
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OUT_P1 = os.path.join(ROOT, "data/core_p1")
STATE = os.path.join(OUT_P1, "bellwether_state_latest.json")
CONTEXT = os.path.join(OUT_P1, "context_latest.json")
PIPELINE = os.path.join(OUT_P1, "daily_pipeline_latest.json")


def run(name, args, timeout=1800):
    cmd = [PY] + args
    t0 = datetime.now()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return {"step": name, "cmd": cmd, "returncode": p.returncode,
            "seconds": round((datetime.now() - t0).total_seconds(), 1),
            "stdout_tail": (p.stdout or "")[-2000:], "stderr_tail": (p.stderr or "")[-2000:]}


def write_context():
    for _p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "app"), ROOT):
        if os.path.isdir(_p): sys.path.insert(0, _p)
    from app.services.wolf_card.bellwether_context import permissions_for_state
    x = json.load(open(STATE, encoding="utf-8"))
    items = []
    for it in x.get("items", []):
        items.append({**it, "permissions": permissions_for_state(it.get("state"), it.get("successor_code"))})
    json.dump({"date": x.get("date"), "source": "unified_all_subthemes", "items": items},
              open(CONTEXT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"step": "context", "n": len(items), "date": x.get("date"), "items": items, "returncode": 0}


def main():
    os.makedirs(OUT_P1, exist_ok=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-agent", action="store_true")
    a = ap.parse_args()
    steps = []
    if not a.skip_agent:
        steps.append(run("agent_confirm", [os.path.join(ROOT, "jobs/agent_confirm_bellwethers.py")], timeout=1200))
    steps.append(run("state", [os.path.join(ROOT, "jobs/build_bellwether_state.py"), "--history"]))
    try:
        ctx = write_context()
        steps.append({k: ctx.get(k) for k in ("step", "n", "returncode")})
        try:
            for _p in (os.path.join(ROOT, "backend"), os.path.join(ROOT, "app"), ROOT):
                if os.path.isdir(_p): sys.path.insert(0, _p)
            from app.services.bellwether_store import upsert_items
            n = upsert_items(ctx["date"], ctx["items"], source="unified_all_subthemes")
            steps.append({"step": "pgsql", "n": n, "returncode": 0})
        except Exception as pe:
            steps.append({"step": "pgsql", "returncode": 1, "error": str(pe)})
    except Exception as e:
        steps.append({"step": "context", "returncode": 1, "error": str(e)})
    out = {"date": datetime.now().strftime("%Y%m%d"), "steps": steps,
           "ok": all(s.get("returncode") == 0 for s in steps)}
    json.dump(out, open(PIPELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    summary = []
    for s in steps:
        d = {k: s.get(k) for k in ("step", "returncode", "seconds")}
        if s.get("returncode") != 0:
            d["stderr_tail"] = str(s.get("stderr_tail") or s.get("error") or "")[-600:]
        summary.append(d)
    print(json.dumps({"ok": out["ok"], "steps": summary}, ensure_ascii=False))
    if not out["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
