# -*- coding: utf-8 -*-
"""做T系统 · TMonitor 监控器（Worker daemon 线程，30s 周期）。

依据 final-t-plan.md §④ 与 spec t-monitor-trigger：
- 分层采样：核心底仓(≤10-20)腾讯 qt 直连(use_cache=False) + ThreadPoolExecutor(≤5) 并发 + jitter；观察池 30s-1min 缓存
- 盘中量比归一：[当前累计换手×(240/已开连续分钟)]/近N日同刻均值（修正 indicator.py turnover_rate/2.0 bug）
- 滞回/去抖/armed 状态机 + 复合企稳确认（价∧量能∧分时企稳）
- regime 前置 GATE（BLOCKED 不写 / MANUAL_ONLY 挂人）
- 命中 → 写 t_triggers(pending, snapshot{suggest_bid/ask, slippage_budget, confidence})
- 14:45 后禁新开仓；Worker 永不直接下单
"""
from .t_leg_kinds import BUY_LEG_KINDS, is_buy_leg, LOWDIP_KINDS, is_lowdip, PREVLOW_M5_KINDS, is_prevlow_m5  # noqa: F401  §9.496 单一来源
import os


def _armdb_root(start: str) -> str:
    """向上找到含 `jobs/arm_db.py` 的仓库根（账本 §9.539：写死层数会数错 ✗）。"""
    p = os.path.dirname(start)
    while p and p != "/" and not os.path.exists(os.path.join(p, "jobs", "arm_db.py")):
        p = os.path.dirname(p)
    return p or os.path.dirname(start)
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import t_db
from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote, fetch_quote_one
# ★ 账本 §9.709 ✓（用户：「旧的 t_regime 直接删掉，已证明是负收益」）：
#   环境门只用**狼大浪型** ✓（`_is_trading_time` 是**交易时段工具**、与收益无关 ⇒ 保留 ✓）
from app.services.t_regime import _is_trading_time
# ★ 账本 §9.713 ✓（测试 test_monitor_wired_markers 早已断言这一点 ✗ —— 原文：「止损口径未挂进 _check_stop_loss」✓）：
#   用户口径 ✓：「**止损只在指数大级别破位**」＋「地量不割」
#   `t_playbook.stop_allowed()` 实现完整 ✓，但 **`_check_stop_loss` 从未调用它** ✗
#     ⇒ "写了模块没挂上" ✗（正是那条测试防的事 ✓，它一直在报红 ✓，是我先前误判为"改名遗留"✗）
#   ⇒ 现挂上 ✓（见 `_check_stop_loss` 开头 ✓）；下面这个名字供测试/文档引用 ✓
from app.services.t_playbook import STOP_BY_BREAK as STOP_BY_INDEX_BREAK

# ── v3 统一止损层灰度（2026-10-23）─────────────────────────────────────────────
# 2026-10-23 旧规则删除后：只走 wolf_stop_layer.evaluate()。
# `reduce_to` 暂时**只记录/提示，不自动卖**（灰度对比阶段）。
STOP_V3 = True   # 2026-10-23 旧规则删除后恒为 v3
_V3_BYPASS_KINDS = frozenset({
    "stop_loss", "custom_support_sell", "wolf_passive_stop_sell",
    "wolf_early_swing_sell", "wolf_boll_mid_exit",
})


# ★ 账本 §9.713 ✓：**轮次度量**（`round_timing()`）—— 测试 `test_monitor_wired_markers` 一直在断言它 ✓
#   用途 ✓：让「闸门到底拦了几次」**看得见** ✗（今晚反复吃亏的正是"看不见" ✗）
#   口径 ✓：只累加、不清零（进程内单调 ✓）；键名与测试一致 ✓（`stop_gate_skip` ✓）
_ROUND_TIMING = {"stop_gate_skip": 0, "stop_gate_pass": 0, "stop_gate_error": 0}


def round_timing() -> dict:
    """本轮/进程内的**止损闸度量** ✓（键：stop_gate_skip / stop_gate_pass / stop_gate_error ✓）。"""
    try:
        return dict(_ROUND_TIMING)
    except Exception as _e_rt:
        print("[TMonitor] round_timing 失败: %s" % str(_e_rt)[:60], flush=True)
        return {}

from app.services.wave_gate import gate_regime as compute_regime   # 波浪版替身 ✓
# ★ 账本 §9.659 ✓（用户：「只依据 wave_state，缺失就现场补，去掉这个」）
#   环境门改为**可切换** ✓：`WOLF_WAVE_GATE_ONLY=1` ⇒ 用**狼大的浪型口径** ✓
#   （`wave_gate.check_gate` ✓：`wave_state.level == down` ⇒ BLOCKED ✓，缺档**现场补** ✓）
#   默认 0 ⇒ 仍走旧 `t_regime` ✓ ⇒ **生产行为逐位不变** ✓
def _sim_day8() -> str:
    """当前**模拟日**（回测里 = `DATA_DIR` 的末段 ✓；生产里退化为今天 ✓）。

    ★ 账本 §9.708 ✓（用户 2026-10-07：「还没做波浪判据?」✗ —— 查出来真因是这里）：
      实测日志 ✗：`357 × [wave_gate] wave_state 过期 11 天 ⇒ 现场补（**as_of=-**）`
              ＋ `357 × judge_wave 无有效 level（'无数据'）⇒ 不写回，沿用旧档`
      ⇒ 调用方 `check_gate(kind)` **没传 as_of** ✗ ⇒ `judge_wave(None)` 只能返回"无数据" ✗
        ⇒ 现场补永远失败 ⇒ **一直沿用旧档** ⇒ 狼大浪型闸**形同虚设** ✗（买入理由里只剩旧 regime ✓）
      ⇒ 本函数给出模拟日 ⇒ 现场补能判**当天** ✓
    """
    try:
        _seg = os.path.basename(os.path.normpath(os.environ.get("DATA_DIR", "") or ""))
        if len(_seg) == 8 and _seg.isdigit():
            return _seg
    except Exception as _e_seg:
        # ★ §9.709 ✓：留痕（防回潮 ✓）—— 取模拟日失败不致命，但要能看见 ✓
        print("[t_monitor] _sim_day8 取 DATA_DIR 末段失败: %s" % str(_e_seg)[:70], flush=True)
    try:
        import datetime as _d8
        return _d8.datetime.now().strftime("%Y%m%d")
    except Exception:
        return ""


def check_gate(kind="low_buy", regime_state=None):
    """环境门 —— **只用狼大浪型口径** ✓（账本 §9.709 ✓）。

    ★ 用户 2026-10-07：「旧的 t_regime 直接删掉，这个东西已经证明是负收益了」✓
      ⇒ 不再回退 `t_regime` ✗；`wave_gate` 不可用/取不到浪型时**放行** ✓（fail-open ✓，不假装有闸 ✓）。
    """
    try:
        from app.services.wave_gate import check_gate as _wg_check
        return _wg_check(kind, as_of=_sim_day8() or None)
    except Exception as _e_wg:
        print("[t_monitor] wave_gate 不可用 ⇒ **放行**（不再回退 t_regime ✓）: %s" % str(_e_wg)[:80], flush=True)
        return {"allowed": True, "mode": "auto", "regime": "ACTIVE", "gate": "ALLOWED",
                "why": "wave_gate 不可用（放行 ✓）"}


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


MONITOR_INTERVAL = 30       # 秒
INITIAL_OFFSET = 20         # 错峰启动
# ── 回测确定性（2026-09-23）：两个开关都**库内默认沿用现状** ⇒ 生产逐字不变 ────────────
# 为什么需要：实测同配置两臂（T11 vs T12，配置只差一个"触发 0 次"的开关）第 1 天问 AI 的
#   腿就不同（13 条 vs 7 条），说明回放里混着**运行级非确定性**；其中两项可在此收敛：
#   ① 并发取价 `MAX_WORKERS`：分批并发 ⇒ 在"时钟被钉住的回放"里腿被评估的时刻受调度影响；
#   ② jitter：`wait = interval + ((time.time()*1000) % (2J+1) - J)` 用**墙钟**抖动量 ⇒ 每轮间隔随进程变。
# `WOLF_MON_WORKERS=1` + `WOLF_MON_JITTER=0` 把这两项钉死（只该在回测里开）。
# 另一项 `PYTHONHASHSEED=0` 必须在**进程启动前**设（不是代码能控的），见臂脚本。
MAX_WORKERS = max(1, int(os.getenv("WOLF_MON_WORKERS", "5") or 5))   # 并发取价上限
JITTER = max(0, int(os.getenv("WOLF_MON_JITTER", "3") or 3))         # ±3s（0=不抖）
MAX_CORE_SYMBOLS = 20       # 核心底仓数量上限
MIN_TURNOVER_BASE = 0.5     # 量比基准兜底 %
COOLDOWN_SECONDS = 300      # 同条件去抖冷却（5min）

# 2026-09-02 架构修正: 只监控股票任务账户, 只跑狼大做T表达式, 停自动维护
T_MONITOR_ACCOUNT = os.getenv("T_MONITOR_ACCOUNT", "stock")
# 持仓口径（2026-09-14 用户拍板）：**只读 stock 账户**；t 账户是测试账户、暂时不使用。
# 需要临时切回 t 时设 WOLF_POSITION_ACCOUNT=t（不用改调用点）。
POS_ACCOUNT = os.getenv("WOLF_POSITION_ACCOUNT", T_MONITOR_ACCOUNT)
T_MONITOR_AUTO_MAINTAIN = os.getenv("T_MONITOR_AUTO_MAINTAIN", "0") == "1"
print("[TM-BUILD] t_monitor 已加载 诊断版(§9.243) ✓", flush=True)

# ★ 2026-10-14（用户：「🟡 浪型 side ⇒ 需 agent 确认 这个也去掉吧」✓）：
#   那条「请求确认」QQ 每次触发都推（同一个标的 10.13 → 10.17 → 10.32 连着推 ✗），而
#   AI 的裁决现在已回推 QQ（`t_ai_agent._notify_ai_decision`）⇒ 请求属重复 ⇒ 默认**不推** ✓。
#   要恢复：`WOLF_WAVE_CONFIRM_QQ=1`（并按 (标的, 腿型) 每小时去重 ✓）。
_WAVE_CONFIRM_SEEN: Dict[str, float] = {}


def _wave_confirm_should_push(symbol: str, trigger_kind: str, dedup_sec: float = 3600.0) -> bool:
    """「浪型 side ⇒ 需 agent 确认」的 QQ 请求是否该推（默认不推，见上方注释）。绝不抛。"""
    try:
        if str(os.getenv("WOLF_WAVE_CONFIRM_QQ", "0")).strip().lower() not in ("1", "true", "yes", "on"):
            return False
        _k = "%s|%s" % (symbol or "?", trigger_kind or "-")
        _now = time.time()
        if _now - float(_WAVE_CONFIRM_SEEN.get(_k, 0) or 0) < float(dedup_sec):
            return False
        _WAVE_CONFIRM_SEEN[_k] = _now
        return True
    except Exception:
        return False

# ── 形态腿量比补齐（2026-09-24；`WOLF_VOL_RATIO_FILL` 库内默认 0 ⇒ 生产逐位不变）──────────
# 病灶（账本 §9.13）：`_insert_wolf_trigger` 原先**只从 quote 读** vol_ratio，而**回测取数替身
#   `fetch_tencent_quote` 不提供该字段** ⇒ `snapshot.vol_ratio` 恒 None ⇒
#   ① 放量破位门（`wolf_dip_volume_gate`：放量破前低 ⇒ 买腿不执行）对**形态腿/AI 腿**恒 fail-open
#      —— 实测四条臂 **0 次真实拦截**（日志里 DIP_VOL 只有 [pins] 那行）；
#   ② AI 被要求判「恐慌放量追跌（**量比骤升**+创新低）」，却拿不到量比 ⇒ 只能从散文里猜
#      —— 那正是抛硬币的主因（13 个抛硬币节点里 9 个是它；补上量比后稳定度 0.56→0.80）。
# 条件腿那条路**自己算**（`calc_volume_ratio_at`）⇒ 有数；只有形态腿哑。本补齐用**同一算法**，
#   基准优先取**该股自己的**近5日同刻换手均值，取不到才退回 `MIN_TURNOVER_BASE`。
VOL_RATIO_FILL = os.getenv("WOLF_VOL_RATIO_FILL", "0").strip().lower() in ("1", "true", "yes", "on")
_VR_BASE_CACHE: Dict[Any, Any] = {}


def symbol_turnover_base(symbol: str) -> Optional[float]:
    """该股换手基准（近5日同刻均值 %）；取不到 → None（调用方退回 MIN_TURNOVER_BASE）。按日缓存。"""
    key = (symbol, datetime.now().strftime("%Y%m%d"))
    if key in _VR_BASE_CACHE:
        return _VR_BASE_CACHE[key]
    val = None
    try:
        from app.services.t_turnover_profile import compute_turnover_profile
        prof = compute_turnover_profile(symbol) or {}
        val = float(prof.get("same_minute_avg") or 0) or None
    except Exception:
        val = None
    _VR_BASE_CACHE[key] = val
    return val


# ── 回测数据面直读（账本 §9.314 ✓）：加仓检查用 ✓ ─────────────────────────────
def _sandbox_price(symbol: str, today8: str = "") -> dict:
    """从**回测数据包**取价（**引擎同源** ✓）：返回 {cur, low, open, res, vr} ✓

    为什么 ✗：回测**离线**（`BT_NET_OFFLINE` ✓）⇒ 联网取数必然空 ⇒ `_cur=0` ⇒ 静默跳过 ✗
    数据面 ✓：`data/_bt_full/pack_shared/*/stock_daily/<SYM>.json` ✓ ／ `.../m5/<SYM>.json` ✓
    安全 ✓：**一律按 today8 过滤** ✓（探针里见过补齐到 2026-09-29 的未来数据 ✗）
    """
    import glob as _g
    import json as _j
    out = {"cur": 0.0, "low": 0.0, "open": 0.0, "high": 0.0, "res": 0.0, "vr": 0.0}
    _sym = str(symbol or "").upper()
    _six = _sym[-6:]
    _roots = []
    for _pat in ("data/_bt_full/pack_shared/*/stock_daily", "data/_bt_full/*/pack/stock_daily",
                 "data/_bt_full/pack_shared/*/index_daily"):
        _roots += _g.glob(_pat)
    # ① 日线 ✓（当日过滤 ✓ 防未来数据）
    for _r in _roots:
        _f = os.path.join(_r, _sym + ".json")
        if not os.path.exists(_f):
            continue
        try:
            _rows = [b for b in _j.load(open(_f, encoding="utf-8"))
                     if str(b.get("trade_date") or b.get("date") or "").replace("-", "")[:8]]
        except Exception as _e_sil1:
            _silent_alert("t_monitor.py:110", _e_sil1)
            continue
        _rows.sort(key=lambda b: str(b.get("trade_date") or b.get("date") or ""))
        _ok = [b for b in _rows
               if not today8 or str(b.get("trade_date") or b.get("date") or "").replace("-", "")[:8] <= today8]
        if not _ok:
            continue
        _last = _ok[-1]
        out["cur"] = float(_last.get("close") or 0)
        out["low"] = float(_last.get("low") or 0)
        out["open"] = float(_last.get("open") or 0)
        out["high"] = float(_last.get("high") or 0)          # 账本 §9.344：校验要用 ✓
        _prev = _ok[:-1][-20:]
        if _prev:
            out["res"] = max(float(b.get("high") or 0) for b in _prev)      # 前 20 日最高 ✓
        _vols = [float(b.get("vol") or 0) for b in _ok[-6:-1]]
        _lastv = float(_last.get("vol") or 0)
        if _vols and sum(_vols) > 0 and _lastv > 0:
            out["vr"] = round(_lastv / (sum(_vols) / len(_vols)), 3)        # 当日量/5日均量 ✓
        break
    # ② 当日 m5 ✓（更贴近盘中：取当日最低/最新 ✓）
    for _pat in ("data/_bt_full/pack_shared/*/m5", "data/_bt_full/*/pack/m5"):
        for _r in _g.glob(_pat):
            _f = os.path.join(_r, _sym + ".json")
            if not os.path.exists(_f):
                continue
            try:
                _d = _j.load(open(_f, encoding="utf-8"))
            except Exception as _e_sil2:
                _silent_alert("t_monitor.py:138", _e_sil2)
                continue
            _bars = _d.get(today8) if isinstance(_d, dict) else None
            if not _bars and isinstance(_d, dict):
                _ks = [k for k in _d if str(k).replace("-", "")[:8] == today8]
                _bars = _d.get(_ks[0]) if _ks else None
            if _bars:
                _lo = [float(b.get("low") or b.get("close") or 0) for b in _bars if b]
                _cl = [float(b.get("close") or 0) for b in _bars if b]
                if _lo:
                    out["low"] = min([x for x in _lo if x] or [out["low"]])
                if _cl:
                    out["cur"] = _cl[-1] or out["cur"]
                _hi2 = [float(b.get("high") or 0) for b in _bars if b]
                if _hi2:
                    out["high"] = max([x for x in _hi2 if x] or [out["high"]])
            break
    return out


def wolf_leg_vol_ratio(quote: dict, symbol: Optional[str] = None,
                       now: Optional[datetime] = None) -> Optional[float]:
    """形态腿（`wolf_t_rules` / AI 腿）的盘中量比：quote 自带优先；缺则按**条件腿同口径**补算。

    开关 `WOLF_VOL_RATIO_FILL` 关 ⇒ 只返回 quote 自带值（即旧行为，缺则 None）。
    """
    try:
        _v = float((quote or {}).get("vol_ratio") or (quote or {}).get("vr") or 0) or None
    except Exception:
        _v = None
    if _v is not None or not VOL_RATIO_FILL:
        return _v
    try:
        base = symbol_turnover_base(symbol) if symbol else None
        cond = {"benchmark_turnover_profile": {"same_minute_avg": base}} if base else {}
        return calc_volume_ratio_at(cond, quote or {}, now or datetime.now())
    except Exception:
        return None



# ── 开盘不追高闸·**条件单补线**（2026-09-23，开关默认 0 ⇒ 生产逐位不变）────────────
# 背景（实测 bug）：闸门装在 `gateway_execute`，但其守卫是 `if ... and trigger_id`，
#   而**条件单成交这条路径只传 `condition_id`、不传 `trigger_id`** ⇒
#   `trend_break_buy` 之类的条件单买入**完全漏过**两类闸（不追高 / 开盘快速拉升）。
#   实例：T5 的 SH603061 20260414 09:40 买 323.30，当日开盘 308.0 ⇒ 已 +4.97%（阈值 3.0%），
#   本该被拦却成交；该腿后来是全臂最大亏损腿。SZ001309 20260318 / SZ002294 20260401 同型。
#   闸门作者本意是覆盖条件腿的（t_monitor 注释："条件腿写 snapshot.fields.quote.open"）。
# 本补线**只补开盘闸**（不动换手闸/不追高闸），以保持单变量；`WOLF_OC_COND_BUY=0` 时逐位回到旧行为。
# 语料：狼大 2025-04-15 条件6「如果当日开盘高开快速拉升，或者低开快速拉升想追进去的，
#       在下午2.00-2.30这个时间段进行回补」＋ 2026-01-12「千万绝对不要开盘买」。
OC_COND_BUY = os.getenv("WOLF_OC_COND_BUY", "0").strip().lower() in ("1", "true", "yes", "on")

# ── 尾盘未确认 → 必 T 出（2026-09-25；库内默认**关**，回测 pins 置 1）──────────────────
# 语料（逐字）：2025-06-04「盘中做T尾盘必须出场，**不加仓隔夜**，确保安全」；
#   2025-08-14「一直到**2点附近不管有没有涨幅我都T完**」；2026-03-05「做T当日出来，**不留过夜**」；
#   2026-03-17「做T的仓位**当日出来**」；2026-03-31「做T的当日都会出来，不留过夜」；
#   2026-08-12「T+0 的仓没必要留在里面…平时不留仓位」。
# 为什么默认关：旧行为是「day_end 已降级: 不做'未确认→必卖'(那批几乎全亏)」—— 那次判断基于当时
#   （未修计价空间/未做腿型归因）的样本。2026-09-25 按**腿型**重算（正T买腿，见账本 §9.18）：
#   改为当日 14:45 平掉 ⇒ T13 −16,418→−3,956、T14 −13,458→−3,295、T11 −11,566→−2,078（各改善 0.9~1.2 万），
#   但 T5 +2,435→−1,382（T5 的正T买靠隔夜持有赚钱）⇒ 仍是**回测里单臂验证**的事项，故不擅自改生产。
DAY_END_T_OUT = os.getenv("WOLF_DAY_END_T_OUT", "0").strip().lower() in ("1", "true", "yes", "on")


def _oc_cond_buy_block(px, quote, kind, hhmm=None):
    """条件单买入的"开盘快速拉升不追"判定 → (block, why)。

    开关关 / 闸门关 / 缺数 / 异常 ⇒ **一律放行**（fail-open，绝不误拦）；放行时 why 为空串。
    `hhmm` 不传则用进程时钟 —— 回测里时钟被钉在当日 bar 上，故取到的就是仿真时刻。
    """
    if not OC_COND_BUY:
        return False, ""
    try:
        from app.services import wolf_no_chase as _nco
        if not _nco.open_enabled():
            return False, ""
        _ok, _why = _nco.open_verdict(float(px or 0),
                                      float((quote or {}).get("open") or 0),
                                      hhmm=hhmm, kind=kind)
        return (False, "") if _ok else (True, _why)
    except Exception as _e:
        print(f"[TMonitor] 条件单开盘闸异常(放行) {kind}: {str(_e)[:80]}")
        return False, ""


# 狼大做T表达式字段(唯一允许)：分时T出(t_sell) + 正T买点(index.intraday_dd 大盘盘中回撤2-3%低吸)
# + 黄线跌破离场(quote.vwap_break, 狼大8-04『黄线跌破直接走』)
# T1缩转放(t1_shrink_expand) 已由个股5min验证无预测力(2026-09-02) → 暂缓, 不再作为自动买腿
_BOARD_EXCLUDE = [x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()]


def _board_tradable(symbol) -> bool:
    """2026-09-10 账户权限最终防线: 无权限板块(创业板/科创板/北交所)不评估不成交,
    防止任何来源(rotation/agent/rollover/手动)布出的腿被触发买入。"""
    s = str(symbol or "")
    p = s[:2]
    code = s[2:8]
    if "cyb" in _BOARD_EXCLUDE and p == "SZ" and code[:3] in ("300", "301"):
        return False
    if "kcb" in _BOARD_EXCLUDE and p == "SH" and code.startswith("688"):
        return False
    if "bj" in _BOARD_EXCLUDE and (p == "BJ" or code[:3] == "920" or code[:1] in ("4", "8")):
        return False
    return True


WOLF_T_FIELDS = ("minute.m5.t_sell", "index.intraday_dd", "quote.vwap_break", "index.m5_dump", "quote.dip_prev_low",
                  # 2026-09-26 补（账本 §9.183）：新增腿型的表达式若**只含新字段** ⇒ 会被本白名单**整轮滤掉** ✗
                  #   · `quote.prev_low_dist_pct`（埋伏腿新触发 ✓ §9.182）
                  #   · `quote.m5_dump`（低吸 253 合取版 ✓ §9.176）
                  "quote.prev_low_dist_pct", "quote.m5_dump")

# 254 低吸触发容差（2026-09-15 参数对齐）：狼大 2025-03-06「就是**挂前一天的低点** 能买进去就做正T」
#   → 语料值 = 0.0（挂前低本身）；历史自设值 0.005（±0.5% 容差）。见 docs/wolf-buy-parameter-ledger.md §2-C1。
LEGACY_DIP_TOL = 0.005        # 历史自设容差
CORPUS_DIP_TOL = 0.0         # **语料值**：狼大 2025-03-06「就是挂前一天的低点 能买进去就做正T」


def _dip_prevlow_tol() -> float:
    """254 触发容差。

    ⚠️ 2026-09-15：**代码默认先回到 legacy(0.005)**，避免"部署即静默改变行为"——
    语料值 0.0 需用户批准后置 `WOLF_DIP_PREVLOW_TOL=0.0`（或后续单独 commit 翻转默认值）。
    影子 `WOLF_DIP_PREVLOW_SHADOW`（默认 1）会记录"按语料值会少成交哪些票"，供切换前评估。
    见 docs/wolf-buy-parameter-ledger.md §2-C1 / §11。
    """
    try:
        return max(float(os.getenv("WOLF_DIP_PREVLOW_TOL", str(LEGACY_DIP_TOL)) or LEGACY_DIP_TOL), 0.0)
    except (TypeError, ValueError):
        return LEGACY_DIP_TOL


DIP_PREVLOW_TOL = _dip_prevlow_tol()


def _dip_shadow_enabled() -> bool:
    """`WOLF_DIP_PREVLOW_SHADOW` 默认 1：只记录"对齐后会少成交"的情形。"""
    return os.getenv("WOLF_DIP_PREVLOW_SHADOW", "1").strip().lower() not in ("0", "false", "no", "")


def _dip_shadow_record(symbol: str, prev_low: float, today_low: float) -> None:
    import json as _json
    d = os.environ.get("DATA_DIR", "/app/data")
    try:
        import datetime as _dt
        path = os.path.join(d, "dip_tol_shadow_%s.json" % _dt.date.today().strftime("%Y%m%d"))
        cur = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cur = _json.load(f) or {}
            except Exception:
                cur = {}
        cur.setdefault("date", _dt.date.today().strftime("%Y%m%d"))
        cur.setdefault("items", {})
        cur["items"][symbol] = {"ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                "prev_low": round(float(prev_low), 3), "today_low": round(float(today_low), 3),
                                "gap_pct": round((float(today_low) / float(prev_low) - 1) * 100, 3),
                                "note": "tol=0 不成交 / 历史 0.005 会成交"}
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(cur, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[TMonitor] dip 影子写盘失败: %s" % str(e)[:60])


def _external_risk_enabled() -> bool:
    """external.* 字段组是否采集。**默认关**（2026-09-16 用户拍板下掉）。

    为什么下掉（审计证据）：
      · **没有任何规则判据用它**——`t_conditions` 里含 `external` 的表达式 = 0 条；
      · 唯一实质消费者是交易腿 agent 的提示词契约，而那条规则本身**缺语料支撑**
        （`db/prompt_seeds.py` 该行无语料引用、参数总账里查不到、阈值 -1.0%/40/0.9 为硬编码）；
      · 它的数据源是 ArkVol（黄金坑功能接口），为取一个字段把整个黄金坑状态计算
        （18 指数 + 行业监控 + 5s deadline）拖进股票做T的每 bar 路径，并且实测能读到
        **跨期快照**（1 月重放读到 as_of=2026-09-15）。
    回退：置 `WOLF_EXTERNAL_RISK=1` 恢复采集（同时 `trade_graph` 的月度门控文案里那句也会恢复）。
    """
    return str(os.getenv("WOLF_EXTERNAL_RISK", "0")).strip().lower() in ("1", "true", "yes", "on")


def _external_snapshot_fields() -> Dict[str, Any]:
    """`external.*` 字段组的取值。关闭时返回**与取数失败时同形**的默认值（字段仍在、恒不告警）。"""
    if not _external_risk_enabled():
        return {"us_risk": False, "us_risk_reason": "", "us_risk_score": 0,
                "nasdaq_pct": 0.0, "sox_pct": 0.0, "gm_liquidity_gate": ""}
    try:
        from app.services.t_external_risk import external_risk_snapshot
        _ext = external_risk_snapshot()
        return {
            "us_risk": bool(_ext.get("us_risk")),
            "us_risk_reason": str(_ext.get("us_risk_reason") or ""),
            "us_risk_score": int(_ext.get("us_risk_score") or 0),
            "nasdaq_pct": (_ext.get("us_market") or {}).get("nasdaq", {}).get("pct", 0.0),
            "sox_pct": (_ext.get("us_market") or {}).get("sox", {}).get("pct", 0.0),
            "gm_liquidity_gate": (_ext.get("global_macro") or {}).get("liquidity_gate", ""),
        }
    except Exception:
        return {"us_risk": False, "us_risk_reason": "", "us_risk_score": 0,
                "nasdaq_pct": 0.0, "sox_pct": 0.0, "gm_liquidity_gate": ""}


class TMonitor:
    """做T监控器：daemon 线程，30s 轮询 t_conditions，命中写 t_triggers。"""

    def __init__(self, interval_seconds: int = MONITOR_INTERVAL, trade_executor=None):
        self.interval = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._trade_executor = trade_executor  # ⑥ 253/254 无底仓建仓执行器（狼大建仓链），None 时保持纯做T gateway 路径
        self._status = {
            "running": False,
            "last_round": None,
            "last_round_ms": 0,
            "conditions_checked": 0,
            "triggers_written": 0,
            "errors": 0,
            "daily_maintained": None,
            "ai_maintained": None,
            "ai_maintain_running": False,
        }
        self._daily_maintained = ""
        self._ai_thread: Optional[threading.Thread] = None
        self._wolf_done = set()   # (symbol, trigger_kind, date) 当日去抖，防同一腿反复触发
        self._v3_done = set()     # v3 灰度：当日 (symbol, action) 去抖
        self._v3_agent_done = set()  # 生产：当日 (symbol, phase) agent 只问一次
        self._v3_m5_cache = {}       # (symbol, minute) -> 5m bars
        self._v3_precompute_done = set()
        self._v3_intraday_done = set()
        self._nochase_deferred = set()   # (symbol, date) 被"不追高"延后到 14:00–14:30 重评的票（只用于降噪打印）
        self._line_deferred = set()      # (symbol, date) 挂低位线等待成交的票（狼大 2026-04-10；只用于降噪打印）
        self._line_bars_cache = {}       # (symbol, date) → 算线用的 as-of 日线（每天只取一次，避免 30s 轮次重复请求）
        self._hpv_bars_cache = {}        # (symbol, date) → 「位置高∧量能放大」闸用的 as-of 日线（同上）
        self._hpv_deferred = set()       # (symbol, date) 被该闸挡下的票（只用于降噪打印）
        self._nobase_deferred = set()    # (symbol, date) 无底仓被挡下的正T买腿（狼大 2026-08-25；只用于降噪打印）
        self._tw_deferred = set()        # (symbol, date) 被狼大做T时间窗挡下的正T买腿（D2，2026-09-21；只用于降噪打印）
        self._oc_deferred = set()        # (symbol, date) 被"开盘快速拉升不追"挡下的买腿（D1，2026-09-21；只用于降噪打印）
        self._core_cap_warned = ""     # 条件标的超上限的当日告警（防刷屏）
        self._fall_deferred = set()      # (symbol, date) 被"下跌中不买/弱势延后"挡下的买腿（B/C，2026-09-22；只用于降噪打印）
        self._held_cache = {"at": 0.0, "set": set(), "ok": False}   # 持仓快照（60s TTL；供"挂线只管再买"判范围）
        self._wolf_bought_today = set()  # (symbol, date) 当日正T买入
        self._wolf_sold_today = set()    # (symbol, date) 当日确认制T出已卖
        # ① 建仓初期波段逻辑止损: 建仓日锚点缓存 {(account, symbol, today): 'YYYY-MM-DD'|None}
        self._buy_date_cache: Dict[Any, Optional[str]] = {}
        # 带日期日K缓存 {(symbol, today, n): bars}（含实时源兜底, 避免 30s 轮次重复请求）
        self._dated_cache: Dict[Any, List[dict]] = {}
        # 止损当日已执行去抖 {(symbol, today)} —— 2026-09-11 修见 _check_stop_loss 注释
        self._stop_done_day: set = set()
        # A1（2026-09-14）：等量换手腿"连续在黄线下"的轮数（用于破线确认）
        self._vwap_break_streak: dict = {}

    # ── 生命周期 ──

    def status(self) -> Dict[str, Any]:
        return dict(self._status)

    # ── 主循环 ──
    def _run(self):
        time.sleep(INITIAL_OFFSET)  # 错峰
        while not self._stop.is_set():
            round_start = time.time()
            try:
                if _is_trading_time():
                    self._round()
                else:
                    time.sleep(60)  # 非交易时段低频等待
                    continue
            except Exception as e:
                self._status["errors"] += 1
                print(f"[TMonitor] 本轮异常: {e}")
                # 2026-09-26 用户「加个全局异常处理，统一走QQ推送」✓（账本 §9.238）
                try:
                    from app.services import alert_hub as _ah
                    _ah.note("t_monitor.round", e)
                except Exception as _e_sil1:
                    try:
                        from app.services import alert_hub as _ah_sil
                        _ah_sil.note_silent("t_monitor.py:465", _e_sil1)
                    except Exception:
                        print("[silent:t_monitor.py:465] %s: %s" % (type(_e_sil1).__name__, str(_e_sil1)[:110]), flush=True)
            elapsed = (time.time() - round_start) * 1000
            self._status.update({
                "last_round": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_round_ms": round(elapsed, 1),
            })
            # jitter 等待
            wait = self.interval + ((time.time() * 1000) % (JITTER * 2 + 1) - JITTER)
            self._stop.wait(max(5.0, wait))

    def _daily_dated(self, sym, n=40):
        """最近 n 个交易日的**带日期**日K: [{'date':'YYYYMMDD','close','high','low','vol'}]。

        与 _prev_daily 同源（data/{stock_5m_bt,recent_sync}/{code6}.json），但**保留日期** ——
        ①建仓初期波段逻辑止损要按"建仓日"锚定（波段低点 = 建仓日之前的最低、持有天数 = 建仓日之后的根数），
        没有日期就无法锚定（_prev_daily 丢掉了 key）。

        **实时源兜底（2026-09-11 生产实测后加）**：
          data/stock_5m_bt（37 个文件，最新 mtime 2026-09-03 14:25）与 data/recent_sync
          （14 个文件，2026-09-03 21:21）都是**一次性导出**，**没有常驻同步任务** —— 当日实测
          当前在册 13 个标的里 **0 个新鲜**（3 个停在 09-03、10 个根本没有文件）。
          若不补齐，依赖本函数的机制（①结构止损 / ①后半句逻辑时间离场）会**恒不动作**（"静默失效"）。
          → 本地缓存最新日期早于"今天-2 自然日"时，用 `t_build._fetch_daily_bars`（Tushare 主源、
            东财降级）补最近 n 根；取不到就用本地现有的，**绝不因为取不到而当成"没有数据就卖/不卖"以外的判断**。
          可用 `WOLF_DATED_LIVE_FALLBACK=0` 关掉兜底。结果按 (symbol, today, n) 缓存，避免 30s 轮次重复请求。
        """
        import os as _os, json as _j
        today = datetime.now().strftime('%Y%m%d')
        ck = (sym, today, n)
        if ck in self._dated_cache:
            return self._dated_cache[ck]
        D = _os.environ.get('DATA_DIR', '/app/data')
        code6 = ''.join(ch for ch in str(sym) if ch.isdigit())[:6]
        data = {}
        for root in ['stock_5m_bt', 'recent_sync']:
            p = _os.path.join(D, root, code6 + '.json')
            try:
                d = _j.load(open(p, encoding='utf-8'))
            except Exception as _e_sil3:
                _silent_alert("t_monitor.py:606", _e_sil3)
                continue
            for k, v in d.items():
                bs = sorted(v, key=lambda x: str(x.get('time') or x.get('trade_time')))
                if bs:
                    data.setdefault(k, {'close': float(bs[-1]['close']),
                                        'high': max(float(b['high']) for b in bs),
                                        'low': min(float(b['low']) for b in bs),
                                        'vol': sum(float(b.get('vol') or 0) for b in bs)})
        days = sorted(k for k in data if k < today and data[k].get('vol'))
        out = []
        for k in days[-n:]:
            r = dict(data[k]); r['date'] = str(k); out.append(r)
        if _os.getenv("WOLF_DATED_LIVE_FALLBACK", "1").strip() not in ("0", "false", "no"):
            from datetime import timedelta
            _last = out[-1]['date'] if out else ''
            _cut = (datetime.now() - timedelta(days=2)).strftime('%Y%m%d')
            if not _last or _last < _cut:
                _live, _src = [], ""
                try:
                    from app.services.t_build import _fetch_daily_bars
                    for b in (_fetch_daily_bars(sym, count=n) or []):
                        _d = str(b.get('date') or '').replace('-', '')[:8]
                        if not _d or _d >= today:
                            continue
                        try:
                            _live.append({'date': _d, 'close': float(b.get('close') or 0),
                                          'high': float(b.get('high') or 0),
                                          'low': float(b.get('low') or 0),
                                          'vol': float(b.get('vol') or 0)})
                        except Exception as _e_sil4:
                            _silent_alert("t_monitor.py:636", _e_sil4)
                            continue
                    if _live:
                        _src = "Tushare/东财"
                except Exception as _e:
                    print(f"[TMonitor] _daily_dated 实时源失败 {sym}: {str(_e)[:100]}")
                if not _live:      # 主兜底两个源都失败 → 腾讯日线(第三个源, 实测更稳且含当日)
                    _alt = _fetch_daily_tencent_dated(sym, n) or []
                    if _alt:
                        _live, _src = _alt, "腾讯日线"
                if _live:
                    # ★ 2026-10-16 修 ✗（alerts 实录：`where=t_monitor.py:758` × `raise×1：'date'` ✓）：
                    #   病灶 ✗：**兜底源的日线行不一定带 `'date'` 键** ✗
                    #     ⇒ `_live[-1]['date']`（上一行打印 ✓）与 `sorted(key=x['date'])` ✗ ⇒ KeyError ✓
                    #     ⇒ 整个 `_daily_dated` 抛异常 ⇒ 「碰新高/转正/纪律」判定恒失效 ✗（静默 ✗）
                    #   修法 ✓：先**归一化**（`date` / `trade_date` / `day` ✓）+ **丢弃无日期的行** ✓，再打印/排序 ✓
                    _norm = []
                    for _r in _live:
                        if not isinstance(_r, dict):
                            continue
                        _d8 = str(_r.get('date') or _r.get('trade_date') or _r.get('day') or '') \
                            .replace('-', '')[:8]
                        if not _d8:
                            continue
                        _r2 = dict(_r)
                        _r2['date'] = _d8
                        _norm.append(_r2)
                    _live = _norm
                    if _live:
                        if _last:
                            print(f"[TMonitor] _daily_dated 本地缓存过期({sym} 停在 {_last}) → {_src}补齐 "
                                  f"{len(_live)} 根(至 {_live[-1]['date']})")
                        out = sorted(_live, key=lambda x: x['date'])[-n:]
                elif _last:
                    print(f"[TMonitor] _daily_dated 实时源全部失败 {sym} → 用本地缓存(停在 {_last})")
        self._dated_cache[ck] = out
        return out


    # ── ★ §9.843 ✓（用户 2026-10-13「不算追高吗？都快历史新高了吧」✓）：建仓/埋伏类的
    #   **追高闸 + 位置闸**（阈值来自本仓**历史成交标定** ✓，`scripts/calib_chase_position2.py` ✓）
    #   标定样本 = 1796 笔真实买入（全账户 ✓，基准=**实际成交价** × **不复权序列** ✓）：
    #     · 距 250 日高 **−2~0%**（贴新高 ✗）⇒ +10d 中位 **−7.19%**、胜率 **22.0%**（n=59 ✓）
    #       对照 −10~−5% ⇒ −1.43% / 46.7%（n=392 ✓）⇒ **贴新高明显更差** ✓
    #     · 20 日涨幅 **>30%** ⇒ +10d 中位 **−4.45%**、胜率 **36.9%**、MAE **−8.93%**（n=195 ✓）
    #       对照 涨幅<0（回调里买 ✓）⇒ **+0.83% / 52.8%**（n=286 ✓）⇒ 反过来佐证他的
    #       「**只在下跌里买**」✓（语料 ledger:450 ✓）
    #   三个阈值**默认 0 = 关** ✓（生产零影响 ✓）；回测/灰度按标定值开启 ✓
    #   （深圳华强 2026-05-06 ✓：距高 −2.36% ✓ 且 20 日涨幅 +51.6% ✓ ⇒ **两道闸任一都能拦住** ✓）

    def _positions(self, account=None):
        """**统一持仓口径**（2026-09-14 用户拍板）：默认**只读 stock 账户**；t 账户是测试账户、暂不使用。

        历史实现散落着 t_pool._get_positions()（硬编码 t 账户）→ 监控器账户是 stock、持仓却读 t，
        两边不一致。现在所有离场/维护/止损都走这里；切回 t 只需 `WOLF_POSITION_ACCOUNT=t`。
        """
        acct = account or _position_accounts()[0]
        out = []
        try:
            import psycopg2 as _pg
            conn = _pg.connect(os.getenv("DATABASE_URL",
                                         "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
            cur = conn.cursor()
            cur.execute("SELECT symbol, volume, avg_price FROM paper_positions "
                        "WHERE account_id = %s AND volume > 0", (acct,))
            for s, v, pr in cur.fetchall():
                out.append({"account_id": acct, "symbol": str(s),
                            "volume": float(v or 0), "avg_price": float(pr or 0)})
            cur.close(); conn.close()
        except Exception as e:
            print(f"[TMonitor] 持仓读取失败({acct}): {e}")
        return out

    # ── v3 统一止损层（2026-10-23，灰度）─────────────────────────────────────
    def _v3_index_bars(self):
        """上证日线（上一交易日及以前）+ 当日 quote；按日缓存。"""
        import json as _j
        today = datetime.now().strftime("%Y%m%d")
        if getattr(self, "_v3_idx_day", "") == today and getattr(self, "_v3_idx", None):
            return self._v3_idx
        rows = []
        data_dir = os.getenv("DATA_DIR", "/app/data")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        for p in (os.path.join(data_dir, "index_daily_000001.json"),
                  os.path.join(root, "data", "index_daily_000001.json")):
            try:
                d = _j.load(open(p, encoding="utf-8"))
                if isinstance(d, list) and d:
                    rows = [{"trade_date": str(r.get("trade_date") or r.get("date") or "").replace("-", ""),
                             "open": float(r.get("open") or 0), "high": float(r.get("high") or 0),
                             "low": float(r.get("low") or 0), "close": float(r.get("close") or 0),
                             "vol": float(r.get("vol") or r.get("amount") or 0)} for r in d]
                    break
            except Exception:
                continue
        rows = [r for r in rows if len(r["trade_date"]) == 8 and r["close"] > 0]
        rows.sort(key=lambda r: r["trade_date"])
        try:
            q = fetch_quote_one("SH000001") or {}
            px = float(q.get("current") or 0)
            if px > 0 and (not rows or rows[-1]["trade_date"] < today):
                rows.append({"trade_date": today, "open": float(q.get("open") or px),
                             "high": float(q.get("high") or px), "low": float(q.get("low") or px),
                             "close": px, "vol": float(q.get("vol") or q.get("amount") or 0)})
        except Exception:
            pass
        self._v3_idx_day, self._v3_idx = today, rows
        return rows

    def _v3_intraday_bars(self, symbol: str):
        """当日/前日 5min（腾讯 m5，60s 缓存）；失败返回空，不阻断日线逻辑。"""
        try:
            import datetime as _dt
            key = (symbol, _dt.datetime.now().strftime("%H%M"))
            if key in self._v3_m5_cache:
                return self._v3_m5_cache[key]
            from app.services.t_data_sources import fetch_minute_bars
            bars = fetch_minute_bars(symbol, freq="m5", count=240) or []
            self._v3_m5_cache = {key: bars}
            return bars
        except Exception:
            return []

    def _v3_stock_bars(self, symbol: str, quote: dict):
        bars = self._daily_dated(symbol, 80) or []
        today = datetime.now().strftime("%Y%m%d")
        bars = [dict(b, trade_date=str(b.get("trade_date") or b.get("date") or "").replace("-", ""))
                for b in bars if str(b.get("trade_date") or b.get("date") or "")]
        bars = [b for b in bars if b.get("trade_date") != today]
        px = float((quote or {}).get("current") or 0)
        if px > 0:
            bars.append({"trade_date": today, "open": float((quote or {}).get("open") or px),
                         "high": float((quote or {}).get("high") or px),
                         "low": float((quote or {}).get("low") or px),
                         "close": px, "vol": float((quote or {}).get("vol") or 0)})
        return bars

    def _v3_context(self, symbol: str, quote: dict, ledger: dict):
        from app.services import wolf_stop_layer as WL
        wave = {}
        try:
            from app.services import wave_store
            wave = wave_store.get_wave() or {}
        except Exception:
            wave = {}
        today = datetime.now().strftime("%Y%m%d")
        pos = (ledger or {}).get(symbol) or {}
        cost = float(pos.get("avg_price") or 0) or float((quote or {}).get("pre_close") or 0)
        cur = float((quote or {}).get("current") or 0)
        pnl = ((cur / cost - 1.0) * 100.0) if (cost > 0 and cur > 0) else 0.0
        try:
            tok, _ = _stop_time_ok()
        except Exception:
            tok = True
        phase = "close" if _in_close_window() else "intraday"
        return WL.StopContext(
            symbol=symbol, asof=today, phase=phase,
            bars=self._v3_index_bars(), wave=wave,
            operation=str(wave.get("operation") or "side"),
            position={"pnl_pct": round(pnl, 2), "was_broken": False},
            stock_bars=self._v3_stock_bars(symbol, quote), stock_symbol=symbol,
            time_ok=bool(tok),
        )

    def _v3_log(self, rec: dict) -> None:
        try:
            import json as _j
            data_dir = os.getenv("DATA_DIR", "data")
            os.makedirs(data_dir, exist_ok=True)
            with open(os.path.join(data_dir, "stop_v3_events.jsonl"), "a", encoding="utf-8") as f:
                f.write(_j.dumps(rec, ensure_ascii=False) + "\n")
            # PG 权威留痕（WOLF_V3_PG_LOG=1 时启用；失败不影响交易）
            try:
                if str(os.getenv("WOLF_V3_PG_LOG", "0")).strip() not in ("1", "true", "yes", "on"):
                    raise RuntimeError("pg_log_off")
                from app.database import SessionLocal
                from sqlalchemy import text
                _db = SessionLocal()
                try:
                    _db.execute(text("""
                        INSERT INTO stop_v3_events(event_date,symbol,phase,action,action_type,layer,severity,
                            level_state,status_text,target_pct,reason,source,quote,level,payload)
                        VALUES (:d,:s,:ph,:a,:at,:l,:sev,:ls,:st,:tp,:r,:src,:q,:lv,:pl)
                    """), {"d": str(rec.get("date") or ""), "s": str(rec.get("symbol") or ""),
                           "ph": str(rec.get("phase") or ""), "a": str(rec.get("action") or ""),
                           "at": str(rec.get("action_type") or ""), "l": str(rec.get("layer") or ""),
                           "sev": str(rec.get("severity") or ""), "ls": str(rec.get("level_state") or ""),
                           "st": str(rec.get("status_text") or ""), "tp": rec.get("target_pct"),
                           "r": str(rec.get("reason") or ""), "src": str(rec.get("source") or ""),
                           "q": rec.get("quote"), "lv": _j.dumps(rec.get("level") or {}, ensure_ascii=False),
                           "pl": _j.dumps(rec, ensure_ascii=False)})
                    _db.commit()
                finally:
                    _db.close()
            except Exception:
                pass
        except Exception:
            pass

    def _check_all_positions_v3(self) -> None:
        """v3：不依赖条件单，持仓每轮都扫一遍（否则没有 custom_support_sell 就不跑）。"""
        try:
            from app.services.t_gateway import get_sellable_ledger
            self._v3_precompute_positions()
            self._v3_check_intraday_watches()
            self._v3_check_rebound_watches()
            ledger = get_sellable_ledger(T_MONITOR_ACCOUNT)
            holds = [p for p in self._positions() if float(p.get("volume") or 0) > 0]
            pairs = [(p.get("symbol"), _normalize_symbol(p.get("symbol")))
                     for p in holds if p.get("symbol")]
            if not pairs:
                return
            quotes = self._fetch_quotes_concurrent([s for _, s in pairs])
            try:
                from app.services import decision_window as _dw2
                _ph2 = _dw2.phase_at().get("phase")
                _hm2 = _dt.datetime.now().strftime("%H:%M")
                _scan_times = {x.strip() for x in os.getenv("WOLF_V3_REBOUND_SCAN_TIMES", "09:45,10:30").split(",") if x.strip()}
                if _ph2 == "tail_confirm":
                    self._v3_agent_positions("tail_confirm")
                elif _ph2 == "intraday" and _hm2 in _scan_times:
                    self._v3_agent_positions("rebound_scan_" + _hm2, scan_only=True)
                elif _ph2 == "intraday" and _hm2 == "14:30":
                    self._v3_agent_positions("time_1430")
                # 3×15 的确认在 13:00-14:30 执行禁区，统一挪到 14:30 这一轮判；
                # 与 time-specific 共用一次 agent 调用，避免重复问。
                elif _ph2 == "intraday" and _dt.datetime.now().strftime("%H:%M") == os.getenv("WOLF_V3_AGENT_3X15_TIME", "14:30"):
                    self._v3_agent_positions("intraday_3x15")
            except Exception:
                pass
            for raw, sym in pairs:
                q = quotes.get(sym)
                if q and q.get("current"):
                    self._check_stop_loss_v3(raw, q, ledger)
        except Exception as e:
            print("[TMonitor][V3] 持仓扫描异常(不卖): %s" % str(e)[:120], flush=True)

    def _check_stop_loss_v3(self, symbol: str, quote: dict, ledger: dict) -> None:
        """v3 灰度：只记录/提示，**不自动卖**。"""
        try:
            from app.services import wolf_stop_layer as WL
            ctx = self._v3_context(symbol, quote, ledger)
            dec = WL.evaluate(ctx)
            if dec.action == "hold":
                return
            today = datetime.now().strftime("%Y%m%d")
            key = (symbol, dec.action, today)
            if key in self._v3_done:
                return
            self._v3_done.add(key)
            rec = {"date": today, "symbol": symbol, "action": dec.action,
                   "action_type": dec.action_type,
                   "layer": dec.layer, "severity": dec.severity,
                   "level_state": dec.level_state, "status_text": dec.status_text,
                   "target_pct": dec.target_pct, "no_new": dec.no_new,
                   "reason": dec.reason, "source": dec.source,
                   "level": dec.level, "quote": float((quote or {}).get("current") or 0)}
            self._v3_log(rec)
            print("[TMonitor][V3] %s %s %s target=%s %s" % (
                symbol, dec.action, dec.layer, dec.target_pct, str(dec.reason)[:120]), flush=True)

            # 生产接线（默认只计划；WOLF_V3_LIVE=1 才真下单）
            if str(os.getenv("WOLF_V3_LIVE", "0")).strip() in ("1", "true", "yes", "on"):
                try:
                    from app.services import decision_window as _dw
                    from app.services import wolf_v3_production as _vp
                    _ph = _dw.phase_at().get("phase")
                    if _ph not in ("tail_confirm", "intraday") or dec.action not in ("reduce_to", "exit"):
                        return
                    _akey = (symbol, _ph, today)
                    if _akey in self._v3_agent_done:
                        return
                    self._v3_agent_done.add(_akey)
                    _bars = getattr(ctx, "stock_bars", None) or getattr(ctx, "bars", None) or []
                    _snap = _vp.build_snapshot(symbol, quote, _bars, self._v3_intraday_bars(symbol))
                    _ag = _vp.ask_agent(_snap)
                    _arec = {"date": today, "symbol": symbol, "phase": _ph,
                             "agent_ok": bool(_ag.get("ok")), "agent_reason": _ag.get("reason"),
                             "agent_decision": _ag.get("decision"), "agent_error": _ag.get("error"),
                             "window": {"phase": _snap.get("phase"), "last_price": _snap.get("last_price"),
                                        "same_day_executable": _snap.get("same_day_executable")}}
                    self._v3_log(_arec)
                    if not _ag.get("ok") or _ag.get("no_action"):
                        return
                    _plan = _vp.plan_sell(symbol, quote, float(dec.target_pct or 0),
                                          account_id=T_MONITOR_ACCOUNT)
                    if not _plan.get("ok"):
                        _arec["plan_reason"] = _plan.get("reason")
                        self._v3_log(_arec)
                        return
                    _key = _vp.action_key(today, symbol, dec.action, (dec.level or {}).get("price"))
                    if _vp.already_done(_key):
                        return
                    _gw = _vp.execute_plan(_plan, reason="[v3] %s %s" % (dec.action, dec.reason),
                                           live=True)
                    _arec.update({"plan": _plan, "gateway": _gw, "key": _key})
                    _vp.record_action({"key": _key, "date": today, "symbol": symbol,
                                       "action": dec.action, "level": dec.level,
                                       "target_pct": dec.target_pct, "plan": _plan,
                                       "gateway": _gw})
                    self._v3_log(_arec)
                    if _gw.get("status") == "success":
                        self._sold_this_round.add(symbol)
                        print("[TMonitor][V3][LIVE] %s %s %s股 target=%s" % (
                            symbol, dec.action, _plan.get("volume"), dec.target_pct), flush=True)
                except Exception as _e_live:
                    print("[TMonitor][V3][LIVE] 异常(不卖) %s: %s" % (symbol, str(_e_live)[:160]), flush=True)
        except Exception as e:
            print("[TMonitor][V3] 异常(不卖) %s: %s" % (symbol, str(e)[:120]), flush=True)

    def _v3_precompute_positions(self) -> None:
        """14:50 预计算：只问一次，结果落盘，14:55 读缓存。"""
        if str(os.getenv("WOLF_V3_LIVE", "0")).strip() not in ("1", "true", "yes", "on"):
            return
        try:
            from app.services import decision_window as _dw
            if _dw.phase_at().get("phase") != "tail_preview":
                return
            from app.services import wolf_v3_production as _vp
            from app.services.t_gateway import get_sellable_ledger
            ledger = get_sellable_ledger(T_MONITOR_ACCOUNT)
            holds = [p for p in self._positions() if float(p.get("volume") or 0) > 0]
            pairs = [(p.get("symbol"), _normalize_symbol(p.get("symbol"))) for p in holds if p.get("symbol")]
            quotes = self._fetch_quotes_concurrent([s for _, s in pairs])
            cap = int(os.getenv("WOLF_V3_PRECOMPUTE_MAX", "8"))
            today = _dt.datetime.now().strftime("%Y%m%d")
            for raw, sym in pairs[:cap]:
                if (raw, today) in self._v3_precompute_done:
                    continue
                self._v3_precompute_done.add((raw, today))
                q = quotes.get(sym)
                if not q or not q.get("current"):
                    continue
                try:
                    dec = self._v3_context(raw, q, ledger)
                    bars = getattr(dec, "stock_bars", None) or getattr(dec, "bars", None) or []
                    snap = _vp.build_snapshot(raw, q, bars, self._v3_intraday_bars(raw))
                    res = _vp.precompute_agent(snap)
                    self._v3_log({"date": _dt.datetime.now().strftime("%Y%m%d"), "symbol": raw,
                                  "phase": "tail_preview", "precompute": True,
                                  "agent_ok": bool(res.get("ok")), "agent_reason": res.get("reason"),
                                  "agent_decision": res.get("decision")})
                except Exception as _e:
                    print("[TMonitor][V3] precompute %s: %s" % (raw, str(_e)[:100]), flush=True)
        except Exception as e:
            print("[TMonitor][V3] precompute 异常: %s" % str(e)[:120], flush=True)

    def _v3_agent_positions(self, phase_label: str, scan_only: bool = False) -> None:
        """窗口驱动的 agent 全持仓判定（tail_confirm / 14:30 定点）。

        不依赖 WL.evaluate 是否 hold：先让 agent 判“是否执行”，再用确定性层给 target_pct；
        agent 说 hold 就不卖；agent 说减仓且确定性层 hold 时，用 WOLF_V3_AGENT_TARGET（默认 0.5）。
        """
        if str(os.getenv("WOLF_V3_LIVE", "0")).strip() not in ("1", "true", "yes", "on"):
            return
        try:
            import datetime as _dt
            from app.services import wolf_v3_production as _vp
            from app.services import wolf_stop_layer as WL
            from app.services.t_gateway import get_sellable_ledger
            today = _dt.datetime.now().strftime("%Y%m%d")
            ledger = get_sellable_ledger(T_MONITOR_ACCOUNT)
            holds = [p for p in self._positions() if float(p.get("volume") or 0) > 0]
            pairs = [(p.get("symbol"), _normalize_symbol(p.get("symbol"))) for p in holds if p.get("symbol")]
            quotes = self._fetch_quotes_concurrent([s for _, s in pairs])
            for raw, sym in pairs:
                q = quotes.get(sym)
                if not q or not q.get("current"):
                    continue
                if _vp.pg_symbol_action_today(raw, today):
                    continue
                akey = (raw, phase_label, today)
                if akey in self._v3_agent_done:
                    continue
                self._v3_agent_done.add(akey)
                try:
                    ctx = self._v3_context(raw, q, ledger)
                    dec = WL.evaluate(ctx)
                    bars = getattr(ctx, "stock_bars", None) or getattr(ctx, "bars", None) or []
                    snap = _vp.build_snapshot(raw, q, bars, self._v3_intraday_bars(raw))
                    res = _vp.ask_agent_cached(snap)
                    self._v3_log({"date": today, "symbol": raw, "phase": phase_label,
                                  "agent_ok": bool(res.get("ok")), "agent_reason": res.get("reason"),
                                  "agent_decision": res.get("decision")})
                    if not res.get("ok") or res.get("no_action"):
                        continue
                    target = float(dec.target_pct) if dec.action in ("reduce_to", "exit") else float(
                        os.getenv("WOLF_V3_AGENT_TARGET", "0.5"))
                    _decd = res.get("decision") or {}
                    _rz = _decd.get("rebound_zone") if isinstance(_decd, dict) else None
                    if isinstance(_rz, dict) and _rz.get("zone"):
                        try:
                            _lo, _hi = float(_rz["zone"][0]), float(_rz["zone"][1])
                            _bp = float((dec.level or {}).get("price") or 0)
                            _vp.save_rebound_watch(raw, today, _lo, _hi, _bp, target,
                                                   source="v3_agent", payload=_decd)
                            self._v3_log({"date": today, "symbol": raw, "phase": phase_label,
                                          "rebound_watch": {"zone": [_lo, _hi], "target": target}})
                        except Exception:
                            pass
                    if scan_only or str(_decd.get("action_mode") or "") == "rebound_exit":
                        # 早盘扫描只建 watch；反抽卖出也交给 watcher，不在这里现价卖
                        continue
                    plan = _vp.plan_sell(raw, q, target, account_id=T_MONITOR_ACCOUNT)
                    if not plan.get("ok"):
                        continue
                    key = _vp.action_key(today, raw, "agent_" + phase_label, (dec.level or {}).get("price"))
                    if _vp.already_done(key):
                        continue
                    gw = _vp.execute_plan(plan, reason="[v3-agent] %s" % phase_label, live=True)
                    _vp.record_action({"key": key, "date": today, "symbol": raw,
                                       "action": "agent_" + phase_label, "level": dec.level,
                                       "target_pct": target, "plan": plan, "gateway": gw})
                    self._v3_log({"date": today, "symbol": raw, "phase": phase_label,
                                  "agent_exec": True, "plan": plan, "gateway": gw, "key": key})
                    if gw.get("status") == "success":
                        self._sold_this_round.add(raw)
                        print("[TMonitor][V3][LIVE] %s agent %s %s股 target=%s" % (
                            raw, phase_label, plan.get("volume"), target), flush=True)
                except Exception as _e:
                    print("[TMonitor][V3] agent %s %s: %s" % (phase_label, raw, str(_e)[:120]), flush=True)
        except Exception as e:
            print("[TMonitor][V3] agent positions 异常: %s" % str(e)[:120], flush=True)

    def _v3_intraday_event(self, symbol: str, quote: dict, ledger: dict, mode: str, level: float) -> None:
        """3×15/放量确认后的 agent 判定；反抽型转 rebound_watch，否则按 target 执行。"""
        try:
            import datetime as _dt
            from app.services import wolf_v3_production as _vp
            from app.services import wolf_stop_layer as WL
            today = _dt.datetime.now().strftime("%Y%m%d")
            ctx = self._v3_context(symbol, quote, ledger)
            dec = WL.evaluate(ctx)
            bars = getattr(ctx, "stock_bars", None) or getattr(ctx, "bars", None) or []
            snap = _vp.build_snapshot(symbol, quote, bars, self._v3_intraday_bars(symbol))
            if mode == "3x15":
                _zz = _vp.rebound_zone_from_level(level, snap.get("candidates") or [])
                if _zz:
                    _vp.save_rebound_watch(symbol, today, _zz["zone_low"], _zz["zone_high"], level,
                                           float(os.getenv("WOLF_V3_REBOUND_TARGET", "0.5")),
                                           source="intraday_3x15",
                                           payload={"level": level, "hit": "3x15"},
                                           invalidation_level=round(_zz["zone_high"] * 1.01, 4))
                    self._v3_log({"date": today, "symbol": symbol, "phase": "intraday_3x15",
                                  "rebound_watch": _zz, "level": level, "auto_wait_rebound": True})
                return
            res = _vp.ask_agent(snap)
            self._v3_log({"date": today, "symbol": symbol, "phase": "intraday_" + mode,
                          "agent_ok": bool(res.get("ok")), "agent_reason": res.get("reason"),
                          "agent_decision": res.get("decision"), "level": level})
            if not res.get("ok") or res.get("no_action"):
                return
            d = res.get("decision") or {}
            rz = d.get("rebound_zone") if isinstance(d, dict) else None
            target = float(dec.target_pct) if dec.action in ("reduce_to", "exit") else float(
                os.getenv("WOLF_V3_AGENT_TARGET", "0.5"))
            if isinstance(rz, dict) and rz.get("zone"):
                try:
                    _vp.save_rebound_watch(symbol, today, float(rz["zone"][0]), float(rz["zone"][1]),
                                           level, target, source="intraday_" + mode, payload=d)
                except Exception:
                    pass
            if str(d.get("action_mode") or "") == "rebound_exit":
                return
            plan = _vp.plan_sell(symbol, quote, target, account_id=T_MONITOR_ACCOUNT)
            if not plan.get("ok"):
                return
            key = _vp.action_key(today, symbol, "intraday_" + mode, "%.2f" % level)
            if _vp.already_done(key):
                return
            gw = _vp.execute_plan(plan, reason="[v3-intraday-%s] %.2f" % (mode, level), live=True)
            _vp.record_action({"key": key, "date": today, "symbol": symbol,
                               "action": "intraday_" + mode, "level": {"price": level},
                               "target_pct": target, "plan": plan, "gateway": gw})
            self._v3_log({"date": today, "symbol": symbol, "phase": "intraday_" + mode,
                          "intraday_exec": True, "plan": plan, "gateway": gw, "key": key})
            if gw.get("status") == "success":
                self._sold_this_round.add(symbol)
        except Exception as e:
            print("[TMonitor][V3] intraday event %s: %s" % (symbol, str(e)[:120]), flush=True)

    def _v3_check_intraday_watches(self) -> None:
        """30s 轮询：已完成 15min 连续 3 根 / 放量 5min 破位 → 事件。"""
        if str(os.getenv("WOLF_V3_LIVE", "0")).strip() not in ("1", "true", "yes", "on"):
            return
        try:
            import datetime as _dt
            from app.services import wolf_v3_intraday as _vi
            from app.services import wolf_v3_production as _vp
            from app.services.t_gateway import get_sellable_ledger
            today = _dt.datetime.now().strftime("%Y%m%d")
            ledger = get_sellable_ledger(T_MONITOR_ACCOUNT)
            holds = [p for p in self._positions() if float(p.get("volume") or 0) > 0]
            pairs = [(p.get("symbol"), _normalize_symbol(p.get("symbol"))) for p in holds if p.get("symbol")]
            quotes = self._fetch_quotes_concurrent([s for _, s in pairs])
            now = _dt.datetime.now()
            for raw, sym in pairs:
                q = quotes.get(sym)
                if not q or not q.get("current"):
                    continue
                if _vp.pg_symbol_action_today(raw, today):
                    continue
                bars5 = self._v3_intraday_bars(raw)
                if len(bars5) < 5:
                    continue
                ctx = self._v3_context(raw, q, ledger)
                dec = WL.evaluate(ctx) if "WL" in globals() else None
                if dec is None:
                    from app.services import wolf_stop_layer as _wl
                    dec = _wl.evaluate(ctx)
                snap = _vp.build_snapshot(raw, q, getattr(ctx, "stock_bars", None) or getattr(ctx, "bars", None) or [],
                                          bars5)
                # 不只 broken：刚跌破/正在测试的 holding 位也纳入候选
                _cur = float(q.get("current") or 0)
                levels = [c for c in (snap.get("candidates") or [])
                          if float(c.get("price") or 0) >= _cur * 0.999
                          and str(c.get("state") or "") in ("broken", "holding")
                          and abs(float(c.get("distance_pct") or 0)) <= 2.0]
                levels.sort(key=lambda c: abs(float(c.get("distance_pct") or 0)))
                for c in levels[:3]:
                    lv = float(c.get("price") or 0)
                    if lv <= 0:
                        continue
                    d3 = _vi.detect_3x15(bars5, lv, now)
                    dv = _vi.detect_volume_break(bars5, lv, now,
                                                 ratio_threshold=float(os.getenv("WOLF_V3_VOL_RATIO", "1.5")))
                    for mode, hit in (("3x15", d3), ("volume", dv)):
                        if not hit.get("confirmed"):
                            continue
                        dkey = (today, raw, mode, round(lv, 2))
                        if dkey in self._v3_intraday_done:
                            continue
                        self._v3_intraday_done.add(dkey)
                        blocked = _vi.in_execution_block(now)
                        status = "pending" if blocked else "confirmed"
                        _vi.save_watch(today, raw, lv, mode, str(hit.get("break_time") or hit.get("last_bar_time") or ""),
                                       int(hit.get("bar_count") or 0), str(hit.get("last_bar_time") or ""),
                                       float(hit.get("volume_ratio") or 0), status=status,
                                       payload={"candidate": c, "hit": hit})
                        self._v3_log({"date": today, "symbol": raw, "phase": "intraday_" + mode,
                                      "intraday_watch": {"level": lv, "status": status, "hit": hit}})
                        if not blocked:
                            self._v3_intraday_event(raw, q, ledger, mode, lv)
        except Exception as e:
            print("[TMonitor][V3] intraday watch 异常: %s" % str(e)[:120], flush=True)

    def _v3_check_rebound_watches(self) -> None:
        """30s 轮询反抽带：进入 zone → 非禁区执行；13:00-14:30 只挂 pending；14:55 过期。"""
        if str(os.getenv("WOLF_V3_LIVE", "0")).strip() not in ("1", "true", "yes", "on"):
            return
        try:
            import datetime as _dt
            from app.services import decision_window as _dw
            from app.services import wolf_v3_production as _vp
            today = _dt.datetime.now().strftime("%Y%m%d")
            watches = _vp.list_rebound_watches(today)
            if not watches:
                return
            syms = sorted({str(w.get("symbol")) for w in watches if w.get("symbol")})
            quotes = self._fetch_quotes_concurrent([_normalize_symbol(x) for x in syms])
            now = _dt.datetime.now()
            phase = _dw.phase_at(now).get("phase")
            after_1455 = (now.hour * 60 + now.minute) >= (14 * 60 + 55)
            for w in watches:
                sym = str(w.get("symbol"))
                q = quotes.get(_normalize_symbol(sym)) or quotes.get(sym) or {}
                px = float(q.get("current") or 0)
                hi_px = float(q.get("high") or px)
                lo, hi = float(w.get("zone_low") or 0), float(w.get("zone_high") or 0)
                if px <= 0 or lo <= 0 or hi <= 0:
                    continue
                _sig = _vp.rebound_signal(px, hi_px, lo, hi)
                in_zone, touch_limit = _sig["in_zone"], _sig["touch_limit"]
                # 确认后/等待中，收盘或现价站回 zone 上沿之外 → 结构修复/假破，取消反抽卖
                inv = float(w.get("invalidation_level") or hi)
                if px > inv:
                    _vp.mark_rebound_watch(int(w["id"]), "invalidated")
                    # 没执行过减仓 → 允许同一 level 二次触发：清掉当日 done key，重新 arm rebreak
                    if not _vp.pg_symbol_action_today(sym, today):
                        try:
                            for k in list(self._v3_intraday_done):
                                if len(k) >= 4 and k[0] == today and k[1] == sym and abs(float(k[3]) - float(w.get("break_price") or 0)) < 0.001:
                                    self._v3_intraday_done.discard(k)
                        except Exception:
                            pass
                        try:
                            from app.services import wolf_v3_intraday as _vi
                            _vi.save_watch(today, sym, float(w.get("break_price") or hi), "rebreak",
                                           "", 0, "", 0, status="watching",
                                           payload={"reason": "rebound_invalidated", "zone": [lo, hi]})
                        except Exception:
                            pass
                    self._v3_log({"date": today, "symbol": sym, "phase": phase,
                                  "rebound_invalidated": {"zone": [lo, hi], "invalidation": inv, "price": px},
                                  "rearm": not _vp.pg_symbol_action_today(sym, today)})
                    continue
                if in_zone and phase == "intraday_blocked":
                    if w.get("status") != "pending":
                        _vp.mark_rebound_watch(int(w["id"]), "pending")
                        self._v3_log({"date": today, "symbol": sym, "phase": phase,
                                      "rebound_pending": {"zone": [lo, hi], "price": px}})
                    continue
                if (in_zone or touch_limit) and not after_1455:
                    target = float(w.get("target_pct") or os.getenv("WOLF_V3_REBOUND_TARGET", "0.5"))
                    plan = _vp.plan_sell(sym, q, target, account_id=T_MONITOR_ACCOUNT)
                    if not plan.get("ok"):
                        continue
                    if touch_limit:
                        plan["price"] = lo
                    key = _vp.action_key(today, sym, "rebound_exit", "%.2f" % hi)
                    if _vp.already_done(key):
                        _vp.mark_rebound_watch(int(w["id"]), "executed")
                        continue
                    gw = _vp.execute_plan(plan, reason="[v3-rebound%s] %.2f-%.2f" % (
                        "-limit" if touch_limit else "", lo, hi), live=True)
                    _vp.record_action({"key": key, "date": today, "symbol": sym,
                                       "action": "rebound_exit", "level": {"price": hi},
                                       "target_pct": target, "plan": plan, "gateway": gw})
                    _vp.mark_rebound_watch(int(w["id"]), "executed" if gw.get("status") in ("success", "planned") else "watching")
                    self._v3_log({"date": today, "symbol": sym, "phase": phase,
                                  "rebound_exec": True, "zone": [lo, hi], "plan": plan, "gateway": gw})
                    if gw.get("status") == "success":
                        self._sold_this_round.add(sym)
                        print("[TMonitor][V3][REBOUND] %s %s股 @%.2f zone=%.2f-%.2f" % (
                            sym, plan.get("volume"), px, lo, hi), flush=True)
                    continue
                if after_1455 and w.get("status") in ("watching", "pending"):
                    # 持续下跌/始终不反抽：尾盘兜底卖，不再“过期等于不卖”
                    if _vp.pg_symbol_action_today(sym, today):
                        _vp.mark_rebound_watch(int(w["id"]), "executed")
                        continue
                    target = float(w.get("target_pct") or os.getenv("WOLF_V3_REBOUND_TARGET", "0.5"))
                    plan = _vp.plan_sell(sym, q, target, account_id=T_MONITOR_ACCOUNT)
                    if not plan.get("ok"):
                        _vp.mark_rebound_watch(int(w["id"]), "expired")
                        continue
                    key = _vp.action_key(today, sym, "rebound_fallback", "%.2f" % hi)
                    if _vp.already_done(key):
                        _vp.mark_rebound_watch(int(w["id"]), "executed")
                        continue
                    gw = _vp.execute_plan(plan, reason="[v3-rebound-fallback] %.2f-%.2f" % (lo, hi), live=True)
                    _vp.record_action({"key": key, "date": today, "symbol": sym,
                                       "action": "rebound_fallback", "level": {"price": hi},
                                       "target_pct": target, "plan": plan, "gateway": gw})
                    _vp.mark_rebound_watch(int(w["id"]), "executed" if gw.get("status") in ("success", "planned") else "expired")
                    self._v3_log({"date": today, "symbol": sym, "phase": phase,
                                  "rebound_fallback": True, "zone": [lo, hi], "plan": plan, "gateway": gw})
                    if gw.get("status") == "success":
                        self._sold_this_round.add(sym)
                        print("[TMonitor][V3][FALLBACK] %s %s股 @%.2f zone=%.2f-%.2f" % (
                            sym, plan.get("volume"), px, lo, hi), flush=True)
        except Exception as e:
            print("[TMonitor][V3] rebound watch 异常: %s" % str(e)[:120], flush=True)

    # ── 突破加仓／回踩确认加仓（账本 §9.304 ✓ 用户要求 ✓；两个开关**库内默认关** ✓）──
    # ── B：十字星后不突破 ⇒ 撤/减（账本 §9.340 ✓ 狼大 2026-02-12 原话 ✓；开关默认关 ✓）──


    def _round(self):
        """v3 纯净模式：只跑止损/破位/减仓，不再评估旧条件单/T/止盈/计划/避险策略。"""
        try:
            if STOP_V3:
                self._check_all_positions_v3()
        except Exception as e:
            self._status["errors"] += 1
            print(f"[TMonitor][V3] round 异常: {str(e)[:160]}", flush=True)

    # ── 账本 §9.498 ✓：**他的线组买点（buy_255）的字段产出** ✓ ──────────────────────
    #   他的原话：「好票跌到事先画好的线（**13/34/60/144**）→ 提前挂单买、与指数无关」✓
    #   量化（§9.495 ✓）：线组买点 T+5 均值 **+1.29%**／中位 +0.73%／左尾 5.0%
    #     vs 我们 254 的 **−0.11%／−0.45%／8.3%** ✓
    #   ⚠️ **无前视** ✓：线只用「**截至前一交易日**」的收盘 ✓；"当天"取 `DATA_DIR` 目录名（回放日 ✓），
    #      取不到才退 `datetime.now()`（生产 ✓）

    def _fetch_quotes_concurrent(self, symbols: List[str]) -> Dict[str, Optional[dict]]:
        """并发取价（腾讯 qt 直连），结果统一以归一化代码（sz159516）为键。

        修复（迭代#58c）：此前以原始 symbol（SZ159516）为键，而调用方用
        _normalize_symbol 小写键查询 → 全部 miss → 监控器对所有条件静默失效
        （"今日触发"恒为 0）。现两处键口径统一为归一化代码。
        """
        if not symbols:
            return {}
        result: Dict[str, Optional[dict]] = {}
        for i in range(0, len(symbols), MAX_WORKERS):
            batch = symbols[i:i + MAX_WORKERS]
            quotes = fetch_tencent_quote([_normalize_symbol(s) for s in batch])
            for ns in (_normalize_symbol(s) for s in batch):
                if ns in quotes and quotes[ns]:
                    result[ns] = quotes[ns]
        return result

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="t-monitor")
        self._thread.start()
        self._status["running"] = True
        print("[TMonitor] ✅ 做T监控器已启动")
        return True

    def stop(self) -> None:
        self._stop.set()
        self._status["running"] = False
        print("[TMonitor] 做T监控器已停止")


    # ── 条件评估 ──

    # ── 写触发事件 ──

def fill_premium_pct() -> float:
    """成交价溢价（%）：`suggest_bid_price = 现价×(1-p/100)`、`suggest_ask_price = 现价×(1+p/100)`。

    **2026-09-20 用户拍板「砍掉」**：这条 0.1% 约定**没有任何语料依据**（代码里字段名自认
    `slippage_budget`，随 commit 8ae11ed 引入时无引用），而且它**买卖两侧同向**——买侧少付 0.1%（有利）、
    卖侧少收 0.1%（不利，`t_account.py:311` 取价时**卖单也读 `suggest_bid_price`**）⇒ 往返基本抵消、
    对盈亏近乎中性，但让**所有成交价系统性比市场低 0.1%**，污染"我们的买点 vs 他的 0.618 位"这类对照。
    ⇒ 默认仍保持 0.1（**生产零影响**），回测用 `WOLF_FILL_PREMIUM_PCT=0` 关掉它（成交价 = 成交时报价）。

    ⚠️ 不追高闸里那个 `round(_qc*0.999,3)`（`_check_wolf_t_rules` 内）**不是成交价**，而是给闸门的
    "建议买价"参照（`wolf_no_chase.PREMIUM_PCT` 默认 0 ⇒ 只要 现价>参照 就算"没等到回踩"）；
    改它会让该闸永远放行 ⇒ 那里保持不变。
    """
    try:
        return max(0.0, float(os.getenv("WOLF_FILL_PREMIUM_PCT", "0.1")))
    except Exception:
        return 0.1


def fill_prices(current: float, pct: float = None, bid: float = None):
    """(bid, ask)：给定现价与溢价%，返回建议买价/卖价；`bid` 显式给出时优先（C 口径的挂线价）。"""
    p = fill_premium_pct() if pct is None else float(pct)
    try:
        cur = float(current or 0)
    except Exception:
        cur = 0.0
    _bid = round(float(bid), 3) if bid else round(cur * (1.0 - p / 100.0), 3)
    return _bid, round(cur * (1.0 + p / 100.0), 3)


def _is_wolf_t_condition(cond: Dict[str, Any]) -> bool:
    """只允许狼大做T表达式条件(expression 含 minute.m5.t_sell / t1_shrink_expand); 其他做T条件(V反/探针/默认)不评估。"""
    expr = cond.get("expression")
    if not isinstance(expr, dict):
        return False
    import json as _json
    s = _json.dumps(expr, ensure_ascii=False)
    # 2026-09-08: 手动护栏/自动离场卖腿(价位/黄线/回撤跟踪)纳入评估——此前因不含
    # WOLF_T_FIELDS字段被_round整轮跳过(155.2/588170黄线都不触发)
    if str(cond.get("trigger_kind") or "") in ("custom_level_sell", "custom_vwap_sell", "custom_trail_sell", "custom_support_sell"):
        return True
    # 2026-09-21: 趋势/突破腿（trend_break_buy，apps/main_line/trend_channel.py）纳入评估。
    #   它不含 WOLF_T_FIELDS（表达式是 quote.current/quote.average 的价位条件）⇒ 会被整轮跳过；
    #   只在通道开关打开时接受，关掉后即便 DB 里还留着旧腿也不会触发（生产零影响）。
    if str(cond.get("trigger_kind") or "") == "trend_break_buy":
        return str(os.getenv("WOLF_TREND_CHANNEL", "0")).strip().lower() in ("1", "true", "yes", "on")
    return any(f in s for f in WOLF_T_FIELDS)


# 结构性伪信号 bar（2026-09-02 复测发现, docs/t1-guard-reback-report.md）：
# 开盘两根(09:30/09:35)与午休后第一根(13:05)是 A股 结构性放量 bar——
# 指数184天中 T1 91% 命中午休效应假信号。T1 触发点排除这三根（阈值保持1.2x）。
T1_EXCLUDE_BARS = ("0930", "0935", "1305")


def _bar_hhmm(b) -> str:
    """从 bar 提取 HHMM（兼容腾讯 time='YYYYMMDDHHMM' / 带分隔符格式）"""
    t = str(b.get("trade_time") or b.get("time") or "")
    t = t.strip()
    if len(t) >= 12 and t.isdigit():
        return t[8:12]
    for sep in (" ", "T"):
        if sep in t:
            t = t.split(sep)[1]
    parts = t.split(":")
    if len(parts) >= 2:
        return parts[0] + parts[1]
    return t


def _bar_date8(raw) -> str:
    """从 bar 时间戳里取出交易日期 YYYYMMDD（**不依赖分隔符**）。

    2026-09-17 修复（P0）：生产分钟源（`t_data_sources.fetch_minute_bars`，brze 中继）返回的时间戳是
    **12 位 `YYYYMMDDHHMM`（无横线）**，而旧写法一律用
    `str(b["time"]).startswith(datetime.now().strftime("%Y-%m-%d"))`（带横线）去筛"当日 bar"——
    两种格式永远匹配不上 → 当日 bar 列表恒空。回测/腾讯 mkline 路径则是
    `2026-09-17 10:35:00`（带横线）。故必须两种格式都能归一：
      · `202609171035` → `20260917`
      · `2026-09-17 10:35:00` / `2026-09-17T10:35` / `2026/9/7` → `20260917`
      · 只有 HHMM（如 `1035`）或空值 → `""`（调用方按"无当日 bar"处理，别静默放行成假信号）
    受影响调用点（原来都因格式不匹配而失效）：
      · `_settle_tsell_pending`（撤销式T出永不结算，13 条观察恒停在 claimed）
      · `_build_minute_snapshot`（`minute.m1.low_today` 恒 0.0）
      · `stabilize_not_new_low_at`（企稳恒 True，fail-open）
      · `_index_intraday_dd`（盘中回撤恒 0.0；这处旧写法是 `%Y%m%d`，对带横线的时间戳同样失配）
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s[:8] if len(s) >= 8 else ""
    head = s.replace("T", " ").split(" ")[0]
    parts = [p for p in head.replace("/", "-").replace(".", "-").split("-") if p]
    if len(parts) >= 3:
        try:
            return "%04d%02d%02d" % (int(parts[0]), int(parts[1]), int(parts[2]))
        except (TypeError, ValueError) as _e_sil23:
            try:
                from app.services import alert_hub as _ah_sil
                _ah_sil.note_silent("t_monitor.py:5269", _e_sil23)
            except Exception:
                print("[silent:t_monitor.py:5269] %s: %s" % (type(_e_sil23).__name__, str(_e_sil23)[:110]), flush=True)
    digits = "".join(ch for ch in head if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _t_signals_from_m5(m5):
    """做T信号(狼大体系): T1缩转放(日内缩量后放量=正T买点) + 分时T出(7-29原话: 放量反弹→第一次分时高点→停量→第二次拉升无量不过前高)
    输入: m5 bars [{time, close, vol,...}] 返回 (t1, t_sell)
    2026-09-02: T1 触发点排除 09:30/09:35/13:05 结构性伪信号 bar。"""
    import numpy as _np
    t1 = False; t_sell = False
    try:
        closes = _np.array([float(b["close"]) for b in m5])
        vols = _np.array([float(b["vol"]) for b in m5])
        times = [_bar_hhmm(b) for b in m5]
        n = len(closes)
        # T1 缩转放: 近8根缩量(末端<=起点) 且 最新放量(>前8均量1.2)；触发点排除结构性 bar
        if n >= 18 and times[-1] not in T1_EXCLUDE_BARS:
            prev = vols[-9:-1]
            if prev.mean() > 0:
                t1 = bool(prev[-1] <= prev[0] and vols[-1] > prev.mean() * 1.2)
        # 分时T出(7-29): 放量反弹(vol>前look1.3) → 第一次分时高点 → 高点后停量(<0.8) → 第二次拉升无量不过前高
        look = 8
        if n >= look * 4 + 4:
            start = None
            for i in range(look, n - look - 2):
                base = vols[max(0, i-look):i].mean()
                if base > 0 and vols[i] > base * 1.3:
                    start = i; break
            if start is not None:
                seg = closes[start:n-look]
                hi = float(seg.max()); hi_idx = start + int(seg.argmax())
                if hi_idx >= start + 2:
                    va = vols[hi_idx+1:hi_idx+1+look].mean() if hi_idx+look < n else 0
                    vb = vols[max(start, hi_idx-look):hi_idx].mean()
                    if vb > 0 and va < vb * 0.8:
                        after = closes[hi_idx+1:]
                        sh = float(after.max()) if len(after) else 0
                        t_sell = bool(sh > hi * 0.98 and sh < hi * 1.005)
    except Exception as _eN24:
        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
        from app.services import gate_alarm as _gaNote
        _gaNote.note("t_monitor:line3634", _eN24)
        pass
    return t1, t_sell


# ── T出"撤销式"(2026-09-07, 离线验证 3357 触发点): 放量过前高=主升未完 → 撤销本次 T出
# m5.t_sell 触发后不立即卖: 延迟观察 TSELL_DELAY_S, 期间若出现 vol>1.3×前均量 且 close>段高
# → 撤销(继续持有等新高后新确认); 无放量新高且达延迟 → 执行卖出; 跨日不强制尾盘卖(day_end 已降级)
_TSELL_PENDING = {}   # symbol -> {hi, base, volume, trig_id, ts, account_id}
_TSELL_UNDO = os.getenv("WOLF_TSELL_UNDO", "1") != "0"
TSELL_DELAY_S = float(os.getenv("TSELL_DELAY_S", "600"))


def _tsell_hi_base(bars):
    """重扫当日 m5(逻辑同 _t_signals_from_m5): t_sell 成立的段高点 hi 与高点前均量 base -> (hi,base) 或 None"""
    import numpy as _np
    try:
        closes = _np.array([float(b["close"]) for b in bars])
        vols = _np.array([float(b["vol"]) for b in bars])
        look = 8; n = len(closes)
        if n < look * 4 + 4:
            return None
        for i in range(look, n - look - 2):
            base = vols[max(0, i - look):i].mean()
            if base > 0 and vols[i] > base * 1.3:
                seg = closes[i:n - look]
                hi = float(seg.max()); hi_idx = i + int(seg.argmax())
                if hi_idx >= i + 2:
                    va = vols[hi_idx + 1:hi_idx + 1 + look].mean() if hi_idx + look < n else 0
                    vb = vols[max(i, hi_idx - look):hi_idx].mean()
                    if vb > 0 and va < vb * 0.8:
                        sh = float(closes[hi_idx + 1:].max()) if hi_idx + 1 < n else 0
                        if sh > hi * 0.98 and sh < hi * 1.005:
                            return (float(hi), float(vb))
        return None
    except Exception:
        return None


def _tsell_undo_decide(hi, base, last_close, last_vol, elapsed_s):
    """撤销式决策: 'undo'(放量过前高→撤销T出) / 'sell'(无新高且超延迟→执行) / 'wait'"""
    if base > 0 and last_vol > 1.3 * base and last_close > hi:
        return "undo"
    if elapsed_s >= TSELL_DELAY_S:
        return "sell"
    return "wait"


# 指数盘中回撤缓存（30s TTL，避免每轮拉腾讯）
def _fetch_daily_tencent_dated(symbol: str, count: int = 40):
    """腾讯前复权**日线**（第三个兜底源, 2026-09-11 加）→ [{'date','open','close','high','low','vol'}]。

    为什么需要它: `_daily_dated` 的实时兜底主源是 `t_build._fetch_daily_bars`（Tushare→东财），
    生产实测**两个源都会短暂失败**（"东财日线失败: Remote end closed connection without response"），
    失败就退回冻结在 2026-09-03 的本地缓存 → ① 的两个机制又变哑。腾讯这个端点在
    `fetch_tencent_mkline`(分钟线)之外，日线走 web.ifzq.gtimg.cn 的 fqkline 接口，实测稳定且**含当日**。
    返回升序、已剔除当日那根（调用方口径：当日盘中最高用 quote.high 另行传入）。
    """
    try:
        import urllib.request, json as _j
        _s = str(symbol).lower()
        if _s[:2] not in ("sh", "sz", "bj"):
            _d = "".join(ch for ch in _s if ch.isdigit())
            _s = ("sh" if _d[:1] == "6" else ("bj" if _d[:2] in ("43", "83", "87", "92", "92") else "sz")) + _d
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               "?param=%s,day,,,%d,qfq" % (_s, max(int(count), 20)))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = _j.loads(resp.read().decode("utf-8"))
        node = (d.get("data") or {}).get(_s) or {}
        rows = node.get("qfqday") or node.get("day") or []
        today = datetime.now().strftime("%Y%m%d")
        out = []
        for r in rows:
            try:
                _dd = str(r[0]).replace("-", "")[:8]
                if not _dd or _dd >= today:
                    continue
                out.append({"date": _dd, "open": float(r[1]), "close": float(r[2]),
                            "high": float(r[3]), "low": float(r[4]), "vol": float(r[5] or 0)})
            except (ValueError, IndexError, TypeError) as _e_sil9:
                _silent_alert("t_monitor.py:5485", _e_sil9)
                continue
        out.sort(key=lambda x: x["date"])
        return out[-int(count):] if out else None
    except Exception as e:
        print(f"[TMonitor] 腾讯日线兜底失败 {symbol}: {str(e)[:100]}")
        return None


_index_dd_cache = {"at": 0.0, "value": 0.0}
_m5_dump_cache = {"at": 0.0, "value": 0.0}
_m5_stock_dump_cache: Dict[str, dict] = {}   # 个股 5min 急杀缓存（按票 ✓）
_prev_low_px_cache: Dict[str, dict] = {}      # 前一日 5min 最低价缓存（按票 ✓；§9.182）
# ②量能分层卖(2026-09-08): 缩量破位→反抽减等待状态 + 支撑腿破位禁低吸
PULLBACK_VOL_RATIO = float(os.getenv("PULLBACK_VOL_RATIO", "1.2"))  # vol_ratio<该值视为缩量
PULLBACK_END_HM = 1445          # 14:45 后仍未反抽达标 → 尾盘确认离场


def pullback_ref_up(sup, vwap, prev_close=0.0) -> float:
    """「量能分层」等待态的反抽目标位。

    原口径（开关关，**默认**）：`max(支撑 support_l1, 当日均价 VWAP)`。
    新口径（`WOLF_PULLBACK_REF_UP=1`）：再抬到 `max(支撑, 均价, 前收×(1+WOLF_PULLBACK_REF_UP_PCT%))`，
      默认 `PCT=0`（即**要求真回到前收**）—— 语义是「缩量破位先不慌，但要真回到前收才卖；当天摸不到就仍按
      原 14:45 尾盘确认卖出」，不引入隔夜风险、无跨日状态。
    依据（2026-09-20，五账户实测）：
      · 该族在 drabj13 是 **17 笔实际 −4,581、+5 日反事实 Δ +7,320（净卖飞）**，量比 0.47–1.39 全为缩量、
        卖价≈当日收盘；本开关（当日完成）的 Δ = drabj13 +1,075(前收) / +1,386(×1.005)、
        draby26 +9,336 / +6,511、draymar +8,116 / +4,931、drabq1 +2,940 / +2,724、drabsize +1,074 / +318
        ⇒ **5/5 账户为正**，受益笔分散（单笔占比 14–33%）。
      · 对照口径「只延后『反抽到离场位』那一支」**不普适**：drabj13 +2,694 但**单笔占 91%**（去掉最大笔仅 +252），
        且 **draby26 −3,254、draymar −5,061 反号**（非极值造成）⇒ 故不做那一支，改做本开关。
      · 目标位敏感性：**前收（PCT=0）在 5 个账户中 4 个优于 ×1.005** ⇒ 取默认 0。
    `prev_close` 缺失/为 0 ⇒ 退回原口径（fail-safe，行为不变）。
    """
    try:
        r = max(float(sup or 0), float(vwap or 0))
    except Exception:
        return 0.0
    try:
        if str(os.getenv("WOLF_PULLBACK_REF_UP", "0")).strip().lower() in ("1", "true", "yes", "on"):
            _pc = float(prev_close or 0)
            if _pc > 0:
                try:
                    _pct = float(os.getenv("WOLF_PULLBACK_REF_UP_PCT", "0"))
                except Exception:
                    _pct = 0.0                      # 环境变量写坏 ⇒ 落默认(前收)，而不是放弃抬高
                r = max(r, _pc * (1.0 + _pct / 100.0))
    except Exception as _eN25:
        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
        from app.services import gate_alarm as _gaNote
        _gaNote.note("t_monitor:line3760", _eN25)
        pass
    return round(r, 4)
_PULLBACK_SELL: Dict[str, dict] = {}   # symbol -> pending(缩量破位待反抽/尾盘确认)
def _stop_time_ok(now=None):
    """止损**时点约束**（2026-09-10，狼大止损六层之④）→ (ok, reason)。

    狼大 2026-03-23：「每天的止损**绝对不应该是下午1点到2点半**这个时间。。。**要么你早上卖 要么你尾盘卖**」。
    他当时是在描述"量化在 13:00-14:30 硬止损被收割"的现象 —— 即该时段执行止损是**劣势时点**。

    → 本门禁止在 [13:00, 14:30) 执行止损（该时段仅预警，不实际卖出）；
      收盘确认清仓（默认 >=14:55）落在 14:30 之后，**不受影响**。

    开关: WOLF_STOP_TIME_GATE=0 关闭; 禁止窗口可用 WOLF_STOP_BLOCK_FROM / WOLF_STOP_BLOCK_TO 调整(默认 1300/1430)。
    """
    if os.getenv("WOLF_STOP_TIME_GATE", "1").strip() in ("0", "false", "no"):
        return True, "时点门关闭"
    try:
        n = now or datetime.now()
        hm = n.strftime("%H%M")
    except Exception:
        return True, "时间不可用→放行"
    a = str(os.getenv("WOLF_STOP_BLOCK_FROM", "1300"))
    b = str(os.getenv("WOLF_STOP_BLOCK_TO", "1430"))
    if a <= hm < b:
        return False, "狼大2026-03-23: 止损不应在 %s-%s 执行(要么早上卖要么尾盘卖)" % (a, b)
    return True, ""


def _in_close_window(now=None) -> bool:
    """是否处于"收盘确认"时段（默认 >= 14:55）。

    P2-4: 狼大「收盘跌破我才出」→ 清仓只在收盘时段执行; 盘中只做减半仓。
    可用 WOLF_CLOSE_BREAK_HM 调整(如设 "1500" 表示严格收盘后)。
    """
    try:
        hm = (now or datetime.now()).strftime("%H%M")
    except Exception:
        return False
    return hm >= str(os.getenv("WOLF_CLOSE_BREAK_HM", "1455"))


# ── 底仓 floor 穿透策略（**狼大原话口径**，2026-09-14）─────────────────────────
# 他的底仓不是"永远不卖"：正常持有期不动（2025-05-27「我T了一把 底仓不动」），
# 但**顶部阶段的"完全止盈"就是清仓**——2025-05-13「顶部阶段…全止盈的位置就放在日线
# BOLL 中轨附近，放量跌破收盘完全止盈」。故此处按机制白名单放行，不做全局放行。
FLOOR_BREAK_KINDS = frozenset({"wolf_boll_mid_exit"})
# 死腿：机制已删（quote.trail_break 恒 False，2026-09-10 删除）→ 不再跨日结转
_ROLL_SKIP_KINDS = frozenset({"custom_trail_sell"})


def _position_accounts() -> tuple:
    """持仓口径账户（2026-09-14 用户拍板：**只读 stock**；t 账户是测试账户、暂时不使用）。

    要临时切回 t 账户：`WOLF_POSITION_ACCOUNT=t`（改环境变量即可，不用改调用点）。
    """
    return (os.getenv("WOLF_POSITION_ACCOUNT", T_MONITOR_ACCOUNT).strip() or T_MONITOR_ACCOUNT,)


def _rollable(conds: Optional[List[dict]]) -> List[dict]:
    """跨日结转前的过滤：剔除**已删除机制的死腿**（见 _ROLL_SKIP_KINDS）。

    背景（2026-09-14 G0）：custom_trail_sell 的机制 2026-09-10 已被删除（quote.trail_break 恒 False），
    但 _roll_wolf_legs 每天仍把它复制成新条件 → 永不触发却冒充"有卖腿"。
    """
    return [c for c in (conds or []) if str((c or {}).get('trigger_kind') or '') not in _ROLL_SKIP_KINDS]


def _floor_break_enabled(kind: str) -> bool:
    """该离场机制是否允许卖底仓（狼大策略口径）。整体开关 WOLF_FLOOR_BREAK=0 可退回"只卖T仓"。"""
    if os.getenv("WOLF_FLOOR_BREAK", "1").strip() in ("0", "false", "no"):
        return False
    return str(kind) in FLOOR_BREAK_KINDS


def _hedge_reduce_volume(sellable: int, floor: int) -> int:
    """周末/长假前避险卖出量 = **T 仓的一半**（他 2026-08-21「把这两天T进去的仓位出来一半」）。

    T 仓 = 可卖 − 底仓 floor；取一半、100 股整数倍；无 T 仓 → 0（**不卖底仓**，与他的原话一致）。
    """
    t_shop = max(int(sellable or 0) - int(floor or 0), 0)
    return (t_shop // 2 // 100) * 100


def _wh_exec_enabled() -> bool:
    """G9 避险是否进入执行层（默认 1=执行；0=只保留提示层）。"""
    return os.getenv("WOLF_WH_EXEC", "1").strip() not in ("0", "false", "no")


def _wh_should_execute(state: Optional[dict], today8: str, hhmm: str, cutoff_hm: str = "1430") -> bool:
    """避险执行门（纯函数）：状态 active ∧ as_of=今日 ∧ 已过检查时点。

    时点按"周末前 / 长假前"分开（G9）：周末前 14:30（他 2026-08-21「2点半…14:35 我按刚才说的操作了」）；
    **长假前 10:00 早盘卖**（他 2025-04-29「节假日出今日…尽量做到早盘卖 尾盘买的反T」）。
    """
    if not isinstance(state, dict) or not state.get("active"):
        return False
    if str(state.get("as_of") or "") != str(today8):
        return False
    try:
        _cut = "".join(ch for ch in str(cutoff_hm or "1430") if ch.isdigit())[:4] or "1430"
        return (int(str(hhmm)[:2]) * 60 + int(str(hhmm)[2:4])
                >= int(_cut[:2]) * 60 + int(_cut[2:4]))
    except Exception:
        return False


def _stop_exit_volume(sellable: int, close_window: bool, floor: int = 100):
    """止损/破位的卖出量分级（P2-4 完整落地, 2026-09-10）。

    狼大 2026-01-29「今天没跌破我没出, 我说了 **收盘跌破我才出**」;
           2026-01-12「**等收盘确认破位出清**」。

    · close_window=True  → **清仓**: 全部可卖(含 100 股工程底仓) —— 狼大"收盘跌破才**出清**";
    · close_window=False → **减半仓**: 保留底仓继续做T(原语义; 全卖会导致后续高抛触发时无券可卖,
                            AI 反复"无底仓"放弃)。

    开关: WOLF_BASE_EXIT_CLOSE=0 关闭收盘清仓(退回"一律减半仓")。
    返回 (volume, mode)；mode ∈ {"close_clear","half"}。
    """
    s = (int(sellable or 0) // 100) * 100
    if s <= 0:
        return 0, "half"
    _enabled = os.getenv("WOLF_BASE_EXIT_CLOSE", "1").strip() not in ("0", "false", "no")
    if close_window and _enabled:
        return s, "close_clear"
    half = (s // 2 // 100) * 100
    return (half if half >= 100 else s), "half"


# P2-4: 止损"收盘未确认"日志去抖 (symbol, stop_price, date) —— 避免每 30s 轮次刷屏
_STOP_HOLD_WARNED: set = set()


# ── 单例管理（对齐 candidate_pool_monitor 模式） ──
_monitor_instance: Optional[TMonitor] = None
_monitor_lock = threading.Lock()



def get_t_monitor(interval_seconds: int = MONITOR_INTERVAL, trade_executor=None) -> TMonitor:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = TMonitor(interval_seconds=interval_seconds, trade_executor=trade_executor)
        elif trade_executor is not None and _monitor_instance._trade_executor is None:
            _monitor_instance._trade_executor = trade_executor
        return _monitor_instance


def start_t_monitor(trade_executor=None) -> bool:

    # 账本 §9.543（用户「全局异常处理…异常推送QQ，避免吞异常」）：**安装全局钩子**
    #   实情：alert_hub 早已写好（落盘 alerts.jsonl + 推 QQ + 防刷屏 + threading.excepthook），
    #   但**没有入口调用 install()** ⇒ 异常不告警（"没做好"就是这一步）
    try:
        import os as _osI
        _osI.environ.setdefault("WOLF_ALERT_HUB", "1")
        from app.services import alert_hub as _ahI
        print("[alert_hub] 全局异常钩子安装: %s" % _ahI.install(), flush=True)
    except Exception as _eI:
        print("[alert_hub] 安装失败（不静默）: %s: %s" % (type(_eI).__name__, str(_eI)[:90]), flush=True)
    """启动 T 监控（**先装全局异常钩子** ✓ —— 用户「统一走QQ推送」✓）。"""
    try:
        from app.services import alert_hub as _ah3
        _ah3.install()
    except Exception as _e_sil24:
        try:
            from app.services import alert_hub as _ah_sil
            _ah_sil.note_silent("t_monitor.py:5646", _e_sil24)
        except Exception:
            print("[silent:t_monitor.py:5646] %s: %s" % (type(_e_sil24).__name__, str(_e_sil24)[:110]), flush=True)
    monitor = get_t_monitor(trade_executor=trade_executor)
    ok = monitor.start()
    # 桥不可达降级：启动低频轮询兜底线程（消费 pending 事件，执行仍经网关）
    try:
        from app.services.t_bridge import fallback_poll_loop
        if not getattr(monitor, "_fallback_started", False):
            monitor._fallback_started = True
            threading.Thread(
                target=fallback_poll_loop,
                args=(monitor._stop,),
                daemon=True,
                name="t-bridge-fallback",
            ).start()
            print("[TMonitor] ✅ 降级兜底轮询线程已启动")
    except Exception as e:
        print(f"[TMonitor] ⚠️ 降级兜底线程启动失败: {e}")
    return ok


def stop_t_monitor() -> None:
    global _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            _monitor_instance.stop()


def get_t_monitor_status() -> Dict[str, Any]:
    monitor = get_t_monitor()
    return monitor.status()


def _sma(values: List[float], period: int) -> float:
    """简单移动平均（取序列最后 period 根的均值；不足则全量均值）。"""
    if not values:
        return 0.0
    window = values[-period:] if len(values) >= period else values
    return round(sum(window) / len(window), 4)


def _expr_summary(expression: Any) -> str:
    """表达式人类可读摘要（写进触发事件快照）。"""
    if not expression:
        return ""
    try:
        from app.services.t_expr import expression_summary
        return expression_summary(expression)
    except Exception:
        return str(expression)[:120]


def _ema(values: List[float], period: int) -> List[float]:
    """指数移动平均序列。"""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _calc_macd_from_closes(closes: List[float]) -> tuple:
    """MACD(12,26,9) → (dif, dea, bar)。"""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    bar = 2.0 * (dif[-1] - dea[-1])
    return round(dif[-1], 4), round(dea[-1], 4), round(bar, 4)


def _calc_kdj_from_bars(highs: List[float], lows: List[float], closes: List[float],
                        current: float) -> tuple:
    """KDJ(9,3,3) → (k, d, j)。用最近9根高低 + 当前价。"""
    if len(closes) < 9:
        return 50.0, 50.0, 50.0
    h9 = max(highs[-9:])
    l9 = min(lows[-9:])
    rsv = (current - l9) / (h9 - l9) * 100 if h9 > l9 else 50.0
    k = 2 / 3 * 50.0 + 1 / 3 * rsv
    d = 2 / 3 * 50.0 + 1 / 3 * k
    j = 3 * k - 2 * d
    return round(k, 2), round(d, 2), round(j, 2)


def _calc_rsi(closes: List[float], period: int = 6) -> float:
    """RSI(Wilder 平滑)。"""
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


# ────────────────────────────────────────────────────────────────
# 纯函数评估集（now 注入，回测与实时共用；TMonitor 方法为薄转发）
# ────────────────────────────────────────────────────────────────

# ★ 账本 §9.663 ✓（用户拍板「改」✓）：**条件单路径的 B/C 买入时点闸** ✓
#   实测 ✗（0106 SH688372）：同一轮、**同一价 112.900** —— `trend_break_buy` **成交了** ✗，
#   紧接着同一价又被判「买入时点闸→本次不执行（不在下跌中买）」✗ ⇒ **条件单漏过该闸** ✓
#   （正T买腿那条路有闸 ✓；网关 0.2c 的 B/C 段在本路径下实测也没拦住 ✗）
#   语料 ✓：2025-06-05「急杀可以买，缓跌不买」；2025-04-03「冲上去一定不能追」；
#           2025-05-23「尾盘能回来就尾盘买 急什么」；2026-01-12「买点只有尾盘」
#   ⇒ 开关 `WOLF_FALLING_GATE_COND`（**库内默认 0 ⇒ 生产逐位不变** ✓；回测 pins 置 1 ✓）
COND_TIMING = os.getenv("WOLF_FALLING_GATE_COND", "0").strip().lower() in ("1", "true", "yes", "on")


def cond_timing_on() -> bool:
    """条件单（只传 condition_id 的那条路 ✓）是否也走 B/C 买入时点闸 ✓。"""
    return COND_TIMING


# ★ 账本 §9.664 ✓：**认 `t_conditions.start_time`/`end_time`** ✓
#   实测 ✗：两列本来就有 ✓、`upsert_condition` 也支持 ✓，但**监视线从来不读** ✗
#   ⇒ 布腿端写进去的"当日时间窗"完全没生效 ✗（突破腿 14:00–14:30 这条 T3 就是靠它 ✓）
#   ⇒ 开关 `WOLF_COND_TIME_WINDOW`（**库内默认 0 ⇒ 生产逐位不变** ✓；回测 pins 置 1）
COND_TIME_WINDOW = os.getenv("WOLF_COND_TIME_WINDOW", "0").strip().lower() in ("1", "true", "yes", "on")
_WINDOW_SKIPPED = set()


def cond_window_ok(cond) -> bool:
    """条件是否在**当日允许的时间窗**内 ✓（无窗 ⇒ 恒 True ✓，绝不因缺字段而拦 ✗）。"""
    if not COND_TIME_WINDOW:
        return True
    try:
        st = str((cond or {}).get("start_time") or "").strip()
        et = str((cond or {}).get("end_time") or "").strip()
        if not st and not et:
            return True
        hm = datetime.now().strftime("%H%M")
        if st and hm < st:
            return False
        if et and hm > et:
            return False
        return True
    except Exception as _e_w:
        print("[TMonitor] 时间窗判定失败(放行 ✓): %s" % str(_e_w)[:70], flush=True)
        return True


def _is_build_kind(kind) -> bool:
    """**建仓类腿型**（与资金上限 `WOLF_BUILD_CAP_KINDS` 同一份 ✓）——§9.865 的买带豁免只对它生效 ✓。"""
    return str(kind or "").strip() in {k.strip() for k in (os.getenv(
        "WOLF_BUILD_CAP_KINDS", "trend_break_buy,buy_253,buy_254,custom_buy,wolf_build") or "").split(",") if k.strip()}


def _leg_band(expr):
    """建仓腿**买带** (下界, 上界)（§9.865 ✓）；取不到 ⇒ None（⇒ 旧行为 ✓）。"""
    try:
        try:
            import intraday_crush as _ic
        except ImportError:
            from main_line import intraday_crush as _ic
        return _ic.band_from_expr(expr)
    except Exception:
        return None


def _falling_gate_exempt(trigger_kind) -> bool:
    """★ 2026-10-13（用户「做吧」）：**B/C 买入时点闸的豁免名单**（默认空 ⇒ 行为逐位不变 ✓）

    依据（`scripts/dip_buy_deep.py` 离线实测 ✓）：被 `[COND-TIMING]` 拦下的买点，若按 `quote_price` 买入，
      · `wolf_ambush_buy` 46 笔：**当日收盘 +1.34%／次日收盘 +7.76%** ✓
      · `trend_break_buy` 18 笔：当日 +0.66%／次日 +2.22% ✓
      · 且被拦触发的**位置均值 0.18～0.25（低位占比 67～80%）** ✗ ⇒ 疑**误拦低位好点** ✓
    ⇒ 提供名单开关交由**对照回测**定论 ✓（粗口径局限：未含 T 腿止盈/止损逻辑 ✗、被拦者无反事实成交价 ✗）
    env：`WOLF_FALLING_GATE_EXEMPT_LEGS=wolf_ambush_buy,trend_break_buy`（逗号分隔 ✓）
    """
    raw = os.getenv("WOLF_FALLING_GATE_EXEMPT_LEGS", "") or ""
    kinds = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return bool(kinds) and str(trigger_kind or "").strip().lower() in kinds


def intraday_timing_blocked(symbol: str, is_buy: bool, pre_close: Optional[float] = None,
                           band=None) -> bool:
    """B/C 买入时点闸的**执行口兜底**（2026-09-22 用户拍板 "A+B+C"）。

    B `WOLF_FALLING_GATE`：现价 < 当日 VWAP ∧ 前 3 根 5min 连跌 ⇒ 本次不执行（不在下跌中买）。
    C `WOLF_WEAK_DEFER`：当日为跌 ∧ 现价 < VWAP 且未到 `WOLF_WEAK_DEFER_HM` ⇒ 延后到尾盘重评。
    与 `intraday_crush_blocked` 同一族（C2″ 抓"恐慌急杀"，本条抓"缓跌/瀑布"）；取不到分钟档 ⇒ 放行。
    """
    if not is_buy:
        return False
    try:
        try:
            import intraday_crush as _ic
        except ImportError:
            from main_line import intraday_crush as _ic
        if not (_ic.falling_on() or _ic.weak_defer_on()):
            return False
        now = datetime.now()
        ok, why = _ic.timing_verdict(symbol, now.strftime("%Y%m%d"), now.strftime("%H%M"),
                                     pre_close=pre_close, band=band)
        if not ok:
            print("[TMonitor] 买入时点闸拦下买腿 %s：%s" % (symbol, str(why)[:120]), flush=True)
            return True
    except Exception as e:
        print("[TMonitor] 买入时点闸异常(放行) %s: %s" % (symbol, str(e)[:80]))
    return False


def ambush_warn_active() -> bool:
    """**指数层预警**（账本 §9.205）—— 是否应把埋伏腿降到防御档 ✓。

    语料（逐字 ✓）：「**趁机在周2前减仓**」（2026-03-02）✓｜「明天有冲高**减到 70%**」（2026-03-05）✓
                「**等指数企稳**了 我打回国算链」（2026-03-20）✓
    量化（21 个月 ✓，**样本外分段验证 ✓**）：
      · 无预警 ⇒ +58.58%／**回撤 −21.6%** ✗
      · **W1(<MA20) + W3(3 日跌占比≥65%) ⇒ 防御档 5 只** ⇒ **+75.93%／回撤 −7.5%** ✓✓
      · 分段：动荡段（2026）**+35.12%／−7.9%** vs 现状 **+17.96%／−19.0%** ✓✓
    开关：`WOLF_AMBUSH_WARN_MA`（默认 0 = 关 ✓；>0 ⇒ 指数收盘 < 其 MA_n 视为预警 ✓）
          ⚠️ **注意（账本 §9.206）**：量化里的 **W3（近 3 日全市场跌占比 ≥65%）在运行时取不到**
             （需要全市场数据 ✗）⇒ **实际落地的是 W1 单条件（指数收盘 < 日线 MA_n）** ✓
             而 W1-only 的量化是 **+66.40%／回撤 −8.6%** ✓（略逊于 W1+W3 的 +75.93%／−7.5% ✓ 但仍远好于现状 ✗）
             `WOLF_AMBUSH_WARN_BREADTH` **仅为占位** ✓（当前不参与判定 ✗，待用可得数据实现后再启用 ✓）
    指数取 `sh000001`（回测 as-of 层提供 ✓）；取不到 ⇒ **不预警**（fail-open ✓，与量化基线一致 ✓）
    """
    try:
        _ma = int(float(os.getenv("WOLF_AMBUSH_WARN_MA", "0") or 0))
        _bw = float(os.getenv("WOLF_AMBUSH_WARN_BREADTH", "0") or 0)
        _dd0 = float(os.getenv("WOLF_AMBUSH_WARN_DAY_DROP", "0") or 0)
    except Exception:
        return False
    if _ma <= 0 and _bw <= 0 and _dd0 <= 0:
        return False
    _key = (datetime.now().strftime("%Y%m%d"), _ma, _bw, _dd0)
    if _AMBUSH_WARN_CACHE.get("key") == _key:
        return bool(_AMBUSH_WARN_CACHE.get("val"))
    val = False
    try:
        if _ma > 0:
            # ⚠️ 2026-09-26 修（账本 §9.206）：原先拿 **5min 收盘**直接算均值 ✗
            #   ⇒ 那其实是"最近 _ma 根 5min"（≈ 100 分钟 ✗），**不是**量化里的**日线 MA20** ✗
            #   正确做法：把 5min 序列**按交易日聚合出日收盘** ✓，再算日线均线 ✓
            # 2026-09-26 修（账本 §9.210）：**必须用监控同源**（`DATA_DIR/recent_sync/<code6>.json` ✓）
            #   病灶：先前用 `fetch_minute_bars("sh000001")` ✗ ⇒ **臂里取不到** ⇒ 静默不预警 ✗
            #   （而监控自己的 `_daily_dated` 正是读 `recent_sync` ✓ ⇒ 同源即同可用 ✓）
            import json as _js
            _D = os.environ.get("DATA_DIR", "/app/data")
            _pj = os.path.join(_D, "recent_sync", "000001.json")
            cl = []
            if os.path.exists(_pj):
                with open(_pj, encoding="utf-8") as _f:
                    _dd = _js.load(_f)
                _days = sorted(str(k) for k in (_dd or {}).keys())
                for _k in _days:
                    _bs = _dd.get(_k) or _dd.get(str(_k)) or []
                    if isinstance(_bs, list) and _bs:
                        _c = _bs[-1].get("close")
                        if _c:
                            cl.append(float(_c))
                print("[TMonitor] 埋伏预警取数：recent_sync/000001.json ⇒ %d 个交易日 ✓" % len(cl), flush=True)
            # 2026-09-26 修（账本 §9.211）：`recent_sync` 的 index 文件是**逐日累积**的
            #   （0105 有 1 天、0106 有 2 天 ✓）⇒ 前期**不够算 MA10** ✗ ⇒ 必须**合并分钟库** ✓
            #   否则预警要跑到第 11 天才生效 ✗（实测：day2 只取到 1 个交易日 ⇒ fail-open ✗）
            if len(cl) < _ma + 1:
                try:
                    from app.services.t_data_sources import fetch_minute_bars
                    _bars2 = fetch_minute_bars("sh000001", freq="m5", count=max(400, (_ma + 5) * 60)) or []
                    _by2: Dict[str, float] = {}
                    for _b in _bars2:
                        _t2 = str(_b.get("time") or _b.get("trade_time") or "")
                        _d82 = _t2[:10].replace("-", "")
                        _c2 = _b.get("close")
                        if _d82 and _c2:
                            _by2[_d82] = float(_c2)          # 升序遍历 ⇒ 末值即当日收盘 ✓
                    _merge = dict(_by2)
                    if os.path.exists(_pj):
                        with open(_pj, encoding="utf-8") as _f2:
                            _dd2 = _js.load(_f2)
                        for _k2 in (_dd2 or {}):
                            _bs2 = _dd2.get(_k2) or []
                            if isinstance(_bs2, list) and _bs2 and _bs2[-1].get("close"):
                                _merge[str(_k2)] = float(_bs2[-1]["close"])
                    cl = [_merge[k] for k in sorted(_merge)]
                    print("[TMonitor] 埋伏预警取数：recent_sync %d 天 ∪ 分钟库 %d 天 ⇒ 合并 %d 个交易日 ✓"
                          % (len(cl), len(_by2), len(cl)), flush=True)
                except Exception as _eM:
                    print("[TMonitor] 埋伏预警取数：分钟库合并不成 ⇒ %s" % str(_eM)[:70], flush=True)
            if len(cl) >= _ma + 1:
                _m = sum(cl[-_ma:]) / _ma
                val = cl[-1] < _m
                _why = ("指数 %.2f < MA%d %.2f" % (cl[-1], _ma, _m)) if val else \
                       ("指数 %.2f ≥ MA%d %.2f" % (cl[-1], _ma, _m))   # 2026-09-26 修：正常时不能也写"指数<MA" ✗
                # ② **当日急跌**（`WOLF_AMBUSH_WARN_DAY_DROP`，默认 0 = 关 ✓）——
                #    量化里的 W3b ✓ 且**只用指数自身** ⇒ **运行时可得** ✓（账本 §9.208）
                #    组合 `指数<MA10 ∨ 当日跌幅≥1.5%`：全样本 **+84.65%／回撤 −8.8%** ✓
                #      分割：2025 **+30.45%／−6.5%** ✓｜2026 **+43.60%／−10.3%** ✓✓（均优于单用 MA10 ✓）
                try:
                    _dd = float(os.getenv("WOLF_AMBUSH_WARN_DAY_DROP", "0") or 0)
                except Exception:
                    _dd = 0.0
                if _dd > 0 and len(cl) >= 2 and cl[-2] > 0:
                    _ret = (cl[-1] / cl[-2] - 1) * 100
                    if _ret <= -_dd:
                        val = True
                        _why += " ∨ 当日 %+.2f%%" % _ret
                print("[TMonitor] 埋伏预警判定：指数收盘 %.2f vs MA%d %.2f（%d 个交易日 ✓）⇒ %s（%s）"
                      % (cl[-1], _ma, _m, len(cl), "预警 ✓" if val else "正常", _why), flush=True)
            else:
                print("[TMonitor] 埋伏预警判定：日线不足（%d < MA%d+1）⇒ **不预警**（fail-open）"
                      % (len(cl), _ma), flush=True)
    except Exception as _eW:
        print("[TMonitor] 埋伏预警(指数)取数异常 ⇒ 不预警: %s" % str(_eW)[:70], flush=True)
    _AMBUSH_WARN_CACHE["key"] = _key
    _AMBUSH_WARN_CACHE["val"] = val
    if val:
        print("[TMonitor] ⚠️ 埋伏预警生效（指数层）⇒ 埋伏腿降防御档 ✓", flush=True)
    return val


_AMBUSH_WARN_CACHE: Dict[str, Any] = {}


def intraday_crush_blocked(symbol: str, is_buy: bool, kind: str = "") -> bool:
    """盘中「急杀分」判据（2026-09-21 C2′+C2″）：买腿**触发那一刻**的形态检查。

    · C2″ 极端门：放量(量比≥1.5) ∧ 跌破前一日低点 ∧ 跌破当日 VWAP ⇒ 拦
    · C2′ 同日准入：当日触发数 ≥ K 后，只放行急杀分 ≥ 当日已触发中位数的
    为什么放在触发时：253/254 腿是 08:18 **开盘前**布的，只有那一刻才有分钟级信息。
    语料方向：2025-06-05「**急杀可以买，缓跌不买**」。开关 `WOLF_CRUSH_GATE`（默认 0）；取不到分钟档 ⇒ 放行。
    """
    # 2026-09-26 用户「都按你说的来」✓：**埋伏腿豁免本门**（`WOLF_AMBUSH_SKIP_SLOWDECLINE`，库内默认 0 ✓）
    #   为什么必须豁免：埋伏的触发本身就是「**温和回踩到位**」⇒ 天然常被判为"缓跌" ✗
    #   （实测 SH603078：`blocked 缓跌且未命中任何买点：现价 26.540 < 均价 26.708` ✗）
    #   而量化（§9.182）用的正是「**回踩前低**」（含缓跌 ✓）且为**正**：+6.29%／为正 63% ✓
    if (str(kind or "") == "wolf_ambush_buy"
            and str(os.getenv("WOLF_AMBUSH_SKIP_SLOWDECLINE", "0")).strip().lower() in ("1", "true", "yes", "on")):
        return False
    if not is_buy:
        return False
    try:
        try:
            import intraday_crush as _ic
        except ImportError:
            from main_line import intraday_crush as _ic
        if not _ic.enabled():
            return False
        now = datetime.now()
        ok, why, sc = _ic.check(symbol, now.strftime("%Y%m%d"), now.strftime("%H:%M"),
                                account=(os.getenv("T_MONITOR_ACCOUNT") or ""))
        if not ok:
            print("[TMonitor] 盘中午判据拦下买腿 %s：%s（急杀分 %.2f）" % (symbol, why, sc), flush=True)
            return True
    except Exception as _eN26:
        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
        from app.services import gate_alarm as _gaNote
        _gaNote.note("t_monitor:line4107", _eN26)
        pass
    return False


def ticket_ban_blocked(symbol: str, is_buy: bool) -> bool:
    """G3「卖出后删票」买入侧拦截（语料 2025-02-06「卖出然后删票」/ 2025-04-03「破之前新低的直接删票」）。

    2026-09-21 用户："怎么还有天津普林和博敏电子" —— 实测发现：`t_triggers` 有**两条写入路径**
      （① `_write_trigger` 条件腿 ② `_insert_wolf_trigger` 做T规则腿），我第一版只堵了 ①，
      而这两只票的复发买入走的是 ② `wolf_zheng_t_buy`。⇒ 收敛成一个 helper，两处都调。
    开关 `WOLF_TICKET_BAN_FIX`（库内默认 0 ⇒ 生产零影响）；失败 fail-open。
    """
    if not is_buy:
        return False
    try:
        try:
            import ban_filter as _bf
        except ImportError:
            from main_line import ban_filter as _bf
        if _bf.enabled() and _bf.is_banned(symbol):
            print("[TMonitor] G3 删票名单命中，跳过买腿 %s（账户=%s）" % (symbol, _bf.current_account()), flush=True)
            return True
    except Exception as _eN27:
        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
        from app.services import gate_alarm as _gaNote
        _gaNote.note("t_monitor:line4130", _eN27)
        pass
    return False


def _is_buy_side(cond: Dict[str, Any]) -> bool:
    """条件执行方向（迭代#58）：direction 显式优先；缺省按 trigger_kind 默认。

    - low_buy/panic_vibrate → 买腿
    - direction=buy/买（custom 等自由类型显式声明）→ 买腿
    - 其余（high_sell/custom 未声明等）→ 卖腿（保持既有语义，避免未标方向的
      custom 突然变成买入）
    """
    kind = cond.get("trigger_kind", "low_buy")
    if kind in ("low_buy", "panic_vibrate"):
        return True
    d = str(cond.get("direction") or "").strip().lower()
    if d in ("buy", "买", "买入"):
        return True
    return False

def evaluate_condition_at(cond: Dict[str, Any], quote: dict, regime_state: dict,
                          snapshot: Optional[dict], now: datetime) -> bool:
    """条件评估（纯函数）：有 expression 走自由表达式求值；无则回退默认复合确认逻辑。"""
    expression = cond.get("expression")
    if expression:
        from app.services.t_expr import evaluate_expression
        try:
            if not evaluate_expression(expression, snapshot or {}):
                return False
        except Exception as e:
            print(f"[t-eval] 表达式求值异常 {cond.get('symbol')}: {e}")
            return False
        # 表达式通过后仍要过通用护栏（时段/状态机/regime 门）
        return pass_common_gates(cond, regime_state, now)
    return evaluate_default_at(cond, quote, regime_state, snapshot, now)


def _prev_daily_rows(sym, n=5):
    """最近 n 个交易日的 {close, high, low, vol}（读 data/recent_sync 或 stock_5m_bt）✓ 模块级。

    从 `TMonitor._prev_daily` 提取（2026-09-25，账本 §9.125）：模块级闸门不能访问 `self` ✗。
    """
    import os as _os, json as _j
    D = _os.environ.get('DATA_DIR', '/app/data')
    code6 = ''.join(ch for ch in str(sym) if ch.isdigit())[:6]
    data = {}
    # ★ 账本 §9.617 ✓：**只在"两个源都失败"时才留痕** ✗
    #   原实现逐 root 报警 ⇒ 回测里 `stock_5m_bt` 永远不存在 ✗ ⇒ **每只票都抛一条假警报** ✗
    #   （实测 t_monitor.py:6105 是当日告警的一大来源 ✓）而真正的兜底 `recent_sync` 随后成功 ✓
    #   ⇒ 行为完全不变（顺序与兜底都不动 ✓），只是**不再报假警** ✓
    _miss10 = None
    d = None
    for root in ['stock_5m_bt', 'recent_sync']:
        p = _os.path.join(D, root, code6 + '.json')
        try:
            d = _j.load(open(p, encoding='utf-8'))
            _miss10 = None
            break
        except Exception as _e_sil10:
            _miss10 = _e_sil10
            continue
    if d is None:
        if _miss10 is not None:
            _silent_alert("t_monitor.py:6105", _miss10)
        return []
    for k, v in d.items():
            bs = sorted(v, key=lambda x: str(x.get('time') or x.get('trade_time')))
            if bs:
                data.setdefault(k, {'close': float(bs[-1]['close']),
                                    'high': max(float(b['high']) for b in bs),
                                    'low': min(float(b['low']) for b in bs),
                                    'vol': sum(float(b.get('vol') or 0) for b in bs)})
    today = datetime.now().strftime('%Y%m%d')
    days = sorted(k for k in data if k < today and data[k].get('vol'))
    return [data[k] for k in days[-n:]]


def pass_common_gates(cond: Dict[str, Any], regime_state: dict, now: datetime) -> bool:
    """表达式通过后的通用护栏：regime GATE + 语境(253) + 时段 + 状态机（纯函数）。"""
    trigger_kind = cond.get("trigger_kind", "low_buy")
    gate = check_gate(trigger_kind, regime_state)
    if not gate["allowed"]:
        return False
    # P1-3(2026-09-10): 253(指数急杀低吸) 的**狼大分语境闸门** —— 筑底/上行可接, 未筑底不接。
    # 依据狼大 2025-06-05「急杀可以买，缓跌不买」/ 2025-07-28「跳水了你高位减仓的钱敢不敢买」
    # vs 2026-05-15「只要磨出底部结构…肯定不是急杀的时候买啊」。判据复用 wave_agent 的狼大语境标签。
    # 注: 该闸门**不是**时钟窗(语料无时段规则), 也**不是** P1-4 的 wave 全军门(只作用于 253 这一条)。
    if trigger_kind in ("custom_m5dump", "m5_dump"):
        try:
            import sys as _wc
            _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "main_line")
            if _p not in _wc.path:
                _wc.path.insert(0, _p)
            from wolf_context import m5dump_allowed
            _ok, _why = m5dump_allowed(symbol=cond.get("symbol"),
                                       prev_days=_prev_daily_rows(cond.get("symbol"), 6))
            if not _ok:
                print(f"[TMonitor] 253语境闸门拦截 {cond.get('symbol')}: {_why}", flush=True)
                return False
            if os.getenv("WOLF_253_CONTEXT_LOG", "0") == "1":
                print(f"[TMonitor] 253语境闸门放行 {cond.get('symbol')}: {_why}", flush=True)
        except Exception as _e:
            # 仅"模块不可导入"这类代码级故障才放行(避免一处 import 错误把 253 整条买路封死);
            # 语境本身缺失/未知时由 wolf_context 内部 fail-closed 处理。
            print(f"[TMonitor] 253语境闸门异常(放行): {str(_e)[:120]}", flush=True)
    hm = now.hour * 100 + now.minute
    if hm >= 1445:
        return False
    if cond.get("armed") != 1:
        return False
    if cond.get("last_triggered_at"):
        try:
            last = datetime.strptime(str(cond["last_triggered_at"]), "%Y-%m-%d %H:%M:%S")
            if (now - last).total_seconds() < COOLDOWN_SECONDS:
                return False
        except (ValueError, TypeError) as _e_sil25:
            try:
                from app.services import alert_hub as _ah_sil
                _ah_sil.note_silent("t_monitor.py:6060", _e_sil25)
            except Exception:
                print("[silent:t_monitor.py:6060] %s: %s" % (type(_e_sil25).__name__, str(_e_sil25)[:110]), flush=True)
    return True


def evaluate_default_at(cond: Dict[str, Any], quote: dict, regime_state: dict,
                        snapshot: Optional[dict], now: datetime) -> bool:
    """默认复合企稳确认（纯函数）：regime GATE ∧ 价到位 ∧ 量能企稳 ∧ 分时企稳 ∧ 状态机 ∧ 时段。

    量比/分时企稳优先取快照（回测快照重建器提供），缺省回退现场计算（实时路径）。
    """
    trigger_kind = cond.get("trigger_kind", "low_buy")

    # 0) regime GATE（低吸 BLOCKED 直接短路）
    gate = check_gate(trigger_kind, regime_state)
    if not gate["allowed"]:
        return False

    # 1) 时段：14:45 后禁新开仓
    hm = now.hour * 100 + now.minute
    if hm >= 1445:
        return False

    # 2) 状态机：armed + cooldown + 当日触发上限
    if cond.get("armed") != 1:
        return False
    if cond.get("last_triggered_at"):
        try:
            last = datetime.strptime(str(cond["last_triggered_at"]), "%Y-%m-%d %H:%M:%S")
            if (now - last).total_seconds() < COOLDOWN_SECONDS:
                return False
        except (ValueError, TypeError) as _e_sil26:
            try:
                from app.services import alert_hub as _ah_sil
                _ah_sil.note_silent("t_monitor.py:6091", _e_sil26)
            except Exception:
                print("[silent:t_monitor.py:6091] %s: %s" % (type(_e_sil26).__name__, str(_e_sil26)[:110]), flush=True)

    # 3) 价格到位（低吸：current ≤ target；高抛：current ≥ sell_target）
    # 修复（迭代#58）：high_sell 与 high_sell_then_buy_back 同样检查高抛目标——
    # 此前仅 high_sell_then_buy_back 有价格门，high_sell 无表达式时量比达标即触发。
    current = float(quote.get("current", 0) or 0)
    target = float(cond.get("target_price") or 0)
    sell_target = float(cond.get("sell_target_price") or 0)
    if trigger_kind in ("low_buy", "panic_vibrate"):
        if target <= 0 or current > target:
            return False
    elif trigger_kind in ("high_sell", "high_sell_then_buy_back"):
        if sell_target <= 0 or current < sell_target:
            return False

    # 4) 量能企稳（量比归一 ≥ 阈值；快照优先；阈值 0 表示关闭量比过滤）
    raw_thresh = cond.get("vol_ratio_thresh")
    vol_thresh = float(raw_thresh) if raw_thresh is not None else 1.5
    vol_ratio = None
    if snapshot and snapshot.get("vol_ratio") is not None:
        try:
            vol_ratio = float(snapshot["vol_ratio"])
        except (TypeError, ValueError):
            vol_ratio = None
    if vol_ratio is None:
        vol_ratio = calc_volume_ratio_at(cond, quote, now)
    if vol_ratio is not None and vol_ratio < vol_thresh:
        return False

    # 5) 分时企稳（低吸：不再创新低；高抛：冲高；快照 stabilised 优先）
    stabilised = bool(snapshot and snapshot.get("quote", {}).get("stabilised"))
    stabilize = cond.get("stabilize_level", "not_new_low")
    if trigger_kind in ("low_buy", "panic_vibrate"):
        if stabilize == "not_new_low":
            ok = stabilised if snapshot else stabilize_not_new_low_at(cond["symbol"], current, now)
            if not ok:
                return False
    return True


def calc_volume_ratio_at(cond: Dict[str, Any], quote: dict, now: datetime) -> Optional[float]:
    """盘中换手节奏比（纯函数）：当前累计换手×时段伸缩 / 个股换手基准。

    公式：vol_ratio = [当前累计换手 × (240/已开盘连续分钟)] / 基准
    基准从 benchmark_turnover_profile.same_minute_avg 读（近5已完成交易日
    日换手均值, 见 t_turnover_profile），缺省用 MIN_TURNOVER_BASE(0.5%) 兜底。
    注意：这是"按当前节奏外推全天换手 ÷ 个股全天基准"的倍数（≈行情量比），
    不是累计换手率本身，也不是行情软件"每分钟均量/近5日同刻均量"的严格同刻口径。
    """
    turnover = float(quote.get("turnover_rate", 0) or 0)
    if turnover <= 0:
        return None
    # 已开盘连续分钟
    opened = 0
    if 930 <= now.hour * 100 + now.minute <= 1130:
        opened = (now.hour - 9) * 60 + now.minute - 30
    elif 1300 <= now.hour * 100 + now.minute <= 1500:
        opened = 120 + (now.hour - 13) * 60 + now.minute
    if opened <= 0:
        return None
    # 基准：condition 里存的同刻均值；缺省 2%
    profile = cond.get("benchmark_turnover_profile")
    base = None
    if isinstance(profile, (dict, str)):
        import json as _json
        try:
            p = _json.loads(profile) if isinstance(profile, str) else profile
            base = float(p.get("same_minute_avg") or 0)
        except (ValueError, TypeError, AttributeError):
            base = None
    if not base:
        base = MIN_TURNOVER_BASE
    scaled = turnover * (240.0 / opened)
    return round(scaled / base, 3)


def stabilize_not_new_low_at(symbol: str, current: float, now: datetime) -> bool:
    """分时企稳（纯函数）：用 m1 分钟线判断当日是否创新低（近 10 根最低 ≥ 当前 × 0.999）。"""
    try:
        from app.services.t_data_sources import fetch_minute_bars
        bars = fetch_minute_bars(symbol, freq="m1", count=120)
        if not bars:
            return True  # 无分钟线时放行（有腾讯 qt 实时兜底）
        today8 = now.strftime("%Y%m%d")
        today_lows = [b["low"] for b in bars if _bar_date8(b.get("time")) == today8]
        if not today_lows:
            return True
        day_low = min(today_lows)
        # 当前价未创新低（或仅在日低上方 0.1% 内视为企稳）
        return current >= day_low * 0.999
    except Exception:
        return True
