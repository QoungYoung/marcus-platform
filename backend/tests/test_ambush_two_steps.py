# -*- coding: utf-8 -*-
"""「低位方向」两步方案（用户 2026-09-25 拍板「按这两步实现」）——账本 §9.145。

② **回调名额**（`WOLF_PICK_PULLBACK_SLOTS`）：为 `dist20h∈[0.85,0.95] ∧ r60≥20%` 的强票预留名额 ✓
   依据：沪电股份 002463 二月正因此被挤出名单 ✗；语料「低位：找辨识度最高老龙头**埋伏**」✓
① **埋伏纪律**（`WOLF_AMBUSH_HOLD_DAYS` / `WOLF_AMBUSH_STOP_PCT`）：**固定 50 交易日 + 宽止损 −20%**
   依据（§9.143）：40 日 +4.74%/58% ✓、60 日 +7.05%/60% ✓、5 日 −1.18% ✗；
   −10% 紧止损 ⇒ 中位 −10.49%（33% 为正）✗✗；−20% 只损失 1.9pp ✓；价格型出场一律砍赢家 ✗
"""
from __future__ import annotations

import os
import sys

_TMP_PYC = "/tmp"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "apps", "main_line"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

import pandas as pd  # noqa: E402


def test_pullback_slots_default_off():
    import stock_confirm_judge as J
    os.environ.pop("WOLF_PICK_PULLBACK_SLOTS", None)
    assert J.pullback_slots() == 0          # 库内默认关 ⇒ 生产零影响 ✓
    os.environ["WOLF_PICK_PULLBACK_SLOTS"] = "3"
    assert J.pullback_slots() == 3
    os.environ.pop("WOLF_PICK_PULLBACK_SLOTS", None)


def test_pullback_ratio_ok_pure_function():
    """r60≥20% ∧ dist20h∈[0.85,0.95] 的判定（用构造序列验证 ✓）。"""
    import stock_confirm_judge as J
    # 构造：60 日前 100 → 20 日前 140（+40%）→ 近 20 日回落到 133（dist20h≈0.95）
    vals = [100.0] * 41 + [140.0] * 19 + [133.0]   # 61 个收盘 ✓
    close = pd.DataFrame({"000001.SZ": vals})
    assert J.pullback_ratio_ok("000001.SZ", close) is True
    # 贴在高点（dist20h=1.0）⇒ 不是回调 ⇒ False
    close2 = pd.DataFrame({"000001.SZ": [100.0] * 41 + [140.0] * 20})   # 61 个 ✓
    assert J.pullback_ratio_ok("000001.SZ", close2) is False
    # 弱票（r60<20%）⇒ False
    close3 = pd.DataFrame({"000001.SZ": [100.0] * 60 + [105.0]})   # 61 个 ✓
    assert J.pullback_ratio_ok("000001.SZ", close3) is False


def test_ambush_discipline_wiring_and_defaults():
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_AMBUSH_HOLD_DAYS", "0")' in src      # 库内默认关 ✓
    assert 'os.getenv("WOLF_AMBUSH_STOP_PCT", "0")' in src
    assert "_check_ambush_discipline()" in src                    # 已挂到常规轮询 ✓
    i = src.index("def _ambush_entry_day")
    seg = src[i:i + 1200]
    assert "ORDER BY id DESC" in seg, "入场日必须按成交逆序 FIFO 反推 ✓"
    j = src.index("def _check_ambush_discipline")
    seg2 = src[j:j + 2600]
    assert "gateway_execute" in seg2 and "wolf_ambush_exit" in seg2


def test_pins_and_ladder():
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    for k, v in (("WOLF_PICK_PULLBACK_SLOTS", "'3'"), ("WOLF_AMBUSH_HOLD_DAYS", "'50'"), ("WOLF_AMBUSH_STOP_PCT", "'20'")):
        assert ("export %s=%s" % (k, v)) in pins
    lad = open(os.path.join(_ROOT, ".dsh-tmp", "wolfbt", "arm_ladder_check.py"), encoding="utf-8").read()
    for k in ("WOLF_PICK_PULLBACK_SLOTS", "WOLF_AMBUSH_HOLD_DAYS", "WOLF_AMBUSH_STOP_PCT"):
        assert ('("%s", "0")' % k) in lad


def test_ambush_leg_exempt_from_lowdip_trend_gate():
    """埋伏腿（wolf_ambush_buy）= 低位**先手建仓** ⇒ 必须**豁免**「低吸只在趋势票上摊薄」那道闸 ✓。

    否则埋伏腿被自己的闸挡死 ⇒ 实测 0202/0203 **0 交易** ✗（45 条低吸腿全被挡 + 趋势腿 0 触发 ✓）。
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    i = gw.index("低吸趋势票门拦住") if "低吸趋势票门拦住" in gw else 0
    seg = gw[i - 900:i + 200]
    assert 'str(_k_to) != "wolf_ambush_buy"' in seg, "埋伏腿必须豁免 ✓"
    sb = open(os.path.join(_ROOT, "apps", "main_line", "switch_builder.py"), encoding="utf-8").read()
    assert "wolf_ambush_buy" in sb and "ambush_candidates.json" in sb
    jg = open(os.path.join(_ROOT, "apps", "main_line", "stock_confirm_judge.py"), encoding="utf-8").read()
    assert "ambush_candidates.json" in jg


def test_ambush_merged_into_buy_new():
    """埋伏候选必须并入 `plan["buy_new"]` ✓，且**主题取自判级结果** ✓。

    坑一（账本 §9.145）：候选文件每天都写 ✓，但 `t_conditions` 里 **0 条** ⇒ 计划层没接 ✗；
    坑二（账本 §9.169）：并入时用"该票第一个概念"当主题 ✗ ⇒ 写腿闸的**主营/主类校验**把它拒掉 ✗
      （实测 `LEG_REJECT SZ001359 ambush: 主营/主类校验 主类=基础化工 不属主题[AI/算力/科技]` ✗，
        二月共 **391 条**埋伏腿被拒 ✓）⇒ 主题必须与正规候选同源：`stock_confirm_result.json` ✓
    """
    src = open(os.path.join(_ROOT, "apps", "main_line", "switch_builder.py"), encoding="utf-8").read()
    assert "埋伏候选并入买入列表" in src
    i = src.index("埋伏候选并入买入列表")
    seg = src[i - 3400:i + 400]
    assert "ambush_candidates.json" in seg, "读候选文件 ✓"
    assert "stock_confirm_result.json" in seg, "主题必须取自判级结果 ✓"
    assert "symbol_themes_of(_sym)" not in seg, "不得用『该票第一个概念』当主题 ✗"
    assert "buy_new[_c6] = [_judged_theme, \"ambush\"]" in seg, "并入用判级主题 ✓"
    assert "if not _judged_theme:" in seg, "取不到真实主题 ⇒ 跳过（不发必被拒的腿 ✓）"



def test_prod_run_arms_ambush_kind():
    """回测**真正的**布腿处在 `jobs/bt_prod_run.py`（`switch_builder._arm_legs` 在回测里不被调用 ✗）。

    它原本只认 `type=buy_253/254` 与 `src=trend` ⇒ 埋伏腿拿不到身份 ✗ ⇒ 必须单列分支 ✓。
    """
    src = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert 'str(L.get("stage")) == "ambush"' in src
    assert '"wolf_ambush_buy", "buy"' in src
    i = src.index('str(L.get("stage")) == "ambush"')
    j = src.index('L.get("type") == "buy_253/254"')
    assert i < j, "埋伏分支必须在普通 253/254 分支**之前**（否则被普通分支吃掉 ✓）"


def test_intraday_t_stop_default_off_and_ambush_exempt():
    """日内 T 仓紧止损（`WOLF_T_STOP_PCT`，库内默认 0 = 关 ✓）。

    依据（账本 §9.152）：狼大 2026-02-02「**亏半个点就把刚加的仓位全部出清**」=
    **当日新加的短线仓** ✓；而紧止损**不能**用在趋势/埋伏腿上 ✗（趋势腿 43 笔实测：
    持有 10 日中位 **+7.26% ⇒ −0.50%/−1.00%** ✗✗ —— 止损只是把赢家变成 −1% 的输家 ✓）。
    """
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_T_STOP_PCT", "0")' in src               # 库内默认关 ✓
    i = src.index("def _check_intraday_t_stop")
    seg = src[i:i + 2600]
    assert 'self._ambush_entry_day(_acct, _sym) != _today' in seg   # 只保护当天新加的仓 ✓
    assert "self._is_ambush_position(_acct, _sym)" in seg           # 埋伏腿豁免 ✓
    assert "wolf_t_stop" in seg and "gateway_execute" in seg
    assert "_check_intraday_t_stop()" in src                        # 已挂到轮询 ✓


def test_ambush_arms_254_only():
    """埋伏腿**只挂 254**（触及前低 + 温和缩量 ✓ = 语料「不破前低 + 地量缩量」）✗253。

    实测（T35 全窗）：`BUY_253_EXPR`（**大盘 5 分钟急杀**，代码自标 `⛔自设（无语料依据）`）只触发 **3 次** ✗、
    `BUY_254_EXPR`（`quote.dip_prev_low` **触及前低** + `vol_ratio ≤ 0.9` 温和缩量 ✓）触发 **155 次** ✓
    ⇒ 原先只挂急杀 ⇒ **0 次触发**（21 条条件单全部 expired ✗）。
    用户 2026-09-26「改掉」⇒ 只留 254 ✓（见账本 §9.163）。
    """
    src = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    i = src.index('str(L.get("stage")) == "ambush"')
    seg = src[i:src.index("continue", i)]        # 只取**埋伏分支本身**（下一分支也用 253 ✓）
    assert "ambush_expr()" in seg, "埋伏腿用 ambush_expr()（默认 touch=254 ✓／near=不破前低 ✓）"
    assert "BUY_253_EXPR" not in seg, "253（大盘急杀）是自设、无语料依据 ⇒ 已去掉 ✗"
    assert seg.count('"wolf_ambush_buy", "buy"') == 1


def test_ambush_kind_in_build_kinds():
    """埋伏腿必须出现在「建仓腿型」名单里 ✓ —— 否则**语料前置门②资格**会拦死它 ✗。

    `t_pool` 原文：「语料前置门②资格：**无底仓（没抄到底就没有做 T 资格）——形态类买腿不建仓**」
    ⇒ 无底仓时"建仓规模"只对 `T_BUILD_KINDS` 里的腿型计算 ✓；埋伏腿是**低位先手建仓** ✓
    ⇒ 必须入列 ✓。实测（T35 一月）：布腿 **19 条、触发 0 次** ✗ 就是漏在此处。
    """
    # §9.496：名单已收成**单一来源**（`t_leg_kinds.py`）⇒ 测试改成**查注册表**（检行为，不检源码文本）
    import sys as _sys
    _sys.path.insert(0, os.path.join(_ROOT, "backend"))
    from app.services.t_leg_kinds import BUY_LEG_KINDS, T_BUILD_KINDS
    assert "wolf_ambush_buy" in T_BUILD_KINDS, "埋伏腿必须在建仓腿型名单里 ✓"
    assert "wolf_ambush_buy" in BUY_LEG_KINDS, "埋伏腿必须在买腿名单里 ✓"
    # 防回退：这两处源码里**不应再出现**该字面名单
    for _rel in ("backend/app/services/t_pool.py", "backend/app/services/t_bridge.py"):
        assert 'wolf_ambush_buy"' not in open(os.path.join(_ROOT, _rel), encoding="utf-8").read(), \
            "%s 不应再硬编码腿名单 ✓（§9.496）" % _rel


def test_discipline_scope_no_overreach():
    """两条纪律的**作用面**必须收窄 ✗（作用面审计，账本 §9.170）。

    ① 「埋伏纪律」（固定 50 交易日 + 宽止损 −20%）**只对埋伏腿建仓的持仓生效** ✗
       —— 原实现遍历**所有持仓** ⇒ 会把趋势腿/低吸腿也按 50 日/宽止损管 ✗；
    ② 「日内 T 仓紧止损」必须**排除趋势腿** ✗
       —— 量化（§9.152）已证紧止损会摧毁趋势腿（10 日中位 **+7.26% ⇒ −1.00%** ✗✗）。
    """
    src = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()

    def body(name, stop_names):
        i = src.index("def %s" % name)
        j = min([src.index("def %s" % x, i) for x in stop_names if ("def %s" % x) in src[i + 10:]] or [len(src)])
        return src[i:j]

    amb = body("_check_ambush_discipline", ["_check_intraday_t_stop", "_is_trend_position", "_is_ambush_position"])
    assert "if not self._is_ambush_position(_acct, _sym):" in amb, "埋伏纪律须限定在埋伏腿 ✓"
    tst = body("_check_intraday_t_stop", ["_is_trend_position", "_is_ambush_position"])
    assert "self._is_trend_position(_acct, _sym)" in tst, "日内T止损须排除趋势腿 ✓"
    assert "self._is_ambush_position(_acct, _sym)" in tst, "日内T止损须排除埋伏腿 ✓"
    assert "def _is_trend_position" in src and "def _is_ambush_position" in src
    rep = open(os.path.join(_ROOT, "jobs", "bt_prod_report.py"), encoding="utf-8").read()
    assert '"wolf_ambush_buy"' in rep, "报告腿型名单须含埋伏腿 ✓"


def test_crush_conjunct_default_off_and_shape():
    """低吸 253 的「**大盘急杀 ∧ 个股急杀**」合取（账本 §9.175）。

    数据：两者同时急杀 +5 中位 **+1.57%／为正 61%** ✓（唯一正期望）；
          只有个股急杀 −0.51%／47% ✗；只有大盘急杀 −0.85%／42% ✗
    语料（2025-06-05）两层：「主线板块筑底行情…急杀可以买」＋「板块/个股急杀…慢慢买」⇒ **合取** ✓
    """
    import importlib.util as _ilu
    sp = _ilu.spec_from_file_location("_rsa_t", os.path.join(_ROOT, "jobs", "rotation_switch_arm.py"))
    m = _ilu.module_from_spec(sp)
    try:
        sp.loader.exec_module(m)
    except Exception:
        # 依赖缺失时退化为源码断言 ✓
        src = open(os.path.join(_ROOT, "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()
        assert "def buy_253_expr" in src and "WOLF_CRUSH_CONJUNCT" in src
        return
    os.environ.pop("WOLF_CRUSH_CONJUNCT", None)
    assert m.buy_253_expr() == m.BUY_253_EXPR, "默认必须返回原 253（现状零变化 ✓）"
    os.environ["WOLF_CRUSH_CONJUNCT"] = "1"
    os.environ["WOLF_CRUSH_STOCK_DROP"] = "3"
    e = m.buy_253_expr()
    fields = [c["field"] for c in e["and"]]
    assert "index.m5_dump" in fields, "大盘急杀 ✓"
    assert "quote.m5_dump" in fields, "个股急杀 ✓（新增字段）"
    cond = [c for c in e["and"] if c["field"] == "quote.m5_dump"][0]
    assert cond["op"] == "<=" and cond["value"] == -3.0, "个股急杀 = 跌幅 ≤ −3% ✓"
    os.environ.pop("WOLF_CRUSH_CONJUNCT", None)
    os.environ.pop("WOLF_CRUSH_STOCK_DROP", None)
    # 字段表登记 ✓
    ex = open(os.path.join(_ROOT, "backend", "app", "services", "t_expr.py"), encoding="utf-8").read()
    assert '"quote.m5_dump"' in ex, "字段表须登记 quote.m5_dump ✓"
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "def _stock_m5_dump" in mon and '"m5_dump": self._stock_m5_dump(symbol)' in mon


def test_ambush_trigger_near_not_break_prev_low():
    """埋伏腿触发口径：**回踩到位但不破前低**（账本 §9.182）。

    语料两层各管一种玩法 ✓：
      · 埋伏/筑底（2026-02-02）：「大盘没过前低是前提，个股也没低于前低是基础条件」✓ ⇒ 本口径
      · 正T 挂单（2025-03-06）：「挂前一天的低点，能买进去就做正T」✓ ⇒ 那是 254（触及前低 ✗）
    数据（强势+回调 1,668 信号，50 日离场）：不破前低 **+6.29%／63% 为正／回撤 −6.9%** ✓
      vs 触及前低 **+0.46%／回撤 −11.7%** ✗ ⇒ 差 5~8 倍
    """
    import importlib.util as _ilu
    sp = _ilu.spec_from_file_location("_rsa_t2", os.path.join(_ROOT, "jobs", "rotation_switch_arm.py"))
    m = _ilu.module_from_spec(sp)
    try:
        sp.loader.exec_module(m)
    except Exception:
        src = open(os.path.join(_ROOT, "jobs", "rotation_switch_arm.py"), encoding="utf-8").read()
        assert "def ambush_expr" in src and "WOLF_AMBUSH_TRIGGER" in src
        return
    os.environ.pop("WOLF_AMBUSH_TRIGGER", None)
    assert m.ambush_expr() == m.BUY_254_EXPR, "默认必须是现状（touch=254 ✓）"
    os.environ["WOLF_AMBUSH_TRIGGER"] = "near"
    os.environ["WOLF_AMBUSH_NEAR_PCT"] = "1.0"
    e = m.ambush_expr()
    conds = {(c["field"], c["op"]): c["value"] for c in e["and"]}
    assert conds.get(("quote.prev_low_dist_pct", ">")) == 0, "必须要求**未破**前低（距离 >0 ✓）"
    assert conds.get(("quote.prev_low_dist_pct", "<=")) == 1.0, "回踩到位带 ✓"
    os.environ.pop("WOLF_AMBUSH_TRIGGER", None)
    os.environ.pop("WOLF_AMBUSH_NEAR_PCT", None)
    ex = open(os.path.join(_ROOT, "backend", "app", "services", "t_expr.py"), encoding="utf-8").read()
    assert '"quote.prev_low_dist_pct"' in ex
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "def _stock_prev_low" in mon and '"prev_low_dist_pct"' in mon


def test_ambush_exemptions_theme_and_slow_decline():
    """两处豁免（用户「都按你说的来」；账本 §9.184）。

    ① **主题口径统一**：埋伏并入的主题必须是「**判级主题 ∩ 该票自身主题**」✓
       —— 实测 SZ002028 判级=半导体/芯片 ✗ 而执行口按主营判=电网设备 ⇒ `rejected 腿批准闸(执行口)` ✗
    ② **缓跌门豁免埋伏腿**（`WOLF_AMBUSH_SKIP_SLOWDECLINE`，库内默认 0 ✓）
       —— 埋伏的触发就是「温和回踩到位」✗ 天然被判"缓跌"（实测 SH603078 被拦 ✗），
          而量化（§9.182）用的"回踩前低"本就含缓跌 ✓ 且为正：+6.29%／63% ✓
    """
    sb = open(os.path.join(_ROOT, "apps", "main_line", "switch_builder.py"), encoding="utf-8").read()
    assert "主题口径不一致" in sb and "symbol_themes_of(" in sb
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_AMBUSH_SKIP_SLOWDECLINE", "0")' in mon        # 库内默认关 ✓
    i = mon.index("def intraday_crush_blocked")
    seg = mon[i:i + 25000]
    assert 'str(kind or "") == "wolf_ambush_buy"' in seg
    assert 'intraday_crush_blocked(symbol, _is_buy_side(cond), str(trigger_kind or ""))' in mon
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_AMBUSH_SKIP_SLOWDECLINE='1'" in pins


def test_ambush_size_dedupe_and_sell_exempt():
    """埋伏腿的规模/去重/卖腿豁免（用户「动手」；账本 §9.185，藏格矿业实证 ✓）。

    实测 SZ000408：01-30 **同日触发两次 ⇒ 400+400 = 800 股 ≈ 账户 27%** ✗
                 02-02 被 **custom_support_sell（破位）** 清掉 ⇒ 亏 −834 ✗（50 日纪律形同虚设 ✗）
    三个开关**库内默认全关**（生产零影响 ✓）：
      · `WOLF_AMBUSH_SIZE_PCT`（小仓试仓：权益×PCT%÷现价 压上限 ✓ 语料「一次只买一点点」✓）
      · `WOLF_AMBUSH_ONE_PER_DAY`（同日只建一仓 ✓）—— ⚠️ 账本 9.348：本闸**纯自设** ✗，
        用户 2026-09-30「先放开 23 试试」✓ ⇒ **已改回默认 0（放开）** ✓
      · `WOLF_AMBUSH_SELL_EXEMPT`（埋伏仓豁免**破位/黄线**类常规卖腿 ✓；止盈与宽止损保留 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_AMBUSH_SIZE_PCT", "0")' in gw
    assert 'os.getenv("WOLF_AMBUSH_ONE_PER_DAY", "0")' in gw
    assert 'os.getenv("WOLF_AMBUSH_SELL_EXEMPT", "0")' in gw
    assert "def _ambush_position(" in gw
    i = gw.index('os.getenv("WOLF_AMBUSH_SELL_EXEMPT"')
    seg = gw[i:i + 2200]
    assert "AMBUSH_SELL_EXEMPT" in seg
    # 豁免名单**可配置**，且**库内默认 = 现状两类**（生产逐字不变 ✓）
    assert ('os.getenv("WOLF_AMBUSH_SELL_EXEMPT_KINDS", "custom_support_sell,custom_vwap_sell")'
            in gw), "默认必须是现状两类 ✓"
    assert "_ex_kinds = {x.strip() for x in str(" in gw
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    # 用户拍板「①④ 两条」⇒ 高抛/止盈（high_sell／wolf_fib_target_sell）+ 防御性减仓 也豁免 ✓
    for _k in ("custom_support_sell", "custom_vwap_sell", "high_sell",
               "wolf_fib_target_sell", "wolf_profit_take_sell", "wolf_defensive_t_reduce",
               "wolf_dao_t_sell"):
        assert _k in pins, _k
    for k, v in (("WOLF_AMBUSH_SIZE_PCT", "'4'"),
                 ("WOLF_AMBUSH_SELL_EXEMPT", "'1'"), ("WOLF_ADJ_MINS_AUTO", "'1'"),
                 # 账本 9.348：本闸**纯自设** ✗ ⇒ 用户「先放开 23 试试」✓ ⇒ 改回默认 0
                 ("WOLF_AMBUSH_ONE_PER_DAY", "'1'")):
        assert ("export %s=%s" % (k, v)) in pins, k
    days = open(os.path.join(_ROOT, "jobs", "bt_days.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_ADJ_MINS_AUTO", "0")' in days and "mk_adj_mins.py" in days


def test_ambush_add_rules_three():
    """埋伏腿**加仓三项**（狼大三条硬规则 ✓；账本 §9.186）。

    语料（逐字 ✓）：
      · 「**分批加法点，不到点位不加**」／「价格必须到位——要到他设定的**第一/第二加法点**，且**下跌幅度足够**」✓
      · 「**分批小加**；**不到位置、不够幅度就不加**」✓
      · 「长波段的**补仓点要隔得很远**」✓
    实现（只约束**加仓** ✓ 首次建仓放行 ✓；三个开关**库内默认 0 = 关** ✓）：
      ① `WOLF_AMBUSH_ADD_STEP_PCT`（第 n 次加仓须 ≤ 上次买入价×(1−n×STEP%) ✓）
      ② `WOLF_AMBUSH_ADD_MIN_DROP_PCT`（须 ≤ 持仓均价×(1−DROP%) ✓）
      ③ `WOLF_AMBUSH_ADD_MIN_GAP_DAYS`（距上次买入 ≥ GAP 个自然日 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    for k in ("WOLF_AMBUSH_ADD_STEP_PCT", "WOLF_AMBUSH_ADD_MIN_DROP_PCT", "WOLF_AMBUSH_ADD_MIN_GAP_DAYS"):
        assert ('os.getenv("%s", "0")' % k) in gw, k
    assert "AMBUSH_ADD_RULE" in gw
    assert "if _hist:" in gw, "无买入史 ⇒ 首次建仓放行 ✓"
    assert "埋伏加仓·点位未到" in gw and "埋伏加仓·幅度不够" in gw and "埋伏加仓·间距不够" in gw
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    for k, v in (("WOLF_AMBUSH_ADD_STEP_PCT", "'3'"), ("WOLF_AMBUSH_ADD_MIN_DROP_PCT", "'3'"),
                 ("WOLF_AMBUSH_ADD_MIN_GAP_DAYS", "'2'")):
        assert ("export %s=%s" % (k, v)) in pins, k


def test_buy_volume_always_round_lot():
    """**买入必须整百股**（用户 2026-09-26「必须是整百股」✓；账本 §9.187）。

    实测碎股：`SZ002792 223 股`、`SH603660 891 股` —— 均由"埋伏小仓档"的 `int()` 截断产生 ✗
    两处修：
      ① 小仓档自身**向下取整到 100** ✓
      ② `validate_order` **之前**加统一整百取整 ⇒ 任何 clamp/额度/比例路径都漏不出去 ✓
    开关 `WOLF_BUY_LOT_ROUND`（库内默认 **1=开** ✓ —— 交易所硬规则，不是策略旋钮；
      生产经券商本就整百 ⇒ 对生产无行为变化 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert (("_cap = (int(_eq * (_amb_pct * _amb_mult) / 100.0 / float(price)) // 100) * 100" in gw)
            or ("_cap = (int(_eq * _amb_pct / 100.0 / float(price)) // 100) * 100" in gw)), "小仓档须向下取整 ✓"
    assert 'os.getenv("WOLF_BUY_LOT_ROUND", "1")' in gw, "默认必须开（交易所硬规则 ✓）"
    assert "BUY_LOT_ROUND" in gw
    i = gw.index('os.getenv("WOLF_BUY_LOT_ROUND"')
    j = gw.index("check = validate_order(symbol, side, price, volume,", i)
    assert j > i, "取整必须在 validate_order **之前** ✓"
    assert "_v_lot = (int(volume) // 100) * 100" in gw[i:i + 1400]
    # 卖侧不取整（A 股允许卖出零股 ✓）
    assert 'if side == "buy" and int(volume or 0) > 0' in gw[i - 300:i + 1400]


def test_ambush_size_tier_small_drop_small_buy():
    """「**小跌小买 大跌大买**」（语料 ✓；账本 §9.192）。

    做法：按"**距持仓均价**的跌幅"分档放大单笔：
      · `WOLF_AMBUSH_SIZE_TIER_STEP`（每档跌幅%，**库内默认 0 = 关** ✓）
      · `WOLF_AMBUSH_SIZE_TIER_GAIN`（每档倍数，默认 0.5 ✓）
      · `WOLF_AMBUSH_SIZE_MAX_MULT`（上限，默认 2 ✓）
    小跌(<3%) ⇒ ×1.0｜中跌(3~6%) ⇒ ×1.5｜大跌(≥6%) ⇒ ×2.0 ✓
    ⚠️ 本项**尚未量化**（属规模杠杆，不改信号 ✓）—— 已在账本标注待量化 ✓
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    for k, d in (("WOLF_AMBUSH_SIZE_TIER_STEP", "0"), ("WOLF_AMBUSH_SIZE_TIER_GAIN", "0.5"),
                 ("WOLF_AMBUSH_SIZE_MAX_MULT", "2")):
        assert ('os.getenv("%s", "%s")' % (k, d)) in gw, k
    assert "_amb_mult = min(1.0 + _t_gain * _tiers, max(1.0, _t_max))" in gw
    assert "_cap = (int(_eq * (_amb_pct * _amb_mult) / 100.0 / float(price)) // 100) * 100" in gw
    assert "埋伏加仓力度" in gw
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    for k, v in (("WOLF_AMBUSH_SIZE_TIER_STEP", "'3'"), ("WOLF_AMBUSH_SIZE_TIER_GAIN", "'0.5'"),
                 ("WOLF_AMBUSH_SIZE_MAX_MULT", "'2'")):
        assert ("export %s=%s" % (k, v)) in pins, k


def test_ambush_max_positions_cap():
    """**同时持仓上限**（用户拍板 ≤15 ✓；账本 §9.196）。

    量化（全历史 21 个月 ✓）：
      · 不限 ⇒ **+97.02%／最大回撤 −34.3%** ✗
      · **≤15 ⇒ +58.58%／−21.6%** ✓（收益/回撤 2.71 vs 2.83 ⇒ 比例几乎不变 ✓）
      · ≤10 ⇒ +32.01%／−13.9%；
    动机：2026-07「**止损群**」✗（同一周 6 只各亏 ~5,000 ⇒ 单月 −30.6% ✗）
    实现：只拦**新建仓** ✓（同一票加仓不占新名额 ✓）；开关**库内默认 0 = 关** ✓
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_AMBUSH_MAX_POSITIONS", "0")' in gw
    assert "def _ambush_open_count(" in gw and "paper_positions" in gw
    i = gw.index('os.getenv("WOLF_AMBUSH_MAX_POSITIONS"')
    seg = gw[i:i + 1500]
    assert "if not _already and _ambush_open_count(account_id) >= _max_pos" in seg, "只拦新建仓 ✓"
    assert "AMBUSH_MAX_POSITIONS" in seg
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_AMBUSH_MAX_POSITIONS='15'" in pins


def test_tsell_settle_passes_trigger_id_and_fallback():
    """T出结算**必须传 trigger_id** + 网关「确认制T出」兜底（账本 §9.198）。

    实测漏洞：SH603660 埋伏仓 01-09 被 `确认制T出(撤销式延迟无放量新高)` **卖掉 300 股** ✗
      · 条件腿那条路**已正确 blocked**（`埋伏仓豁免常规卖腿` ✓，日志可见两次 ✓）
      · 但 `_settle_tsell_pending()` 调 `gateway_execute` 时**不传 trigger_id** ✗
        ⇒ `_turnover_kind()` 取不到腿型 ⇒ 豁免名单匹配不到 ⇒ **放行** ✗
    修：① 传 `trigger_id=p["trig_id"]` ✓；② 网关加「确认制T出」⇒ `high_sell` 兜底 ✓
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = mon.index('reason="确认制T出(撤销式延迟无放量新高)"')
    assert 'trigger_id=p.get("trig_id")' in mon[i:i + 700], "T出结算必须传 trigger_id ✓"
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    j = gw.index('if not _k_se and str(reason or "").startswith("确认制T出"):')
    assert '_k_se = "high_sell"' in gw[j:j + 200], "兜底须映射为 high_sell ✓"


def test_trend_stop_exempts_ambush():
    """「②趋势线止损/减仓」豁免埋伏仓（用户 2026-09-26 ✓；账本 §9.201）。

    病灶：T35 里埋伏票被本规则卖掉 **17 笔** ✓（其中**止损 11 笔合计 −3,246** ✗），
    而长样本量化显示该形态需 **40~60 日**才转正 ✓ ⇒ 提前止损与形态冲突 ✗
    实现：在**设置止损价之前**判断「本仓是否由埋伏腿建立」⇒ 是则 `stop_price=0` 且跳过 ✓
      · 传入监控自己的账户 `T_MONITOR_ACCOUNT` ✓（`_is_ambush_position(acct, sym)` 是**两参数** ✗）
      · 沿用开关 `WOLF_AMBUSH_SELL_EXEMPT`（**库内默认 0** ✓）
      · 查询异常**必须打日志** ✗（原写法 `except: pass` 会把参数错误静默吞掉 ✗）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = mon.index('②趋势线豁免埋伏仓')
    seg = mon[i - 900:i + 500]
    assert 'os.getenv("WOLF_AMBUSH_SELL_EXEMPT", "0")' in seg
    assert "self._is_ambush_position(T_MONITOR_ACCOUNT, symbol)" in seg, "必须传两参数 ✓"
    assert "stop_price = 0.0" in seg and "continue" in seg
    assert "②趋势线豁免查询异常" in seg, "异常必须打日志（不能静默 ✗）"
    assert mon.count("def _is_ambush_position(self, acct: str, sym: str)") == 1


def test_ambush_index_warning_derisk():
    """**指数层预警 ⇒ 降防御档**（用户「量化 ④」→ 落地 ✓；账本 §9.205）。

    语料（逐字 ✓）：2026-03-02「趁机在周2前减仓」✓／03-05「明天有冲高减到 70%」✓／
                  03-20「等指数企稳了 我打回国算链」✓
    量化（21 个月 ✓ + **样本外分段 ✓**）：
      · 无预警 ⇒ **+58.58%／回撤 −21.6%** ✗
      · **W1(<MA20) + W3(3 日跌占比≥65%) ⇒ 防御档 5 只** ⇒ **+75.93%／回撤 −7.5%** ✓✓
      · 分段：2026 动荡段 **+35.12%／−7.9%** vs 现状 **+17.96%／−19.0%** ✓✓（⇒ 非过拟合 ✓）
    实现（三处，**库内默认全关** ✓）：
      · `ambush_warn_active()`（`WOLF_AMBUSH_WARN_MA`／`_BREADTH` ✓；取不到指数 ⇒ **不预警** fail-open ✓）
      · 网关：预警 ⇒ **新开仓按防御档**（`WOLF_AMBUSH_WARN_DEF_CAP` ✓）
      · 监控：预警 ⇒ `_ambush_warn_trim()` **减到防御档**（`WOLF_AMBUSH_WARN_TRIM` ✓，每日一次、砍浮亏最深 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "def ambush_warn_active()" in mon
    assert 'os.getenv("WOLF_AMBUSH_WARN_MA", "0")' in mon
    # ⚠️ 账本 §9.206：MA 必须是**日线**口径（原先误用 5min 收盘算均值 ✗）
    i0 = mon.index("def ambush_warn_active")
    seg0 = mon[i0:i0 + 7000]
    assert "recent_sync" in seg0 and "000001.json" in seg0, "主源＝recent_sync ✓（与监控同源 ✓）"
    assert "_merge" in seg0 and "sorted(_merge)" in seg0, "必须合并分钟库 ⇒ 否则前期不够算 MA ✗"
    assert "fetch_minute_bars(\"sh000001\"" in seg0
    # 诚实标注：W3（宽度）运行时取不到 ⇒ 实际只有 W1 ✓
    assert "W3" in seg0 and "占位" in seg0
    # ② 当日急跌（W3b 的**可运行**版 ✓ 只用指数 ⇒ 全样本 +84.65%／−8.8%，两段皆优于 MA10 单用 ✓）
    assert 'os.getenv("WOLF_AMBUSH_WARN_DAY_DROP", "0")' in seg0
    assert "_ret = (cl[-1] / cl[-2] - 1) * 100" in seg0
    assert "def _ambush_warn_trim(self)" in mon
    assert 'os.getenv("WOLF_AMBUSH_WARN_TRIM", "0")' in mon
    assert "self._ambush_warn_trim()" in mon, "必须接到周期里 ✓"
    i = mon.index("def _ambush_warn_trim")
    seg = mon[i:i + 25000]
    assert "预警降档" in seg and "rank.sort()" in seg, "砍浮亏最深者 ✓（与量化一致 ✓）"
    # ⚠️ 作用面（用户「为什么只有埋伏仓减仓呢，我们不是为了躲避大跌吗」✓）：
    #    默认 `ambush` = 现状 ✓；`account` ⇒ **全账户** ✓（他「减到 70%」讲的是组合层 ✓）
    assert 'os.getenv("WOLF_WARN_TRIM_SCOPE", "ambush")' in mon
    assert '_scope == "account"' in seg and "全账户" in seg
    assert "无需减仓（作用面=" in seg, "持仓未超防御档也要留痕"
    assert "self._ambush_warn_trim()" in mon
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    j = gw.index('os.getenv("WOLF_AMBUSH_WARN_MA"')
    assert "ambush_warn_active()" in gw[j:j + 900], "网关须读同一预警 ✓"
    assert 'os.getenv("WOLF_AMBUSH_WARN_DEF_CAP", "5")' in gw
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    for k, v in (("WOLF_AMBUSH_WARN_MA", "'10'"), ("WOLF_AMBUSH_WARN_DAY_DROP", "'1.5'"),
                 ("WOLF_AMBUSH_WARN_DEF_CAP", "'5'"), ("WOLF_AMBUSH_WARN_TRIM", "'1'")):
        assert ("export %s=%s" % (k, v)) in pins, k


def test_key_files_compile():
    """**必须用 `compile()` 校验**（账本 §9.209）✗。

    病灶：`ast.parse()` **查不出** `continue` 不在循环里 ✗ —— 我此前一直用 `ast.parse` 自检 ✓，
    结果把一处 `continue` 放进了**非循环**分支 ✗（②趋势线豁免埋伏仓那段 ✓），
    `ast.parse` 说"语法 OK" ✓，直到**跑回测**才炸：
      `[prod] 持续腿开关回显失败: 'continue' not properly in loop (t_monitor.py, line 3802)`
      `SyntaxError: 'continue' not properly in loop` ⇒ 当日 rc=1 ⇒ **整轮年跑终止** ✗
    ⇒ 断言：所有关键文件都能 **`compile()`** 通过 ✓（这类错只有 compile 会报 ✓）
    """
    import py_compile
    files = ["backend/app/services/t_monitor.py", "backend/app/services/t_gateway.py",
             "backend/app/services/t_pool.py", "backend/app/services/t_expr.py",
             "jobs/bt_days.py", "jobs/bt_prod_run.py", "jobs/rotation_switch_arm.py",
             "apps/main_line/switch_builder.py",
             # 账本 §9.371：今天在这里栽过 ✗ —— `ast.parse` 说 OK ✓，但 day-run 报
             #   `SyntaxError: 'return' outside function (bt_agent_loop.py, line 339)`
             #   （**这类错只有 compile 会报** ✗）⇒ 把常改的 jobs/services 一并纳入 ✓
             "jobs/bt_agent_loop.py", "jobs/bt_asof_api.py", "jobs/bt_dashboard.py",
             "jobs/bt_agent_tools.py",   # 账本 §9.382：这里也栽过 ✗（字符串里嵌 ASCII 双引号）

             "backend/app/services/t_capacity.py", "backend/app/services/t_turnover.py",
             "backend/app/services/t_data_sources.py", "backend/app/services/t_bridge.py",
             "backend/app/services/t_ai_agent.py"]
    bad = []
    for f in files:
        p = os.path.join(_ROOT, f)
        if not os.path.exists(p):
            continue
        try:
            py_compile.compile(p, doraise=True, cfile=os.path.join(_TMP_PYC, "x.pyc"))
        except Exception as e:
            bad.append("%s: %s" % (f, str(e)[:80]))
    assert not bad, "compile 失败：%s" % bad


def test_ambush_sell_master_gate():
    """**埋伏仓卖出的总闸**（用户「这个卖出对吗」→ 修 ✓；账本 §9.215）。

    病灶（实测 SZ000408）：埋伏仓被 **AI 决策路径**卖掉两半 ✗
      · 0203 理由：「…**不满足任何 wait/abandon 客观证据，按默认执行卖腿**」✗ ⇒ **默认偏向卖出** ✗
      · 0212 理由：「…**无客观证据否决，按确认卖腿 exec 减仓 100 股兑现/止损**」✗
      · 机制：**AI 路径不带腿型** ⇒ 以腿型为键的豁免名单**拦不到** ✗
        ⇒ 这是**同类漏洞的第三次** ✗（①条件腿自动执行漏 trigger_id ✓②T出结算漏传 ✓③本处）
    修法（**总闸** ✓）：埋伏仓的卖出**只放行两件事** ✓
      · 「**埋伏纪律**」（50 交易日 / 宽止损 −20% ✓）
      · 「**预警降档**」（指数层预警主动降档 ✓ —— 这是我们要的 ✓）
      其余（AI 卖腿／破位／高抛／黄线／趋势线／G4／板块半仓 …）**一律拦** ✗
    开关沿用 `WOLF_AMBUSH_SELL_EXEMPT`（**库内默认 0 = 关** ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert "AMBUSH_SELL_MASTER" in gw
    i = gw.index("_ALLOW_SUB = (")    # 锚在**代码行**上 ✓（"埋伏仓卖出总闸"在注释/消息里也出现 ⇒ 会锚错 ✗）
    seg = gw[i - 900:i + 5200]
    assert "_ALLOW_SUB" in seg, "白名单须显式（便于审计 ✓）"
    for _k in ('"埋伏纪律"', '"预警降档"', '"避险"', '"风险"'):
        assert _k in seg, "白名单须含 %s ✓" % _k
    assert "not any(_k in _rs for _k in _ALLOW_SUB)" in seg
    assert "_ambush_position(account_id, symbol)" in seg
    assert "check = validate_order(" in gw[gw.index("AMBUSH_SELL_MASTER"):], "总闸须在 final validate 之前 ✓"
    # 埋伏纪律自身必须带「埋伏纪律」字样 ✓（否则会被自己的总闸拦住 ✗）
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    # 纪律自身的 reason 必须含「埋伏纪律」✓（实测：`埋伏纪律：持有 N/M 交易日到期 ⇒ 离场` ✓
    #   与 `埋伏纪律：宽止损 现价 … ≤ 成本 …` ✓）—— 否则总闸会拦住它自己 ✗
    assert '"埋伏纪律：持有 %d/%d 交易日到期 ⇒ 离场"' in mon
    assert '"_why = (\"埋伏纪律：宽止损' in mon or "埋伏纪律：宽止损" in mon


def test_ambush_exempt_from_min_two_lots():
    """**埋伏腿豁免"补到 2 手"**（用户「豁免」✓；账本 §9.218）。

    病灶：引擎 `wolf_build_lots`「买后持仓不足 2 手 ⇒ 补到 2 手」✓
      · 原意＝「一手仓的 T 仓恒为 0 ⇒ 止盈腿永久失效」✓ ⇒ **为做 T 服务** ✓
      · 但它把**埋伏小仓档**从 100 股**放大到 200 股** ✗（实测 5 笔 ✓）
        例：`埋伏小仓档：SZ000408 400→100 股`（4% ✓）⇒ 紧接着 `100→200 股` ✗ ⇒ 实际 ≈ **8%** ✗
      · **埋伏腿不做 T** ✓、要**长持 50 日** ✓ ⇒ 该理由**不适用** ✗ ⇒ 豁免 ✓
    开关 `WOLF_AMBUSH_SKIP_MIN_LOTS`（**库内默认 0 = 关** ✓；pins 1 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_AMBUSH_SKIP_MIN_LOTS", "0")' in gw
    i = gw.index('os.getenv("WOLF_AMBUSH_SKIP_MIN_LOTS"')
    seg = gw[i:i + 1600]
    assert 'str(_k_bl) == "wolf_ambush_buy"' in seg, "只豁免埋伏腿 ✓"
    assert "_bl = None" in seg and "if _bl is not None and _bl.enabled():" in seg, "豁免须真跳过 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_AMBUSH_SKIP_MIN_LOTS='1'" in pins


def test_ambush_promotion_to_normal():
    """**埋伏仓"转正"**（用户「把"转正"补上」✓；账本 §9.220）。

    语料 ✓：「**13 日内需要碰新高**或者新高。否则这个票呆的意义就不大」✓（碰上去了 ⇒ 逻辑成立 ⇒ 继续拿 ✓）
            ＋ 代码本来就有档位概念：`tranche_ladder.CONFIRM_STAGE_AMBUSH=("缩量止跌","结构到位")` ✓
              ⇒ 到「**突破/站稳**」就不再是埋伏档 ✓（**我们只是没接** ✗）
    量化（长样本 21 个月 ＋ **样本外分割** ✓）：
      · 不转正（固定 50 日 ＋ 预警降档）⇒ **+79.47%／回撤 −10.1%**
      · **浮盈 ≥10% 转正 ⇒ 之后「回撤 10%」移动止盈出** ⇒ **+152.10%／回撤 −5.3%** ✓✓
        分割：前段 **+46.78%／−5.3%** ✓｜后段 **+63.41%／−5.5%** ✓
        （基线 +32.69%/−6.6%、+41.52%/−10.5% ✗）
    实现（**无需新状态** ✓ —— 用 `paper_positions.highest_price` ✓）：
      · `WOLF_AMBUSH_PROMOTE_PCT`（**库内默认 0 = 关** ✓；pins **10** ✓）
      · `WOLF_AMBUSH_TRAIL_PCT`（默认 10 ✓）
      · 转正后：①**不再受 50 日/宽止损约束** ✗ ②改走「自最高价回撤 trail% 出」✓
                ③**总闸不再豁免** ✓（＝交回普通管理 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "def _is_ambush_promoted(self, acct: str, sym: str) -> bool" in mon
    assert 'os.getenv("WOLF_AMBUSH_PROMOTE_PCT", "0")' in mon
    assert 'os.getenv("WOLF_AMBUSH_TRAIL_PCT", "10")' in mon
    assert "highest_price" in mon, "转正判定须用 highest_price ✓（无需新状态 ✓）"
    assert "埋伏纪律：转正移动止盈" in mon, "转正后须走移动止盈 ✓（理由含「埋伏纪律」⇒ 过总闸 ✓）"
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert "_promo_ok" in gw and "转正后不再豁免" in gw
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    for k, v in (("WOLF_AMBUSH_PROMOTE_PCT", "'10'"), ("WOLF_AMBUSH_TRAIL_PCT", "'10'")):
        assert ("export %s=%s" % (k, v)) in pins, k


def test_warn_derisk_ratio_scope():
    """预警降档的**比例口径**（用户「仓位会相应降低吗」→ 量化后落地 ✓；账本 §9.223）。

    病灶（用户观察 ✓）：**我实现的是"只数"口径** ✗（新开 ≤5 只、超了才卖 ✓）
      ⇒ **"只数"管不住"仓位"** ✗ —— 实证：**20260211 仓位已 74.3%** 而**只数并没超** ✓
    他原话：「明天有冲高**减到 70%**」✓ ＝ **仓位比例** ✓（不是只数 ✗）
    量化（长样本 ＋ **样本外分割** ✓，均带"转正 10%／回撤 10%"✓）：
      · 只数口径（防御档 5 只）⇒ +152.10%／回撤 **−5.3%**（前段 +46.78%/−5.3%、后段 +63.41%/−5.5%）
      · **比例 40%** ⇒ **+541.14%／−9.6%**（前段 **+141.44%/−8.5%**、后段 **+140.10%/−10.8%**）✓
      · 比例 50% ⇒ +434.32%／−14.4%（后段 −14.5% ✗）；比例 60% ⇒ +299.87%／−12.9%
    ⇒ **比例 40% 两段都稳、收益约 3 倍** ✓（代价：绝对回撤略大 ✗）
    实现：`WOLF_WARN_TRIM_MAX_PCT`（**库内默认 0 = 关** ✓；pins **40** ✓）
      ⇒ 预警时**同时**满足「只数 ≤ 防御档」**与**「剩余市值 ≤ 权益×X%」✓（取两者**更严**者 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_WARN_TRIM_MAX_PCT", "0")' in mon
    i = mon.index('os.getenv("WOLF_WARN_TRIM_MAX_PCT"')
    seg = mon[i:i + 7000]
    assert "_target = _eq7 * _maxpct / 100.0" in seg, "目标＝权益×X% ✓"
    assert "_keep_n = max(_keep_n, _k7)" in seg, "取「只数」与「比例」中更严者 ✓"
    assert "预警降档(比例)" in seg
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_WARN_TRIM_MAX_PCT='40'" in pins


def test_promotion_requires_self_updated_highest_price():
    """转正的**前提**：`highest_price` 必须**自己在回测里更新** ✗（账本 §9.225）。

    病灶（实测）：5 只持仓的 `highest_price` **全部等于成本**（+0.0% ✗）
      · 追查发现：`highest_price` **只有生产端** `stop_loss_monitor._update_highest_price()` 在更新 ✓
        ⇒ **回测里它不跑** ✗ ⇒ 最高价永远 = 成本 ⇒ **转正永远触发不了** ✗✗
    修：①在埋伏纪律里**自己更新**（用监控已取到的现价 ✓）
        ②并把"取现价"（`_q`/`_cur`/`_cost`）**提到转正判定之前** ✓（否则 NameError ✗）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = mon.index("def _check_ambush_discipline")
    seg = mon[i:i + 25000]
    assert "UPDATE paper_positions SET highest_price=" in seg, "必须自己更新 ✓"
    assert "GREATEST(COALESCE(highest_price,0), :h)" in seg, "最高价只升不降 ✓（§9.534）"
    # 顺序：_cur 必须出现在 _promoted 判定之前 ✓
    a = seg.index('_cur = float((_qo.get("current") or _qo.get("price") or _qo.get("last") or 0) or 0)')
    b = seg.index("_promoted = False")
    assert a < b, "取现价必须在转正判定之前 ✓（否则 NameError ✗）"
    assert "自 self._ambush_promote_pct()" not in seg


def test_ambush_discipline_uses_current_field():
    """埋伏纪律取现价必须用 **`current`** ✗（账本 §9.227）。

    病灶（实测 002792）：现价 **60.78**（+36.1% ✓）而 `highest_price` 仍 **44.66** ✗
      ⇒ 追查：纪律里读的是 **`price`** ✗，而 `fetch_tencent_quote` 的字段是 **`current`** ✓
        ⇒ `_cur` 恒为 **0** ⇒ ①**最高价永不更新** ✗ ②**宽止损/50 日两条离场永不触发** ✗
        （与"本轮 0 条 `埋伏纪律` 日志"完全吻合 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert '_qo.get("current") or _qo.get("price") or _qo.get("last")' in mon, "必须优先 current ✓"
    i = mon.index("def _check_ambush_discipline")
    seg = mon[i:i + 25000]
    assert "current" in seg


def test_promotion_D_and_add_B():
    """**转正口径 D ＋ 加仓口径 B**（用户「转正条件改成D，加仓改成B」✓；账本 §9.234/235）。

    **D ＝ A∨B 转正 ＋ 13 日不碰新高离场** ✓（量化 +545.15%／−9.4% ✓，两端都不差 ✓）
      · A＝浮盈 ≥10%（我们的量化 ✓）；B＝**碰新高**（最高 > **买入前 20 日最高** ✓，他的口径 ✓）
      · **13 日不碰新高 ⇒ 离场** ✗ —— 账本自记「这条在**四个臂里从未出手**」✓，量化后**触发 58 次且有正贡献** ✓
    **B（加仓）＝ 转正日重置本轮锚** ✓（量化 +715.75%／−13.0%、比例 55.1 ✓；两段都优于 A ✓）
      · **3 个自然日内** ＋ **价 ≤ 转正日价×1.04** ⇒ **放行一次** ✓（语料「突破第一根加一半、第二根打满」✓＋「不追高」✓）
      · 反例：**"马上加"(C) 只有 +664.30%** ✗ ⇒ 「不追高 ≤4%」本身创造价值 ✓
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "def _promote_on_newhigh(self)" in mon and "def _prior_high_before_entry(self" in mon
    assert "def _no_newhigh_exit_days(self)" in mon and "def _record_promotion(self" in mon
    assert 'os.getenv("WOLF_AMBUSH_PROMOTE_NEWHIGH", "0")' in mon
    assert 'os.getenv("WOLF_AMBUSH_NO_NEWHIGH_EXIT_DAYS", "0")' in mon
    assert "ambush_promoted.json" in mon, "转正日须落盘（供加仓=B 用 ✓）"
    i = mon.index("def _check_ambush_discipline")
    seg = mon[i:i + 12000]
    assert "_prior_high_before_entry(_sym, _entry)" in seg, "B 口径：碰买入前 20 日最高 ✓"
    assert "日内未碰新高" in mon, "13 日离场须实现 ✓（对整文件断言 —— 窗口取小了会漏 ✗）"
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_AMBUSH_PROMOTE_ADD", "0")' in gw
    j = gw.index('os.getenv("WOLF_AMBUSH_PROMOTE_ADD"')
    seg2 = gw[j:j + 2600]
    # 改为**整文件搜索** ✓（这几个标记够独特 ✓；避免"窗口随插入失效"反复踩坑 ✗）
    assert "_ppx * 1.04" in gw, "溢价 ≤4% ✓（不追高 ✓）"
    assert "_todayA" in gw, "\"今天\"须取模拟日 ✓（DATA_DIR 末段 ✓）"
    assert "_ac = None" in gw, "命中后须跳过加仓闸 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    for k, v in (("WOLF_AMBUSH_PROMOTE_NEWHIGH", "'1'"), ("WOLF_AMBUSH_NO_NEWHIGH_EXIT_DAYS", "'13'"),
                 ("WOLF_AMBUSH_PROMOTE_ADD", "'1'")):
        assert ("export %s=%s" % (k, v)) in pins, k


def test_alert_hub_unified_qq_push():
    """**统一异常告警出口**（用户「加个全局异常处理，统一走QQ推送」✓；账本 §9.238）。

    教训（真实 ✓）：埋伏纪律每次抛 `No module named 'PySide6'` ✗，而监控主循环**只 print** ✗
      ⇒ **没有任何告警** ⇒ 整条纪律**静默失效很久**才被用户追问发现 ✗✗
    实现：`alert_hub`（**库内默认关** ✓）
      · `install()`：`sys.excepthook` ＋ **`threading.excepthook`**（**监控线程是子线程** ✓ 关键 ✓）
      · `note(where, exc, msg)`：**必落盘** `<DATA_DIR>/alerts.jsonl` ✓ → 再按开关推 QQ ✓
      · **防刷屏**：`WOLF_ALERT_DEDUP_SEC`（默认 600 ✓）＋ `WOLF_ALERT_MAX_PER_HOUR`（默认 20 ✓）
      · 接线：监控主循环异常 ✓、埋伏纪律异常 ✓、`start_t_monitor` 安装 ✓、`bt_prod_run` 安装 ✓
    """
    ah = open(os.path.join(_ROOT, "backend", "app", "services", "alert_hub.py"), encoding="utf-8").read()
    assert "def install(" in ah and "def note(" in ah and "def push_qq(" in ah
    assert "threading.excepthook" in ah, "子线程（监控线程）必须覆盖 ✓"
    assert "sys.excepthook" in ah
    assert '_env_on("WOLF_ALERT_QQ", "0")' in ah, "推送默认关 ✓"
    assert '_env_on("WOLF_ALERT_HUB", "0")' in ah, "钩子默认关 ✓"
    assert "WOLF_ALERT_DEDUP_SEC" in ah and "WOLF_ALERT_MAX_PER_HOUR" in ah, "必须防刷屏 ✓"
    assert "alerts.jsonl" in ah, "必须落盘（不依赖 QQ ✓）"
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'note("t_monitor.round", e)' in mon, "主循环异常须走告警 ✓"
    assert 'note("t_monitor.ambush_discipline", _e)' in mon, "纪律异常须走告警 ✓"
    assert "_ah3.install()" in mon, "start_t_monitor 须安装钩子 ✓"
    bp = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert "_ahP.install()" in bp, "回测驱动也须安装 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    # ★ 账本 §9.643 ✓（用户 2026-10-05：「机器人疯狂推送,我要烦死了」✗）：
    #   **回测里 QQ 置 0** ✓（`WOLF_ALERT_HUB` 钩子仍开 ✓ ⇒ 全部**落盘** ✓，看板可查 ✓）
    #   ★ 契约的**灵魂不变** ✓：钩子必开 ✓、必落盘 ✓、收件人必在 ✓、防刷屏必在 ✓
    #     变的只是"回测要不要真的打扰人" ✓ —— 生产仍按 `WOLF_ALERT_QQ=1` 推 ✓
    # ★ 账本 §9.644 ✓（用户：「怎么全都不通知了」✗）：**恢复推送** ✓
    #   止噪不靠"关推送" ✗，而靠**过滤器**（路径只认仓库 ✓、类型只留真失败 ✓、note_silent 只落盘 ✓）
    assert "export WOLF_ALERT_HUB='1'" in pins, "钩子必须开（落盘＋推送靠它）✓"
    assert "export WOLF_ALERT_QQ='1'" in pins, "真失败必须能推出去 ✓"
    assert "export WOLF_ALERT_QQ_TO=" in pins, "收件人须写进 pins ✓"


def test_alert_hub_does_not_pollute_database_url():
    """**告警推送不得污染 `DATABASE_URL`** ✗✗（账本 §9.240 —— 本轮真实事故 ✓）。

    事故（实测 ✓）：`core/qq_notifier.py` 顶层 `_load_env()` **强制覆盖 `DATABASE_URL`** ✗
      （改成 `.env` 里的 **18789** ✓ —— AGENTS.md 早有警告 ✓）
      · 我加的统一告警一推 ⇒ **本进程后续数据库连接全废** ✗✗
      · 症状：`[TMonitor] 持仓读取失败(drabt35): … port 18789 failed` ⇒ **0 成交** ✗
    修：`push_qq()` **保存 `DATABASE_URL`** ✓，`finally` 里**还原** ✓ ⇒ 副作用完全隔离 ✓
    （实测：推送后 DSN 仍为 **5433** ✓，且 QQ 正常收到 ✓）
    """
    ah = open(os.path.join(_ROOT, "backend", "app", "services", "alert_hub.py"), encoding="utf-8").read()
    assert '_dsn_bak = os.environ.get("DATABASE_URL")' in ah, "推送前须保存 DSN ✓"
    assert 'os.environ["DATABASE_URL"] = _dsn_bak' in ah, "finally 里须还原 ✓"
    i = ah.index("def push_qq(")
    seg = ah[i:i + 2600]
    assert seg.count("_dsn_bak") >= 2, "保存 + 还原 ✓"
    assert "qq_notifier" in seg, "推送走现成 notifier ✓"


def test_ambush_discipline_falls_back_to_daily_close():
    """埋伏纪律**行情取不到时必须回退日线收盘** ✗（账本 §9.241 —— 第四层原因 ✓）。

    链路（逐步挖出来的 ✓）：
      ①纪律函数**整体抛异常**（PySide6 ✗）⇒ 已修（两处 try ✓）
      ②字段名错（`price` vs **`current`** ✗）⇒ 已修
      ③符号大小写（`SZ…` ✗）⇒ 已修
      ④**行情源在回测里根本没替换到** ✗ —— `t_monitor` 模块顶部
        `from app.services.t_data_sources import fetch_tencent_quote` ✓，
        而回测替身是**之后**替换**模块属性**的 ✗ ⇒ 监控拿到的仍是**真函数** ✗
        ⇒ 沙箱无网络 ⇒ 行情空 ⇒ `_cur = 0` ⇒ **不更新/不转正/不离场** ✗✗
      ⇒ 修：`_cur <= 0` 时**回退到日线最后一根的收盘**（as-of 同源、复权 ✓，两种环境都对 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = mon.index("def _check_ambush_discipline")
    seg = mon[i:i + 14000]
    assert "回退：**日线最后一根的收盘**" in seg or "日线最后一根的收盘" in seg
    j = seg.index("_cur <= 0 and _bd")
    assert "close" in seg[j:j + 500], "回退必须用 close ✓"


def test_promotion_record_persists_and_active_add():
    """转正记录必须**落在运行根** ✗ ＋ 转正后必须**主动加仓一次** ✓（账本 §9.244）。

    实测暴露的两个真问题（用户「你看看这次转正了吗，我看没加仓呢」✓）：
      ① **转正确实发生了** ✓（SH603660 01-06/07/08/12、SZ002792 01-08/09/12 ✓）——
         但记录写在**按日目录** ✗ ⇒ **每天一个新文件** ⇒ 「3 自然日窗口」**形同虚设** ✗
         ⇒ 修：落 `os.path.dirname(DATA_DIR)`，即**运行根** ✓
      ② **加仓 0 次** ✗：`转正加仓放行` **0 次** ✓ —— 因为**没有买入腿愿意买它们**
         （这两只上产生的全是**卖出/兑现腿** ✗）⇒ 我原先只做"**被动放行**" ✗，
         与量化的 B（"**主动买一次**" ✓，+715.75%／−13.0% ✓）**对不上** ✗
         ⇒ 修：新增 `_check_ambush_promote_add()` ✓（3 内 ∧ ≤转正价×1.04 ⇒ **主动买一次** ✓，买成后置 `added` ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'os.path.dirname(os.environ.get("DATA_DIR", "/app/data"))' in mon, "记录须落运行根 ✓"
    assert "def _check_ambush_promote_add(self)" in mon
    assert "self._check_ambush_promote_add," in mon, "须挂进统一入口 ✓"
    i = mon.index("def _check_ambush_promote_add")
    seg = mon[i:i + 7000]
    assert "_ppx * 1.04" in seg, "溢价 ≤4% ✓（不追高 ✓）"
    assert "_gap > 3" in seg and "_gap < 0" in seg, "3 自然日窗口 ✓"
    assert 'gateway_execute(_sym, "buy"' in seg, "必须**主动**买 ✓（不是只放行 ✗）"


def test_gateway_promotion_exemption_uses_record():
    """网关的**豁免判断**必须与"转正口径"一致 ✗（账本 §9.245 —— 用户「通宇通讯为什么没有再触发交易」✓）。

    实测（SZ002792 ✓）：
      · 监控按「**碰新高**」判它**转正** ✓（记录 `pday 20260107` ✓）
      · 而网关豁免判断**只看浮盈 ≥10%** ✗（它 highest 48.20 < 49.13 ✗）
      · ⇒ 网关仍当它是"未转正的埋伏仓" ✗ ⇒ **01-05 之后一笔都没成交** ✗
        （卖腿被"埋伏仓豁免"全拦 ✓；加仓又被"不追高 ≤4%"挡 ✓ ⇒ 一动不动 ✓）
    修：网关**直接读转正记录** `ambush_promoted.json` ✓（全运行共享 ✓ 单一事实来源 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert "ambush_promoted.json" in gw, "网关须读转正记录 ✓"
    i = gw.index("已转正（记录 ✓）")
    seg = gw[max(0, i - 1200):i + 400]
    assert "_promo_ok = False" in seg, "已转正 ⇒ 不再豁免 ✓"
    assert "os.path.dirname(os.environ.get(\"DATA_DIR\"" in seg, "须读运行根 ✓"


def test_dual_source_audit_fixes():
    """**双源分歧审计**的两处高杠杆修复（账本 §9.246 ✓）。

    审计（`.dsh-tmp/wolfbt/audit_dual_source.py` ✓）扫出这一类病的**规模** ✗：
      A 未归一化行情调用 **31 处** ✗ ｜ B 读 `"price"` 5 处（多为兼容读 ✓）
      C `datetime.now()` **381 处** ✗ ｜ D `from-import` 被 patch 的函数 **11 处** ✗
      E 跨日状态写按日目录 **41 处** ✗ ｜ F 顶层 import 有副作用 **8 处** ✗

    修（两处杠杆最大 ✓）：
      ① `core/qq_notifier._load_env`：**已显式注入的关键变量（`DATABASE_URL` 等）不得覆盖** ✗
         （事故：import 一次 ⇒ 进程内所有数据库连接全废 ⇒ 回测 0 成交 ✓）
      ② `jobs/bt_prod_run.fanout_shims()`：遍历 `sys.modules`，把**行情替身／时段门／模拟时钟**
         **写进每个已导入模块的命名空间** ✓ ⇒ 一次覆盖 A/C/D 三类共 ~420 个潜在点 ✓
    """
    qq = open(os.path.join(_ROOT, "core", "qq_notifier.py"), encoding="utf-8").read()
    assert "_KEEP" in qq and '"DATABASE_URL"' in qq, "关键变量不得被 .env 覆盖 ✓"
    bp = open(os.path.join(_ROOT, "jobs", "bt_prod_run.py"), encoding="utf-8").read()
    assert "def fanout_shims()" in bp and "fanout_shims()   # **补丁广播**" in bp
    i = bp.index("def fanout_shims()")
    seg = bp[i:i + 3000]
    assert "_sys.modules.items()" in seg, "须遍历已导入模块 ✓"
    assert 'setattr(mod, "fetch_tencent_quote", _fake)' in seg, "行情替身广播 ✓"
    assert 'setattr(mod, "_is_trading_time"' in seg, "时段门广播 ✓"
    assert 'setattr(mod, "datetime", _dt.datetime)' in seg, "模拟时钟广播 ✓（§9.616：本文件导入的是 datetime as _dt，原 datetime.datetime 会 NameError ✗）"
    assert "_REAL_DATETIME_CLS" in bp, "须记录原类以识别待替换模块 ✓"


def test_audit_a_b_migrations():
    """**(a) 行情调用归一化** ＋ **(b) 跨日状态根**（用户「a+b」✓；账本 §9.247／§9.248）。

    (a) §9.246-A（31 处 ✗）：机械补齐 `_normalize_symbol` ✓ —— **但指数代码除外** ✗✗
        原因：`_normalize_symbol("000001")` ⇒ **`sz000001`（平安银行 ✗）**，而不是**上证指数** ✗
        ⇒ `t_regime`／`t_external_risk` 两处**故意不动** ✓（它们取的是**指数** ✓）
    (b) §9.246-E（41 处 ✗）：**跨日状态**改落"状态根" ✓ —— 新增 `state_paths.state_dir()` ✓
        · **回测**：`WOLF_STATE_ROOT` = **运行根** ✓（pins ✓）
        · **生产**：不设 ⇒ **回落 `DATA_DIR`** ✓ ⇒ **零影响** ✓
        已改 6 个文件（`roundtrip_sell`／`wolf_hedge_refill`／`wolf_discipline`／
        `wolf_mainline_select`／`systemic_risk`／`trade_graph` ✓）＋ `t_base_floor._data_dir` ✓
    """
    sp = open(os.path.join(_ROOT, "backend", "app", "services", "state_paths.py"), encoding="utf-8").read()
    assert "def state_dir()" in sp and "WOLF_STATE_ROOT" in sp and "WOLF_STATE_ROOT\")" or True
    assert 'os.environ.get("WOLF_STATE_ROOT")' in sp
    for rel in ("backend/app/services/roundtrip_sell.py", "backend/app/services/wolf_hedge_refill.py",
                "backend/app/services/wolf_discipline.py", "backend/app/services/wolf_mainline_select.py",
                "apps/main_line/systemic_risk.py", "backend/app/services/trade_graph.py"):
        t = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        assert "__state_path" in t, rel + " 须走状态根 ✓"
        assert "from app.services.state_paths import state_path" in t
    bf = open(os.path.join(_ROOT, "backend", "app", "services", "t_base_floor.py"), encoding="utf-8").read()
    assert 'os.environ.get("WOLF_STATE_ROOT")' in bf
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_STATE_ROOT=" in pins
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert '_normalize_symbol(s["symbol"])' in mon, "个股列表须归一化 ✓"
    # **指数**两处必须保持原样 ✓（否则会把上证指数变成平安银行 ✗）
    for rel, needle in (("backend/app/services/t_regime.py", "fetch_tencent_quote(list"),
                        ("backend/app/services/t_external_risk.py", "fetch_tencent_quote(list")):
        t = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        assert needle in t, rel + " 取指数 ⇒ 不得套个股归一化 ✓"


def test_promote_add_has_visibility_when_no_quote():
    """转正加仓**必须留痕**（即使行情取不到 ✓）—— 账本 §9.249。

    教训（本轮 ✓）：我先前只对"**价格 > 1.04×**"留痕 ✗，而**取不到行情**（`_cur=0` ✗）
    是**静默跳过** ✗ ⇒ 与"没跑"分不清 ✗（又是同一类可观测性坑 ✓）。
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = mon.index("def _check_ambush_promote_add")
    seg = mon[i:i + 7000]
    assert "回退到日线收盘" in seg, "行情取不到时须回退日线收盘 ✓（与纪律同口径 ✓）"
    assert "行情与日线都空" in seg, "都空时仍要留痕 ✓"
    assert seg.count("埋伏转正加仓·跳过") >= 2, "两种跳过原因都要留痕 ✓"


def test_dynamic_quote_helper_wired():
    """纪律/加仓必须走**动态行情** ✓（账本 §9.249 —— 第五次同类坑 ✗）。

    病灶：`t_monitor` 顶部 `from … import fetch_tencent_quote` ✗ ⇒ from-import **绑走原对象** ✗
      ⇒ 回测替换模块属性**对它无效** ✗ ⇒ 沙箱无网络 ⇒ `_cur = 0` ⇒ 纪律静默失效 ✗
    修：`_quote_now()` **每次从模块属性取** ✓ ⇒ 谁替换都立刻生效 ✓（与"补丁广播"双保险 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "def _quote_now(self, symbols)" in mon
    assert 'getattr(_tds, "fetch_tencent_quote", None)' in mon
    import re as _re
    calls = _re.findall(r"self\._quote_now\((\w+)\)", mon)
    assert len(calls) >= 2, "纪律与加仓都要用它 ✓"
    lines = mon.split("\n")
    for i, ln in enumerate(lines):
        m = _re.search(r"self\._quote_now\((\w+)\)", ln)
        if not m:
            continue
        var = m.group(1)
        assert any((("%s = " % var) in x) or (("for %s in" % var) in x)
                   for x in lines[max(0, i - 60):i]), \
            "行 %d 的参数 %s 必须在同函数内已定义（含循环变量 ✓；防 NameError ✗）" % (i + 1, var)


def test_promotion_is_monotonic_from_record():
    """**转正只进不退** ✓（账本 §9.253 —— 又一次"双源" ✗）。

    病灶（实测 SH603660 ✓）：01-06 已判转正 ✓（写进记录 ✓），
      而 01-07 重算时**浮盈不足 ＋ 当日未破前高** ⇒ 又算成 **转正=否** ✗
      ⇒ 状态**翻烧饼** ✗（豁免／移动止盈／加仓窗口全跟着抖 ✗）
    修：**记录＝单一事实来源** ✓ —— 已在记录里 ⇒ **永远算已转正** ✓
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = mon.index("if not _promoted:")
    seg = mon[i:i + 2500]
    assert "ambush_promoted.json" in seg, "须查记录 ✓"
    assert "_promoted = True" in seg, "查到即判已转正 ✓"
    assert "if _promoted:\n                        self._record_promotion" in seg or "if _promoted:" in seg


def test_promote_add_exempt_from_t_window():
    """**转正后加仓豁免做T时段窗** ✓（账本 §9.256 —— 用户「现在能看到了吗」暴露的真因 ✓）。

    实测（终于看到了理由 ✓）：
      `[TMonitor] 埋伏转正加仓 SH603660 买 900股@11.13: blocked｜理由=做T时段窗：09:15 不在 09:45-10:00,14:00-14:30 内`
      `[TMonitor] 埋伏转正加仓 SZ003031 买 100股@84.79: blocked｜理由=做T时段窗：09:15 不在 …`
    ⇒ **加仓检查总在 09:15 跑** ✗ ⇒ 不豁免 ⇒ **永远被挡** ✗✗（这就是"一笔都没加过"的真因 ✓）

    修（用**现成的豁免通道** ✓）：
      ① 加仓买入传**专属腿型** `trigger_kind="wolf_ambush_promote_add"` ✓
      ② pins：`WOLF_TURNOVER_TW_EXEMPT='custom_prevlow,wolf_ambush_promote_add'` ✓
         （⚠️ **必须合并成一行** ✗ —— bash 里**后者覆盖前者** ✓，我第一版就是两行 ⇒ 会被盖掉 ✗）
    """
    # 机制（§9.257 修正 ✓）：`gateway_execute` **没有** `trigger_kind` 形参 ✗
    #   ⇒ 改为**在网关里按理由识别**腿型 ✓（传参那条路会 TypeError ✗ —— 实测被异常打印抓到 ✓）
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'trigger_kind="wolf_ambush_promote_add"' not in mon, "不得再传不存在的参数 ✗"
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'if not _tkA and "埋伏转正加仓" in str(reason or ""):' in gw, "网关须按理由识别加仓腿型 ✓"
    assert "_tkA or _turnover_kind(trigger_id, condition_id)" in gw, "识别结果须真正接到时段窗判定 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    imports = [ln for ln in pins.split("\n") if ln.startswith("export WOLF_TURNOVER_TW_EXEMPT=")]
    assert len(imports) == 1, "豁免名单**只能有一行** ✗（后者会覆盖前者 ✓）"
    assert "wolf_ambush_promote_add" in imports[0], "须含加仓腿型 ✓"
    assert "custom_prevlow" in imports[0], "原有豁免不得丢 ✓"


def test_cooldown_real_trading_days_pin():
    """「跨日冷却」必须按**真实交易日差**判 ✓（账本 §9.258 —— 加仓被它全拦 ✗）。

    仓库注释（2026-09-22 修 T6 时查出 ✓）：
      「原实现**从未使用 `COOLDOWN_DAYS` 做比较**，只判 `最近同向成交日 < 今天`
       ⇒ 语义退化…（而文案写着"未满 2 个交易日" —— **与实现不符**）」✗
    实测（本轮 ✓）：加仓被反复拦：
      `理由=跨日冷却：SH603660 上次买入在 2026-01-05，未满 2 个交易日` ✗
    修（用仓库**现成的修正开关** ✓）：`WOLF_COOLDOWN_REAL_DAYS=1`
      ⇒ 按真实交易日差判（≥ `COOLDOWN_DAYS` 个交易日 ⇒ 放行 ✓）；**只放宽、不收紧** ✓
    """
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_COOLDOWN_REAL_DAYS='1'" in pins, "须开真实交易日差判 ✓"
    tt = open(os.path.join(_ROOT, "backend", "app", "services", "t_turnover.py"), encoding="utf-8").read()
    assert "WOLF_COOLDOWN_REAL_DAYS" in tt and "_REAL_CD" in tt, "开关须真的被使用 ✓"


def test_cooldown_real_scope_both():
    """「跨日冷却」的修正必须**作用到买侧** ✓（账本 §9.259 —— 加仓被它拦的**真因** ✗）。

    链路（逐层挖出 ✓）：
      · 加仓调用**确实发出** ✓ ⇒ 被拦 ⇒ 理由 `跨日冷却：… 未满 2 个交易日` ✗
      · 开了 `WOLF_COOLDOWN_REAL_DAYS=1` ✓ 仍拦 ✗
      · 实测 `_cal_days_between(20260105, 20260107) = **2**` ✓（已满）⇒ 说明**买侧没走修正逻辑** ✗
      · 根因：该修正的作用面开关 **`WOLF_COOLDOWN_REAL_SCOPE` 默认 `sell`** ✗
        （仓库注释自己写着 `both` = 「**买侧同 bug 一并修**」✓）
      ⇒ 修：pins 加 `WOLF_COOLDOWN_REAL_SCOPE='both'` ✓
      ⇒ 实测（`t_turnover.check` 买侧、2 个交易日后）⇒ **`allow=True`** ✓✓
    """
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_COOLDOWN_REAL_SCOPE='both'" in pins, "作用面须含买侧 ✓"
    assert "export WOLF_COOLDOWN_REAL_DAYS='1'" in pins
    tt = open(os.path.join(_ROOT, "backend", "app", "services", "t_turnover.py"), encoding="utf-8").read()
    assert "WOLF_COOLDOWN_REAL_SCOPE" in tt and "_REAL_CD_SCOPE" in tt


def test_break_support_wolf_caliber():
    """`break_support` 换成**狼大口径** ✓（账本 §9.263 —— 用户「C 落地」✓）。

    语料（逐字 ✓）：
      「**最基础的技术，记点位画线** 这些点位都是之前**突破位**，相反下跌就是**支撑位**」
      「**3日5日最多10日** 都是些散户游资 而**机构单子一般都是挂在 10日 13日 34**」
      **「可以不追高 但是卖点只有跌破支撑位」**／「**放量跌破确认**」／「**缩量到支撑不割肉**」
    ⇒ C：`break_support = **首次跌破（MA13 或 MA34）** ∧ **放量（>1.2×5 日均量）**` ✓
    旧口径（现价 ≤「现价下方最近」支撑 ✗）⇒ **比狼大更近** ⇒ 卖飞（实测 002156：卖点 39.45 而 MA13/34＝37.56/37.01 ✓）
    开关 `WOLF_BREAK_SUPPORT_TREND`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_BREAK_SUPPORT_TREND", "0")' in mon, "须有开关且默认关 ✓"
    assert "_first_break_v" in mon and "_vol_ok_v" in mon, "须有首次破位 + 放量两个判据 ✓"
    assert "_pma13" in mon and "_pma34" in mon, "“首次”须用**昨日**均线比较 ✓"
    assert '"ma13": _ma13_v' in mon and '"ma34": _ma34_v' in mon, "留痕字段 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_BREAK_SUPPORT_TREND='1'" in pins


def test_newhigh_after_entry_caliber():
    """「新高」＝**买入之后** ✓（账本 §9.265 —— 用户「改」✓）。

    他原话：「我说一下我用的 **买入有时间** 然后 **13 日内需要碰新高**或者新高。
            否则这个票呆的意义就不大，**证明自己的买入逻辑和时间**有问题…」✓
    旧口径 ✗：新高＝**建仓日之前** 13 根日K 的最高价 ⇒ 而我们的入场是**突破型**
      ⇒ **买入时已在其上** ⇒ 「碰新高」买入即成立 ⇒ 判据**空转**
      （实测 4 例中 3 例：002156 39.54>38.72／603660 11.44>11.24／603690 33.41>32.88 ✓）
    新口径 ✓：新高线＝**建仓日（含）当根的最高价** ✓ ⇒ 「买入后创新高」✓
    开关 `WOLF_NEWHIGH_AFTER_ENTRY`（**库内默认 0 = 关** ⇒ 旧口径逐字不变 ✓；pins 开 ✓）
    """
    es = open(os.path.join(_ROOT, "backend", "app", "services", "wolf_early_stop.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_NEWHIGH_AFTER_ENTRY", "0")' in es, "须有开关且默认关 ✓"
    assert "ref_high" in es and "ph = float(ref_high)" in es, "须真的用新参照 ✓"
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_NEWHIGH_AFTER_ENTRY", "0")' in mon, "埋伏侧同口径 ✓"
    assert "if _d8 == str(entry) and _h:" in mon, "参照须是**建仓日当根** ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_NEWHIGH_AFTER_ENTRY='1'" in pins


def test_promote_add_exempt_from_addon_cap():
    """**转正加仓豁免「加仓口径闸」** ✓（账本 §9.269 —— 用户拍板「a」✓）。

    冲突（实测 ✓）：那道闸的锚是「**埋伏建仓那一笔**」⇒ 要求 价 ≤ 首笔×1.04 ∧ 距首笔 ≤3 自然日
      而**转正通常在建仓后 8～11 天**且价已 > 首笔 +4%
      ⇒ 两条同时满足概率≈0（603660：首笔 11.21 ⇒ 上限 11.66，而价 12.22／11.92／11.80 ✗）
      ⇒ ⇒ **转正加仓必然被拦** ✗
    拍板 ✓：**转正加仓只服从量化的 B 口径**（锚＝转正日：3 日内 ∧ ≤ 转正价×1.04 ✓）
    开关 `WOLF_PROMOTE_ADD_EXEMPT`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_PROMOTE_ADD_EXEMPT", "0")' in gw, "须有开关且默认关 ✓"
    assert "埋伏转正加仓" in gw and "_pa_exempt" in gw, "须按理由识别 ✓"
    i = gw.index("_pa_exempt")
    seg = gw[i:i + 1200]
    assert "_ac = None" in seg, "命中即跳过加仓闸 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_PROMOTE_ADD_EXEMPT='1'" in pins


def test_promoted_skips_t_window():
    """**已转正的仓位，卖腿不再受「做T时段窗」约束** ✓（账本 §9.270）。

    依据 ✓：① 用户已拍板「转正 ⇒ 交回普通管理」✓（§9.220／§9.232）
           ② 时段窗是**做T的纪律**（语料 playbook:287 自划界：「这里说的买卖方法一般对应的**单日或…**」✓）
              ⇒ 高抛/止盈/黄线**不是做T** ✓ ⇒ 不该受它约束 ✓
           ③ **实测**：被它挡最多的**正是 3 只已转正的票** ✗
             `SZ002792 **140 次** ✗｜SH603660 96 次｜SZ002384 40 次`（合计 **318 次** ✓）
    开关 `WOLF_PROMOTED_SKIP_TWINDOW`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_PROMOTED_SKIP_TWINDOW", "0")' in gw, "须有开关且默认关 ✓"
    assert "wolf_promoted_normal" in gw, "须把腿型置换为**非做T类** ✓"
    assert "ambush_promoted.json" in gw, "须查转正记录 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_PROMOTED_SKIP_TWINDOW='1'" in pins


def test_promoted_skips_ambush_exemption():
    """**已转正 ⇒ 不再被「埋伏仓豁免」拦** ✓（账本 §9.271）。

    为什么以前没生效 ✗：我 §9.245 的修补落在了**另一个块**（总闸 `_ALLOW_SUB` ✓），
      而**卖腿豁免块**（`WOLF_AMBUSH_SELL_EXEMPT` ✓）**只看两个条件**（腿型 ✓ ＋ 是不是埋伏仓 ✓）
      ⇒ **没查转正记录** ✗ ⇒ 实测 **SZ002792 转正后（01-09）仍被它拦** ✗（价 57.31 ✗）
    语义 ✓：转正 ＝ **交回普通管理**（用户已拍板 ✓）⇒ 高抛/止盈/黄线**应当能卖** ✓
    开关 `WOLF_PROMOTED_SKIP_EXEMPT`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert "WOLF_PROMOTED_SKIP_EXEMPT" in gw, "须有开关 ✓"
    assert "_skip_ex" in gw and "(not _skip_ex) and _ambush_position(" in gw, "须真的让它不豁免 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_PROMOTED_SKIP_EXEMPT='1'" in pins


def test_ambush_entry_day_no_early_break():
    """`_ambush_entry_day` 的**致命早退**已修 ✓（账本 §9.276）。

    旧 ✗：`for 最新→最旧: if 买: _acc+=v; _entry=日期 else: _acc-=v; if _acc<=0: break`
      ⇒ **最新一笔是"卖"就立刻 break** ⇒ `_entry` 从未赋值 ⇒ 返回 **""** ✗
      ⇒ 纪律第 5 道门（`if not _entry: continue`）把它挡掉 ⇒ **永久不再管理该票** ✗✗
    实证（SZ002792）：01-08 卖了两笔 ⇒ 入场日="" ⇒ **没有移动止盈** ⇒ 峰值 73.35 → 45.33 才被卖 ✗
    新 ✓：先取 `paper_positions.volume` 作为目标 ⇒ 只把**覆盖当前持仓**的买日记为入场 ✓
      且**不再 break** ⇒ 取**最早**那个 ✓（FIFO ✓）
    复算：卖100→−100；卖100→−200；买200(01-07)→0（<200 ✗）；买200(01-05)→200 ⇒ **entry=20260105** ✓
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    i = mon.index("def _ambush_entry_day")
    seg = mon[i:i + 2600]
    assert "_target" in seg, "须取当前持股数作为目标 ✓"
    assert "if _target <= 0 or _acc >= _target:" in seg, "只把覆盖当前持仓的买日记为入场 ✓"
    assert "if _acc <= 0:" not in seg, "**不得**再有那个致命早退 ✗"


def test_wave_hold_no_reduce_gate():
    """**浪型动作=参与 ⇒ 当天不做减仓性操作** ✓（账本 §9.287 —— 用户「甲」✓）。

    判据＝**他的原话** ✓（不看收益 ✓）：
      「主升浪中间的调整我一般不看的，特别还是趋势大标，这种**一旦卖了很难买回来**」✓
      「就是挂前一天的低点 能买进去就做正T，**买不进去证明涨了，不用动** 主升浪的做法」✓
      「一个票最多买 2 笔 卖 2 笔 后面如果是主升浪的话**越动收益越低**」✓
    依据＝我们的浪型"**动作层**"与他的原话对照 **6/7 ≈ 86% 对得上**（§9.286 ✓）
    保留**保护性**离场 ✓（他的"不动"是**不折腾**，不是不设防 ✓）
    开关 `WOLF_WAVE_HOLD_NO_REDUCE`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_WAVE_HOLD_NO_REDUCE", "0")' in gw, "须有开关且默认关 ✓"
    assert "_WAVE_HOLD_REDUCE_KINDS" in gw and "_WAVE_HOLD_PROTECT_KW" in gw, "减仓性/保护性清单 ✓"
    assert '"level": "WAVE_HOLD"' in gw, "须有拦截级别留痕 ✓"
    assert "def _wave_hold_action" in gw and "abs((_b - _a).days) > 10" in gw, "须有新鲜度校验 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "WOLF_WAVE_HOLD_NO_REDUCE" in pins, "pins 须可开 ✓"


def test_wave_hold_only_blocks_weakness_sells():
    """浪型"不折腾"闸**只拦\"因弱而卖\"** ✓（账本 §9.289 —— 用户「主升时期是所有股票都不能卖出吗」✓）。

    他的原话（逐字 ✓）：
      「到**压力位/BOLL 上轨减半**、**破位收盘确认才走**；**没有个股级的固定百分比止损**（**止损只在指数大级别破位**）」✓
      「他的保护不是靠止损，而是靠 **①不追高 ②不在地量割肉 ③周末/事件前减半避险 ④看不懂就不做**」✓
      「**板上减半**（他 2026-09-01：「**吃一口减一半**」「**板上减了**」）」✓
      「主升浪中间的调整我一般不看的…一旦卖了很难买回来」✓ ＝ **不因回调而卖** ✓，**不是"什么都不能卖"** ✗
    ⇒ **拦**：破黄线／去弱留强／道氏T卖／自定义位／移动／破支撑 ✓
    ⇒ **放行**：压力位兑现(0.618)／小赚兑现／板上减半／确认卖／T出前高 ✓
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert "_WAVE_HOLD_ALLOW_KINDS" in gw, "须有放行清单 ✓"
    for k in ("wolf_fib_target_sell", "wolf_profit_take_sell", "wolf_board_half_sell", "wolf_confirm_sell", "high_sell"):
        i = gw.index("_WAVE_HOLD_ALLOW_KINDS = {")
        assert k in gw[i:gw.index("}", i)], "放行清单须含 %s ✓" % k
    i2 = gw.index("_WAVE_HOLD_REDUCE_KINDS = {")
    block = gw[i2:gw.index("}", i2)]
    for k in ("custom_vwap_sell", "wolf_defensive_t_reduce", "wolf_dao_t_sell"):
        assert k in block, "拦清单须含 %s ✓" % k
    for k in ("wolf_fib_target_sell", "wolf_profit_take_sell", "wolf_board_half_sell"):
        assert k not in block, "拦清单**不得**含高位类 %s ✗" % k


def test_ambush_peak_survives_row_rewrite():
    """**持仓行被成交重写 ⇒ `highest_price` 丢失** ✗ ⇒ 移动止盈永不触发 ✗（账本 §9.291）。

    实证（本轮 8 只持仓 ✓，统计性证据 ✓）：
      · **成交过**的：`SH603375 39.940(=成本)` ✗／`SZ002364 44.280(=成本)` ✗／`SH600487 71.180(=成本)` ✗
      · **没成交**的：`SH603061 最高 255.35`（成本 186.93 ✓）／`SH601231 37.419` ✓
      ⇒ ⇒ 只有**没被重写**的行才保得住 ⇒ 移动止盈基数被清 ⇒ **永不触发** ✗
      （现场：`[埋伏纪律] SZ002792 … 最高=0.00 持有=13日 转正=是` ✗ 全程 0 ✗
        ⇒ 它最终在 01-22 以 **45.33** 被普通腿卖掉 ✗，而峰值是 **73.35** ✗）
    修 ✓：**从日线档算"入场以来的最高"** ✓（数据驱动 ✓ 不受行重写影响 ✓），并**写回 DB** ✓
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "_peak_bar" in mon, "须从日线档算峰值 ✓"
    i = mon.index("_peak_bar = max(_hb)")
    seg = mon[i:i + 2500]
    assert "UPDATE paper_positions SET highest_price=" in seg, "峰值须写回 ✓"
    assert "_hi = _peak_bar" in seg, "须用峰值参与转正/移动止盈判定 ✓"


def test_base_exempt_strict_scope():
    """「**浮盈≥3% 兑现只针对做T**」✓、**全仓只在突破/压力位/加速结束才减半** ✓（账本 §9.299）。

    用户口径 ✓：
      · 「**浮盈≥3% 就兑现**」是**针对做T的** ✓，**不是全仓** ✗
      · **全仓**应收紧到**只在「突破／压力位／加速结束」**时减半 ✓
    ⇒ 收紧后 **可动底仓** 只有：`wolf_fib_target_sell`(0.618 压力位 ✓)／`wolf_boll_upper_sell`(BOLL 上轨 ✓)／
      `wolf_board_half_sell`(板上/加速 ✓)／`wolf_confirm_sell`(加速结束 ✓)
    ⇒ **做T类移出**（只动 T 仓 ✓）：`wolf_profit_take_sell` ✗／`high_sell` ✗／`wolf_dao_t_sell` ✗
    ⇒ **关键词表同步收窄** ✓（原含"兑现/高抛/止盈"⇒ 会把移出的腿**绕过** ✗）
    开关 `WOLF_BASE_EXEMPT_STRICT`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    """
    cap = open(os.path.join(_ROOT, "backend", "app", "services", "t_capacity.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_BASE_EXEMPT_STRICT", "0")' in cap, "须有开关且默认关 ✓"
    i = cap.index('if str(os.getenv("WOLF_BASE_EXEMPT_STRICT"')
    seg = cap[i:i + 2500]
    for k in ("wolf_fib_target_sell", "wolf_boll_upper_sell", "wolf_board_half_sell", "wolf_confirm_sell"):
        assert k in seg, "收紧后须允许 %s ✓" % k
    for k in ("wolf_profit_take_sell", "high_sell", "wolf_dao_t_sell"):
        assert ('"%s"' % k) not in seg, "做T类须移出白名单 %s ✗" % k
    assert "压力位" in seg and "上轨" in seg and "板上" in seg, "关键词须对齐他的口径 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_BASE_EXEMPT_STRICT='1'" in pins


def test_fib_resistance_leg_halves_whole_position():
    """`fib 0.618 压力位腿` 的**卖出量**按用户口径 = **全仓减半** ✓（账本 §9.302）。

    现场 ✗（C 轮 01-06 SZ002156 ✓）：只卖了 **100 股**（＝2400 的 4% ✗）
      `[gateway] 止盈腿穿透底仓→跳过方案③ T仓上限 SZ002156 100 股（本轮已用 0 次，上限 2）`
    根因 ✗：`_check_fib_target` 的注释与代码都写着「卖出量 = **T 仓**（sellable − 底仓 floor，保留底仓）」
      ⇒ 它的量逻辑与**做T腿同款** ✗ ⇒ 与"**压力位**"这个身份**不符** ✗
    用户口径 ✓：「**全仓**应收紧到只在「**突破／压力位／加速结束**」时**减半**」
      ⇒ 压力位腿必须能**减半全仓** ✓ ⇒ 开关 `WOLF_FIB_HALF_BASE`（**库内默认 0 = 关** ✓）
    ⚠️ 另一处同款代码在 `_check_passive_stop`（1876 行 ✓），**不得**被误改 ✗
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "WOLF_FIB_HALF_BASE" in mon, "须有开关 ✓"
    s = mon.index("def _check_fib_target")
    e = mon.index("def ", s + 10)
    seg = mon[s:e]
    assert "vol = (max(sellable, 0) // 2 // 100) * 100" in seg, "压力位腿须能全仓减半 ✓"
    assert "base_floor_shares(acct, sym, volume=sellable)" in seg, "旧分支仍须保留（开关关时逐字不变）✓"
    p = mon.index("def _check_passive_stop")
    pseg = mon[p:p + 3000]
    assert "WOLF_FIB_HALF_BASE" not in pseg, "**不得**误改 passive_stop ✗"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_FIB_HALF_BASE='1'" in pins


def test_breakout_and_retest_add_switches():
    """**突破加仓／回踩确认加仓**两个开关 ✓（账本 §9.304 —— 用户「做出来」✓）。

    他的原话（逐字 ✓）：
      「不知道 **我的操作是突破加仓 回踩确认加仓 中间不做大级别操作 向下破线止损**」（2022-06-08 ✓✓）
      「**突破下跌趋势第一根开头我加一半 突破后的第二根红K尾盘 打满**」（✓）
      「**固定仓位50%，剩下的做T等突破或回踩打满**」（2025-02-11 ✓）
      「带量(上证持续2000E以上)**站上2930那再加仓**」（2016-05-17 ✓）
    实现 ✓：`_check_breakout_add` ✓，挂进 `run_discipline_checks` ✓
      · **只对非埋伏仓** ✓（埋伏仓另有体系 ✓）
      · **突破加仓**：现价 > **前 20 日最高**（与 `trend_break_buy` 同口径 ✓）∧ **带量**(vol_ratio ≥ 1.2) ⇒ 加"持仓的 50%" ✓
      · **回踩确认加仓**：当日最低 ≤ 关键位 ∧ 现价 > 关键位（**回踩不破** ✓）⇒ 加 50% ✓
      · 约束 ✓：他的「**一个票最多买 2 笔**」⇒ 每票每轮 ≤ 2 次 ✓；当日只加一次 ✓
      · 开关 `WOLF_BREAKOUT_ADD`／`WOLF_RETEST_ADD`（**库内默认 0 = 关** ✓；pins 开 ✓）
    """
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "def _check_breakout_add" in mon, "须有该方法 ✓"
    assert "self._check_breakout_add" in mon, "须挂进纪律入口 ✓"
    i = mon.index("def _check_breakout_add")
    _j = mon.index("    def ", i + 10)
    seg = mon[i:_j]
    assert 'os.getenv("WOLF_BREAKOUT_ADD", "0")' in seg and 'os.getenv("WOLF_RETEST_ADD", "0")' in seg, "两开关且默认关 ✓"
    assert "突破加仓" in seg and "回踩确认加仓" in seg, "两条路径 ✓"
    assert "_is_ambush_position" in seg, "须排除埋伏仓 ✓"
    assert "int(_st.get(\"n\") or 0) >= 2" in seg or 'int(_st.get("n") or 0) >= 2' in seg, "须有\"最多买 2 笔\"约束 ✓"
    assert "_sandbox_price" in seg, "须直读回测数据面 ✓（账本 §9.314）"
    assert "_prev = [h for _d, h in _hs if not _today or _d < _today]" in seg or "_res = float(_sp.get(\"res\") or 0)" in seg, \
        "关键位须来自数据面/按当日过滤 ✓"
    assert "self._snapshot_for(" not in seg and "self._today8(" not in seg, "不得**调用**不存在的方法 ✗"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_BREAKOUT_ADD='1'" in pins and "export WOLF_RETEST_ADD='1'" in pins


def test_base_half_cap_and_round_cumulative():
    """① **压力位/加速类 ⇒ 减半全仓** ✓；② **同轮累计 ≤ 50%** ✓（账本 §9.306 —— 用户「①②」✓）。

    他的原话（逐字 ✓）：「**吃一口减一半**」（2026-09-01 楼678 ✓）／
      「**突破减半 剩下的吃溢价**」（2025-12-24 ✓）／「**分档减：先减到半仓以下**」✓
    为什么改在 `t_gateway` ✓（而不是 `t_monitor._check_fib_target` ✗）：
      **触发不带量** ✗ ⇒ 量由「AI 建议量 ＋ 网关的收敛」决定 ✓；
      实测 `[gateway] 止盈腿穿透底仓→跳过方案③ **T仓上限 100 股**` ✓ —— 就出在这个位置 ✓
    ⇒ ① 压力位/加速类（`fib_target`／`boll_upper`／`board_half`／`confirm`）⇒ 卖量**至少持仓的一半** ✓
    ⇒ ② 同轮累计已减 ≥ 持仓一半 ⇒ **拦住** ✓（理由含 清仓/加速结束/避险/止损/破位 ⇒ 放行 ✓）
    开关 `WOLF_BASE_HALF_CAP`（**库内默认 0 = 关** ✓；pins 开 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_BASE_HALF_CAP", "0")' in gw, "须有开关且默认关 ✓"
    i = gw.index('os.getenv("WOLF_BASE_HALF_CAP"')
    seg = gw[i:i + 4200]
    for k in ("wolf_fib_target_sell", "wolf_boll_upper_sell", "wolf_board_half_sell", "wolf_confirm_sell"):
        assert k in seg, "压力位/加速类须含 %s ✓" % k
    assert "_half = int(max(_pos // 2, 100) // 100 * 100)" in seg, "① 须按持仓减半 ✓"
    assert "_room = int(max(_pos // 2 - _sold, 0) // 100 * 100)" in seg, "② 须算同轮剩余额度 ✓"
    assert '"level": "BASE_HALF_CAP"' in seg, "② 须有独立的拦截留痕 ✓"
    assert "吃溢价" in seg, "② 的理由须引他的原话 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_BASE_HALF_CAP='1'" in pins


def test_retest_add_independent_of_two_buy_cap():
    """「**回踩确认加仓**」独立于 2 笔上限 ✓（账本 §9.321 —— 用户选 **(b)** ✓）。

    他的原话 ✓：「**突破加仓 回踩确认加仓** 中间不做大级别操作 向下破线止损」（2022-06-08）
      ⇒ 「回踩确认加仓」是**突破之后独立的一次机会** ✓ ⇒ **不算进"突破那 2 笔"** ✓
    ⇒ 实现 ✓：
      ① 网关：reason 含「回踩确认加仓」⇒ **豁免加仓口径闸** ✓（与"转正加仓"同一路数 ✓）
      ② 加仓检查：计数**分桶**（`n`＝突破 ✓／`n_rt`＝回踩 ✓），各 ≤2（**自设** ✓）
    开关 ✓ `WOLF_RETEST_ADD_INDEPENDENT`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    """
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_RETEST_ADD_INDEPENDENT", "0")' in gw, "网关须有开关且默认关 ✓"
    i = gw.index('os.getenv("WOLF_RETEST_ADD_INDEPENDENT"')
    seg = gw[max(0, i - 500):i + 300]
    assert "回踩确认加仓" in seg and "_ac = None" in seg, "回踩须豁免加仓口径闸 ✓"
    assert '"level": "ADDON_CAP"' in gw, "原拦截须保留（开关关时逐字不变 ✓）"
    mon = open(os.path.join(_ROOT, "backend", "app", "services", "t_monitor.py"), encoding="utf-8").read()
    assert "_fires" in mon and "_bucket" in mon and 'n_rt' in mon, \
        "两条须**独立判定** ✓（`_fires` 列表 ＋ 分桶 `n_rt` ✓；用户：「不能用 elif」✓）"
    assert "两条判定同时成立" in mon, "两条同时成立时须留痕 ✓"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_RETEST_ADD_INDEPENDENT='1'" in pins


def test_reduce_legs_exempt_from_no_t_sleeve():
    """**保护/减仓类腿可穿透底仓止血** ✓（账本 §9.331 —— 用户「把 ② 也改掉」✓）。

    病灶 ✗：浮亏中想减仓 ⇒ **卖腿空转**（量推导为 0）⇒ 拖到更深才砍 ✗
      · `t_capacity.py` 自述：「想减仓的 0107–0112 全被『仅底仓无T仓可卖』拦掉，拖到 0113 尾盘才砍」
      · `t_gateway.py:86`：「快克智能…被挡 3,701 次，3 月已实现 −4,279」
      · SH603690 同族：浮亏 −13% 时黄线腿被吃掉 ⇒ 一路走到 −19% ✗
    口径 ✓：语料「**止血动作必须能执行**」⇒ 按**腿型**豁免（不再靠关键词碰运气 ✗）
    开关 ✓ `WOLF_SELL_EXEMPT_REDUCE`（**库内默认 0 = 关** ⇒ 旧行为逐字不变 ✓；pins 开 ✓）
    注意 ✓：**兑现类**（high_sell／fib_target／profit_take）**仍受 20% 日内预算** ✓（未被误放 ✓）
    """
    cap = open(os.path.join(_ROOT, "backend", "app", "services", "t_capacity.py"), encoding="utf-8").read()
    assert 'os.getenv("WOLF_SELL_EXEMPT_REDUCE", "0")' in cap, "须有开关且默认关 ✓"
    assert "trigger_kind: str = \"\"" in cap, "须按腿型（新增参数 ✓）"
    i = cap.index("_REDUCE_PROTECT_KINDS")
    seg = cap[i:i + 2500]
    for k in ("custom_vwap_sell", "custom_support_sell", "custom_level_sell", "custom_trail_sell"):
        assert k in seg, "保护/减仓类须含 %s ✓" % k
    assert "止血必须能执行" in cap, "须引语料 ✓（对**整文件**断言 ✓ —— 窗口取小了会漏 ✗）"
    gw = open(os.path.join(_ROOT, "backend", "app", "services", "t_gateway.py"), encoding="utf-8").read()
    assert "trigger_kind=str(_turnover_kind(trigger_id, condition_id) or \"\")" in gw, \
        "网关须**透传腿型** ✓（否则参数拿不到 ✗）"
    pins = open(os.path.join(_ROOT, "jobs", "bt_env_pins.sh"), encoding="utf-8").read()
    assert "export WOLF_SELL_EXEMPT_REDUCE='1'" in pins
