import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_policy_has_sources_and_rules():
    import yaml
    p = yaml.safe_load((ROOT / "config/mainline/wolf_policy.yaml").read_text(encoding="utf-8"))
    ids = {r["id"] for r in p.get("rules") or []}
    for x in ("role_triad", "overhang_light", "leader_first", "climax_spillover"):
        assert x in ids
    assert all(r.get("text") for r in p.get("rules") or [])


def test_select_no_unsourced_hard_gates():
    s = (ROOT / "jobs/theme_sleeve_select.py").read_text(encoding="utf-8")
    assert "breadth" not in s or "< 0.35" not in s
    assert "action\") != \"buy\"" not in s
    assert "if len(sel) >= 2" not in s


def test_facts_no_b4_failed_label():
    s = (ROOT / "jobs/theme_sleeve_facts.py").read_text(encoding="utf-8")
    assert 'tag = "failed"' not in s
    assert "open_heavy" in s


def test_1102_review_has_no_high():
    d = json.loads((ROOT / "tests/fixtures/wolf_review_20261102.json").read_text(encoding="utf-8"))
    high = [v for v in d.get("violations") or [] if v.get("severity") == "high"]
    assert high == [], high
