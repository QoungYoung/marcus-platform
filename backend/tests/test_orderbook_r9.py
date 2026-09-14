# -*- coding: utf-8 -*-
"""R9 数据底座单测（盘口解析 / 卖压代理）。"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services import t_orderbook as OB


def _fields(cur=10.0, ask1=12345, bid1=6789):
    f = [""] * 40
    f[1], f[3] = "测试", str(cur)
    f[9], f[10] = "9.99", str(bid1)          # 买一价/量
    f[19], f[20] = "10.01", str(ask1)        # 卖一价/量
    f[21], f[22] = "10.02", "1000"
    return f


def test_parse_depth_maps_ask_bid():
    d = OB.parse_depth(_fields())
    assert d["bid"][0] == (9.99, 6789.0)
    assert d["ask"][0] == (10.01, 12345.0)
    assert d["ask"][1] == (10.02, 1000.0)


def test_parse_depth_missing_is_none_not_zero():
    f = _fields()
    f[19] = "0"; f[20] = "0"          # 卖一价与量都缺
    d = OB.parse_depth(f)
    assert d["ask"][0][0] is None            # 缺失不当 0
    m = OB.depth_metrics(d, cur=10.0)
    assert m["ask_vol"] == 1000.0            # 只算有效档


def test_metrics_wall_ratio_and_near():
    d = OB.parse_depth(_fields())
    m = OB.depth_metrics(d, cur=10.0)
    assert m["wall_ratio"] == round(13345 / (13345 + 6789), 4)
    assert m["ask_big_share"] == round(12345 / 13345, 4)
    assert m["near_ratio"] == 1.0            # 卖一 10.01 在现价 +1% 内


def test_metrics_empty_depth_safe():
    m = OB.depth_metrics({"bid": [], "ask": []}, cur=None)
    assert m["wall_ratio"] is None and m["bid_vol"] == 0 and m["ask_vol"] == 0


def test_norm_symbol():
    assert OB._norm("SH588170") == "sh588170"
    assert OB._norm("588170.SH") == "sh588170"
    assert OB._norm("sz159915") == "sz159915"
