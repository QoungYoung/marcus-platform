# -*- coding: utf-8 -*-
"""prune_legacy_wave_state.py — 把 **2025 年以前**的 dated 老档移出决策路径（可回滚 ✓）。

为什么要治（2026-10-23）：
  · `data/wave_state_<date>.json` 里混着 **LLM 时代**手抄的档（2016/2021/2022 共 8 份，命名风格
    三种并存：`wave_state_20161130.json` / `wave_state_2016-06-24.json`），其 `operation` 是旧 agent 的
    `build/exit/side` —— **现在的浪型链算不出这些值**，而每日 08:20 的 regen 会把它们**原地重写** ✗
    ⇒ ①历史快照被悄悄改写；②`daily_decision` 在回测下**优先读 dated** ⇒ 2016–2024 的回测会吃到
    「半个 LLM 档 + 半个浪型链档」的混合产物 ✗。
  · 处置：整批挪到 `data/_legacy_wave_state/`（**不在任何读取路径上** ✓）+ 写 `INDEX.json`
    记录原文件名/日期/op/sha256 ⇒ 随时 `--restore` 可逆 ✓。
  · 配套闸门：`jobs/regen_wave_state_ladder.py::root_files()` 默认**跳过** `date < DATED_SINCE`
    的 dated 老档（`--include-legacy` 可放开）⇒ 即便有人把文件挪回来也不会被重算 ✓。

用法::
    python jobs/prune_legacy_wave_state.py                  # dry-run：列出会挪哪些
    python jobs/prune_legacy_wave_state.py --apply           # 真挪（幂等 ✓ 重复跑只补漏）
    python jobs/prune_legacy_wave_state.py --before 20240101 # 自定义分界
    python jobs/prune_legacy_wave_state.py --restore         # 逆操作：按 INDEX 全部挪回
"""
import argparse
import glob
import hashlib
import json
import os
import time

DATA = os.environ.get("DATA_DIR") or "data"
LEGACY_DIR = "_legacy_wave_state"
INDEX = "INDEX.json"
DEFAULT_BEFORE = (os.getenv("WOLF_WAVE_DATED_SINCE") or "20250102").strip()[:8]


def day_of(f):
    """`data/wave_state_2016-06-24.json` → `20160624`（三种命名都能解析 ✓）。"""
    return os.path.basename(f).replace("wave_state_", "").replace(".json", "").replace("-", "")[:8]


def select(data_dir=DATA, before=DEFAULT_BEFORE):
    """→ 需要挪走的文件列表（`date < before` 的 dated 档；live/history 一律不碰 ✓）。"""
    out = []
    for f in sorted(glob.glob(os.path.join(data_dir, "wave_state_[0-9]*.json"))):
        d = day_of(f)
        if len(d) == 8 and d.isdigit() and d < str(before)[:8]:
            out.append(f)
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _meta(path):
    m = {"file": os.path.basename(path), "date8": day_of(path)}
    try:
        obj = json.load(open(path, encoding="utf-8")) or {}
        for k in ("level", "sub_level", "operation", "source", "generator_version", "as_of"):
            m[k] = obj.get(k)
    except Exception:
        m["unreadable"] = True
    try:
        m["sha256"] = _sha256(path)
    except OSError:
        m["sha256"] = ""
    m["bytes"] = os.path.getsize(path) if os.path.exists(path) else 0
    return m


def _load_index(path):
    try:
        obj = json.load(open(path, encoding="utf-8")) or {}
    except Exception:
        obj = {}
    obj.setdefault("moved", [])
    return obj


def _save_index(path, obj):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真挪（默认 dry-run ✓）")
    ap.add_argument("--restore", action="store_true", help="按 INDEX 把老档挪回 data/ ✓")
    ap.add_argument("--before", default=DEFAULT_BEFORE, help="分界日（含之前挪走，默认 20250102 ✓）")
    ap.add_argument("--data", default=DATA, help="data 目录（默认 data/ ✓）")
    args = ap.parse_args()

    ldir = os.path.join(args.data, LEGACY_DIR)
    ipath = os.path.join(ldir, INDEX)

    if args.restore:
        obj = _load_index(ipath)
        if not obj["moved"]:
            print("INDEX 为空或无索引 ⇒ 无需恢复")
            return 0
        n, miss = 0, 0
        for m in obj["moved"]:
            src = os.path.join(ldir, m["file"])
            dst = os.path.join(args.data, m["file"])
            if not os.path.exists(src):
                miss += 1
                continue
            if args.apply:
                os.replace(src, dst)
            n += 1
        print("%s恢复 %d 份（缺失 %d）→ %s" % ("已" if args.apply else "将", n, miss, args.data))
        if args.apply:
            obj["restored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_index(ipath, obj)
        return 0

    files = select(args.data, args.before)
    print("分界 %s ｜ 待挪 %d 份 ｜ 目前 _legacy 目录存在：%s" % (args.before, len(files), os.path.isdir(ldir)))
    for f in files:
        m = _meta(f)
        print("  %-34s date=%s op=%-8s source=%-12s %d bytes"
              % (m["file"], m["date8"], m.get("operation"), m.get("source"), m["bytes"]))
    if not files:
        print("✅ 无需处理（data/ 下已无 2025 年前 dated 老档 ✓）")
        return 0
    if not args.apply:
        print("\n（dry-run；加 --apply 才挪动 ✓）")
        return 0

    os.makedirs(ldir, exist_ok=True)
    obj = _load_index(ipath)
    have = {m["file"] for m in obj["moved"]}
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for f in files:
        m = _meta(f)
        m["moved_at"] = ts
        os.replace(f, os.path.join(ldir, m["file"]))
        if m["file"] not in have:
            obj["moved"].append(m)
        n += 1
    obj["before"] = args.before
    obj["note"] = ("2025 年前的 dated 老档（LLM 时代手抄快照）已移出决策路径；"
                   "读取方按文件名找不到 ⇒ fail-open 到价格摆动 / history，不再吃到混合档 ✓")
    _save_index(ipath, obj)
    print("已挪走 %d 份 → %s（索引 %s，共 %d 条记录 ✓）" % (n, ldir, ipath, len(obj["moved"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
