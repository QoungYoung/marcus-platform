from app.services.wolf_card.selection_rules import apply_bellwether_overlay


def test_overlay_does_not_drop_picks():
    picks = [{"code": "A"}, {"code": "B"}]
    out = apply_bellwether_overlay(picks, {"state": "dead", "permissions": {"second_line_new": False}})
    assert [p["code"] for p in out] == ["A", "B"]
    assert all(p["position_multiplier"] == 0.25 for p in out)
    assert out[0]["bellwether_state"] == "dead"


def test_overlay_alive_full_size():
    out = apply_bellwether_overlay([{"code": "A"}], {"state": "alive", "permissions": {"high_cut_low": True}})
    assert out[0]["position_multiplier"] == 1.0


def test_overlay_unknown_defaults_half():
    out = apply_bellwether_overlay([{"code": "A"}], {})
    assert out[0]["position_multiplier"] == 0.5
