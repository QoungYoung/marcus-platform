# -*- coding: utf-8 -*-
"""主营校验的**批量**判定（2026-09-20 用户：「我们的主营校验能批量校验而不是每次只校验一个吗」）。

为什么批量（实测，见 docs/wolf-buy-parameter-ledger.md 末节）：
  · 13101 是**串行**服务：单发 2.1–6.7s、2 并发中位 5.5s、3 并发 9.6s、6 并发 9.1–13.0s
    ⇒ 延迟≈并发×单发 ⇒ 单票逐个问 = 一天 344 次请求 ≈ 16–25 分钟，且并发一高就排队超时/撞 405。
  · 批量一次判 20–40 只：实测 n=10/25/40 → 5.0s / 18.9s / 10.0s，行行可解析，
    每票成本从 ~4.5s 降到 ~0.25s，请求数降一个数量级，**全程单请求串行** ⇒ 无队尾等待、无 405。

口径不变（重要）：批量 prompt 与单票 `theme_member_llm.member_prompt` **同义**，只有输出格式不同
（单票"只回答两个字" ↔ 批量"只输出 N 行「序号 无关/有关」"）。结论写回**单票同一缓存**
（`theme_member_llm.store_result`），所以逐票路径一行不改也能吃到；解析不到的票由调用方回落到单票。

开关：`WOLF_MEMBER_BATCH`（**库内默认 0 = 关**，回测 pins 置 1）、`WOLF_MEMBER_BATCH_SIZE`（默认 25）。
任何异常/解析失败都**不写缓存**（回落到单票），绝不用"猜"的结论污染缓存。
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import theme_member_llm as ML


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


_CRIT = ("只看主营/产业链归属；概念表可能有脏数据，仅概念沾边不算有关，"
         "例如主营为地产/REITs/医药/消费/计算机设备/工程等")

# 「1 无关」「1. 无关」「1、有关」「1：有关」都收
_LINE = re.compile(r"(\d{1,3})\s*[.、,，:：)）]?\s*(无关|有关)")


def enabled() -> bool:
    return str(os.getenv("WOLF_MEMBER_BATCH", "0")).strip().lower() in ("1", "true", "yes", "on")


def chunk_size() -> int:
    try:
        return max(2, int(os.getenv("WOLF_MEMBER_BATCH_SIZE", "25")))
    except Exception:
        return 25


def build_prompt(theme: str, items: List[Tuple[str, List[str]]]) -> str:
    """items = [(symbol, concepts)]（顺序即编号 1..N）。"""
    rows = []
    for i, (sym, cs) in enumerate(items, 1):
        c = ML._prompt_concepts(cs) or "无"      # 与单票 prompt/key 同一常量（防漂移）
        rows.append("%d. 代码 %s；概念：%s" % (i, sym, c))
    return ("下面 %d 只A股，逐只判断其**主营业务**是否与【%s】方向**完全无关**（%s）。\n%s\n"
            "只输出 %d 行，每行格式为「序号 无关」或「序号 有关」，不要任何其它文字。"
            % (len(items), str(theme), _CRIT, "\n".join(rows), len(items)))


def parse_reply(reply: str, n: int) -> Dict[int, str]:
    """回复 → {序号(1..n): 无关/有关}。多认/越界/重复一律按"首次出现"取，缺行不补。"""
    out: Dict[int, str] = {}
    for m in _LINE.finditer(str(reply or "")):
        try:
            idx = int(m.group(1))
        except Exception as _e_sil1:
            _silent_alert("theme_member_batch.py:61", _e_sil1)
            continue
        if 1 <= idx <= int(n) and idx not in out:
            out[idx] = m.group(2)
    return out


def check_batch(items: List[Tuple[str, List[str]]], theme: str,
                size: Optional[int] = None) -> Dict[str, int]:
    """批量判定并把结论写回单票缓存。返回统计（asked/ok/miss/calls/err）。

    · 不发网络的情形：`ML.cached()` 命中（内存/落盘）或 `ML.needs_dsh()` 为假（白名单/主类 reject）。
    · 解析失败的票**不写缓存**，由调用方回落单票（口径上更安全）。
    """
    st = {"asked": 0, "ok": 0, "miss": 0, "calls": 0, "err": 0, "failed_syms": []}
    todo = []
    for sym, cs in (items or []):
        try:
            st["asked"] += 1
            if ML.cached(str(sym), cs, theme) is not None:
                st["ok"] += 1
                continue
            if not ML.needs_dsh(str(sym), cs, theme):
                st["ok"] += 1                       # 主类/白名单已定案，问 dsh 反而会破坏确定性
                continue
            todo.append((str(sym), cs))
        except Exception as _e_sil2:
            _silent_alert("theme_member_batch.py:87", _e_sil2)
            continue
    if not todo:
        return st
    n = int(size or chunk_size())
    # ★ 账本 §9.660 ✓（用户：「让我知道布腿进度、候选进度、总计多少腿、布了多少、还剩多少腿」）：
    #   批量路径是回测的**主路径** ✓（`WOLF_MEMBER_BATCH=1` ✓，一次请求判 n 只 ✓）
    #   ⇒ **每批前后各打一行** ✓（批号/批数/本批只数/已用时长 ✓）⇒ 布腿期不再是黑箱 ✓
    import time as _t8
    _t8_0 = _t8.time()
    _tot8 = len(todo)
    _nb8 = (len(todo) + n - 1) // n
    print("[member_llm] ★ 批量开始: %d 只 / %d 批（每批 %d 只，theme=%s，超时 %.0fs）"
          % (_tot8, _nb8, n, theme, ML.batch_timeout()), flush=True)
    for _bi8, i in enumerate(range(0, len(todo), n), 1):
        part = todo[i:i + n]
        print("[member_llm] ★ 批量 %d/%d 批（本批 %d 只，已判 %d/%d，theme=%s）｜已用 %.0fs"
              % (_bi8, _nb8, len(part), min(i, _tot8), _tot8, theme, _t8.time() - _t8_0), flush=True)
        _reply, _err = None, None
        for _try in range(ML.batch_retry() + 1):                 # 整批重试比逐票重试划算得多
            try:
                _reply = ML._ask_llm(build_prompt(theme, part), timeout=ML.batch_timeout())
                st["calls"] += 1
                _err = None
                break
            except Exception as e:
                _err = e
                st["calls"] += 1
        if _err is not None:
            st["err"] += len(part)
            ML._warn("批量判定失败(%d 只)：%s" % (len(part), type(_err).__name__))
            if ML.fail_fast():                                   # 负缓存整批 ⇒ 调用方不必再逐票问同一个服务
                for _sym, _cs in part:
                    ML.neg_put(_sym, _cs, theme, why="batch_fail")
                    st["failed_syms"].append(_sym)
            continue
        reply = _reply
        got = parse_reply(reply, len(part))
        for j, (sym, cs) in enumerate(part, 1):
            v = got.get(j)
            if v is None:
                st["miss"] += 1
                continue
            try:
                ML.store_result(sym, cs, theme, v == "有关", v)
                ML._STATS["batch"] = ML._STATS.get("batch", 0) + 1
                st["ok"] += 1
            except Exception:
                st["miss"] += 1
    print("[member_llm] ★ 批量完成: 问 %d｜命中 %d｜缺 %d｜调用 %d｜失败 %d（%.0fs ✓）"
          % (st["asked"], st["ok"], st["miss"], st["calls"], st["err"], _t8.time() - _t8_0), flush=True)
    return st


def warm(items: List[Tuple[str, List[str]]], theme: str, size: Optional[int] = None) -> Dict[str, int]:
    """对外主入口（leg_gate.prefetch 用）：批量优先；未覆盖的由调用方兜底单票。"""
    if not enabled():
        return {"asked": len(items or []), "ok": 0, "miss": len(items or []), "calls": 0, "err": 0}
    try:
        return check_batch(items, theme, size=size)
    except Exception as e:
        ML._warn("批量预热异常：%s" % type(e).__name__)
        return {"asked": len(items or []), "ok": 0, "miss": len(items or []), "calls": 0, "err": 1}
