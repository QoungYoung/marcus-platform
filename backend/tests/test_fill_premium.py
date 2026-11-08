# -*- coding: utf-8 -*-
"""成交价溢价 `WOLF_FILL_PREMIUM_PCT`（2026-09-20 用户拍板「砍掉」那条无语料依据的 0.1%）。

背景：`suggest_bid_price = 现价×0.999`（且 `t_account.py:311` 取价时**卖单也读该字段** ⇒ 卖侧同样 ×0.999）
⇒ 买卖两侧同向、往返抵消，但**所有成交价系统性比市场低 0.1%**，污染"我们的买点 vs 他的 0.618 位"这类对照。
默认仍 0.1（生产零影响），回测 pins 置 0 ⇒ 成交价 = 成交时报价。
"""
import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _tm(monkeypatch, val=None):
    from app.services import t_monitor as TM
    if val is None:
        monkeypatch.delenv("WOLF_FILL_PREMIUM_PCT", raising=False)
    else:
        monkeypatch.setenv("WOLF_FILL_PREMIUM_PCT", val)
    importlib.reload(TM)
    return TM


def test_默认仍是0_1_生产零影响(monkeypatch):
    TM = _tm(monkeypatch)
    assert TM.fill_premium_pct() == 0.1
    bid, ask = TM.fill_prices(100.0)
    assert bid == 99.9 and ask == 100.1, "默认与生产现状一致（库内不改行为）"


def test_置0后成交价等于报价(monkeypatch):
    TM = _tm(monkeypatch, "0")
    assert TM.fill_premium_pct() == 0.0
    bid, ask = TM.fill_prices(64.64)
    assert bid == 64.64 and ask == 64.64, "砍掉 0.1% ⇒ 买卖两侧都按报价"


def test_显式线价优先于溢价(monkeypatch):
    TM = _tm(monkeypatch, "0.1")
    bid, ask = TM.fill_prices(64.64, bid=63.267)
    assert bid == 63.267, "C 口径的挂线价优先"


def test_脏值回落与下限(monkeypatch):
    assert _tm(monkeypatch, "abc").fill_premium_pct() == 0.1
    assert _tm(monkeypatch, "-5").fill_premium_pct() == 0.0


def test_pins钉住砍掉():
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    assert "WOLF_FILL_PREMIUM_PCT='0'" in txt


def test_不追高闸的参照价未被误改():
    """`round(_qc*0.999,3)` 是给不追高闸的**参照价**（PREMIUM_PCT 默认 0 ⇒ 只要现价>参照就算没等到回踩），
    不是成交价 —— 改它会让该闸永远放行。这里做源码级护栏。"""
    src = (REPO_ROOT / "backend" / "app" / "services" / "t_monitor.py").read_text(encoding="utf-8")
    assert "_ncz.verdict(_qc, round(_qc * 0.999, 3)" in src
