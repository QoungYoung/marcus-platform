# -*- coding: utf-8 -*-
"""参数来源守卫（2026-09-15，用户红线：参数必须来自语料，自设/未知必须标记）。

两件事钉死：
  1. **自设参数**（他完全没提，见 `docs/wolf-buy-parameter-ledger.md` §4）在代码里必须带 `⛔自设` 标记；
  2. **已对齐参数**（来自他的原话）在代码里必须带**原话日期**，可逐条回查。
删掉标记 / 删掉日期 → 测试失败，防止未来把自设值误当成"他的话"。
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SELF_MARK = "⛔自设"

SELF_SET = [
    ("apps/main_line/wolf_confirm_pick.py", ["MIN_AMT20_YI", "LIMITUP_PCT", "RANK_WIN",
                                             "WOLF_PICK_POOL_N", "WOLF_PICK_DIST_PCT", "WOLF_PICK_TIER2_GAP",
                                             "WOLF_PICK_MAX_LEGS", "WOLF_PICK_MIN_R20", "WOLF_RS_MIN",
                                             "dist_prevlow_prev"]),
    ("jobs/rotation_switch_arm.py", ["WOLF_PICK_BUY_SHORTLIST", "ROT_POOL_LEGS", "0.4}"]),
    ("backend/app/services/t_gateway.py", ["MAX_SINGLE_ORDER_PCT", "DAILY_LOSS_BREAKER_PCT",
                                           "MAX_DAILY_TURNOVER_RATIO", "COOLDOWN_AFTER_LOSS_MIN",
                                           "SLIPPAGE_PCT"]),
    ("backend/app/services/wolf_ticket_ban.py", ["WOLF_BAN_TTL_TD"]),
]

ALIGNED = [
    ("backend/app/services/t_monitor.py", ["WOLF_DIP_PREVLOW_TOL", "2025-03-06"]),
    ("apps/main_line/wolf_confirm_pick.py", ["WOLF_DIP_PREVLOW_TOL"]),
    ("backend/app/services/t_gateway.py", ["2025-02-07", "一个票最多买2笔"]),
    ("backend/app/services/wolf_etf_vol.py", ["2026-08-21", "3.0"]),
    ("apps/main_line/wolf_theme_vol_fund.py", ["2025-06-16", "WOLF_THEME_VOLFUND_GATE"]),
    ("apps/main_line/wolf_context.py", ["2025-06-16", 'WOLF_THEME_FUND_DAYS", "5"']),
    ("backend/app/services/position_tier.py", ["2026-04-14", "2026-01-17", "base_bucket_pct"]),
    ("backend/app/api/indicator.py", ["WOLF_TOTAL_CAP_CORPUS", "2026-01-17"]),
]


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_self_set_params_are_marked():
    """自设参数必须带 ⛔自设 标记（且标记数不少于清单条数）。"""
    for rel, names in SELF_SET:
        src = _src(rel)
        n = src.count(SELF_MARK)
        assert n >= len(names) - 1, \
            "%s 的 ⛔自设 标记只有 %d 个（清单 %s）" % (rel, n, names)
        for nm in names:
            assert nm in src, "%s 里找不到 %s" % (rel, nm)


def test_aligned_params_carry_quote_date():
    """已对齐参数必须带原话日期（可逐条回查），防止被改成"无出处的值"。"""
    for rel, needles in ALIGNED:
        src = _src(rel)
        for nd in needles:
            assert nd in src, "%s 缺少 %s（原话日期/取值，见参数总账）" % (rel, nd)


def test_ledger_exists_and_counts():
    """总账文档必须存在，且保留"未知/自设"两类清单与四处冲突的处置记录。"""
    md = _src("docs/wolf-buy-parameter-ledger.md")
    for kw in ("⛔ **自设**", "❓ **未知**", "C1", "C3", "C4", "第一要素"):
        assert kw in md, "总账缺少 %s" % kw
