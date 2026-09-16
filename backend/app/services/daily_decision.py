# -*- coding: utf-8 -*-
"""daily_decision.py — G1 每日决策对象（2026-09-12，蓝图 §7 G1 / §4 分层优先级）。

**为什么需要它**：蓝图实证——他每天 **2.7 次判据 / 0.5 次成交**（判断先于交易），
而我们的系统是「11 种 trigger_kind 在等腿触发」（成交先于判断），且 L5/L6 的闸门能**直接拦掉**
L1–L4 已经选出的买入。本模块把当天各层的结论**汇成一个对象**：
`data/decision/<YYYYMMDD>.json`，供盘中所有腿引用；**没有对象就不开新腿**（可回退开关）。

**存储（2026-09-12 按用户口径改为「PG 为主 + 文件镜像」）**
  · 主存 = PostgreSQL `daily_artifacts(trade_date, artifact_key='decision', payload jsonb)` —— 与 G2 同一张表
  · 文件 `data/decision/<date>.json`（+ `latest.json`）= **镜像**，供人查看 / 回放脚本 / DB 不可用时兜底
  · `load()` 读序 = **PG → 文件**；`run()` 两边都写；payload 带 `revision`（同日重算递增，不静默丢版本）

**六层（与 §4 一致）**
  L1 方向（主线/主题）→ L2 档位（wave operation：build/t_only/side/defense/exit）→
  L3 仓位（目标/下限，来自 tier_targets）→ L4 选票（当日 confirm 结果）→
  L5 买点（**是否允许开新仓 + 前置条件 + 拦阻项**）→ L6 兑现（当日生效的离场规则）

**口径与诚实边界**
  · 每条结论都带 `basis`（来自哪个文件/函数）与 `as_of`；文件缺失时该层 `value=None` 并记进 `missing`
  · 本对象**只做汇总与准入**，不新增任何选股/择时逻辑（不造机制）
  · `entry_allowed()` 缺对象时的行为由开关 `WOLF_DECISION_GATE` 决定：**默认 0 = 仍放行**（不改现有行为）；
    置 1 后「无决策对象 → 拒绝开新腿」，这才是他「判据先于成交」的强制化
  · 对象生产由 `WOLF_DAILY_DECISION` 控制（默认 0）
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

LAYERS = ["L1_direction", "L2_operation", "L3_position", "L4_picks", "L5_entry", "L6_exit"]

# L2 档位 → 是否允许**新开仓**（与 wolf_discipline.tier_targets 的档位口径一致）
BUY_ALLOWED_OPS = ("build", "t_only", "side")


def enabled() -> bool:
    return os.getenv("WOLF_DAILY_DECISION", "0").strip().lower() not in ("0", "false", "no", "")


def max_age_days() -> int:
    """允许用「最近一个决策对象」的最大陈旧天数（默认 4：覆盖 Friday→Monday 的跨周末）。

    为什么要回退：盘后 job（19:45）产出的是**当日**对象，而盘中腿在 09:30–15:00 就要用它
    ——若只看"当天对象"，周一开盘时它还不存在，会把整天的买入全拦掉。他的实际做法正是
    「盘后定计划、次日执行」，所以用最近一个对象 + 标陈旧天数是符合他口径的。
    """
    try:
        return int(float(os.getenv("WOLF_DECISION_MAX_AGE_DAYS", "") or 4))
    except (TypeError, ValueError):
        return 4


def missing_policy() -> str:
    """没有任何可用对象时的策略：block（默认，符合"没预案就不买"）/ allow。"""
    v = (os.getenv("WOLF_DECISION_GATE_MISSING", "block") or "block").strip().lower()
    return v if v in ("block", "allow") else "block"


def gate_enabled() -> bool:
    """是否启用「无决策对象 → 拒绝开新腿」。**默认关**，避免突然改变生产行为。"""
    return os.getenv("WOLF_DECISION_GATE", "0").strip().lower() not in ("0", "false", "no", "")


def data_dir() -> str:
    return os.environ.get("DATA_DIR", "/app/data")


def decision_dir() -> str:
    return os.path.join(data_dir(), "decision")


def path_for(d8: str) -> str:
    return os.path.join(decision_dir(), "%s.json" % d8)


# ───────────────────────── 读当天产物（缺失返回 None 并记录）─────────────────────────
def _read_json(name: str, d8: str) -> Optional[Any]:
    for cand in (name.format(d=d8, dash="%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])), name):
        p = os.path.join(data_dir(), cand)
        try:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            continue
    return None


def _src(name: str, d8: str) -> Dict[str, Any]:
    p = os.path.join(data_dir(), name.format(d=d8, dash="%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])))
    return {"path": p, "present": os.path.isfile(p),
            "mtime": int(os.path.getmtime(p)) if os.path.isfile(p) else None}


def _norm_date8(v: Any) -> Optional[str]:
    """把日期归一成 YYYYMMDD（接受 '2026-09-16' / '20260916' / date / datetime）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        v = str(int(v))
    s = str(v).strip()
    if len(s) >= 10 and s[4] == "-":
        s = s[:10].replace("-", "")
    else:
        s = s[:8]
    return s if len(s) == 8 and s.isdigit() else None


def _days_between(a8: Optional[str], b8: Optional[str]) -> Optional[int]:
    try:
        import datetime as _dt
        a = _dt.datetime.strptime(a8, "%Y%m%d").date()
        b = _dt.datetime.strptime(b8, "%Y%m%d").date()
        return (b - a).days
    except Exception:
        return None


_PREV_TD_CACHE: Dict[str, Optional[str]] = {}


def _prev_trade_day(d8: str) -> Optional[str]:
    """d8 之前最近一个**已收盘**交易日 —— 波浪判定的数据日（as_of）正常就等于它。

    为什么是"上一交易日"而不是 d8：`apps/main_line/wave_agent.py` 08:10 跑，
    `_ensure_index_fresh()` 的注释写得很清楚「确保指数日线到最近收盘，**wave 判定 date=昨日**」。
    所以盘前 08:25 / 盘后 19:45 两个对象里 as_of=上一交易日都是**正常**的——
    2026-09-16 实测：wave_state.json 写于 09-16 08:11、内部 date='2026-09-15'。
    只有 as_of **早于**上一交易日，才说明 08:10 判浪没跑成/失败。

    复用 wolf_eod.prev_trade_day（同一交易日历口径 + 工作日回退）；取不到 → None
    （此时**不做**陈旧判断，宁可少告警也不要每天误报）；按日缓存避免重复取日历。
    """
    if d8 in _PREV_TD_CACHE:
        return _PREV_TD_CACHE[d8]
    v: Optional[str] = None
    try:
        from app.services.wolf_eod import prev_trade_day
        v = prev_trade_day(d8)
    except Exception:
        v = None
    _PREV_TD_CACHE[d8] = v
    return v


def _load_wave(d8: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """读波浪状态 + 来源元信息（**这是 2026-09-16 修的新鲜度缺陷的落点**）。

    实测（生产 /app/data）：日更的 `apps/main_line/wave_agent.py` **只写无日期的**
    `data/wave_state.json`（其 `date` 字段是带横杠的 '2026-09-16'），带日期的
    `wave_state_2026-09-xx.json` 全是历史回填产物。旧代码只找 `wave_state_{YYYYMMDD}.json`
    → 每天 `missing` 里恒有 `wave_dated`、basis 写的来源与实际不符，**且对 wave_state.json
    没有任何新鲜度校验**：08:10 判浪失败时，盘前/盘后对象会静默沿用旧档位。

    读序：带日期的（两种命名都试）→ 无日期的最新文件；后者用内部 `date` 算 as_of/stale_days。
    """
    for name in ("wave_state_{d}.json", "wave_state_{dash}.json"):
        w = _read_json(name, d8)
        if isinstance(w, dict) and w:
            fname = os.path.basename(name.format(d=d8, dash="%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])))
            p = os.path.join(data_dir(), fname)
            return w, {"path": p, "file": fname, "present": True, "kind": "dated",
                       "mtime": int(os.path.getmtime(p)) if os.path.isfile(p) else None,
                       "as_of": _norm_date8(w.get("date") or w.get("as_of")) or d8,
                       "stale_days": 0}
    w = _read_json("wave_state.json", d8)
    if isinstance(w, dict) and w:
        p = os.path.join(data_dir(), "wave_state.json")
        as_of = _norm_date8(w.get("date") or w.get("as_of"))
        return w, {"path": p, "file": "wave_state.json", "present": True, "kind": "latest",
                   "mtime": int(os.path.getmtime(p)) if os.path.isfile(p) else None,
                   "as_of": as_of, "stale_days": None}
    return None, {"path": os.path.join(data_dir(), "wave_state.json"), "file": "wave_state.json",
                  "present": False, "kind": "none", "mtime": None, "as_of": None, "stale_days": None}


# ───────────────────────── 判据层（纯函数，可测）─────────────────────────
def _l1(gate: Optional[Dict[str, Any]], ms: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """L1 方向：**优先**用方向层主线判定（池：量能占比topK ∩ r5>0 ∩ 资格闸 → 池内 r5 top1），
    没有时退回旧的 gate 结构资格闸。

    为什么换：gate 单独命中他的方向仅 ≈ 随机（75% vs 77%，见 docs/wolf-stats），
    而池判定在"他股票主线层"口径下 recall 75% / top1 54% / top3 85%（docs/wolf-structural-pool.md §十）。
    """
    if isinstance(ms, dict) and ms.get("mainline"):
        return {"value": {"mainline": ms.get("mainline"), "second": ms.get("second"),
                          "pool": list(ms.get("pool") or []),
                          "candidates": list(ms.get("rank_in_gate") or [])[:8],
                          "pool_k": ms.get("pool_k")},
                "basis": "daily_artifacts.mainline_select（方向层池判定）"}
    return {"value": None, "basis": "mainline_select 缺失（gate 回退分支已于 2026-09-13 删除）"}


def _l2(wave: Optional[Dict[str, Any]], src: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """L2 档位：波浪 operation（build/t_only/side/defense/exit）+ 级别。

    2026-09-16：basis 改为**实际**读到的文件，并把 as_of/陈旧天数带进 value（原先写死
    `wave_state_<d>.json`，而日更 agent 只写无日期的 wave_state.json → 来源名不副实）。
    """
    if not wave:
        return {"value": None, "basis": "波浪状态缺失（wave_state*.json 都没有）"}
    op = wave.get("operation") or wave.get("wave_operation")
    lvl = wave.get("level")
    sub = wave.get("sub_level")
    src = src or {}
    val: Dict[str, Any] = {"operation": op, "level": lvl, "sub_level": sub,
                           "allow_new_position": (str(op) in BUY_ALLOWED_OPS) if op else None}
    if src:
        val["wave_source"] = src.get("file") or src.get("path")
        val["wave_as_of"] = src.get("as_of")
        val["wave_stale_days"] = src.get("stale_days")
    basis = "波浪状态 %s（apps/main_line/wave_agent.py LLM 产出；as_of=%s）" % (
        src.get("file") or "wave_state*.json", src.get("as_of") or "未知（文件无 date 字段）")
    return {"value": val, "basis": basis}


def _l3(tiers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """L3 仓位：目标仓位/下限（来自 tier_targets，与 §5 映射表一致）。"""
    if not tiers:
        return {"value": None, "basis": "wolf_discipline.tier_targets（缺失）"}
    return {"value": {"target_pct": tiers.get("target_pct"), "floor_pct": tiers.get("floor_pct"),
                      "tier": tiers.get("tier") or tiers.get("operation")},
            "basis": "wolf_discipline.tier_target_pct/tier_floor_pct(operation)"}


def _l4(picks: Optional[Any]) -> Dict[str, Any]:
    """L4 选票：当日 confirm 结果（**只转述，不新增选股逻辑**）。"""
    if not picks:
        return {"value": None, "basis": "stock_confirm_result.json（缺失）"}
    syms: List[str] = []
    if isinstance(picks, dict):
        for k in ("picks", "symbols", "candidates", "stocks"):
            v = picks.get(k)
            if isinstance(v, list):
                for x in v:
                    syms.append(x.get("symbol") if isinstance(x, dict) else str(x))
                break
    elif isinstance(picks, list):
        syms = [x.get("symbol") if isinstance(x, dict) else str(x) for x in picks]
    return {"value": {"symbols": [s for s in syms if s][:20], "n": len(syms)},
            "basis": "stock_confirm_result.json（当日覆盖型）"}


def _l5(l2: Dict[str, Any], gates: Dict[str, Any]) -> Dict[str, Any]:
    """L5 买点：**是否允许开新仓** + 前置条件 + 拦阻项 + 警告（把会拦掉 L1–L4 结论的闸门显式化）。

    ⚠️ 拦阻项必须**逐条有语料支撑**（这是本项目的红线，2026-09-12 修过一次）：
      · 拦：**诱多**——他 2026-09-01「不过4000怎么诱多」、09-03「不上3WE的突破就是诱多」→ 不追高、不加仓
      · 拦：**G9 周末/长假前**——他 2026-08-21「如果还是缩量 还是不拉升…先出来一半…65%仓位过周末」→ 只减不加
      · 拦：**L2 档位不允许**——他 2026-01-17「主升75%+／调整50%／有风险30%／**下跌趋势就不做**」
      · **不拦（只警告）：跌破关口（破位）**——他 2026-08-25 说破位对应的是「指数破位后的止损」
        （对**持仓**的止损评估），而 2026-08-24 破位当天他照样「跌破了 按计划打入」→
        破位**不禁止**按预设条件买入。曾一度把它写成 blocker，会把整天的开仓全禁掉，已改正。
    """
    blockers: List[str] = []
    warnings: List[str] = []
    conditions: List[str] = []
    op_ok = (l2.get("value") or {}).get("allow_new_position")
    if op_ok is False:
        blockers.append("L2 档位=%s 不允许新开仓（他 2026-01-17 分档：下跌趋势就不做）"
                        % ((l2.get("value") or {}).get("operation")))
    g10 = gates.get("G10_volume_gate") or {}
    if g10.get("breakdown_risk"):
        warnings.append("G10：已跌破关口（破位）→ 按他 2026-08-25 口径这是**止损/减仓评估**，"
                        "不是禁买；但按 2026-08-24 的做法**只按预设条件买**，不追、不临时起意")
    if g10.get("fake_breakout_risk"):
        blockers.append("G10：攻关口而量能未达突破级 → 判为**诱多**（他 2026-09-03），不追高、不加仓")
    g9 = gates.get("G9_weekend_hedge") or {}
    if g9.get("active"):
        blockers.append("G9：周末/长假前 缩量∧未拉升 → **只减不加**（他 2026-08-21）")
    if (gates.get("A5_trade_window") or {}).get("enabled"):
        conditions.append("A5 时间窗生效（9:45-10:00 / 14:00-14:30）")
    conditions.append("买点位置：**只在下跌里买、高开不追**（蓝图 §9 第 9 条）")
    allowed = (op_ok is not False) and not blockers
    return {"value": {"allowed": allowed, "conditions": conditions, "blockers": blockers,
                      "warnings": warnings},
            "basis": "L2 档位 + G9/G10/A5 当日状态（汇层，逐条语料支撑见 docstring）"}


def _l6(gates: Dict[str, Any]) -> Dict[str, Any]:
    """L6 兑现：当日生效的离场规则（他的一天里的兑现动作）。"""
    rules = ["吃一口减一半（板上减半 / 小赚兑现）", "破黄线离场 / 破支撑离场", "BOLL 上轨减半、中轨带顶部阶段前提"]
    if (gates.get("G9_weekend_hedge") or {}).get("active"):
        rules.append("G9：近两日 T 仓减半、收盘目标 ≤65%、下周一确认安全再拿回")
    return {"value": {"rules": rules}, "basis": "wolf_discipline（board_half/profit_take/boll/cushion）+ G9"}


def _gates_state() -> Dict[str, Any]:
    """当日各闸门/开关状态（读各模块的 enabled()/状态文件，读不到就记 unknown）。"""
    out: Dict[str, Any] = {}
    def _try(name: str, fn) -> None:
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": "%s: %s" % (type(e).__name__, str(e)[:60])}
    def _g10():
        from app.services.wolf_volume_gate import enabled as en, load as ld
        st = ld() or {}
        return {"enabled": bool(en()), "as_of": st.get("as_of"),
                "tag": ((st.get("level") or {}).get("tag")),
                "breakdown_risk": st.get("breakdown_risk"), "fake_breakout_risk": st.get("fake_breakout_risk")}
    def _g9():
        from app.services.wolf_weekend_hedge import enabled as en, load as ld
        st = ld() or {}
        return {"enabled": bool(en()), "active": bool(st.get("active")), "reason": st.get("reason")}
    def _a5():
        from app.services.wolf_trade_window import enabled as en
        return {"enabled": bool(en())}
    def _a9():
        from app.services.wolf_gap_open import _caution_on
        return {"enabled": bool(_caution_on())}
    _try("G10_volume_gate", _g10)
    _try("G9_weekend_hedge", _g9)
    _try("A5_trade_window", _a5)
    _try("A9_gap_caution", _a9)
    return out


def build(d8: str, gate: Optional[Dict[str, Any]] = None, wave: Optional[Dict[str, Any]] = None,
          tiers: Optional[Dict[str, Any]] = None, picks: Optional[Any] = None,
          gates: Optional[Dict[str, Any]] = None,
          ms: Optional[Dict[str, Any]] = None,
          sources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """组装决策对象（参数可注入 → 单测不依赖文件/数据库）。

    2026-09-13：`gate` 参数已废弃（mainline_gate 模块删除）；L1 方向层改用 `ms`（方向层池判定）。
    """
    import datetime as _dt
    ms = ms if ms is not None else (_read_json("wolf_mainline_select.json", d8)
                                    or (_read_json("main_line_state.json", d8) or {}).get("mainline_select"))
    # wave：2026-09-16 起返回 (状态, 来源元信息)；注入 wave 时不读文件（单测用）
    if wave is not None:
        wave_src: Dict[str, Any] = {"kind": "injected", "present": True, "file": "(注入)",
                                    "path": None, "mtime": None,
                                    "as_of": _norm_date8((wave or {}).get("date")),
                                    "stale_days": None}
    else:
        wave, wave_src = _load_wave(d8)
    # 新鲜度 = as_of 与「d8 之前最近一个已收盘交易日」的差距（0=正常，见 _prev_trade_day 注释）
    wave_src["expected_as_of"] = _prev_trade_day(d8)
    _sd = _days_between(wave_src.get("as_of"), wave_src.get("expected_as_of"))
    wave_src["stale_days"] = max(0, _sd) if _sd is not None else None
    picks = picks if picks is not None else _read_json("stock_confirm_result.json", d8)
    gates = gates if gates is not None else _gates_state()
    l2 = _l2(wave, wave_src)
    if tiers is None:
        # 生产里的真实函数名是 tier_target_pct / tier_floor_pct（2026-09-12 核对）
        op = (l2.get("value") or {}).get("operation")
        try:
            from app.services.wolf_discipline import tier_target_pct, tier_floor_pct
            tgt = tier_target_pct(op)
            flr = tier_floor_pct(op)
            tiers = {"target_pct": (float(tgt) / 100.0) if tgt else None,
                     "floor_pct": (float(flr) / 100.0) if flr else None, "tier": op}
        except Exception:
            tiers = {}
    layers = {"L1_direction": _l1(None, ms), "L2_operation": l2, "L3_position": _l3(tiers),
              "L4_picks": _l4(picks), "L5_entry": _l5(l2, gates), "L6_exit": _l6(gates)}
    src = sources if sources is not None else {
        # "gate": 2026-09-13 删除——mainline_gate 模块与产物已废弃（主线判定唯一＝方向层池判定）
        # 2026-09-16：wave_dated/wave_latest 两个键合并成 "wave"（带 kind/as_of/stale_days），
        #            否则盘前盘后每天都把不存在的带日期文件记为缺失，纯噪声且来源名不副实。
        "wave": wave_src, "picks": _src("stock_confirm_result.json", d8),
        # "heat": 2026-09-13 移除——heat_v2 已停用（与他做法反向 t=-11.89），不再作为源文件检查项，
        #          否则每天会被记为 missing → 可能触发"数据不全"降级。
    }
    wsrc = (src or {}).get("wave") or wave_src or {}
    missing = [k for k, v in (src or {}).items() if isinstance(v, dict) and not v.get("present")]
    missing += [k for k, v in layers.items() if (v.get("value") is None)]
    # ── 诚实告警（2026-09-16 扩）：波浪状态的来源/新鲜度 ──
    warns: List[str] = []
    stale: List[str] = []
    try:
        _today = _dt.date.today().strftime("%Y%m%d")
        kind = wsrc.get("kind")
        if kind == "latest" and d8 != _today:
            warns.append("wave 用了不带日期的最新文件（wave_state.json），**回填历史日时可能不是当日状态**"
                         "（look-ahead 风险），只可作参考，不可用于回测")
        if kind in ("latest", "dated"):
            sd = wsrc.get("stale_days")
            as_of = wsrc.get("as_of")
            exp = wsrc.get("expected_as_of")
            if as_of is None:
                warns.append("波浪状态文件 %s 没有 date 字段 → **无法判定新鲜度**，L2 档位按最新判浪执行"
                             % wsrc.get("file"))
            elif isinstance(sd, int) and sd > 0:
                stale.append("wave(as_of=%s, 应为%s, 陈旧%d天)" % (as_of, exp, sd))
                warns.append("波浪状态的数据日 %s 早于最近已收盘交易日 %s（陈旧 %d 天）→ 08:10 判浪"
                             "**可能没跑成/失败**，L2 档位是旧口径：只按预设条件执行、不据此加仓"
                             % (as_of, exp, sd))
    except Exception:
        pass
    obj = {"date": d8, "warnings": warns, "stale": stale,
           "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
           "layers": layers, "gates": gates, "sources": src, "missing": missing,
           "entry_allowed": bool(((layers["L5_entry"]["value"]) or {}).get("allowed")),
           "note": ("本对象只汇总与准入，不新增选股/择时逻辑；每条带 basis；"
                    "缺文件时该层为 null 并记入 missing；陈旧但可用的源记入 stale（不拦开仓）")}
    return obj


def _load_pg(d8: str) -> Optional[Dict[str, Any]]:
    """从 daily_artifacts 读决策对象（主存）。失败/没有 → None。"""
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
    except Exception:
        return None
    db = None
    try:
        db = SessionLocal()
        row = db.execute(text(
            "SELECT payload FROM daily_artifacts WHERE trade_date = :d AND artifact_key = 'decision'"
        ), {"d": d8}).mappings().first()
        if not row:
            return None
        pl = row["payload"]
        return pl if isinstance(pl, dict) else json.loads(pl)
    except Exception:
        return None
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


def _save_pg(obj: Dict[str, Any]) -> Dict[str, Any]:
    """写主存：upsert 进 daily_artifacts，并让 payload.revision 递增（同日重算可追溯）。"""
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
    except Exception as e:
        return {"ok": False, "reason": "import:%s" % type(e).__name__}
    db = None
    try:
        db = SessionLocal()
        prev = db.execute(text(
            "SELECT payload FROM daily_artifacts WHERE trade_date = :d AND artifact_key = 'decision'"
        ), {"d": obj["date"]}).mappings().first()
        rev = 1
        if prev:
            try:
                old = prev["payload"] if isinstance(prev["payload"], dict) else json.loads(prev["payload"])
                rev = int(old.get("revision") or 0) + 1
            except Exception:
                rev = 1
        obj["revision"] = rev
        obj["updated_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        db.execute(text("""
            INSERT INTO daily_artifacts (trade_date, artifact_key, payload, sha256_16, src_path, src_mtime)
            VALUES (:d, 'decision', CAST(:p AS jsonb), :h, :sp, now())
            ON CONFLICT (trade_date, artifact_key) DO UPDATE
              SET payload = EXCLUDED.payload, sha256_16 = EXCLUDED.sha256_16,
                  src_path = EXCLUDED.src_path, src_mtime = EXCLUDED.src_mtime, created_at = now()
        """), {"d": obj["date"], "p": json.dumps(obj, ensure_ascii=False),
               "h": None, "sp": "daily_decision"})
        db.commit()
        return {"ok": True, "revision": rev}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "reason": str(e)[:80]}
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


def run(d8: Optional[str] = None, save: bool = True) -> Dict[str, Any]:
    import datetime as _dt
    d8 = d8 or _dt.date.today().strftime("%Y%m%d")
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    obj = build(d8)
    pg = {"ok": False, "reason": "not_saved"}
    if save:
        # ① 主存：PG（同日重算 revision 递增）
        pg = _save_pg(obj)
        # ② 镜像：文件（供人查看 / 回放 / DB 不可用时兜底）
        try:
            os.makedirs(decision_dir(), exist_ok=True)
            with open(path_for(d8), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            with open(os.path.join(decision_dir(), "latest.json"), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
        except Exception as e:
            if not pg.get("ok"):
                return {"ok": False, "reason": "write_failed:pg=%s,file=%s" % (pg.get("reason"), str(e)[:40])}
            obj["file_error"] = str(e)[:80]
    l5 = (obj["layers"]["L5_entry"]["value"]) or {}
    print("[decision] %s rev=%s 主存PG=%s L2=%s(%s, as_of=%s) L5允许=%s 拦阻=%d 缺失层=%s 陈旧=%s"
          % (d8, obj.get("revision"), pg.get("ok"),
             (obj["layers"]["L2_operation"]["value"] or {}).get("operation"),
             (obj["layers"]["L2_operation"]["value"] or {}).get("wave_source"),
             (obj["layers"]["L2_operation"]["value"] or {}).get("wave_as_of"),
             l5.get("allowed"), len(l5.get("blockers") or []), obj["missing"], obj.get("stale") or "无"))
    return {"ok": True, **obj}


def load(d8: Optional[str] = None) -> Dict[str, Any]:
    """读序 = **PG（主存）→ 文件（镜像/latest）**。"""
    import datetime as _dt
    d8 = d8 or _dt.date.today().strftime("%Y%m%d")
    obj = _load_pg(d8)
    if obj and obj.get("date") == d8:
        return obj
    for p in (path_for(d8), os.path.join(decision_dir(), "latest.json")):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            continue
    return {}


def _latest_pg_at_or_before(d8: str) -> Optional[Dict[str, Any]]:
    """主存里查"不晚于 d8 的最近一个决策对象"。失败 → None。（单测会打桩此函数）"""
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT payload FROM daily_artifacts WHERE artifact_key = 'decision' "
                "AND trade_date <= :d ORDER BY trade_date DESC LIMIT 1"), {"d": d8}).mappings().first()
            if row:
                pl = row["payload"]
                return pl if isinstance(pl, dict) else json.loads(pl)
        finally:
            db.close()
    except Exception:
        pass
    return None


def latest_within(d8: str, max_age: Optional[int] = None) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """取**不晚于** d8 的最近决策对象，返回 (obj, 陈旧天数)。没有 → (None, None)。"""
    import datetime as _dt
    mx = max_age_days() if max_age is None else int(max_age)
    try:
        base = _dt.datetime.strptime(d8, "%Y%m%d").date()
    except ValueError:
        return None, None
    # ① PG（主存）：直接按日期倒序找
    obj = _latest_pg_at_or_before(d8)
    if obj and obj.get("date"):
        try:
            stale = (base - _dt.datetime.strptime(str(obj["date"]), "%Y%m%d").date()).days
            if 0 <= stale <= mx:
                return obj, stale
        except ValueError:
            pass
    # ② 文件镜像兜底：目录里找最近的
    try:
        cands = sorted([f[:-5] for f in os.listdir(decision_dir())
                        if f.endswith(".json") and f[:-5].isdigit() and f[:-5] <= d8], reverse=True)
    except Exception:
        cands = []
    for c in cands:
        obj = load(c)
        if not obj or obj.get("date") != c:
            continue
        try:
            stale = (base - _dt.datetime.strptime(c, "%Y%m%d").date()).days
        except ValueError:
            continue
        if 0 <= stale <= mx:
            return obj, stale
    return None, None


_ALLOW_CACHE: Dict[str, Any] = {"at": 0.0, "key": "", "value": None}
ALLOW_TTL_SEC = 60


def _allow_ttl() -> float:
    try:
        return float(os.getenv("WOLF_DECISION_TTL_SEC", "") or ALLOW_TTL_SEC)
    except (TypeError, ValueError):
        return ALLOW_TTL_SEC


def entry_allowed_cached(d8: Optional[str] = None) -> Tuple[bool, str]:
    """带 60s 进程内缓存的准入（给高频触发路径用，避免每次触发都查库）。

    决策对象一天只变两次（盘后/盘前），60s TTL 足够；`WOLF_DECISION_TTL_SEC` 可调。
    """
    import time as _t
    d8 = d8 or __import__("datetime").date.today().strftime("%Y%m%d")
    now = _t.time()
    if _ALLOW_CACHE["value"] is not None and _ALLOW_CACHE["key"] == d8 \
            and now - float(_ALLOW_CACHE["at"] or 0) < _allow_ttl():
        return _ALLOW_CACHE["value"]
    v = entry_allowed(d8)
    _ALLOW_CACHE.update({"at": now, "key": d8, "value": v})
    return v


def entry_allowed(d8: Optional[str] = None) -> Tuple[bool, str]:
    """给腿用的准入：返回 (是否允许开新仓, 原因)。**永不用于卖出**。

    · `WOLF_DECISION_GATE=0`（默认）→ 永远放行（保持现状）
    · 置 1：优先当日对象；没有则用**最近一个**（≤ `WOLF_DECISION_MAX_AGE_DAYS`，默认 4 天，
      覆盖跨周末），并在 reason 里标出陈旧天数；完全没有 → 按 `WOLF_DECISION_GATE_MISSING`
      （默认 block）处理
    """
    if not gate_enabled():
        return True, "decision_gate_off"
    import datetime as _dt
    d8 = d8 or _dt.date.today().strftime("%Y%m%d")
    obj = load(d8)
    stale = 0
    if not obj or obj.get("date") != d8:
        obj, stale = latest_within(d8)
    if not obj:
        if missing_policy() == "allow":
            return True, "无决策对象但 MISSING=allow（%s）" % d8
        return False, "无可用决策对象（%s 及最近 %d 天内都没有）" % (d8, max_age_days())
    tag = "" if stale == 0 else "（用 %s 的对象，陈旧 %d 天）" % (obj.get("date"), stale)
    l5 = (obj.get("layers", {}).get("L5_entry", {}) or {}).get("value") or {}
    if l5.get("allowed"):
        return True, "L5 允许" + tag
    return False, "L5 拦阻%s: %s" % (tag, "; ".join(l5.get("blockers") or ["未说明"]))


def directive(d8: Optional[str] = None) -> str:
    """给 prompt 注入的一行摘要；关闭或没有对象时返回空。"""
    if not enabled():
        return ""
    obj = load(d8)
    if not obj:
        return ""
    L = obj.get("layers") or {}
    l1 = (L.get("L1_direction") or {}).get("value") or {}
    l2 = (L.get("L2_operation") or {}).get("value") or {}
    l3 = (L.get("L3_position") or {}).get("value") or {}
    l5 = (L.get("L5_entry") or {}).get("value") or {}
    head = ("🧭 每日决策对象（%s）：L1 主线=%s｜L2 档位=%s（允许新开仓=%s）｜L3 目标仓位=%s%%"
            % (obj.get("date"), "、".join((l1.get("confirmed") or [])[:3]) or "—",
               l2.get("operation") or "—", l2.get("allow_new_position"),
               ("%.0f" % (float(l3["target_pct"]) * 100)) if isinstance(l3.get("target_pct"), (int, float)) else "—"))
    # 2026-09-16：档位来源陈旧时必须显式标注（否则 prompt 会把旧口径当当天口径用）
    _sd = l2.get("wave_stale_days")
    if isinstance(_sd, int) and _sd > 0:
        head += "｜⚠️ 档位陈旧 %d 天（wave as_of=%s）" % (_sd, l2.get("wave_as_of") or "未知")
    if l5.get("blockers"):
        return head + "｜⛔ 今日拦阻: " + "；".join(l5["blockers"])
    return head + "｜✅ 今日无拦阻（开新仓仍须满足 L5 前置条件）"
