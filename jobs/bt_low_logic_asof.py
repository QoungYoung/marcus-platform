# -*- coding: utf-8 -*-
"""bt_low_logic_asof.py — 按 as-of 重放「低位看逻辑 agent」（`apps/main_line/low_logic_agent.py`）。

## 生产形态（触发链）
`config/tasks.yaml` → `id: position_judge`（name 概念高低位判定，`cron: "20 8 * * mon"`，Asia/Shanghai，
`enabled: true`，script=`apps/main_line/position_judge.py`）：
    `position_judge.py:10`  position_class.main()        → 写 `position_class_result.json`
    `position_judge.py:12`  low_logic_agent.main()       → 对 LOW 概念**一次批量** `/chat` → 写 `low_logic.json`
    `position_judge.py:14`  stock_confirm_judge.main()
    `position_judge.py:18`  rotation_universe.main()
消费方：`position_class.py:123 read_logic()` → `position_class.py:350 logic=read_logic()` →
`classify_one→resonance()`（`position_class.py:216-236` 的 `logic_strong` 信号）。
⚠️ **因果是"跨周"的**：`position_judge` 里 position_class 先跑、low_logic_agent 后跑，
所以本周写出的 `low_logic.json` 只影响**下一次**（下周一）的 position_class 跑，不影响当天那份 result。

## 回测里必须处理的 4 件事
 ① **写穿**：沙箱 `data/_bt_year/<day>/low_logic.json` 是**指向 repo `data/low_logic.json` 的符号链接**
    （实测 162/162 天都是链接），而 agent 用 `json.dump(open(OUT,"w"))` 写 → 会**跟随链接写穿**到 repo
    （= 2026-09-17 那次事故的同一机制）。本脚本写前一律 `_materialize()`（复用 `bt_seed_day._materialize`
    的同一实现：islink → unlink），并有**写穿自检**（跑前跑后比对 repo `data/` 下同名文件的 md5）。
 ② **沙箱**：`DATA_DIR` = `<root>/<day>`，并造 `MARCUS_WORKSPACE` farm（同 `bt_prod_run._farm_root`）→
    生产代码的读写全部落在沙箱，绝不碰 repo `data/`。
 ③ **LLM 非确定** → `jobs/bt_llm_replay.py` 的 `LLMReplay(agent="low_logic", as_of=…)` 录制/回放
    （agent 走 `requests.post` → `install()` 换掉模块里的 `requests`；回放未命中**直接报错**，不静默降级）。
 ④ **钉钟 + 断网**：`bt_run_pinned.pin_clock()`（含 `time.localtime/strftime`）+ relay/gzcloud 替身
    + `bt_local_pro.install_net_offline()`（**回环放行** → 本地 LLM 隧道 127.0.0.1:13001 仍可外呼）。

## 用法
```sh
# 只读预览（**不落盘**）：打印将要写的内容 + 与现有 low_logic.json 的逐字段差异 + 对 position_class 的影响预测
.venv/bin/python jobs/bt_low_logic_asof.py --day 20260601 --stdout --mode replay

# 录制（真外呼一次，结果落"缓存 + 沙箱"）
.venv/bin/python jobs/bt_low_logic_asof.py --day 20260601 --mode record

# 回放（只读缓存；缺缓存/口径漂移按 strict 报错）
.venv/bin/python jobs/bt_low_logic_asof.py --day 20260601 --mode replay

# 显式指定 as-of（默认 = 沙箱 `_seed.json` 的 cut，与 seed 里 position_class 的 as-of 对齐）
.venv/bin/python jobs/bt_low_logic_asof.py --day 20260601 --as-of 20260601 --stdout
```

env：`BT_LLM_MODE` / `BT_LLM_CACHE` / `BT_LLM_STRICT` / `BT_NET_OFFLINE` / `BT_AGENT_CHAT_URL`

## 已知限制（**回放可复现 ≠ as-of 语义完整**）
 · **LLM 工具通道仍是实时的**：dsh `/chat` 那侧可能自带取数工具 → prompt 是 as-of 的，但回答可能引用了
   **现实时点**的数据（与 2026-09-17 交易腿 agent 实测到的 PIT 泄漏同一类）。要彻底钉住必须在**生产侧**
   把工具通道也钉到 as-of（本轮不做）。
 · `wolf_theme_features_v5.csv` 在**本机 repo / 沙箱 / 生产宿主 / 生产容器四处都不存在**（生产 2026-09 起
   就已如此）→ 生产 agent 的 `pd.read_csv` 抛异常被 `except: pass` 吞掉 → prompt 里 `主题催化： {}` 为空。
   本脚本**照生产原样回放（同样为空）**，不补 CSV —— 补了反而与生产口径不一致。
 · `latest_hot_sectors.json` 在沙箱里是**同一个当期快照**（162 天共用，`bt_seed_day` 标
   `live_file_STALE?`）→ prompt 的"热点概念"一段并非严格 as-of（属 seed 层遗留问题，不在本脚本职责内）。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
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
REPO_DATA = os.path.join(REPO, "data")
AGENT_REL = "apps/main_line/low_logic_agent.py"
# 沙箱里这份 agent 会读的输入（第 4 个在本机/生产/沙箱**都不存在**，见 --report 的 wolf_theme_features 段）
INPUTS = ("position_class_result.json", "main_line_state.json", "latest_hot_sectors.json",
          "wolf_theme_features_v5.csv")
# 写穿自检：这三份是 2026-09-17 事故里被"补桩跟随符号链接"改过 mtime 的文件（未被 git 跟踪）
PENETRATION_GUARD = ("low_logic.json", "etf_theme_map_pi.json", "wolf_ticket_ban.json")


def _jload(p, dflt=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dflt


def _md5(p):
    try:
        with open(p, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return "?"


def _materialize(dst: str) -> bool:
    """dst 若是**符号链接** → 先删链再写（与 `bt_seed_day._materialize` 同一实现，避免两处分叉）。

    写穿机制（2026-09-17 实测）：沙箱软链农场把 repo `data/low_logic.json` 链进沙箱，随后
    `json.dump(open(dst,"w"))` **跟随符号链接**写到 repo 真文件上。补桩的本意是"沙箱里落一个真文件"，
    所以必须先解除链接。
    """
    try:
        import bt_seed_day  # noqa: WPS433  复用同一实现
        return bt_seed_day._materialize(dst)
    except Exception:
        try:
            if os.path.islink(dst):
                os.unlink(dst)
                return True
        except OSError as _e_sil1:
            _silent_alert("bt_low_logic_asof.py:113", _e_sil1)
        return False


def _provenance(p: str) -> dict:
    """输入文件来源：真文件 / 符号链接（→ 指向哪 = PIT 泄漏与写穿风险所在）。"""
    if not os.path.lexists(p):
        return {"exists": False}
    info = {"exists": True, "bytes": os.path.getsize(p), "md5": _md5(p)}
    if os.path.islink(p):
        info["kind"] = "SYMLINK"
        info["target"] = os.readlink(p)
        info["into_repo_data"] = os.path.abspath(os.path.join(os.path.dirname(p), info["target"])).startswith(
            os.path.abspath(REPO_DATA))
    else:
        info["kind"] = "REAL"
    return info


def resolve_code_dir(day: str, explicit: str = "") -> str:
    """该日"在跑的代码"版本树（与 `bt_seed_day.py` 同一解析顺序：`data/_bt_code/rev_map.json`）。

    版本树里的 `low_logic_agent.py` 若与 repo 当前版本**逐字节相同**，回放口径不受影响（脚本会打印比对）。
    """
    if explicit:
        return explicit if os.path.isdir(explicit) else ""
    try:
        # 用 REPO_DATA 而不是 bt_env.DATA：后者在 import 时就定了（本脚本会在 main() 里把 DATA_DIR 改成沙箱）
        base = os.path.join(REPO_DATA, "_bt_code")
        m = _jload(os.path.join(base, "rev_map.json"), {}) or {}
        rev = ((m.get(day) or {}).get("rev")) or ""
        d = os.path.join(base, "rev_%s" % rev) if rev else ""
        return d if d and os.path.isdir(d) else ""
    except Exception:
        return ""


def load_agent(path: str):
    """按**路径**加载生产 agent（而不是 `import low_logic_agent`）→ 才能用得上版本树那份。"""
    spec = importlib.util.spec_from_file_location("low_logic_agent_asof", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def low_entries(res: dict) -> list:
    """`low_logic_agent.main()` 的取数口径（逐字复刻 `low_logic_agent.py:27`）。"""
    return [v for v in (res or {}).values()
            if isinstance(v, dict) and v.get("position") == "LOW" and "features" in v]


def field_diff(old: dict, new: dict) -> dict:
    """逐字段差异：新增 / 缺失 / 变更（logic_score、verdict、reason 分开记）。"""
    old = old or {}
    new = new or {}
    changed = {}
    for k in sorted(set(old) & set(new)):
        a, b = old[k] or {}, new[k] or {}
        d = {}
        for f in ("logic_score", "verdict", "reason"):
            if a.get(f) != b.get(f):
                d[f] = [a.get(f), b.get(f)]
        if d:
            changed[k] = d
    return {"old_n": len(old), "new_n": len(new),
            "added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)),
            "changed": changed,
            "identical": sorted(k for k in (set(old) & set(new)) if k not in changed)}


def _logic_strong(logic) -> bool:
    """`position_class.resonance()` 的判据（`position_class.py:216-218`）。"""
    if not logic:
        return False
    ls = logic.get("logic_score")
    lv = logic.get("verdict", "")
    return bool((ls is not None and ls >= 0.6) or lv == "值得埋伏低吸")


def predict_low_actions(res: dict, logic_map: dict) -> dict:
    """按 `resonance()` 的 LOW 分支**复算** action（只把 logic_map 换掉，其余信号取当天 result 里的原值）。

    用途：`low_logic.json` 是**下周一**才被 position_class 读的 → 这里回答"若启用，下一周这份 map
    会把哪些概念的 action/signals 改掉"。自检：用**现有** map 复算应与存档 action 完全一致（matched）。
    """
    out = {"n_low": 0, "selfcheck_matched": 0, "selfcheck_total": 0, "affected": []}
    for v in low_entries(res):
        out["n_low"] += 1
        sig = v.get("signals") or {}
        fe = v.get("features") or {}
        st = fe.get("structure") or {}
        name = v.get("name")
        fsdir = (v.get("fund_flow") or {}).get("dir", "flat")
        macro_op = sig.get("macro") or v.get("op") or "side"
        struct_high = bool(st.get("m_top") or st.get("new_high_pullback"))
        macro_danger = macro_op not in ("build",)
        low_block = macro_op in ("defense", "exit")
        cc = fe.get("confirm_stage") or "下跌中"
        idx = fe.get("index_confirm") or "下跌中"

        def _act(logic):
            ls = _logic_strong(logic)
            ok = (fsdir == "in" and (ls or bool(sig.get("catalyst"))) and not low_block)
            if struct_high and macro_danger:
                return "减仓/只做T", ls
            if ok and cc in ("确认", "突破候选") and idx in ("确认", "突破候选"):
                return "低吸埋伏", ls
            if ok and cc in ("确认", "突破候选"):
                return "观望(埋伏候选,等指数确认)", ls
            if ok:
                return "观望(埋伏候选,等确认链)", ls
            return "观望", ls

        # 用**该概念当下的存档 signals**复算（signals.logic_strong 就是当时那份 map 的结论）
        recalc, _ = _act({"logic_score": 1.0 if sig.get("logic_strong") else 0.0, "verdict": ""})
        out["selfcheck_total"] += 1
        if recalc == v.get("action"):
            out["selfcheck_matched"] += 1
        act_new, ls_new = _act(logic_map.get(name))
        act_none, _ = _act(None)   # 没有任何 low_logic 条目时的 action（对照）
        if act_new != v.get("action") or ls_new != bool(sig.get("logic_strong")):
            out["affected"].append({
                "name": name, "fund": fsdir, "catalyst": bool(sig.get("catalyst")),
                "macro": macro_op, "confirm": cc, "index_confirm": idx,
                "action_stored": v.get("action"), "action_no_map": act_none,
                "action_with_new_map": act_new,
                "logic_strong_stored": bool(sig.get("logic_strong")), "logic_strong_new": ls_new,
                "delta": ("action" if act_new != v.get("action") else "signals_only"),
            })
    return out


def snapshot_guard() -> dict:
    """repo `data/` 下"绝不能变"的三份文件的指纹（写穿自检）。"""
    return {n: _md5(os.path.join(REPO_DATA, n)) for n in PENETRATION_GUARD}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="回测决策日 T（沙箱目录名，如 20260601）")
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"), help="沙箱根（默认 data/_bt_year）")
    ap.add_argument("--as-of", default="", help="LLM 缓存键的 as-of（默认 = 沙箱 _seed.json 的 cut）")
    ap.add_argument("--mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"],
                    help="record=真外呼并落缓存；replay=只读缓存（未命中报错）")
    ap.add_argument("--cache", default=os.getenv("BT_LLM_CACHE", os.path.join(bt_env.DATA, "_bt_llm_year2")),
                    help="LLM 录制/回放缓存根（默认 data/_bt_llm_year2）")
    ap.add_argument("--url", default=os.getenv("BT_AGENT_CHAT_URL", "http://127.0.0.1:13001/chat"),
                    help="record 模式的 LLM /chat（本地隧道 127.0.0.1:13001）")
    ap.add_argument("--stdout", action="store_true",
                    help="**只读模式**：只打印将要写的内容，不安装到沙箱 low_logic.json（临时文件也会删掉）")
    ap.add_argument("--code-dir", default="", help="该日代码版本树（默认按 data/_bt_code/rev_map.json 解析）")
    ap.add_argument("--out", default="", help="非 stdout 模式下把结果写到这个路径（默认 = 沙箱 low_logic.json）")
    ap.add_argument("--json", default="", help="把本次报告写到这个路径（默认只打印）")
    ap.add_argument("--no-net-offline", action="store_true", help="不切断出网（默认切断，仅放行回环）")
    ap.add_argument("--no-relay-shim", action="store_true",
                    help="不装 relay/gzcloud/local_pro 替身（low_logic 本身不取数；装了只是更保险）")
    a = ap.parse_args()

    day = a.day
    a.root = os.path.abspath(a.root)
    day_dir = os.path.join(a.root, day)
    if not os.path.isdir(day_dir):
        print("⛔ 沙箱不存在：%s（先用 jobs/bt_seed_day.py 造）" % day_dir, file=sys.stderr)
        return 2
    seed = _jload(os.path.join(day_dir, "_seed.json"), {}) or {}
    cut = str(seed.get("cut") or "")
    as_of = (a.as_of or cut or day).replace("-", "")   # pin_clock 要 YYYYMMDD
    guard_before = snapshot_guard()

    # ── ① 环境（**必须在 import 生产 agent 之前**：模块级常量在 import 时从 env 取）───────────
    os.environ["DATA_DIR"] = day_dir
    os.environ["WAVE_CHAT_URL"] = a.url
    os.environ["BT_LLM_MODE"] = a.mode
    os.environ["BT_LLM_CACHE"] = a.cache
    farm = ""
    if str(os.getenv("BT_FARM", "1")).strip() not in ("0", "false", "no"):
        try:
            import bt_prod_run  # noqa: W543  与 bt_prod_run 用同一个 farm 约定
            farm = bt_prod_run._farm_root(day_dir)
            os.environ["MARCUS_WORKSPACE"] = farm
        except Exception as e:
            print("[farm] 造 farm 失败（不影响 agent，它只用 DATA_DIR）：%s" % str(e)[:80], file=sys.stderr)

    # ── ② 钉钟 + 断网 + relay/gzcloud 替身（= bt_run_pinned.main() 的同一套）──────────────
    import bt_run_pinned as brp
    brp.pin_clock(as_of)
    if not a.no_net_offline and str(os.getenv("BT_NET_OFFLINE", "1")).strip() not in ("0", "false", "no"):
        try:
            import bt_local_pro
            bt_local_pro.install_net_offline()          # 回环（本地 LLM 隧道）放行
            print("[shim] 已切断出网（回环放行：%s）" % a.url, file=sys.stderr)
        except Exception as e:
            print("[shim] 断网失败：%s" % str(e)[:80], file=sys.stderr)
    bars_db = os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite")
    shim = gzshim = None
    if not a.no_relay_shim:
        try:
            shim = brp.install_relay_shim(bars_db, cut or as_of)
            gzshim = brp.install_gzcloud_shim(bars_db, cut or as_of)
        except Exception as e:
            print("[shim] relay/gzcloud 替身安装失败（low_logic 不取数，通常无影响）：%s" % str(e)[:90],
                  file=sys.stderr)

    # ── ③ 加载生产 agent（优先版本树）────────────────────────────────────────────────
    code_dir = resolve_code_dir(day, a.code_dir)
    agent_path = os.path.join(code_dir, AGENT_REL) if code_dir and os.path.exists(
        os.path.join(code_dir, AGENT_REL)) else os.path.join(REPO, AGENT_REL)
    same_as_head = _md5(agent_path) == _md5(os.path.join(REPO, AGENT_REL))
    mod = load_agent(agent_path)
    import bt_llm_replay
    llm = bt_llm_replay.LLMReplay(agent="low_logic", as_of=as_of, cache_dir=a.cache, mode=a.mode)
    llm.install(mod)                                    # 换掉模块里的 requests（agent 走 requests.post）

    rep = {"script": "jobs/bt_low_logic_asof.py", "day": day, "cut": cut, "as_of": as_of,
           "mode": a.mode, "stdout_only": bool(a.stdout), "sandbox": day_dir, "farm": farm,
           "agent": {"path": agent_path, "code_dir": code_dir or "(无版本树→用 repo 当前代码)",
                     "same_as_repo_head": same_as_head, "md5": _md5(agent_path)},
           "llm": {"url": a.url, "cache_dir": a.cache, "cache_file": llm.cache_file,
                   "installed": llm.installed},
           "inputs": {n: _provenance(os.path.join(day_dir, n)) for n in INPUTS},
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print("[asof] day=%s cut=%s as_of=%s mode=%s DATA_DIR=%s" % (day, cut, as_of, a.mode, day_dir),
          file=sys.stderr)
    print("[asof] agent=%s（%s）" % (agent_path, "与 repo 当前版本一致" if same_as_head else "⚠️ 与当前版本不同"),
          file=sys.stderr)
    for n, info in rep["inputs"].items():
        if not info.get("exists"):
            print("[asof] 输入 %-30s ❌ 缺失" % n, file=sys.stderr)
        elif info.get("kind") == "SYMLINK":
            print("[asof] 输入 %-30s 🔗 软链 → %s%s" % (n, info.get("target"),
                  "（指向 repo data/！）" if info.get("into_repo_data") else ""), file=sys.stderr)
        else:
            print("[asof] 输入 %-30s ✅ 真文件 %sB md5=%s" % (n, info.get("bytes"), str(info.get("md5"))[:8]),
                  file=sys.stderr)

    # ── ④ 输入快照（agent 跑之前的沙箱 low_logic.json = 现有"stub"）+ LOW 概念预览 ────────
    sb_out = os.path.join(day_dir, "low_logic.json")
    old_map = _jload(sb_out, {}) or {}
    rep["old_low_logic"] = {"keys": sorted(old_map), "n": len(old_map), **_provenance(sb_out)}
    res = _jload(os.path.join(day_dir, "position_class_result.json"), {}) or {}
    lows = low_entries(res)
    rep["low_concepts"] = {"n": len(lows), "names": [v.get("name") for v in lows],
                           "used_in_prompt": [v.get("name") for v in lows[:40]]}
    print("[asof] 当天 LOW 概念 %d 个（prompt 取前 40）：%s" % (len(lows), rep["low_concepts"]["used_in_prompt"][:12]),
          file=sys.stderr)

    # 无 LOW → 生产 agent 直接 return（`low_logic_agent.py:28-29`），**不写文件**
    if not lows:
        rep["agent_run"] = {"ran": True, "skipped": "无 LOW 概念（生产同行为：跳过、不写 low_logic.json）"}
        rep["written"] = None
        rep["llm_summary"] = llm.summary()
        rep["diff_vs_stub"] = None
        rep["impact"] = None
        print("[asof] ⏭ 无 LOW 概念 → 生产 agent 会跳过（不会调用 LLM、不会写 low_logic.json）", file=sys.stderr)
        _finish(rep, a, guard_before, sb_out, written=None)
        return 0

    # ── ⑤ 让 agent 写到一个**沙箱内的临时文件**（再决定是否安装）──────────────────────────
    tmp_out = os.path.join(day_dir, ".bt_low_logic_out.json")
    for p in (tmp_out,):
        if os.path.lexists(p):
            os.unlink(p)
    mod.OUT = tmp_out
    buf = io.StringIO()
    t0 = time.time()
    err = None
    try:
        with contextlib.redirect_stdout(buf):
            mod.main()
    except Exception as e:                              # 含 replay 未命中（LLMReplayError）
        import traceback
        err = traceback.format_exc()
        print("[asof] ⛔ agent 抛错：%s" % str(e)[:160], file=sys.stderr)
    dt = time.time() - t0
    log = buf.getvalue()
    rep["agent_run"] = {"ran": True, "elapsed_s": round(dt, 1), "log": log.strip().splitlines()[-8:],
                        "error": (err or "")[-600:] or None}
    written = _jload(tmp_out, None)
    if written is None:
        rep["written"] = None
        rep["llm_summary"] = llm.summary()
        print("[asof] ⚠️ agent 未写出结果（解析失败 / LLM 失败 / 抛错）→ 沙箱 low_logic.json 保持不变",
              file=sys.stderr)
        if os.path.lexists(tmp_out):
            os.unlink(tmp_out)
        _finish(rep, a, guard_before, sb_out, written=None)
        return 1 if err else 0

    # ── ⑥ 逐字段差异 + 影响预测 ────────────────────────────────────────────────────────
    rep["written"] = {"n": len(written), "keys": sorted(written)}
    rep["diff_vs_stub"] = field_diff(old_map, written)
    rep["impact"] = predict_low_actions(res, written)
    rep["llm_summary"] = llm.summary()
    fd = rep["diff_vs_stub"]
    imp = rep["impact"]
    print("[asof] 与沙箱现有 low_logic.json 的差异：新增 %d / 缺失 %d / 变更 %d（原有 %d → 新 %d）"
          % (len(fd["added"]), len(fd["removed"]), len(fd["changed"]), fd["old_n"], fd["new_n"]), file=sys.stderr)
    print("[asof] 影响预测：LOW %d 个，复算自检 %d/%d 一致，会被这份 map 改变的有 %d 个"
          % (imp["n_low"], imp["selfcheck_matched"], imp["selfcheck_total"], len(imp["affected"])), file=sys.stderr)

    # ── ⑦ 落盘（stdout 模式不落盘）─────────────────────────────────────────────────────
    if a.stdout:
        rep["installed_to"] = None
        print("[asof] 👀 --stdout 只读模式：**不安装**，下面打印将要写的内容", file=sys.stderr)
        print("---- low_logic.json（would-write，%d 概念）----" % len(written))
        print(json.dumps(written, ensure_ascii=False, indent=1))
        print("---- end ----")
    else:
        dst = a.out or sb_out
        unlinked = _materialize(dst)                    # 🔴 关键：软链 → 先解链（防写穿）
        if unlinked:
            print("[asof] 🔗 %s 原为符号链接（指向 repo data/）→ 已解链，写入落在沙箱" % dst, file=sys.stderr)
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(written, f, ensure_ascii=False, indent=1)
        rep["installed_to"] = dst
        print("[asof] ✅ 已写入 %s（%d 概念，%dB）" % (dst, len(written), os.path.getsize(dst)), file=sys.stderr)
    if os.path.lexists(tmp_out):
        os.unlink(tmp_out)
    _finish(rep, a, guard_before, sb_out, written=written)
    return 1 if err else 0


def _print_impact(imp: dict) -> None:
    for x in imp.get("affected", [])[:40]:
        print("    · %-14s 资金=%-4s 催化=%-5s 结构确认=%-6s 指数=%-6s | action %s → %s | logic_strong %s → %s [%s]"
              % (x["name"], x["fund"], x["catalyst"], x["confirm"], x["index_confirm"],
                 x["action_stored"], x["action_with_new_map"],
                 x["logic_strong_stored"], x["logic_strong_new"], x["delta"]))


def _finish(rep: dict, a, guard_before: dict, sb_out: str, written) -> None:
    """收尾：写穿自检 + 报告落盘 + 摘要打印。"""
    guard_after = snapshot_guard()
    pen = {n: [guard_before.get(n), guard_after.get(n)] for n in guard_before
           if guard_before.get(n) != guard_after.get(n)}
    rep["write_penetration"] = {"changed": pen, "ok": not pen,
                               "checked": list(guard_before), "repo_data": REPO_DATA}
    if pen:
        print("[asof] 🔴 写穿告警：repo data/ 下这些文件变了 %s" % json.dumps(pen, ensure_ascii=False),
              file=sys.stderr)
    else:
        print("[asof] ✅ 写穿自检通过：repo data/ 下 %s 的 md5 前后一致" % list(guard_before), file=sys.stderr)
    llm = rep.get("llm_summary") or {}
    if llm:
        print("[asof] LLM：mode=%s hit=%s recorded=%s miss=%s cache_records=%s unused=%s errors=%s"
              % (llm.get("mode"), llm.get("hit"), llm.get("recorded"), llm.get("miss"),
                 llm.get("cache_records"), llm.get("unused"), (llm.get("errors") or [])[:1]), file=sys.stderr)
    if rep.get("diff_vs_stub"):
        fd = rep["diff_vs_stub"]
        print("[asof] diff：added=%s removed=%s changed=%s identical=%s"
              % (fd["added"][:8], fd["removed"][:8], sorted(fd["changed"])[:8], fd["identical"][:8]), file=sys.stderr)
    if rep.get("impact"):
        _print_impact(rep["impact"])
    rep["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if a.json:
        try:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=1)
            print("[asof] 报告已写 %s" % a.json, file=sys.stderr)
        except Exception as e:
            print("[asof] 报告写失败：%s" % str(e)[:80], file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
