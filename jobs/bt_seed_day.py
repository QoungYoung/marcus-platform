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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="决策日 T（如 20260911）")
    ap.add_argument("--cut", default="", help="数据切点（默认 = 前一交易日）")
    ap.add_argument("--bars-db", default=os.path.join(ROOT, "bars.sqlite"))
    ap.add_argument("--no-llm", action="store_true", help="跳过 wave agent（LLM）再生")
    ap.add_argument("--llm-mode", default=os.getenv("BT_LLM_MODE", "record"), choices=["record", "replay"])
    a = ap.parse_args()

    T = a.date
    cut = a.cut or prev_trade_day(T, a.bars_db)
    sb = os.path.join(ROOT, T)
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
    # index_daily 直接拷贝（wave/144 只用 ≤ 日期的部分；由消费方按 as-of 过滤）
    idx = os.path.join(SRC, "index_daily_000001.json")
    if os.path.exists(idx):
        rec("index_daily_000001.json", copy_json(idx, os.path.join(sb, "index_daily_000001.json")) | {"src": "copy"})

    # ② 按日产物
    for tmpl in PER_DATE:
        name = tmpl.format(c=cut)
        rec(name, from_archive_or_db(name, cut, os.path.join(sb, name)))
    for name in SINGLE_FROM_ARCHIVE:
        rec(name, from_archive_or_db(name, cut, os.path.join(sb, name)))

    # ③c 沙箱兜底：把生产 data 下**其余文件**软链进来（脚本缺输入时才不会炸）；
    #     已知产物先建成真实空文件 → 任何写入都落在沙箱里，**绝不会穿透到生产文件**。
    PRODUCER_OUTPUTS = ["rotation_universe_result.json", "rotation_sub_universe.json",
                        "position_class_result.json", "stock_confirm_result.json",
                        "wolf_mainline_select.json", "wave_state.json", "theme_r5_%s.json" % cut]
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

    # ③ 再生：方向层主线选择（有 --date）
    try:
        r = subprocess.run([sys.executable, "/app/jobs/wolf_mainline_select.py", "--date", cut],
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
            r = subprocess.run([sys.executable, "/app/jobs/bt_wave_asof.py", "--as-of", cut,
                                "--mode", a.llm_mode, "--main-line-state", mls,
                                "--out", os.path.join(sb, "wave_state.json")],
                               capture_output=True, text=True, timeout=900,
                               env={**os.environ, "DATA_DIR": sb,
                                    "BT_LLM_CACHE": os.environ.get("BT_LLM_CACHE", os.path.join(SRC, "_bt_llm"))})
            rec("wave_state.json(regen)", {"src": "regen_llm", "rc": r.returncode,
                                           "tail": (r.stdout or r.stderr or "")[-160:]})
        except Exception as e:
            rec("wave_state.json(regen)", {"src": "regen_llm_err", "err": str(e)[:90]})

    # ④ 待打桩的 4 个：先沿用最近一版（manifest 标 stub）
    for name in STUB_FROM_LIVE:
        src = os.path.join(SRC, name)
        if not os.path.exists(src) and name == "p3_position_tiers.json":
            src = "/app/config/p3_position_tiers.json"
        info = copy_json(src, os.path.join(sb, name)) if os.path.exists(src) else None
        rec(name, (dict(info, src="stub", from_=src) if info else None))

    man["finished_at"] = time.strftime("%H:%M:%S")
    man["n_ok"] = sum(1 for v in man["files"].values() if v.get("src") != "MISSING")
    man["n_missing"] = sum(1 for v in man["files"].values() if v.get("src") == "MISSING")
    _jdump(os.path.join(sb, "_seed.json"), man)
    print("[seed] date=%s cut=%s → %s | ok=%d missing=%d" % (T, cut, sb, man["n_ok"], man["n_missing"]))
    for k, v in man["files"].items():
        if v.get("src") == "MISSING":
            print("       ❌ MISSING %s" % k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
