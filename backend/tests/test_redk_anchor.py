# -*- coding: utf-8 -*-
"""红K锚（狼大 2026-03-06「用第一根或者前两根**顶板红K的开盘价和收盘价**做指标计算进场位置和止损位置」）。

背景：原实现用**自设**的"建仓前 13 根最低"当波段低点（语料只说"波段低点"、未给窗口），
实测该锚落在买点下方 10.6%~17.9% ⇒ 止损 −13~−14% 且**从不触发**；
由他"0.618 进场 + 止损 −6%"反推，锚应在买点下方 ≈3.1% ⇒ **红K 的开盘价**（实体 8% 的红K ⇒ −5.88%）。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_redk_setup as R  # noqa: E402
from app.services import wolf_early_stop as W  # noqa: E402


def _bars(rows):
    return [{"date": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in rows]


def _flat_then_big(open_px=37.86, close_px=39.67):
    """20 根窄幅盘整 + 一根大红K（模仿 603773 0305）。"""
    out = []
    px = 36.0
    for i in range(20):
        d = "202601%02d" % (i + 1)
        out.append((d, px, px + 0.3, px - 0.3, px + 0.05))
    out.append(("20260121", open_px, close_px + 0.1, open_px - 0.05, close_px))
    return _bars(out)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("WOLF_EARLY_STOP_ANCHOR", "WOLF_BUY_DATE_PER_ROUND", "WOLF_REDK_LOOKBACK",
              "WOLF_REDK_RISE_PCT", "WOLF_REDK_CONSOL_AMP"):
        monkeypatch.delenv(k, raising=False)


def test_大红K开盘价就是锚(monkeypatch):
    b = _flat_then_big()
    assert R.big_red_open(b, "20260121", "SH603773") == pytest.approx(37.86)
    # 买点在信号次日 ⇒ 回看窗口内仍能找到那根红K
    b.append({"date": "20260122", "open": 39.68, "high": 41.25, "low": 38.47, "close": 40.18})
    assert R.big_red_open(b, "20260122", "SH603773") == pytest.approx(37.86)


def test_无大红K时返回None_由调用方failopen():
    b = _bars([("202601%02d" % (i + 1), 36 + i * 0.01, 36.3 + i * 0.01, 35.9 + i * 0.01, 36.1 + i * 0.01)
               for i in range(10)])
    assert R.big_red_open(b, "20260110", "SH601138") is None


def test_回看窗口外的大红K不算(monkeypatch):
    b = _flat_then_big()
    for i in range(10):                       # 信号后隔 10 根平价K
        b.append({"date": "202602%02d" % (i + 1), "open": 40.0, "high": 40.2, "low": 39.8, "close": 40.0})
    monkeypatch.setenv("WOLF_REDK_LOOKBACK", "5")
    assert R.big_red_open(b, "20260210", "SH603773") is None


def test_20CM门槛更高(monkeypatch):
    b = _bars([("202601%02d" % (i + 1), 36.0, 36.3, 35.8, 36.0) for i in range(5)] +
              [("20260106", 36.0, 37.5, 35.9, 37.2)])       # +3.3%：主板算大红K？不；20CM 更不
    assert R.big_red_open(b, "20260106", "SH600000") is None
    assert R.big_red_open(b, "20260106", "SZ300001") is None


def test_detect信号与进场止损位():
    b = _flat_then_big()
    sig = R.detect(b, "20260121", "SH603773")
    assert sig and sig["open"] == pytest.approx(37.86) and sig["close"] == pytest.approx(39.67)
    assert sig["entry"] == pytest.approx(39.67 - 0.618 * (39.67 - 37.86))     # 0.618 回撤位
    assert sig["stop"] == pytest.approx(37.86 * 0.97, abs=1e-3)                # 红K开盘 ×0.97（早停价按 3 位取整）
    # 进场距止损的量级：本例实体 4.8% ⇒ ≈ −4.7%（身体越粗，越接近他说的 −6%）
    assert (sig["stop"] / sig["entry"] - 1) * 100 == pytest.approx(-4.7, abs=0.3)


def test_实体八个点的红K正好复现他的六个点():
    """验收口径（狼大「按我0.618买入的情况下 这样止损就是-6%左右」）。"""
    rows = [("202601%02d" % (i + 1), 36.0, 36.3, 35.8, 36.05) for i in range(20)]
    rows.append(("20260121", 37.00, 40.05, 36.95, 40.00))          # 实体 +8.1% 的大红K
    sig = R.detect(_bars(rows), "20260121", "SH603773")
    assert sig
    assert (sig["stop"] / sig["entry"] - 1) * 100 == pytest.approx(-5.9, abs=0.3)


def test_detect不是第一根就不算():
    b = _flat_then_big()
    b.append({"date": "20260122", "open": 39.7, "high": 43.7, "low": 39.6, "close": 43.6})   # 连续第二根大红K
    assert R.detect(b, "20260122", "SH603773") is None


def test_盘整太宽不算(monkeypatch):
    rows = []
    px = 30.0
    for i in range(20):                       # 20 日振幅 ~40% ⇒ 不是"长期盘整"
        rows.append(("202601%02d" % (i + 1), px, px + 2, px - 2, px + 1))
        px *= 1.02
    rows.append(("20260121", px, px * 1.1, px * 0.99, px * 1.099))
    monkeypatch.setenv("WOLF_REDK_CONSOL_AMP", "15")
    assert R.detect(_bars(rows), "20260121", "SH603773") is None


def test_resolve_stop用红K锚(monkeypatch):
    monkeypatch.setenv("WOLF_EARLY_STOP_ANCHOR", "redk")
    b = _flat_then_big()
    b.append({"date": "20260122", "open": 39.68, "high": 41.25, "low": 38.47, "close": 40.18})
    sp, src, why = W.resolve_stop(None, b, "2026-01-22", symbol="SH603773")
    assert src == "wolf_early_swing"
    assert sp == pytest.approx(W.early_stop_price(37.86, 3), abs=1e-6)   # 与既有取整口径一致
    assert "锚=redk" in why


def test_resolve_stop无红K时退回swing13(monkeypatch):
    monkeypatch.setenv("WOLF_EARLY_STOP_ANCHOR", "redk")
    b = _bars([("202601%02d" % (i + 1), 36 + i * 0.01, 36.3 + i * 0.01, 35.9 + i * 0.01, 36.1 + i * 0.01)
               for i in range(14)])
    sp, src, why = W.resolve_stop(None, b, "2026-01-14", symbol="SH601138")
    assert (sp is None) or ("锚=swing13" in why)


def test_默认仍是swing13():
    b = _flat_then_big()
    sp, src, why = W.resolve_stop(None, b, "2026-01-21", symbol="SH603773")
    assert "锚=swing13" in why, "库内默认必须保持原口径"


def test_接线护栏_网关与开关存在():
    gw = (REPO_ROOT / "backend/app/services/t_gateway.py").read_text(encoding="utf-8")
    es = (REPO_ROOT / "backend/app/services/wolf_early_stop.py").read_text(encoding="utf-8")
    assert "WOLF_ADDON_CAP" in gw and 'level": "ADDON_CAP"' in gw
    assert "WOLF_EARLY_STOP_ANCHOR" in es
    assert "WOLF_BUY_DATE_PER_ROUND" in es
