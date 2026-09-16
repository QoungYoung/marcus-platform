# -*- coding: utf-8 -*-
"""底仓 floor 口径（2026-09-16 用户拍板）：一次性认账 rebasing + 只增不减 + 分档 ratio。

## 为什么是这套写法（两条已证否的路径）

· floor 锚「当前持仓 × ratio」（2026-09-07 之前的写法）→ 每卖一次持仓变小、floor 跟着变小
  → T 仓“复活”→ 继续卖，把底仓一点点啃掉（588170 连卖超卖事故，已被那次修复否定）。
· floor 锚「累计未void买入 × ratio」（2026-09-07 至今）→ 卖出不动锚、不会被啃，但**历史卖超的标的**
  floor 会永久大于当前持仓 → 卖腿推导量恒 0（2026-09-16 实测：SH588170 持仓23200/累计买入109000/
  floor54500、SH512480 1300/8000/4000、SZ002409 200/400/200 全部被锁死）。

二者不可兼得：**floor 挂在持仓上就会侵蚀，挂在累计买入上就可能锁死**。本模块的解法：

1. 锚仍是「累计未void买入」（卖出不动锚，保留 2026-09-07 防连卖超卖的原始意图）；
2. 只在 floor 把 T 仓锁死时（floor > 可卖 − 100 股）对该 (account, symbol) 做**一次性认账**：
   rec.base = 可卖 × ratio、rec.cum_at_base = 当时的累计买入；
   此后 floor = base + (累计买入 − cum_at_base) × ratio —— **只随新增买入增长，不随卖出下降**；
3. 持仓归零时写 reset 记录（base=0、cum_at_base=当时的累计买入），避免清仓后重建又被历史累计买入毒住；
4. ratio 按浪型分档（狼大 2026-01-17「主升趋势就75%以上…调整就50% 有风险就30% 下跌趋势就不做」
   + 2026-04-14「50%的底仓 30%左右做日内…剩下20%应对黑天鹅」），开盘前定、盘中不变。

## 开关 / 状态

· `T_BASE_FLOOR_REBASE=0` → 退回旧的「累计买入 × ratio」口径（回滚用）。
· `T_BASE_FLOOR_AUDIT=0` → 不写 t_triggers 审计行。
· 状态文件：`$DATA_DIR/t_base_floor_rebase.json`（原子写；可人工编辑 bucket_override / buckets）。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from trade_direction import is_buy, is_sell  # noqa: E402 统一方向词表

MIN_LOT = 100                    # 一手；也是"至少留一条腿"的可卖额度
_STATE_NAME = "t_base_floor_rebase.json"
_MAX_EVENTS = 30                 # 每标的保留最近 N 条认账/重置事件（审计）

# ratio 分档（底仓 / 持仓）。主升档取 2/3 = 他的话"底仓 50% / 总仓 75%"（2026-04-14 + 2026-01-17）。
_BUCKET_ENV = {
    "main_up": "T_BASE_KEEP_RATIO_MAIN_UP",
    "range": "T_BASE_KEEP_RATIO",          # 沿用历史默认 0.5
    "high": "T_BASE_KEEP_RATIO_HIGH",
    "exit": "T_BASE_KEEP_RATIO_EXIT",
}
_BUCKET_DEFAULT = {"main_up": 2.0 / 3.0, "range": 0.5, "high": 1.0 / 3.0, "exit": 0.0}
# 浪型 operation → 档（与 position_tier.CORPUS_PROFILE["op_to_total"] 同源：build=主升/t_only·side=调整/
# defense=有风险/exit=下跌）
OP_BUCKET = {"build": "main_up", "t_only": "range", "side": "range",
             "defense": "high", "exit": "exit"}
_STALE_DAYS = int(os.getenv("T_BASE_RATIO_STALE_DAYS", "7"))


# ────────────────────────────── 基础 ──────────────────────────────

def _enabled() -> bool:
    return os.getenv("T_BASE_FLOOR_REBASE", "1").strip().lower() not in ("0", "false", "no")


def _audit_enabled() -> bool:
    return os.getenv("T_BASE_FLOOR_AUDIT", "1").strip().lower() not in ("0", "false", "no")


def _data_dir() -> str:
    """状态文件目录。按 DATA_DIR → MARCUS_WORKSPACE/data → 逐级向上找已存在的 data/ → 兜底。

    ⚠️ 2026-09-16 部署踩坑：容器里代码挂在 /app/app（= 宿主 backend/app），从
    /app/app/services 向上三级会走到 "/"，于是状态文件被写进**容器内的 /data**（可写层）——
    既不落宿主 bind mount，又让 backend 与 worker 两个容器各写一份、floor 互相不一致。
    故改为"向上逐级探测已存在的 data/"，不再假设固定层级。
    """
    d = os.environ.get("DATA_DIR") or ""
    if d:
        return d
    ws = os.environ.get("MARCUS_WORKSPACE") or ""
    if ws:
        cand = os.path.join(ws, "data")
        if os.path.isdir(cand):
            return cand
    # 逐级向上：优先认"仓库根"（同级有 backend/ 或 apps/），否则退回首个存在的 data/
    p = os.path.dirname(os.path.abspath(__file__))
    fallback = None
    for _ in range(6):
        cand = os.path.join(p, "data")
        if os.path.isdir(cand):
            if os.path.isdir(os.path.join(p, "backend")) or os.path.isdir(os.path.join(p, "apps")):
                return cand
            if fallback is None:
                fallback = cand
        nxt = os.path.dirname(p)
        if nxt == p:
            break
        p = nxt
    if fallback:
        return fallback
    return "/app/data" if os.path.isdir("/app/data") else "data"


def _state_path() -> str:
    return os.path.join(_data_dir(), _STATE_NAME)


def _key(account_id: str, symbol: str) -> str:
    return "%s:%s" % (account_id or "stock", symbol or "")


def _lot_floor(x: float, lot: int = MIN_LOT) -> int:
    """向下取整到一手（floor 与可卖额度都保持 100 股整数倍）。"""
    try:
        return int(int(x) // int(lot)) * int(lot)
    except Exception:
        return 0


def load_state() -> Dict[str, Any]:
    p = _state_path()
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
            return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_state(st: Dict[str, Any]) -> None:
    p = _state_path()
    try:
        d = os.path.dirname(p) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".t_base_floor_", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, p)
    except Exception as e:
        print(f"[t-base-floor] 状态写入失败: {type(e).__name__}: {str(e)[:80]}")


# ────────────────────────────── ratio 分档 ──────────────────────────────

def ratio_of(bucket: str) -> float:
    """档 → ratio（环境变量优先，未知档按 range）。"""
    b = str(bucket or "").strip().lower() or "range"
    if b not in _BUCKET_DEFAULT:
        b = "range"
    env = _BUCKET_ENV.get(b)
    raw = os.getenv(env) if env else None
    if raw not in (None, ""):
        try:
            return max(float(raw), 0.0)
        except Exception:
            pass
    return float(_BUCKET_DEFAULT[b])


def wave_bucket() -> Optional[str]:
    """当前浪型 operation → 档；文件缺失/过旧（mtime 超过 T_BASE_RATIO_STALE_DAYS）→ None。

    只读 data/wave_state*.json（与 position_tier.read_wave_state 同源）。**用文件 mtime 判新鲜度**，
    不用文件内 date 字段（daily_decision 记录过该字段口径不一致的坑：文件写于当日、date 却是前一交易日）。
    """
    cands = []
    now = datetime.now()
    d8 = now.strftime("%Y%m%d")
    dash = now.strftime("%Y-%m-%d")
    cands.append(os.path.join(_data_dir(), f"wave_state_{d8}.json"))
    cands.append(os.path.join(_data_dir(), f"wave_state_{dash}.json"))
    cands.append(os.path.join(_data_dir(), "wave_state.json"))
    for p in cands:
        try:
            if not os.path.isfile(p):
                continue
            age = now - datetime.fromtimestamp(os.path.getmtime(p))
            if age > timedelta(days=_STALE_DAYS):
                continue
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
            b = OP_BUCKET.get(str((st or {}).get("operation") or "").strip().lower())
            if b:
                return b
        except Exception:
            continue
    return None


def resolve_ratio(symbol: Optional[str] = None, bucket: Optional[str] = None,
                  state: Optional[Dict[str, Any]] = None) -> Tuple[float, str]:
    """ratio 与档位。优先级：按标的覆盖 > 显式 bucket > 状态文件全局覆盖 > 浪型 > range。"""
    st = state if state is not None else load_state()
    if symbol:
        ov = (st.get("buckets") or {}).get(symbol)
        if ov:
            b = str(ov).strip().lower()
            return ratio_of(b), b
    if bucket:
        b = str(bucket).strip().lower()
        return ratio_of(b), b
    ov = st.get("bucket_override")
    if ov:
        b = str(ov).strip().lower()
        return ratio_of(b), b
    b = wave_bucket() or "range"
    return ratio_of(b), b


# ────────────────────────────── 纯核心 ──────────────────────────────

def _rebase_floor(sellable: int, ratio: float, min_lot: int = MIN_LOT) -> int:
    """认账后的新底仓 = 可卖 × ratio（取整到一手，且至少给 T 仓留一条腿）。"""
    sel = int(sellable or 0)
    if sel < 2 * min_lot:
        return sel                      # 不足两条腿：整仓即底仓，不解锁（别把唯一一手当 T 仓卖光）
    return max(min(_lot_floor(sel * float(ratio), min_lot), sel - min_lot), 0)


def compute_floor(cum_buy: int, sellable: int, ratio: float,
                  rec: Optional[Dict[str, Any]] = None,
                  override: Optional[int] = None,
                  min_lot: int = MIN_LOT,
                  position: Optional[int] = None) -> Tuple[int, Optional[Dict[str, Any]]]:
    """纯函数：算底仓 floor，并返回需要落库的事件（None 表示无需认账）。

    rec（已认账记录）：{"base": int, "cum_at_base": int}。给了 rec 后 floor 只随**新增买入**增长，
    卖出不动它 —— 这是"解锁但不侵蚀"的关键。

    position = **真实持仓**（volume−frozen），sellable = 今日可卖额。两者必须分开：
      · position ≤ 0 → 真清仓 → 记 reset（否则重建后被历史累计买入毒住）；
      · position > 0 但 sellable ≤ 0 → 只是**今日无券可卖**（T+1 当日买入冻结），
        **既不认账也不重置**，保持原有锚（明天可卖时照旧生效）。
      不区分这两者会把"今天刚买、还没到 T+1"的标的错误地重标成 0 底仓。
    """
    cum = int(cum_buy or 0)
    sel = int(sellable or 0)
    pos = sel if position is None else int(position or 0)

    if rec is not None:
        base = int(rec.get("base") or 0)
        cum_at = int(rec.get("cum_at_base") or 0)
        anchor = base + int(max(cum - cum_at, 0) * float(ratio))
    elif cum > 0:
        anchor = int(cum * float(ratio))
    elif sel > 0:
        anchor = int(sel * float(ratio))        # 无成交流水（外部同步仓）
    elif override is not None:
        anchor = int(override)                  # 旧 T_BASE_FLOOR_OVERRIDES（仅无流水、无持仓时）
    else:
        anchor = min_lot
    floor = max(_lot_floor(anchor, min_lot), min_lot)

    if pos <= 0:
        # 无持仓：无流水且无认账记录 → 沿用旧口径返回值（override/下限），无需记事件
        if cum <= 0 and rec is None:
            return floor, None
        # 真清仓 → 记 reset，别让历史累计买入在重建后继续毒住 floor
        if rec is None or int(rec.get("base") or 0) != 0 or int(rec.get("cum_at_base") or 0) != cum:
            return 0, {"kind": "reset", "floor": 0, "sellable": 0, "cum_buy": cum,
                       "ratio": float(ratio), "old_floor": floor}
        return 0, None

    if sel <= 0:
        # 持仓还在、今日无券可卖（T+1 冻结）→ 不认账、不重置，锚保持不变（明天照旧生效）
        return floor, None

    if floor > sel - min_lot:
        # 刚 reset 过且无新增买入 → 不重复认账（避免每次读都重算、以及 0 股时的抖动）
        if rec is not None and int(rec.get("base") or 0) <= 0 and int(rec.get("cum_at_base") or 0) == cum:
            return floor, None
        new_floor = _rebase_floor(sel, ratio, min_lot)
        return new_floor, {"kind": "rebase", "floor": new_floor, "sellable": sel, "cum_buy": cum,
                           "ratio": float(ratio), "old_floor": floor}
    return floor, None


# ────────────────────────────── 落库（事件 + 审计）──────────────────────────────

def _record_event(account_id: str, symbol: str, event: Dict[str, Any],
                  ratio: float, bucket: str) -> Dict[str, Any]:
    st = load_state()
    key = _key(account_id, symbol)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    syms = st.setdefault("symbols", {})
    prev = syms.get(key) or {}
    events: List[Dict[str, Any]] = list(prev.get("events") or [])
    entry = {
        "base": int(event.get("floor") or 0),
        "cum_at_base": int(event.get("cum_buy") or 0),
        "kind": event.get("kind"),
        "rebased_at": now,
        "ratio": round(float(ratio), 4),
        "bucket": bucket,
        "sellable": int(event.get("sellable") or 0),
    }
    events.append({**entry, "prev": {"base": prev.get("base"), "cum_at_base": prev.get("cum_at_base")}})
    syms[key] = {**entry, "events": events[-_MAX_EVENTS:]}
    st["as_of"] = now
    st["last_bucket"] = bucket
    save_state(st)
    print(f"[t-base-floor] {event.get('kind')} {key}: floor {event.get('old_floor')} → {entry['base']}"
          f"（可卖{entry['sellable']} 累计买入{entry['cum_at_base']} ratio={round(float(ratio), 4)} {bucket}）",
          flush=True)
    _audit_trigger(account_id, symbol, event, ratio, bucket)
    return entry


def _audit_trigger(account_id: str, symbol: str, event: Dict[str, Any],
                   ratio: float, bucket: str) -> None:
    """落一条 t_triggers 审计行（status=info，绝不 pending —— 否则会被 t_bridge 当待执行单再跑一次）。"""
    if not _audit_enabled():
        return
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        reason = ("底仓 floor %s：%s → %s 股（可卖%s，累计买入%s，ratio=%s，档=%s）"
                  % (event.get("kind"), event.get("old_floor"), event.get("floor"),
                     event.get("sellable"), event.get("cum_buy"), round(float(ratio), 4), bucket))
        snap = json.dumps({"old_floor": event.get("old_floor"), "floor": event.get("floor"),
                           "sellable": event.get("sellable"), "cum_buy": event.get("cum_buy"),
                           "ratio": float(ratio), "bucket": bucket, "kind": event.get("kind")},
                          ensure_ascii=False)
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO t_triggers (account_id, symbol, event_type, status, mode, direction, reason, snapshot) "
                "VALUES (:a, :s, 'base_floor_rebase', 'info', 'system', NULL, :r, CAST(:snap AS jsonb))"),
                {"a": account_id, "s": symbol, "r": reason[:200], "snap": snap})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[t-base-floor] 审计行写入失败（不影响 floor）: {type(e).__name__}: {str(e)[:80]}")


# ────────────────────────────── 数据读取 ──────────────────────────────

def _cum_buy_volume(account_id: str, symbol: str) -> int:
    """累计未void买入量（底仓锚定基数：只升不降，卖出不缩小底仓）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        v = db.execute(text(
            "SELECT COALESCE(SUM(volume), 0) FROM paper_trades "
            "WHERE account_id = :a AND symbol = :s AND direction IN ('买入','buy') "
            "AND (voided = 0 OR voided IS NULL)"),
            {"a": account_id, "s": symbol}).scalar()
        return int(v or 0)
    finally:
        db.close()


def _hold_shares(account_id: str, symbol: str) -> int:
    """当前可卖持仓 = volume − frozen（与 api/trades._get_hold_shares 同口径）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT volume, frozen FROM paper_positions WHERE account_id = :a AND symbol = :s"),
            {"a": account_id, "s": symbol}).mappings().first()
        if not row:
            return 0
        return max(int(row.get("volume") or 0) - int(row.get("frozen") or 0), 0)
    finally:
        db.close()


# ────────────────────────────── 对外入口 ──────────────────────────────

def _legacy_floor(account_id: str, symbol: str, volume: Optional[int],
                  override: Optional[int], cum_buy: Optional[int] = None) -> int:
    """旧口径（T_BASE_FLOOR_REBASE=0 或新口径异常时的回退）：累计买入×ratio，下限 100 股。"""
    keep = float(os.getenv("T_BASE_KEEP_RATIO", "0.5"))
    base = cum_buy if cum_buy is not None else _cum_buy_volume(account_id, symbol)
    if base and int(base) > 0:
        return max(int(int(base) * keep), MIN_LOT)
    if volume is not None and int(volume) > 0:
        return max(int(int(volume) * keep), MIN_LOT)
    return int(override) if override is not None else MIN_LOT


def evaluate(account_id: str, symbol: str, volume: Optional[int] = None,
             cum_buy: Optional[int] = None, override: Optional[int] = None,
             persist: bool = True) -> Tuple[int, Optional[Dict[str, Any]]]:
    """返回 (floor, event)。volume=可卖持仓（None 时自查 paper_positions），cum_buy 可注入（测试用）。"""
    if not _enabled():
        return _legacy_floor(account_id, symbol, volume, override, cum_buy), None

    cum = int(cum_buy) if cum_buy is not None else _cum_buy_volume(account_id, symbol)
    sel = int(volume) if volume is not None else None
    if sel is None:
        pos = _hold_shares(account_id, symbol)
        sel = pos
    elif sel > 0:
        pos = sel                      # 有券可卖 ⇒ 持仓必然 > 0，免一次查询（热路径）
    else:
        pos = _hold_shares(account_id, symbol)   # 无券可卖：区分"真清仓"与"T+1 冻结"
    st = load_state()
    ratio, bucket = resolve_ratio(symbol=symbol, state=st)
    rec = (st.get("symbols") or {}).get(_key(account_id, symbol))
    floor, event = compute_floor(cum, sel, ratio, rec=rec, position=pos,
                                 override=override if (cum <= 0 and not rec) else None)
    if event and persist:
        _record_event(account_id, symbol, event, ratio, bucket)
    return int(floor), event


def base_floor_shares(account_id: str, symbol: str, volume: Optional[int] = None,
                      override: Optional[int] = None, cum_buy: Optional[int] = None,
                      persist: bool = True) -> int:
    """底仓保留下限（狼大铁律：底仓不卖）—— 新口径入口。"""
    return evaluate(account_id, symbol, volume=volume, cum_buy=cum_buy,
                    override=override, persist=persist)[0]


def maintain(accounts: Tuple[str, ...] = ("stock",), persist: bool = True) -> Dict[str, Any]:
    """盘前一次性认账：对每个持仓标的按新口径算 floor，需要认账的落状态 + 审计。返回摘要。"""
    from app.services.t_gateway import get_sellable_ledger      # 懒导入，避免循环依赖
    out: Dict[str, Any] = {"checked": 0, "rebased": [], "reset": [], "floors": {}, "bucket": None}
    for acc in accounts or ():
        try:
            ledger = get_sellable_ledger(acc) or {}
        except Exception as e:
            print(f"[t-base-floor] 读取 {acc} 可卖账本失败: {type(e).__name__}: {str(e)[:80]}")
            continue
        for sym, item in ledger.items():
            sel = int((item or {}).get("sellable") or 0)
            try:
                floor, event = evaluate(acc, sym, volume=sel, persist=persist)
            except Exception as e:
                print(f"[t-base-floor] {acc}:{sym} 评估失败: {type(e).__name__}: {str(e)[:80]}")
                continue
            out["checked"] += 1
            out["floors"][_key(acc, sym)] = floor
            if event:
                key = "rebased" if event.get("kind") == "rebase" else "reset"
                out[key].append(f"{sym}(可卖{sel}/底仓{floor})")
    try:
        out["bucket"] = resolve_ratio()[1]
    except Exception:
        pass
    return out
