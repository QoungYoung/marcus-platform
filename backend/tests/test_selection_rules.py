import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "services"))
from wolf_card.selection_rules import select_for_theme


def test_weak_stage_selects_resilient_zhongwu():
    r = select_for_theme("资源/周期", "20260615", 6, line="low_position")
    assert r["stage"]["stage"] == "weak"
    codes = [p["code"] for p in r["picks"]]
    assert "000657.SZ" in codes
    assert r["picks"][0]["code"] == "000657.SZ"


def test_semi_weak_stage_has_picks():
    r = select_for_theme("半导体/芯片", "20260812", 6, line="low_position")
    assert r["picks"]
    assert r["stage"]["stage"] in ("weak", "downtrend", "low_flat", "oversold")


def test_subtheme_split_ai_soft_vs_optical():
    from wolf_card.selection_rules import select_for_theme
    soft = select_for_theme("AI/算力/科技", "20260812", 6, line="low_position", subtheme="AI软")
    opt = select_for_theme("AI/算力/科技", "20260812", 6, line="low_position", subtheme="光模块/CPO")
    soft_codes = [p["code"] for p in soft["picks"]]
    opt_codes = [p["code"] for p in opt["picks"]]
    assert "300502.SZ" in opt_codes and "300502.SZ" not in soft_codes
    assert len(opt_codes) == len(set(opt_codes))


def test_rebound_watch_has_fengzhushou():
    from wolf_card.selection_rules import select_for_theme
    r = select_for_theme("AI/算力/科技", "20260812", 6, line="low_position", subtheme="AI软")
    codes = [p["code"] for p in r.get("rebound_watch", [])]
    assert codes and codes[0] == "301382.SZ"


def test_main_business_removes_dirty_ai_soft():
    from wolf_card.selection_rules import select_for_theme
    r = select_for_theme("AI/算力/科技", "20260812", 6, line="low_position", subtheme="AI软")
    codes = [p["code"] for p in r.get("rebound_watch", [])] + [p["code"] for p in r.get("picks", [])]
    assert "301382.SZ" in codes
    assert "002739.SZ" not in codes   # 儒意电影：影视
    assert "000560.SZ" not in codes   # 我爱我家：房产服务
    assert "600186.SH" not in codes   # 莲花控股：食品
