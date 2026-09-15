# -*- coding: utf-8 -*-
"""verify_buy_quotes.py — 买入侧模块 docstring 里引用的**狼大原话**是否逐字存在于语料（2026-09-15 round 14）。

为什么要它：总账 §20 把"语料已对齐但未入账"的模块登记进账时，原话是从**模块 docstring** 摘的
（本项目 house style：模块开头就写原话与日期）。但"docstring 里写了原话"不等于"原话在语料里逐字存在" ——
用户红线是**语料绝不手打、绝不编造**，所以这里做一次真实性核对。

三级判定（与 `jobs/verify_exit_evidence.py` 同口径，另加"最长片段"级，因为 docstring 常做省略）：
  ① **strict**：去空白后是否为语料连续子串；
  ② **punct** ：再去掉中英文标点后是否为子串（防"，"写成","）；
  ③ **frag**  ：按省略号/逗号切分后，**最长片段（≥8 字）**能否命中（docstring 里常见「…」省略）。
三级都不中 → 进 `suspect`，**必须人工回原文核**（或修正 docstring）。

用法（本地）::

    .venv/bin/python jobs/verify_buy_quotes.py --modules wolf_gap_open,wolf_day_rules --corpus docs
    .venv/bin/python jobs/verify_buy_quotes.py --all-in-ledger --corpus docs

产出：`.dsh-tmp/buyside/buy_quotes_verify.json` + 控制台汇总。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".dsh-tmp", "buyside", "buy_quotes_verify.json")
LEDGER = os.path.join(ROOT, "docs", "wolf-buy-parameter-ledger.md")

PUNCT = "".join(["　", " ", "\t", "\n", "\r", "，", ",", "。", ".", "、", "；", ";", "：", ":",
                 "“", "”", "\"", "‘", "’", "'", "！", "!", "？", "?", "（", "）", "(", ")",
                 "【", "】", "[", "]", "《", "》", "<", ">", "—", "－", "·", "…", "~", "～",
                 # docstring 里的 markdown/排版标记：不剥掉会把整句判成"未命中"（2026-09-15 实测 45 条里大半是这个原因）
                 "*", "＊", "`", "#", "＞", "→", "＋", "+"])


def strip_md(s):
    """去掉 docstring 的排版标记与我们的编辑性括注（(…)（…）里的说明不算原话）。"""
    s = re.sub(r"\*\*|\*|`", "", str(s or ""))
    return s


def norm_strict(s):
    return re.sub(r"\s+", "", str(s or ""))


def norm_punct(s):
    s = norm_strict(s)
    for ch in PUNCT:
        s = s.replace(ch, "")
    return s


def load_corpus(paths):
    out = {}
    for p in paths:
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        out[os.path.basename(p)] = (norm_strict(txt), norm_punct(txt))
    return out


def load_xlsx(path, kws=None):
    """把原始 xlsx 的全部单元格当语料源（**权威源**：md 是派生导出，内容不全）。

    2026-09-15 实测：`wolf_trade_window` 的「当日只做上午 9.45-10.00…」在 md 里 0 命中、
    在 xlsx 的「2025」sheet 里命中 → 说明 docstring 引的是**原始表**，md 导出不全。
    """
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        import pandas as pd
        xl = pd.ExcelFile(path)
    except Exception as e:
        print("[verify] xlsx 打不开: %s" % str(e)[:80])
        return out
    for sh in xl.sheet_names:
        try:
            df = xl.parse(sh, header=None, dtype=str)
        except Exception:
            continue
        cells = []
        for col in df.columns:
            for v in df[col].dropna().astype(str):
                if len(v) >= 4:
                    cells.append(v)
        if cells:
            txt = "\n".join(cells)
            out["xlsx:%s" % sh] = (norm_strict(txt), norm_punct(txt))
    return out


def _files(spec):
    fs = []
    for c in [x for x in (spec or "").split(",") if x.strip()]:
        if os.path.isdir(c):
            fs += [os.path.join(c, f) for f in sorted(os.listdir(c)) if f.endswith(".md")]
        else:
            fs.append(c)
    return fs


def quotes_of(module):
    """从模块文件里抽 docstring/注释中的「…」原话（去掉过短的与明显非原话的）。"""
    path = None
    for pat in ("apps/main_line/%s.py", "backend/app/services/%s.py", "backend/app/api/%s.py", "core/%s.py"):
        p = os.path.join(ROOT, pat % module)
        if os.path.exists(p):
            path = p
            break
    if path is None:
        return [], None
    src = open(path, encoding="utf-8").read()
    head = src[:6000]                       # docstring/文件头（原话都在这）
    qs = re.findall(r"「([^」]{8,200})」", head)
    seen, out = set(), []
    for q in qs:
        k = norm_punct(q)
        if len(k) < 8 or k in seen:
            continue
        seen.add(k)
        out.append(q)
    return out, path


def check(quote, corp):
    quote = strip_md(quote)
    st, pu = norm_strict(quote), norm_punct(quote)
    if any(st and st in t[0] for t in corp.values()):
        return "strict", ""
    if any(pu and pu in t[1] for t in corp.values()):
        return "punct", ""
    # 最长片段
    frags = [f for f in re.split(r"[…\.]{1,}|，|,|。|；|;", quote) if len(norm_punct(f)) >= 8]
    if frags:
        best = max(frags, key=lambda f: len(norm_punct(f)))
        bf = norm_punct(best)
        hit = next((name for name, t in corp.items() if bf and bf in t[1]), None)
        if hit:
            return "frag", "%d字片段命中@%s" % (len(bf), hit)
    return "miss", ""


def ledger_modules():
    """从总账 §20 的表格里取模块名（`module` 列）。"""
    try:
        s = open(LEDGER, encoding="utf-8").read()
    except Exception:
        return []
    i = s.find("## 20 ")
    seg = s[i:] if i >= 0 else ""
    return sorted(set(re.findall(r"^\| `([a-z0-9_]+)` \|", seg, flags=re.M)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", default="", help="逗号分隔的模块名（不含 .py）")
    ap.add_argument("--all-in-ledger", action="store_true", help="用总账 §20 表里的全部模块")
    ap.add_argument("--corpus", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--xlsx", default=os.path.join(ROOT, "狼大回复汇总 20260814-1457&往期.xlsx"),
                    help="原始语料表（权威源；md 是派生导出）")
    ap.add_argument("--json", default=OUT)
    args = ap.parse_args()

    mods = [m.strip() for m in (args.modules or "").split(",") if m.strip()]
    if args.all_in_ledger or not mods:
        mods = ledger_modules() or mods
    corp = load_corpus(_files(args.corpus))
    corp.update(load_xlsx(args.xlsx))
    if not corp:
        print("[verify] 没载入语料，检查 --corpus / --xlsx")
        return 2
    n_xlsx = sum(1 for k in corp if k.startswith("xlsx:"))
    print("[verify] 模块 %d 个 | 语料源 %d 个（其中 xlsx sheet %d 个）" % (len(mods), len(corp), n_xlsx))

    res = {"n_quotes": 0, "strict": 0, "punct": 0, "frag": 0, "miss": 0, "by_module": {}, "suspect": []}
    for mod in mods:
        qs, path = quotes_of(mod)
        rec = {"file": path and os.path.relpath(path, ROOT), "n": 0, "strict": 0, "punct": 0,
               "frag": 0, "miss": 0, "items": []}
        for q in qs:
            flag, note = check(q, corp)
            rec["n"] += 1
            rec[flag] += 1
            res[flag] += 1
            res["n_quotes"] += 1
            rec["items"].append({"flag": flag, "note": note, "quote": q[:120]})
            if flag == "miss":
                res["suspect"].append({"module": mod, "quote": q[:160]})
        res["by_module"][mod] = rec
        print("  %-24s n=%-3d strict=%-3d punct=%-3d frag=%-3d miss=%-3d"
              % (mod, rec["n"], rec["strict"], rec["punct"], rec["frag"], rec["miss"]))

    n = max(1, res["n_quotes"])
    print("\n[verify] 原话 %d 条：严格 %d (%.1f%%) / 标点归一 %d / 片段命中 %d / **未命中 %d**"
          % (res["n_quotes"], res["strict"], 100.0 * res["strict"] / n, res["punct"], res["frag"], res["miss"]))
    if res["suspect"]:
        print("  未命中（需人工回原文核 / 修 docstring）：")
        for s in res["suspect"][:15]:
            print("    [%s] %s" % (s["module"], s["quote"][:90]))
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump(res, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[verify] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
