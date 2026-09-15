# -*- coding: utf-8 -*-
"""verify_acceptance_claims.py — 验收页 / 总账里的**开关声明**与代码实际是否一致（2026-09-15 round 18）。

起因：round 13 的覆盖率体检抓到"总账里写的键名在生产代码里不存在"（`WOLF_PICK_RS_MIN` vs `WOLF_RS_MIN`）
—— 文档漂移会让下一个接手的人做错决定。本脚本把这类检查自动化：

  ① 从 `docs/wolf-buy-alignment-acceptance.md` 抽出所有 `WOLF_*` / `P3_*` / `T_*` / `ROT_*` 开关名；
  ② 到 `apps/`、`backend/app/`、`jobs/`（排除 tests、_tmp）里找 `os.getenv("<NAME>", "<default>")`；
  ③ 报告：**文档提到但代码里没有**（漂移/拼错）、**代码里有但文档没提**（可能是漏记的开关）；
  ④ 从验收页抽 `data/<name>_shadow_*.json` 形式的影子产物名，检查是否真有模块写它（串起来"影子在跑"的说法）。

用法::

    .venv/bin/python jobs/verify_acceptance_claims.py
    .venv/bin/python jobs/verify_acceptance_claims.py --doc docs/wolf-buy-alignment-acceptance.md
"""
import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "wolf-buy-alignment-acceptance.md")
LEDGER = os.path.join(ROOT, "docs", "wolf-buy-parameter-ledger.md")
SCAN_DIRS = ("apps", "backend/app", "jobs")
PREFIX = ("WOLF_", "P3_", "T_", "ROT_", "SWITCH_")


def py_files():
    out = []
    for base in SCAN_DIRS:
        for dirpath, _d, files in os.walk(os.path.join(ROOT, base)):
            if "__pycache__" in dirpath or dirpath.endswith("tests") or "/tests/" in dirpath:
                continue
            for f in files:
                if f.endswith(".py") and not f.startswith("_tmp"):
                    out.append(os.path.join(dirpath, f))
    return out


def code_knobs(files):
    """→ {NAME: [(relpath, default)]}（AST 扫 os.getenv / os.environ.get 的字面量键）。"""
    out = {}
    for p in files:
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except Exception:
            continue
        rel = os.path.relpath(p, ROOT)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
                continue
            fn = node.func
            is_getenv = fn.attr == "getenv" and isinstance(fn.value, ast.Name) and fn.value.id == "os"
            is_env_get = (fn.attr == "get" and isinstance(fn.value, ast.Attribute)
                          and fn.value.attr == "environ"
                          and isinstance(fn.value.value, ast.Name) and fn.value.value.id == "os")
            if not (is_getenv or is_env_get):
                continue
            a0 = node.args[0]
            if not (isinstance(a0, ast.Constant) and isinstance(a0.value, str)):
                continue
            default = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                default = node.args[1].value
            out.setdefault(a0.value, []).append((rel, default))
    return out


def shadow_writers(files):
    """→ {影子文件名模板里的关键词: [模块]}（扫 data/<x>_shadow 或 _shadow_<date> 的字符串）。"""
    out = {}
    for p in files:
        try:
            src = open(p, encoding="utf-8").read()
        except Exception:
            continue
        rel = os.path.relpath(p, ROOT)
        for m in re.finditer(r"[\"']([^\"']*shadow[^\"']*)[\"']", src, flags=re.I):
            out.setdefault(m.group(1), []).append(rel)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default=DOC)
    ap.add_argument("--ledger", default=LEDGER)
    args = ap.parse_args()

    text = open(args.doc, encoding="utf-8").read()
    try:
        text += "\n" + open(args.ledger, encoding="utf-8").read()
    except Exception:
        pass
    raw = {k for k in re.findall(r"\b([A-Z][A-Z0-9_]{3,})\b", text) if k.startswith(PREFIX)}
    mentioned = sorted(k for k in raw if not k.endswith("_") and len(k) > 4)
    shadows_mentioned = sorted(set(re.findall(r"([a-z0-9_]+_shadow_[a-z0-9_*<>]*)", text)))

    files = py_files()
    knobs = code_knobs(files)
    writers = shadow_writers(files)
    print("[verify] 扫 %d 个模块 | 代码里的 env 键 %d 个 | 文档提到的开关名 %d 个 | 文档提到的影子产物 %d 个"
          % (len(files), len(knobs), len(mentioned), len(shadows_mentioned)))

    # 文档里"明确写着它不存在/已改名"的键不算漂移（例：总账 §19 记的 WOLF_PICK_RS_MIN）
    excused = set()
    for m in re.finditer(r"([A-Z][A-Z0-9_]{4,})\s*`?\s*(?:这个键)?\*\*?(?:在生产代码里)?\*\*?\s*不(?:存在|再存在)", text):
        excused.add(m.group(1))
    for m in re.finditer(r"([A-Z][A-Z0-9_]{4,})[^\n]{0,20}(?:真实键是|实际是|已改名)", text):
        excused.add(m.group(1))
    missing = [k for k in mentioned if k not in knobs and k not in excused]
    unmentioned = sorted(k for k in knobs if k.startswith(("WOLF_", "P3_", "ROT_")) and k not in mentioned)
    print("\n❌ 文档提到、代码里**没有**的开关名（%d）：" % len(missing))
    for k in missing:
        print("   " + k)
    print("\n⚠️ 代码里有、文档**未提**的 WOLF_/P3_/ROT_ 键（%d，可能只是没写进验收页）：" % len(unmentioned))
    for k in unmentioned[:40]:
        locs = knobs[k]
        print("   %-42s %s" % (k, "; ".join("%s=%r" % (r, d) for r, d in locs[:2])))

    print("\n== 影子产物是否有模块在写 ==")
    for s in shadows_mentioned:
        base = s.split("_shadow")[0]
        if "_shadow" not in s or not base:
            print("   %-34s （非影子产物，跳过）" % s)
            continue
        hit = sorted({f for k in writers if base in k for f in writers[k]})
        print("   %-34s %s" % (s, ("✅ " + ", ".join(hit[:3])) if hit else "❌ 没找到写它的模块"))

    # 关键开关的默认值是否与验收页声称的一致（只查验收页里明确写了"默认 0/1"的那几个）
    print("\n== 开关默认值：文档声称值 vs 代码默认值 ==")
    # 只认"…默认 **X**"的显式声明（`NAME=1` 在本项目文档里是"置 1 才生效"的开启写法，不是默认值）
    claimed = {}
    for m in re.finditer(r"`?([A-Z][A-Z0-9_]{4,})`?\s*[（(]?\s*默认\s*\*{0,2}([0-9]+(?:\.[0-9]+)?)\*{0,2}", text):
        claimed.setdefault(m.group(1), m.group(2))
    bad = 0
    for k in sorted(claimed):
        if k not in knobs:
            continue
        d = knobs[k][0][1]
        rel = knobs[k][0][0]
        same = str(claimed[k]) == str(d) or (str(claimed[k]) in ("0", "1") and str(d) in ("0", "1")
                                             and str(claimed[k]) == str(d))
        if not same:
            bad += 1
            print("   ⚠️ %-40s 文档=%s 代码=%r (%s)" % (k, claimed[k], d, rel))
    print("   共比对 %d 个带值的开关，**不一致 %d 个**" % (len([k for k in claimed if k in knobs]), bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
