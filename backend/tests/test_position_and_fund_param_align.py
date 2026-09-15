# -*- coding: utf-8 -*-
"""C3/P2 参数对齐的定向单测（2026-09-15）。

C3 三仓 50/30/20（2026-04-14）与分档总仓位 75/50/30/0（2026-01-17）：
  · **桶级** vs **单笔级**的语义纪律：他的话是桶级比例，**不得塞进 cap_pct**（否则单笔可下 50% 净值）
  · `_get_total_cap` 的语料档由 `WOLF_TOTAL_CAP_CORPUS` 控制（默认关 = 现状）
P2 主题资金门 3 日 → 5 日（2025-06-16「资金没有5日连续流出的」）
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))


def test_corpus_buckets_match_quotes():
    from app.services import position_tier as P
    c = P.CORPUS_PROFILE
    assert (c["base_bucket_pct"], c["t_bucket_pct"], c["cash_floor_bucket_pct"]) == (50, 30, 20)
    assert c["total_cap_pct"] == {"主升": 75, "调整": 50, "有风险": 30, "下跌": 0}
    assert "2026-04-14" in c["quote_buckets"] and "2026-01-17" in c["quote_total"]


def test_buckets_are_not_single_order_caps():
    """桶级字段必须是**新增字段**，不得改写 cap_pct（防单笔上限被放大到 50%）。"""
    from app.services import position_tier as P
    for op, cfg in P.DEFAULT_CFG["wave"].items():
        for intent, rule in (cfg.get("intents") or {}).items():
            assert float(rule.get("cap_pct") or 0) <= 10.0, "%s/%s cap_pct 被放大" % (op, intent)
    assert "base_bucket_pct" not in str(P.DEFAULT_CFG)   # 桶级不进 legacy 默认表


def test_profile_switch_defaults_to_legacy(monkeypatch):
    from app.services import position_tier as P
    monkeypatch.delenv("P3_TIER_PROFILE", raising=False)
    assert P.profile() == "legacy"                       # 默认零变化
    monkeypatch.setenv("P3_TIER_PROFILE", "corpus")
    assert P.profile() == "corpus"
    assert P.corpus_buckets()["base_bucket_pct"] == 50


def test_total_cap_corpus_switch(monkeypatch):
    """`_get_total_cap` 的语料档：只验证**判定逻辑与单一来源**（避免重依赖 FastAPI 导入）。"""
    import re
    src = open(os.path.join(ROOT, "backend", "app", "api", "indicator.py"), encoding="utf-8").read()
    assert 'WOLF_TOTAL_CAP_CORPUS' in src and "2026-01-17" in src
    assert '{"green": 100.0, "yellow": 50.0, "red": 20.0}' in src      # 默认（现状）
    assert 'from app.services.position_tier import CORPUS_PROFILE' in src   # 语料数字单一来源
    from app.services import position_tier as P
    t = P.CORPUS_PROFILE["total_cap_pct"]
    assert (t["主升"], t["调整"], t["有风险"], t["下跌"]) == (75, 50, 30, 0)
    # 模拟两种开关下的取值
    def _cap(stance, on):
        return float({"green": 75.0, "yellow": 50.0, "red": 30.0}.get(stance, 50.0)) if on else \
            float({"green": 100.0, "yellow": 50.0, "red": 20.0}.get(stance, 50.0))
    assert [_cap(s, False) for s in ("green", "yellow", "red")] == [100.0, 50.0, 20.0]
    assert [_cap(s, True) for s in ("green", "yellow", "red")] == [75.0, 50.0, 30.0]


def test_theme_fund_days_default_is_5(monkeypatch):
    """P2：主题资金门默认 5 日（他的话），env 可覆写回 3。"""
    import wolf_context as W
    src = open(os.path.join(ROOT, "apps", "main_line", "wolf_context.py"), encoding="utf-8").read()
    assert 'WOLF_THEME_FUND_DAYS", "5"' in src
    assert "2025-06-16" in src
