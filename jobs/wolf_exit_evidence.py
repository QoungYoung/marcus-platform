# -*- coding: utf-8 -*-
"""wolf_exit_evidence.py — 兑现层（出场/减仓/止盈/止损）语料取证，**dsh 逐条语义精读**。

**为什么要这个**：`docs/plan-exit-alignment.md` 阶段 1（R1–R5）与阶段 3（R8）的每条兑现规则都必须
能指回狼大的**原话+日期**（用户红线：不做未经语料支撑的自造机制）。此前兑现层的原话分散在
`wolf-playbook.md` §8/§9/§10 的二手归纳里 → 本脚本按用户要求做**一轮专门抽取**，把兑现层原话
结构化落盘，供规则设计与验收直接引用。

**方法与红线**（沿用 `jobs/wolf_d12_evidence.py` 的既有通道与纪律）：
  · 通道：dsh 网关 `POST http://marcus-dsh:3001/chat`（仅容器网络内可达）→ 脚本在
    `marcus-backend` 容器里跑；
  · **不做关键词/正则语义提取**：正则只用于**切片与版式处理**（按日期块/行数打包 ≤ `--max-chars`）；
  · **禁止手打转写**：语料只来自 `/app/data/corpus/wolf-daily-log-*.md`（md5 与仓库 docs/ 一致）；
  · dsh 会抖动 → `call_dsh` 退避重试，失败记入 `failed`，**不得把"取数失败"当成"他没说过"**；
  · 断点续跑：已完成的 slice 跳过；每片保留原始 reply 便于人工复核。

**抽取类别（兑现层七类）**：E1 做T兑现 / E2 离场触发 / E3 减仓 / E4 止盈保护 / E5 避险兑现 /
E6 止损 / E7 尾段与结束。

用法（容器内）：
  python /app/jobs/wolf_exit_evidence.py --files /app/data/corpus/wolf-daily-log-nga.md [--shard 0/3]
  输出：`/app/data/wolf_exit_evidence.json`（含每片 raw reply）
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

CHAT_URL = os.getenv("MAIN_LINE_CHAT_URL", "http://marcus-dsh:3001/chat")
TIMEOUT = float(os.getenv("WOLF_LLM_TIMEOUT", "600"))
DATA = os.environ.get("DATA_DIR", "/app/data")
OUT = os.path.join(DATA, "wolf_exit_evidence.json")
CORPUS = os.path.join(DATA, "corpus")
DEFAULT_FILES = [os.path.join(CORPUS, f) for f in (
    "wolf-daily-log-nga.md",       # 30 个交易日，**带精确时刻**（兑现时序的主证据）
    "wolf-daily-log-xls2026.md",   # 2026 段，按日历日重建
    "wolf-daily-log-xls2025.md",   # 2025-01→2026-08，按动作分类（日期在行内）
)]

PROMPT = """你是交易语料判据抽取器。下面是同一位交易者（网名"狼大"）在 **{period}** 的行为记录，
由他的原话整理而成（`>` 引用即**逐字原话**，表格里的"原话"列同样是逐字原话）。

任务：只抽取**与"卖出 / 出场 / 兑现"有关**的判据，分七类：

- **E1 做T兑现**：日内做 T 的目标幅度、什么时候了结、是否留隔夜、没到目标怎么办（"力度不够也收工"这类）。
- **E2 离场触发**：什么信号出现就直接走 / 就出（破某条线、形态走坏、二次冲高缩量不过前高、到压力位、破位…）。
- **E3 减仓**：减多少（吃一口减一半、板上减、减 1/3…）、什么环境下减（板块高潮、高位方向、轮动、卖强留弱/留强丢弱）。
- **E4 止盈保护**：中轨/上轨/均线/被动止盈/移动止盈/浮盈达标后怎么保护利润、分批止盈。
- **E5 避险兑现**：周末、长假、事件（讲话/数据/议息）前、尾盘、隔夜仓的处置。
- **E6 止损**：止损条件（指数破位/个股逻辑/黑天鹅）、不割肉的情形（地量）、是否要收盘确认、什么时段不止损。
- **E7 尾段与结束**：行情尾段/最后一波怎么办、什么时候大减或清仓、"行情结束"的判断。

严格要求：
1. 每条必须给出**日期**（找不到日期就写 ""）与**逐字原话**（照抄，不得改写、不得拼接、不得润色）。
2. **找不到就不输出**。绝不允许编造、推测、或用你自己的话冒充他的话。
3. 不要输出泛泛的交易道理/心态纪律；不要输出**买入/选股/方向判断**（那是别的抽取任务）。
4. 同一句话最多归一类；宁可少而准。
5. `trigger`/`action`/`magnitude`/`scope` 用**你自己的话**做一句话提炼，且必须严格来自该条原话；
   原话里没有的信息（例如具体百分比）**留空字符串**，不要补。

只输出 JSON（不要任何解释文字）：
{{"items": [{{"date": "YYYY-MM-DD", "kind": "E1|E2|E3|E4|E5|E6|E7",
  "quote": "逐字原话", "trigger": "触发前提(一句话)", "action": "动作(一句话)",
  "magnitude": "幅度/数量(没有就留空)", "scope": "适用范围/例外(没有就留空)"}}]}}

===== 语料开始 =====
{body}
===== 语料结束 ====="""


def log(*a):
    print(*a, flush=True)


def call_dsh(sid, message, attempts=6):
    """dsh 网关偶发断连（RemoteDisconnected/拒绝连接）→ 退避重试；失败必须显式记录，不能当"没有"。"""
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
            log("[exit] %s 第 %d 次失败 %s，退避重试" % (sid, k + 1, type(e).__name__))
            time.sleep(8 * (k + 1))
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
    try:
        o = json.loads(reply.strip())
        if isinstance(o, dict):
            return o
    except Exception:
        pass
    return None


def slices_for(path, max_chars=40000):
    """切片（**只做版式处理，不做语义筛选**）：

    A 版式：`### YYYY-MM-DD` 日块（NGA / xls2026）→ 按日块打包到 ≤ max_chars；
    B 版式：其余（xls2025/xls2122 动作分类表）→ 按行打包到 ≤ max_chars（日期在行内，保持原样）。
    """
    txt = open(path, encoding="utf-8").read()
    tag = os.path.basename(path).replace("wolf-daily-log-", "").replace(".md", "")
    blocks = re.split(r"\n(?=#{2,3} \d{4}-\d{2}-\d{2})", txt)
    has_dayblocks = sum(1 for b in blocks if re.match(r"#{2,3} \d{4}-\d{2}-\d{2}", b)) > 3
    units = blocks if has_dayblocks else [txt[i:i + max_chars] for i in range(0, len(txt), max_chars)]
    if not has_dayblocks:
        # 按行边界切，避免把一句话截断
        units, buf = [], ""
        for line in txt.splitlines(True):
            if len(buf) + len(line) > max_chars:
                units.append(buf)
                buf = line
            else:
                buf += line
        if buf:
            units.append(buf)
    out, buf = {}, ""
    idx = 0
    for u in units:
        if buf and len(buf) + len(u) > max_chars:
            out["%s_%02d" % (tag, idx)] = buf
            idx += 1
            buf = u
        else:
            buf += ("\n" if buf else "") + u
    if buf:
        out["%s_%02d" % (tag, idx)] = buf
    return out


def _merge():
    """各分片跑完后合并：以 (slice, kind, quote) 去重，保留所有 raw reply 供复核。"""
    import glob
    parts = [OUT] + sorted(glob.glob(OUT + ".shard*"))
    merged = {"items": [], "slices": {}, "failed": [], "_corpus": {}}
    seen = set()
    for p in parts:
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for sid, meta in (d.get("slices") or {}).items():
            merged["slices"].setdefault(sid, meta)
        for f in (d.get("failed") or []):
            if f not in merged["failed"]:
                merged["failed"].append(f)
        merged["_corpus"].update(d.get("_corpus") or {})
        for it in (d.get("items") or []):
            k = (it.get("slice"), it.get("kind"), (it.get("quote") or "")[:120])
            if k in seen:
                continue
            seen.add(k)
            merged["items"].append(it)
    json.dump(merged, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    kinds = {}
    for it in merged["items"]:
        kinds[it.get("kind")] = kinds.get(it.get("kind"), 0) + 1
    log("[exit] merge → %d 条 %s / %d 片 / 失败 %d → %s"
        % (len(merged["items"]), kinds, len(merged["slices"]), len(merged["failed"]), OUT))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--max-chars", type=int, default=40000)
    ap.add_argument("--shard", default="0/1", help="分片跑：i/n（dsh 并发上限≈3）")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sleep", type=float, default=5.0)
    ap.add_argument("--merge", action="store_true", help="把 OUT.shard* 合并进 OUT（各分片跑完后执行）")
    args = ap.parse_args()
    if args.merge:
        return _merge()
    si, sn = (int(x) for x in args.shard.split("/"))
    global OUT
    if sn > 1:                     # 并发分片各写各的文件（避免读-改-写互相覆盖），结束后 --merge 合并
        OUT = OUT + ".shard%d" % si

    state = {"items": [], "slices": {}, "failed": [], "_corpus": {}, "_shard": args.shard}
    if os.path.exists(OUT) and not args.force:
        for _ in range(3):
            try:
                state = json.load(open(OUT, encoding="utf-8"))
                break
            except Exception:
                time.sleep(2)
    state.setdefault("items", []); state.setdefault("slices", {}); state.setdefault("failed", [])
    state.setdefault("_corpus", {})

    jobs = []
    for f in [x.strip() for x in args.files.split(",") if x.strip()]:
        if not os.path.exists(f):
            log("[exit] 缺文件 %s" % f)
            continue
        state["_corpus"][os.path.basename(f)] = hashlib.md5(open(f, "rb").read()).hexdigest()
        for sid, body in sorted(slices_for(f, args.max_chars).items()):
            jobs.append((sid, os.path.basename(f), body))
    mine = [j for i, j in enumerate(jobs) if i % sn == si]
    log("[exit] 切片 %d 个，本分片 %d 个：%s" % (len(jobs), len(mine), ", ".join(j[0] for j in mine)))

    for sid, src, body in mine:
        if state["slices"].get(sid, {}).get("n_items") is not None and not args.force:
            log("[exit] 跳过已完成 %s" % sid)
            continue
        if not body.strip():
            continue
        t0 = time.time()
        try:
            reply = call_dsh("wolfexit_" + sid, PROMPT.format(period=src, body=body))
            obj = _extract_json(reply)
            items = (obj or {}).get("items") or []
            for it in items:
                it["slice"] = sid
                it["source"] = src
            state["items"] = [x for x in state["items"] if x.get("slice") != sid] + items
            state["slices"][sid] = {"src": src, "n_items": len(items), "chars": len(body),
                                    "secs": round(time.time() - t0, 1),
                                    "reply_chars": len(reply or ""), "raw": (reply or "")[:30000]}
            log("[exit] %s → %d 条 (%.0fs)" % (sid, len(items), time.time() - t0))
        except Exception as e:
            state["failed"].append({"slice": sid, "err": "%s: %s" % (type(e).__name__, str(e)[:150])})
            log("[exit] %s 失败 %s: %s" % (sid, type(e).__name__, str(e)[:120]))
        try:
            tmp = OUT + ".tmp"
            json.dump(state, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, OUT)
        except Exception as e:
            log("[exit] 写盘失败 %s" % str(e)[:80])
        time.sleep(args.sleep)

    kinds = {}
    for it in state["items"]:
        if it.get("slice", "").startswith(("nga_", "xls2026_", "xls2025_")) or True:
            kinds[it.get("kind")] = kinds.get(it.get("kind"), 0) + 1
    log("\n[exit] 累计 %d 条 %s | 失败 %d 片 → %s" % (len(state["items"]), kinds, len(state["failed"]), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
