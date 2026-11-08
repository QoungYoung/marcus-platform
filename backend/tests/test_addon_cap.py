# -*- coding: utf-8 -*-
"""加仓口径闸（狼大 2026-06-15「第一根开头加一半 + 第二根红K尾盘打满，之后不再加」）。

实测背景（y26 SH603773）：进场 38.581 ≡ 他的 0.618 位 38.55，但 0311(收涨停)/0312/0316/0317
在 41.68~42.48 追高加仓 ⇒ 均价 39.02→42.19（+8.1%）⇒ 同一波下跌他 −6.5%、我们 −14.2%。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_addon_cap as A  # noqa: E402

# 603773 三月那轮的真实流水（含价格）
SEQ = [("20260306", "买入", 500, 38.581),
       ("20260310", "卖出", 100, 39.411), ("20260310", "卖出", 100, 39.920),
       ("20260311", "卖出", 100, 41.468), ("20260311", "卖出", 100, 41.468),
       ("20260311", "买入", 200, 42.138),
       ("20260312", "买入", 300, 42.477),
       ("20260313", "卖出", 100, 42.308),
       ("20260316", "卖出", 100, 42.907), ("20260316", "买入", 200, 42.388),
       ("20260317", "买入", 200, 41.678),
       ("20260324", "卖出", 800, 36.210)]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("WOLF_ADDON_CAP", "WOLF_ADDON_MAX_BUYS", "WOLF_ADDON_MAX_CALDAYS",
              "WOLF_ADDON_MAX_PREMIUM_PCT", "WOLF_ADDON_ROUND_FIX"):
        monkeypatch.delenv(k, raising=False)


def _decide_upto(day, vol, px):
    i = [k for k, r in enumerate(SEQ) if r[0] == day][0]
    return A.decide(SEQ[:i], vol, day, add_price=px)


def test_本轮起点与首笔价(monkeypatch):
    """本轮 = **当前未平仓**那一轮。0324 全部卖出后该轮已结束：
    开关关（默认）⇒ 旧行为返回历史首笔；开关开 ⇒ None（本轮尚未开始）。"""
    open_seq = SEQ[:-1]                                    # 0324 卖出之前：持仓未归零
    assert A.round_start_day(open_seq) == "20260306"
    assert A.round_first_price(open_seq) == pytest.approx(38.581)
    assert A.round_start_idx(open_seq) == 0
    assert A.round_start_day(SEQ) == "20260306", "默认关 ⇒ 逐字旧行为"
    monkeypatch.setenv("WOLF_ADDON_ROUND_FIX", "1")
    assert A.round_start_day(SEQ) is None
    assert A.round_first_price(SEQ) is None


def test_四笔追高加仓全部被拦():
    for day, vol, px in (("20260311", 200, 42.138), ("20260312", 300, 42.477),
                         ("20260316", 200, 42.388), ("20260317", 200, 41.678)):
        ok, why = _decide_upto(day, vol, px)
        assert not ok, (day, why)
        assert "加仓口径" in why


def test_低位做T买回放行():
    ok, why = _decide_upto("20260311", 100, 39.50)          # ≤ 首笔 38.581×1.04
    assert ok, why


def test_新建仓首笔放行():
    ok, why = A.decide([], 500, "20260306", add_price=38.581)
    assert ok, why


def test_创新高次数上限(monkeypatch):
    monkeypatch.setenv("WOLF_ADDON_MAX_PREMIUM_PCT", "0")   # 关掉价格上限，单测次数上限
    seq = [("20260306", "买入", 500, 38.0), ("20260307", "买入", 200, 38.5)]   # 第 2 次创新高
    ok, why = A.decide(seq, 300, "20260308", add_price=39.0)
    assert not ok and "两批" in why or "已达" in why


def test_建仓时窗上限(monkeypatch):
    monkeypatch.setenv("WOLF_ADDON_MAX_PREMIUM_PCT", "0")
    monkeypatch.setenv("WOLF_ADDON_MAX_BUYS", "9")
    seq = [("20260306", "买入", 500, 38.0)]
    ok, why = A.decide(seq, 200, "20260320", add_price=38.5)   # 隔 14 自然日
    assert not ok and "自然日" in why


def test_开关默认关_不查库也返回放行(monkeypatch):
    monkeypatch.setenv("WOLF_ADDON_MAX_PREMIUM_PCT", "0")
    ok, why = A.check("draby26", "SH603773", 200, "20260311", add_price=99.0)
    assert ok and why == "", "库内默认必须零行为变化"
    assert not A.enabled()


def test_开关打开才生效(monkeypatch):
    monkeypatch.setenv("WOLF_ADDON_CAP", "1")
    assert A.enabled()

# ────────────────────────────────────────────────────────────────
# 2026-09-21 修复：序列末尾「已归零」时 round_start_idx 返回 len(seq)（本轮尚未开始）
# 旧实现返回 0（整段历史当本轮）⇒ 拿**已结束轮次**的峰值/首笔价当本轮基准
# 实测两例（同一天，drabj13 2026-02-27）：SZ002552 买回被旧轮首笔价拦、SZ000555 被判「做T买回」
# ────────────────────────────────────────────────────────────────

# SZ000555 到 2026-02-27 09:50 之前的真实流水（末尾 02-25 卖到 0 ⇒ 已归零）
FLAT_555 = [("20260204", "买入", 2300, 17.620), ("20260205", "买入", 500, 17.240),
            ("20260205", "卖出", 1100, 17.390), ("20260205", "卖出", 600, 17.410),
            ("20260205", "卖出", 600, 17.520), ("20260206", "卖出", 300, 17.740),
            ("20260206", "买入", 500, 17.770), ("20260206", "卖出", 200, 17.770),
            ("20260209", "卖出", 100, 17.850), ("20260209", "买入", 500, 18.230),
            ("20260209", "卖出", 100, 18.100), ("20260210", "卖出", 400, 18.400),
            ("20260212", "卖出", 100, 18.240), ("20260212", "买入", 500, 18.320),
            ("20260213", "卖出", 400, 18.390), ("20260224", "买入", 500, 17.660),
            ("20260225", "卖出", 900, 17.880)]


def test_开关默认关_保留旧行为(monkeypatch):
    """`WOLF_ADDON_ROUND_FIX` 未设（库内默认关）⇒ 逐字保留旧行为：末尾归零仍返回 0。"""
    assert not A.round_fix_on()
    assert A.round_start_idx(FLAT_555) == 0
    assert A.round_start_idx_raw(FLAT_555) == len(FLAT_555), "修后语义始终可由 *_raw 取到"
    seq_552 = [("20260204", "买入", 400, 19.920), ("20260227", "卖出", 400, 20.960)]
    ok, why = A.decide(seq_552, 400, "20260227", add_price=21.130)
    assert not ok and "首笔买入价" in why, "默认关 ⇒ 旧行为（用已结束轮次的首笔价拦）"


def test_归零后本轮起点为空_不再引用旧轮(monkeypatch):
    monkeypatch.setenv("WOLF_ADDON_ROUND_FIX", "1")
    assert A.round_start_idx(FLAT_555) == len(FLAT_555), "末尾归零 ⇒ 本轮尚未开始（空窗口）"
    assert A.round_start_day(FLAT_555) is None
    assert A.round_first_price(FLAT_555) is None


def test_归零后买回不再被旧轮首笔价拦(monkeypatch):
    """02-27 14:00 SZ002552 实况形状：旧轮首笔 19.92，买回 21.13（+6.1% > 4%）——旧实现拦、修后放行。"""
    monkeypatch.setenv("WOLF_ADDON_ROUND_FIX", "1")
    seq = [("20260204", "买入", 400, 19.920), ("20260227", "卖出", 400, 20.960)]
    ok, why = A.decide(seq, 400, "20260227", add_price=21.130)
    assert ok, why
    assert A.round_first_price(seq) is None, "已结束轮次的首笔价不得当作本轮基准"


def test_归零后新买大于旧轮峰值_不再按两批次数拦(monkeypatch):
    """旧轮峰值 700 / 创新高 2 次；归零后新买 800 股 —— 旧实现按「已达 2 次」拦，修后按新轮首笔放行。"""
    monkeypatch.setenv("WOLF_ADDON_MAX_PREMIUM_PCT", "0")
    monkeypatch.setenv("WOLF_ADDON_ROUND_FIX", "1")
    seq = [("20260306", "买入", 500, 38.0), ("20260307", "买入", 200, 38.5),
           ("20260308", "卖出", 700, 39.0)]
    assert A.round_start_idx(seq) == len(seq)
    ok, why = A.decide(seq, 800, "20260310", add_price=38.2)
    assert ok, why


def test_归零前的旧行为对照_证明缺陷真实存在(monkeypatch):
    """把 round_start_idx 还原成旧实现（返回 0），上面两例确实被拦 —— 缺陷回归护栏。"""
    monkeypatch.setenv("WOLF_ADDON_MAX_PREMIUM_PCT", "0")
    seq_flat = [("20260306", "买入", 500, 38.0), ("20260307", "买入", 200, 38.5),
                ("20260308", "卖出", 700, 39.0)]
    def old(seq):            # 旧行为：末尾归零 → 0
        i = A.round_start_idx_raw(seq)
        return 0 if i >= len(seq) else i
    _orig = A.round_start_idx
    A.round_start_idx = old
    try:
        ok, why = A.decide(seq_flat, 800, "20260310", add_price=38.2)
        assert not ok and "加仓口径" in why, why
    finally:
        A.round_start_idx = _orig
    monkeypatch.setenv("WOLF_ADDON_MAX_PREMIUM_PCT", "4.0")
    seq_552 = [("20260204", "买入", 400, 19.920), ("20260227", "卖出", 400, 20.960)]
    A.round_start_idx = old
    try:
        ok2, why2 = A.decide(seq_552, 400, "20260227", add_price=21.130)
        assert not ok2 and "首笔买入价" in why2, why2
    finally:
        A.round_start_idx = _orig


def test_未归零时本轮口径不变():
    """持仓未归零 ⇒ 起点仍是本轮第一笔；两批上限照旧生效（回归）。"""
    seq = [("20260306", "买入", 500, 38.0), ("20260307", "买入", 200, 38.5)]
    assert A.round_start_idx(seq) == 0
    assert A.round_start_day(seq) == "20260306"
    assert A.round_first_price(seq) == pytest.approx(38.0)
    ok, why = A.decide(seq, 300, "20260308", add_price=38.2)
    assert not ok and "加仓口径" in why, why


def test_重开一轮后仍受两批约束():
    """清仓后重开：第 1 笔放行；同轮第 3 笔（创新高）仍被两批上限拦。"""
    seq = [("20260204", "买入", 400, 20.0), ("20260205", "卖出", 400, 20.5),
           ("20260227", "买入", 400, 20.1), ("20260228", "买入", 300, 20.2)]
    assert A.round_start_idx(seq) == 2
    ok1, _ = A.decide(seq, 300, "20260302", add_price=20.3)
    assert not ok1, "同轮第 3 次创新高买入必须被拦"
