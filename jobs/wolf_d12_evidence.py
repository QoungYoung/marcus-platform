# -*- coding: utf-8 -*-
"""wolf_d12_evidence.py — D12 语料取证：他对"方向有没有行情 / 回避哪些方向"的原话判据。

**为什么要这个**：D1 方向层（gate ∩ 近5日相对强度）在 1/3/8 月把**农业(28天)/医药(20天)**推到第一，
而他 2026 全年**农业 0 天**、医药 5 天。要修这个偏差，过滤条件**必须有语料支撑**（用户红线：
不做未经语料支撑的自造机制），所以先把他的原话判据取出来，再设计/验收。

**方法**：按**月**切片 `docs/wolf-daily-log-*.md`（逐日整理稿，原话完整保留），每片交给 dsh
（`POST http://marcus-dsh:3001/chat`，项目既有通道）做**语义精读**抽取三类判据：
  A 如何判断方向"有没有行情"（量能/成交/带动/持续性/催化/联动…）
  B 明确回避/不参与的方向或方向类型 + 理由
  C 主线 / 支线 / 补涨 / 后排 / 防御 / 滞涨 相关表述
**不用正则/关键词匹配**（用户长期要求：语料结构化走 dsh）。
输出 `/app/data/wolf_d12_evidence.json`（含每片原始 reply，便于核对，防手打转写）。

用法：python jobs/wolf_d12_evidence.py [--files a.md,b.md] [--force]
"""
import json
import os
import re
import sys
import time

CHAT_URL = os.getenv("MAIN_LINE_CHAT_URL", "http://marcus-dsh:3001/chat")
TIMEOUT = float(os.getenv("WOLF_LLM_TIMEOUT", "300"))
DATA = os.environ.get("DATA_DIR", "/app/data")
OUT = os.path.join(DATA, "wolf_d12_evidence.json")
DEFAULT_FILES = [
    os.path.join(DATA, "corpus", "wolf-daily-log-xls2026.md"),
    os.path.join(DATA, "corpus", "wolf-daily-log-nga.md"),
]

PROMPT = """你是交易语料判据抽取器。下面是同一位交易者（网名"狼大"）在 **{period}** 的逐日行为记录，
由他的原话整理而成（`>` 引用即原话）。

任务：只抽取与**方向/题材/板块的取舍**有关的判据，分三类：

- **A 类**：他**如何判断一个方向/题材"有没有行情"**——可执行的依据（量能、成交量、板块带动、
  持续性、消息催化、龙头/辨识度、相对强弱、资金认可度…），只要他说出了"凭什么认为有/没有行情"就归 A。
- **B 类**：他**明确回避 / 不参与某个方向或某类方向**，以及**理由**（含"这种我不碰""不是我的体系""不做…"）。
- **C 类**：关于**主线 / 支线 / 补涨 / 后排 / 跟风 / 防御 / 滞涨 / 轮动**的表述与判据。

严格要求：
1. 每条必须给出**日期**与**逐字原话**（从 `>` 引用里摘，不得改写、不得拼接、不得润色）。
2. **找不到就不输出**。绝不允许编造、推测、或用你自己的话冒充他的话。
3. 不要输出泛泛的交易道理、仓位管理、心态纪律、个股买卖点——只要**方向取舍**相关内容。
4. 同一句话最多归一类；宁可少而准。

只输出 JSON（不要任何解释文字）：
{{"items": [{{"date": "YYYY-MM-DD", "kind": "A|B|C", "quote": "原话", "criterion": "一句话可执行判据"}}]}}

===== 语料开始 =====
{body}
===== 语料结束 ====="""


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


def call_dsh(sid, message, attempts=4):
    """dsh 网关会偶发断连（实测 RemoteDisconnected）→ 退避重试，别把抖动当"没有依据"。"""
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
            print("[d12] %s 第 %d 次失败 %s，退避重试" % (sid, k + 1, type(e).__name__), flush=True)
            time.sleep(8 * (k + 1))
    raise last


def slices_for(path):
    """按 `###/## YYYY-MM-DD` 切日期块，再按 (年-月) 聚合。"""
    txt = open(path, encoding="utf-8").read()
    parts = re.split(r"\n(?=#{2,3} \d{4}-\d{2}-\d{2})", txt)
    out = {}
    for p in parts:
        m = re.match(r"#{2,3} (\d{4}-\d{2})-\d{2}", p)
        if not m:
            continue
        out.setdefault(m.group(1), []).append(p)
    return {k: "\n".join(v) for k, v in out.items()}


def main():
    files = DEFAULT_FILES
    if "--files" in sys.argv:
        files = [f.strip() for f in sys.argv[sys.argv.index("--files") + 1].split(",")]
    force = "--force" in sys.argv
    state = {"items": [], "slices": {}, "failed": []}
    if os.path.exists(OUT) and not force:
        try:
            state = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            pass

    jobs = []
    for f in files:
        if not os.path.exists(f):
            print("[d12] 缺文件 %s" % f, flush=True)
            continue
        tag = os.path.basename(f).replace("wolf-daily-log-", "").replace(".md", "")
        for period, body in sorted(slices_for(f).items()):
            jobs.append((tag + "_" + period, period, body))
    print("[d12] 切片 %d 个（%s）" % (len(jobs), ", ".join(j[0] for j in jobs)), flush=True)

    for sid, period, body in jobs:
        if state["slices"].get(sid, {}).get("n_items") and not force:
            print("[d12] 跳过已完成 %s (%d 条)" % (sid, state["slices"][sid]["n_items"]), flush=True)
            continue
        t0 = time.time()
        try:
            reply = call_dsh("wolfd12_" + sid, PROMPT.format(period=period, body=body))
            obj = _extract_json(reply)
            items = (obj or {}).get("items") or []
            for it in items:
                it["slice"] = sid
            state["items"] = [x for x in state["items"] if x.get("slice") != sid] + items
            state["slices"][sid] = {"n_items": len(items), "chars": len(other := body),
                                    "secs": round(time.time() - t0, 1),
                                    "reply_chars": len(reply or ""), "raw": (reply or "")[:20000]}
            print("[d12] %s → %d 条 (%.0fs)" % (sid, len(items), time.time() - t0), flush=True)
        except Exception as e:
            state["failed"].append({"slice": sid, "err": "%s: %s" % (type(e).__name__, str(e)[:120])})
            print("[d12] %s 失败 %s: %s" % (sid, type(e).__name__, str(e)[:100]), flush=True)
        try:
            json.dump(state, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception as e:
            print("[d12] 写盘失败 %s" % str(e)[:80], flush=True)
        time.sleep(5.0)      # dsh 网关串行处理，间隔太短会被断连（实测 1s 间隔触发 RemoteDisconnected）

    kinds = {}
    for it in state["items"]:
        kinds[it.get("kind")] = kinds.get(it.get("kind"), 0) + 1
    print("\n[d12] 共 %d 条判据 %s | 失败 %d 片 → %s"
          % (len(state["items"]), kinds, len(state["failed"]), OUT), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
