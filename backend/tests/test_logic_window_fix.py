# -*- coding: utf-8 -*-
"""「13 日观察窗」两个 bug 的修复（`WOLF_LOGIC_WINDOW_FIX`；用户 2026-09-20 拍板①）。

语料（2026-03-05/06）：「**13 日内**需要碰新高或者新高。否则这个票呆的意义就不大」
                    「13天内是不是**高于**抄底底部那根K线的高点…**超过了**就是对的」
两个 bug：① 窗口未截断（扫 buy_date 之后**全部** bar ⇒ `any(hi>=ph)` 恒 True ⇒ 规则永不触发）；
         ② 判据 `>=` 应为严格 `>`。
"""
import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _es(monkeypatch, fix):
    from app.services import wolf_early_stop as ES
    monkeypatch.setenv("WOLF_LOGIC_WINDOW_FIX", "1" if fix else "0")
    monkeypatch.setenv("WOLF_SWING_HIGH_WIN", "5")
    importlib.reload(ES)
    return ES


def _bars(pre_highs, after_highs, base=10.0):
    """前 5 根用于算前高（最高 = pre_highs 的最大值）、其后是买入日之后的 bar。"""
    out = []
    for i, h in enumerate(pre_highs):
        out.append({"date": "202601%02d" % (i + 1), "open": base, "high": h, "low": base - 1, "close": base})
    d0 = "202601%02d" % (len(pre_highs) + 1)
    out.append({"date": d0, "open": base, "high": base, "low": base - 1, "close": base})   # 买入日
    for j, h in enumerate(after_highs):
        out.append({"date": "202602%02d" % (j + 1), "open": base, "high": h, "low": base - 1, "close": base})
    return out, d0


def test_默认关_旧行为(monkeypatch):
    ES = _es(monkeypatch, False)
    assert ES.window_fix_on() is False
    bars, d0 = _bars([9.0, 9.0, 12.0, 9.0, 9.0], [10.0] * 14 + [99.0])   # 第 15 天才碰前高(12)
    assert ES.made_new_high(bars, d0, days=13) is True, "旧行为：将来任意一天碰到就算碰过（bug①）"


def test_修复后_窗口截断(monkeypatch):
    ES = _es(monkeypatch, True)
    assert ES.window_fix_on() is True
    bars, d0 = _bars([9.0, 9.0, 12.0, 9.0, 9.0], [10.0] * 14 + [99.0])
    assert ES.made_new_high(bars, d0, days=13) is False, "13 日内没碰过 ⇒ 该出（窗口外的不算）"


def test_修复后_严格大于(monkeypatch):
    ES = _es(monkeypatch, True)
    bars, d0 = _bars([9.0, 12.0, 9.0, 9.0, 9.0], [12.0] * 13)            # 恰好等于前高 12.0
    assert ES.made_new_high(bars, d0, days=13) is False, "恰好打平不算「高于/超过了」"
    bars2, d02 = _bars([9.0, 12.0, 9.0, 9.0, 9.0], [10.0] * 5 + [12.01] + [10.0] * 7)
    assert ES.made_new_high(bars2, d02, days=13) is True, "窗口内严格超过 ⇒ 碰过"


def test_窗口未走完不动作(monkeypatch):
    ES = _es(monkeypatch, True)
    bars, d0 = _bars([9.0, 12.0, 9.0, 9.0, 9.0], [10.0] * 6)             # 只有 6 根后续 bar
    assert ES.made_new_high(bars, d0, days=13) is None, "窗口未走完 ⇒ 不动作（fail-open 持有）"


def test_数据不足返回None(monkeypatch):
    ES = _es(monkeypatch, True)
    bars = [{"date": "20260101", "open": 1, "high": 1, "low": 1, "close": 1}]
    assert ES.made_new_high(bars, "20260101", days=13) is None


def test_窗口内碰过_两态一致(monkeypatch):
    for fix in (True, False):
        ES = _es(monkeypatch, fix)
        bars, d0 = _bars([9.0, 12.0, 9.0, 9.0, 9.0], [10.0, 10.0, 13.0] + [10.0] * 10)
        assert ES.made_new_high(bars, d0, days=13) is True


def test_logic_time_stop_端到端(monkeypatch):
    """`logic_time_stop` 在修复后应当对「13 日不碰前高」的持仓给出离场。"""
    ES = _es(monkeypatch, True)
    bars, d0 = _bars([9.0, 12.0, 9.0, 9.0, 9.0], [10.0] * 14)
    out = ES.logic_time_stop(bars, d0, days=13, today="20260301")
    assert isinstance(out, tuple) and len(out) == 2
    assert out[0] is True, "13 日未碰前高 ⇒ 应离场：%s" % (out[1],)


def test_pins钉住两项():
    txt = (REPO_ROOT / "jobs" / "bt_env_pins.sh").read_text(encoding="utf-8")
    assert "WOLF_LOGIC_WINDOW_FIX='1'" in txt
    assert "WOLF_BUY_LINE='0'" in txt
