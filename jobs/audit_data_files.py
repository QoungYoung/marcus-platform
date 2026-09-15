# -*- coding: utf-8 -*-
"""audit_data_files.py — 「机制在、数据空转」的系统性审计（2026-09-15 round 26）。

起因（两次同类事故，见记忆 lessons）：
  · E3 的 `data/wolf_ticket_ban.json`（破线删票黑名单）**从未产生**；
  · round 25 的 `data/wolf_negative_events.json`（个股级负事件）**在生产不存在** →
    `negative_event()` 恒返回 None → 「有意外利空就不套用结构止损线」的分支从未生效。
共同特征：**读 data/*.json 的机制写好了，但没有任何东西往里写** → 静默不生效。

做法（不需要生产凭据也能跑代码侧）：
  1. 扫 `backend/app`、`apps`、`jobs`、`core` 里的字符串字面量 `xxx.json`（含 `%s` / `{}` 占位）；
  2. 判断同一个文件里**有没有写入动作**（`json.dump(` / `open(..., "w")`）→ 有=writer，只读=reader-only；
  3. 给出 `--prod-dir` 时（本地挂载或拉下来的生产 data 目录快照）比对**生产是否存在该文件**；
  4. 输出三类：
     * 🔴 **只读·生产缺失** —— 最可能的静默空转（要人工确认读它的代码路径是否在生产跑）；
     * 🟡 **只读·生产存在** —— 手工/外部维护的文件（要确认"谁写它"）；
     * ✅ **有 writer**（并注明生产是否存在）。

用法::

    .venv/bin/python jobs/audit_data_files.py --prod-dir /path/to/prod/data --json out.json
"""
import argparse
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("backend/app", "apps", "jobs", "core")
NAME_RE = re.compile(r"[\"']([A-Za-z0-9_\-\.]+\.json)[\"']")
WRITE_RE = re.compile(r"json\.dump\(|open\([^)]*[\"']w[\"']")
SKIP_PREFIX = ("_tmp",)


def code_files():
    out = []
    for base in SCAN_DIRS:
        for dirpath, _d, files in os.walk(os.path.join(ROOT, base)):
            if "__pycache__" in dirpath or dirpath.endswith("tests") or "/tests/" in dirpath:
                continue
            for f in files:
                if f.endswith(".py") and not f.startswith(SKIP_PREFIX):
                    out.append(os.path.join(dirpath, f))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod-dir", default="", help="生产 data 目录快照路径；给了才做存在性比对")
    ap.add_argument("--prod-config-dir", default="",
                    help="生产 config 目录快照路径（⚠️ 必须给：position_tier 等模块**先查 config/ 再查 data/**，"
                         "只快照 data/ 会产生假阳性 —— 2026-09-15 实测 p3_position_tiers.json 就因此被误报缺缺失）")
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "data_file_audit.json"))
    args = ap.parse_args()

    writers = collections.defaultdict(set)
    readers = collections.defaultdict(set)
    for p in code_files():
        try:
            src = open(p, encoding="utf-8").read()
        except Exception:
            continue
        rel = os.path.relpath(p, ROOT)
        for m in NAME_RE.finditer(src):
            name = m.group(1)
            if name in ("config.json", "package.json"):     # 非 data 目录产物
                continue
            # ⚠️ writer 要**按名字附近窗口**判定，不能按"整个文件里有没有 json.dump"：
            #   否则同一文件里写过别的 json，就会把只读的文件误判成有写入方
            #   （2026-09-15 实测：wolf_negative_events.json 因此被漏报）
            a, b = max(0, m.start() - 320), min(len(src), m.end() + 320)
            win = src[a:b]
            is_writer = bool(re.search(r"json\.dump\(", win)) or bool(
                re.search(r"open\([^)]*[\"']w[\"']", win))
            (writers if is_writer else readers)[name].add(rel)

    prod = set()
    for d in (args.prod_dir, args.prod_config_dir):
        if d and os.path.isdir(d):
            prod |= {f for f in os.listdir(d) if f.endswith(".json")}

    def present(name):
        if not prod:
            return None
        if name in prod:
            return True
        # 支持 xxx_%s.json / xxx_{date}.json 这类模板：按前缀匹配
        stem = name.split("%")[0].split("{")[0].rstrip("_")
        return any(f.startswith(stem) for f in prod) if stem else False

    rows = []
    allnames = sorted(set(writers) | set(readers))
    for name in allnames:
        w = sorted(writers.get(name) or [])
        r = sorted(readers.get(name) or [])
        rows.append({"name": name, "writers": w, "readers_only": r, "in_prod": present(name)})

    red = [x for x in rows if x["in_prod"] is False and not x["writers"]]

    def _prod_reader(x):
        """读者是否落在**生产路径**：backend/app（服务/接口）、apps/main_line（决策）、或定时任务脚本。"""
        for r in x["readers_only"]:
            if r.startswith("backend/app/") or r.startswith("apps/main_line/"):
                return True
        return False

    red_prod = [x for x in red if _prod_reader(x)]
    red_other = [x for x in red if not _prod_reader(x)]
    yellow = [x for x in rows if x["in_prod"] is True and not x["writers"]]
    green = [x for x in rows if x["writers"]]

    print("[audit] 代码里出现的 data/*.json 名 %d 个 | 生产快照 %s + %s | json 文件 %d 个"
          % (len(rows), args.prod_dir or "(未给)", args.prod_config_dir or "(未给)", len(prod)))
    print("\n🔴 **只读 · 生产缺失 · 读者在生产路径**（优先人工核，%d 个）：" % len(red_prod))
    for x in red_prod:
        print("   %-34s 读它的模块: %s" % (x["name"], ", ".join(x["readers_only"][:3])))
    print("\n⚪ 只读 · 生产缺失 · 读者在离线/回测/其它 app（大概率是我们自己的分析产物，%d 个）：" % len(red_other))
    print("   " + ", ".join(x["name"] for x in red_other[:24]) + (" ..." if len(red_other) > 24 else ""))
    print("\n🟡 **只读 · 生产存在**（手工/外部维护：要先确认「谁写它」，%d 个）：" % len(yellow))
    for x in yellow:
        print("   %-34s 读它的模块: %s" % (x["name"], ", ".join(x["readers_only"][:3])))
    print("\n✅ **代码里同时有 writer**（%d 个；生产存在性见括号）：" % len(green))
    for x in green[:40]:
        print("   %-34s 生产=%s writer=%s" % (x["name"], x["in_prod"], ", ".join(x["writers"][:2])))
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"n_names": len(rows), "prod_dir": args.prod_dir, "prod_json_count": len(prod),
               "readonly_missing_prod_path": red_prod, "readonly_missing_other": red_other,
               "readonly_present": yellow, "has_writer": green},
              open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[audit] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
