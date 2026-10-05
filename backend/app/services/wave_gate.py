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


def ensure_wave(as_of: Optional[str] = None, max_stale_days: int = 10) -> Optional[dict]:
    """读 `wave_state` ✓；**缺失/过期 ⇒ 现场补** ✓（并写回 ✓）。补不出来返回 None ✓。"""
    w = _load_wave()
    age = _stale_days(w) if w else None
    ok = bool(w) and (age is None or age <= max_stale_days)
    if ok:
        return w
    print("[wave_gate] wave_state %s ⇒ **现场补**（as_of=%s ✓）"
          % ("缺失" if not w else "过期 %s 天" % age, as_of or "-"), file=sys.stderr, flush=True)
    try:
        import wave_level as _wl                       # apps/main_line/wave_level.py ✓
        _d = None
        if as_of:
            a = str(as_of).replace("-", "")
            _d = "%s-%s-%s" % (a[:4], a[4:6], a[6:8])
        lbl, feats = _wl.judge_wave(_d) if _d else _wl.judge_wave(None)
        if not lbl:
            raise RuntimeError("judge_wave 返回空")
        got = {"level": None, "sub_level": None, "operation": None, "date": _d,
               "reasons": "wave_gate 现场补（judge_wave=%s）" % str(lbl)[:40]}
        try:
            inner = json.loads(lbl) if str(lbl).strip().startswith("{") else None
        except Exception as _e:
            inner = None
        if isinstance(inner, dict):
            got.update({k: inner.get(k) for k in ("level", "sub_level", "operation", "confidence", "reasons")})
        got["level"] = got.get("level") or ""
        # ★ 修正 ✓：**只有拿到有效 level 才写回** ✗（自测里 `judge_wave` 返回"无数据"
        #   也能写回 ⇒ 把好的档覆盖成 `level="无数据"` ✗✗ —— 事故 ✓）
        if not got["level"] or got["level"] in ("无数据", "None", "?"):
            print("[wave_gate] judge_wave 无有效 level（%r）⇒ **不写回**，沿用旧档 ✓" % str(lbl)[:40],
                  file=sys.stderr, flush=True)
            return _load_wave()
        try:
            os.makedirs(os.path.dirname(_wave_path()), exist_ok=True)
            with open(_wave_path(), "w", encoding="utf-8") as f:
                json.dump(got, f, ensure_ascii=False, indent=1)
            print("[wave_gate] ✅ 已写回 %s（level=%s ✓）" % (_wave_path(), got.get("level")),
                  file=sys.stderr, flush=True)
        except Exception as _e_w:
            print("[wave_gate] 写回失败（不影响判据 ✓）: %s" % str(_e_w)[:80], file=sys.stderr, flush=True)
        return got
    except Exception as _e_c:
        print("[wave_gate] ✗ 现场补失败: %s ⇒ **放行**（绝不因缺数据拦死 ✓）" % str(_e_c)[:120],
              file=sys.stderr, flush=True)
        try:
            from app.services import alert_hub as _ah
            _ah.note("wave_gate.现场补失败", msg="%s（放行 ✓）" % str(_e_c)[:160])
        except Exception as _e_a:
            print("[wave_gate] 告警失败: %s" % str(_e_a)[:60], file=sys.stderr, flush=True)
        return None


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
        _raw = _wc.index_level_stop(wave=w, today=None)
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
    if bool(stop):
        return {"allowed": False, "mode": "blocked", "regime": "down" if False else "HALT",
                "gate": "BLOCKED",
                "why": "指数大级别转下跌1浪（狼大口径 ✓）：%s" % str(_why or w.get("level"))[:80]}
    return {"allowed": True, "mode": "auto", "regime": "ACTIVE",
            "gate": "ALLOWED",
            "why": str(_why or ("wave_state.level=%s（非 down ✓）" % str(w.get("level"))))[:100]}
