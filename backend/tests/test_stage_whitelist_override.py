# -*- coding: utf-8 -*-
"""§9.872：阶段白名单覆盖开关 `WOLF_STAGE_WHITELIST`（默认空 ⇒ 逐字不变 ✓）

依据（104 个种子日的决策表 ✓，口径＝买点触发后 T+5）：
  现行 TOP2∧白名单  n=2314 中位 −0.65% 胜率 46% ｜ **只放宽阶段** n=4741 中位 −0.23% 胜率 49% ✓
  只放宽主题(TOP3)  n=2850 中位 −0.68% 胜率 45% ✗（更差 ⇒ 主题不放宽 ✓）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))


def _sb(monkeypatch, val):
    if val is None:
        monkeypatch.delenv("WOLF_STAGE_WHITELIST", raising=False)
    else:
        monkeypatch.setenv("WOLF_STAGE_WHITELIST", val)
    import switch_builder as SB
    return SB


def test_default_is_unchanged(monkeypatch):
    """不设开关 ⇒ 与原来完全一致 ✓（B2 关 = BASE_STAGES ✓）。"""
    SB = _sb(monkeypatch, None)
    monkeypatch.delenv("WOLF_QUALIFIED_THEME_DIP", raising=False)
    monkeypatch.delenv("BT_ASOF_FETCH", raising=False)
    assert SB.stages_for_theme("半导体/芯片") == SB.BASE_STAGES


def test_star_override_returns_all_but_falsified(monkeypatch):
    """`*` ⇒ 除「证伪」外全部阶段 ✓（证伪按构造是负信号 ⇒ 不纳入 ✓）。"""
    SB = _sb(monkeypatch, "*")
    got = SB.stages_for_theme("半导体/芯片")
    assert "下跌中" in got and "缩量止跌" in got and "确认" in got
    assert "证伪" not in got


def test_explicit_list_override(monkeypatch):
    """显式清单 ⇒ 精确替换 ✓（可做单变量实验 ✓）。"""
    SB = _sb(monkeypatch, "确认, 下跌中")
    assert SB.stages_for_theme("半导体/芯片") == ("确认", "下跌中")


def test_override_beats_theme_logic(monkeypatch):
    """覆盖优先于主题合格判定 ✓（主题不合格时也生效 ✓）。"""
    SB = _sb(monkeypatch, "确认")
    monkeypatch.setenv("WOLF_QUALIFIED_THEME_DIP", "0")
    assert SB.stages_for_theme("任意主题") == ("确认",)
