# -*- coding: utf-8 -*-
"""底仓锚「防下调反馈」单测（2026-09-19 实测：高抛腿把锚一路啃到一手，整仓被卖光）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "backend") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "backend"))

from app.services import t_base_floor as BF   # noqa: E402


def _seq(no_down: bool, sells):
    """模拟卖出序列，返回每步 floor。"""
    BF.NO_DOWN_REBASE = no_down
    ratio, cum, rec = 2.0 / 3.0, 1100, None
    out = []
    for sel in sells:
        floor, ev = BF.compute_floor(cum, sel, ratio, rec=rec)
        if ev and ev.get("kind") == "rebase":
            rec = {"base": int(ev.get("floor") or 0), "cum_at_base": cum}
        out.append(floor)
    return out


def test_防下调_锚不再随卖出走低(monkeypatch):
    monkeypatch.setattr(BF, "NO_DOWN_REBASE", True)
    got = _seq(True, [1100, 700, 600, 400, 200, 100])
    assert got == [700, 400, 400, 400, 400, 400], got          # 首次认账后不再下调
    monkeypatch.setattr(BF, "NO_DOWN_REBASE", False)
    old = _seq(False, [1100, 700, 600, 400, 200, 100])
    assert old[-1] == 100 and old[-1] < got[-1], old            # 旧口径一路啃到 100 股


def test_新增买入仍抬升锚(monkeypatch):
    monkeypatch.setattr(BF, "NO_DOWN_REBASE", True)
    ratio = 2.0 / 3.0
    rec = {"base": 400, "cum_at_base": 1100}
    floor, _ = BF.compute_floor(1400, 1400, ratio, rec=rec)      # 又买了 300 股
    assert floor > 400, floor                                    # floor = base + 新增×ratio


def test_默认开关_库内为关():
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_base_floor.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_BASE_FLOOR_NO_DOWN"' in src
    assert "NO_DOWN_REBASE" in src


def test_A2_严格锚_完全不下调(monkeypatch):
    """A2：floor 恒 = 累计买入 × ratio（只有清仓才 reset）——宁可 T 仓锁死，也不下调底仓。"""
    monkeypatch.setattr(BF, "STRICT_ANCHOR", True)
    monkeypatch.setattr(BF, "NO_DOWN_REBASE", True)
    ratio, cum = 2.0 / 3.0, 1100
    got = []
    for sel in (1100, 700, 600, 400, 200, 100):
        floor, ev = BF.compute_floor(cum, sel, ratio, rec=None)
        assert ev is None, ev                     # 严格锚：不产生任何认账事件
        got.append(floor)
    assert len(set(got)) == 1 and got[0] >= 700, got   # 全程维持 733 档（取整后 ≥700）
    monkeypatch.setattr(BF, "STRICT_ANCHOR", False)
