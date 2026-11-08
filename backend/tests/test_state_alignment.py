import json, os
import pytest

P = os.path.join(os.path.dirname(__file__), "..", "..", "data", "eval_state_alignment.json")
pytestmark = pytest.mark.skipif(not os.path.exists(P), reason="P1 alignment not built yet")


def test_alignment_counts():
    x = json.load(open(P, encoding="utf-8"))
    assert x["n"] == 83
    assert sum(v for _, v in x["status"]) == 83
    assert len(x["violations"]) == 2
    assert len(x["needs_confirm"]) == 4
    assert len(x["unknown_gate"]) == 21
