# -*- coding: utf-8 -*-
"""甲/乙/丙 单测（2026-09-22 用户："都做"；均起因于「兆易创新 0409 止跌企稳为什么没买」）。

甲 `WOLF_BAN_TTL_CAL_FIX`：删票 TTL 的日历口径修复（原实现出窗即退化成自然日 ⇒ 禁令提前约 4 个交易日解除）。
乙 `WOLF_THEME_SRC=mainline_select`：主题源改用逐日 as-of 的 mainline_select（原 fusion 在回放里近乎常数）。
丙 `WOLF_STAGE_S1_RELAX`：S1 的量能半边不再是否决项；"价格止跌但没缩量"单列 `止跌待确认`。
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend", REPO_ROOT / "jobs", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

BARS = str(REPO_ROOT / "data" / "_bt_full" / "bars.sqlite")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("WOLF_BAN_TTL_CAL_FIX", "WOLF_STAGE_S1_RELAX", "WOLF_THEME_SRC", "BT_BARS_DB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BT_BARS_DB", BARS)


# ── 甲 ────────────────────────────────────────────────────────
def test_甲_日历口径_修复后不再提前解除(monkeypatch):
    import bt_run_pinned as brp
    from app.services import wolf_ticket_ban as TB
    monkeypatch.setenv("WOLF_BAN_TTL_CAL_FIX", "1")
    brp.pin_clock("20260409")
    assert TB.elapsed_td("20260323", "20260409") == 12, "真实交易日数 = 12"
    brp.pin_clock("20260410")
    assert TB.elapsed_td("20260323", "20260410") == 13, "第 13 个交易日才到期"
    brp.pin_clock("20260403")
    assert TB.elapsed_td("20260323", "20260403") == 9


def test_甲_开关关时保持旧行为(monkeypatch):
    import bt_run_pinned as brp
    from app.services import wolf_ticket_ban as TB
    monkeypatch.delenv("WOLF_BAN_TTL_CAL_FIX", raising=False)
    brp.pin_clock("20260407")
    # 旧口径：窗口日历出窗 ⇒ 退化成自然日（15 天）⇒ 13 天 TTL 被判到期
    assert TB.elapsed_td("20260323", "20260407") >= 13


def test_甲_日历完全不可用时保守估算(monkeypatch):
    from app.services import wolf_ticket_ban as TB
    monkeypatch.setenv("WOLF_BAN_TTL_CAL_FIX", "1")
    monkeypatch.setattr(TB, "_norm8", lambda x: "".join(ch for ch in str(x or "") if ch.isdigit())[:8])
    # 指向不存在的 bars 库 + 让窗口日历返回空 ⇒ 走保守估算
    monkeypatch.setenv("BT_BARS_DB", "/nonexistent/bars.sqlite")
    monkeypatch.setattr(TB, "_win_cal", lambda *a, **k: [], raising=False) if hasattr(TB, "_win_cal") else None
    n = TB.elapsed_td("20260323", "20260409")
    assert 0 < n <= 17, "保守估算不得超过自然日数（自然日 17）"


# ── 乙 ────────────────────────────────────────────────────────
def test_乙_主题源用逐日_mainline_select(monkeypatch, tmp_path):
    import switch_builder as SB
    (tmp_path / "main_line_state.json").write_text(json.dumps({
        "fusion": {"A/主题": {"score": 1.0}, "B/主题": {"score": 0.9}},
        "mainline_select": {"date": "20260408", "mainline": "半导体/芯片", "second": "新能源/电池"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(SB, "DATA", str(tmp_path))
    monkeypatch.setenv("WOLF_THEME_SRC", "mainline_select")
    assert SB.theme_top12(2) == (["半导体/芯片", "新能源/电池"], "mainline_select")
    monkeypatch.delenv("WOLF_THEME_SRC")
    assert SB.theme_top12(2) == (["A/主题", "B/主题"], "fusion"), "默认仍是 fusion（生产零影响）"


# ── 丙 ────────────────────────────────────────────────────────
def _stage(code, cut, relax):
    import importlib
    import pandas as pd
    import confirm_chain as CC
    os.environ["WOLF_STAGE_S1_RELAX"] = relax
    importlib.reload(CC)
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % BARS, uri=True)
    rows = con.execute("select close, vol from bars where ts_code=? and trade_date<=? order by trade_date",
                       (code, cut)).fetchall()
    con.close()
    ser = pd.Series([r[0] for r in rows]); vol = pd.Series([r[1] for r in rows])
    return CC.confirm_chain(ser.tail(200), net=None, vol=vol.tail(200))


def test_丙_兆易创新0408判级_旧下跌中_新止跌待确认(monkeypatch):
    old = _stage("603986.SH", "20260408", "0")     # 0409 的 08:18 候选池用的就是 ≤0408 的数据
    new = _stage("603986.SH", "20260408", "1")
    assert old["stage"] == "下跌中"
    assert new["stage"] == "止跌待确认", "改后 0409 的候选池里就有它了"
    assert new["signals"]["price_stop"] is True and new["signals"]["vol_shrink"] is False
    os.environ.pop("WOLF_STAGE_S1_RELAX", None)
    import importlib
    import confirm_chain as CC
    importlib.reload(CC)


def test_丙_放量下跌仍判下跌中(monkeypatch):
    """语义红线：今天还在放量下跌（量 z>0 且收跌）⇒ 不进候选（与新加的 B 闸同向）。"""
    c = _stage("603986.SH", "20260409", "1")       # 0409 收跌 −2.14%、vol z≈0.9
    assert c["stage"] == "下跌中"
    os.environ.pop("WOLF_STAGE_S1_RELAX", None)
    import importlib
    import confirm_chain as CC
    importlib.reload(CC)


def test_丙_白名单只在开关开时扩(monkeypatch):
    import switch_builder as SB
    monkeypatch.setattr(SB, "_dip_on", lambda *a, **k: True)
    monkeypatch.setattr(SB, "BASE_STAGES", ("突破候选",))
    monkeypatch.setattr(SB, "DIP_STAGES", ("结构到位",))
    monkeypatch.delenv("WOLF_STAGE_S1_RELAX", raising=False)
    import confirm_chain as CC
    import wolf_context as WC
    monkeypatch.setattr(WC, "theme_qualify_struct", lambda t: (True, "stub"), raising=False)
    monkeypatch.setattr(CC, "s1_relax_on", lambda: False)
    assert "止跌待确认" not in SB.stages_for_theme("任意主题")
    monkeypatch.setattr(CC, "s1_relax_on", lambda: True)
    got = SB.stages_for_theme("任意主题")
    assert "止跌待确认" in got and "结构到位" in got
