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

sys.path[:0] = ["/app", "/app/apps/main_line", "/app/jobs"]
SRC = os.environ.get("DATA_DIR", "/app/data")
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
    return os.path.join("/app", rel)


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
    _rb = restore_files(sb, _snap_seed)
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
            r = subprocess.run([sys.executable, "/app/jobs/bt_wave_asof.py", "--as-of", cut, "--code-dir", code_dir,
                                "--mode", a.llm_mode, "--main-line-state", mls,
                                "--out", os.path.join(sb, "wave_state.json")],
                               capture_output=True, text=True, timeout=900,
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
                r = subprocess.run([sys.executable, "/app/jobs/bt_run_pinned.py", "--as-of", cut,
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
    for name in STUB_FROM_LIVE:
        if any(name == n for n, _s, _a in PINNED):
            continue
        src = os.path.join(SRC, name)
        if not os.path.exists(src) and name == "p3_position_tiers.json":
            src = "/app/config/p3_position_tiers.json"
        info = copy_json(src, os.path.join(sb, name)) if os.path.exists(src) else None
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
