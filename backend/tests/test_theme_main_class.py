# -*- coding: utf-8 -*-
"""主题「主类」确定性判定单测（前置 dsh 主营校验）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import theme_main_class as M      # noqa: E402
import theme_member_llm as TM     # noqa: E402


def test_三分支(monkeypatch):
    monkeypatch.setattr(M, "RULE_ON", True)
    assert M.decide("X", "半导体/芯片", ["半导体", "融资融券"])[0] == "allow"
    assert M.decide("X", "半导体/芯片", ["产业地产", "房地产", "人工智能"])[0] == "reject"
    assert M.decide("X", "半导体/芯片", ["融资融券", "昨日涨停", "深股通"])[0] == "defer"
    assert M.decide("X", "未配置主题", ["半导体"])[0] == "defer"
    monkeypatch.setattr(M, "RULE_ON", False)
    assert M.decide("X", "半导体/芯片", ["半导体"])[0] == "defer"      # 开关关 ⇒ 全交 dsh


def test_空概念列表走取数(monkeypatch):
    """调用方传 [] 表示"没给概念" ⇒ 必须去 PG 取，否则会误判成 defer。"""
    monkeypatch.setattr(M, "RULE_ON", True)
    monkeypatch.setattr(M, "concepts_of", lambda s: ["半导体"])
    v, why = M.decide("SH603893", "半导体/芯片", [])
    assert v == "allow" and "半导体" in why


def test_错配票与真票(monkeypatch):
    monkeypatch.setattr(M, "RULE_ON", True)
    # 真半导体 ⇒ 放行
    assert M.decide("SH603893", "半导体/芯片", ["半导体", "AI芯片"])[0] == "allow"
    # 电子城/华泰/科德/日科 ⇒ 拦
    assert M.decide("SH600658", "半导体/芯片", ["半导体概念", "产业地产", "房地产"])[0] == "reject"
    assert M.decide("SH600308", "AI/算力/科技", ["AI语料", "大宗用纸", "造纸"])[0] == "reject"
    assert M.decide("SZ300192", "AI/算力/科技", ["AI芯片", "在线教育", "教育"])[0] == "reject"
    assert M.decide("SZ300214", "AI/算力/科技", ["东数西算", "化学制品", "基础化工"])[0] == "reject"
    # 快克智能/科瑞技术：在 AI 主题下拦，在机器人主题下放行（错的是标签不是票）
    _k = ["CPO概念", "先进封装", "机械设备", "自动化设备", "工业母机", "机器人概念"]
    assert M.decide("SH603203", "AI/算力/科技", _k)[0] == "reject"
    assert M.decide("SH603203", "机器人/智能制造", _k)[0] == "allow"


def test_接进is_member(monkeypatch):
    monkeypatch.setenv("BT_ASOF_FETCH", "1")
    monkeypatch.delenv("WOLF_THEME_MEMBER_CHECK", raising=False)
    import importlib
    importlib.reload(TM)
    assert TM.ON is True
    monkeypatch.setattr(M, "decide", lambda s, t, c=None: ("reject", "主类=机械设备 不属主题"))
    ok, why = TM.is_member("SH603203", [], "AI/算力/科技")
    assert ok is False and why.startswith("主类:"), why
    monkeypatch.setattr(M, "decide", lambda s, t, c=None: ("allow", "主类=半导体"))
    assert TM.is_member("SH603893", [], "半导体/芯片")[0] is True


def test_词表自洽():
    for th, ss in M.THEME_MAIN_CLASS.items():
        assert ss, th
        assert ss <= M.INDUSTRY_WORDS, (th, sorted(ss - M.INDUSTRY_WORDS)[:5])
    assert "半导体" in M.THEME_MAIN_CLASS["半导体/芯片"]
    assert "机械设备" in M.THEME_MAIN_CLASS["机器人/智能制造"]
