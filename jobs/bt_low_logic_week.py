# -*- coding: utf-8 -*-
"""bt_low_logic_week.py — 回测里 `low_logic.json` 的「按周承载 + 跨周因果」接线。

## 生产语义（为什么必须"按周 + 跨周"）
`config/tasks.yaml` → `id: position_judge`（`cron: "20 8 * * mon"` = **每周一 08:20**）→
`apps/main_line/position_judge.py`：
    `position_class.main()`   → **先读** `low_logic.json`（`position_class.py:124 read_logic()`）
    `low_logic_agent.main()`  → **后写**（整份替换）`low_logic.json`
    `stock_confirm_judge.main()` →（不读 low_logic；全库只有 position_class 一个消费方）
⇒ ① 这份 map 每周只在**周一 08:20 更新一次**；② 因为"先读后写"，周一写出的 map 只影响**下一次**
   （下周一）的 position_class = **跨周因果**。

## 本模块做的 3 件事（全部只写**沙箱内**文件，绝不碰 repo `data/`）
  ① **carry（承载）**：把"截至当日之前、最近一次 position_judge 产出的 map"复制成本沙箱的
     `low_logic.json`（**真文件**：先 `_materialize` 解链，防 2026-09-17 那种"跟随软链写穿 repo"）。
  ② **agent（周内不重算）**：**只有周一**才跑 `jobs/bt_low_logic_asof.py`（record/replay 由 `--mode`/`BT_LLM_MODE`
     决定），产物落在本沙箱的旁挂文件 `low_logic_written.json` ——**不覆盖当天在用的 map**（因为当天
     position_class 已经在 08:20 之前读过了），供**下一周**的 carry 取用。
  ③ **provenance（审计）**：沙箱内 `low_logic_provenance.json` 回答两个问题：
     "今天用的是哪一天的 map（md5 / 来源 / 还是不是 repo 9 月 stub）"、"今天有没有写出新产品、给哪一周用"。

## 产品（product）的两种存放形态（扫描口径，见 `_products()`）
  · `sidecar`：`<沙箱>/low_logic_written.json`（本接线跑出来的标准形态）
  · `inline` ：`<沙箱>/low_logic.json` 是**真文件**、**没有** provenance、且 md5 ≠ repo stub
    → 承认"有人手工/旧流程直接 `bt_low_logic_asof.py --day X`（默认写 low_logic.json）"的产物
  ⚠️ 一旦沙箱有 `low_logic_provenance.json`，它的 `low_logic.json` 就**不再被当成产品**（那是 carry 出来的
     继承副本，不是当日新产物）——否则"上周的 map"会被逐日重复计入，把周一的新产品挤掉。

## 口径开关 `--effective`
  · `prod`（默认）：只用**上一个 ISO 周或更早**写出的产品 = 生产跨周因果。
  · `same_week`：允许用**本周内**（当天之前）写出的产品 = "即时生效"口径（**与生产不符**，仅做敏感性对比）。

## 用法
```sh
# 只做 carry（幂等；= bt_seed_day 在跑 position_class 之前要做的事）
.venv/bin/python jobs/bt_low_logic_week.py --day 20260601 --root data/_bt_year --do carry

# carry + 周一跑 agent（record/replay 由 --mode 决定）；非周一自动只 carry
.venv/bin/python jobs/bt_low_logic_week.py --day 20260601 --root data/_bt_year --do carry --do agent --mode replay

# 只看审计（不改任何文件）
.venv/bin/python jobs/bt_low_logic_week.py --day 20260601 --root data/_bt_year --do audit

# 就地重算 position_class 链（只给"旧 seed 造的沙箱"用：它的 position_class_result.json 是旧 map 口径）
.venv/bin/python jobs/bt_low_logic_week.py --day 20260601 --root data/_bt_year --do carry --do regen
```
env：`BT_LLM_MODE` / `BT_LLM_CACHE` / `BT_AGENT_CHAT_URL`（透传给 `bt_low_logic_asof.py`）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path[:0] = []
import bt_env  # noqa: E402


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

bt_env.add_paths()

REPO = bt_env.REPO
REPO_DATA = os.path.join(REPO, "data")          # 恒指 repo 的 data/（**不是** DATA_DIR 沙箱）
CACHE_DEFAULT = os.path.join(REPO_DATA, "_bt_llm_year2")
DAY_RE = re.compile(r"^20\d{6}$")
PRODUCT = "low_logic_written.json"              # 旁挂产品（周一的产出）
PROV = "low_logic_provenance.json"              # 审计：当天用的是哪份 map
REPORT_DIR = "_low_logic_asof"                  # <root>/_low_logic_asof/asof_<day>.json（报告，不进沙箱）
WD = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


# ── 基础工具 ────────────────────────────────────────────────────────────────
def _jload(p, dflt=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dflt


def _jdump(p, obj):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _md5(p):
    try:
        with open(p, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return "?"


def _materialize(dst: str) -> bool:
    """**写前解链**（复用 `bt_seed_day._materialize` 的同一实现）：软链会跟随写到 repo 真文件上。"""
    try:
        import bt_seed_day  # noqa: WPS433
        return bt_seed_day._materialize(dst)
    except Exception:
        try:
            if os.path.islink(dst):
                os.unlink(dst)
                return True
        except OSError as _e_sil1:
            _silent_alert("bt_low_logic_week.py:110", _e_sil1)
        return False


def repo_stub_md5() -> str:
    """repo `data/low_logic.json`（= 2026-09-14 当期判断被套到全年的那份 stub）的 md5。"""
    return _md5(os.path.join(REPO_DATA, "low_logic.json"))


def d8_to_date(day: str) -> _dt.date:
    return _dt.date(int(day[:4]), int(day[4:6]), int(day[6:8]))


def weekday(day: str) -> int:
    return d8_to_date(day).weekday()


def is_monday(day: str) -> bool:
    return weekday(day) == 0


def iso_week(day: str) -> tuple:
    y, w, _ = d8_to_date(day).isocalendar()
    return (y, w)


def iso_week_str(day: str) -> str:
    y, w = iso_week(day)
    return "%d-W%02d" % (y, w)


def monday_of(day: str) -> str:
    return (d8_to_date(day) - _dt.timedelta(days=weekday(day))).strftime("%Y%m%d")


def sandbox_days(root: str) -> list:
    try:
        return sorted(n for n in os.listdir(root) if DAY_RE.match(n) and os.path.isdir(os.path.join(root, n)))
    except OSError:
        return []


# ── 产品发现 ────────────────────────────────────────────────────────────────
def _products(day: str, root: str, effective: str = "prod") -> list:
    """所有"候选产品"：`[(day, path, kind)]`，按 day 升序。

    只认 `day < 当日`；`prod` 口径下再要求"产品所在 ISO 周 < 当日所在 ISO 周"（跨周因果）。
    """
    stub = repo_stub_md5()
    out = []
    for d in sandbox_days(root):
        if d >= day:
            continue
        if effective == "prod" and iso_week(d) >= iso_week(day):
            continue
        side = os.path.join(root, d, PRODUCT)
        if os.path.isfile(side) and os.path.getsize(side) > 2:
            out.append((d, side, "sidecar"))
            continue
        inline = os.path.join(root, d, "low_logic.json")
        if os.path.isfile(inline) and not os.path.islink(inline) \
                and not os.path.exists(os.path.join(root, d, PROV)) and _md5(inline) != stub:
            out.append((d, inline, "inline"))
    return out


def find_product(day: str, root: str, effective: str = "prod"):
    """最近一次 position_judge 的产物（取 day 最大的那份）。没有 → None。"""
    ps = _products(day, root, effective)
    if not ps:
        return None
    ps.sort(key=lambda x: (x[0], x[2] == "sidecar"))
    d, path, kind = ps[-1]
    return {"day": d, "path": path, "kind": kind, "md5": _md5(path), "n": len(_jload(path, {}) or {})}


def bootstrap_source(day: str, root: str):
    """没有任何产品时的兜底：**上一周最后一个交易日**沙箱里的 `low_logic.json`。

    （此时它通常就是 repo 9 月 stub 的副本 —— 会**如实标注** `is_repo_stub=true`，不假装是 as-of 产物。）
    """
    mon = monday_of(day)
    prevs = [d for d in sandbox_days(root) if d < mon]
    if not prevs:
        return None
    src_day = max(prevs)
    src = os.path.join(root, src_day, "low_logic.json")
    kind = "prev_week_sandbox"
    if not os.path.lexists(src):
        src = os.path.join(REPO_DATA, "low_logic.json")
        kind = "repo_stub"
    elif os.path.islink(src):
        kind = "prev_week_sandbox_symlink"      # 软链 → 内容其实来自 repo data/（如实标注）
    return {"day": src_day, "path": src, "kind": kind, "md5": _md5(src), "n": len(_jload(src, {}) or {})}


def resolve_effective(day: str, root: str, effective: str = "prod") -> dict:
    """决定"本日 position_class 应该读到的那份 map"：产品优先，无产品则上一周兜底。"""
    p = find_product(day, root, effective)
    stub = repo_stub_md5()
    if p:
        return {"source_day": p["day"], "source_kind": p["kind"], "source_file": p["path"],
                "md5": p["md5"], "n_concepts": p["n"], "is_repo_stub": p["md5"] == stub,
                "source_iso_week": iso_week_str(p["day"]), "bootstrap": False}
    b = bootstrap_source(day, root)
    if not b:
        f = os.path.join(REPO_DATA, "low_logic.json")
        return {"source_day": None, "source_kind": "repo_stub", "source_file": f,
                "md5": _md5(f), "n_concepts": len(_jload(f, {}) or {}), "is_repo_stub": True,
                "source_iso_week": None, "bootstrap": True}
    return {"source_day": b["day"], "source_kind": b["kind"], "source_file": b["path"],
            "md5": b["md5"], "n_concepts": b["n"], "is_repo_stub": b["md5"] == stub,
            "source_iso_week": iso_week_str(b["day"]), "bootstrap": True}


# ── carry ───────────────────────────────────────────────────────────────────
def carry_into_sandbox(day: str, sb: str, root: str = "", effective: str = "prod",
                       quiet: bool = True, from_seed: bool = False) -> dict:
    """把"本日应该用的 map"落成沙箱里的**真文件**，并写 `low_logic_provenance.json`。

    **幂等**：结果只取决于 (day, root, effective) 与产品文件内容；重复跑得到同一份字节。
    `from_seed=True` 时（由 `bt_seed_day.py` 在跑 position_class **之前**调用）额外记录
    "PINNED 步骤紧随其后会用这份 map 重算 position_class"。
    """
    sb = os.path.abspath(sb)
    root = os.path.abspath(root or os.path.dirname(sb))
    dst = os.path.join(sb, "low_logic.json")
    prev_md5 = _md5(dst) if os.path.lexists(dst) else None
    prev_kind = "SYMLINK→%s" % os.readlink(dst) if os.path.islink(dst) else ("REAL" if prev_md5 else "MISSING")
    eff = resolve_effective(day, root, effective)
    unlinked = _materialize(dst)
    # 用 copyfile（**不**copy2）：dst 的 mtime 必须是"现在"，否则 bt_seed_day 的 `stub_skipped_newer`
    # 判据（dst 比 repo stub 新才跳过覆盖）会失效 → 承载会被 repo 9 月 stub 盖回去。
    shutil.copyfile(eff["source_file"], dst)
    eff["installed_md5"] = _md5(dst)
    assert eff["installed_md5"] == eff["md5"], "carry 后 md5 不一致：%s" % dst
    seed = _jload(os.path.join(sb, "_seed.json"), {}) or {}
    rec = {
        "day": day, "sandbox": sb, "root": root,
        "weekday": weekday(day), "weekday_name": WD[weekday(day)], "iso_week": iso_week_str(day),
        "cut": str(seed.get("cut") or ""),
        "policy": {"effective": effective,
                   "note": "prod=只用上一个 ISO 周或更早写出的 map（生产跨周因果）；"
                           "same_week=本周内即时生效（与生产不符，仅敏感性对比）"},
        "effective_map": eff,
        "before": {"md5": prev_md5, "kind": prev_kind, "unlinked_symlink": unlinked},
        "applies_to": "本日 position_class（seed/PINNED 会用它重算 position_class_result.json）",
        "carried_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if from_seed:
        rec["position_class_regen"] = "bt_seed_day 的 PINNED 步骤紧随其后（本次 seed 会用这份 map 重算）"
    else:
        rec["position_class_regen"] = ("沙箱现有 position_class_result.json 可能是用**上一份** map 算的"
                                       "（旧 seed）；需要时用 `--do regen` 就地重算")
    rec["position_class_stale"] = bool(prev_md5 and prev_md5 != eff["md5"] and not from_seed)
    _write_prov(sb, rec)
    if not quiet:
        print("[lowlogic] carry %s：%s（%s，%s，%d 概念，%s）→ %s"
              % (day, eff["source_day"] or "无来源", eff["source_kind"], str(eff["md5"])[:8],
                 eff["n_concepts"], "⚠️ 仍是 repo stub" if eff["is_repo_stub"] else "非 stub", dst), flush=True)
        if rec["position_class_stale"]:
            print("[lowlogic] ⚠️ %s 沙箱里原 low_logic.json（%s）与新 map 不同 → 该沙箱的 position_class_result.json"
                  " 是旧口径，需重跑 seed（或 --regen-position-class）" % (day, str(prev_md5)[:8]), flush=True)
    return rec


def _write_prov(sb: str, patch: dict) -> dict:
    p = os.path.join(sb, PROV)
    d = _jload(p, {}) or {}
    d.update(patch)
    _jdump(p, d)
    return d


# ── era 检查（该日生产有没有这条链）──────────────────────────────────────────
def era_check(day: str) -> tuple:
    """该日**代码版本树**里有没有 `apps/main_line/low_logic_agent.py`。

    `git log` 实测：`low_logic_agent.py` 与 `position_judge` 任务都是 **2026-09-02** 才入库的
    （commit 36e1e78 / 536c9d2）→ 之前的周一跑它属于**反事实**（会如实标注，不当成 as-of 事实）。
    返回 (ok, reason)：没有该日 rev（信息缺失）→ True（保守不拦），reason 标明 unknown。
    """
    for base in (os.path.join(REPO_DATA, "_bt_code"), os.path.join(REPO_DATA, "_bt_code_year")):
        m = _jload(os.path.join(base, "rev_map.json"), {}) or {}
        rev = ((m.get(day) or {}).get("rev")) or ""
        if not rev:
            continue
        rel = os.path.join(base, "rev_%s" % rev, "apps/main_line/low_logic_agent.py")
        return (os.path.exists(rel), "rev_%s(agent_%s)" % (rev[:8], "in" if os.path.exists(rel) else "absent"))
    return (True, "unknown(该日无代码版本树→不拦，反事实)")


# ── 周一的 agent 跑（周内不重算）─────────────────────────────────────────────
def run_agent(day: str, sb: str, root: str = "", mode: str = "", cache: str = "", url: str = "",
              force: bool = False, quiet: bool = True) -> dict:
    """**只在周一**跑 `bt_low_logic_asof.py`，产物写到旁挂 `low_logic_written.json`。

    幂等：该日已有产品且没有 `--force-agent` → 直接跳过（重复跑不产生第二次 LLM 调用）。
    """
    sb = os.path.abspath(sb)
    root = os.path.abspath(root or os.path.dirname(sb))
    mode = mode or os.getenv("BT_LLM_MODE", "record")
    cache = cache or os.getenv("BT_LLM_CACHE", CACHE_DEFAULT)
    url = url or os.getenv("BT_AGENT_CHAT_URL", "http://127.0.0.1:13001/chat")
    side = os.path.join(sb, PRODUCT)
    rec = {"ran": False, "mode": mode, "as_of": str((_jload(os.path.join(sb, "_seed.json"), {}) or {}).get("cut") or ""),
           "product_file": side, "force": bool(force)}
    if not is_monday(day):
        rec.update(skip="not_monday(%s)" % WD[weekday(day)],
                   note="周内不重算：沿用上周产物（本日 map 见 effective_map）")
        return rec
    ok, why = era_check(day)
    rec["era"] = why
    if not ok:
        rec.update(skip="era_gate(%s)：该日版本树里没有 low_logic_agent（生产尚未上线）" % why)
        _write_prov(sb, {"written_this_day": rec})
        if not quiet:
            print("[lowlogic] ⏭ %s 周一但 %s → 不跑 agent（反事实日）" % (day, rec["skip"]), flush=True)
        return rec
    if os.path.isfile(side) and os.path.getsize(side) > 2 and not force:
        rec.update(skip="already_written(幂等：该日产品已存在)", product_md5=_md5(side),
                   product_n=len(_jload(side, {}) or {}))
        _write_prov(sb, {"written_this_day": rec})
        if not quiet:
            print("[lowlogic] ⏭ %s 产品已存在（md5=%s）→ 跳过（幂等）"
                  % (day, str(rec["product_md5"])[:8]), flush=True)
        return rec
    rep_path = os.path.join(root, REPORT_DIR, "asof_%s.json" % day)
    cmd = [sys.executable, bt_env.jobs_file("bt_low_logic_asof.py"), "--day", day, "--root", root,
           "--mode", mode, "--cache", cache, "--url", url, "--out", side, "--json", rep_path]
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"}   # DATA_DIR 由 asof 脚本自己按沙箱设
    t0 = time.time()
    rec.update(ran=True, cmd=" ".join(cmd), report=rep_path)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
        rec["rc"] = p.returncode
        rec["tail"] = (p.stderr or p.stdout or "").strip().splitlines()[-6:]
    except Exception as e:
        rec.update(rc=-1, error=str(e)[:200])
    rec["elapsed_s"] = round(time.time() - t0, 1)
    if os.path.isfile(side) and os.path.getsize(side) > 2:
        rec["product_md5"] = _md5(side)
        rec["product_n"] = len(_jload(side, {}) or {})
        nxt = (d8_to_date(monday_of(day)) + _dt.timedelta(days=7)).strftime("%Y%m%d") \
            if is_monday(day) else ""
        rec["applies_to_week"] = "从 %s （下周一）起生效" % nxt
    else:
        rep = _jload(rep_path, {}) or {}
        ar = rep.get("agent_run") or {}
        rec["skip"] = "no_product(agent 未写出：%s)" % (ar.get("skipped") or ar.get("error") or "rc=%s" % rec.get("rc"))
    _write_prov(sb, {"written_this_day": rec, "new_product_effective_from": rec.get("applies_to_week")})
    if not quiet:
        print("[lowlogic] %s 周一 agent：%s | rc=%s %.1fs | 产品 md5=%s n=%s | %s"
              % (day, "✅ 已产出" if rec.get("product_md5") else "⚠️ 无产出", rec.get("rc"),
                 rec["elapsed_s"], str(rec.get("product_md5"))[:8], rec.get("product_n"),
                 rec.get("applies_to_week") or rec.get("skip")), flush=True)
    return rec


def regen_position_class(day: str, sb: str, root: str = "", quiet: bool = True) -> dict:
    """就地重算 `position_class_result.json`（+ 依赖它的 derive_sub_universe / stock_confirm_judge）。

    只在"沙箱是旧 seed 造的（position_class 用的是 stub map）"时需要；跑法与 `bt_seed_day.py` 的 PINNED
    完全同构（`bt_run_pinned.py` + 同一份版本树解析），**只写沙箱**。
    """
    import bt_seed_day
    root = os.path.abspath(root or os.path.dirname(sb))
    cut = str((_jload(os.path.join(sb, "_seed.json"), {}) or {}).get("cut") or "")
    code_dir = ""
    try:
        m = _jload(os.path.join(REPO_DATA, "_bt_code", "rev_map.json"), {}) or {}
        rev = ((m.get(day) or {}).get("rev")) or ""
        d = os.path.join(REPO_DATA, "_bt_code", "rev_%s" % rev) if rev else ""
        code_dir = d if d and os.path.isdir(d) else ""
    except Exception:
        code_dir = ""
    out = {"day": day, "cut": cut, "code_dir": code_dir, "steps": {}}
    for name, script, args in (("position_class_result.json", "apps/main_line/position_class.py", []),
                               ("rotation_sub_universe.json", "apps/main_line/derive_sub_universe.py",
                                ["--refresh-result"]),
                               ("stock_confirm_result.json", "apps/main_line/stock_confirm_judge.py", [])):
        outp = os.path.join(sb, name)
        before = _md5(outp)
        try:
            r = subprocess.run([sys.executable, bt_env.jobs_file("bt_run_pinned.py"), "--as-of", cut,
                                "--data-dir", sb, "--bars-db", os.path.join(REPO_DATA, "_bt_full", "bars.sqlite"),
                                "--code-dir", code_dir,
                                "--script", bt_seed_day.script_path(code_dir, script), "--"] + args,
                               capture_output=True, text=True, timeout=1800, env={**os.environ, "DATA_DIR": sb})
            out["steps"][name] = {"rc": r.returncode, "md5_before": before, "md5_after": _md5(outp),
                                  "changed": before != _md5(outp),
                                  "tail": (r.stderr or r.stdout or "").strip().splitlines()[-3:]}
        except Exception as e:
            out["steps"][name] = {"rc": -1, "err": str(e)[:160]}
        if not quiet:
            s = out["steps"][name]
            print("[lowlogic] regen %s rc=%s changed=%s" % (name, s.get("rc"), s.get("changed")), flush=True)
    _write_prov(sb, {"position_class_regen_done": out})
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────
def audit(day: str, root: str, effective: str = "prod") -> dict:
    sb = os.path.join(root, day)
    prov = _jload(os.path.join(sb, PROV), None)
    prod = _products(day, root, effective)
    return {"day": day, "sandbox": sb, "exists": os.path.isdir(sb), "weekday_name": WD[weekday(day)],
            "iso_week": iso_week_str(day), "provenance": prov,
            "products_before": [{"day": d, "kind": k, "path": p, "md5": _md5(p), "n": len(_jload(p, {}) or {})}
                                for d, p, k in prod[-6:]],
            "candidates_all": [{"day": d, "kind": k} for d, _p, k in _products(day, root, "same_week")[-8:]]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="决策日 T（沙箱目录名，如 20260601）")
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"), help="沙箱根（默认 data/_bt_year）")
    ap.add_argument("--do", action="append", default=None, choices=["carry", "agent", "audit", "regen"],
                    help="要做的步骤，可重复（默认 carry）")
    ap.add_argument("--effective", default=os.getenv("BT_LOW_LOGIC_EFFECTIVE", "prod"),
                    choices=["prod", "same_week"])
    ap.add_argument("--mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    ap.add_argument("--cache", default=os.getenv("BT_LLM_CACHE", CACHE_DEFAULT))
    ap.add_argument("--url", default=os.getenv("BT_AGENT_CHAT_URL", "http://127.0.0.1:13001/chat"))
    ap.add_argument("--force-agent", action="store_true", help="该日已有产品也重跑 agent（默认幂等跳过）")
    ap.add_argument("--json", default="", help="把本次结果写到这个路径")
    a = ap.parse_args()
    a.root = os.path.abspath(a.root)
    sb = os.path.join(a.root, a.day)
    if not os.path.isdir(sb):
        print("⛔ 沙箱不存在：%s（先用 jobs/bt_seed_day.py 造）" % sb, file=sys.stderr)
        return 2
    steps = a.do or ["carry"]
    res = {"day": a.day, "root": a.root, "sandbox": sb, "steps": steps, "effective": a.effective}
    if "audit" in steps:
        res["audit"] = audit(a.day, a.root, a.effective)
        print(json.dumps(res["audit"], ensure_ascii=False, indent=1))
    if "carry" in steps or "agent" in steps:
        res["carry"] = carry_into_sandbox(a.day, sb, a.root, a.effective, quiet="audit" in steps,
                                         from_seed=False)
    if "agent" in steps:
        res["agent"] = run_agent(a.day, sb, a.root, a.mode, a.cache, a.url, a.force_agent,
                                 quiet="audit" in steps)
    if "regen" in steps:
        res["regen"] = regen_position_class(a.day, sb, a.root)
        for _n, _s in (res["regen"].get("steps") or {}).items():
            print("[lowlogic] regen %-28s rc=%s changed=%s" % (_n, _s.get("rc"), _s.get("changed")), flush=True)
    if a.json:
        _jdump(a.json, res)
        print("[lowlogic] 报告 → %s" % a.json, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
