# -*- coding: utf-8 -*-
"""material_entry selftest：大级别买点/确认链条件"""
import os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import material_entry as me

CASES = [
    ("after-earnings-9月", me.after_earnings_season(date(2026, 9, 2)), True),
    ("earnings-8月中-等待", me.after_earnings_season(date(2026, 8, 10)), False),
    ("build-ready", me.big_level_ok({"operation": "build", "sub_level": "3-3"})[0], True),
    ("4-4-t_only-wait", me.big_level_ok({"operation": "t_only", "sub_level": "4-4"})[0], False),
    ("side-筑底-ok", me.big_level_ok({"operation": "side", "sub_level": "4-3筑底"})[0], True),
]
pos = {"a": {"name": "半导体材料", "confirm_chain": {"stage": "确认"}}}
assert me.material_confirm(pos)[0]
pos2 = {"a": {"name": "半导体材料", "confirm_chain": {"stage": "下跌中"}}}
assert not me.material_confirm(pos2)[0]
for name, got, exp in CASES:
    assert got == exp, (name, got, exp)
print("SELFTEST OK %d cases" % len(CASES))
