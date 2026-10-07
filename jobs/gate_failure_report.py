# -*- coding: utf-8 -*-
"""gate_failure_report.py — 闸门异常复盘报告（2026-09-25 用户要求：bug 类要能"复盘就看到"）。

用法：python jobs/gate_failure_report.py --root data/_bt_t28 [--out 报告路径]
产出：按 (闸门, 异常类型) 聚合的表格 + 最近的样例 + 涉及的标的 ⇒ 复盘时一眼看见。
数据源：① `<root>/gate_failures.jsonl`（gate_alarm.alarm 落盘）
        ② `<root>/_summary/*.log` 里的 `[GATE-ALARM]` / `异常(放行)` / `判定异常` 行（含历史遗留格式）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import Counter, defaultdict


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



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/_bt_t28")
    ap.add_argument("--out", default="")
    ap.add_argument("--notify", action="store_true",
                    help="有**新增**闸门异常时推 QQ（状态文件去重，避免刷屏）")
    ap.add_argument("--state", default=".dsh-tmp/wolfbt/gate_failures_state.json")
    ap.add_argument("--tag", default="", help="臂标签，用于消息标题")
    a = ap.parse_args()
    rows, samples, symbols = Counter(), [], Counter()
    # ① JSONL
    p = os.path.join(a.root, "gate_failures.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                o = json.loads(line)
            except Exception as _e_sil1:
                _silent_alert("gate_failure_report.py:36", _e_sil1)
                continue
            k = (o.get("where", "?")[:40], o.get("exc_type", "?"))
            rows[k] += 1
            if len(samples) < 10:
                samples.append("%s | %s: %s" % (o.get("where"), o.get("exc_type"), str(o.get("exc"))[:80]))
            s = (o.get("ctx") or {}).get("symbol")
            if s:
                symbols[str(s)] += 1
    # ② 日志里的旧格式（**只进报告，不参与推送计数** —— 2026-09-25：它们不是新异常 ✗）
    legacy = Counter()
    for f in glob.glob(os.path.join(a.root, "_summary", "*.log")):
        for line in open(f, encoding="utf-8", errors="replace"):
            if "[GATE-ALARM]" in line:
                continue
            if "异常(放行)" in line or "判定异常" in line:
                tag = line.split("]")[1].split(":")[0].strip()[:44] if "]" in line else "（未识别）"
                legacy[tag] += 1
                if len(samples) < 10:
                    samples.append(line.strip()[:110])
    out = ["# 闸门异常复盘报告",
           "",
           "根目录：`%s`" % a.root,
           "",
           "## 按闸门 × 异常类型",
           "",
           "| 闸门 | 异常类型 | 次数 |",
           "|---|---|---|"]
    for (w, t), n in rows.most_common(30):
        out.append("| %s | %s | %d |" % (w, t, n))
    if not rows:
        out.append("| （无） | — | 0 |")
    out += ["", "## 历史格式（`异常(放行)` 打印 —— 仅参考，不触发推送）", "",
            "| 位置 | 次数 |", "|---|---|"]
    for tag, n in legacy.most_common(20):
        out.append("| %s | %d |" % (tag, n))
    if not legacy:
        out.append("| （无） | 0 |")
    out += ["", "## 最近样例", ""] + ["- `%s`" % s for s in samples[:10]]
    if symbols:
        out += ["", "## 涉及标的 Top10", "",
                "| 标的 | 次数 |", "|---|---|"]
        for s, n in symbols.most_common(10):
            out.append("| %s | %d |" % (s, n))
    txt = "\n".join(out) + "\n"
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(txt)
        print("[gate-report] 已写 %s（%d 类异常、共 %d 次）" % (a.out, len(rows), sum(rows.values())))
    else:
        print(txt)

    if a.notify:
        _notify_if_new(a, rows)
    return 0


def _push_qq(text: str) -> bool:
    """推 QQ：走 `core.qq_notifier.send_c2c_message`（HTTP + access_token，不依赖 WS ✓）。

    接收人取 `.env` 的 `QQ_BOT_RECIPIENT`（`core.qq_notifier` 导入时会加载 `.env` ✓）。
    """
    try:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core.qq_notifier import send_c2c_message   # noqa: E402  （导入即加载 .env）
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[gate-report] 推送跳过：未配置 QQ_BOT_RECIPIENT")
            return False
        ok = bool(send_c2c_message(openid, text))
        print("[gate-report] 推送%s" % ("成功 ✓" if ok else "失败 ✗"))
        return ok
    except Exception as e:
        print("[gate-report] 推送异常: %s" % str(e)[:100])
        return False


def _notify_if_new(a, rows) -> None:
    """只有**新增**（总数变多 或 出现新类）才推 —— 去重状态存 `--state`。"""
    total = sum(rows.values())
    new_classes = sorted({"%s|%s" % (w, t) for (w, t) in rows})
    state = {"total": 0, "classes": []}
    try:
        if os.path.exists(a.state):
            state = json.load(open(a.state, encoding="utf-8"))
    except Exception as _e_sil2:
        _silent_alert("gate_failure_report.py:122", _e_sil2)
    added = total - int(state.get("total") or 0)
    fresh = [c for c in new_classes if c not in set(state.get("classes") or [])]
    NL = chr(10)
    if added > 0 or fresh:
        top = "、".join("%s×%d" % (str(w)[:28], n) for (w, t), n in rows.most_common(3))
        parts = ["[闸门告警]" + ((" " + a.tag) if a.tag else ""),
                 "新增 %d 次（累计 %d）｜新类 %d 个" % (max(added, 0), total, len(fresh)),
                 "Top: " + top,
                 "详见 gate_failures_report.md（.dsh-tmp wolfbt 目录）"]
        _push_qq(NL.join(parts))
    else:
        print("[gate-report] 无新增（累计 %d 次），不推送 ✓" % total)
    try:
        os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)
        json.dump({"total": total, "classes": new_classes}, open(a.state, "w", encoding="utf-8"))
    except Exception as _e_sil3:
        _silent_alert("gate_failure_report.py:139", _e_sil3)


if __name__ == "__main__":
    raise SystemExit(main())
