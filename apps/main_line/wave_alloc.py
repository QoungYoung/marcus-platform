# -*- coding: utf-8 -*-
"""wave_alloc.py — wave 调档·主线候选权重分配(2026-09-07 回测验证落地)

主题层+个股层回测(wave-tuned-v2)结论 → 候选/做T多方向资源按 wave_state.operation 分配：
  build   → top1:top2:top3 = 40/35/25, invest=100%  (主升吃轮动, 分散)
  t_only  → 70/20/10,          invest=100%  (收益持平回撤更小)
  side    → 60/25/15,          invest=100%
  defense → 60/25/15,          invest=30%   (深调段降仓控亏)
  exit    → 60/25/15,          invest=70%   (只降不空, 防踏空)

用法: alloc = read_wave_alloc() → {'operation','invest','w':[w1,w2,w3],'top3':[主题名×3],'score':[...]}
仅影响候选方向间资源分配(布腿数/顺序/仓位参考)，不绕过现有浪gate/风控硬门。
"""
import json, os


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


DATA = os.environ.get("DATA_DIR", "/app/data")

# wave 档 → (top1权重, top2, top3, invest比例)
WAVE_ALLOC = {
    "build": (0.40, 0.35, 0.25, 1.0),
    "t_only": (0.70, 0.20, 0.10, 1.0),
    "side": (0.60, 0.25, 0.15, 1.0),
    "defense": (0.60, 0.25, 0.15, 0.30),
    "exit": (0.60, 0.25, 0.15, 0.70),
}
DEFAULT_OP = "t_only"


def _load(name):
    try:
        return json.load(open(os.path.join(DATA, name), encoding="utf-8"))
    except Exception:
        return {}


def read_wave_alloc(state_file: str = "wave_state.json") -> dict:
    """读当前 wave_state.operation + main_line_state.fusion top3 → 分配配置。"""
    st = _load(state_file)
    op = str(st.get("operation") or DEFAULT_OP)
    w1, w2, w3, invest = WAVE_ALLOC.get(op, WAVE_ALLOC[DEFAULT_OP])
    # ★ 2026-10-13（§9.829）：`fusion` 自 2026-09-13 起已被移除 ✗ ⇒ 改用**方向层**同源清单 ✓
    #   （与布腿器 `switch_builder.theme_top12` 同一来源 ✓；分数用 `mainline_select.r5` ✓）
    top3, scores = [], []
    try:
        ml = _load("main_line_state.json")
        _ms = ml.get("mainline_select") or {}
        _names = [t for t in (_ms.get("mainline"), _ms.get("second")) if t]
        if not _names:
            _names = [str(x) for x in (ml.get("candidates") or []) if x][:3]
        _r5 = _ms.get("r5") if isinstance(_ms.get("r5"), dict) else {}
        for k in _names[:3]:
            top3.append(k)
            try:
                scores.append(round(float(_r5.get(k)), 3))
            except Exception:
                scores.append(None)
        if not _names:
            print("[wave_alloc] 方向层清单缺失 ⇒ top3 空（不再回落已退役的 fusion ✗）", flush=True)
    except Exception as _e_sil1:
        _silent_alert("wave_alloc.py:50", _e_sil1)
    return {"operation": op, "invest": invest, "w": [w1, w2, w3],
            "top3": top3, "scores": scores}


if __name__ == "__main__":
    import pprint
    pprint.pprint(read_wave_alloc())
