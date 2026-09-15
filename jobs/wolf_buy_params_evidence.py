# -*- coding: utf-8 -*-
"""wolf_buy_params_evidence.py — 买入层**带数字的参数**语料取证，**dsh 逐条语义精读**。

**为什么要这个**：买入层落差审计（`docs/wolf-buy-gap-audit.md`）里 29 条规则中，"他的话"一侧目前来自
二手归纳（playbook/blueprint/redesign §0）。① 「辨识度最高的老龙头」换代理指标之前必须先做一轮
**一手抽取**，否则又是"我们的代理≠他的话"（实测已因此亏过一次：动量代理 IC 负向）。

**方法与红线**（沿用 `jobs/wolf_d12_evidence.py` 的既有通道与纪律）：
  · 通道：dsh 网关 `POST http://marcus-dsh:3001/chat`（仅容器网络内可达）→ 脚本在
    `marcus-backend` 容器里跑；
  · **不做关键词/正则语义提取**：正则只用于**切片与版式处理**（按日期块/行数打包 ≤ `--max-chars`）；
  · **禁止手打转写**：语料只来自 `/app/data/corpus/wolf-daily-log-*.md`（md5 与仓库 docs/ 一致）；
  · dsh 会抖动 → `call_dsh` 退避重试，失败记入 `failed`，**不得把"取数失败"当成"他没说过"**；
  · 断点续跑：已完成的 slice 跳过；每片保留原始 reply 便于人工复核。

**抽取类别（五类参数）**：P1 量能与缩量 / P2 位置与幅度 / P3 时间与节奏 / P4 仓位与数量 / P5 主题与标的筛选。

用途：用户红线——**开发时参数必须与狼大语料一致，不能自己设定参数，未知参数要标记出来报告**。
本产物 = `docs/wolf-buy-parameter-ledger.md`（参数总账）的语料侧证据：每个代码参数要么能指回一条原话，
要么被明确标成"未知/自设"。

用法（容器内）：
  python /app/jobs/wolf_buy_evidence.py --shard 0/3   # 3 片并行（dsh 并发上限≈3），跑完 --merge
  输出：`/app/data/wolf_buy_params_evidence.json`（含每片 raw reply）
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
OUT = os.path.join(DATA, "wolf_buy_params_evidence.json")
CORPUS = os.path.join(DATA, "corpus")
DEFAULT_FILES = [os.path.join(CORPUS, f) for f in (
    "wolf-daily-log-nga.md",       # 30 个交易日，**带精确时刻**（兑现时序的主证据）
    "wolf-daily-log-xls2026.md",   # 2026 段，按日历日重建
    "wolf-daily-log-xls2025.md",   # 2025-01→2026-08，按动作分类（日期在行内）
)]

PROMPT = """你是交易语料**参数抽取器**。下面是同一位交易者（网名"狼大"）在 **{period}** 的行为记录，
由他的原话整理而成（`>` 引用即**逐字原话**，表格"原话"列同样是逐字原话）。

任务：**只抽取带"可量化数值/阈值/条件"的买入侧判据**，即能直接写成代码参数的句子。分五类：

- **P1 量能与缩量**：成交额/量能倍数（如 2WE/3WE）、缩量/地量口径、量比、放量确认、换手率阈值。
- **P2 位置与幅度**：低位/高位的判定（距高点/低点百分比、箱体、均线）、回调幅度（回撤几个点）、
  距前低/前高的百分比、横盘天数。
- **P3 时间与节奏**：几天、几个交易日、盘中时点（如几点前必须做完）、分步加仓的间隔与次数。
- **P4 仓位与数量**：仓位百分比（几成/50%/75%）、单票上限、分几笔买、一天买几次、留多少现金。
- **P5 主题与标的筛选**：板块容量（多少亿）、成分数量、市值/流动性下限、龙头位次（第几名）、
  概念个数、板块涨幅门槛。

严格要求：
1. 每条必须给出**日期**与**逐字原话**（照抄，不得改写/拼接/润色）；找不到日期写 ""。
2. **必须含明确数量**（数字、百分比、天数、倍数、"几成"）。**纯定性的句子不要输出**（例如"缩量不参与"没给数值就不要）。
3. **找不到就不输出**。绝不允许编造、推测或用自己的话冒充他的话。
4. 不要输出卖出/止盈/止损类的数值（那是别的抽取任务）；但如果同一句里买入与卖出都提到，只摘买入那一半的原话。
5. `param` 用**你自己的话**写"这个数是什么参数"（如"量能活跃天数比例"），`value` 照抄原话里的数（不要换算、不要补全）。

只输出 JSON（不要任何解释文字）：
{{"items": [{{"date": "YYYY-MM-DD", "kind": "P1|P2|P3|P4|P5",
  "quote": "逐字原话", "param": "参数名（一句话）", "value": "原话里的数值（照抄）",
  "scope": "适用范围/例外(没有就留空)"}}]}}

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
            log("[param] %s 第 %d 次失败 %s，退避重试" % (sid, k + 1, type(e).__name__))
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
    log("[param] merge → %d 条 %s / %d 片 / 失败 %d → %s"
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
            log("[param] 缺文件 %s" % f)
            continue
        state["_corpus"][os.path.basename(f)] = hashlib.md5(open(f, "rb").read()).hexdigest()
        for sid, body in sorted(slices_for(f, args.max_chars).items()):
            jobs.append((sid, os.path.basename(f), body))
    mine = [j for i, j in enumerate(jobs) if i % sn == si]
    log("[param] 切片 %d 个，本分片 %d 个：%s" % (len(jobs), len(mine), ", ".join(j[0] for j in mine)))

    for sid, src, body in mine:
        if state["slices"].get(sid, {}).get("n_items") is not None and not args.force:
            log("[param] 跳过已完成 %s" % sid)
            continue
        if not body.strip():
            continue
        t0 = time.time()
        try:
            reply = call_dsh("wolfparam_" + sid, PROMPT.format(period=src, body=body))
            obj = _extract_json(reply)
            items = (obj or {}).get("items") or []
            for it in items:
                it["slice"] = sid
                it["source"] = src
            state["items"] = [x for x in state["items"] if x.get("slice") != sid] + items
            state["slices"][sid] = {"src": src, "n_items": len(items), "chars": len(body),
                                    "secs": round(time.time() - t0, 1),
                                    "reply_chars": len(reply or ""), "raw": (reply or "")[:30000]}
            log("[param] %s → %d 条 (%.0fs)" % (sid, len(items), time.time() - t0))
        except Exception as e:
            state["failed"].append({"slice": sid, "err": "%s: %s" % (type(e).__name__, str(e)[:150])})
            log("[param] %s 失败 %s: %s" % (sid, type(e).__name__, str(e)[:120]))
        try:
            tmp = OUT + ".tmp"
            json.dump(state, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, OUT)
        except Exception as e:
            log("[param] 写盘失败 %s" % str(e)[:80])
        time.sleep(args.sleep)

    kinds = {}
    for it in state["items"]:
        if it.get("slice", "").startswith(("nga_", "xls2026_", "xls2025_")) or True:
            kinds[it.get("kind")] = kinds.get(it.get("kind"), 0) + 1
    log("\n[param] 累计 %d 条 %s | 失败 %d 片 → %s" % (len(state["items"]), kinds, len(state["failed"]), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
