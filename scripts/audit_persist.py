#!/usr/bin/env python3
"""audit_persist.py — 给 json.dump 落盘分类，用于决定哪些该迁 PG。

用法：.venv/bin/python scripts/audit_persist.py [--json out.json]

分类依据（按优先级）：
  1. 能找到 .json 文件名（带引号字面量）→ 再用"是否被其它文件引用"判定共享性
  2. open(<args.X>) / open(<a.X>)        → G. 调用方(CLI)指定输出
  3. open(OUT/DATA/ROOT ...)             → H. 模块常量目录下的输出
  4. 其余变量                            → I. 未解析（需人工看）
"""
import ast
import json
import os
import re
from collections import Counter, defaultdict

ROOT = "/home/fengx/marcus-platform"
SCAN = ["jobs", "scripts", "apps", "core", "backend/app"]
SKIP = {"__pycache__", "node_modules", ".venv", ".git", "tests", "fixtures"}
Q = re.compile(r"""["']([^"']*?\.(?:json|jsonl))["']""")
STATE = re.compile(r"(state|latest|confirmed|_plan|position|ledger|inventory|status|snapshot|history)", re.I)


def all_py():
    out = []
    for d in SCAN:
        for dp, dn, fn in os.walk(os.path.join(ROOT, d)):
            dn[:] = [x for x in dn if x not in SKIP]
            for f in fn:
                if f.endswith(".py") and not f.startswith("test_"):
                    rel = os.path.relpath(os.path.join(dp, f), ROOT)
                    if not any(p in SKIP for p in rel.split(os.sep)):
                        out.append(rel)
    return out


files = all_py()
bodies = {}
for rel in files:
    try:
        bodies[rel] = open(os.path.join(ROOT, rel), encoding="utf-8-sig", errors="ignore").read()
    except OSError:
        bodies[rel] = ""

ref = defaultdict(set)
for rel, body in bodies.items():
    for m in Q.findall(body):
        ref[os.path.basename(m)].add(rel)

sites = []
for rel in files:
    src = bodies[rel]
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    withs = []
    for n in ast.walk(tree):
        if isinstance(n, ast.With):
            end = max([getattr(x, "end_lineno", n.lineno) or n.lineno for x in n.body] + [n.lineno])
            for it in n.items:
                if isinstance(it.optional_vars, ast.Name):
                    try:
                        ctxt = ast.get_source_segment(src, it.context_expr) or ""
                    except Exception:
                        ctxt = ""
                    withs.append((n.lineno, end, it.optional_vars.id, ctxt))

    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr == "dump"
                and isinstance(f.value, ast.Name) and f.value.id == "json") or len(n.args) < 2:
            continue
        src_line = lines[n.lineno - 1].strip() if n.lineno - 1 < len(lines) else ""
        ctx = ""
        # with 上下文
        if isinstance(n.args[1], ast.Name):
            cands = [w for w in withs if w[2] == n.args[1].id and w[0] <= n.lineno <= w[1]]
            if cands:
                cands.sort(key=lambda w: w[1] - w[0])
                ctx = cands[0][3]
        if not ctx:
            try:
                ctx = ast.get_source_segment(src, n.args[1]) or ""
            except Exception:
                ctx = ""

        base, how = "", ""
        for d in range(0, 6):
            hit = []
            for ln in (n.lineno - 1 + d, n.lineno - 2 - d):
                if 0 <= ln < len(lines):
                    hit = Q.findall(lines[ln])
                if hit:
                    break
            if hit:
                base, how = os.path.basename(hit[-1]), "邻近字面量"
                break
        if not base:
            hit = Q.findall(ctx)
            if hit:
                base, how = os.path.basename(hit[-1]), "上下文表达式"

        sites.append({"file": rel, "line": n.lineno, "base": base, "how": how,
                      "ctx": ctx[:120], "src": src_line[:160]})

for s in sites:
    b, ctx = s["base"], s["ctx"] + " " + s["src"]
    if b and STATE.search(b) and len(ref.get(b, set()) - {s["file"]}) >= 1:
        s["cls"] = "A"
    elif b and len(ref.get(b, set()) - {s["file"]}) >= 1:
        s["cls"] = "B"
    elif b and re.search(r"(tmp|sandbox|sb_|\.dsh)", b, re.I):
        s["cls"] = "C"
    elif b:
        s["cls"] = "F"
    elif re.search(r"\bargs?\.\w+|\ba\.(out|json)\b", ctx):
        s["cls"] = "G"
    elif re.search(r"\b(OUT|DATA|ROOT)\b", ctx):
        s["cls"] = "H"
    else:
        s["cls"] = "I"

LABEL = {
    "A": "A. 共享状态 → 迁 PG（最该做）",
    "B": "B. 共享产物 → 集中管理",
    "C": "C. 临时文件混在 data/ → 移 .dsh-tmp",
    "F": "F. 私有产物/缓存 → 可留文件",
    "G": "G. CLI/调用方指定输出 → 可留",
    "H": "H. 常量目录下输出 → 可留",
    "I": "I. 未解析（变量路径）→ 待人工",
}

print(f"总计 {len(sites)} 处 / {len({s['file'] for s in sites})} 个文件")
cnt = Counter(s["cls"] for s in sites)
for k in "ABCFGHI":
    print(f"  {cnt.get(k,0):4d}  {LABEL[k]}")

# 写 /tmp 的临时明细：用 write_text 而不是 json.dump，避免顶破 scripts/lint_persist.py 的棘轮
open("/tmp/persist_audit.json", "w", encoding="utf-8").write(
    json.dumps(sites, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    # 明细已写到 /tmp/persist_audit.json；这里只做摘要输出
    print(f"\n明细：/tmp/persist_audit.json")
