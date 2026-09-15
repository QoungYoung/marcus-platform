# -*- coding: utf-8 -*-
"""scan_literal_params.py — 补一道覆盖率：**函数签名/赋值里的字面量参数**（round 32，总账 §41）。

起因：`jobs/build_param_ledger.py --coverage` 扫的是 `os.getenv` 键 + 模块级**大写**常量，
但买入链里还大量存在**函数默认值/局部字面量**形式的旋钮，例如
`def pick_buy(chain, exclude=None, limit=3)` —— 改它与改 env 键一样会改变"买什么/买多少"，
却不在任何清单里。

判据（本脚本只做定位，判定靠逐条精读）：
  · 形参默认值是数字字面量，且形参名属于"策略语义"白名单（limit/top/n/k/days/win/max/min/pct/ratio/thr…）；
  · 赋值语句右侧是数字字面量，且变量名属于同一白名单；
  · 排除明显的工程常量（timeout/sleep/port/retry/ttl/cache/workers/width/precision…）。

用法::

    .venv/bin/python jobs/scan_literal_params.py                 # 买入链模块（默认）
    .venv/bin/python jobs/scan_literal_params.py --all           # 更大扫描面
"""
import argparse
import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUY_MODULES = [
    "apps/main_line",
    "backend/app/services",
    "backend/app/api",
]

# 只看这些模块名（买入链相关）
NAME_RX = re.compile(
    r"^(wolf_|t_|position_|pick|confirm|mainline|rotation|theme|direction|decision|trade_graph|step_refill)"
)

STRATEGY_WORDS = re.compile(
    r"(limit|top|_n$|^n_|rank|k$|days|win|window|max|min|pct|ratio|thr|tol|gap|frac|cap|floor|"
    r"lookback|confirm|fresh|stale|share|legs|qty|weight|score|drop|rise|slope|band)"
)
ENGINEER_WORDS = re.compile(
    r"(timeout|sleep|interval|port|retry|retries|ttl|cache|worker|thread|width|precision|"
    r"encoding|chunk|batch_size|page|size_kb|max_age_s|backoff|depth|train|trial|epoch|fold|seed)"
)
SKIP_DIRS = ("__pycache__", ".git", "node_modules")
# 非生产文件（回测/审计/A-B/临时）：默认排除，--all 时仍只看生产命名规则
NONPROD_RX = re.compile(r"^(backtest|ab_|audit|tmp_|_tmp|probe|exp_|bt_)")


def iter_files(roots):
    for root in roots:
        base = os.path.join(REPO, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                mod = f[:-3]
                if NONPROD_RX.match(mod):
                    continue
                if NAME_RX.match(mod) or "main_line" in dirpath:
                    yield os.path.join(dirpath, f)


def scan_file(path):
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except Exception:
        return []
    out = []
    rel = os.path.relpath(path, REPO)

    def is_num(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool)

    for node in ast.walk(tree):
        # ① 函数形参默认值
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = list(node.args.args) + list(node.args.kwonlyargs)
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
            for a, d in zip(args[-len(defaults):], defaults) if defaults else []:
                if is_num(d) and STRATEGY_WORDS.search(a.arg) and not ENGINEER_WORDS.search(a.arg):
                    out.append({"file": rel, "line": getattr(d, "lineno", node.lineno),
                                "kind": "默认值", "name": "%s(%s=)" % (node.name, a.arg),
                                "value": repr(d.value)})
        # ② 赋值右侧字面量
        if isinstance(node, ast.Assign) and is_num(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and STRATEGY_WORDS.search(tgt.id) \
                        and not ENGINEER_WORDS.search(tgt.id) and not tgt.id.isupper():
                    out.append({"file": rel, "line": node.lineno, "kind": "赋值",
                                "name": tgt.id, "value": repr(node.value.value)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="扫更大范围（含 core/apps 其它目录）")
    args = ap.parse_args()

    roots = BUY_MODULES + (["core", "apps", "jobs"] if args.all else [])
    rows = []
    for p in iter_files(roots):
        rows.extend(scan_file(p))

    print("[literals] 命中 %d 条（%d 个文件）" % (len(rows), len({r['file'] for r in rows})))
    by_file = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    for f in sorted(by_file):
        print("\n== %s ==" % f)
        for r in sorted(by_file[f], key=lambda x: x["line"]):
            print("   %-5d %-4s %-42s = %s" % (r["line"], r["kind"], r["name"], r["value"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
