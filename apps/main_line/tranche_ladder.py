# -*- coding: utf-8 -*-
"""tranche_ladder.py — 狼大 t_only 期建仓执行链·三档状态机 (2026-09-07, openspec add-wolf-trial-ladder)

档位: ambush(低位埋伏先手) -> trial(试仓) -> normal(主升建仓)
- 触发: 仅低吸信号 254(custom_prevlow 触前低+缩量) / 253(custom_m5dump 大盘急杀), 禁追高
- 方向: fusion TOP1∪TOP2(当日运行时) + sector_g3 自动板块归属; 零标的硬编码
- 升级: wave=build 或 结构主升确认(上证 close>MA200 ∧ MA20>MA60 ∧ vol≥1.2×20均 ∧
        close≥近60日箱体上沿×0.995)——箱体上沿/均线滚动计算, 禁止任何点位字面量
- 全部阈值 env(config), 记账 data/tranche_state.json
"""
import json, os, math
import numpy as np
import pandas as pd

DATA = os.environ.get("DATA_DIR", "/app/data")
# env 化阈值(不硬编码)
AMBUSH_SINGLE_PCT = float(os.getenv("AMBUSH_SINGLE_PCT", "1"))     # 单次 ≤净值 %
AMBUSH_CAP_PCT = float(os.getenv("AMBUSH_CAP_PCT", "10"))          # 累计 ≤计划仓 %
TRIAL_SINGLE_PCT = float(os.getenv("TRIAL_SINGLE_PCT", "2"))
TRIAL_CAP_PCT = float(os.getenv("TRIAL_CAP_PCT", "30"))
AMBUSH_SHRINK_MAX = float(os.getenv("AMBUSH_SHRINK_MAX", "0.7"))   # 埋伏缩量上限(量比)
ESCALATE_VOL_RATIO = float(os.getenv("ESCALATE_VOL_RATIO", "1.2")) # 放量倍数(20日均量)
ESCALATE_BOX_RATIO = float(os.getenv("ESCALATE_BOX_RATIO", "0.995"))  # 贴近60日箱体上沿
W_MA_S, W_MA_M, W_MA_L = (int(x) for x in os.getenv("ESCALATE_MA_WINDOWS", "20,60,200").split(","))
W_BOX = int(os.getenv("ESCALATE_BOX_WINDOW", "60"))
W_VOL = int(os.getenv("ESCALATE_VOL_WINDOW", "20"))
INDEX_CSV = os.getenv("INDEX_CSV", os.path.join(DATA, "指数数据/index_daily/000001.SH.csv"))
TRIAL_STATE_FILE = os.path.join(DATA, "tranche_state.json")

LOW_BUY_KINDS = ("custom_prevlow", "custom_m5dump")   # 254/253
CONFIRM_STAGE_TRIAL = ("确认", "突破候选")
CONFIRM_STAGE_AMBUSH = ("缩量止跌", "结构到位")


def _load(name, default=None):
    try:
        p = os.path.join(DATA, name)
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fusion_top12():
    """fusion TOP1∪TOP2 (当日, 运行时)"""
    ml = _load("main_line_state.json", {})
    fus = ml.get("fusion") or {}
    rank = sorted(fus.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))
    return [k for k, _ in rank[:2]]


def wave_operation():
    st = _load("wave_state.json", {})
    return str(st.get("operation") or "")


def symbol_concepts(symbol):
    """股票/ETF → 概念名(stock_concept_map); ETF/无概念→[]"""
    import psycopg2
    try:
        s = str(symbol).strip().upper()
        if s.endswith((".SH", ".SZ")):
            code6, ex = s[:6], s[-2:]
        elif s[:2] in ("SH", "SZ"):
            code6, ex = s[2:], s[:2]
        else:
            code6, ex = s, ("SH" if s.startswith("6") else "SZ")
        conn = psycopg2.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()
        cur.execute("SELECT concept_name FROM stock_concept_map WHERE ts_code=%s", (code6 + "." + ex,))
        out = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        return out
    except Exception:
        return []


def _stage_of(symbol):
    """stock_confirm_result 中该标的 confirm stage; 无→''"""
    sc = _load("stock_confirm_result.json", {})
    code6 = "".join(ch for ch in str(symbol) if ch.isdigit())[:6]
    for v in sc.values():
        if not isinstance(v, dict): continue
        for s in (v.get("stocks") or []):
            if str(s.get("code", "")).split(".")[0] == code6:
                return str(s.get("stage") or "")
    return ""


def _position_low_concepts():
    """position_class_result 中 position=LOW 的概念集合(可选维度)"""
    pc = _load("position_class_result.json", {})
    lows = set()
    for k, v in pc.items():
        if isinstance(v, dict) and (v.get("position") or "").upper() == "LOW":
            lows.add(str(k))
    return lows


def tier_for(symbol):
    """三档判定: 'ambush'|'trial'|'normal'|'none'"""
    op = wave_operation()
    if op in ("defense", "exit"):
        return "none", "defense/exit 不介入"
    top12 = fusion_top12()
    try:
        import sys as _s
        _s.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
        from sector_g3 import symbol_themes
        themes = symbol_themes(symbol)
    except Exception:
        themes = []
    if not themes or not (set(themes) & set(top12)):
        return "none", f"不在主线候选TOP1∪TOP2(themes={themes})"
    stage = _stage_of(symbol)
    if stage in CONFIRM_STAGE_TRIAL:
        return "trial", f"stage={stage}"
    if stage in CONFIRM_STAGE_AMBUSH:
        # 埋伏档再加低位/缩量/不破前低过滤(调用方以 254 信号+缩量实际触发为准; 这里给档)
        return "ambush", f"stage={stage}"
    # 无 confirm stage 但有 position LOW 概念 → ambush(弱证据)
    lows = _position_low_concepts()
    if lows and (set(symbol_concepts(symbol)) & lows):
        return "ambush", "position LOW 概念(无confirm stage)"
    if escalate_signal()[0] or op == "build":
        return "normal", f"op={op}"
    return "none", "无确认信息"


def _index_df():
    try:
        df = pd.read_csv(INDEX_CSV, parse_dates=["trade_date"]).sort_values("trade_date")
        return df.dropna(subset=["close"])
    except Exception:
        return None


def escalate_signal():
    """动态升级: wave=build 或 结构主升确认(上证滚动) → (bool, [triggers])。无点位字面量。"""
    op = wave_operation()
    if op == "build":
        return True, ["wave=build"]
    df = _index_df()
    if df is None or len(df) < W_MA_L + 5:
        return False, ["no_index_data"]
    close = df["close"].astype(float)
    ma_l = close.rolling(W_MA_L).mean().iloc[-1]
    ma_s = close.rolling(W_MA_S).mean().iloc[-1]
    ma_m = close.rolling(W_MA_M).mean().iloc[-1]
    vol = df["vol"].astype(float) if "vol" in df.columns else pd.Series(dtype=float)
    v20 = vol.rolling(W_VOL).mean().iloc[-1] if len(vol) else 0
    box_top = df["high"].astype(float).rolling(W_BOX).max().iloc[-1] if "high" in df.columns else float("nan")
    c = float(close.iloc[-1])
    trig = []
    if not (c > ma_l): trig.append("close<=MA%d" % W_MA_L)
    if not (ma_s > ma_m): trig.append("MA%d<=MA%d" % (W_MA_S, W_MA_M))
    vol_ok = bool(len(vol) and v20 and vol.iloc[-1] >= ESCALATE_VOL_RATIO * v20)
    if not vol_ok: trig.append("未放量(vol<%.1fx20日均)" % ESCALATE_VOL_RATIO)
    box_ok = bool(not math.isnan(box_top) and c >= box_top * ESCALATE_BOX_RATIO)
    if not box_ok: trig.append("未站上%.1f%%箱体上沿" % (ESCALATE_BOX_RATIO * 100))
    ok = (c > ma_l) and (ma_s > ma_m) and vol_ok and box_ok
    return bool(ok), (trig if not ok else ["结构主升确认"])


def _state():
    return _load("tranche_state.json", {})


def _save_state(st):
    try:
        with open(TRIAL_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _net_asset(account_id="stock"):
    try:
        from app.services.t_gateway import t_net_asset
        return float(t_net_asset(account_id) or 0)
    except Exception:
        return 0.0


def allowed_buy_volume(symbol, trigger_kind, quote, ledger=None, account_id="stock"):
    """低吸档位放行量(股)。仅 254/253 且档位 ambush/trial → 档位上限内量; normal(escalate)→-1 走正常建仓; 其余 0。"""
    if trigger_kind not in LOW_BUY_KINDS:
        return 0, "非低吸触发"
    tier, reason = tier_for(symbol)
    if tier == "none":
        return 0, reason
    if tier == "normal":
        return -1, "normal(主升确认): 走正常建仓"
    price = float((quote or {}).get("current") or 0)
    if price <= 0:
        return 0, "no_price"
    na = _net_asset(account_id)
    st = _state()
    used = float((st.get(symbol) or {}).get("bought_amt") or 0)
    if tier == "ambush":
        single = na * AMBUSH_SINGLE_PCT / 100.0
        cap = na * AMBUSH_CAP_PCT / 100.0
    else:
        single = na * TRIAL_SINGLE_PCT / 100.0
        cap = na * TRIAL_CAP_PCT / 100.0
    room = max(cap - used, 0.0)
    amt = min(single, room)
    vol = int(amt / price / 100) * 100
    return vol, f"tier={tier} 上限额≈{amt:.0f}元 room={room:.0f}"


def record_buy(symbol, price, volume, tier):
    st = _state()
    e = st.setdefault(str(symbol), {"tier": tier, "bought_amt": 0.0, "batches": 0, "updated": ""})
    e["bought_amt"] = float(e.get("bought_amt") or 0) + float(price) * int(volume)
    e["batches"] = int(e.get("batches") or 0) + 1
    e["tier"] = tier
    e["updated"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_state(st)


def sync_normal_upgrade():
    """盘前重算: escalate 后 ambush/trial→normal 提示(不自动改仓位, 供报告)"""
    esc, trig = escalate_signal()
    st = _state()
    changed = []
    for sym in list(st.keys()):
        if esc and st[sym].get("tier") in ("ambush", "trial"):
            st[sym]["tier"] = "normal"
            st[sym]["upgraded_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            changed.append(sym)
    if changed:
        _save_state(st)
    return esc, trig, changed


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["SH588170", "SH603259"]
    for s in syms:
        print(s, tier_for(s))
    print("escalate:", escalate_signal())
    print("top12:", fusion_top12(), "wave:", wave_operation())
