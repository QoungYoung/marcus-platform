# -*- coding: utf-8 -*-
"""买单「挂到低位的线」上（狼大 2026-04-10；2026-09-20 用户拍板 C）。

语料：「所有自己手上的票**找低位的线挂进去** 不管是**黄金分割** 还是**均线** **挂远一点**」
自设：线集合（ma10/ma20/prev_low/fib618）、最小间距 0.5%、成交价取线价。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


@pytest.fixture()
def WL(monkeypatch):
    monkeypatch.setenv("WOLF_BUY_LINE", "1")
    monkeypatch.delenv("WOLF_BUY_LINE_SET", raising=False)
    monkeypatch.setenv("WOLF_BUY_LINE_MIN_GAP_PCT", "0.5")
    from app.services import wolf_buy_line as _W
    importlib.reload(_W)
    _W._CACHE.clear()
    return _W


def _bars(closes, lows=None, highs=None):
    lows = lows or [c * 0.99 for c in closes]
    highs = highs or [c * 1.01 for c in closes]
    return [{"close": c, "low": l, "high": h} for c, l, h in zip(closes, lows, highs)]


def test_库内默认关(WL, monkeypatch):
    monkeypatch.delenv("WOLF_BUY_LINE", raising=False)
    importlib.reload(WL)
    assert WL.enabled() is False, "库内默认必须关（生产零影响）"


def test_取现价下方最近的那条线(WL):
    lns = {"ma10": 63.267, "ma20": 62.616, "prev_low": 62.71}
    name, px = WL.pick(64.64, lns, gap_pct=0.5)
    assert name == "ma10" and px == 63.267, "低位的线里取最高的那条"


def test_线不够远就不挂单(WL):
    lns = {"ma10": 64.60, "ma20": 64.55}          # 都贴着现价（<0.5%）
    assert WL.pick(64.64, lns, gap_pct=0.5) == (None, None)


def test_现价下方没有线就不挂单(WL):
    assert WL.pick(60.0, {"ma20": 62.6, "ma10": 63.2}, gap_pct=0.5) == (None, None)


def test_此刻在线上才成交且成交价等于线价(WL):
    bars = _bars([60 + i * 0.1 for i in range(20)], lows=[59.5] * 20)
    lns = WL.lines(bars, "SH601138", "20260106")
    ma10 = lns["ma10"]
    # 现价明显高于线（+0.6%）⇒ 不成交，等价格下来
    r1 = WL.resolve(bars, "SH601138", "20260106", {"current": ma10 * 1.006, "low": ma10 - 0.3})
    assert r1["fire"] is False and "等价格下来" in r1["why"]
    # 现价在线价容差内 ⇒ 成交，成交价 = 线价（保守，不假设更便宜）
    r2 = WL.resolve(bars, "SH601138", "20260106", {"current": ma10 * 1.001, "low": ma10 - 0.3})
    assert r2["fire"] is True and r2["price"] == ma10 and r2["line"]


def test_MA与前低由as_of昨日日线算出(WL):
    bars = _bars([10.0] * 9 + [20.0], lows=[9.0] * 9 + [8.0])
    lns = WL.lines(bars, "SZ000001", "20260106")
    assert lns["ma10"] == pytest.approx(11.0)      # (10*9+20)/10
    assert lns["prev_low"] == 8.0                  # 最后一根=昨日


def test_形状容错_字典与元组(WL):
    tup = [("20260105", 1.0, 1.1, 0.9, 10.0)] * 10
    tup = [(d, o, h, l, c) for (d, o, h, l, c) in [(x[0], x[1], x[2], x[3], x[4]) for x in tup]]
    lns1 = WL.lines(tup, "SZ000001", "20260106")
    dct = {"20260105": {"close": 10.0, "high": 11.0, "low": 9.0}}
    lns2 = WL.lines(dct, "SZ000001", "20260106")
    assert lns1.get("ma10") == 10.0 and lns2.get("prev_low") == 9.0


def test_线集合可配(WL, monkeypatch):
    monkeypatch.setenv("WOLF_BUY_LINE_SET", "ma20")
    importlib.reload(WL)
    assert WL.line_set() == ["ma20"]
    bars = _bars([10.0] * 25, lows=[9.5] * 25)
    assert set(WL.lines(bars, "SZ000001", "20260106")) == {"ma20"}


def test_fib618取红K进场位(WL, monkeypatch):
    from app.services import wolf_buy_line as _W
    monkeypatch.setattr(_W, "_fib618", lambda b, s, d: 38.55, raising=False)
    bars = _bars([40.0] * 20, lows=[39.0] * 20)
    assert _W.lines(bars, "SH603773", "20260306").get("fib618") == 38.55


def test_pins钉住挂线口径():
    """2026-09-20 用户拍板① 之后 `WOLF_BUY_LINE='0'`（去掉了挂线）——代码与参数保留、口径关闭。"""
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    for frag in ("WOLF_BUY_LINE='0'", "WOLF_BUY_LINE_MIN_GAP_PCT='0.5'",
                 "WOLF_BUY_LINE_SET='ma5,ma10,ma20,prev_low,fib618'"):
        assert frag in txt, "pins 少了 %s" % frag


def test_选线锚在昨收而不是实时现价(WL):
    """首版锚在实时现价 ⇒ 价格越跌线越往下挪、当日永远追不上（实测 0106 零成交）。"""
    bars = _bars([10.0] * 19 + [12.0], lows=[9.0] * 20)      # 昨收 12.0，MA10 = (10*9+12)/10 = 10.2
    lns = WL.lines(bars, "SZ000001", "20260106")
    assert lns["ma10"] == pytest.approx(10.2)
    # 现价已跌到 10.4：锚昨收 ⇒ 仍选 MA10(10.2)；锚现价则会把线挪走
    # 选线看昨收（12.0）⇒ 选中 MA10(10.2)；若锚现价(10.4) 则会因"线不够远"而无单
    r = WL.resolve(bars, "SZ000001", "20260106", {"current": 10.2, "low": 10.0})
    assert r["line"] == "ma10" and r["price"] == pytest.approx(10.2) and r["fire"] is True


def test_回测日线源强制as_of(WL, tmp_path, monkeypatch):
    """bars_sqlite 必须只取 trade_date < day 的日线（无未来函数）；文件不存在则返回 None（生产零影响）。"""
    import sqlite3
    db = tmp_path / "bars.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("create table bars(ts_code text, trade_date text, open real, high real, low real, close real)")
    for i, d in enumerate(("20260102", "20260105", "20260106")):
        con.execute("insert into bars values(?,?,?,?,?,?)", ("601138.SH", d, 10 + i, 11 + i, 9 + i, 10.5 + i))
    con.commit(); con.close()
    monkeypatch.setenv("WOLF_BUY_LINE_BARS_DB", str(db))
    bars = WL.bars_sqlite("SH601138", "20260106", 40)
    assert [b["date"] for b in bars] == ["20260102", "20260105"], "当日(0106)必须被排除"
    monkeypatch.setenv("WOLF_BUY_LINE_BARS_DB", str(tmp_path / "none.sqlite"))
    assert WL.bars_sqlite("SH601138", "20260106") is None


def test_当日bar即使混进来也会被剔除(WL):
    """防未来函数：数源若带上当日未收盘 bar，lines() 必须丢掉它。"""
    bars = [{"date": "20260105", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.0}] * 19
    bars.append({"date": "20260106", "open": 99.0, "high": 99.0, "low": 99.0, "close": 99.0})
    lns = WL.lines(bars, "SZ000001", "20260106")
    assert lns.get("ma10") == pytest.approx(10.0), "9 根 10.0 + 1 根 99.0 会算出 18.9 ⇒ 说明当日 bar 没被剔除"
    assert lns.get("prev_low") == 9.5


def test_必须此刻在线上才成交(WL):
    """实跑教训：只判"当日最低碰过线"会在现价远离线时发单（AI 按脱节>1% 拒执行）。"""
    bars = _bars([10.0] * 19 + [12.0], lows=[9.0] * 20)     # MA10 = 10.2，昨收 12.0
    lns = WL.lines(bars, "SZ000001", "20260106")
    px = lns["ma10"]
    # 当日低早已碰过线，但现价已远离（+3%）⇒ 不该成交
    r1 = WL.resolve(bars, "SZ000001", "20260106", {"current": px * 1.03, "low": px - 0.5})
    assert r1["fire"] is False and "等价格下来" in r1["why"]
    # 现价回到线上（容差内）⇒ 成交，成交价 = 线价
    r2 = WL.resolve(bars, "SZ000001", "20260106", {"current": px * 1.002, "low": px - 0.2})
    assert r2["fire"] is True and r2["price"] == px
    # 现价明显低于线 ⇒ 也成交，但**按现价**成交（min(线价,现价)，见下一个用例）
    r3 = WL.resolve(bars, "SZ000001", "20260106", {"current": px * 0.97, "low": px - 0.6})
    assert r3["fire"] is True and r3["price"] == pytest.approx(round(px * 0.97, 3))
    assert r3["line_price"] == px


def test_pins钉住容差():
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    assert "WOLF_BUY_LINE_TOL_PCT='0.3'" in txt


def test_现价在线下方时按现价成交(WL):
    """限价不会比线贵；现价已在线下方 ⇒ 成交价=现价（否则"建议价高于市价"会被 AI 当脱节拒单）。"""
    bars = _bars([10.0] * 19 + [12.0], lows=[9.0] * 20)     # MA10 = 10.2
    lns = WL.lines(bars, "SZ000001", "20260106")
    px = lns["ma10"]
    r = WL.resolve(bars, "SZ000001", "20260106", {"current": px * 0.97, "low": px - 0.6})
    assert r["fire"] is True
    assert r["price"] == pytest.approx(round(px * 0.97, 3)), "现价低于线 ⇒ 按现价成交"
    assert r["line_price"] == px, "线价仍要留档（审计用）"
    # 现价就在线上 ⇒ 成交价 = 线价
    r2 = WL.resolve(bars, "SZ000001", "20260106", {"current": px, "low": px - 0.1})
    assert r2["price"] == pytest.approx(px)


# ─────────────── 适用范围（WOLF_BUY_LINE_SCOPE：all / rebuy / new）───────────────

def test_范围默认all(WL, monkeypatch):
    monkeypatch.delenv("WOLF_BUY_LINE_SCOPE", raising=False)
    importlib.reload(WL)
    assert WL.scope() == "all"
    assert WL.applies("all", True) is True and WL.applies("all", False) is True


def test_范围rebuy只对再买(WL):
    assert WL.applies("rebuy", True) is True
    assert WL.applies("rebuy", False) is False


def test_范围new只对首次建仓(WL):
    assert WL.applies("new", True) is False
    assert WL.applies("new", False) is True


def test_范围取env且脏值回落(WL, monkeypatch):
    monkeypatch.setenv("WOLF_BUY_LINE_SCOPE", "REBUY")
    importlib.reload(WL)
    assert WL.scope() == "rebuy"
    assert WL.applies(None, False) is False, "应读 env"
    monkeypatch.setenv("WOLF_BUY_LINE_SCOPE", "zzz")
    importlib.reload(WL)
    assert WL.applies("zzz", False) is True, "脏值 ⇒ 当作 all（不放行任何单也不误挡）"


def test_pins钉住范围():
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    assert "WOLF_BUY_LINE_SCOPE=" in txt
