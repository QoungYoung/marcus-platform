# -*- coding: utf-8 -*-
"""rotation_gate selftest：覆盖 gate_rotation v2 五分支 + B3补丁（P2 轮动 19 场景）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rotation_gate as rg

HIGH_OUT2 = {"position": "HIGH", "fund": {"dir": "out", "conv": 2}, "structure": "flat"}
REL_LOW_IN = {"position": "MID", "rel": "low", "fund": {"dir": "in", "conv": 1}, "struct_ok": True}

CASES = [
    # id, wave_op, a, b, sucking, inside, not_dist, healthy, expected
    ("A1", "side",   None, None, False, True,  False, True, "mainline_rotation"),
    ("A2", "build",  None, None, True,  True,  False, True, "mainline_rotation"),
    ("A3", "build",  None, None, False, True,  False, True, "mainline_rotation"),
    ("A4", "exit",   None, None, True,  True,  False, True, "block"),
    ("A5", "side",   None, None, False, True,  False, True, "mainline_rotation"),
    ("A6", "t_only", None, None, False, False, False, True, "defensive_reduce"),
    ("B1", "build",  HIGH_OUT2, None, False, False, False, True, "sell_guard"),
    ("B2", "build",  None, None, False, True,  False, True, "mainline_rotation"),
    ("B3", "defense",None, None, False, True,  True,  True, "defense_mainline_rotation"),
    ("B4", "build",  HIGH_OUT2, REL_LOW_IN, False, False, False, True, "switch_low"),
    ("B5", "build",  HIGH_OUT2, None, False, False, False, True, "sell_guard"),
    ("C1", "t_only", None, REL_LOW_IN, False, False, False, True, "switch_low"),
    ("C2", "t_only", None, None, False, False, False, True, "defensive_reduce"),
    ("C4", "t_only", None, None, False, False, False, True, "defensive_reduce"),
    ("C5", "side",   None, None, False, False, False, True, "defensive_reduce"),
    ("C6", "side",   None, None, False, False, False, True, "defensive_reduce"),
    ("D4", "exit",   None, None, True,  True,  False, True, "block"),
    ("D5", "t_only", None, None, True,  True,  False, True, "block"),
    ("D6", "side",   None, None, False, False, False, False, "block"),
]
def main():
    fails = 0
    for cid, op, a, b, suck, inside, nd, healthy, exp in CASES:
        got = rg.decide(op, a=a, b=b, mainline_sucking=suck, inside_mainline=inside,
                        not_distributed=nd, rotation_healthy=healthy)["verdict"]
        if got != exp:
            fails += 1
            print("FAIL %-4s got=%s expect=%s" % (cid, got, exp))
    print("PASS %d/%d, FAIL %d" % (len(CASES) - fails, len(CASES), fails))
    return 1 if fails else 0
if __name__ == "__main__":
    raise SystemExit(main())
