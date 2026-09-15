# -*- coding: utf-8 -*-
"""mine_xlsx_buy_evidence.py — 对 **md 语料未覆盖**的那部分 xlsx，做一次真正的"逐条精读"抽取。

背景：总账 §22 实测派生 md 只覆盖原表他本人交易相关发言的 **38.1%**；
§23 的 `jobs/scan_xlsx_gaps.py` 在未被覆盖的 62% 里用关键词+数值筛出 **150 条"买入侧且带数字"的句子**，
并做了人工分类（当日操作 97 / 判据句 33 / 规则句 20）。但**关键词分类不能替代精读** ——
本脚本把**这 150 条所在的完整原帖**（带发帖时间）切片，用与 `jobs/wolf_buy_evidence.py` **完全相同的
七类提示词**（B1–B7）交给 dsh 逐条读，产出可与既有取证产物对齐的判据。

纪律（沿用既有通道）：
  · 通道 = dsh 网关 `POST http://marcus-dsh:3001/chat`（仅容器网络），故**在容器内跑**；
  · **不做正则语义提取**：正则只用来找"这条句子属于哪个原帖"与切片打包；
  · 语料 = 仓库根 xlsx（权威源），**不手打**；
  · 失败要显式记录（不得把"取数失败"当成"他没说"）。

用法（容器内）::

    python /app/jobs/mine_xlsx_buy_evidence.py --build            # 只切片
    python /app/jobs/mine_xlsx_buy_evidence.py --run --shard 0/1  # 跑某一片
    python /app/jobs/mine_xlsx_buy_evidence.py --merge            # 汇总
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
CHAT_URL = os.getenv("MAIN_LINE_CHAT_URL", "http://marcus-dsh:3001/chat")
TIMEOUT = float(os.getenv("WOLF_LLM_TIMEOUT", "900"))
DATA = os.environ.get("DATA_DIR", "/app/data")
XLSX = os.getenv("WOLF_XLSX", "/app/狼大回复汇总 20260814-1457&往期.xlsx")
SCAN = os.getenv("WOLF_GAP_SCAN", "/app/data/xlsx_gap_scan.json")
SLICE_DIR = os.path.join(DATA, "xlsx_buy_slices")
OUT = os.path.join(DATA, "xlsx_buy_evidence.json")

# 与 jobs/wolf_buy_evidence.py 同一套提示词（保持七类口径一致，便于合并对账）
PROMPT = """你是交易语料判据抽取器。下面是同一位交易者（网名"狼大"）若干**原始发帖**（带发帖时间）。
注意：这些帖子**此前没有被整理进我们的日志**，可能包含新的判据，也可能只是当日的操作流水。

任务：只抽取**与"买什么 / 什么位置买 / 什么时候加仓 / 买多少"有关**的判据，分七类：
- **B1 选股·辨识度与龙头** / **B2 选股·不买什么** / **B3 买点·时机与形态** / **B4 买点·前提（环境门）**
- **B5 加仓** / **B6 仓位与资金** / **B7 组合与 ETF**

严格要求：
1. 每条给出**日期**与**逐字原话**（照抄，不得改写、不得拼接、不得润色）；
2. **找不到就不输出**；绝不允许编造或用自己的话冒充他的话；
3. 只要**买入侧**判据；**不要**输出卖出/止盈/止损/兑现；
4. 纯心态、纯复盘感慨、纯提问、没有买入判据的句子**不要输出**；
5. 若某条判据与"当日仓位/当日持仓"这类**一次性事实**有关，标注 `scope="当日操作"`；
   若能提炼出**可复用条件**，`trigger` 写条件、`action` 写动作；原话里没有的数值**留空**，不要补。

只输出 JSON（不要任何解释文字）：
{{"items": [{{"date": "YYYY-MM-DD", "kind": "B1|B2|B3|B4|B5|B6|B7",
  "quote": "逐字原话", "trigger": "前提条件(一句话)", "action": "买入动作(一句话)",
  "scope": "适用范围/例外(没有就留空；一次性事实写 当日操作)"}}]}}

===== 语料开始 =====
{body}
===== 语料结束 ====="""


def log(*a):
    print(*a, flush=True)


def load_posts():
    """xlsx → [(sheet, date, text)]（他本人、非引用、≥16 字）。"""
    import pandas as pd
    xl = pd.ExcelFile(XLSX)
    out = []
    for sh in xl.sheet_names:
        df = xl.parse(sh, header=None, dtype=str)
        for _, r in df.iterrows():
            m = re.match(r"(\d{4}-\d{2}-\d{2})", str(r.get(0) or ""))
            d8 = m.group(1) if m else ""
            for c in list(df.columns)[1:]:
                v = r.get(c)
                if isinstance(v, str) and len(v.strip()) >= 16 and "[quote]" not in v:
                    out.append((sh, d8, v.strip()))
    return out


def build(max_chars=36000):
    scan = json.load(open(SCAN, encoding="utf-8"))
    sents = [it["sentence"] for it in (scan.get("new_items") or [])]
    log("[mine] 缺口句子 %d 条" % len(sents))
    posts = load_posts()
    hit, seen = [], set()
    for sh, d8, txt in posts:
        for s in sents:
            if s[:40] in txt and (sh, d8, txt[:60]) not in seen:
                seen.add((sh, d8, txt[:60]))
                hit.append((d8, sh, txt))
                break
    hit.sort(key=lambda x: x[0])
    log("[mine] 命中原帖 %d 条（去重后）" % len(hit))
    os.makedirs(SLICE_DIR, exist_ok=True)
    slices, buf, idx = [], "", 1
    for d8, sh, txt in hit:
        block = "\n### %s（%s）\n%s\n" % (d8 or "?", sh, txt)
        if len(buf) + len(block) > max_chars and buf:
            slices.append((idx, buf)); idx += 1; buf = block
        else:
            buf += block
    if buf:
        slices.append((idx, buf))
    for i, body in slices:
        p = os.path.join(SLICE_DIR, "slice_%02d.md" % i)
        open(p, "w", encoding="utf-8").write(body)
        log("[mine] 写出 %s（%d 字）" % (p, len(body)))
    return len(slices)


def call_dsh(sid, message, attempts=5):
    import requests
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    last = None
    for k in range(attempts):
        try:
            r = requests.post(CHAT_URL, json={"message": message, "session_id": sid},
                              headers={"Content-Type": "application/json"}, timeout=TIMEOUT, verify=False)
            r.raise_for_status()
            return (r.json() or {}).get("reply") or ""
        except Exception as e:
            last = e
            log("[mine] %s 第 %d 次失败 %s，退避重试" % (sid, k + 1, type(e).__name__))
            time.sleep(10 * (k + 1))
    raise last


def _extract_json(reply):
    if not reply:
        return None
    for s in re.findall(r"\{.*\}", reply, re.S):
        try:
            o = json.loads(s)
            if isinstance(o, dict) and "items" in o:
                return o
        except Exception:
            continue
    return None


def run(shard="0/1"):
    i, n = [int(x) for x in shard.split("/")]
    files = sorted(f for f in os.listdir(SLICE_DIR) if f.endswith(".md"))
    mine = [f for k, f in enumerate(files) if k % n == i]
    log("[mine] 分片 %s → %d 片：%s" % (shard, len(mine), mine))
    rep = {}
    if os.path.exists(OUT):
        try:
            rep = json.load(open(OUT, encoding="utf-8")) or {}
        except Exception:
            rep = {}
    rep.setdefault("slices", {})
    rep.setdefault("failed", [])
    for f in mine:
        if f in rep["slices"]:
            log("[mine] %s 已完成，跳过" % f)
            continue
        body = open(os.path.join(SLICE_DIR, f), encoding="utf-8").read()
        try:
            reply = call_dsh("mine_xlsx_" + f, PROMPT.format(body=body))
        except Exception as e:
            rep["failed"].append({"slice": f, "err": str(e)[:200]})
            log("[mine] %s 失败：%s" % (f, str(e)[:120]))
            continue
        o = _extract_json(reply) or {"items": [], "_parse_failed": True}
        rep["slices"][f] = {"n": len(o.get("items") or []), "items": o.get("items") or [],
                            "raw_head": (reply or "")[:400]}
        log("[mine] %s → %d 条" % (f, len(o.get("items") or [])))
        json.dump(rep, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(rep, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("[mine] 写出 %s（已完成片 %d，失败 %d）" % (OUT, len(rep["slices"]), len(rep["failed"])))
    return 0


def merge():
    rep = json.load(open(OUT, encoding="utf-8"))
    items, seen = [], set()
    for f, v in (rep.get("slices") or {}).items():
        for it in v.get("items") or []:
            key = (it.get("date"), (it.get("quote") or "")[:40])
            if key in seen:
                continue
            seen.add(key)
            it["slice"] = f
            items.append(it)
    by_kind = {}
    for it in items:
        by_kind[it.get("kind") or "?"] = by_kind.get(it.get("kind") or "?", 0) + 1
    log("[mine] 合并 %d 条（去重后），按类：%s" % (len(items), by_kind))
    rep["items"] = items
    rep["by_kind"] = by_kind
    json.dump(rep, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("[mine] 写出 %s" % OUT)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--shard", default="0/1")
    args = ap.parse_args()
    if args.build:
        return build()
    if args.run:
        return run(args.shard)
    if args.merge:
        return merge()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
