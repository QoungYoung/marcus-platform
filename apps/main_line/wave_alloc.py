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
    # fusion top3(分数序)
    top3, scores = [], []
    try:
        ml = _load("main_line_state.json")
        fus = ml.get("fusion") or {}
        rank = sorted(fus.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))
        for k, v in rank[:3]:
            top3.append(k); scores.append(round(float(v.get("score", 0)), 3))
    except Exception:
        pass
    return {"operation": op, "invest": invest, "w": [w1, w2, w3],
            "top3": top3, "scores": scores}


if __name__ == "__main__":
    import pprint
    pprint.pprint(read_wave_alloc())
