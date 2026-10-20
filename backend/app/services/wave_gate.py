# -*- coding: utf-8 -*-
"""wave_gate.py —— **只用狼大的浪型口径当环境门**（账本 §9.659 ✓）

## 为什么（用户 2026-10-06 三条指令 ✓）

> 「**只依据 wave_state**，wave_state **缺失就现场补**，**去掉这个**」

- 「这个」= `t_regime`（`ACTIVE/CAUTIOUS/HALT` 三态 ✓）
  · 它的依据写在 `t_regime.py` 的 docstring 里：**`final-t-plan.md §⑤` ＋ `spec t-regime-gate`** ✗
    （= **我们自己的 spec** ✓，判据是 `market_diagnosis` 统计评分 ＋ MA20/60 ＋ 量能 ✗）
  · ⇒ **不是狼大的语料** ✗（按用户铁律「只对齐狼大,不自己私自加规则」⇒ 应去掉 ✓）
- ★ **狼大的口径在 `apps/main_line/wolf_context.py:600 index_level_stop()`** ✓
  · 依据他的原话 ✓：「**只看指数大级别**…如果不走大5浪而**转为下跌1浪就止损**」✓
  · 判据 = **`wave_state.level == "down"`** ✓（**只取大级别** ✓，不掺 `sub_level`/`operation` ✓）
  · 且**自带过期护栏**（`max_stale_days` ✓）

## 本模块做什么 ✓

`check_gate(kind)` ⇒ 与旧 `t_regime.check_gate` **同签名同返回形状** ✓：
`{"gate": "ALLOWED"|"BLOCKED"|"MANUAL_ONLY", "why": ...}` ✓
- **`wave_state` 缺失/过期** ⇒ ★ **现场补** ✓（调 `wave_level.judge_wave(as_of)` ✓ 并**写回** ✓）
- 补不出来 ⇒ **放行（ALLOWED）** ✓ 并**大声告警** ✓（绝不因缺数据而拦死 ✓）

开关 ✓：`WOLF_WAVE_GATE_ONLY=1` ⇒ 用本模块 ✓（**库内默认 0** ⇒ 生产行为不变 ✓；回测 pins 置 1 ✓）
"""
from __future__ import annotations
import re
import json
import os
import sys
from typing import Any, Dict, Optional

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # backend/
_REPO = os.path.dirname(_APP)
for _p in (os.path.join(_REPO, "apps", "main_line"), _REPO, _APP):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def enabled() -> bool:
    return str(os.getenv("WOLF_WAVE_GATE_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")


def _wave_path() -> str:
    """`wave_state.json` 的位置 ✓（按 `DATA_DIR` ⇒ 沙箱内 ✓）。"""
    d = os.getenv("DATA_DIR") or os.path.join(_REPO, "data")
    return os.path.join(d, "wave_state.json")


def _load_wave() -> Optional[dict]:
    try:
        with open(_wave_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as _e:
        print("[wave_gate] 读 wave_state 失败: %s" % str(_e)[:80], file=sys.stderr, flush=True)
        return None


def _stale_days(wave: dict) -> Optional[int]:
    try:
        import datetime as _dt
        d = str((wave or {}).get("date") or "").replace("/", "-")
        if not d:
            return None
        t = _dt.date.fromisoformat(d[:10]) if "-" in d else _dt.datetime.strptime(d[:8], "%Y%m%d").date()
        # ★ 账本 §9.659 修正 ✓（自测踩到 ✗）：**必须用"被钉住的钟"** ✓
        #   回测里 `pin_clock(as_of)` 已把 `datetime.now()` 钉到交易日 ✓
        #   而 `date.today()` 读**真实日期** ✗ ⇒ 会把"当日新鲜"的档误判成"过期 273 天" ✗
        now = _dt.datetime.now().date()
        return (now - t).days
    except Exception as _e:
        print("[wave_gate] 新鲜度解析失败: %s" % str(_e)[:70], file=sys.stderr, flush=True)
        return None


def _find_archived(sim8: str) -> Optional[dict]:
    """按**归档链**找当天的 wave_state ✓（账本 §9.710 ✓）。

    ★ 用户 2026-10-07 纠正 ✓：「我们哪有中文浪名，用的不是统一的 **4-3 side** 这种吗」✓
      ⇒ 真实格式（归档原文 ✓）：`level: d3` ＋ `sub_level: 3-4` ＋ `operation: t_only|build|side|defense|exit`
      ⇒ 我先前把"现场补"接到了 **`wave_level.judge_wave`（本地判官 ✗）** ⇒ 它给的是**中文浪名** ✗
        ⇒ 与闸门要的 `level` 格式**对不上** ⇒ 永远"沿用旧档" ✗（而那份旧档可能陈旧很多天 ✗）

    说明 ✓：**每天的 wave_state 早就存在** ✓（跑批按天复制 ✓；原始在 `data/_bt_year/<day>/` ✓）
      ⇒ 闸门**根本不需要现场补** ✗ ⇒ 先按归档链找 ✓，找到就用 ✓（四层 ✓，全部只读文件 ✓）：
        ① `DATA_DIR/wave_state.json`（当天档 ✓）
        ② `DATA_DIR/wave_state_<YYYY-MM-DD>.json`（带日期档 ✓）
        ③ `<arm_root>/wave_state_history/<sim8>.json`（历史目录 ✓）
        ④ 原始天目录 `data/_bt_year/<sim8>/wave_state.json`（跑批的复制源 ✓）
    """
    import glob as _g
    cands = []
    _d = os.environ.get("DATA_DIR") or ""
    if _d:
        _d = str(_d).rstrip("/")
        cands.append(os.path.join(_d, "wave_state.json"))
        if len(sim8) == 8:
            cands.append(os.path.join(_d, "wave_state_%s-%s-%s.json" % (sim8[:4], sim8[4:6], sim8[6:8])))
        _root = os.path.dirname(_d)
        if len(sim8) == 8:
            cands.append(os.path.join(_root, "wave_state_history", sim8 + ".json"))
            cands.append(os.path.join(_root, "wave_state_by_day", sim8 + ".json"))
            cands.append(os.path.join(_root, sim8, "wave_state.json"))
    if len(sim8) == 8:
        # ★ §9.710 修正 ✓：跑批的**复制源**是 `<...>/data/_bt_year/<day>/`
        #   （`DATA_DIR` = `<...>/data/_bt_t35d/<day>` ⇒ 往上两级是 `<...>/data` ✓）
        #   ⚠️ 不能用 `_REPO` ✗ —— 它是 **backend 目录**（实测 ✓），不是仓库根 ✗
        _d2 = str(os.environ.get("DATA_DIR") or "").rstrip("/")
        if _d2:
            _data_dir = os.path.dirname(os.path.dirname(_d2))      # <...>/data ✓
            cands.append(os.path.join(_data_dir, "_bt_year", sim8, "wave_state.json"))
        cands.append(os.path.join(os.path.dirname(_REPO), "data", "_bt_year", sim8, "wave_state.json"))
    for c in cands:
        try:
            if not os.path.exists(c):
                continue
            with open(c, encoding="utf-8") as f:
                j = json.load(f) or {}
            if str(j.get("level") or "").strip():
                print("[wave_gate] 归档命中 ✓ %s（level=%s op=%s）" % (c[-58:], j.get("level"), j.get("operation")),
                      file=sys.stderr, flush=True)
                return j
        except Exception as _e_c:
            print("[wave_gate] 归档读取失败 %s: %s" % (str(c)[-40:], str(_e_c)[:60]), file=sys.stderr, flush=True)
    return None


def ensure_wave(as_of: Optional[str] = None, max_stale_days: int = 10) -> Optional[dict]:
    """读 `wave_state` ✓；**缺失/过期 ⇒ 现场补** ✓（并写回 ✓）。补不出来返回 None ✓。"""
    w = _load_wave()
    age = _stale_days(w) if w else None
    # ★★ 账本 §9.738 ✓：原判据**只有上界** ✗（`age <= max_stale_days`）
    #   ⇒ 归档来自**模拟日之后**（真实世界的档 ✗）时 age 为**负** ⇒ `-231 <= 10` **为真** ✗
    #   ⇒ 实测:模拟日 2026-01-15 取到 **date=2026-04-03 / operation=defense** ✗ ⇒ 假 HALT ✗
    #   ⇒ 修 ✓：**加下界** —— 归档日期必须 **≤ 模拟日**（0 <= age）✓
    ok = bool(w) and (age is None or (0 <= age <= max_stale_days))
    if ok:
        return w
    # ★ §9.710 ✓：先按**归档链**找当天的真实档 ✓（每天的档早就存在 ✓ ⇒ 不需要现场补 ✗）
    _aw = _find_archived(sim_day8())
    if _aw:
        try:
            os.makedirs(os.path.dirname(_wave_path()), exist_ok=True)
            with open(_wave_path(), "w", encoding="utf-8") as f:
                json.dump(_aw, f, ensure_ascii=False, indent=1)
            print("[wave_gate] ✅ 已用归档回填 %s（level=%s ✓）" % (_wave_path()[-46:], _aw.get("level")),
                  file=sys.stderr, flush=True)
        except Exception as _e_w2:
            print("[wave_gate] 回填写回失败（不影响判据 ✓）: %s" % str(_e_w2)[:60], file=sys.stderr, flush=True)
        return _aw
    print("[wave_gate] wave_state %s ⇒ **现场补**（as_of=%s ✓）"
          % ("缺失" if not w else "过期 %s 天" % age, as_of or "-"), file=sys.stderr, flush=True)
    # ★ 2026-10-20 修（用户要求）：原「现场补」走的是 **wave_level.judge_wave**（旧 LLM 判据 ✗，已退役）
    #   ⇒ 换成**浪型链回放**（带单向锁 ✓，与 live/卡片同源 ✓）。纪律：只读不写盘 ✓（写权归 08:10 任务），
    #   拿不到 ⇒ None ⇒ 上层放行 ✓（绝不因缺数据拦死 ✓）。
    try:
        from app.services.wave_ladder_gate import ladder_state
        got = ladder_state(as_of or (sim_day8() or None))
        if got and got.get("level"):
            print("[wave_gate] ✅ 现场补（浪型链回放）：date=%s level=%s op=%s" % (
                got.get("date"), got.get("level"), got.get("operation")), file=sys.stderr, flush=True)
            return got
        print("[wave_gate] ✗ 浪型链回放也拿不到 ⇒ **放行**", file=sys.stderr, flush=True)
        return None
    except Exception as _e_c:
        print("[wave_gate] ✗ 现场补失败: %s ⇒ **放行**（绝不因缺数据拦死 ✓）" % str(_e_c)[:120],
              file=sys.stderr, flush=True)
        try:
            from app.services import alert_hub as _ah
            _ah.note("wave_gate.现场补失败", msg="%s（放行 ✓）" % str(_e_c)[:160])
        except Exception as _e_a:
            print("[wave_gate] 告警失败: %s" % str(_e_a)[:60], file=sys.stderr, flush=True)
        return None
    except Exception as _e_c:
        print("[wave_gate] ✗ 现场补失败: %s ⇒ **放行**（绝不因缺数据拦死 ✓）" % str(_e_c)[:120],
              file=sys.stderr, flush=True)
        try:
            from app.services import alert_hub as _ah
            _ah.note("wave_gate.现场补失败", msg="%s（放行 ✓）" % str(_e_c)[:160])
        except Exception as _e_a:
            print("[wave_gate] 告警失败: %s" % str(_e_a)[:60], file=sys.stderr, flush=True)
        return None


def sim_day8() -> str:
    """当前**模拟日**（回测 = `DATA_DIR` 末段 ✓；生产退化为今天 ✓）。账本 §9.709 ✓。"""
    try:
        _seg = os.path.basename(os.path.normpath(os.environ.get("DATA_DIR", "") or ""))
        if len(_seg) == 8 and _seg.isdigit():
            return _seg
    except Exception as _e_seg:
        print("[wave_gate] sim_day8 取 DATA_DIR 末段失败: %s" % str(_e_seg)[:70], flush=True)
    try:
        import datetime as _d8
        return _d8.datetime.now().strftime("%Y%m%d")
    except Exception as _e_d8:
        print("[wave_gate] sim_day8 取今天失败: %s" % str(_e_d8)[:70], flush=True)
        return ""


def gate_regime(kind: str = "low_buy") -> Dict[str, Any]:
    """旧 `t_regime.compute_regime` 的**波浪版**替身 ✓（同键 ✓，供 t_bridge / t_pool / t_monitor 复用 ✓）。"""
    g = check_gate(kind, as_of=sim_day8() or None)
    _gate = g.get("gate")
    return {"regime": "HALT" if _gate == "BLOCKED" else "ACTIVE",
            "gate_low_buy": _gate, "gate_high_sell": _gate,
            "interpret_sign": 1, "index_drop": 0.0,
            "state": g.get("mode") or "auto", "why": g.get("why"), "src": "wave_gate"}


def wave_summary(kind: str = "low_buy", wave: Optional[dict] = None,
                 as_of: Optional[str] = None) -> Dict[str, Any]:
    """**浪型档位**的人话摘要 ✓（用户 2026-10-12：「改用浪型做判据，不要 regime/震荡」✗）

    返回键：level / sub_level / operation / date / gate / mode / allowed / why / text ✓
      · `text` 例：`d4/4-2 · t_only（只做T、不追不新建主升）｜本腿判定：允许自动 ✓`
      · 语义**照抄** `apps/main_line/wave_agent.py` 的 operation 表（狼大原文 ✓，不自己编 ✗）：
        build=建仓/追 ⇒ 放行｜t_only=只做T不追不新建主升 ⇒ 买类拦｜side=观望/调仓换股 ⇒ 买类人工确认
        defense=防御不建仓 ⇒ 买类拦｜exit=兑现降仓 ⇒ 买类拦；**卖类（high_sell*）永不拦** ✓
    """
    _OP_CN = {"build": "建仓/追（主升/上升浪 ⇒ 放行）",
              "t_only": "只做T、不追不新建主升",
              "side": "观望/调仓换股（买类需人工确认）",
              "defense": "防御不建仓",
              "exit": "兑现降仓"}
    try:
        w = wave or ensure_wave(as_of=as_of or (sim_day8() or None))
    except Exception:
        w = wave
    g = check_gate(kind=kind, wave=w, as_of=as_of) or {}
    lv = str((w or {}).get("level") or "")
    sb = str((w or {}).get("sub_level") or "")
    op = str((w or {}).get("operation") or "").strip().lower()
    tag = "%s%s" % (lv or "?", ("/" + sb) if sb else "")
    _gate = str(g.get("gate") or "")
    _verdict = {"ALLOWED": "允许自动 ✓", "MANUAL_ONLY": "需人工确认 ✗", "BLOCKED": "拦下 ✗"}.get(_gate, _gate)
    txt = "%s · %s（%s）｜本腿判定：%s" % (tag, op or "?", _OP_CN.get(op, "未知档"), _verdict)
    return {"level": lv, "sub_level": sb, "operation": op,
            "date": str((w or {}).get("date") or ""), "gate": _gate,
            "mode": str(g.get("mode") or ""), "allowed": bool(g.get("allowed")),
            "why": str(g.get("why") or ""), "text": txt}


def check_gate(kind: str = "low_buy", wave: Optional[dict] = None,
               as_of: Optional[str] = None) -> Dict[str, Any]:
    """**狼大口径**的环境门 ✓（与旧 `t_regime.check_gate` 同返回形状 ✓）。"""
    try:
        import wolf_context as _wc                       # apps/main_line/wolf_context.py ✓
    except Exception as _e_i:
        print("[wave_gate] 导入 wolf_context 失败 ⇒ 放行: %s" % str(_e_i)[:90], file=sys.stderr, flush=True)
        return {"allowed": True, "mode": "auto", "regime": "ACTIVE", "gate": "ALLOWED",
                "why": "wolf_context 不可用（放行 ✓）"}
    w = wave or ensure_wave(as_of=as_of)
    if not w:
        return {"allowed": True, "mode": "auto", "regime": "ACTIVE", "gate": "ALLOWED",
                "why": "wave_state 不可得 ⇒ 放行 ✓"}
    try:
        # ★ 账本 §9.659 修正 ✓（自测抓到 ✗，**致命**）：
        #   `index_level_stop()` 返回的是**元组** `(bool, str)` ✗
        #     ⇒ 我先前写 `bool(...)` ⇒ **`(False, "不触发")` 是"非空元组" ⇒ 恒为真** ✗✗
        #     ⇒ **把所有触发都判成 BLOCKED** ✗（回测会几乎没有交易 ✗）
        #   ⇒ 必须**解包取第一个元素** ✓（并兼容只返回 bool 的旧签名 ✓）
        # ★★ 账本 §9.738 ✓：原传 `today=None` ✗ ⇒ `if today and wd …` **直接短路** ✗
        #   ⇒ 狼大原判据里的"新鲜度护栏"**从未执行** ✓ ⇒ 过期/未来档照样能触发清仓级判断 ✗
        _raw = _wc.index_level_stop(wave=w, today=(as_of or sim_day8() or None))
        if isinstance(_raw, tuple):
            stop, _why = (list(_raw) + [""])[:2]
        else:
            stop, _why = bool(_raw), ""
    except Exception as _e_s:
        print("[wave_gate] index_level_stop 失败 ⇒ 放行: %s" % str(_e_s)[:90], file=sys.stderr, flush=True)
        return {"allowed": True, "mode": "auto", "regime": "ACTIVE", "gate": "ALLOWED",
                "why": "index_level_stop 异常（放行 ✓）"}
    # ★ 账本 §9.659 再修正 ✓（用户 QQ：`t_monitor.py:3597/4501/6201/6258  raise×N：'allowed'` ✗）：
    #   我上一版只返回 `{"gate","why"}` ✗ ⇒ 而调用方取 `gate["allowed"]` ✗ ⇒ **KeyError 刷屏** ✗✗
    #   ⇒ 必须与旧 `t_regime.check_gate` **同形状** ✓：
    #     `{"allowed": bool, "mode": "auto"|"human_confirm"|"blocked", "regime": str}` ✓
    #     （`gate`／`why` 作为**附加键**保留 ✓，不影响调用方 ✓）
    # ★★ 账本 §9.712 ✓（用户口径原文：「**止损只在指数大级别破位**」✓）：
    #   ⇒ 指数大级别破位是**触发止损的时机** ✗，**不是"禁止卖出"** ✓
    #   ⇒ ⇒ **卖类（`high_sell*`）永不拦** ✓ —— 这是今晚验证里发现的真问题 ✗
    #     （旧实现在 down 时把买、卖一起 BLOCKED ✗ ⇒ 破位时**反而卖不出去** ✗，与口径相反 ✓）
    _kind_s = str(kind or "low_buy")
    # ★ 2026-10-20 修：原来只豁免 `high_sell` / `high_sell_then_buy_back` 两个**精确名字** ✗，
    #   而全仓还在用 **`high_sell_strong`**（10 处 ✓）⇒ 它落进下面的指数止损分支 ⇒ 被 BLOCKED ✗✗
    #   （"破位时反而卖不出去" ✗ —— 正是本函数注释里说已修过的那类事故 ✓）。改成**前缀匹配** ✓。
    if _kind_s.startswith("high_sell"):
        return {"allowed": True, "mode": "auto", "regime": "down",
                "gate": "ALLOWED",
                "why": "卖类不受环境门限制 ✓（口径：破位是止损的**触发**，不是禁止卖出 ✓）：%s"
                       % str(_why or w.get("level"))[:60]}
    if bool(stop):
        return {"allowed": False, "mode": "blocked", "regime": "down" if False else "HALT",
                "gate": "BLOCKED",
                "why": "指数大级别转下跌1浪（狼大口径 ✓）：%s" % str(_why or w.get("level"))[:80]}
    # ★★ 账本 §9.711 ✓（用户 2026-10-07：「接进去」✓ —— 把**真实的 `operation`** 接进闸门 ✓）：
    #   语义**照抄** `apps/main_line/wave_agent.py:259-272`（狼大口径原文 ✓，不自己编 ✗）：
    #     build    建仓/追（主升、d3+3-3、d1/上升浪、W底确认）        ⇒ 买类 **放行** ✓
    #     t_only   只做T**不追不新建主升**（4-4、4-2/B反、3-4、ABC高位）⇒ 买类 **拦** ✗（T仓仍可动 ✓）
    #     side     观望/调仓换股（4-3筑底、大2、ABC、3-2回调…）        ⇒ 买类 **MANUAL_ONLY**（人工确认 ✓）
    #     defense  防御**不建仓**                                     ⇒ 买类 **拦** ✗
    #     exit     兑现**降仓**（d5/末段防顶）                        ⇒ 买类 **拦** ✗
    #   ★ **卖类（`high_sell*`）永不拦** ✓ —— 与全仓既有原则一致（止血/兑现动作必须能执行 ✓）
    #   开关：`WOLF_WAVE_OP_GATE`（**库内默认 0** ⇒ 生产零影响 ✓；回测由 pins 置 1 ✓）
    _op_on = str(os.getenv("WOLF_WAVE_OP_GATE", "0")).strip().lower() in ("1", "true", "yes", "on")
    if _op_on:
        _kind = str(kind or "low_buy")
        _is_sell = _kind.startswith("high_sell")      # ★ 同上：前缀匹配（含 high_sell_strong ✓）
        _op = str((w or {}).get("operation") or "").strip().lower()
        if (not _is_sell) and _op in ("t_only", "defense", "exit"):
            return {"allowed": False, "mode": "blocked",
                    "regime": str((w or {}).get("level") or ""),
                    "gate": "BLOCKED",
                    "why": "浪型 operation=%s（狼大口径：%s）⇒ 买类不放行 ✓"
                           % (_op, {"t_only": "只做T不追不新建主升", "defense": "防御不建仓",
                                    "exit": "兑现降仓"}.get(_op, ""))}
        if (not _is_sell) and _op == "side":
            return {"allowed": True, "mode": "human_confirm",
                    "regime": str((w or {}).get("level") or ""),
                    "gate": "MANUAL_ONLY",
                    "why": "浪型 operation=side（观望/调仓换股）⇒ 买类需人工确认 ✓"}
        if (not _is_sell) and not _op:
            print("[wave_gate] operation 缺失 ⇒ 买类按 level 判（保守 ✓）", file=sys.stderr, flush=True)
    return {"allowed": True, "mode": "auto", "regime": "ACTIVE",
            "gate": "ALLOWED",
            "why": str(_why or ("wave_state.level=%s（非 down ✓）" % str(w.get("level"))))[:100]}


# ══════════════════════════════════════════════════════════════════════════
# **条件命中自动执行**也要过浪型检查（账本 §9.746 ✓）
#
# 用户 2026-10-07：「**条件命中自动执行也要过波浪浪型检查**」✓
#
# 背景 ✗：自动执行那条路（`t_monitor` 里 `reason=条件命中自动执行（<kind>）` ✓）
#   原先**不经过任何浪型闸** ✗ —— 它只过「开盘不追高 / B·C 买点 / G3」等闸 ✓
#   ⇒ 实测：`t_only` 那几天仍成交 `trend_break_buy`（趋势突破建仓 ✗）
#     （SH688403 汇成 ¥101,420 ✗、以及 0105 SZ002156 ¥98,575、0114 SZ300346 ¥96,509 ✓）
#   ⇒ 而狼大口径原文（`apps/main_line/wave_agent.py:259-272` ✓）明说：
#       `t_only` ⇒ 「只做T、**不追不新建主升**」✓ ⇒ **趋势突破建仓正是"新建主升"** ✗
#
# 判定（**逐字照抄狼大，不自己编** ✓）：
#   operation = build     ⇒ 放行 ✓（建仓/追正是它允许的 ✓）
#   operation = defense   ⇒ **拦所有买** ✗（防御不建仓 ✓）
#   operation = exit      ⇒ **拦所有买** ✗（兑现降仓 ✓）
#   operation = side      ⇒ **不自动执行** ✗（观望/调仓换股 ⇒ 买类需人工确认 ✓，
#                            自动路径没法问人 ⇒ 交给 AI/人工，不由条件单直接落 ✓）
#   operation = t_only    ⇒ 按**腿型**分：
#                             · T 腿（`wolf_zheng_t_buy` 等 ✓）⇒ **放行** ✓（它就是"只做T" ✓）
#                             · 建仓/主升腿（`trend_break_buy` / `wolf_build` /
#                               `buy_253` / `buy_254` ✓）⇒ **拦** ✗（不新建主升 ✓）
#                             · 埋伏腿（`*ambush*` ✓）⇒ 由 `WOLF_WAVE_COND_AMBUSH` 定 ✓
#                               （**默认 block** ✓ —— 它是**新开仓** ✓；要放行设 allow ✓）
#   operation 缺失        ⇒ **放行** ✓ ＋ 打一行日志 ✓（不静默整片禁买 ✗）
#
# 开关 ✓：`WOLF_WAVE_COND_GATE`（**库内默认 0** ⇒ 生产逐位不变 ✓；回测由 pins 置 1 ✓）
# 卖类 ✓：**永不由本函数拦** ✗（调用方只对买入方向调用 ✓；止血/兑现必须能执行 ✓）
# ══════════════════════════════════════════════════════════════════════════

# 腿型分类（按 trigger_kind ✓；命中即归类 ✓）
_T_LEG_KEYS = ("wolf_zheng_t_buy", "zheng_t", "t_buy", "sell_then_buy_back", "t_low_buy")
# ★ 2026-10-13：补入**低吸建仓**类腿型 ✓（原先 `low_buy` 落进 other ⇒ 浪型软处理覆盖不到 ✗；
#   归类本身不改变行为 ✗：SOFT=0 时 t_only/defense 对 build 与 other 都仍是"拦" ✓）
_BUILD_LEG_KEYS = ("trend_break_buy", "wolf_build", "buy_253", "buy_254", "build",
                   "low_buy", "dip_buy", "pullback_buy")
_AMBUSH_LEG_KEYS = ("ambush",)


def classify_buy_leg(kind: str) -> str:
    """把买入腿型归成 `t` / `build` / `ambush` / `other` ✓（只按名字 ✓，不猜语义 ✓）"""
    k = str(kind or "").strip().lower()
    if any(x in k for x in _AMBUSH_LEG_KEYS):
        return "ambush"
    if any(x in k for x in _T_LEG_KEYS):
        return "t"
    if any(x in k for x in _BUILD_LEG_KEYS):
        return "build"
    return "other"


def _wave_from_sandbox_day(sim8: str) -> Optional[dict]:
    """★ 账本 §9.755 ✓（2026-10-07 实测漏放 ✗）：直接从**当天沙箱天目录**读档 ✓

    为什么必须补 ✗：`ensure_wave` 走的是**归档链**（`_bt_year/<sim8>/wave_state.json` 等 ✓），
      **不含沙箱天目录** ✗ ⇒ 跑批里 `DATA_DIR` 指向别处时查不到 ✗ ⇒ 浪型为空 ⇒
      `cond_buy_wave_block` 走「operation 缺失 ⇒ 放行」⇒ **该拦的建仓腿被漏放** ✗
      实测 ✗：`2026-02-06 SH600986 trend_break_buy ¥101,104`（当天 `op=t_only` ✗）
        触发记录原文：`自动执行 buy 7100股 @14.24: success | 条件命中自动执行 | **level=None**` ✗
    候选（按优先级 ✓，全部**as-of 正确**：天目录里的那份就是当天该用的档 ✓）：
      `$BT_ROOT/<sim8>/wave_state.json` ⇒ `data/_bt_t35d/<sim8>/wave_state.json`
      ⇒ `data/<sim8>/wave_state.json`
    """
    if not sim8:
        return None
    # ★★ 账本 §9.755 补 ✓（用户 2026-10-07「生产不受影响吧」✗）：**仅回测模式启用** ✓
    #   为什么必须加这道护栏 ✗：本函数会去看 `data/_bt_t35d/<日期>/wave_state.json` 这类
    #     **回测沙箱产物** ✗ ⇒ 生产若在同机部署、且沙箱里恰好有"今天"的档 ✗
    #     ⇒ 生产就可能**误读回测档** ✗（口径被回测数据污染 ✗）—— 这是绝不能有的 ✗
    #   ⇒ 判据 ✓：只要进程带任何**回测专用**环境变量 ⇒ 才认为是回测 ✓
    #      （`BT_ROOT` / `WOLF_SIM_DAY` / `BT_ASOF_DAY` / `BT_ASOF_STATE` / `BT_NET_OFFLINE=1`）
    #   ⇒ 生产（这些都不设 ✓）⇒ 本函数**直接返回 None** ✓ ⇒ 回落到 `ensure_wave` ✓
    #      ＝ **生产行为逐位不变** ✓（守"生产零影响"✓）
    _bt = any(str(os.getenv(k) or '').strip() for k in
              ('BT_ROOT', 'WOLF_SIM_DAY', 'BT_ASOF_DAY', 'BT_ASOF_STATE'))
    if not _bt:
        _bt = str(os.getenv('BT_NET_OFFLINE', '')).strip().lower() in ('1', 'true', 'yes', 'on')
    if not _bt:
        return None
    d8 = str(sim8).replace('-', '')[:8]
    dash = '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:8]) if len(d8) == 8 else d8
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    roots = []
    _r = str(os.getenv('BT_ROOT') or '').strip()
    if _r:
        roots.append(_r)
    roots += [os.path.join(repo, 'data', '_bt_t35d'), os.path.join(repo, 'data')]
    for _root in roots:
        for name in ('wave_state.json', 'wave_state_%s.json' % dash):
            c = os.path.join(_root, d8, name)
            try:
                if not os.path.exists(c):
                    continue
                with open(c, encoding='utf-8') as fh:
                    j = json.load(fh) or {}
                if str(j.get('level') or '').strip():
                    print('[wave_gate] 条件单浪型闸：天目录命中 ✓ %s（level=%s op=%s ✓）'
                          % (c[-52:], j.get('level'), j.get('operation')), file=sys.stderr, flush=True)
                    return j
            except Exception:
                continue
    return None


def _cond_fail_policy() -> str:
    """浪型**取不到/无法判定**时的策略：`open`（默认，放行 ＋ 留痕 ✓）/ `close`（保守拦截 ✓）。

    ★ 2026-10-10（用户「改」✓）：实测事故 —— 回测里 LLM 隧道不在 ⇒ 当天 `wave_state` 缺失 ✗
      ⇒ `op` 为空 ⇒ **fail-open 放行** ✗ ⇒ `t_only` 那一天照样成交 `trend_break_buy`（正邦科技 ✗）
      （同日志还能看到`时间窗外→本轮不发腿`只挡时间窗 ✓、浪型闸那道口子却静默放行 ✗）
    ⇒ 回测/批量场景应取 **close**（保守拦截 ✓）；生产默认仍是 **open** ⇒ **行为逐位不变** ✓
    """
    return str(os.getenv("WOLF_WAVE_COND_FAIL", "open")).strip().lower()


_FAIL_ALERTED: dict = {}
_FAIL_COUNT: dict = {}


def _cond_is_backtest() -> bool:
    """是否回测/批量环境（与 `_wave_from_sandbox_day` 同判据 ✓）—— 回测不推 QQ（避免刷屏 ✗）。"""
    for k in ("BT_ROOT", "WOLF_SIM_DAY", "BT_ASOF_DAY", "BT_ASOF_STATE"):
        if str(os.getenv(k) or "").strip():
            return True
    return str(os.getenv("BT_NET_OFFLINE", "")).strip().lower() in ("1", "true", "yes", "on")


def _cond_fail_alert(kind: str, op: str, tag: str, decision: str, where: str) -> None:
    """浪型**不可用** ⇒ **推送 QQ**（每天一次 ✓ — 用户 2026-10-10：「不可用的时候推送QQ，不建仓」✓）

    · 每天**只推一条** ✓（同一天多轮多腿只累计计数 ✓，避免刷屏 ✗）
    · 回测环境**不推** ✓（`BT_ROOT`/`WOLF_SIM_DAY`/… 在场 ⇒ 只落盘计数 ✓；要推设 `WOLF_WAVE_COND_QQ_BT=1` ✓）
    · 推送失败只留痕 ✓（绝不因为告警失败影响交易判定 ✗）
    """
    try:
        import time as _tm
        _d = _tm.strftime("%Y-%m-%d")
    except Exception:
        _d = "?"
    try:
        _FAIL_COUNT[_d] = int(_FAIL_COUNT.get(_d, 0)) + 1
    except Exception:
        pass
    if _FAIL_ALERTED.get(_d):
        return
    _FAIL_ALERTED[_d] = True
    _bt = _cond_is_backtest() and str(os.getenv("WOLF_WAVE_COND_QQ_BT", "0")).strip().lower() not in ("1", "true", "yes", "on")
    _msg = ("[浪型闸] ⚠️ 浪型**不可用**（%s）⇒ **本腿不建仓** ✓（保守口径 %s）｜腿型=%s 档=%s 当日累计=%d 次%s"
            % (where, decision, str(kind)[:28], tag, _FAIL_COUNT.get(_d, 1),
               "（回测环境：仅计数不推送 ✓）" if _bt else ""))
    print("[wave_gate] ★ " + _msg, flush=True)
    if _bt:
        return
    try:
        from app.services import alert_hub as _ah
        _ah.push_qq(_msg)
    except Exception as _e:
        try:
            print("[wave_gate] 浪型不可用告警推送失败: %s" % str(_e)[:80], flush=True)
        except Exception:
            pass


def _cond_fail_record(kind: str, op: str, tag: str, decision: str, where: str) -> None:
    """fail 决策**显式留痕 ＋ 计数** ✓（原来只有一行 stderr ⇒ 对账时看不见 ✗）"""
    try:
        import logging as _lg
        _lg.warning("[wave_gate] 条件单浪型闸 %s：kind=%s op=%r tag=%s where=%s"
                    % (decision, str(kind)[:32], op, tag, where))
    except Exception:
        pass
    try:
        import json as _js, time as _tm
        _d = str(os.getenv("DATA_DIR") or "").strip() or \
             os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                 os.path.dirname(os.path.abspath(__file__))))), "data")
        _f = os.path.join(_d, "wave_gate_events.jsonl")
        with open(_f, "a", encoding="utf-8") as _fh:
            _fh.write(_js.dumps({"ts": _tm.strftime("%Y-%m-%d %H:%M:%S"), "event": "cond_wave_fail",
                                 "decision": decision, "kind": str(kind or ""), "op": str(op or ""),
                                 "tag": tag, "where": where, "sim_day": sim_day8() or ""},
                                ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:
        print("[wave_gate] 条件单浪型闸 %s：kind=%s op=%r（%s，%s）⇒ %s"
              % (decision, str(kind)[:32], op, tag, where,
                 "拦下 ✗" if decision == "FAIL-CLOSE" else "放行 ✓"), flush=True)
    except Exception:
        pass
    # ★ 2026-10-10（用户：「不可用的时候推送QQ，不建仓」✓）：浪型不可用 ⇒ 推送 QQ（每天一次 ✓）
    try:
        _cond_fail_alert(kind, op, tag, decision, where)
    except Exception:
        pass


def cond_buy_wave_verdict(kind: str = "low_buy", as_of: Optional[str] = None, symbol: str = "") -> Dict[str, Any]:
    """★ 2026-10-13（用户：「这个要改成 agent 确认，我可没收到任何确认通知」✓）：

    把 `cond_buy_wave_block` 的"拦"细分成三种**处置**（调用方据此分流 ✓）：
      · `auto`          —— 放行（自动执行 ✓）
      · `agent_confirm` —— **浪型 side（观望/调仓换股）** ⇒ 不自动执行 ✗，**送 agent 确认** ✓
        （原先等于"人工确认"✗：既不通知、也永不执行 ✗ —— 实测生产 `[WAVE-COND] …side⇒买类需人工确认`
          连续拦了 24 次（SH600025 2026-10-13）而用户**没收到任何通知** ✗）
      · `block`         —— 硬拦（defense/exit/t_only 建仓类/FAIL-CLOSE ✓）

    开关 `WOLF_WAVE_SIDE_AGENT_CONFIRM`（**默认 1 = 开启 agent 确认 ✓**；置 0 回到旧行为：直接拦 ✗）
    """
    blocked, why = cond_buy_wave_block(kind=kind, as_of=as_of, symbol=symbol)
    op = ""
    try:
        _asof = as_of or sim_day8() or None
        w = _wave_from_sandbox_day(str(_asof or "")) or ensure_wave(as_of=_asof)
        op = str((w or {}).get("operation") or "").strip().lower()
    except Exception:
        op = ""
    if not blocked:
        mode = "auto"
    elif op == "side" and str(os.getenv("WOLF_WAVE_SIDE_AGENT_CONFIRM", "1")).strip().lower() \
            not in ("0", "false", "no"):
        mode = "agent_confirm"
    else:
        mode = "block"
    return {"blocked": bool(blocked), "mode": mode, "op": op, "why": str(why or "")}


def theme_wave_op(theme: str, as_of: Optional[str] = None, extra_dirs=None) -> tuple:
    """**主线板块浪型**（第一版：确定性派生 ✓）⇒ `(op, detail)` ✓

    为什么不能用 `trend_confirm.stage` 当浪型 ✗（用户 2026-10-13 指出「主线浪型无效」✓）：
      那个 `stage`（full_window_confirmed / suspect / not_confirmed / window_limited ✓）是
      **主升结构确认档** ✓（docstring：「上升中继结构 = 低点抬高 + 2浪回调不破前低 + 再创新高」✓，
      track_b 还只是**成分股确认比例** ✓）⇒ 它是"确认度"，**不是** level/sub_level/operation ✗

    本函数用 `track_a` 的**结构事实**按他的原话派生 op ✓（原话：「低点抬高 + 2浪回调不破前低 + 再创新高」✓）：
      · `no_break is False`（破前低 ✗）⇒ `defense`（结构破坏 ✓）
      · `new_high=True` 且 `pullback` 为合格 2 浪回调（非空 ✓）⇒ `build`（主升中继 ✓）
      · `new_high=True` 但回调不合格/缺失 ⇒ `t_only`（形态不全 ✓ ⇒ 只做T不新建主升 ✓）
      · 其余（无新高 ✓）⇒ `side`（观望 ✓）
    返回 `("", "")` 表示**取不到结构**（调用方回落大盘口径 ✓）
    """
    if not theme:
        return "", ""
    d8 = "".join(ch for ch in str(as_of or sim_day8() or "") if ch.isdigit())[:8]
    import glob as _glob
    cands = []
    _dirs = list(extra_dirs or []) + _data_dirs()
    for pat in [os.path.join(d, f) for d in _dirs
                for f in ("trend_confirm_*_long.json", "trend_confirm_*.json")]:
        cands += [f for f in _glob.glob(pat)
                  if re.search(r"(\d{8})", os.path.basename(f))
                  and (not d8 or re.search(r"(\d{8})", os.path.basename(f)).group(1) <= d8)]
    cands.sort(key=lambda f: re.search(r"(\d{8})", os.path.basename(f)).group(1), reverse=True)
    for fp in cands[:3]:
        try:
            with open(fp, encoding="utf-8") as f:
                tr = json.load(f)
        except Exception:
            continue
        for t in (tr.get("themes") or []):
            if t.get("theme") != theme:
                continue
            a = t.get("track_a") or {}
            nh, pb, nb = a.get("new_high"), a.get("pullback"), a.get("no_break")
            _why = "; ".join(str(x) for x in (a.get("reason") or [])[:2])[:70]
            if nb is False:
                return "defense", "结构破前低(%s)" % _why
            if nh and pb:
                return "build", "新高+合格2浪回调(%s)" % _why
            if nh:
                return "t_only", "新高但回调不合格(%s)" % _why
            return "side", "无新高(%s)" % _why
    return "", ""


def wave_scope() -> str:
    """★ 2026-10-13（用户拍板 ✓）：浪型判定的**范围** —— `index`（默认，现状 ✓）| `mainline`（主线板块 ✓）

    两个浪型 ✓：
      · **大盘浪型** = `wave_state`（level/sub_level/operation ✓）—— 现有实现 ✓
      · **主线板块浪型** = 当日主线/次主线主题的**结构确认档**（`trend_confirm_<d>_long.json`
        的 `themes[].track_a.stage` ✓：not_confirmed / suspect / confirmed ✓）
    口径依据（原话 ✓）：2025-06-05「…但是**主线板块筑底行情**…**急杀可以买，缓跌不买**」✓；
      2016-04-13「**看个股 不要看指数**」✓ —— 板块级为主判据 ✓
    """
    return str(os.getenv("WOLF_WAVE_SCOPE", "index")).strip().lower()


def _mainline_themes(as_of: Optional[str] = None):
    """当日主线/次主线（方向层口径 ✓，与布腿器同源 ✓）"""
    try:
        p = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "main_line_state.json")
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        ms = m.get("mainline_select") or {}
        out = [t for t in (ms.get("mainline"), ms.get("second"), m.get("main_line")) if t]
        seen = []
        for t in out:
            if t not in seen:
                seen.append(t)
        return seen
    except Exception:
        return []


def _data_dirs() -> list:
    """数据目录候选（本地仓库 / 容器 / 沙箱 ✓）—— 先前只认 DATA_DIR ⇒ 本地/单测取不到文件 ✗"""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = []
    for d in (os.environ.get("DATA_DIR"), os.path.join(here, "data"), "data", "/app/data"):
        if d and d not in out and os.path.isdir(d):
            out.append(d)
    return out


def theme_stage(theme: str, as_of: Optional[str] = None) -> str:
    """主题的**结构确认档**（track_a 优先、缺失用 track_b ✓，`trend_confirm_<d>_long.json` ✓）"""
    if not theme:
        return ""
    d8 = "".join(ch for ch in str(as_of or sim_day8() or "") if ch.isdigit())[:8]
    for cand in [os.path.join(d, f) for d in _data_dirs()
                 for f in ("trend_confirm_%s_long.json" % d8, "trend_confirm_%s.json" % d8)]:
        try:
            with open(cand, encoding="utf-8") as f:
                tr = json.load(f)
        except Exception:
            continue
        for t in (tr.get("themes") or []):
            if t.get("theme") == theme:
                return str(((t.get("track_a") or {}).get("stage"))
                           or ((t.get("track_b") or {}).get("stage")) or "")
    return ""


def symbol_mainline_stage(symbol: str, as_of: Optional[str] = None):
    """该票是否属于当日主线/次主线主题，及其结构档 ⇒ `(theme, stage)`；不属于 ⇒ `("", "")` ✓"""
    if not symbol:
        return "", ""
    ths = _mainline_themes(as_of)
    if not ths:
        return "", ""
    try:
        import sys as _s
        _s.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "apps", "main_line"))
        from sector_g3 import symbol_themes
        mine = symbol_themes(symbol) or []
    except Exception:
        mine = []
    if not mine:
        try:
            from apps.main_line.sector_g3 import symbol_themes as _st2
            mine = _st2(symbol) or []
        except Exception:
            mine = []
    for t in ths:
        if t in mine:
            return t, theme_stage(t, as_of)
    return "", ""


def wave_size_mult(op: str, stage: str = "") -> float:
    """**降档系数**（浪型不再一票否决 ⇒ 改用仓位降档 ✓）

    出处 ✓：
      · 大盘 `defense`（2025-06-05「主线板块筑底行情」以外的防御段 ✓；2016-04-29「预期大盘是跌…不用买进」✓）
        ⇒ 0.5 折（**代理值** ✗ 无语料数字 ⇒ 标注为代理 ✓）
      · 大盘 `t_only`（「只做T、不追不新建主升」✓）⇒ 0.7 折（同上，代理 ✓）
      · 板块结构 `not_confirmed`（无确认 ⇒ 不确认不重仓 ✓）⇒ 再 ×0.7（代理 ✓）
      · 板块 `confirmed` ⇒ ×1.0 ✓（「主线板块筑底行情…可以买」✓）
    开关 `WOLF_WAVE_SOFT_MULT`（默认 1 = 启用降档 ✓；置 0 ⇒ 恒 1.0 ✓）
    """
    if str(os.getenv("WOLF_WAVE_SOFT_MULT", "1")).strip().lower() in ("0", "false", "no"):
        return 1.0
    m = 1.0
    _o = str(op or "").lower()
    if _o == "defense":
        m *= 0.5
    elif _o in ("t_only", "side"):
        m *= 0.7
    _s = str(stage or "").lower()
    if _s == "not_confirmed":
        m *= 0.7
    return round(m, 4)


def cond_buy_wave_block(kind: str, as_of: Optional[str] = None, symbol: str = "") -> tuple:
    """条件单买入是否被**浪型**拦下 ⇒ `(blocked: bool, why: str)` ✓

    · 开关关（默认 ✓）⇒ 恒 `(False, "")` ✓（生产零影响 ✓）
    · ★ **卖类永不拦** ✓ —— 本函数**自带**这层保护 ✓（不依赖调用方 ✓）：
      止血/兑现/减仓必须能执行 ✓（与 `check_gate` 里"卖类不受环境门限制"同一条原则 ✓）
    · ★ **未归类**的买入（`custom_buy` 等 ✓）由 `WOLF_WAVE_COND_OTHER` 定 ✓
      （**默认 block** ✓ —— 未知腿型在 t_only 下保守处理 ✓；要放行设 allow ✓）
    """
    _k = str(kind or "").strip().lower()
    if any(t in _k for t in ("sell", "reduce")):
        return False, ""                     # ★ 卖类永不放行限制 ✓
    if str(os.getenv("WOLF_WAVE_COND_GATE", "0")).strip().lower() not in ("1", "true", "yes", "on"):
        return False, ""
    _asof = as_of or sim_day8() or None
    # ★★ 账本 §9.755 ✓（2026-10-07 实测 ✗）：**天目录优先**，归档链只作兜底 ✓
    #   为什么必须反过来 ✗：归档链（`ensure_wave`）在跑批里会命中**陈旧/无关档** ✗ ——
    #     实测 0206 那天它返回 `d3/3-5·exit（date=2026-01-14）` ✗（而当天真档是 `d3/3-4·t_only` ✓）
    #     ⇒ 先前的写法只在"operation 为空"时才兜底 ✗ ⇒ 陈旧档**非空** ⇒ 兜底不触发 ✗
    #     ⇒ 结果：①该拦的建仓腿**拦错理由** ✗ ②`exit` 语义把 **T 腿/埋伏也一起拦掉** ✗（过严 ✗）
    #   而**天目录里的那份**是跑批"按天复制"的 ✓ ⇒ 天然是当天该用的 as-of 档 ✓ ⇒ 优先用它 ✓
    w = _wave_from_sandbox_day(str(_asof or "")) or ensure_wave(as_of=_asof)
    op = str((w or {}).get("operation") or "").strip().lower()
    lv = str((w or {}).get("level") or "")
    sb = str((w or {}).get("sub_level") or "")
    _wd = str((w or {}).get("date") or "?")
    tag = "%s/%s" % (lv or "?", sb or "?")
    if not op:
        # ★ 2026-10-10：op 缺失 = 浪型**不可用** ⇒ 由 `WOLF_WAVE_COND_FAIL` 决定（默认 open ✓）
        if _cond_fail_policy() == "close":
            _cond_fail_record(kind, op, tag, "FAIL-CLOSE", "operation 缺失（浪型不可用）")
            return True, ("浪型不可用（operation 缺失，%s）⇒ **保守拦截** ✗"
                          "（回测口径 WOLF_WAVE_COND_FAIL=close ✓；要放行设 open ✓）" % tag)
        _cond_fail_record(kind, op, tag, "FAIL-OPEN", "operation 缺失（浪型不可用）")
        return False, ""
    if op == "build":
        return False, ""
    # ── ★ 2026-10-13（用户拍板 ✓）：浪型对「建仓类」**不再一票否决** ⇒ 改为与 B 闸同口径 + 仓位降档 ✓
    #    开关 `WOLF_WAVE_BUILD_SOFT`（默认 0 ✓ = 现状零影响 ✓；回测/灰度置 1 ✓）
    #    两个浪型（scope ✓）：index（大盘 ✓）/ mainline（主线板块 ✓）
    _soft = str(os.getenv("WOLF_WAVE_BUILD_SOFT", "0")).strip().lower() in ("1", "true", "yes", "on")
    _cls = classify_buy_leg(kind)
    if _soft and _cls in ("build", "ambush") and op != "exit":
        _scope = wave_scope()
        _th, _st = symbol_mainline_stage(symbol, _asof) if _scope == "mainline" else ("", "")
        # ① 主线板块口径：属当日主线/次主线且结构**已确认** ⇒ 放行建仓 ✓
        #    依据：2025-06-05「主线板块筑底行情…急杀可以买，缓跌不买」✓（时点仍由 B 闸把关 ✓）
        _top, _tdetail = theme_wave_op(_th, _asof) if _th else ("", "")
        if _top == "build":
            _mult = wave_size_mult("build", _st)
            return False, ("[WAVE-SCOPE=mainline] 主线板块 %s 浪型=**build**（%s ✓）⇒ 建仓放行 ✓"
                           "（降档 ×%.2f；时点由 B 闸「不在下跌中买」把关 ✓；大盘档 %s/%s·%s）"
                           % (_th, _tdetail, _mult, tag, op, _wd))
        if _top == "defense":
            return True, ("[WAVE-SCOPE=mainline] 主线板块 %s 浪型=**defense**（%s ✗）"
                          "⇒ 建仓不放行（结构已破 ✓；大盘档 %s/%s·%s）"
                          % (_th, _tdetail, tag, op, _wd))
        # ② t_only 下**追高/主升**类仍拦 ✓（语料：只做T、**不追不新建主升** ✓）
        if op == "t_only" and _cls == "build" and any(x in str(kind or "").lower() for x in ("trend_break", "breakout", "new_high")):
            return True, ("浪型 %s·t_only ⇒ 「只做T、不追**不新建主升**」✓，本腿（%s）属主升/突破 ✗ "
                          "不放行（档 date=%s）" % (tag, str(kind)[:28], _wd))
        # ③ 其余建仓/埋伏 ⇒ 放行但**降档** ✓（大盘 defense/t_only 只压仓位 ✓，不再否定 ✓）
        _mult = wave_size_mult(op, _st)
        return False, ("[WAVE-SOFT] 大盘档 %s·%s ⇒ 建仓**放行但降档 ×%.2f** ✓"
                       "（原为硬拦 ✗；改按 B 闸「不在下跌中买」把关 ✓；板块结构=%s；档 date=%s）"
                       % (tag, op, _mult, _st or "-", _wd))
    if op in ("defense", "exit"):
        return True, ("浪型 %s·%s ⇒ 建仓类不放行 ✓（狼大口径：%s，档 date=%s）"
                      % (tag, op, "防御不建仓" if op == "defense" else "兑现降仓", _wd))
    if op == "side":
        return True, ("浪型 %s·side ⇒ 买类需人工确认 ✓，**条件单不自动执行** ✗（档 date=%s）" % (tag, _wd))
    if op == "t_only":
        cls = classify_buy_leg(kind)
        if cls == "t":
            return False, ""
        if cls == "ambush":
            _amb = str(os.getenv("WOLF_WAVE_COND_AMBUSH", "block")).strip().lower()
            if _amb in ("allow", "1", "true", "yes"):
                return False, ""
            return True, ("浪型 %s·t_only ⇒ 埋伏腿是**新开仓** ✗，条件单不自动执行 ✓"
                          "（要放行设 WOLF_WAVE_COND_AMBUSH=allow ✓；档 date=%s）" % (tag, _wd))
        if cls == "other":
            _oth = str(os.getenv("WOLF_WAVE_COND_OTHER", "block")).strip().lower()
            if _oth in ("allow", "1", "true", "yes"):
                return False, ""
            return True, ("浪型 %s·t_only ⇒ 「只做T、**不追不新建主升**」✓，本腿（%s）**未归类** ✗"
                          "⇒ 保守拦下 ✓（要放行设 WOLF_WAVE_COND_OTHER=allow ✓；档 date=%s）"
                          % (tag, str(kind)[:28], _wd))
        return True, ("浪型 %s·t_only ⇒ 「只做T、**不追不新建主升**」✓，"
                      "本腿（%s，归类=%s）属建仓/主升 ✗ 不放行（档 date=%s）"
                      % (tag, str(kind)[:28], cls, _wd))
    # 未知 operation ⇒ 默认放行 ＋ 留痕；`WOLF_WAVE_COND_FAIL=close` ⇒ 保守拦截 ✓
    if _cond_fail_policy() == "close":
        _cond_fail_record(kind, op, tag, "FAIL-CLOSE", "未知 operation")
        return True, ("浪型 operation=%s 未知（%s）⇒ **保守拦截** ✗"
                      "（WOLF_WAVE_COND_FAIL=close ✓；要放行设 open ✓）" % (op, tag))
    _cond_fail_record(kind, op, tag, "FAIL-OPEN", "未知 operation")
    return False, ""
