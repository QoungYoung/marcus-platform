import json, os
import pytest

P = os.path.join(os.path.dirname(__file__), "..", "..", "data", "l0", "decision_states.jsonl")

pytestmark = pytest.mark.skipif(not os.path.exists(P), reason="P0 decision_states not built yet")


def _rows():
    return [json.loads(l) for l in open(P, encoding="utf-8")]


def test_counts_and_ids():
    rows = _rows()
    assert len(rows) == 100
    assert len({r["id"] for r in rows}) == 100
    assert sum(1 for r in rows if r["id"].startswith("act-")) == 83
    assert sum(1 for r in rows if r["id"].startswith("neg-")) == 17


def test_enums():
    rows = _rows()
    assert all(r["stage"] in {"regime", "direction", "vehicle", "entry", "exit"} for r in rows)
    assert all(r["action"] in {"build", "add", "t_only", "defense", "exit", "reduce", "no_trade", "watch", "negative"} for r in rows)
    assert all(r["vehicle"] in {"etf", "stock", "theme", "position", "none"} for r in rows)
    assert all(r.get("quote") for r in rows)
