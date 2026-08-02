# -*- coding: utf-8 -*-
"""验证 KDJ 死叉 + K线形态 + 量价背离 检测逻辑"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── Test 1: KDJ Death Cross ──
print("=" * 60)
print("Test 1: _eval_kdj_death_cross")
print("=" * 60)

# Mock LayerResult for standalone testing
class LayerResult:
    def __init__(self, passed=True, grade="", details=None, downgrade_reason="", downgrade_action=""):
        self.passed = passed
        self.grade = grade
        self.details = details or []
        self.downgrade_reason = downgrade_reason
        self.downgrade_action = downgrade_action

# Copy the function inline for testing
def _eval_kdj_death_cross(prev_k, prev_d, cur_k, cur_d):
    if prev_k <= 0 or prev_d <= 0 or cur_k <= 0 or cur_d <= 0:
        return LayerResult(passed=True, grade="PASS", details=["KDJ-K/D data unavailable"]), 1.0

    was_golden = prev_k >= prev_d
    is_dead = cur_k < cur_d
    crossed = was_golden and is_dead

    if not crossed:
        if cur_k >= cur_d:
            return LayerResult(passed=True, grade="PASS", details=[f"KDJ K({cur_k:.1f}) >= D({cur_d:.1f}) - golden"],), 1.0
        else:
            return LayerResult(passed=True, grade="PASS", details=[f"KDJ K({cur_k:.1f}) < D({cur_d:.1f}) - already dead, not new cross"],), 1.0

    if cur_k >= 80:
        return LayerResult(passed=False, grade="BLOCKED",
            details=[f"KDJ HIGH DEATH CROSS: K({cur_k:.1f}) < D({cur_d:.1f}), K>=80"],
            downgrade_reason="KDJ high death cross(K>=80)", downgrade_action="Block"), 0.0
    elif cur_k >= 70:
        return LayerResult(passed=True, grade="WARN",
            details=[f"KDJ MID DEATH CROSS: K({cur_k:.1f}) < D({cur_d:.1f}), 70<=K<80"],
            downgrade_reason="KDJ mid death cross(70<=K<80)", downgrade_action="Probe only"), 0.5
    else:
        return LayerResult(passed=True, grade="PASS",
            details=[f"KDJ LOW DEATH CROSS: K({cur_k:.1f}) < D({cur_d:.1f}), K<70"]), 1.0

# BYD 7/21 case: prev K=82.89, prev D=82.29, cur K=81.60, cur D=82.06
print("\n[BYD 7/21] prev K=82.89 D=82.29, cur K=81.60 D=82.06:")
r, m = _eval_kdj_death_cross(82.89, 82.29, 81.60, 82.06)
print(f"  Passed: {r.passed}, Grade: {r.grade}, Multiplier: {m}")
print(f"  Details: {r.details}")
assert not r.passed, "BYD should be BLOCKED by KDJ high death cross!"
assert m == 0.0, "Multiplier should be 0 for BLOCKED!"
print("  => PASS: Correctly blocked!")

# Normal case: K > D
print("\n[Normal] prev K=50 D=48, cur K=52 D=50:")
r, m = _eval_kdj_death_cross(50, 48, 52, 50)
print(f"  Passed: {r.passed}, Grade: {r.grade}, Multiplier: {m}")
assert r.passed, "Normal golden cross should pass!"
print("  => PASS: Correctly passed!")

# Mid-level death cross
print("\n[Mid] prev K=76 D=74, cur K=74 D=75:")
r, m = _eval_kdj_death_cross(76, 74, 74, 75)
print(f"  Passed: {r.passed}, Grade: {r.grade}, Multiplier: {m}")
assert r.passed and m == 0.5, "Mid death cross should warn!"
print("  => PASS: Correctly warned!")

# Low-level death cross
print("\n[Low] prev K=65 D=62, cur K=60 D=63:")
r, m = _eval_kdj_death_cross(65, 62, 60, 63)
print(f"  Passed: {r.passed}, Grade: {r.grade}, Multiplier: {m}")
assert r.passed and m == 1.0, "Low death cross should pass normally!"
print("  => PASS: Correctly passed!")

# ── Test 2: Pattern Detection ──
print("\n" + "=" * 60)
print("Test 2: _detect_patterns")
print("=" * 60)

class Bar:
    def __init__(self, o, h, l, c, v):
        self.open = o; self.high = h; self.low = l; self.close = c; self.vol = v

def _detect_patterns(bars):
    if not bars or len(bars) < 2:
        return {"pattern": None, "hard_block": False, "detail": ""}
    bar0, bar1 = bars[0], bars[1]
    o0, h0, l0, c0 = bar0.open, bar0.high, bar0.low, bar0.close
    o1, c1 = bar1.open, bar1.close
    body = abs(c0 - o0)
    upper_shadow = h0 - max(o0, c0)
    lower_shadow = min(o0, c0) - l0
    if body > 0 and upper_shadow >= 2.0 * body and upper_shadow > lower_shadow * 1.5:
        return {"pattern": "shooting_star", "hard_block": True, "detail": f"SHOOTING STAR: upper shadow({upper_shadow:.2f}) >= body({body:.2f})*2"}
    if c1 > o1 and c0 < o0 and o0 > c1 and c0 < o1:
        return {"pattern": "bearish_engulfing", "hard_block": True, "detail": "BEARISH ENGULFING"}
    return {"pattern": None, "hard_block": False, "detail": ""}

# BYD 7/21 shooting star: open=93.93, high=96.80, low=93.35, close=94.30
# Previous day 7/20: open=93.49, high=95.28, low=92.77, close=93.92
bars_byd = [
    Bar(93.93, 96.80, 93.35, 94.30, 671592),  # 7/21
    Bar(93.49, 95.28, 92.77, 93.92, 439890),  # 7/20
]
print("\n[BYD 7/21 shooting star]:")
r = _detect_patterns(bars_byd)
print(f"  Pattern: {r['pattern']}, HardBlock: {r['hard_block']}")
print(f"  Detail: {r['detail']}")
assert r['pattern'] == 'shooting_star', "Should detect shooting star!"
assert r['hard_block'] == True, "Should hard block!"
print("  => PASS: Correctly detected shooting star!")

# Normal bars (no pattern)
bars_normal = [
    Bar(94.0, 95.5, 91.0, 94.5, 500000),
    Bar(92.0, 93.0, 90.0, 92.8, 400000),
]
print("\n[Normal bars]:")
r = _detect_patterns(bars_normal)
print(f"  Pattern: {r['pattern']}, HardBlock: {r['hard_block']}")
assert r['pattern'] is None, "Normal bars should not trigger pattern!"
print("  => PASS: No false positive!")

# ── Test 3: Volume Divergence ──
print("\n" + "=" * 60)
print("Test 3: _eval_volume_divergence")
print("=" * 60)

def _eval_volume_divergence(bars):
    if not bars or len(bars) < 5:
        return {"divergence": False, "warning": False, "detail": ""}
    recent = bars[:5]
    highs = [b.high for b in recent]
    volumes = [b.vol for b in recent]
    cur_high = highs[0]
    max_high = max(highs)
    if cur_high < max_high:
        return {"divergence": False, "warning": False, "detail": ""}
    cur_vol = volumes[0]
    max_vol = max(volumes)
    if cur_vol < max_vol:
        return {"divergence": True, "warning": True,
            "detail": f"VOL DIVERGENCE: price new high({cur_high:.2f}) but vol({cur_vol/10000:.0f}w) < peak({max_vol/10000:.0f}w)"}
    return {"divergence": False, "warning": False, "detail": ""}

# BYD 7/21: high=96.80 (5-day highest), vol=671592 < 687346 (7/17 peak)
bars_div = [
    Bar(93.93, 96.80, 93.35, 94.30, 671592),  # 7/21 - new high, not max vol
    Bar(93.49, 95.28, 92.77, 93.92, 439890),  # 7/20
    Bar(94.14, 95.95, 91.90, 93.47, 687346),  # 7/17 - max vol!
    Bar(91.74, 94.76, 91.15, 94.14, 607204),  # 7/16
    Bar(89.65, 93.48, 89.45, 91.76, 514154),  # 7/15
]
print("\n[BYD 7/21 volume divergence]:")
r = _eval_volume_divergence(bars_div)
print(f"  Divergence: {r['divergence']}, Warning: {r['warning']}")
print(f"  Detail: {r['detail']}")
assert r['divergence'] == True, "Should detect volume divergence!"
assert r['warning'] == True, "Should trigger warning!"
print("  => PASS: Correctly detected volume divergence!")

# No divergence (volume confirms)
bars_confirm = [
    Bar(93.93, 96.80, 93.35, 94.30, 750000),  # new high + max vol
    Bar(93.49, 95.28, 92.77, 93.92, 439890),
    Bar(94.14, 95.95, 91.90, 93.47, 687346),
    Bar(91.74, 94.76, 91.15, 94.14, 607204),
    Bar(89.65, 93.48, 89.45, 91.76, 514154),
]
print("\n[No divergence - volume confirms]:")
r = _eval_volume_divergence(bars_confirm)
print(f"  Divergence: {r['divergence']}, Warning: {r['warning']}")
assert r['divergence'] == False, "Should NOT detect divergence when volume confirms!"
print("  => PASS: No false positive!")

# ── Summary ──
print("\n" + "=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)
print("\nBYD 7/21 would be caught by:")
print("  1. Layer 1: KDJ high death cross (K=81.6 < D=82.1, K>=80) => BLOCKED")
print("  2. Layer 3: Shooting star pattern => HARD BLOCK")
print("  3. Layer 3: Volume-price divergence => WARNING (probe only)")
print("  => DOUBLE INTERCEPT: entry denied by both Layer 1 and Layer 3")
