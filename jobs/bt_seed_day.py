# -*- coding: utf-8 -*-
"""bt_seed_day.py — 全拟真回测：为**单个决策日 T** 准备 as-of 沙箱（PIT 打桩第一层）。

产出 `data/_bt_full/<T>/`：把 T 日 09:20 布腿器会读到的**所有文件**都换成本地沙箱里的 as-of 版本，
并写 `_seed.json` 记录每个文件的**来源与新鲜度**（archive / daily_artifacts / 截断 / 再生 / 缺失）。

四类处理（对应流程稿 §4 的 PIT 纪律）：
  ① **滚动窗口文件截断**：`concept_hist.json`（每概念 dates/close/net_amount 并排数组）、
     `concept_long_seed.json`、`theme_mf_daily.json`（按日期键）、`concept_vol.json`（dates 数组）
     → 一律只保留 `date <= cut`；
  ② **按日产物直接取**：`mainline_gate_<cut>.json` / `heat_v2_<cut>.json` / `trend_confirm_<cut>_long.json` /
     `main_line_state.json` / `latest_hot_sectors.json` / `etf_share_flow.json` / `concept_long.json` /
     `theme_inst_flow.json` / `wave_pivots.json` → 优先 `data/_archive/<cut>/`，其次 DB `daily_artifacts`，
     再次生产 `data/` 里的同名/按日文件；
  ③ **再生（带 --date 的生产脚本）**：`wolf_mainline_select.json`（`jobs/wolf_mainline_select.py --date cut`）、
     `wave_state.json`（`jobs/bt_wave_asof.py --as-of cut`，LLM 录制/回放）；
  ④ **需 as-of 打桩的生产脚本**（本脚本 v1 先**沿用最近一版快照**并在 manifest 标 `stub`，
     下一轮接 `bt_shim` 逐个再生）：`rotation_universe_result.json` / `rotation_sub_universe.json` /
     `stock_confirm_result.json` / `position_class_result.json`。

用法：
  python jobs/bt_seed_day.py --date 20260911            # cut 自动 = 前一交易日
  python jobs/bt_seed_day.py --date 20260911 --cut 20260910 --no-llm   # 只做文件层，跳过 LLM
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

sys.path[:0] = []
import bt_env  # noqa: E402
bt_env.add_paths()
SRC = os.environ.get("DATA_DIR") or bt_env.DATA
ROOT = os.path.join(SRC, "_bt_full")
DB_URL = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")

# 生产 09:20 布腿器 + 主题门会读到的文件（来源 → 处理方式）
PER_DATE = ["mainline_gate_{c}.json", "heat_v2_{c}.json", "trend_confirm_{c}_long.json",
            "theme_r5_{c}.json", "wave_pivots.json"]
SINGLE_FROM_ARCHIVE = ["main_line_state.json", "latest_hot_sectors.json", "etf_share_flow.json",
                       "concept_long.json", "theme_inst_flow.json", "mainline_confirm_history.json",
                       "wolf_mainline_select.json"]
STUB_FROM_LIVE = ["rotation_universe_result.json", "rotation_sub_universe.json",
                  "stock_confirm_result.json", "position_class_result.json", "rotation_crowding.json",
                  "wolf_ticket_ban.json", "etf_theme_map_pi.json", "low_logic.json",
                  "trend_confirm_params.json", "p3_position_tiers.json"]
ROLLING = ["concept_hist.json", "concept_long_seed.json", "theme_mf_daily.json", "concept_vol.json",
           "index_daily_000001.json"]


def _jload(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _jdump(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def prev_trade_day(d8: str, bars_db: str) -> str:
    import sqlite3
    c = sqlite3.connect(bars_db)
    r = c.execute("SELECT max(trade_date) FROM bars WHERE trade_date < ?", (d8,)).fetchone()
    c.close()
    return (r[0] if r and r[0] else d8)


# ── ① 滚动窗口截断 ─────────────────────────────────────────────
def truncate_concept_hist(src: str, dst: str, cut: str):
    d = _jload(src, {}) or {}
    out = {}
    for k, v in d.items():
        if not isinstance(v, dict):
            out[k] = v; continue
        dates = v.get("dates") or []
        keep = [i for i, x in enumerate(dates) if str(x) <= cut]
        nv = dict(v)
        if keep:
            last = keep[-1] + 1
            for key in ("dates", "close", "net_amount", "vol", "amount"):
                if isinstance(v.get(key), list):
                    nv[key] = v[key][:last]
        else:
            for key in ("dates", "close", "net_amount"):
                if isinstance(v.get(key), list):
                    nv[key] = []
        out[k] = nv
    _jdump(dst, out)
    return {"concepts": len(out), "cut": cut}


# 按日产物家族：软链农场会把"当期"文件也链进来，而消费方常按"日期最大"取用 → **必须裁掉 > cut 的**
DATED_FAMILIES = ("trend_confirm_*_long.json", "mainline_gate_*.json", "heat_v2_*.json", "theme_r5_*.json",
                  "chain_map_2*.json", "rotation_crowding_2*.json", "wave_state_2*.json",
                  "main_line_state_2*.json", "mainline_confirm_history_2*.json")


def prune_future_dated(sb: str, cut: str):
    """删掉沙箱里日期 > cut 的按日产物（含软链）——**防未来函数**。

    实测踩坑：1 月沙箱里混进 `trend_confirm_202609xx_long.json`，而 `wolf_context._latest("trend_confirm_")`
    取日期最大的那份 → 1 月决策用了 9 月的结构状态（真前视）。
    """
    import re as _re
    removed = []
    for pat in DATED_FAMILIES:
        for p in glob.glob(os.path.join(sb, pat)):
            m = _re.search(r"(?<!\d)(20\d{6})(?!\d)", os.path.basename(p))
            if m and m.group(1) > cut:
                try:
                    os.unlink(p)
                    removed.append(os.path.basename(p))
                except OSError:
                    pass
    return removed


def assert_pit(sb: str, cut: str, names):
    """**PIT 硬校验**：沙箱里的滚动输入不得含 > cut 的日期（当日/未来数据）。

    用户的纪律：**主题资金流等按日数据，当日值只能用于次日决策**（避免未来函数）。
    这里对三类结构做检查并返回明细；任何越界都记 `man["pit_violations"]` 并大声打印：
      · 顶层键是日期（`theme_mf_daily.json`）
      · `{concepts:{name:{dates:[...]}}}`（`concept_hist.json`）
      · `{series:{...}}` / `{dates:[...]}`（`concept_long_seed.json` / `concept_vol.json`）
    """
    bad = {}
    for nm in names:
        p = os.path.join(sb, nm)
        if not os.path.exists(p):
            continue
        d = _jload(p, None)
        if d is None:
            continue
        found = []

        def _scan(obj, depth=0):
            if depth > 4 or len(found) > 5:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(k, str) and len(k) == 8 and k.isdigit() and k > cut:
                        found.append(k)
                    _scan(v, depth + 1)
            elif isinstance(obj, list):
                for v in obj[:400]:
                    if isinstance(v, str) and len(v) == 8 and v.isdigit() and v > cut:
                        found.append(v)
                    elif isinstance(v, (dict, list)):
                        _scan(v, depth + 1)

        _scan(d)
        if found:
            bad[nm] = sorted(set(found))[:5]
    return bad


def truncate_by_date_keys(src: str, dst: str, cut: str):
    d = _jload(src, {}) or {}
    out = {k: v for k, v in d.items() if str(k) <= cut} if isinstance(d, dict) else d
    _jdump(dst, out)
    return {"keys_kept": len(out) if isinstance(out, dict) else 0}


def truncate_concept_long_seed(src: str, dst: str, cut: str):
    """`concept_long_seed.json` = {meta, series:{concept:{dates,close}}} → 按 dates ≤ cut 截断。"""
    d = _jload(src, {}) or {}
    ser = d.get("series") or {}
    out_ser = {}
    for k, v in ser.items():
        if not isinstance(v, dict) or not isinstance(v.get("dates"), list):
            out_ser[k] = v; continue
        dates = v["dates"]
        keep = [i for i, x in enumerate(dates) if str(x) <= cut]
        last = (keep[-1] + 1) if keep else 0
        nv = dict(v)
        for key, val in list(v.items()):
            if isinstance(val, list) and len(val) == len(dates):
                nv[key] = val[:last]
        out_ser[k] = nv
    nd = dict(d, series=out_ser)
    nd["meta"] = dict(d.get("meta") or {}, asof_cut=cut, asof_note="回测截断到 cut")
    _jdump(dst, nd)
    return {"concepts": len(out_ser), "cut": cut}


def truncate_dates_arrays(src: str, dst: str, cut: str):
    d = _jload(src, {}) or {}
    out = {}
    for k, v in d.items():
        if not isinstance(v, dict) or not isinstance(v.get("dates"), list):
            out[k] = v; continue
        dates = v["dates"]
        keep = [i for i, x in enumerate(dates) if str(x) <= cut]
        last = (keep[-1] + 1) if keep else 0
        nv = dict(v)
        for key, val in list(v.items()):
            if isinstance(val, list) and len(val) == len(dates):
                nv[key] = val[:last]
        out[k] = nv
    _jdump(dst, out)
    return {"concepts": len(out)}


def copy_json(src: str, dst: str, cut: str = "20991231"):
    d = _jload(src, None)
    if d is None:
        return None
    _jdump(dst, d)
    return {"bytes": os.path.getsize(dst)}


# ── ② 按日产物：archive → daily_artifacts → 生产 data ──────────
ART_KEYS = {"mainline_gate": "mainline_gate", "heat_v2": "heat_v2", "trend_confirm_long": "trend_confirm_long",
            "main_line_state": "main_line_state", "latest_hot_sectors": "latest_hot_sectors",
            "etf_share_flow": "etf_share_flow", "concept_long": "concept_long",
            "theme_inst_flow": "theme_inst_flow", "wave_pivots": "wave_pivots",
            "mainline_confirm_history": "mainline_confirm_history", "wolf_mainline_select": "mainline_select"}


def from_archive_or_db(name: str, d8: str, dst: str, want_key: str = ""):
    """按日产物：archive 目录优先 → DB daily_artifacts → 生产 data/ 里的按日文件。"""
    cand = os.path.join(SRC, "_archive", d8, name)
    if os.path.exists(cand):
        shutil.copy2(cand, dst)
        return {"src": "archive", "path": cand}
    key = want_key or ART_KEYS.get(name.replace(".json", ""), name.replace(".json", ""))
    try:
        import psycopg2
        c = psycopg2.connect(DB_URL); cur = c.cursor()
        cur.execute("SELECT payload, src_path FROM daily_artifacts WHERE trade_date=%s AND artifact_key=%s",
                    (d8, key))
        row = cur.fetchone(); cur.close(); c.close()
        if row and row[0] is not None:
            payload = row[0]
            obj = json.loads(payload) if isinstance(payload, str) else payload
            _jdump(dst, obj)
            return {"src": "daily_artifacts", "artifact_key": key, "orig": row[1],
                    "rebuilt": "_rebuilt" in json.dumps(obj)[:2000]}
    except Exception as e:
        return {"src": "db_err", "err": str(e)[:80]}
    live = os.path.join(SRC, name)
    if os.path.exists(live):
        shutil.copy2(live, dst)
        return {"src": "live_file_STALE?", "path": live}
    return None


def normalize_state_files(sb: str, cut: str) -> list:
    """把 as-of 文件里的**易变字段**钉到 cut（否则同一 as-of 两次跑出的 prompt 不同 → LLM 缓存放不进回放）。

    实测（2026-09-15）：`main_line_state.json` 的 `updated_at` 是**墙钟时间**（跑的时候才写），
    `wolf_mainline_select.py` 重算后会把它刷新成"现在" → wave agent 的 prompt 每次都变，
    `bt_llm_replay` 的 prompt 一致性检查因此报错（**这个检查正是要抓这类 PIT 漏洞**）。
    """
    fixed = []
    p = os.path.join(sb, "main_line_state.json")
    d = _jload(p, None)
    if isinstance(d, dict):
        d["updated_at"] = "%s-%s-%s 09:00:00" % (cut[:4], cut[4:6], cut[6:8])
        d["_asof_normalized"] = True
        _jdump(p, d)
        fixed.append("main_line_state.updated_at")
    for nm in ("wolf_mainline_select.json", "rotation_universe_result.json", "rotation_sub_universe.json"):
        q = os.path.join(sb, nm)
        dd = _jload(q, None)
        if isinstance(dd, dict) and "generated_at" in dd:
            dd["generated_at"] = "%s-%s-%s 09:00:00" % (cut[:4], cut[4:6], cut[6:8])
            _jdump(q, dd)
            fixed.append(nm + ".generated_at")
    return fixed


# 这些文件在沙箱里**由本脚本按 as-of 播种**，但会被"再生类"生产脚本顺手改写（实测：
# `wolf_mainline_select.py` 会把 `main_line_state.json` 重写 → 用它去选主题/确认域就不是 as-of 了）
# → 在跑 producer 前**快照**，跑完**还原**（producer 自己负责产出的文件不在此列）。
PROTECT_AFTER_PRODUCERS = ["main_line_state.json", "mainline_confirm_history.json",
                           "latest_hot_sectors.json", "etf_share_flow.json", "concept_long.json",
                           "theme_inst_flow.json", "wave_pivots.json"]


def script_path(code_dir: str, rel: str) -> str:
    """**关键**：`runpy`/`subprocess` 跑的是"这个文件"，import 才走 sys.path。
    要真正用当天版本，必须执行**版本树里的那份脚本**（否则只有 import 到的模块是旧版，入口脚本还是今天的）。"""
    if code_dir:
        cand = os.path.join(code_dir, rel)
        if os.path.exists(cand):
            return cand
    return os.path.join(bt_env.REPO, rel)


def snapshot_files(sb: str, names) -> dict:
    snap = {}
    for nm in names:
        p = os.path.join(sb, nm)
        if os.path.exists(p) and not os.path.islink(p):
            try:
                with open(p, "rb") as f:
                    snap[nm] = f.read()
            except Exception:
                pass
    return snap


def restore_files(sb: str, snap: dict) -> list:
    """把 producer 跑动过的受保护文件还原（返回被还原的文件名）。"""
    restored = []
    for nm, blob in snap.items():
        p = os.path.join(sb, nm)
        try:
            cur = open(p, "rb").read() if os.path.exists(p) else None
        except Exception:
            cur = None
        if cur != blob:
            with open(p, "wb") as f:
                f.write(blob)
            restored.append(nm)
    return restored


def themes_from_prod_log(T: str, log_dir: str = ""):
    """从生产自己的调度日志里读"当天实际用的主题"（用于补 `main_line_state.candidates`）。

    为什么需要（§9.21）：`main_line_state.json` 是**当日覆盖型**文件，某些天（如 09-10 早晨那份）在
    归档 / 带日期文件 / DB artifact 三处都没有 → 只能退到上一交易日那份，`candidates` 少几个主题
    → `stock_confirm_judge` 只覆盖部分主题 → 确认域退化 → 布腿 0 条。生产日志里恰好记着它当时用的主题：
      · `stock_confirm_refresh` 的 `TOP确认主题: [...]`（= 当时 state.candidates）
      · `rotation_switch_arm` 的 `GATE_CONFIRMED_TODAY [...]` / `MAINLINE_POOL_TODAY [...]`
    这些是**生产自己的产物**（可查证），注入比"退到旧版本"更忠实，且会写进 manifest 供审计。
    """
    import glob as _glob
    import re as _re
    log_dir = log_dir or bt_env.LOGS
    day = "%s-%s-%s" % (T[:4], T[4:6], T[6:])
    pats = [("top_confirm", r"TOP确认主题:\s*\[([^\]]*)\]"),
            ("gate_confirmed", r"GATE_CONFIRMED_TODAY\s*\[([^\]]*)\]"),
            ("mainline_pool", r"MAINLINE_POOL_TODAY\s*\[([^\]]*)\]")]
    found = {}
    for path in _glob.glob(os.path.join(log_dir, "scheduler_%s.jsonl" % day)) + \
            _glob.glob(os.path.join(log_dir, "scheduler_%s.jsonl" % day.replace("-", ""))):
        try:
            for ln in open(path, encoding="utf-8", errors="replace"):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    j = json.loads(ln)
                except Exception:
                    continue
                blob = (j.get("output") or "") + "\n" + (j.get("error") or "")
                for key, pat in pats:
                    for m in _re.finditer(pat, blob):
                        items = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
                        if items and key not in found:
                            found[key] = items
        except Exception:
            continue
    # 当天 derive 用的主主线（`rotation_universe_refresh` 日志：`derive: WROTE rotation_sub_universe.json main=X`）
    main = None
    for path in _glob.glob(os.path.join(log_dir, "scheduler_%s.jsonl" % day)):
        try:
            for ln in open(path, encoding="utf-8", errors="replace"):
                m = _re.search(r"derive: WROTE rotation_sub_universe\.json main=(\S+)", ln)
                if m:
                    main = m.group(1)
                    break
        except Exception:
            pass
        if main:
            break
    themes = found.get("top_confirm") or found.get("mainline_pool") or found.get("gate_confirmed") or []
    return themes, dict(found, derive_main=main)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="决策日 T（如 20260911）")
    ap.add_argument("--cut", default="", help="数据切点（默认 = 前一交易日）")
    ap.add_argument("--bars-db", default=os.path.join(SRC, "_bt_full", "bars.sqlite"))
    ap.add_argument("--root", default=ROOT, help="沙箱根目录（默认 <DATA_DIR>/_bt_full）；**不同轮次用不同根目录**，避免一次跑批把已验证的沙箱覆盖掉（踩过：全窗口跑批把 09-11/09-14 的 wave_state 冲成空）")
    ap.add_argument("--no-llm", action="store_true", help="跳过 wave agent（LLM）再生")
    ap.add_argument("--code-dir", default="", help="该日代码版本树（默认按 data/_bt_code/rev_map.json 解析）")
    ap.add_argument("--no-shim", action="store_true", help="跳过 4 个 producer 的 as-of 打桩（全用 stub）")
    ap.add_argument("--llm-mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    ap.add_argument("--state-from-producers", action="store_true",
                    help="**全年反事实模式**：不要求存在生产的历史 `main_line_state.json` 快照，"
                         "改由生产者（`wolf_mainline_select` 等）按 as-of 数据**就地算出** state，"
                         "并且**不还原**（as-of 快照优先的逻辑只为对齐生产而设）")
    ap.add_argument("--state-themes-from-log", action="store_true",
                    help="用生产调度日志里的 TOP确认主题/GATE_CONFIRMED_TODAY 覆盖 state.candidates"
                         "（当日 state 版本缺失时的可查证替代；注入内容写进 manifest）")
    a = ap.parse_args()

    T = a.date
    cut = a.cut or prev_trade_day(T, a.bars_db)
    # 该日"在跑的代码"（版本树）：producer 与 regen 都用它，保证不是拿今天的代码回放历史
    code_dir = a.code_dir
    if not code_dir:
        try:
            # 注意：`ROOT` 是 _bt_full，版本树在 `SRC/_bt_code`（踩过一次：写成 ROOT/_bt_code → 永远解析为空）
            _m = json.load(open(os.path.join(SRC, "_bt_code", "rev_map.json"), encoding="utf-8"))
            _rev = ((_m.get(T) or {}).get("rev")) or ""
            _d = os.path.join(SRC, "_bt_code", "rev_%s" % _rev) if _rev else ""
            code_dir = _d if _d and os.path.isdir(_d) else ""
        except Exception:
            code_dir = ""
    sb = os.path.join(a.root, T)
    os.makedirs(sb, exist_ok=True)
    man = {"date": T, "cut": cut, "sandbox": sb, "files": {}, "started_at": time.strftime("%H:%M:%S")}

    def rec(name, info):
        man["files"][name] = info or {"src": "MISSING"}
        flag = "✅" if info else "❌"
        print("[seed] %s %-32s %s" % (flag, name, json.dumps(info or {}, ensure_ascii=False)[:110]), flush=True)

    # ① 滚动窗口截断
    for name, fn in (("concept_hist.json", truncate_concept_hist),
                     ("theme_mf_daily.json", truncate_by_date_keys),
                     ("concept_long_seed.json", truncate_concept_long_seed),
                     ("concept_vol.json", truncate_dates_arrays)):
        src = os.path.join(SRC, name)
        rec(name, fn(src, os.path.join(sb, name), cut) | {"src": "truncated", "from": src}
            if os.path.exists(src) else None)
    # index_daily 必须**按日期截断**（有些消费方是整段算的：position_class 的确认链、wave 的均线）
    idx = os.path.join(SRC, "index_daily_000001.json")
    if os.path.exists(idx):
        rows = _jload(idx, []) or []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            kept = [r for r in rows if str(r.get("trade_date") or "") <= cut]
            _jdump(os.path.join(sb, "index_daily_000001.json"), kept)
            rec("index_daily_000001.json", {"src": "truncated", "rows": len(kept), "of": len(rows),
                                           "last": (kept[-1].get("trade_date") if kept else None)})
        else:
            rec("index_daily_000001.json", copy_json(idx, os.path.join(sb, "index_daily_000001.json")) | {"src": "copy"})

    # ② 按日产物
    for tmpl in PER_DATE:
        name = tmpl.format(c=cut)
        rec(name, from_archive_or_db(name, cut, os.path.join(sb, name)))
    for name in SINGLE_FROM_ARCHIVE:
        rec(name, from_archive_or_db(name, cut, os.path.join(sb, name)))
    # `main_line_state.json` 是**当日覆盖型、且会被 T 日早间任务（08:00）重写**的文件：
    #   T 日 08:20 的 stock_confirm / 09:20 的布腿器看到的版本 = **updated_at ≤ T 日早晨** 的那份
    #   （实测 09-11：生产那份 updated_at=2026-09-11 08:00:46，main_line=稳增长/基建、candidates 含 AI/传媒；
    #    而 09-10 的 D1 select 只给 农业 —— 两者是不同写入方，用错就会把主题选偏、连带确认域与腿全偏）。
    # 选择顺序：_archive/<T>（19:50 快照，若未被当日链重写则就是早间那份）→ _archive/<cut> → DB(daily_artifacts)。
    # ⚠️ **必须把"带日期变体"也列进候选**：09-09 的 `main_line_state_20260909.json` 一直存在，
    #    但旧链（`_archive/<T>/main_line_state.json` → `_archive/<cut>/…` → DB artifact）没列它
    #    → 掉到"当期活文件"兜底 → 主题/池/腿全偏（实测 09-09 回放 main=半导体/芯片，生产是 消费/内需）。
    _cands = []
    for d8, tag in ((T, "T"), (cut, "cut")):
        _cands += [
            (os.path.join(SRC, "_archive", d8, "main_line_state.json"), "archive_%s" % tag),
            (os.path.join(SRC, "_archive", d8, "main_line_state_%s.json" % d8), "archive_%s_dated" % tag),
            (os.path.join(SRC, "main_line_state_%s.json" % d8), "%s_dated" % tag),
            (os.path.join(SRC, "main_line_state_%s-%s-%s.json" % (d8[:4], d8[4:6], d8[6:])), "%s_dated_dash" % tag),
        ]
    _picked = None
    for cand, tag in _cands:
        if not os.path.exists(cand):
            continue
        dd = _jload(cand, {}) or {}
        upd = str(dd.get("updated_at") or "")[:10].replace("-", "")
        if upd and upd > T:          # 比 T 还晚 = 当日盘后被重写过，不是早间那份
            continue
        shutil.copy2(cand, os.path.join(sb, "main_line_state.json"))
        _picked = {"src": tag, "path": cand, "updated_at": dd.get("updated_at"), "date": dd.get("date"),
                   "main_line": dd.get("main_line")}
        rec("main_line_state.json", _picked)
        print("[seed] main_line_state.json ← %s（%s, main_line=%s）" % (tag, cand, dd.get("main_line")), flush=True)
        break
    # ②b（可选）用生产日志的当日主题覆盖 `candidates`：当日的 state 版本三处都缺时才用得上，
    #     且**必须留痕**（manifest 里记 injected/原 candidates），避免"悄悄改了输入还不知道"。
    if a.state_themes_from_log:
        try:
            _themes, _found = themes_from_prod_log(T)
            _p = os.path.join(sb, "main_line_state.json")
            if _themes and os.path.exists(_p):
                _cur = _jload(_p, {}) or {}
                _old_c = list(_cur.get("candidates") or [])
                _old_ml = _cur.get("main_line")
                _cur["candidates"] = list(_themes)
                _ml_new = (_found or {}).get("derive_main")
                if _ml_new:
                    _cur["main_line"] = _ml_new
                _cur["_bt_themes_from_log"] = {"themes": _themes, "old_candidates": _old_c,
                                               "old_main_line": _old_ml, "derive_main": _ml_new,
                                               "found": _found, "src": "prod_scheduler_log"}
                _jdump(_p, _cur)
                man["state_themes_injected"] = {"themes": _themes, "old_candidates": _old_c,
                                                "main_line": _cur.get("main_line"), "old_main_line": _old_ml}
                print("[seed] ⚑ state ← 生产日志：main_line %s→%s，candidates %s→%s"
                      % (_old_ml, _cur.get("main_line"), _old_c, _themes), flush=True)
            else:
                print("[seed] ⚑ 生产日志里没读到当日主题（found=%s）→ 不注入" % (_found or {}), flush=True)
        except Exception as _e:
            print("[seed] ⚑ 日志注主题失败: %s" % str(_e)[:90], flush=True)

    if not _picked and a.state_from_producers:
        # 全年反事实：历史快照可能根本不存在 → 交给生产者算；先放一个空占位（wave 步骤要求文件存在）
        _p = os.path.join(sb, "main_line_state.json")
        if os.path.islink(_p):
            os.unlink(_p)
        if not os.path.exists(_p):
            _jdump(_p, {})
        rec("main_line_state.json", {"src": "counterfactual_placeholder", "note": "由生产者按 as-of 数据就地计算"})
        print("[seed] ⚑ 反事实模式：不找生产 state 快照，交由生产者计算（cut=%s）" % cut, flush=True)
        _picked = {"src": "counterfactual_placeholder"}
    if not _picked:
        got = from_archive_or_db("main_line_state.json", cut, os.path.join(sb, "main_line_state.json"))
        rec("main_line_state.json", got)
        _ok = isinstance(got, dict) and got.get("src") not in (None, "live_file_STALE?")
        if not _ok:
            # 三处都没有 → **显式标 fatal**，不要静默用当期活文件（否则"没跑对"会被当成"确实没腿"）
            man["fatal"] = "main_line_state_unavailable(T=%s, cut=%s)" % (T, cut)
            print("[seed] ⛔ FATAL main_line_state 三处都取不到（T=%s cut=%s）→ 本日主题/池/腿不可信" % (T, cut),
                  flush=True)

    # ③c 沙箱兜底：把生产 data 下**其余文件**软链进来（脚本缺输入时才不会炸）；
    #     已知产物先建成真实空文件 → 任何写入都落在沙箱里，**绝不会穿透到生产文件**。
    # ⚠️ **凡是 PINNED 生产者（position_class / derive_sub_universe+rotation_universe / stock_confirm_judge）
    #    会写的文件，都必须在沙箱里是"真实文件"**：留成软链 = 写入穿透到生产文件。
    #    实测 2026-09-16 06:33：`rotation_crowding.json`（由 regen 链里的 build_crowding 写）经软链
    #    重写了生产文件；这次因输出确定性、**字节完全相同**才没造成损失，换个 as-of 就会改掉生产。
    PRODUCER_OUTPUTS = ["rotation_universe_result.json", "rotation_sub_universe.json",
                        "position_class_result.json", "stock_confirm_result.json",
                        "wolf_mainline_select.json", "wave_state.json",
                        "rotation_crowding.json", "rotation_proxy_state.json",
                        "sector_g3_state.json", "trend_confirm_params.json",
                        "switch_builder_plan.json", "rotation_switch_plan.json",
                        "theme_r5_%s.json" % cut]
    n_links = n_real = 0
    try:
        for nm in os.listdir(SRC):
            srcp = os.path.join(SRC, nm)
            dstp = os.path.join(sb, nm)
            if nm.startswith("_bt") or nm == "_archive" or os.path.isdir(srcp) or os.path.exists(dstp):
                continue
            os.symlink(srcp, dstp)
            n_links += 1
        for nm in PRODUCER_OUTPUTS:
            dstp = os.path.join(sb, nm)
            if os.path.islink(dstp):
                os.unlink(dstp)
            if not os.path.exists(dstp):
                _jdump(dstp, {})
                n_real += 1
        man["sandbox_links"] = n_links
        man["sandbox_placeholder_files"] = n_real
        print("[seed] 沙箱软链 %d 个文件；预建产物占位 %d 个（防写入穿透）" % (n_links, n_real), flush=True)
    except Exception as e:
        print("[seed] 沙箱兜底失败: %s" % str(e)[:90], flush=True)


    # ③a-1.3 **裁掉未来按日产物**（软链农场会链进当期文件；消费方按"日期最大"取 → 真前视）
    try:
        _pruned = prune_future_dated(sb, cut)
        if _pruned:
            man["pruned_future_files"] = _pruned
            print("[seed] ✂️ 裁掉 %d 个 > cut 的按日产物：%s" % (len(_pruned), _pruned[:6]), flush=True)
    except Exception as _e:
        print("[seed] 裁未来文件失败: %s" % str(_e)[:80], flush=True)

    # ③a-1.4 **PIT 硬校验**：确保滚动输入里没有 > cut 的日期（当日/未来数据不许进来）
    try:
        _viol = assert_pit(sb, cut, ["theme_mf_daily.json", "concept_hist.json", "concept_long_seed.json",
                                     "concept_vol.json", "index_daily_000001.json"])
        if _viol:
            man["pit_violations"] = _viol
            print("[seed] ⛔ PIT 越界（存在 > cut=%s 的日期）：%s" % (cut, _viol), flush=True)
        else:
            print("[seed] ✅ PIT 校验通过：滚动输入均 ≤ cut=%s（当日资金流不会进当日决策）" % cut, flush=True)
    except Exception as _e:
        print("[seed] PIT 校验异常: %s" % str(_e)[:90], flush=True)

    # ③a-1.5 **trend_confirm as-of cut**：`trend_confirm_<cut>_long.json` 是链上真实输入
    #   （`wolf_context.py` 从它取 `themes[*].track_a.stage` → 布腿器的 stage/verdict；`mainline_state_inject`
    #    也用它），而它由盘后任务 `daily_inputs_chain` 生成、**历史日期只有部分存在**。
    #   生产 2026-09-13 已删掉 heat_v2 / mainline_gate 两步（heat_v2 假设被证伪、gate 作约束净负），
    #   所以只剩 trend_confirm 这一个必须自己算的输入。
    _tc_path = os.path.join(sb, "trend_confirm_%s_long.json" % cut)
    if a.state_from_producers or not os.path.exists(_tc_path):
        try:
            r = subprocess.run([sys.executable, bt_env.jobs_file("bt_run_pinned.py"), "--as-of", cut,
                                "--data-dir", sb, "--bars-db", a.bars_db, "--code-dir", code_dir,
                                "--script", script_path(code_dir, "apps/main_line/trend_confirm.py"), "--",
                                "--hist", os.path.join(sb, "concept_long.json"),
                                "--params", os.path.join(sb, "trend_confirm_params.json"),
                                "--as-of", cut, "--json", _tc_path],
                               capture_output=True, text=True, timeout=1800,
                               env={**os.environ, "DATA_DIR": sb})
            rec("trend_confirm_%s_long.json" % cut, {"src": "regen_pinned", "rc": r.returncode,
                                                     "script": "apps/main_line/trend_confirm.py",
                                                     "tail": (r.stderr or r.stdout or "")[-160:]})
            print("[seed] trend_confirm as-of %s rc=%d" % (cut, r.returncode), flush=True)
        except Exception as _e:
            rec("trend_confirm_%s_long.json" % cut, {"src": "regen_err", "err": str(_e)[:90]})

    # ③a-1.6 **反事实模式**：把每日链上另外两个"按日期"的生产者也算出来（生产 `daily_inputs_chain` 里有）
    #   `build_etf_flow --date cut` / `build_inst_flow --date cut`；否则沙箱里用的是**当期快照**（前视/失真）。
    if a.state_from_producers:
        for _rel, _out in (("apps/main_line/build_etf_flow.py", "etf_share_flow.json"),
                           ("apps/main_line/build_inst_flow.py", "theme_inst_flow.json")):
            try:
                r = subprocess.run([sys.executable, "/app/jobs/bt_run_pinned.py".replace("/app/jobs", os.path.dirname(os.path.abspath(__file__))),
                                    "--as-of", cut, "--data-dir", sb, "--bars-db", a.bars_db,
                                    "--code-dir", code_dir, "--script", script_path(code_dir, _rel),
                                    "--", "--date", cut],
                                   capture_output=True, text=True, timeout=1200,
                                   env={**os.environ, "DATA_DIR": sb})
                rec(_out + "(asof)", {"src": "regen_pinned", "rc": r.returncode, "script": _rel,
                                      "tail": (r.stderr or r.stdout or "")[-140:]})
                print("[seed] %s as-of %s rc=%d" % (_rel.split("/")[-1], cut, r.returncode), flush=True)
            except Exception as _e:
                rec(_out + "(asof)", {"src": "regen_err", "err": str(_e)[:90]})

    # ③a-1.7 **crowding 按日**：`rotation_crowding_<季度末>.json` 里选 ≤ cut 最近的一份（生产是季度更新）
    try:
        import glob as _g2
        _cands = sorted(_g2.glob(os.path.join(SRC, "rotation_crowding_2*.json")))
        _pick = None
        for _c in _cands:
            _d = "".join(ch for ch in os.path.basename(_c) if ch.isdigit())[:8]
            if _d and _d <= cut:
                _pick = (_c, _d)
        if _pick:
            _dst = os.path.join(sb, "rotation_crowding.json")
            if os.path.islink(_dst):
                os.unlink(_dst)
            shutil.copy2(_pick[0], _dst)
            rec("rotation_crowding.json", {"src": "asof_quarter", "path": _pick[0], "quarter_end": _pick[1]})
            print("[seed] rotation_crowding ← %s（≤cut 最近一份）" % os.path.basename(_pick[0]), flush=True)
    except Exception as _e:
        rec("rotation_crowding.json", {"src": "asof_err", "err": str(_e)[:80]})

    # ③a-2 regen 前先快照"按 as-of 播种好的"受保护文件（regen 会顺手改写 main_line_state 之类）
    _snap_seed = snapshot_files(sb, PROTECT_AFTER_PRODUCERS)
    man["protected_files"] = sorted(_snap_seed.keys())

    # ③ 再生：方向层主线选择（有 --date）
    try:
        _wms = script_path(code_dir, "jobs/wolf_mainline_select.py")
        r = subprocess.run([sys.executable, _wms, "--date", cut],
                           capture_output=True, text=True, timeout=600,
                           env={**os.environ, "DATA_DIR": sb})
        ok = os.path.exists(os.path.join(sb, "wolf_mainline_select.json"))
        if not ok:                                   # 脚本可能写到生产 DATA → 取回来
            live = os.path.join(SRC, "wolf_mainline_select.json")
            if os.path.exists(live):
                shutil.copy2(live, os.path.join(sb, "wolf_mainline_select.json"))
                ok = True
        rec("wolf_mainline_select.json(regen)",
            {"src": "regen", "rc": r.returncode, "ok": ok, "tail": (r.stdout or r.stderr or "")[-160:]})
    except Exception as e:
        rec("wolf_mainline_select.json(regen)", {"src": "regen_err", "err": str(e)[:90]})

    # ③a-3 regen 的副作用立刻撤掉（它会重写 main_line_state → 那就不再是 as-of 那份）
    #       ⚠️ 但**全年反事实**模式下，我们要的正是"按 as-of 数据算出来的 state" → 不还原。
    _rb = [] if a.state_from_producers else restore_files(sb, _snap_seed)
    if _rb:
        man["restored_after_regen"] = _rb
        print("[seed] regen 后还原 as-of 文件：%s" % _rb, flush=True)

    # ③b 归一化易变字段（必须在 wave 之前：否则 prompt 每次都变，LLM 缓存不可回放）
    try:
        man["normalized"] = normalize_state_files(sb, cut)
        print("[seed] 归一化易变字段 %s" % man["normalized"], flush=True)
    except Exception as e:
        print("[seed] 归一化失败: %s" % str(e)[:90], flush=True)

    # ③ 再生：波浪判定（LLM 录制/回放）
    if not a.no_llm:
        try:
            mls = os.path.join(sb, "main_line_state.json")
            # ⚠️ 2026-09-16：波浪步必须**和其它生产者一样套 `bt_run_pinned`**（钉钟 + relay/gzcloud 替身
            #    + 本地日线 ≤ cut）。此前直接调 `bt_wave_asof.py`：本机跑时取数走真实中继而失败
            #    （实测 `ProxyError 127.0.0.1:7890`）→ `wave_state.json` 只剩空占位（无 operation）
            #    → `daily_decision` 的 L2/L5 取不到波浪 → **L5 默认放行**（缺层不算拦阻）→ 年跑静默偏乐观。
            r = subprocess.run([sys.executable, bt_env.jobs_file("bt_run_pinned.py"), "--as-of", cut,
                                "--data-dir", sb, "--bars-db", a.bars_db, "--code-dir", code_dir,
                                "--script", bt_env.jobs_file("bt_wave_asof.py"), "--",
                                "--as-of", cut, "--code-dir", code_dir,
                                "--mode", a.llm_mode, "--main-line-state", mls,
                                "--out", os.path.join(sb, "wave_state.json")],
                               capture_output=True, text=True, timeout=1800,
                               env={**os.environ, "DATA_DIR": sb,
                                    "BT_LLM_CACHE": os.environ.get("BT_LLM_CACHE", os.path.join(SRC, "_bt_llm"))})
            rec("wave_state.json(regen)", {"src": "regen_llm", "rc": r.returncode,
                                           "tail": (r.stdout or r.stderr or "")[-160:]})
        except Exception as e:
            rec("wave_state.json(regen)", {"src": "regen_llm_err", "err": str(e)[:90]})

    # ③a-0 **写入穿透自检**：记录生产 data 下所有 *.json/*.jsonl 的 mtime，跑完再比一次；
    #      只要有文件被改动 → 记 man["write_penetration"] 并大声打印（这是"回测改了生产"的告警）。
    def _src_mtimes():
        """生产 data 下文件的"指纹"：小文件用 md5（准确），大文件用 (size, mtime)。"""
        import hashlib
        out = {}
        for pat in ("*.json", "*.jsonl"):
            for _p in glob.glob(os.path.join(SRC, pat)):
                try:
                    sz = os.path.getsize(_p)
                    if sz <= 2 * 1024 * 1024:
                        out[_p] = hashlib.md5(open(_p, "rb").read()).hexdigest()
                    else:
                        out[_p] = "%d:%s" % (sz, os.path.getmtime(_p))
                except OSError:
                    pass
        return out

    _mt_before = _src_mtimes()

    # ④ as-of 打桩：跑没有 --date 的生产脚本（钉时钟 + 本地日线 + 沙箱 DATA_DIR）
    #    顺序 = 概念高低位 → 子方向 → 方向层池 → 确认域（后面的依赖前面的产物）
    # ⚠️ 生产 08:05 只有**一个**任务：`rotation_universe_refresh` = `derive_sub_universe.py --refresh-result`，
    #    它**一次写两个产物**（`rotation_sub_universe.json` + `rotation_universe_result.json`）。
    #    早期版本这里多跑了一个 `rotation_universe.py` 并把池文件**覆盖**掉 → 池的 `room_bottom` 与生产不同
    #    （实测 09-10：生产 [免税概念,短剧互动游戏,数字货币] vs 回放 [Kimi概念,智谱AI,AI语料]），
    #    买链因此整体偏掉、腿级 0/8。**别再加回来**。
    PINNED = [
        ("position_class_result.json", "apps/main_line/position_class.py", []),
        ("rotation_sub_universe.json", "apps/main_line/derive_sub_universe.py", ["--refresh-result"]),
        ("stock_confirm_result.json", "apps/main_line/stock_confirm_judge.py", []),
    ]
    if a.no_shim:
        for name, _script, _args in PINNED:
            src = os.path.join(SRC, name)
            info = copy_json(src, os.path.join(sb, name)) if os.path.exists(src) else None
            rec(name, (dict(info, src="stub_no_shim") if info else None))
    else:
        _snap = snapshot_files(sb, PROTECT_AFTER_PRODUCERS)   # producers 前再快照一次（含 wave 之后的最终态）
        for name, script, args in PINNED:
            out = os.path.join(sb, name)
            before = os.path.getmtime(out) if os.path.exists(out) else 0
            try:
                r = subprocess.run([sys.executable, bt_env.jobs_file("bt_run_pinned.py"), "--as-of", cut,
                                    "--data-dir", sb, "--bars-db", a.bars_db,
                                    "--code-dir", code_dir,
                                    "--script", script_path(code_dir, script), "--"] + args,
                                   capture_output=True, text=True, timeout=1800,
                                   env={**os.environ, "DATA_DIR": sb})
                after = os.path.getmtime(out) if os.path.exists(out) else 0
                rec(name, {"src": "regen_pinned", "rc": r.returncode, "written": after > before,
                           "script": script, "tail": (r.stderr or r.stdout or "")[-200:]})
            except Exception as e:
                rec(name, {"src": "regen_pinned_err", "err": str(e)[:100]})
        man["restored_after_producers"] = restore_files(sb, _snap)
        if man["restored_after_producers"]:
            print("[seed] producer 跑完后还原 as-of 文件：%s" % man["restored_after_producers"], flush=True)

    # 其余辅助文件仍沿用最近一版（manifest 标 stub）
    # ⚠️ **不许覆盖生产者刚算出来的产物**：踩过的坑——`rotation_universe_result.json` 是
    #    `derive_sub_universe --refresh-result` 的产物（它内部调 `rotation_universe.main()` 写池），
    #    但它在"补桩名单"里也存在 → 这一轮 copy_json 把**当期快照**盖回去，池退化成写死的
    #    `SUB_UNIVERSE`（room_bottom 变成 国算/算力 这种主题名而不是概念）→ pathA 选不出票 → 0 条腿。
    #    规则：① 名字出现在 PRODUCER_OUTPUTS/PINNED 里的一律跳过；② 目标文件比源文件**新**（= 生产者刚写的）也跳过。
    _stub_skip = set(PRODUCER_OUTPUTS) | {n for n, _s, _a in PINNED}
    for name in STUB_FROM_LIVE:
        if name in _stub_skip:
            continue
        src = os.path.join(SRC, name)
        if not os.path.exists(src) and name == "p3_position_tiers.json":
            src = os.path.join(bt_env.REPO, "config", "p3_position_tiers.json")
        dst = os.path.join(sb, name)
        try:
            if os.path.exists(dst) and os.path.exists(src) and os.path.getmtime(dst) > os.path.getmtime(src):
                rec(name, {"src": "stub_skipped_newer", "dst_mtime": int(os.path.getmtime(dst))})
                continue
        except OSError:
            pass
        info = copy_json(src, dst) if os.path.exists(src) else None
        rec(name, (dict(info, src="stub", from_=src) if info else None))

    # ③a-1 写入穿透自检（跑完比对）
    try:
        _changed = sorted(_p for _p, _t in _src_mtimes().items() if _mt_before.get(_p) != _t)
        if _changed:
            man["write_penetration"] = _changed[:20]
            print("[seed] ⛔ 写入穿透告警：生产 data 下有 %d 个文件在本次 seed 期间被改动：%s"
                  % (len(_changed), _changed[:8]), flush=True)
    except Exception as _e:
        man["write_penetration_err"] = str(_e)[:80]

    man["code_dir"] = code_dir
    man["finished_at"] = time.strftime("%H:%M:%S")
    man["n_ok"] = sum(1 for v in man["files"].values() if v.get("src") != "MISSING")
    man["n_missing"] = sum(1 for v in man["files"].values() if v.get("src") == "MISSING")
    _jdump(os.path.join(sb, "_seed.json"), man)
    print("[seed] date=%s cut=%s → %s | ok=%d missing=%d" % (T, cut, sb, man["n_ok"], man["n_missing"]))
    for k, v in man["files"].items():
        if v.get("src") == "MISSING":
            print("       ❌ MISSING %s" % k)
    if man.get("fatal"):
        print("[seed] ⛔ FATAL %s —— 沙箱可用，但本日主题/池/腿**不可信**（勿把它当成\"确实没腿\"）" % man["fatal"],
              flush=True)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
