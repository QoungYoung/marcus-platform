# -*- coding: utf-8 -*-
"""verify_exit_evidence.py — 兑现层语料抽取的**真实性校验**（不看语义，只查"这句原话在不在语料里"）。

为什么需要：dsh 精读的输出必须能指回原文（用户红线：语料绝不手打转写、绝不编造）。
本脚本对 `/app/data/wolf_exit_evidence.json` 的每条 `quote` 做两级核对：
  ① **严格**：去掉空白后，quote 是否为语料文件的连续子串；
  ② **标点归一**：把中英文标点/引号/空白全部去掉后再做子串匹配（防 dsh 把"，"写成","）。
两级都不命中的条目 → 进 `suspect`，**必须人工回原文核**（或重跑该片）。

用法（本地或容器内）：
  python jobs/verify_exit_evidence.py --evidence data/wolf_exit_evidence.json \
      --corpus /app/data/corpus:/home/fengx/marcus-platform/docs
输出：`<evidence>.verify.json` + 控制台汇总。
"""
import argparse
import collections
import json
import os
import re
import sys

PUNCT = "".join(["　", " ", "\t", "\n", "\r", "，", ",", "。", ".", "、", "；", ";", "：", ":",
                 "“", "”", "\"", "‘", "’", "'", "！", "!", "？", "?", "（", "）", "(", ")",
                 "【", "】", "[", "]", "《", "》", "<", ">", "—", "-", "－", "·", "…", "~", "～"])


def norm_strict(s):
    return re.sub(r"\s+", "", str(s or ""))


def norm_punct(s):
    s = norm_strict(s)
    for ch in PUNCT:
        s = s.replace(ch, "")
    return s


def load_corpus(paths):
    """返回 {文件名: (strict_text, punct_text)}。"""
    out = {}
    for p in paths:
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        out[os.path.basename(p)] = (norm_strict(txt), norm_punct(txt))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default="/app/data/wolf_exit_evidence.json")
    ap.add_argument("--corpus", default="", help="逗号分隔的目录或文件；目录里的 *.md 全部载入")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    files = []
    for c in [x for x in (args.corpus or "").split(",") if x.strip()]:
        if os.path.isdir(c):
            files += [os.path.join(c, f) for f in sorted(os.listdir(c)) if f.endswith(".md")]
        else:
            files.append(c)
    corp = load_corpus(files)
    if not corp:
        print("[verify] 没载入任何语料文件，检查 --corpus")
        return 2

    d = json.load(open(args.evidence, encoding="utf-8"))
    items = d.get("items") or []
    res = {"n": len(items), "strict": 0, "punct": 0, "miss": 0, "by_kind": {}, "by_source": {},
           "suspect": []}
    for it in items:
        q = it.get("quote") or ""
        if not q.strip():
            res["miss"] += 1
            res["suspect"].append({"reason": "empty_quote", **{k: it.get(k) for k in ("date", "kind", "slice")}})
            continue
        st, pu = norm_strict(q), norm_punct(q)
        hit_strict = any(st and st in t[0] for t in corp.values())
        hit_punct = (not hit_strict) and any(pu and pu in t[1] for t in corp.values())
        src = it.get("source") or (it.get("slice") or "").split("_")[0]
        k = it.get("kind") or "?"
        res["by_kind"].setdefault(k, {"n": 0, "strict": 0, "punct": 0, "miss": 0})
        res["by_source"].setdefault(src, {"n": 0, "strict": 0, "punct": 0, "miss": 0})
        if hit_strict:
            res["strict"] += 1
            res["by_kind"][k]["strict"] += 1
            res["by_source"][src]["strict"] += 1
            flag = "strict"
        elif hit_punct:
            res["punct"] += 1
            res["by_kind"][k]["punct"] += 1
            res["by_source"][src]["punct"] += 1
            flag = "punct"
        else:
            res["miss"] += 1
            res["by_kind"][k]["miss"] += 1
            res["by_source"][src]["miss"] += 1
            flag = "miss"
            res["suspect"].append({"reason": "not_found", "date": it.get("date"), "kind": k,
                                   "slice": it.get("slice"), "quote": q[:160]})
        res["by_kind"][k]["n"] += 1
        res["by_source"][src]["n"] += 1
        it["_verify"] = flag

    out = args.out or (args.evidence + ".verify.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = max(1, res["n"])
    print("[verify] %d 条：严格命中 %d (%.1f%%) / 标点归一中 %d / **未命中 %d**"
          % (res["n"], res["strict"], 100.0 * res["strict"] / n, res["punct"], res["miss"]))
    print("  按类别：")
    for k in sorted(res["by_kind"]):
        v = res["by_kind"][k]
        print("    %-3s n=%-4d strict=%-4d punct=%-3d miss=%-3d" % (k, v["n"], v["strict"], v["punct"], v["miss"]))
    print("  按来源：")
    for k in sorted(res["by_source"]):
        v = res["by_source"][k]
        print("    %-12s n=%-4d strict=%-4d punct=%-3d miss=%-3d" % (k, v["n"], v["strict"], v["punct"], v["miss"]))
    if res["suspect"]:
        print("  未命中样例（必须人工回原文核）：")
        for s in res["suspect"][:10]:
            print("    [%s/%s] %s" % (s.get("kind"), s.get("date"), (s.get("quote") or "")[:90]))
    print("  → %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
