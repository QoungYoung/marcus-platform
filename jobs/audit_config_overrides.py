# -*- coding: utf-8 -*-
"""audit_config_overrides.py — 「生效值 vs 代码默认」自查（2026-09-15 round 32，总账 §42）。

起因：跑起来的参数**不一定等于代码默认值**。生产有三层来源且**优先级明确**：

    DB 配置表  →  配置文件（config/ 或 DATA_DIR/ 同名 json）  →  代码内置默认

    · `wolf_discipline_config.cfg_json`（仓位档/止盈/板块半数/周末避险，DB 写、可热改）
    · `t_build_params.params_json`（做T建仓参数覆盖，DB 写）
    · `golden_pit_sector_config`（黄金坑逐 ETF 出入场/载体，DB 写）

于是「我们与语料一致吗」这类断言**必须看生效值**：只看代码默认值可能在 DB 里被覆盖成别的数字
（实测例子：`no_rebuild_symbols` 代码默认 `["SH515880"]`，生产 DB 是 `["SH515880","SZ002409"]`）。

本脚本把 DB 生效值与**代码内置默认**逐键对比，列出：一致 / DB覆盖 / 仅DB / 仅代码。

用法::

    # 本地（用 .dsh-tmp/wolfbt/local_pg.py 的 SSH 隧道）
    .venv/bin/python jobs/audit_config_overrides.py
    # 容器内（直连本机 PG）
    docker exec marcus-worker python /app/jobs/audit_config_overrides.py
    # 指定 DSN
    DATABASE_URL=postgresql://... .venv/bin/python jobs/audit_config_overrides.py
"""
import argparse
import ast
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 纯函数（可单测）────────────────────────────────────────────
def flatten(obj, prefix=""):
    """嵌套 dict → 点号键的扁平表；list/标量原样。"""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = ("%s.%s" % (prefix, k)) if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
    return out


def diff(db, code):
    """→ [{key, db, code, status}]；status ∈ 一致 / DB覆盖 / 仅DB / 仅代码。"""
    rows = []
    for k in sorted(set(db) | set(code)):
        in_db, in_code = k in db, k in code
        if in_db and in_code:
            status = "一致" if db[k] == code[k] else "DB覆盖"
        elif in_db:
            status = "仅DB"
        else:
            status = "仅代码"
        rows.append({"key": k, "db": db.get(k), "code": code.get(k), "status": status})
    return rows


# ── 代码内置默认：AST 读（不 import 生产模块，避免副作用/依赖）────────
def read_module_dict(path, name):
    tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except Exception:
                return {}
    return {}


def read_inline_default(path, func_name="_cfg", var_name="default"):
    """读函数体内 `var_name = {...}` 字面量（wolf_discipline._cfg() 的内置默认就是这么写的）。"""
    tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for sub in node.body:
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Dict) \
                        and any(isinstance(t, ast.Name) and t.id == var_name for t in sub.targets):
                    try:
                        return ast.literal_eval(sub.value)
                    except Exception:
                        return {}
    return {}


# ── DB ────────────────────────────────────────────────────────
def connect():
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        import psycopg2
        return psycopg2.connect(dsn)
    helper = os.path.join(REPO, ".dsh-tmp", "wolfbt")
    if os.path.isdir(helper):
        sys.path.insert(0, helper)
        from local_pg import ensure_tunnel, DSN
        ensure_tunnel()
        import psycopg2
        return psycopg2.connect(**DSN)
    raise SystemExit("需要 DATABASE_URL，或本地存在的 .dsh-tmp/wolfbt/local_pg.py（SSH 隧道）")


def db_json(cur, table, col):
    cur.execute("SELECT %s FROM %s WHERE id=1" % (col, table))
    row = cur.fetchone()
    if not row or row[0] is None:
        return {}
    v = row[0]
    return json.loads(v) if isinstance(v, str) else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-changed", action="store_true", help="只打印非『一致』的行")
    ap.add_argument("--files-only", action="store_true",
                    help="只查 data/*_params.json 文件层覆盖（不需要 PG）")
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"),
                    help="生产数据目录（默认 /app/data；本地审阅时指向快照目录）")
    args = ap.parse_args()

    checks = [
        ("wolf_discipline_config.cfg_json", "wolf_discipline_config", "cfg_json",
         os.path.join(REPO, "backend/app/services/wolf_discipline.py"), "inline"),
        ("t_build_params.params_json", "t_build_params", "params_json",
         os.path.join(REPO, "backend/app/services/t_build.py"), "BUILD_PARAMS_DEFAULT"),
    ]
    # 文件层覆盖：`*_calibrate.py` 写出的校准产物，被 daily chain 用 `--params` 指过去 → **生效值**
    file_checks = [
        ("data/trend_confirm_params.json.params", "trend_confirm_params.json", "params",
         os.path.join(REPO, "apps/main_line/trend_confirm.py"), "TREND_CFG"),
        ("data/trend_gate_params.json.a_params", "trend_gate_params.json", "a_params",
         os.path.join(REPO, "apps/main_line/trend_confirm.py"), "TREND_CFG"),
    ]

    total_bad = 0
    cur = None
    if not args.files_only:
        conn = connect()
        cur = conn.cursor()
        for label, table, col, code_path, mode in checks:
            db = flatten(db_json(cur, table, col))
            code = flatten(read_inline_default(code_path) if mode == "inline"
                           else read_module_dict(code_path, mode))
            rows = diff(db, code)
            bad = [r for r in rows if r["status"] != "一致"]
            total_bad += len(bad)
            print("\n=== %s：DB %d 键 / 代码默认 %d 键 → 差异 %d 处" % (label, len(db), len(code), len(bad)))
            for r in (bad if args.only_changed else rows):
                if r["status"] == "一致" and args.only_changed:
                    continue
                print("  %-44s %-6s DB=%-28s 代码=%s"
                      % (r["key"], r["status"],
                         json.dumps(r["db"], ensure_ascii=False)[:28],
                         json.dumps(r["code"], ensure_ascii=False)[:28]))
        cur.close()
        conn.close()

    for label, fname, sub, code_path, mode in file_checks:
        path = os.path.join(args.data_dir, fname)
        if not os.path.exists(path):
            print("\n=== %s：**文件不存在**（%s）→ 未覆盖，按代码默认" % (label, path))
            continue
        raw = json.load(open(path, encoding="utf-8"))
        filecfg = flatten(raw.get(sub) or {})
        code = flatten(read_module_dict(code_path, mode))
        rows = diff(filecfg, code)
        bad = [r for r in rows if r["status"] != "一致"]
        total_bad += len(bad)
        print("\n=== %s：文件 %d 键 / 代码默认 %d 键 → 差异 %d 处（生成器 %s，日期 %s）"
              % (label, len(filecfg), len(code), len(bad), raw.get("generator", "?"), raw.get("date", "?")))
        if raw.get("note"):
            print("  ⚠️ 文件自带说明：%s" % str(raw["note"])[:120])
        for r in (bad if args.only_changed else rows):
            if r["status"] == "一致" and args.only_changed:
                continue
            status = "文件覆盖" if r["status"] == "DB覆盖" else r["status"]
            print("  %-44s %-6s 文件=%-26s 代码=%s"
                  % (r["key"], status,
                     json.dumps(r["file"] if "file" in r else r["db"], ensure_ascii=False)[:26],
                     json.dumps(r["code"], ensure_ascii=False)[:26]))

    print("\n[overrides] 差异合计 %d 处 —— **任何「与语料一致」的断言都应基于这里的生效值"
          "（DB 与 data/*_params.json 两层都要核）**" % total_bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
