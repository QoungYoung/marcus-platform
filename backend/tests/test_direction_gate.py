import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "services"))
from wolf_card.direction_gate import theme_gate


def test_direction_gate_states():
    semi = theme_gate("半导体/芯片", "20260812")
    assert semi["status"] == "t_only" and semi["new_base"] is False
    ai = theme_gate("AI/算力/科技", "20260812")
    assert ai["status"] == "active" and ai["new_base"] is True
    semi_late = theme_gate("半导体/芯片", "20260930")
    assert semi_late["status"] == "blocked" and semi_late["new_base"] is False
