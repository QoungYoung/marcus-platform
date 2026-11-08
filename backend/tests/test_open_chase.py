# -*- coding: utf-8 -*-
"""D1「开盘快速拉升不追」单测（2026-09-21 用户拍板："把 D1 补上"）。

语料 2025-04-15 条件6：「如果**当日开盘高开快速拉升**，或者**低开快速拉升想追进去的**，
  **在下午2.00-2.30这个时间段进行回补**…」＋条件2 后半句「尽量避免开盘直接买卖」、
  2026-01-12「千万绝对不要开盘买」。

为什么另起一条（而不是复用 `verdict`）：`verdict` 的参照物是**建议买价**，
  而 `wolf_zheng_t_buy` 的触发价 = 现价 ⇒ premium 恒 0 ⇒ 天然豁免
  （实测 2026-01-12 苏州科达距开盘 +3.9% / 距昨收 +6.4% 照买）。D1 把参照物换成**当日开盘价**。

影响面（`.dsh-tmp/d1d2_quant2.py`，t1–t5 共 143 笔买成交、FIFO 已实现盈亏）：
  开盘段 0935–0945 共 42 笔；≥开盘+3% 的 5 笔净 **−1,398**（其余 37 笔 +18,924）⇒ 拦对且影响面 3.5%。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "backend",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import wolf_no_chase as N  # noqa: E402


@pytest.fixture(autouse=True)
def _oc(monkeypatch):
    monkeypatch.setattr(N, "OPEN_ON", True)
    monkeypatch.setattr(N, "OPEN_WINDOW", "0930-0945")
    monkeypatch.setattr(N, "OPEN_RISE_PCT", 3.0)
    monkeypatch.setattr(N, "OPEN_KINDS", "*")
    monkeypatch.setattr(N, "PM_WINDOW", "1400-1430")


def test_库内默认关():
    """`WOLF_OPEN_CHASE` 默认 0 ⇒ 生产逐位不变（用户硬规则：所有改动开关默认关）。"""
    import importlib
    import os
    old = os.environ.pop("WOLF_OPEN_CHASE", None)
    try:
        m = importlib.reload(N)
        assert m.open_enabled() is False
        ok, why = m.open_verdict(12.16, 11.70, hhmm="0935")
        assert ok is True and "关闭" in why
    finally:
        if old is not None:
            os.environ["WOLF_OPEN_CHASE"] = old
        importlib.reload(N)


def test_开盘段内冲高_拦():
    """距当日开盘 +3.93% ≥ 3% ⇒ 拦（苏州科达 0112 09:35 的那一笔就是这种形态）。"""
    ok, why = N.open_verdict(12.16, 11.70, hhmm="0935", kind="wolf_zheng_t_buy")
    assert ok is False
    assert "开盘快速拉升不追" in why
    assert "高开快速拉升" in why and "低开快速拉升" in why      # 条件6 原文
    assert "+3.93%" in why


def test_开盘段内没冲高_放行():
    """低吸腿在开盘段照买：距开盘 +0.85% < 3% ⇒ 放行（D1 只拦"快速拉升"，不拦开盘低吸）。"""
    ok, why = N.open_verdict(11.80, 11.70, hhmm="0935", kind="custom_prevlow")
    assert ok is True and "不是快速拉升" in why


def test_非开盘段_放行():
    """10:35 的买腿不归 D1 管（那条由 D2 时间窗管，两闸分工不重叠）。"""
    ok, why = N.open_verdict(12.16, 11.70, hhmm="1035")
    assert ok is True and "不在开盘段" in why


def test_他给的追高窗口内_放行():
    """若把开盘段配到 14:00–14:30（他给的回补窗口），则按条件6 放行——不与自己打架。"""
    import importlib
    import os
    for k, v in (("WOLF_OPEN_CHASE", "1"), ("WOLF_OC_WINDOW", "1400-1430")):
        os.environ[k] = v
    try:
        m = importlib.reload(N)
        ok, why = m.open_verdict(12.16, 11.70, hhmm="1405")
        assert ok is True and "追高窗口" in why
    finally:
        for k in ("WOLF_OPEN_CHASE", "WOLF_OC_WINDOW"):
            os.environ.pop(k, None)
        importlib.reload(N)


def test_卖腿不适用():
    ok, why = N.open_verdict(12.16, 11.70, hhmm="0935", side="sell")
    assert ok is True and "卖腿不适用" in why


def test_缺数据fail_open():
    """取不到当日开盘价 ⇒ 放行（绝不因缺数据误拦）。"""
    assert N.open_verdict(12.16, 0, hhmm="0935")[0] is True
    assert N.open_verdict(12.16, None, hhmm="0935")[0] is True
    assert N.open_verdict(0, 11.70, hhmm="0935")[0] is True


def test_腿型白名单():
    """`WOLF_OC_KINDS=*` ⇒ 所有买腿；写白名单时只覆盖列出的腿型。"""
    import importlib
    import os
    assert N.oc_applies("wolf_zheng_t_buy") and N.oc_applies("custom_m5dump") and N.oc_applies(None)
    os.environ["WOLF_OPEN_CHASE"] = "1"
    os.environ["WOLF_OC_KINDS"] = "low_buy,custom_prevlow"
    try:
        m = importlib.reload(N)
        assert m.oc_applies("custom_prevlow") is True
        assert m.oc_applies("wolf_zheng_t_buy") is False
        ok, why = m.open_verdict(12.16, 11.70, hhmm="0935", kind="wolf_zheng_t_buy")
        assert ok is True and "不在 WOLF_OC_KINDS" in why
        assert m.open_verdict(12.16, 11.70, hhmm="0935", kind="custom_prevlow")[0] is False
    finally:
        for k in ("WOLF_OPEN_CHASE", "WOLF_OC_KINDS"):
            os.environ.pop(k, None)
        importlib.reload(N)


def test_计数可观测():
    """拦/放/缺数据都要计数 —— "开关开了没效果"是本项目反复踩的坑，必须能自证。"""
    n0 = N.stats()["oc_block"]
    N.open_verdict(12.16, 11.70, hhmm="0935")
    assert N.stats()["oc_block"] == n0 + 1


# ─────────────────────── 接线（源码级；防"开关开了没效果"） ───────────────────────
# 本项目反复踩的坑：闸门写好了，但**接的不是会成交的那条腿/那个口**（台账 §续4/§L）。
# 这里用源码级断言把三处接线钉死。
def _src(rel):
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_接线_网关执行口有D1D2():
    s = _src("backend/app/services/t_gateway.py")
    assert 'level": "TRADE_WINDOW"' in s, "D2 时间窗必须在**执行口**兜底（条件腿之外的 wolf_zheng_t_buy 只走这里）"
    assert 'level": "OPEN_CHASE"' in s, "D1 必须在执行口判"
    assert "_nco.open_verdict" in s and "fields" in s, "D1 取值要同时认 wolf 腿与条件腿两种 snapshot 形状"


def test_接线_正T买腿两条路径都判():
    s = _src("backend/app/services/t_monitor.py")
    assert "_defer_tw" in s and "_defer_oc" in s, "wolf_zheng_t_buy 那条路径（_check_wolf_t_rules）必须有这两个 defer"
    assert "not _defer_tw and not _defer_oc" in s, "defer 必须真的进'发不发腿'的判据链，否则等于没接"
    assert '"open": _op' in s, "wolf 腿 snapshot 必须写当日开盘价，否则执行口的 D1 静默 fail-open"


def test_接线_时间窗白名单可配且默认不含正T腿():
    from app.services import wolf_trade_window as T
    assert T.DEFAULT_KINDS == "low_buy,custom_prevlow", "库内默认白名单原样保留（口径可追溯）"
    assert T.applies_to("wolf_zheng_t_buy") is False, "默认下正T腿不受时间窗约束（要生效必须在 pins 里显式扩白名单）"
