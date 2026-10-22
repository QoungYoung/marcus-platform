from app.services.wolf_card.selection_rules import annotate_bellwether


def test_annotation_keeps_all_picks():
    picks = [{"code": "L", "role": "leader"}, {"code": "S", "role": "second"}]
    out = annotate_bellwether(picks, {"state": "warning"})
    assert [p["code"] for p in out] == ["L", "S"]
    assert all(p["bellwether_context"] == "advisory" for p in out)
    assert all("position_multiplier" not in p for p in out)
    assert all("bellwether_gate" not in p for p in out)
