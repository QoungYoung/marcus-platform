from app.services.wolf_card.selection_rules import apply_bellwether_gate


def test_alive_allows_second():
    out = apply_bellwether_gate([{"code": "A", "role": "second"}], {"state": "alive"})
    assert len(out) == 1 and out[0]["bellwether_gate"] == "allow"


def test_warning_blocks_second_but_keeps_leader():
    out = apply_bellwether_gate([{"code": "L", "role": "leader"}, {"code": "S", "role": "second"}], {"state": "warning"})
    assert [p["code"] for p in out] == ["L"]
    assert out[0]["bellwether_gate"] == "allow"


def test_dead_blocks_all():
    out = apply_bellwether_gate([{"code": "L", "role": "leader"}, {"code": "S", "role": "second"}], {"state": "dead"})
    assert out == []


def test_unknown_blocks_second():
    out = apply_bellwether_gate([{"code": "L", "role": "leader"}, {"code": "S", "role": "second"}], {})
    assert [p["code"] for p in out] == ["L"]
