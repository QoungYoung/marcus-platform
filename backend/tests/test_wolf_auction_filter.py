import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "jobs"))
from wolf_auction_filter import classify  # noqa: E402


def _row(price, pre, vr, amt=20_000_000):
    return {"price": price, "pre_close": pre, "volume_ratio": vr, "amount": amt,
            "open": price, "high": price, "low": price}


def test_auction_pass():
    r = classify(_row(10.2, 10.0, 1.2), "trend")
    assert r["state"] == "pass" and r["entry"] == "auction"


def test_auction_no_chase():
    r = classify(_row(10.5, 10.0, 1.5), "trend")
    assert r["state"] == "no_chase" and r["entry"] == "drop"


def test_auction_weak_left_side_keeps_trigger():
    r = classify(_row(10.1, 10.0, 0.2, 1_000_000), "left_side")
    assert r["state"] == "weak" and r["entry"] == "wait_trigger"


def test_auction_weak_open_waits():
    r = classify(_row(9.7, 10.0, 0.5), "trend")
    assert r["state"] == "wait" and r["entry"] == "wait_trigger"
