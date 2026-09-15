# -*- coding: utf-8 -*-
"""scan_dict_params.py — 参数载体的**第四档**：字典型默认值（2026-09-15 round 32，总账 §42）。

前三档（都已被扫过）：
  ① env 键、② 模块级**标量**大写常量（`jobs/build_param_ledger.py --coverage`，总账 §19）、
  ③ 函数默认值/局部字面量（`jobs/scan_literal_params.py`，总账 §41）。
漏掉的一档：**字典型默认值的内部键** —— 例如
  · `t_build.BUILD_PARAMS_DEFAULT`（做T建仓参数 **50 键**：门槛/评分权重/时机/规模分档）
  · `trend_confirm.TREND_CFG`（10 键）、`position_class.CONFIG`（11 键）、`wave_alloc.WAVE_ALLOC`（5 键）、
    `position_tier.CORPUS_PROFILE`（7 键）、`wolf_trend_stop.DEFAULTS`（5 键）
大写常量扫描只能看到一个容器名，看不到里面**每一个都是可调旋钮**。

用法::

    .venv/bin/python jobs/scan_dict_params.py                     # 列出所有字典型默认值（≥5 键）
    .venv/bin/python jobs/scan_dict_params.py --keys BUILD_PARAMS_DEFAULT   # 展开某个容器的键值
    .venv/bin/python jobs/scan_dict_params.py --min 3             # 放宽到 3 键
"""
import argparse
import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUY_ROOTS = ["apps/main_line", "backend/app/services", "backend/app/api"]
NAME_RX = re.compile(
    r"^(wolf_|t_|position_|pick|confirm|mainline|rotation|theme|direction|decision|step_refill|risk_|sector_|chain_)"
)
NONPROD_RX = re.compile(r"^(backtest|ab_|audit|tmp_|_tmp|probe|exp_|bt_|test_|replay|regress_|eval_|backfill)")
# 纯"词典/映射"（主题→概念、事件码）：不是参数，排除
MAPPING_HINT = re.compile(r"(THEME|CONCEPT|KW|DIRS|SYMS|CODES|EVENT|SEED|ALIAS|PARENT|SUB_UNIVERSE)")


def iter_files(roots=None):
    for root in (roots or BUY_ROOTS):
        base = os.path.join(REPO, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                mod = f[:-3]
                if NONPROD_RX.match(mod):
                    continue
                if NAME_RX.match(mod) or "main_line" in dirpath:
                    yield os.path.join(dirpath, f)


def _dict_keys(node):
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys if isinstance(k, ast.Constant)]
    return []


def scan(roots=None, min_keys=5, include_mappings=False):
    """→ [{file, line, name, keys:[...]}]（模块级大写常量且值为 dict 字面量）。"""
    out = []
    for path in iter_files(roots):
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        except Exception:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Dict) or len(node.value.keys) < min_keys:
                continue
            for t in node.targets:
                if not (isinstance(t, ast.Name) and t.id.isupper()):
                    continue
                if not include_mappings and MAPPING_HINT.search(t.id):
                    continue
                out.append({"file": os.path.relpath(path, REPO), "line": node.lineno,
                            "name": t.id, "keys": _dict_keys(node.value)})
    return out


def dump_keys(name, roots=None):
    """展开某个容器的键 → 值（只取字面量，表达式记 '<expr>'）。"""
    for path in iter_files(roots):
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                    and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                rows = []
                for k, v in zip(node.value.keys, node.value.values):
                    kk = k.value if isinstance(k, ast.Constant) else "<expr>"
                    try:
                        vv = repr(ast.literal_eval(v))
                    except Exception:
                        vv = "<expr:%s>" % type(v).__name__
                    rows.append((kk, vv))
                return os.path.relpath(path, REPO), node.lineno, rows
    return None, None, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", default="", help="展开指定容器的键值")
    ap.add_argument("--min", type=int, default=5, help="最少键数（默认 5）")
    ap.add_argument("--include-mappings", action="store_true", help="连主题/事件映射词典一起列")
    args = ap.parse_args()

    if args.keys:
        f, ln, rows = dump_keys(args.keys)
        if not rows:
            print("未找到容器:", args.keys)
            return 1
        print("%s:%d  %s（%d 键）" % (f, ln, args.keys, len(rows)))
        for k, v in rows:
            print("   %-34s = %s" % (k, v[:90]))
        return 0

    rows = scan(min_keys=args.min, include_mappings=args.include_mappings)
    rows.sort(key=lambda r: -len(r["keys"]))
    print("[dicts] 字典型默认值常量 %d 个（≥%d 键，已排除主题/事件映射词典）" % (len(rows), args.min))
    for r in rows:
        print("  %-52s %-28s 键=%3d  例: %s"
              % (r["file"], r["name"], len(r["keys"]), ", ".join(map(str, r["keys"][:5]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
