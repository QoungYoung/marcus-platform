# -*- coding: utf-8 -*-
"""trend_break_v2 单测：结构/量能/3连站/回踩/止损/数据不足。"""
import os, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'apps', 'main_line'))
from trend_break_v2 import detect_structure, volume_ok, stand3, pullback_ok, evaluate, DEFAULTS


def _base(n=60, px=10.0):
    high = [px + 0.5] * n
    low = [px - 0.1] * n
    close = [px + 0.2] * n
    open_ = [px] * n
    vol = [80.0 + (k % 7) * 5 for k in range(n)]
    amount = [1000.0 + (k % 5) * 20 for k in range(n)]
    return high, low, close, open_, vol, amount


def test_platform_structure():
    h, l, c, o, v, a = _base(60)
    st = detect_structure(h, l, c, 59, {'allowed_structures': ('platform',)})
    assert st['structure'] in ('platform', 'fallback')
    assert st['level'] and st['level'] > 0


def test_volume_ok_and_fail():
    h, l, c, o, v, a = _base(30)
    # 放量突破 bar
    i = 25
    v[i] = 300; a[i] = 3000; h[i] = 11.0; l[i] = 10.0; c[i] = 10.9
    ok = volume_ok(v, a, h, l, c, i)
    assert ok['ok'] is True
    v[i] = 105; a[i] = 1050
    bad = volume_ok(v, a, h, l, c, i)
    assert bad['ok'] is False


def test_stand3_pass_and_fail():
    c = [10.0] * 30 + [11.2, 11.1, 11.15, 11.2]
    r = stand3(c, 11.0, 30)
    assert r['ok'] is True and r['done'] is True
    c2 = [10.0] * 30 + [11.2, 10.9, 11.15]
    r2 = stand3(c2, 11.0, 30)
    assert r2['ok'] is False


def test_pullback_ok():
    c = [10.0] * 30 + [11.2, 11.1, 11.15, 11.2, 11.1]
    h = [10.5] * 30 + [11.3, 11.2, 11.2, 11.25, 11.2]
    l = [9.9] * 30 + [11.0, 11.0, 11.0, 11.0, 11.05]
    v = [100.0] * 30 + [300, 150, 150, 120, 100]
    r = pullback_ok(h, l, c, v, 11.0, 30, 34)
    assert r['ok'] is True


def test_evaluate_confirm_or_pullback():
    h, l, c, o, v, a = _base(60)
    # 第 56 根放量突破 10.6，随后 3 根站上，最后 1 根维持
    for k in range(56, 60):
        h[k] = 11.4; l[k] = 10.8; c[k] = 11.25
    v[56] = 300; a[56] = 3000
    out = evaluate(h, l, c, o, v, a)
    assert out['stage'] in ('TRIGGER', 'CONFIRM', 'PULLBACK')
    assert out.get('level')


def test_evaluate_stop_fail():
    h, l, c, o, v, a = _base(60)
    for k in range(56, 60):
        h[k] = 11.4; l[k] = 10.8; c[k] = 11.25
    v[56] = 300; a[56] = 3000
    c[59] = 10.0
    out = evaluate(h, l, c, o, v, a)
    assert out['stage'] in ('FAIL', '')


def test_too_short():
    h, l, c, o, v, a = _base(10)
    out = evaluate(h, l, c, o, v, a)
    assert out['stage'] == ''
