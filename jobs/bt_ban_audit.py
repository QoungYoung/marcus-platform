#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_ban_audit.py — 「卖出后删票」是否真被执行的**日内级**审计（只读）

为什么需要它（2026-09-21 我犯过的错）：
  我第一版审计按**日期**比较（`买入日 > 删票日` 才算违规）⇒ 同一天内
  「09:45 卖出→删票 → 10:55 又买」被判成"删票前买入"，于是我在汇报里写了
  "T4 删票后违规 0 笔" —— **错的**，用户拿页面上的成交流水当场指出"这个账户明明买了"。
  本脚本改用**日内时间戳**排序：以"删票登记日当天/之后的第一笔卖出"作为禁令生效时刻，
  之后（严格晚于）的买入一律算违规。

用法：
  python -u jobs/bt_ban_audit.py --tag t4            # 审计某条臂
  python -u jobs/bt_ban_audit.py --tag t4 --all      # 连没违规的票也列出来
"""
import argparse
import glob
import json
import os


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


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_trades(tag):
    tr = {}
    for p in glob.glob(os.path.join(REPO, "data/_bt_%s/*/marcus_trades.jsonl" % tag)):
        for line in open(p, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception as _e_sil1:
                _silent_alert("bt_ban_audit.py:34", _e_sil1)
                continue
            oid = str(o.get("order_id") or "")
            if oid:
                tr[oid] = o
    return sorted(tr.values(), key=lambda o: str(o.get("timestamp")))


def load_bans(tag):
    """{symbol: (since, reason)} —— 取**最早**的一次登记。"""
    ban = {}
    for p in sorted(glob.glob(os.path.join(REPO, "data/_bt_%s/*/wolf_ticket_ban.json" % tag))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as _e_sil2:
            _silent_alert("bt_ban_audit.py:48", _e_sil2)
            continue
        for k, v in d.items():
            s = str(v.get("symbol") or k.split(":")[-1])
            since = str(v.get("since") or "")
            if s and (s not in ban or since < ban[s][0]):
                ban[s] = (since, str(v.get("reason") or ""))
    return ban


def audit(tag, show_all=False):
    trs = load_trades(tag)
    ban = load_bans(tag)
    print("=== 臂 %s：成交 %d 笔 / 有删票记录 %d 只 ===" % (tag, len(trs), len(ban)))
    tot_viol = 0
    for sym, (since, reason) in sorted(ban.items()):
        rs = [o for o in trs if str(o.get("symbol")) == sym]
        if not rs:
            continue
        iso = "%s-%s-%s" % (since[:4], since[4:6], since[6:8]) if len(since) == 8 else since
        sells = sorted(str(o["timestamp"]) for o in rs
                       if o.get("type") == "sell" and str(o["timestamp"])[:10].replace("-", "") >= since)
        t0 = sells[0] if sells else iso + "T00:00:00"
        buys = [str(o["timestamp"]) for o in rs if o.get("type") == "buy"]
        pre = [b for b in buys if b <= t0]
        post = [b for b in buys if b > t0]
        if not post and not show_all:
            continue
        tot_viol += len(post)
        print("  %-9s 删票 since=%s（%s）  生效≈%s" % (sym, since, reason[:16], t0))
        print("     删票前买入: %s" % (pre or "无"))
        print("     **删票后买入: %s**" % (post or "无"))
    print("  ⇒ 合计删票后买入 **%d 笔**" % tot_viol)
    return tot_viol


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="t4")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    audit(a.tag, a.all)
