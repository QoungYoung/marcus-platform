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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import t_db
from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote, fetch_quote_one
from app.services.t_regime import check_gate, compute_regime, _is_trading_time

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
        except Exception:
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
            except Exception:
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

    def status(self) -> Dict[str, Any]:
        return dict(self._status)

    # ── 主循环 ──
    def _run(self):
        time.sleep(INITIAL_OFFSET)  # 错峰
        # 2026-09-03 修复：启动即补当日狼大持续腿——跨日/重启后监控条件不再丢失
        # （worker 常在非交易时段重启；这里无条件幂等补一次，交易日切换再补一次）
        try:
            self._roll_wolf_legs(datetime.now().strftime("%Y%m%d"))
            self._arm_stock_exit_legs(datetime.now().strftime("%Y%m%d"))
        except Exception as e:
            print(f"[TMonitor] 启动持续腿结转异常: {e}")
        while not self._stop.is_set():
            round_start = time.time()
            try:
                if _is_trading_time():
                    today_d = datetime.now().strftime("%Y%m%d")
                    if self._daily_maintained != today_d:
                        self._daily_maintained = today_d
                        # 2026-09-03：狼大持续腿跨日结转不依赖自动维护开关——
                        # _daily_maintain 停用后也必须让 249/250/252/253/254 每个交易日可用
                        try:
                            self._roll_wolf_legs(today_d)
                            self._arm_stock_exit_legs(today_d)
                        except Exception as e:
                            print(f"[TMonitor] 交易日持续腿结转异常: {e}")
                        if T_MONITOR_AUTO_MAINTAIN:   # 2026-09-02: 默认停自动维护(只留狼大做T条件)
                            self._status["daily_maintained"] = self._daily_maintain()
                            self._start_ai_maintain()
                    self._round()
                    self._settle_tsell_pending()  # 撤销式T出结算(放量过前高→撤销/超时→执行)
                    self._ambush_warn_trim()      # 指数层预警 ⇒ 埋伏仓减到防御档（账本 §9.205 ✓）
                    self._settle_pullback_sell()  # ②量能分层(2026-09-08): 缩量破位→反抽/尾盘确认离场
                    self._check_plan_triggers()   # 计划触发(复用同一监控器): 命中→唤醒交易agent
                    self._check_wolf_t_rules()    # 做T规则(向狼大看齐): 正T/倒T命中→写t_triggers
                    self._check_roundtrip_sell()  # B模型·等量换手: 低吸后反弹≥狼大兑现幅度(默认+3%)卖≤N旧仓
                    # day_end 默认降级: 不做'未确认→必卖'(那批几乎全亏); 卖出仅靠确认制T出/defensive
                    # 开 WOLF_DAY_END_T_OUT=1 ⇒ 恢复狼大「做T当日出来，不留过夜」(14:45 后未确认即减T仓)
                    if DAY_END_T_OUT:
                        self._check_day_end_de_t()
                    self._check_defensive_t_reduce()  # 风险/结构恶化(量能不足+滞涨)→减已持T仓(08-27式)
                    self._check_board_half()  # 板上减半(狼大纪律②): 触及/接近涨停+浮盈达标→减半锁定
                    self._check_profit_take()  # 小赚兑现(P0-3, 2026-09-14 开): 浮盈>=阈值→减T半仓(保留底仓)
                    self._check_weekend_hedge()  # G9 周末/长假前避险**执行层**(2026-09-14, 狼大 2026-08-21)
                    self._check_hedge_refill()   # C2b 避险回补腿(下一个交易日补回; 狼大「周一再拿回来」)
                    self._check_fib_target()     # G2 个股止盈点=前一波拉升 0.618 位(狼大 2026-04-23)
                    self._check_passive_stop()   # G4 被动止盈线只上移、不破不卖(狼大 2025-06-09/2026-07-01)
                    self._check_boll_sell()  # A10 BOLL上轨减半锁利(2026-09-11, 狼大 2026-04-29)
                    self._check_boll_mid_exit()  # A10 BOLL中轨全止盈(尾盘确认窗, 狼大 2025-05-13)
                    self._check_position_discipline()  # 去弱留强(P1-6): 反弹语境内减T仓最弱者
                    self._check_index_level_stop()  # ③ 指数大级别止损(狼大2026-08-27): 转下跌1浪→止损
                    self._check_logic_time_stop()  # ① 后半句: 13日内未碰前高→逻
                    self._check_ambush_discipline()  # 埋伏纪律: 固定 N 日 + 宽止损（默认关 ✓）
                    self._check_intraday_t_stop()  # 日内T仓紧止损（默认关 ✓；狼大 2026-02-02 口径）辑时间离场(狼大2026-03-05)
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
                except Exception:
                    pass
            elapsed = (time.time() - round_start) * 1000
            self._status.update({
                "last_round": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_round_ms": round(elapsed, 1),
            })
            # jitter 等待
            wait = self.interval + ((time.time() * 1000) % (JITTER * 2 + 1) - JITTER)
            self._stop.wait(max(5.0, wait))

    def _check_plan_triggers(self) -> None:
        """计划触发检查(复用TMonitor 30s轮次): 命中armed计划→立即 run_trade_decision 唤醒交易agent。"""
        try:
            from app.services.plan_runner import plan_context
            from app.services.trade_graph import run_trade_decision
            block = plan_context()   # 评估armed计划, 命中→status=fired+写plan_replay_log, 返回上下文块
            if '🔔 计划命中' in block:
                res = run_trade_decision(
                    'auto_trade_plan_trigger',
                    'PLAN' + datetime.now().strftime('%Y%m%d%H%M%S'),
                    'plan trigger: 立即执行已命中的计划(回补/建仓/减半)')
                self._status['plan_fired'] = datetime.now().isoformat()
                print('[TMonitor] 🔔 计划触发→唤醒交易agent:', res.get('pi_stance'),
                      '|', (res.get('report') or '')[:120])
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] 计划触发检查异常: {e}")

    def _check_roundtrip_sell(self) -> None:
        """B模型·等量换手卖出检查(2026-09-08 落地; 2026-09-11 兑现幅度; 2026-09-14 A1+A4):
        当日低吸N后，按狼大做 T 流程卖出 ≤N 股旧仓（不动当日买入/底仓 floor）。

        **A4（2026-09-14 用户"改成跟狼大一致"）**：
          · 目标兑现只在**他的两个做 T 时间窗**内执行（09:45–10:00 / 14:00–14:30，
            2025-04-15 成文流程条件 2）——窗口外达标不卖，让它跑；
          · **到点决断**：14:00 仍未达标也 T 掉收工（2026-09-02 14:03「2 点到了 力度不够
            我先把早上博弈的先T了…结束今天半导体做T操作」），容忍"亏个手续费"（默认 0.5%）；
          · **保护**：确认破黄线（A1：幅度≥0.5% 或连续 2 轮）任何时候都可走
            （2026-08-04 10:48「一旦突发跌破直接走」）。
        回退：`WOLF_RT_WINDOW=0`（回 A1 原行为，任何时间达标即卖）；`WOLF_RT_FORCE_HM=off`（只保留窗口语义）；
        `WOLF_RT_PRIORITY=vwap_first`（回"破线优先"旧行为）。
        ROUNDTRIP_SELL_UP 默认 0.03 = 狼大「3-5个点」下沿(2026-08-13 楼275/280; 2026-09-02 楼728)。
        实测（n=428 参考腿，`jobs/eval_roundtrip_windows.py`）：本口径 ≈0（不是靠它赚钱，是靠它忠实），
        且 1 日/2 日窗口内各口径差异都 ≤0.15pp —— 见 docs/exit-rules-m5-report.md §4.2。
        """
        try:
            from app.services import roundtrip_sell as _rs
            from app.services import roundtrip_priority as RP
            if not _rs.ROUNDTRIP_ENABLED:
                return
            pend = _rs.pending_symbols()
            if not pend:
                return
            hm = datetime.now().strftime("%H:%M")     # A4：做 T 时间窗 / 到点决断
            quotes = self._fetch_quotes_concurrent([s for s, _ in pend])
            for sym, st in pend:
                try:
                    q = quotes.get(sym) or {}
                    cur = float(q.get("current") or 0)
                    if cur <= 0:
                        continue
                    buy_avg = float(st.get("buy_avg") or 0)
                    avg = float(q.get("average") or q.get("avg_price") or 0)
                    _streak = int(self._vwap_break_streak.get(sym, 0))
                    act, why, _streak2 = RP.roundtrip_decision(
                        cur, buy_avg, avg or None, _rs.sell_up_for(sym), streak=_streak, hm=hm)
                    if _streak2:
                        self._vwap_break_streak[sym] = _streak2
                    else:
                        self._vwap_break_streak.pop(sym, None)
                    if act == "wait":
                        continue
                    from app.services.t_gateway import (gateway_execute, get_sellable_ledger,
                                                        base_floor_shares)
                    acct = st.get("account") or "stock"
                    ledger = get_sellable_ledger(account_id=acct)
                    item = ledger.get(sym) or {}
                    sellable = int(item.get("sellable", 0) or 0)
                    floor = base_floor_shares(acct, sym, volume=sellable)
                    rem = _rs.remaining(sym)
                    vol = min(rem, max(sellable - floor, 0))
                    vol = (vol // 100) * 100
                    if vol < 100:
                        continue
                    gw = gateway_execute(
                        sym, "sell", cur, vol,
                        reason=(f"[B等量换手] 低吸@{buy_avg:.2f}→现价@{cur:.2f}@{hm}"
                                f"({act}: {why}) 卖回{vol}股"),
                        decision_source="rule", account_id=acct)
                    if gw.get("status") == "success":
                        _rs.mark_sold(sym, vol)
                    else:
                        print(f"[RoundT] 换手卖出被拒 {sym}: {gw.get('reason')}")
                except Exception as e:
                    print(f"[RoundT] {sym} 检查异常: {e}")
        except Exception as e:
            print(f"[TMonitor] _check_roundtrip_sell 异常: {e}")

    def _prev_daily(self, sym, n=5):
        """最近 n 个交易日的 {close, high, low, vol}（委托模块级 `_prev_daily_rows` ✓）。

        2026-09-25（账本 §9.125）：`pass_common_gates` 是**模块级函数**却写了 `self._prev_daily(...)`
        ⇒ NameError ⇒ **253 语境闸门从未真正生效** ✗（日志 51 次 `异常(放行): name 'self' is not defined`）
        ⇒ 逻辑提取为模块级函数，方法与闸门共用同一实现 ✓
        """
        return _prev_daily_rows(sym, n)

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
            except Exception:
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
                        except Exception:
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
                    if _last:
                        print(f"[TMonitor] _daily_dated 本地缓存过期({sym} 停在 {_last}) → {_src}补齐 "
                              f"{len(_live)} 根(至 {_live[-1]['date']})")
                    out = sorted(_live, key=lambda x: x['date'])[-n:]
                elif _last:
                    print(f"[TMonitor] _daily_dated 实时源全部失败 {sym} → 用本地缓存(停在 {_last})")
        self._dated_cache[ck] = out
        return out

    def _buy_date(self, sym, today=None):
        """建仓日（paper_trades 首笔未作废买入；退回 paper_positions.entry_date），当日缓存。"""
        _t = today or datetime.now().strftime('%Y%m%d')
        ck = (T_MONITOR_ACCOUNT, sym, _t)
        if ck not in self._buy_date_cache:
            try:
                from app.services.wolf_early_stop import first_buy_date
                self._buy_date_cache[ck] = first_buy_date(T_MONITOR_ACCOUNT, sym)
            except Exception:
                self._buy_date_cache[ck] = None
        return self._buy_date_cache[ck]

    def _today_bars(self, sym):
        """当日 5min bars（读 recent_sync/stock_5m_bt/{code6}.json 的今天）。"""
        import os as _os, json as _j
        D=_os.environ.get('DATA_DIR','/app/data')
        code6=''.join(ch for ch in str(sym) if ch.isdigit())[:6]
        today=datetime.now().strftime('%Y%m%d')
        for root in ['recent_sync','stock_5m_bt']:
            p=_os.path.join(D,root,code6+'.json')
            try: d=_j.load(open(p,encoding='utf-8'))
            except Exception: continue
            if today in d: return d[today]
        return []

    def _log_cycle(self, sym, cyc):
        """把正T买→分时T出→收益 追加到 data/wolf_t_cycles.jsonl。"""
        import os as _os, json as _j
        D=_os.environ.get('DATA_DIR','/app/data')
        with open(_os.path.join(D,'wolf_t_cycles.jsonl'),'a',encoding='utf-8') as f:
            f.write(_j.dumps({'at':datetime.now().isoformat(),'symbol':sym,**cyc},ensure_ascii=False)+chr(10))

    def _held_today(self, sym) -> bool:
        """该票当前是否持有（供 `WOLF_BUY_LINE_SCOPE=rebuy/new` 判"再买 vs 首次建仓"）。

        60s TTL 缓存，避免每个标的每一轮都查库；**读失败时返回 True**（=继续挂线，偏保守）。
        """
        import time as _t0
        try:
            if _t0.time() - float(self._held_cache.get("at") or 0) > 60.0:
                _s = set()
                for _p in (self._positions() or []):
                    try:
                        if float(_p.get("volume") or 0) > 0:
                            _s.add(str(_p.get("symbol")))
                    except Exception:
                        continue
                self._held_cache = {"at": _t0.time(), "set": _s, "ok": True}
        except Exception:
            self._held_cache = {"at": _t0.time(), "set": set(), "ok": False}
        if not self._held_cache.get("ok"):
            return True                      # 持仓读不到 ⇒ 按"继续挂线"处理（保守）
        s = str(sym)
        return (s in self._held_cache["set"]) or (s[:2] + s[2:] in self._held_cache["set"])

    def _insert_wolf_trigger(self, sym, kind, quote, reason, account_id=None, bid=None, line=None):
        """把 wolf_t_rules 命中写成 t_triggers(pending/auto)，复用同一做T执行管道(不直接下单)。

        `account_id` 可指定（纪律类卖腿要覆盖 stock 账户，而不是只写 t 账户）；返回触发 id。
        """
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            current=float(quote.get('current') or 0)
        except Exception:
            current=0
        # 2026-09-19：把「当日自低点拉升%／日内分位%」写进快照 —— 供**不追高闸**（wolf_no_chase）
        #   在执行口判定"是不是冲上去追"（语料 2025-04-03「冲上去一定不能追」；他给的追高正解是
        #   2025-04-15 条件6「想追进去的…在下午 2.00-2.30 进行回补」）。
        _dl = float(quote.get('low') or 0)
        _dh = float(quote.get('high') or 0)
        _rise = round((current / _dl - 1.0) * 100, 3) if (current > 0 and _dl > 0) else None
        _quant = round((current - _dl) / (_dh - _dl) * 100, 2) if (_dh > _dl > 0 and current > 0) else None
        # 2026-09-19：另写「前低 / 当日低 / 量比」——供**放量破位门**（wolf_dip_volume_gate）在执行口判定
        #   「放量破前低 = 杀跌，不是 254 低吸位」（254 的原话口径是"破前低+缩量"）。
        #   ⚠️ 2026-09-19 事故：`_prev_daily` 返回的是 **list[{'close','high','low','vol'}]**（按日期列表，
        #      不是按日期索引的 dict）——首版按 dict 写 ⇒ `.get` 打到 list 上 ⇒ `wolf_t_rules检查异常:
        #      'list' object has no attribute 'get'` **每个标的都抛** ⇒ 全天零触发（实测 0106 触发 0、
        #      成交 0）。教训：往 hot path 里加取数必须**容错 + 形状自适应**，且异常绝不能冒泡出去。
        try:
            _pdl = self._prev_daily(sym, 5) or []
            _p_last = _pdl[-1] if isinstance(_pdl, list) and _pdl else (
                _pdl.get(sorted(str(k) for k in _pdl)[-1]) if isinstance(_pdl, dict) and _pdl else {})
            _prev_low = float((_p_last or {}).get('low') or 0) if isinstance(_p_last, dict) else 0.0
        except Exception:
            _prev_low = 0.0
        # 2026-09-24：改用 wolf_leg_vol_ratio —— quote 缺 vol_ratio 时按条件腿同口径补算
        #   （回测取数替身不提供该字段；开关 WOLF_VOL_RATIO_FILL 默认 0 ⇒ 旧行为）
        _vr = wolf_leg_vol_ratio(quote, symbol=sym)
        # 2026-09-21（D1）：另写「当日开盘 / 昨收」——供**开盘不追高闸**（wolf_no_chase.open_verdict）
        #   在执行口判「现价距**当日开盘**已快速拉升多少」（语料 2025-04-15 条件6）。
        #   ⚠️ 条件腿那条路径的 snapshot 本来就有 `fields.quote.open`；**wolf_t_rules 这条没有**
        #      ⇒ 不补这两个字段，"开盘不追高"闸在 `wolf_zheng_t_buy`（实测占买成交 84%）上会**静默 fail-open**。
        try:
            _op = float(quote.get('open') or 0) or None
        except Exception:
            _op = None
        try:
            _pc = float(quote.get('pre_close') or 0) or None
        except Exception:
            _pc = None
        _fp = fill_prices(current, bid=bid)
        # ⚠️ 2026-09-30（账本 §9.380 ✓ 用户「修复这个问题」✓）：`trigger_price` 原先**写死 None** ✗
        #   实测 ✓：全表 11,410 条触发里只有 6 条有价（0.1% ✗），而那 6 条都是 `stop_loss`
        #   （走 `stop_price` 那条路径 ✓）⇒ 这条 wolf 腿路径**一直没填** ✗
        #   这里**只填"手里真有的价"** ✓：`bid` = C 口径线价 ✓（`line` 是线**名**不是价 ✓）
        #   纯状态型腿（量能分层/黄线/破位 ✓）本就无单一价位 ⇒ 保持 None ✓（语义成立 ✓）
        #   开关 ✓：`WOLF_TRIGGER_PRICE_FILL`（库内默认 0 = 关 ⇒ 生产逐字不变 ✓）
        _tp_fill = None
        _line_name = None
        try:
            if str(os.getenv("WOLF_TRIGGER_PRICE_FILL", "0")).strip().lower() in ("1", "true", "yes", "on"):
                _tp_fill = float(bid) if bid else None
                _line_name = (str(line) if line else None)
        except Exception:
            _tp_fill = None
            _line_name = None
        trig={
            "account_id": account_id or T_MONITOR_ACCOUNT, "condition_id": None, "symbol": sym,
            "event_type": kind, "trigger_price": _tp_fill, "quote_price": current,
            # 2026-09-20：成交价溢价改为可配（`WOLF_FILL_PREMIUM_PCT`，默认 0.1 = 生产现状；
            #   回测 pins 置 0 ⇒ 成交价 = 成交时报价，砍掉那条无语料依据的 0.1%）。
            #   买单若由 C 口径给线价（`bid`），则优先用线价。
            "suggest_bid_price": _fp[0], "suggest_ask_price": _fp[1],
            "slippage_budget": round(fill_premium_pct() / 100.0, 6),
            "snapshot": {"quote_time": now, "wolf_rule": reason, "trigger_kind": kind, "source": "wolf_t_rules",
                         "day_rise_pct": _rise, "day_quantile": _quant, "open": _op, "pre_close": _pc,
                         "prev_low": (_prev_low or None), "today_low": (_dl or None), "vol_ratio": _vr,
                         "line_name": _line_name},
            "mode": "auto",
            "direction": "buy" if kind == "wolf_zheng_t_buy" else "sell",
        }
        try:
            from app.services import t_trigger_mute as _tm0
            if _tm0.is_muted(sym, kind):
                return None                      # 当日静默（无 T 仓可卖 / G8 已停机）
        except Exception as _eN0:
            # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
            from app.services import gate_alarm as _gaNote
            _gaNote.note("t_monitor:line685", _eN0)
            pass
        if ticket_ban_blocked(sym, str(trig.get("direction")) == "buy"):
            return None                       # G3 删票名单：买入侧不再复发（见 helper docstring）
        if intraday_crush_blocked(sym, str(trig.get("direction")) == "buy", str(kind or "")):
            return None                       # C2′+C2″ 盘中午判据
        try:
            tid = t_db.insert_trigger(trig)
            if tid:
                print(f"[TMonitor] wolf_t_rules触发 #{tid} {sym} {kind} @ {current} ({reason})")
            return tid
        except Exception as e:
            print(f"[TMonitor] wolf_t_rules写触发异常: {e}")
            return None

    def _settle_pullback_sell(self) -> None:
        """②量能分层卖结算(2026-09-08): 缩量破位等待中的标的——
        反抽回 ref_up(支撑/黄线上沿) → 执行卖T仓; 14:45后仍未收回 → 尾盘确认离场; 处理完即清理。"""
        if not _PULLBACK_SELL:
            return
        try:
            from app.services.t_gateway import gateway_execute, resolve_sell_cap
            now_hm = datetime.now().hour * 100 + datetime.now().minute
            for sym in list(_PULLBACK_SELL.keys()):
                p = _PULLBACK_SELL[sym]
                try:
                    cur = 0.0
                    try:
                        from app.services.t_data_sources import fetch_tencent_quote, _normalize_symbol
                        _ns = _normalize_symbol(sym)
                        q = fetch_quote_one(_ns) or {}
                        cur = float(q.get("current") or 0)
                    except Exception:
                        cur = 0.0
                    if cur <= 0:
                        continue
                    do_sell, reason_tag = False, ""
                    if cur >= float(p.get("ref_up") or 0):
                        do_sell, reason_tag = True, "反抽到离场位"
                    elif now_hm >= PULLBACK_END_HM:
                        do_sell, reason_tag = True, "14:45尾盘确认离场"
                    if not do_sell:
                        continue
                    cap = resolve_sell_cap(sym, account_id=p.get("account_id") or T_MONITOR_ACCOUNT)
                    vol = min(int(p.get("volume") or 0), cap)
                    vol = (vol // 100) * 100
                    if vol < 100:
                        del _PULLBACK_SELL[sym]
                        continue
                    gw = gateway_execute(sym, "sell", cur, vol,
                                         reason="[量能分层] %s %s 缩量破位后离场" % (reason_tag, p.get("kind")),
                                         trigger_id=p.get("trig_id"),
                                         decision_source="rule",
                                         account_id=p.get("account_id") or T_MONITOR_ACCOUNT)
                    if gw.get("status") == "success":
                        print(f"[TMonitor] 量能分层卖出 {sym} {vol}股@{cur} ({reason_tag})")
                        self._after_sell(sym, p.get("account_id") or T_MONITOR_ACCOUNT, "custom_support_sell",
                                         "量能分层破位离场（%s）" % reason_tag)
                    del _PULLBACK_SELL[sym]
                except Exception as e:
                    print(f"[TMonitor] pullback settle err {sym}: {str(e)[:100]}")
                    del _PULLBACK_SELL[sym]
        except Exception as e:
            print(f"[TMonitor] _settle_pullback_sell 异常: {str(e)[:120]}")

    def _ambush_promote_pct(self) -> float:
        """**转正阈值**（浮盈 %）—— `WOLF_AMBUSH_PROMOTE_PCT`（**库内默认 0 = 关** ✓；pins 10 ✓）。"""
        try:
            return abs(float(os.getenv("WOLF_AMBUSH_PROMOTE_PCT", "0") or 0))
        except Exception:
            return 0.0

    def _ambush_trail_pct(self) -> float:
        """转正后的**移动止盈回撤 %**（自**最高价**算）—— `WOLF_AMBUSH_TRAIL_PCT`（默认 10 ✓）。"""
        try:
            return abs(float(os.getenv("WOLF_AMBUSH_TRAIL_PCT", "10") or 10))
        except Exception:
            return 10.0

    def _quote_now(self, symbols):
        """**动态取行情** ✓（账本 §9.249）—— 永远读 `t_data_sources` 的**模块属性** ✓。

        为什么 ✗：本模块顶部是 `from app.services.t_data_sources import fetch_tencent_quote` ✗
          ⇒ **from-import 绑走原对象** ⇒ 回测后来替换模块属性**对它无效** ✗
          ⇒ 沙箱/回测内无网络 ⇒ 行情空 ⇒ `_cur = 0` ⇒ 纪律**静默失效** ✗（第五次同类坑 ✓）
        修法：**每次调用时**去模块属性上取 ✓ ⇒ 谁替换都立刻生效 ✓（与"补丁广播"双保险 ✓）
        """
        try:
            import app.services.t_data_sources as _tds
            # ⚠️ 2026-09-30 全局统一（账本 §9.312）：走**唯一入口** ✓
            #   原来直接调 `fetch_tencent_quote` ✗ ⇒ 读键方式与其它调用点不一致 ✗
            #   ⇒ 回测替身下可能拿到空 ⇒ `_cur=0` ⇒ **静默跳过** ✗（加仓就是这么没的 ✗）
            _one = getattr(_tds, "fetch_quote_one", None)
            if _one is not None:
                _syms = symbols if isinstance(symbols, (list, tuple, set)) else [symbols]
                _out = {}
                for _s1 in _syms:
                    _v1 = _one(_s1) or {}
                    if _v1:
                        _out[_s1] = _v1
                        try:
                            _nn = _tds._normalize_symbol(_s1)
                            _out.setdefault(_nn, _v1)
                        except Exception:
                            pass
                return _out
            fn = getattr(_tds, "fetch_tencent_quote", None) or fetch_tencent_quote
        except Exception:
            fn = fetch_tencent_quote
        try:
            return fn(symbols) or {}
        except Exception:
            return {}

    def _promote_on_newhigh(self) -> bool:
        """转正是否也接受「**碰新高**」（他的口径 ✓）—— `WOLF_AMBUSH_PROMOTE_NEWHIGH`（默认 0 = 关 ✓）。"""
        return str(os.getenv("WOLF_AMBUSH_PROMOTE_NEWHIGH", "0")).strip().lower() in ("1", "true", "yes", "on")

    def _no_newhigh_exit_days(self) -> int:
        """**13 日不碰新高 ⇒ 离场**（他原话后半截 ✓）—— `WOLF_AMBUSH_NO_NEWHIGH_EXIT_DAYS`（默认 0 = 关 ✓）。"""
        try:
            return max(0, int(float(os.getenv("WOLF_AMBUSH_NO_NEWHIGH_EXIT_DAYS", "0") or 0)))
        except Exception:
            return 0

    def _prior_high_before_entry(self, sym: str, entry: str, n: int = 20) -> float:
        """**买入前 n 个交易日的最高价**（"新高"的参照 ✓）—— 用监控同源日线 ✓。"""
        try:
            # ⚠️ 2026-09-26（账本 §9.241）：这次调用**必须**也在 try 里 ✗
            #   `_daily_dated` 在缓存未命中时会走**回退取数** ⇒ import 到需要 **PySide6** 的模块 ✗
            #   ⇒ 上一版我只包了纪律主体里那一次 ✗，而**本函数**（碰新高判定 ✓）里这一次**漏了** ✗
            #   ⇒ 依然整个纪律抛异常 ✓（QQ 告警抓到了 ✓ —— 告警链首次真实立功 ✓）
            try:
                _bd = self._daily_dated(sym, 120) or []
            except Exception:
                _bd = []
            # ⚠️ 2026-09-26（账本 §9.265）：**「新高」必须相对「买入之后」** ✓
            #   他原话：「我说一下我用的 **买入有时间** 然后 **13 日内需要碰新高**或者新高。
            #          否则这个票呆的意义就不大，**证明自己的买入逻辑和时间**有问题…」✓
            #   旧写法＝**建仓日之前** 20 根的最高价 ✗ ⇒ 而我们的入场是**突破型**
            #     ⇒ 买入时价格**已在其上** ⇒ 「碰新高」买入即成立 ⇒ 该判据**空转** ✗
            #     （实测 4 例中 3 例：002156 39.54>38.72 ✓／603660 11.44>11.24 ✓／603690 33.41>32.88 ✓）
            #   新写法＝**建仓日（含）当根的最高价** ✓（=「买入后创新高」✓）
            _after = str(os.getenv("WOLF_NEWHIGH_AFTER_ENTRY", "0")).strip().lower() in ("1", "true", "yes", "on")
            if _after:
                for _b in _bd:
                    _d8 = str(_b.get("date") or _b.get("trade_date") or "").replace("-", "")
                    _h = _b.get("high") or _b.get("close")
                    if _d8 == str(entry) and _h:
                        return float(_h)
                return 0.0
            _days = []
            for _b in _bd:
                _d8 = str(_b.get("date") or _b.get("trade_date") or "").replace("-", "")
                _h = _b.get("high")
                if _d8 and _d8 < str(entry) and _h:
                    _days.append((_d8, float(_h)))
            if not _days:
                return 0.0
            _days.sort()
            return max(h for _, h in _days[-n:])
        except Exception:
            return 0.0

    def _record_promotion(self, sym: str, day: str, px: float) -> None:
        """**记录转正日与价**（供"加仓=B"用 ✓）—— 落在 `DATA_DIR/ambush_promoted.json` ✓。"""
        try:
            import json as _js2
            # ⚠️ 账本 §9.244：必须落在**运行根** ✗ —— `DATA_DIR` 是**按日**目录 ✓，
            #   写在里面 ⇒ 每天一个新文件 ⇒ 3 日窗口**形同虚设** ✗（实测每天重记一次 ✓）
            _D2 = os.path.dirname(os.environ.get("DATA_DIR", "/app/data")) or "/app/data"
            _p2 = os.path.join(_D2, "ambush_promoted.json")
            _cur2 = {}
            if os.path.exists(_p2):
                with open(_p2, encoding="utf-8") as _f3:
                    _cur2 = _js2.load(_f3) or {}
            if sym not in _cur2:
                _cur2[sym] = {"pday": str(day), "ppx": float(px or 0), "added": False}
                with open(_p2, "w", encoding="utf-8") as _f4:
                    _js2.dump(_cur2, _f4, ensure_ascii=False)
        except Exception:
            pass

    def _is_ambush_promoted(self, acct: str, sym: str) -> bool:
        """本仓是否已**转正**（＝最高价曾达 成本×(1+阈值%) ✓）。

        2026-09-26 用户「把"转正"补上」✓（账本 §9.220）——
        语料：「**13 日内需要碰新高**…否则这个票呆的意义就不大」✓（碰上去了 ⇒ 逻辑成立 ✓）；
              且 `tranche_ladder.CONFIRM_STAGE_AMBUSH=("缩量止跌","结构到位")` ✓
              ⇒ 到「**突破/站稳**」就不再是埋伏档 ✓
        **无需新状态** ✓：`paper_positions.highest_price` 就是"买入后最高价" ✓
        """
        _pct = self._ambush_promote_pct()
        if _pct <= 0:
            return False
        try:
            from sqlalchemy import text as _tp
            from app.database import SessionLocal
            with SessionLocal() as _sp:
                _r = _sp.execute(_tp(
                    "SELECT COALESCE(highest_price,0), COALESCE(avg_price,0) FROM paper_positions "
                    "WHERE account_id=:a AND symbol=:s AND COALESCE(volume,0)>0"),
                    {"a": acct, "s": sym}).fetchone()
            if not _r:
                return False
            _hi, _cost = float(_r[0] or 0), float(_r[1] or 0)
            return bool(_cost > 0 and _hi >= _cost * (1 + _pct / 100.0))
        except Exception:
            return False

    def _ambush_warn_trim(self) -> None:
        """**指数层预警 ⇒ 埋伏仓减到防御档**（账本 §9.205 ✓）。

        语料：「**趁机在周2前减仓**」✓「明天有冲高**减到 70%**」✓「**等指数企稳**了再打回」✓
        量化（21 个月 + 样本外分段 ✓）：无预警 **+58.58%／回撤 −21.6%** ✗
          ⇒ **预警 + 防御档 5 只：+75.93%／回撤 −7.5%** ✓✓（2026 动荡段 +35.12%／−7.9% ✓）
        开关：`WOLF_AMBUSH_WARN_TRIM`（**库内默认 0 = 关** ✓）＋ `WOLF_AMBUSH_WARN_DEF_CAP`（默认 5 ✓）
        口径：**每日最多一次** ✓；只动**埋伏仓** ✓；**砍浮亏最深者** ✓（与量化一致 ✓，已标注 ✓）
        """
        if str(os.getenv("WOLF_AMBUSH_WARN_TRIM", "0")).strip().lower() not in ("1", "true", "yes", "on"):
            return
        try:
            _cap = int(float(os.getenv("WOLF_AMBUSH_WARN_DEF_CAP", "5") or 5))
        except Exception:
            _cap = 5
        if _cap <= 0:
            return
        _today = datetime.now().strftime("%Y%m%d")
        if getattr(self, "_amb_trim_day", None) == _today:
            return
        try:
            if not ambush_warn_active():
                return
            self._amb_trim_day = _today
            from sqlalchemy import text as _tt
            from app.database import SessionLocal
            from app.services.t_gateway import gateway_execute
            from app.services.t_data_sources import fetch_tencent_quote
            # 2026-09-26 用户「**为什么只有埋伏仓减仓呢，我们不是为了躲避大跌吗**」✓
            #   ⇒ **作用面改为"全账户"**（`WOLF_WARN_TRIM_SCOPE='account'` ✓；库内默认 `ambush` = 现状 ✓）
            #   依据：①我们 8 个最差日**全是普跌日** ✓ ⇒ 要躲的是**全账户的大跌** ✓
            #        ②他原话「明天有冲高**减到 70%**」（2026-03-05）✓ 讲的是**组合层总仓位** ✓
            _scope = str(os.getenv("WOLF_WARN_TRIM_SCOPE", "ambush") or "ambush").strip().lower()
            with SessionLocal() as _ss:
                if _scope == "account":
                    rows = _ss.execute(_tt(
                        "SELECT p.symbol, p.volume, p.avg_price FROM paper_positions p "
                        "WHERE p.account_id=:a AND COALESCE(p.volume,0)>0"),
                        {"a": T_MONITOR_ACCOUNT}).fetchall()
                else:
                    rows = _ss.execute(_tt(
                        "SELECT p.symbol, p.volume, p.avg_price FROM paper_positions p "
                        "WHERE p.account_id=:a AND COALESCE(p.volume,0)>0 AND EXISTS ("
                        " SELECT 1 FROM paper_trades t WHERE t.account_id=:a AND t.symbol=p.symbol "
                        " AND COALESCE(t.voided,0)=0 AND t.direction LIKE '买%' "
                        " AND COALESCE(t.reason,'') LIKE :p)"), {"a": T_MONITOR_ACCOUNT, "p": "%wolf_ambush_buy%"}).fetchall()
            # ── **比例口径**（用户在 §9.222 的观察：只数管不住仓位 ✗）──────────────────
            #   「预警 ⇒ 总仓位（市值/权益）压到 X%」✓ —— 贴他原话「明天有冲高**减到 70%**」✓
            #   量化（长样本 ＋ **样本外分割** ✓，均带"转正 10%/回撤 10%"✓）：
            #     · 只数口径（防御档 5 只，现状）⇒ +152.10%／回撤 **−5.3%**（前段 +46.78%/−5.3%、后段 +63.41%/−5.5%）
            #     · **比例口径 40%** ⇒ **+541.14%／−9.6%** ✓✓（前段 **+141.44%/−8.5%**、后段 **+140.10%/−10.8%** ✓）
            #     比例 50% ⇒ +434.32%/−14.4%（后段 −14.5% ✗）；比例 60% ⇒ +299.87%/−12.9%
            #   ⇒ **比例 40% 两段都稳且收益约 3 倍** ✓（代价：绝对回撤略大 ✗）
            try:
                _maxpct = float(os.getenv("WOLF_WARN_TRIM_MAX_PCT", "0") or 0)
            except Exception:
                _maxpct = 0.0
            _mv = 0.0
            if _maxpct > 0:
                try:
                    from sqlalchemy import text as _tm7
                    from app.database import SessionLocal as _SL7
                    with _SL7() as _s7:
                        _mv = float(_s7.execute(_tm7(
                            "SELECT COALESCE(SUM(volume*avg_price),0) FROM paper_positions "
                            "WHERE account_id=:a AND COALESCE(volume,0)>0"),
                            {"a": T_MONITOR_ACCOUNT}).scalar() or 0)
                except Exception:
                    _mv = 0.0
            if len(rows) <= _cap and not (_maxpct > 0 and _mv > 0):
                print("[TMonitor] 预警降档：持仓 %d 只 ≤ 防御档 %d ⇒ 无需减仓（作用面=%s ✓）"
                      % (len(rows), _cap, _scope), flush=True)
                return
            if _maxpct > 0 and _mv > 0:
                print("[TMonitor] 预警降档(比例口径)：持仓成本 %.0f ／ 阈值 %.0f%% ⇒ 将按比例减仓 ✓"
                      % (_mv, _maxpct), flush=True)
            _qs = [_normalize_symbol(r[0]) for r in rows]
            _q = fetch_tencent_quote([_normalize_symbol(_s) for _s in (_qs or [])]) or {}
            rank = []
            for sym, vol, avg in rows:
                q = _q.get(_normalize_symbol(sym)) or {}
                cur = float(q.get("current") or 0)
                cost = float(avg or 0)
                if cur > 0 and cost > 0:
                    rank.append((cur / cost, sym, int(vol), cur))
            rank.sort()                                   # 浮亏最深在前 ✓
            # 目标：**只数 ≤ 防御档** ✓ ∧（若开了比例口径）**剩余市值 ≤ 权益×X%** ✓
            _keep_n = max(0, len(rank) - _cap)
            if _maxpct > 0 and _mv > 0:
                try:
                    from sqlalchemy import text as _te7
                    from app.database import SessionLocal as _SE7
                    with _SE7() as _se7:
                        _cash7 = float(_se7.execute(_te7(
                            "SELECT COALESCE(available_cash,0) FROM paper_account_info WHERE account_id=:a"),
                            {"a": T_MONITOR_ACCOUNT}).scalar() or 0)
                except Exception:
                    _cash7 = 0.0
                _eq7 = _cash7 + _mv
                _target = _eq7 * _maxpct / 100.0
                _rem = _mv
                _k7 = 0
                # ⚠️ 2026-09-30 修（账本 §9.292）：原来这里是 **或** 关系 ✗
                #   ⇒ 只要「只数 ≤ 防御档」成立就 break ✗ ⇒ **按市值比例该减的一分没减** ✗
                #   实测：算过 92 次、每次「需减 0 只」✗（持仓 8 只 ≤ 上限 8 ✓）
                #   现在：**比例口径只以市值为终止条件** ✓（只数档是另一条独立约束 ✓）
                for _r, _sym7, _vol7, _cur7 in rank:
                    if str(os.getenv("WOLF_WARN_TRIM_RATIO_STANDALONE", "1")).strip().lower() in ("1", "true", "yes", "on"):
                        if _rem <= _target:
                            break
                    elif _rem <= _target or (len(rank) - _k7) <= _cap:
                        break
                    _rem -= _vol7 * _cur7
                    _k7 += 1
                _keep_n = max(_keep_n, _k7)               # 取两者中**更严**的 ✓
                print("[TMonitor] 预警降档(比例)：权益 %.0f ⇒ 目标市值 %.0f；按最弱顺序需减 %d 只 ✓"
                      % (_eq7, _target, _k7), flush=True)
            for _r, sym, vol, cur in rank[:_keep_n]:
                gw = gateway_execute(sym, "sell", cur, vol,
                                     reason="预警降档（指数层预警 ⇒ %s减到防御档）"
                                            % ("全账户" if _scope == "account" else "埋伏仓"),
                                     decision_source="risk", account_id=T_MONITOR_ACCOUNT)
                print("[TMonitor] 预警降档 %s 卖 %d股@%.2f: %s" % (sym, vol, cur, gw.get("status")), flush=True)
        except Exception as _eT:
            print("[TMonitor] 预警降档异常: %s" % str(_eT)[:90], flush=True)

    def _settle_tsell_pending(self) -> None:
        """撤销式 T出 结算(2026-09-07): 对 pending 的 high_sell 观察——
        期间放量(vol>1.3×前均量)创新高(close>段高) → 撤销T出(继续持有等新高后新确认);
        无新高且超 TSELL_DELAY_S → 执行卖出; 14:45 后不强制(day_end 已降级, 允许跨日)。"""
        if not _TSELL_PENDING:
            return
        import time as _tm
        from app.services.t_gateway import gateway_execute
        from app.services.t_data_sources import fetch_minute_bars, fetch_tencent_quote
        today8 = datetime.now().strftime("%Y%m%d")   # 归一成 8 位日期，与 _bar_date8 同口径
        for sym in list(_TSELL_PENDING.keys()):
            p = _TSELL_PENDING[sym]
            try:
                qs = _normalize_symbol(sym)
                bars = fetch_minute_bars(qs, freq="m5", count=60) or []
                # 当日 bar 过滤：不依赖分隔符（生产 12 位 YYYYMMDDHHMM / 回测 '2026-09-17 10:35:00'）
                tb = [b for b in bars if _bar_date8(b.get("time") or b.get("trade_time")) == today8]
                if not tb:
                    continue
                last = tb[-1]
                lc = float(last.get("close") or 0); lv = float(last.get("vol") or 0)
                dec = _tsell_undo_decide(p["hi"], p["base"], lc, lv, _tm.time() - p["ts"])
                q = (fetch_tencent_quote([_normalize_symbol(qs)]) or {}).get(qs) or {}
                cur = float(q.get("current") or 0)
                if dec == "undo":
                    t_db.update_trigger_status(p["trig_id"], "cancelled",
                                               reason=f"放量过前高({lc}>{p['hi']})→撤销本次T出(主升未完, 继续持有)")
                    print(f"[TMonitor] T出撤销 {sym} (放量过前高 {lc} > hi {p['hi']})")
                    del _TSELL_PENDING[sym]
                elif dec == "sell":
                    gw = gateway_execute(sym, "sell", cur if cur > 0 else p.get("last_price", cur),
                                         int(p["volume"]), reason="确认制T出(撤销式延迟无放量新高)",
                                         decision_source="ai_led", account_id=p.get("account_id", T_MONITOR_ACCOUNT),
                                         # 2026-09-26 修（账本 §9.198）：**必须传 trigger_id** ✗
                                         #   病灶：本调用原先不传 ⇒ 网关 `_turnover_kind()` 取不到腿型 ⇒
                                         #   **埋伏仓豁免名单匹配不到 ⇒ 放行卖出** ✗（实测 SH603660 01-09
                                         #   被"确认制T出"卖掉 300 股 ✗，而条件腿那条路已正确 `blocked` ✓）
                                         trigger_id=p.get("trig_id"))
                    ok = gw.get("status") == "success"
                    t_db.update_trigger_status(p["trig_id"], "executed" if ok else "blocked",
                                               reason=f"确认制T出(延迟确认): {gw.get('status')} | {str(gw.get('reason') or '')[:80]}")
                    print(f"[TMonitor] T出执行(延迟无新高) {sym} {p['volume']}股@{cur}: {gw.get('status')}")
                    del _TSELL_PENDING[sym]
            except Exception as ex:
                print(f"[TMonitor] settle_tsell err {sym}: {str(ex)[:100]}")

    def _check_wolf_t_rules(self) -> None:
        """做T规则(向狼大看齐): 对做T宇宙标的用实时quote算正T/倒T, 命中写 t_triggers(当日去抖)。"""
        try:
            from app.services.wolf_t_rules import zheng_t_buy_quote, dao_t_sell_quote
            conds = t_db.list_active_conditions(account_id=T_MONITOR_ACCOUNT)
            syms = sorted({c.get('symbol') for c in conds if _is_wolf_t_condition(c)})
            if not syms:
                return
            quotes = fetch_tencent_quote([_normalize_symbol(s) for s in syms])
            today = datetime.now().strftime('%Y%m%d')
            for sym in syms:
                q = quotes.get(_normalize_symbol(sym))
                if not q:
                    continue
                prev = self._prev_daily(sym, 5)
                if not prev:
                    continue
                b, rb = zheng_t_buy_quote(q, prev)
                d, rd = dao_t_sell_quote(q, prev)
                if b and (sym, 'wolf_zheng_t_buy', today) not in self._wolf_done:
                    # ── 不追高：开盘/平稳时段的"冲上去"腿 → **不当日丢弃**，延后到 14:00–14:30 重评 ──
                    #   语料 2025-04-15 条件6「**想追进去的**…在下午 2.00-2.30 这个时间段进行回补」；
                    #   用户 2026-09-19 拍板 C（A+B）：既要"延后回补"、又把门槛收紧为
                    #   「拉升≥2% **且** 分位≥85」。做法：不写触发、**也不加 _wolf_done** ⇒ 后续 round
                    #   （尤其 14:00–14:30）会重新评估；届时 verdict 在窗口内直接放行 ⇒ 腿得以"晚点追"。
                    _defer_nc = False
                    try:
                        from app.services import wolf_no_chase as _ncz
                        if _ncz.enabled():
                            _qc = float(q.get('current') or 0)
                            _ql = float(q.get('low') or 0)
                            _qh = float(q.get('high') or 0)
                            _rz = round((_qc / _ql - 1.0) * 100, 3) if (_qc > 0 and _ql > 0) else None
                            _qz = round((_qc - _ql) / (_qh - _ql) * 100, 2) if (_qh > _ql > 0 and _qc > 0) else None
                            _okz, _whyz = _ncz.verdict(_qc, round(_qc * 0.999, 3), rise_pct=_rz, quantile=_qz)
                            if not _okz and not _ncz.in_pm_window(datetime.now().strftime('%H%M')):
                                _defer_nc = True
                                if (sym, today) not in self._nochase_deferred:
                                    self._nochase_deferred.add((sym, today))
                                    print("[TMonitor] 不追高→延后到 14:00–14:30 重评 %s: %s"
                                          % (sym, str(_whyz)[:90]), flush=True)
                    except Exception as _eN1:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line832", _eN1)
                        pass
                    # ── D2) 狼大做T时间窗（2026-09-21 用户拍板："把 D2 也补上"）──
                    #   语料 2025-04-15 条件2「**当日只做上午 9.45-10.00 下午 2.00-2.30 这两个时间段的
                    #   交易，尽量避免开盘直接买卖和平稳时间的来回T**」。
                    #   ⚠️ 覆盖范围（`WOLF_TW_KINDS`）：原白名单只有 `low_buy,custom_prevlow`，
                    #      而 253/254 低吸腿实测**一笔都没成交**（t1–t5 共 143 笔买成交 100% 是
                    #      `wolf_zheng_t_buy` + `trend_break_buy`）⇒ 只开 `WOLF_TRADE_WINDOW=1` 在本臂
                    #      **等于没开**（第 N 次同族"静默失效"）。要真生效必须显式把本腿型写进
                    #      `WOLF_TW_KINDS`（`jobs/bt_env_pins.sh` 已写死并附影响面）。
                    #   做法与不追高一致：不写触发、**也不加 `_wolf_done`** ⇒ 后续 round（窗口内）重新评估。
                    _defer_tw = False
                    try:
                        from app.services import wolf_trade_window as _twz
                        if _twz.enabled() and _twz.applies_to('wolf_zheng_t_buy'):
                            _okw, _whyw = _twz.allowed()
                            if not _okw:
                                _defer_tw = True
                                if (sym, today) not in self._tw_deferred:
                                    self._tw_deferred.add((sym, today))
                                    print("[TMonitor] 狼大做T时间窗外→不发正T买腿 %s: %s"
                                          % (sym, str(_whyw)[:100]), flush=True)
                    except Exception as _eN2:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line854", _eN2)
                        pass
                    # ── D1) 开盘「快速拉升不追」（2026-09-21 用户拍板："把 D1 也补上"）──
                    #   语料 2025-04-15 条件6「如果当日开盘高开快速拉升，或者低开快速拉升想追进去的，
                    #   在下午 2.00-2.30 这个时间段进行回补」。
                    #   ⚠️ 为什么原不追高闸拦不住本腿：它的参照物是**建议买价**，而本腿触发价=现价
                    #      ⇒ premium 恒 0 ⇒ 天然豁免（实测 0112 苏州科达距开盘 +3.9%/距昨收 +6.4% 照买）。
                    #      D1 换参照物为**当日开盘价**。
                    _defer_oc = False
                    try:
                        from app.services import wolf_no_chase as _nco
                        if _nco.open_enabled():
                            _okq, _whyq = _nco.open_verdict(float(q.get('current') or 0),
                                                            float(q.get('open') or 0),
                                                            kind='wolf_zheng_t_buy')
                            if not _okq:
                                _defer_oc = True
                                if (sym, today) not in self._oc_deferred:
                                    self._oc_deferred.add((sym, today))
                                    print("[TMonitor] 开盘快速拉升不追 %s: %s" % (sym, str(_whyq)[:110]),
                                          flush=True)
                    except Exception as _eN3:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line875", _eN3)
                        pass
                    # ── B/C) 买入时点闸（2026-09-22 用户拍板 "A+B+C"）──
                    #   B `WOLF_FALLING_GATE`：现价 < 当日 VWAP ∧ 前 3 根 5min 连跌 ⇒ 不在下跌中买
                    #     （语料 2025-06-05「急杀可以买，缓跌不买」；实测该族 246 笔单笔 −108、跨臂 9/10 更差）
                    #   C `WOLF_WEAK_DEFER`：当日为跌 ∧ 现价 < VWAP 且未到 `WOLF_WEAK_DEFER_HM`(1445)
                    #     ⇒ 延后到尾盘重评（语料 2025-05-23「尾盘能回来就尾盘买 急什么」）
                    #   起点个案：环旭电子 601231 2026-03-03 09:40 买 47.39（当日最高 48.50）⇒ 收跌停 43.58。
                    #   做法与不追高一致：不写触发、**也不加 `_wolf_done`** ⇒ 后续 round 重新评估。
                    _defer_fall = False
                    try:
                        try:
                            import intraday_crush as _icr2
                        except ImportError:
                            from main_line import intraday_crush as _icr2
                        if _icr2.falling_on() or _icr2.weak_defer_on():
                            _now2 = datetime.now()
                            _okf, _whyf = _icr2.timing_verdict(
                                sym, _now2.strftime("%Y%m%d"), _now2.strftime("%H%M"),
                                pre_close=float(q.get('pre_close') or 0) or None)
                            if not _okf:
                                _defer_fall = True
                                if (sym, today) not in self._fall_deferred:
                                    self._fall_deferred.add((sym, today))
                                    print("[TMonitor] 买入时点闸→本次不执行 %s: %s" % (sym, str(_whyf)[:110]), flush=True)
                    except Exception as _eN4:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line900", _eN4)
                        pass
                    # ── C) 买单必须挂在「低位的线」上（狼大 2026-04-10「找低位的线挂进去…挂远一点」）──
                    #   语料：黄金分割/均线；自设：线集合与"挂远一点"的最小间距（见 wolf_buy_line 模块头）。
                    #   碰不到线 ⇒ **不发单**（挂单等待），也不记 done ⇒ 后续 round 继续评估。
                    _defer_line = False
                    _line_bid, _line_name = None, None
                    try:
                        from app.services import wolf_buy_line as _wl
                        if _wl.enabled() and _wl.applies(_wl.scope(), self._held_today(sym)):
                            # 数源（2026-09-20 实测）：`_prev_daily` 在回放里常常只有 0–1 天
                            #   ⇒ MA10/MA20 恒缺（线集合退化成"昨低"）。改用 `_daily_dated(sym, 40)`
                            #   （带日期 + 生产有 t_build 实时兜底），拿不到再退回 _prev_daily。
                            _lb = self._line_bars_cache.get((sym, today), "MISS")
                            if _lb == "MISS":
                                _lb = None
                                # 数源优先级（2026-09-20 实测）：
                                #   ⓪ `wolf_buy_line.bars_sqlite` —— **回测专用**：直接读 `data/_bt_full/bars.sqlite`
                                #      （回放的权威日线源，强制 trade_date < 当日）。上面三个源实测都不行（见模块注释）。
                                #   ① `_fetch_daily_tencent_dated` —— 生产里是腾讯日线兜底；
                                #   ② `_daily_dated` —— 回放里实测**恒定停在 seed cut（20251231）**，
                                #      与回放日无关（MA10/MA20 会算错，只用它兜底）；
                                #   ③ `_prev_daily` —— 常常 0–1 天。
                                for _get in (lambda: _wl.bars_sqlite(sym, today, 40),
                                             lambda: _fetch_daily_tencent_dated(sym, 40),
                                             lambda: self._daily_dated(sym, 40),
                                             lambda: self._prev_daily(sym, 25)):
                                    try:
                                        _cand = _get()
                                    except Exception:
                                        _cand = None
                                    if _cand and len(_cand) >= 2:
                                        _lb = _cand
                                        break
                                self._line_bars_cache[(sym, today)] = _lb
                            _r = _wl.resolve(_lb or [], sym, today, q)
                            if _r.get("fire"):
                                _line_bid, _line_name = _r.get("price"), _r.get("line")
                                if (sym, today) not in self._line_deferred:
                                    self._line_deferred.add((sym, today))
                                    print("[TMonitor] 挂低位线成交 %s: %s @%.3f" % (sym, _r.get("why"), _line_bid or 0), flush=True)
                            else:
                                _defer_line = True
                                if (sym, today) not in self._line_deferred:
                                    self._line_deferred.add((sym, today))
                                    print("[TMonitor] 挂低位线等待 %s: %s" % (sym, str(_r.get("why"))[:110]), flush=True)
                    except Exception as _eN5:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line946", _eN5)
                        pass
                    # ── D) 位置高 ∧ 前一日量能放大 ⇒ 不买（2026-09-20；语料「高位看量价」「别山顶接」
                    #   「冲上去一定不能追」）。离线（y26 78 轮 / draymar 63 轮）：拦掉的轮次实现盈亏
                    #   +12,704 / +10,373，拦到大亏 7/15、6/11，误伤大赚 1/11、0/10 ⇒ 见
                    #   backend/app/services/wolf_high_pos_vol.py 模块头与台账。
                    #   只作用**新开底仓**（WOLF_HPV_SCOPE=new 默认）⇒ 持仓加仓/做T买回不受影响。
                    _defer_hpv = False
                    _hpv_why = ""
                    try:
                        from app.services import wolf_high_pos_vol as _hpv
                        if _hpv.enabled() and _hpv.applies(self._held_today(sym)):
                            _hb = self._hpv_bars_cache.get((sym, today), "MISS")
                            if _hb == "MISS":
                                _hb = _hpv.bars_asof(sym, today, 30)
                                self._hpv_bars_cache[(sym, today)] = _hb
                            _block, _hpv_why = _hpv.verdict(_hb, held=self._held_today(sym),
                                                           day=today, symbol=sym)
                            if _block:
                                _defer_hpv = True
                                if (sym, today) not in self._hpv_deferred:
                                    self._hpv_deferred.add((sym, today))
                    except Exception as _eN6:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line968", _eN6)
                        pass
                    # ── E) 无底仓 ⇒ 不发正T买腿（2026-09-21；语料 2026-08-25「我今天没抄底 没有资格T」
                    #   「先有低吸仓位才有资格做T」＋2025-04-15 成文流程「尽量不要把做T的仓位变加仓」）。
                    #   实测（drabj13 全窗）：无底仓正T买腿 89 条 T+3 −2.66%(t=−4.20)、有底仓 66 条 +0.71%；
                    #   且无底仓腿在网关侧会退化成"重新建仓"（首开非底仓 → 转人工/超时取消，2026-03-24 即此）。
                    _defer_nobase = False
                    try:
                        from app.services import wolf_t_base_gate as _tbg
                        _okb, _whyb = _tbg.allow(self._held_today(sym))
                        if not _okb:
                            _defer_nobase = True
                            if (sym, today) not in self._nobase_deferred:
                                self._nobase_deferred.add((sym, today))
                                print("[TMonitor] 无底仓→不发正T买腿 %s: %s" % (sym, str(_whyb)[:110]), flush=True)
                    except Exception as _eN7:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line983", _eN7)
                        pass
                    if (not _defer_nc and not _defer_line and not _defer_hpv
                            and not _defer_nobase and not _defer_tw and not _defer_oc
                            and not _defer_fall):
                        try:
                            from app.services.wolf_t_rules import t_cycle_pnl
                            tb=self._today_bars(sym)
                            cyc=t_cycle_pnl(tb, 2.5) if tb else None
                            if cyc:
                                if cyc.get('sell'):
                                    rb += ' | 正T买@%.2f→确认制T出@%.2f(+%.2f%%/日高+%.2f%%)' % (cyc['buy'], cyc['sell'], cyc['pnl'], cyc['pnl_dayhigh'])
                                else:
                                    rb += ' | 正T买@%.2f 未确认→黄线/持有至次日/周五减T仓(不强制日结)' % (cyc['buy'])
                                self._log_cycle(sym, cyc)
                                if cyc.get('confirm'):
                                    self._wolf_sold_today.add((sym, today))
                                    self._insert_wolf_trigger(sym, 'wolf_confirm_sell', q, '确认制T出(停量+二次不过前高)')
                        except Exception as _eN8:
                            # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                            from app.services import gate_alarm as _gaNote
                            _gaNote.note("t_monitor:line1001", _eN8)
                            pass
                        if _line_name:
                            rb += ' | 挂线=%s@%.3f（狼大 2026-04-10「低位的线挂进去」）' % (_wl.label(_line_name), _line_bid or 0)
                        self._insert_wolf_trigger(sym, 'wolf_zheng_t_buy', q, rb, bid=_line_bid, line=_line_name)
                        self._wolf_bought_today.add((sym, today))
                        self._wolf_done.add((sym, 'wolf_zheng_t_buy', today))
                if d and (sym, 'wolf_dao_t_sell', today) not in self._wolf_done:
                    self._insert_wolf_trigger(sym, 'wolf_dao_t_sell', q, rd)
                    self._wolf_done.add((sym, 'wolf_dao_t_sell', today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] wolf_t_rules检查异常: {e}")

    def _check_day_end_de_t(self) -> None:
        """当日正T买(狼大T+0) + 尾盘14:45后仍未确认T出 → 减T仓(保留底仓); 隔日反T/周五例外. """
        try:
            now=datetime.now()
            if not (now.hour==14 and now.minute>=45) and now.hour!=15: return
            today=now.strftime('%Y%m%d')
            for sym in [s for s,t in self._wolf_bought_today if t==today]:
                if (sym, today) in self._wolf_sold_today: continue
                if (sym,'wolf_day_end_de_t',today) in self._wolf_done: continue
                q=fetch_tencent_quote([_normalize_symbol(sym)]).get(_normalize_symbol(sym))
                if not q: continue
                self._insert_wolf_trigger(sym, 'wolf_day_end_de_t', q, '当日正T买+尾盘未确认→减T仓(保留底仓)')
                self._wolf_done.add((sym,'wolf_day_end_de_t',today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] day_end_de_t异常: {e}")

    def _check_defensive_t_reduce(self) -> None:
        """防御性减T(风险/结构驱动): wave只做T + (量能不足 或 滞涨) → 写 wolf_defensive_t_reduce 减T触发(08-27/09-01式)."""
        try:
            from app.services.wolf_t_rules import defensive_t_reduce_quote, defensive_t_reduce_sw
            conds = t_db.list_active_conditions(account_id=T_MONITOR_ACCOUNT)
            active = {c.get('symbol') for c in conds if _is_wolf_t_condition(c)}
            today = datetime.now().strftime('%Y%m%d')
            syms = sorted(set(active) | {s for s,t in self._wolf_bought_today if t==today})
            if not syms: return
            quotes = fetch_tencent_quote([_normalize_symbol(s) for s in syms])
            for sym in syms:
                q = quotes.get(_normalize_symbol(sym))
                if not q: continue
                prev = self._prev_daily(sym, 5)
                if not prev: continue
                ok, reason = defensive_t_reduce_quote(q, prev, wave_op='t_only')
                if ok and (sym,'wolf_defensive_t_reduce',today) not in self._wolf_done:
                    self._insert_wolf_trigger(sym,'wolf_defensive_t_reduce',q,reason)
                    self._wolf_done.add((sym,'wolf_defensive_t_reduce',today))
                # 个股申万行业级防御(行业近高而个股未跟, 泛化非科技) → 独立触发 wolf_defensive_t_reduce_index
                sym_hi = float(q.get('high') or 0)
                sym_hi_prev = max([p.get('high') for p in prev if p.get('high')], default=0)
                iok, ireason = defensive_t_reduce_sw(sym, sym_hi, sym_hi_prev, wave_op='t_only')
                if iok and (sym,'wolf_defensive_t_reduce_index',today) not in self._wolf_done:
                    self._insert_wolf_trigger(sym,'wolf_defensive_t_reduce_index',q,ireason)
                    self._wolf_done.add((sym,'wolf_defensive_t_reduce_index',today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] defensive_t_reduce异常: {e}")

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

    def _discipline_positions(self, accounts=None):
        """纪律类卖腿（板上减半 / 小赚兑现 / 周末避险 / 中轨全止盈）的持仓口径。

        **默认 = POS_ACCOUNT（stock）**；t 账户是测试账户、暂时不使用（用户 2026-09-14 拍板）。
        """
        accts = tuple(accounts or _position_accounts())
        out = []
        for a in accts:
            out += self._positions(a)
        return out

    def _check_board_half(self) -> None:
        """板上减半(狼大纪律②): 持仓当日触及/接近涨停(10%板>=9.5%, 20%板>=19.5%) 且 本轮浮盈>=3% -> 减半锁定.
        复用 trigger 管道写 wolf_board_half_sell(网关执行), 当日去抖."""
        try:
            from app.services.wolf_discipline import board_half
            import json as _j, datetime as _dt
            held = [p for p in self._discipline_positions() if float(p.get('volume') or 0) > 0]
            if not held:
                return
            acct_of = {_normalize_symbol(p['symbol']): p['account_id'] for p in held}
            xq_syms = sorted({_normalize_symbol(p.get('symbol')) for p in held})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq_syms or [])])
            qmap = {s: {'current': float((quotes.get(s) or {}).get('current', 0) or 0),
                        'pre_close': float((quotes.get(s) or {}).get('pre_close', 0) or 0)} for s in xq_syms}
            portfolio = {"positions": [{"symbol": _normalize_symbol(p.get('symbol')),
                                        "avg_cost": float(p.get('avg_price') or p.get('avg_cost') or 0),
                                        "volume": float(p.get('volume') or 0)} for p in held]}
            bh = board_half(_j.dumps(portfolio, ensure_ascii=False), _dt.datetime.now(), quotes=qmap)
            today = _dt.datetime.now().strftime('%Y%m%d')
            for s in bh.get('active_sells') or []:
                sym = s.get('symbol')
                if (sym, 'wolf_board_half_sell', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                self._insert_wolf_trigger(sym, 'wolf_board_half_sell', q, s.get('reason', '板上减半锁定'),
                                          account_id=acct_of.get(sym))
                self._wolf_done.add((sym, 'wolf_board_half_sell', today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] board_half异常: {e}")

    def _check_boll_mid_exit(self) -> None:
        """A10 BOLL 中轨「完全止盈」（2026-09-11，狼大 2025-05-13）。

        「顶部阶段…**全止盈的位置就放在日线BOLL中轨附近，放量跌破收盘完全止盈**」→ 三个前提：
        ① **放量**（当日量 ≥ 前一日量 × 阈值）② **收盘**确认（只在收盘确认窗内判，复用 _in_close_window）
        ③ **止盈**（要求浮盈>0；亏损侧交给既有六层止损，避免双杀）。
        命中写 `wolf_boll_mid_exit`；`WOLF_BOLL_MID_EXIT=1` 才生效（默认 0）。
        **执行量（2026-09-14 用户拍板，按他的策略）**：他说的是"**完全止盈**" → **底仓一起清**
        （`_floor_break_enabled('wolf_boll_mid_exit')`；`WOLF_FLOOR_BREAK=0` 可整体退回"只卖 T 仓"）。
        走网关 rule 通道**直连执行**（与止损同一模式，不经 AI 转一手），并把触发记进 t_triggers 供复盘。
        """
        try:
            from app.services.wolf_boll_levels import mid_break_sells, mid_exit_enabled
            if not mid_exit_enabled():
                return
            if not _in_close_window():
                return          # 他要求"收盘"确认
            from app.services.t_gateway import gateway_execute, get_sellable_ledger, base_floor_shares
            import datetime as _dt
            held = [p for p in self._discipline_positions() if float(p.get('volume') or 0) > 0]
            if not held:
                return
            acct_of = {_normalize_symbol(p['symbol']): p['account_id'] for p in held}
            xq_syms = sorted({_normalize_symbol(p.get('symbol')) for p in held})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq_syms or [])])
            qmap = {s: {'current': float((quotes.get(s) or {}).get('current', 0) or 0),
                        'high': float((quotes.get(s) or {}).get('high', 0) or 0),
                        'vol': float((quotes.get(s) or {}).get('vol', 0) or 0),
                        'pre_close': float((quotes.get(s) or {}).get('pre_close', 0) or 0)} for s in xq_syms}
            portfolio = {"positions": [{"symbol": _normalize_symbol(p.get('symbol')),
                                        "avg_cost": float(p.get('avg_price') or p.get('avg_cost') or 0),
                                        "volume": float(p.get('volume') or 0)} for p in held]}
            today = _dt.datetime.now().strftime('%Y%m%d')
            for sl in mid_break_sells(portfolio, quotes=qmap):
                sym = sl.get('symbol')
                if (sym, 'wolf_boll_mid_exit', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                cur = float(q.get('current') or 0)
                acct = acct_of.get(sym) or T_MONITOR_ACCOUNT
                reason = sl.get('reason', 'BOLL中轨跌破完全止盈')
                tid = self._insert_wolf_trigger(sym, 'wolf_boll_mid_exit', q, reason, account_id=acct)
                if cur <= 0:
                    continue
                try:
                    sellable = int(((get_sellable_ledger(account_id=acct).get(sym) or {}).get('sellable', 0)) or 0)
                except Exception:
                    sellable = 0
                # 动作强度分层（2026-09-14）：顶部阶段来自原代理 → 他"完全止盈"（清仓含底仓）；
                # 仅来自 G1 信号 → 他"减仓避一下"（减 T 仓的一半，不动底仓）。
                rr = float(sl.get('reduce_ratio') or 1.0)
                t_shop = max(sellable - base_floor_shares(acct, sym, volume=sellable), 0)
                if rr >= 1.0 and _floor_break_enabled('wolf_boll_mid_exit'):
                    vol = sellable                      # 完全止盈（含底仓）
                elif rr >= 1.0:
                    vol = t_shop                        # 完全止盈但关了底仓穿透 → 只卖 T 仓
                else:
                    frac = float(os.getenv("WOLF_TOP_SIGNAL_REDUCE", "0.5"))   # "避一下"=减 T 仓一半
                    vol = int(t_shop * max(min(frac, 1.0), 0.0))
                vol = (vol // 100) * 100
                if vol < 100:
                    self._wolf_done.add((sym, 'wolf_boll_mid_exit', today))
                    continue
                gw = gateway_execute(sym, 'sell', cur, vol, reason=reason, trigger_id=tid,
                                     decision_source='rule', account_id=acct)
                if gw.get('status') == 'success':
                    self._sold_this_round.add(sym)
                    print(f"[TMonitor] 中轨全止盈(底仓穿透) {sym} {vol}股@{cur} [{acct}]")
                else:
                    print(f"[TMonitor] 中轨全止盈被拒 {sym}: {str(gw.get('reason') or '')[:60]}")
                try:
                    t_db.update_trigger_status(tid, 'executed' if gw.get('status') == 'success' else 'blocked',
                                               reason=f"中轨全止盈 {vol}股@{cur} [{acct}]: {gw.get('status')}")
                except Exception as _eN9:
                    # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                    from app.services import gate_alarm as _gaNote
                    _gaNote.note("t_monitor:line1200", _eN9)
                    pass
                self._wolf_done.add((sym, 'wolf_boll_mid_exit', today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] boll_mid_exit异常: {e}", flush=True)

    def _check_boll_sell(self) -> None:
        """A10 BOLL 上轨减半锁利（2026-09-11）。

        狼大 2026-04-29「当出现 股价分时毫无理由地急速拉升，**触及上方大级别压力位(如BOLL上轨)时，
        逢高卖出部分底仓、锁定利润**」+ 2026-01-13「偏离太多 **先卖一半** 等回归BOLL轨内再接回」
        → 持仓**触及日线 BOLL(20,2) 上轨且浮盈>0** 时，写 wolf_boll_upper_sell（减半，复用 trigger 管道），
        当日去抖。`WOLF_BOLL_SELL=0` 关。
        """
        try:
            from app.services.wolf_boll_levels import active_sells, enabled as _boll_on
            if not _boll_on():
                return
            import json as _j, datetime as _dt
            pos_list = self._positions()
            held = [p for p in pos_list if float(p.get('volume') or 0) > 0]
            if not held:
                return
            xq_syms = sorted({_normalize_symbol(p.get('symbol')) for p in held})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq_syms or [])])
            qmap = {s: {'current': float((quotes.get(s) or {}).get('current', 0) or 0),
                        'high': float((quotes.get(s) or {}).get('high', 0) or 0),
                        'pre_close': float((quotes.get(s) or {}).get('pre_close', 0) or 0)} for s in xq_syms}
            portfolio = {"positions": [{"symbol": _normalize_symbol(p.get('symbol')),
                                        "avg_cost": float(p.get('avg_price') or p.get('avg_cost') or 0),
                                        "volume": float(p.get('volume') or 0)} for p in held]}
            r = active_sells(portfolio, quotes=qmap)
            today = _dt.datetime.now().strftime('%Y%m%d')
            for sl in r.get('active_sells') or []:
                sym = sl.get('symbol')
                if (sym, 'wolf_boll_upper_sell', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                self._insert_wolf_trigger(sym, 'wolf_boll_upper_sell', q, sl.get('reason', 'BOLL上轨减半锁利'))
                self._wolf_done.add((sym, 'wolf_boll_upper_sell', today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] boll_sell异常: {e}", flush=True)

    def _index_pct_today(self):
        """上证当日涨跌幅（%）：现价 vs 昨收。取不到 → None（G7 判定退化为 neutral，不猜）。"""
        try:
            q = fetch_tencent_quote([_normalize_symbol("sh000001")]).get("sh000001") or {}
            cur = float(q.get("current") or 0)
            pre = float(q.get("pre_close") or 0)
            if cur > 0 and pre > 0:
                return (cur / pre - 1.0) * 100.0
        except Exception as e:
            print(f"[TMonitor] 指数涨跌幅取数失败: {str(e)[:60]}")
        return None

    def _check_profit_take(self) -> None:
        """小赚兑现(P0-3, 2026-09-10): 持仓浮盈 >= 阈值 → 写 wolf_profit_take_sell 减仓(保留底仓)。

        规则实现在 wolf_discipline.profit_take（配置 DB `wolf_discipline_config` 的 profit_take 段，
        2026-09-14 用户拍板**开启**：他「正常收益就是 3-5 个点」2026-04-23 /「T+0 2个点我就够了」2025-04-03）。
        持仓口径 = **stock + t 两个账户**（t 是测试账户，纪律规则必须覆盖主账户）。
        减仓量 = 卖 T 仓的一半、保留底仓 —— 由卖出管道按 floor 推导，不在本函数里定死。
        """
        try:
            from app.services.wolf_discipline import profit_take
            from app.services import wolf_discipline as _WD
            import json as _j, datetime as _dt
            force = os.getenv("WOLF_PROFIT_TAKE", "").strip() in ("1", "true", "yes")
            pos_list = [p for p in self._discipline_positions() if float(p.get('volume') or 0) > 0]
            if not pos_list:
                return
            acct_of = {_normalize_symbol(p['symbol']): p['account_id'] for p in pos_list}
            xq_syms = sorted({_normalize_symbol(p.get('symbol')) for p in pos_list})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq_syms or [])])
            qmap = {s: {'current': float((quotes.get(s) or {}).get('current', 0) or 0),
                        'pre_close': float((quotes.get(s) or {}).get('pre_close', 0) or 0)} for s in xq_syms}
            portfolio = {"positions": [{"symbol": _normalize_symbol(p.get('symbol')),
                                        "avg_cost": float(p.get('avg_price') or p.get('avg_cost') or 0),
                                        "volume": float(p.get('volume') or 0)} for p in pos_list]}
            cfg = None
            if force:
                cfg = {"profit_take": {"enabled": True,
                                       "min_float_pct": float(os.getenv("WOLF_PROFIT_TAKE_PCT", "3.0")),
                                       "reduce_ratio": float(os.getenv("WOLF_PROFIT_TAKE_RATIO", "0.5"))}}
            # G7（2026-09-14）狼大 2025-01-23「大涨之日少买票，多卖票，大跌之日多买票 少卖票」
            #   · 大涨日 → 兑现门槛下调（多卖票）；· 大跌日 → 当日不新增兑现类卖腿（少卖票）。
            try:
                from app.services import wolf_day_rules as DR
                bias = DR.day_bias(self._index_pct_today())
                if not DR.allow_realize_sell(bias):
                    print(f"[TMonitor] G7 大跌日（{bias}）→ 当日不新增小赚兑现腿（保护性卖出照旧）")
                    return
                if cfg is None:
                    cfg = {"profit_take": dict((_WD._cfg().get("profit_take") or {}))}
                _pt = dict(cfg.get("profit_take") or {})
                _base = float(_pt.get("min_float_pct") or 3.0)
                _pt["min_float_pct"] = DR.tp_threshold(_base, bias)
                if _pt["min_float_pct"] != _base:
                    print(f"[TMonitor] G7 大涨日 → 小赚兑现门槛 {_base}% → {_pt['min_float_pct']}%（多卖票）")
                cfg["profit_take"] = _pt
            except Exception as _de:
                print(f"[TMonitor] G7 日型判定失败（按原口径执行）: {str(_de)[:60]}")
            pt = profit_take(_j.dumps(portfolio, ensure_ascii=False), _dt.datetime.now(), cfg=cfg, quotes=qmap)
            if not pt.get("enabled"):
                return
            today = _dt.datetime.now().strftime('%Y%m%d')
            for s in pt.get('active_sells') or []:
                sym = s.get('symbol')
                if (sym, 'wolf_profit_take_sell', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                self._insert_wolf_trigger(sym, 'wolf_profit_take_sell', q, s.get('reason', '小赚兑现'),
                                          account_id=acct_of.get(sym))
                self._wolf_done.add((sym, 'wolf_profit_take_sell', today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] profit_take异常: {e}")

    def _check_weekend_hedge(self) -> None:
        """G9 周末/长假前避险 **执行层**（狼大 2026-08-21 NGA 带时刻原话）。

        14:20「2点半 如果还是缩量 还是不拉升 我会先把这两天T进去的仓位出来一半 防止周末出利空
              这样周一再拿回来。出于仓位安全考虑 65%仓位过周末。」
        14:35「2点半过了 **我按刚才说的操作了**。」← 他**当场执行**（此前我们只写到提示层）

        判定由 jobs/wolf_weekend_hedge.py（14:31 调度）落 `wolf_weekend_hedge.json`；
        本函数只负责"把判定变成卖腿"：**卖出量 = T 仓的一半**（`_hedge_reduce_volume`，底仓不动），
        覆盖 stock + t 两个账户。开关 `WOLF_WH_EXEC=0` 可退回"只提示"。
        回补腿（他「周一再拿回来」/ 2025-09-24「避险逻辑结束后 是不是应该补回来」）**另行设计**。
        """
        try:
            if not _wh_exec_enabled():
                return
            from app.services import wolf_weekend_hedge as WH
            from app.services.t_gateway import gateway_execute, get_sellable_ledger, base_floor_shares
            now = datetime.now()
            today = now.strftime('%Y%m%d')
            state = WH.load() or {}
            _kind = str(state.get("kind") or "")
            if not _wh_should_execute(state, today, now.strftime('%H%M'), WH.cutoff_for(_kind)):
                return
            held = [p for p in self._discipline_positions() if float(p.get('volume') or 0) > 0]
            if not held:
                return
            xq_syms = sorted({_normalize_symbol(p['symbol']) for p in held})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq_syms or [])])
            for p in held:
                sym = _normalize_symbol(p['symbol'])
                acct = p['account_id']
                if (sym, 'wolf_weekend_hedge_sell', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                cur = float(q.get('current') or 0)
                if cur <= 0:
                    continue
                try:
                    sellable = int(((get_sellable_ledger(account_id=acct).get(sym) or {}).get('sellable', 0)) or 0)
                except Exception:
                    sellable = 0
                floor = base_floor_shares(acct, sym, volume=sellable)
                vol = _hedge_reduce_volume(sellable, floor)
                reason = ("[G9 周末避险] 14:30 仍缩量(量比%s)∧未拉升(指数%s%%) → T仓减半 %d 股（保留底仓；"
                          "狼大 2026-08-21「先把这两天T进去的仓位出来一半…周一再拿回来」）"
                          % (state.get('shrink_ratio'), state.get('idx_pct'), vol))
                tid = self._insert_wolf_trigger(sym, 'wolf_weekend_hedge_sell', q, reason, account_id=acct)
                self._wolf_done.add((sym, 'wolf_weekend_hedge_sell', today))
                if vol < 100:
                    continue
                gw = gateway_execute(sym, 'sell', cur, vol, reason=reason, trigger_id=tid,
                                     decision_source='rule', account_id=acct)
                print(f"[TMonitor] G9 周末避险减T半仓 {sym} {vol}股@{cur} [{acct}]: {gw.get('status')}")
                if gw.get('status') == 'success':
                    # C2b: 登记待回补额度 → 下一个交易日按"不追高/逻辑没变"补回（狼大 2026-08-21「周一再拿回来」）
                    try:
                        from app.services import wolf_hedge_refill as _RF
                        _RF.record_sell(sym, cur, vol, acct, today, kind=_kind)
                    except Exception as _re:
                        print(f"[TMonitor] 回补登记失败 {sym}: {_re}")
                try:
                    t_db.update_trigger_status(tid, 'executed' if gw.get('status') == 'success' else 'blocked',
                                               reason=f"G9 周末避险 {vol}股@{cur} [{acct}]: {gw.get('status')}")
                except Exception as _eN10:
                    # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                    from app.services import gate_alarm as _gaNote
                    _gaNote.note("t_monitor:line1383", _eN10)
                    pass
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] weekend_hedge异常: {e}")

    def _check_hedge_refill(self) -> None:
        """C2b 避险**回补腿**：把 G9 周末/长假避险卖出的那一份，在**下一个交易日**补回来。

        狼大 2026-08-21「…这样**周一再拿回来**」＋「等周一确认安全再说 **万一低开 那就等于做了个反T**
        万一高开 那**没吃到就没吃到了 不纠结**」＋ 2025-09-24「避险逻辑结束后 是不是应该补回来
        **在个股逻辑没变的情况下**」。

        口径：**只补等量**（不放大仓位）、**不追高**（≤卖出价×(1+WOLF_REFILL_CHASE_MAX)，默认 1%）、
        负事件则放弃（"逻辑变了"）、超窗（WOLF_REFILL_DAYS，默认 2 个交易日）即放弃。
        走 gateway 唯一买入通道 → 仍受 L5 准入闸（`WOLF_DECISION_GATE`）与资金检查约束。
        """
        try:
            from app.services import wolf_hedge_refill as RF
            if not RF.enabled():
                return
            pend = RF.pending()
            if not pend:
                return
            from app.services.t_gateway import gateway_execute
            now = datetime.now()
            today = now.strftime('%Y%m%d')
            hhmm = now.strftime('%H%M')
            xq = sorted({_normalize_symbol(p['symbol']) for p in pend})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq or [])])
            for p in pend:
                sym = _normalize_symbol(p['symbol'])
                q = quotes.get(sym) or {}
                cur = float(q.get('current') or 0)
                if cur <= 0:
                    continue
                need = int(p.get('qty') or 0) - int(p.get('buy_qty') or 0)
                if need <= 0:
                    continue
                elapsed = RF.elapsed_td(str(p.get('sell_date') or ''), today)
                neg = False
                try:
                    from app.services.wolf_early_stop import negative_event
                    neg = bool(negative_event(sym, p.get('sell_date')))
                except Exception:
                    neg = False
                act, reason = RF.refill_decision(float(p.get('sell_px') or 0), cur, elapsed, hhmm,
                                                 neg_event=neg,
                                                 start_hm=RF.from_hm(bool(p.get('holiday'))))
                if act == 'expire':
                    RF.expire(p['key'], reason)
                    continue
                if act != 'buy':
                    continue
                # C6（2026-09-15 round 20）：**附加**他的回补条件 —— 时间窗(14:00-14:30) ∧ 缩量。
                #   2025-04-15 条件6「…在下午2.00-2.30这个时间段进行回补…确保分时上涨放量、回调缩量」＋
                #   2025-06-10「下去了缩量再买回来」。默认**只记录（影子）**，WOLF_STEP_REFILL=1 才真拦；
                #   数据缺失 fail-open（绝不因读数失败而放弃回补）。只影响回补/加仓，不动卖出与止损。
                try:
                    from app.services import wolf_step_refill as SR
                    _st_sr = SR.state()
                    if SR.shadow_enabled():
                        SR.shadow_record({"candidates": [p['symbol'] for p in pend],
                                          "gate": SR.enabled()})
                    if SR.enabled() and not SR.allow(_st_sr):
                        print("[TMonitor] C6 回补条件不满足，跳过 %s: %s" % (sym, _st_sr.get("why")))
                        continue
                except Exception as _e_sr:
                    print("[TMonitor] step_refill 条件异常(放行): %s" % str(_e_sr)[:80])
                if (sym, 'wolf_hedge_refill', today) in self._wolf_done:
                    continue
                vol = (need // 100) * 100
                if vol < 100:
                    continue
                rid = self._insert_wolf_trigger(sym, 'wolf_hedge_refill', q,
                                                "[G9 回补] " + reason, account_id=p['account'])
                gw = gateway_execute(sym, 'buy', cur, vol, reason="[G9 回补] " + reason,
                                     trigger_id=rid, decision_source='rule', account_id=p['account'])
                if gw.get('status') == 'success':
                    RF.mark_done(p['key'], vol)
                    self._wolf_done.add((sym, 'wolf_hedge_refill', today))
                    print(f"[TMonitor] G9 回补 {sym} {vol}股@{cur} [{p['account']}] ok")
                else:
                    print(f"[TMonitor] G9 回补被拒 {sym}: {str(gw.get('reason') or '')[:70]}")
                try:
                    t_db.update_trigger_status(rid, 'executed' if gw.get('status') == 'success' else 'blocked',
                                               reason=f"G9 回补 {vol}股@{cur} [{p['account']}]: {gw.get('status')}")
                except Exception as _eN11:
                    # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                    from app.services import gate_alarm as _gaNote
                    _gaNote.note("t_monitor:line1470", _eN11)
                    pass
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] hedge_refill异常: {e}")

    def _check_fib_target(self) -> None:
        """G2：个股止盈点 = **前一波拉升幅度的 0.618 位**（狼大 2026-04-23「超过或者到了…就是我的止盈点了」）。

        前一波 = 买入前最近一个已确认的"低→高"波段（摆动点两侧各 3 根）；止盈位 = 低点 + 0.618×(高−低)。
        触发 = 现价 ≥ 止盈位 ∧ 有浮盈；卖出量 = **T 仓**（sellable − 底仓 floor，保留底仓）。
        开关 `WOLF_FIB_TARGET=0`；比率 `WOLF_FIB_RATIO`（默认 0.618，他自己说「只用 0.382 和 0.618」）。
        """
        try:
            from app.services import wolf_fib_target as FT
            if not FT.enabled():
                return
            held = [p for p in self._positions() if float(p.get('volume') or 0) > 0]
            if not held:
                return
            from app.services.t_gateway import gateway_execute, get_sellable_ledger, base_floor_shares
            today = datetime.now().strftime('%Y%m%d')
            xq = sorted({_normalize_symbol(p['symbol']) for p in held})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq or [])])
            for p in held:
                sym = _normalize_symbol(p['symbol'])
                acct = p['account_id']
                if (sym, 'wolf_fib_target_sell', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                cur = float(q.get('current') or 0)
                cost = float(p.get('avg_price') or 0)
                if cur <= 0:
                    continue
                bars = _fetch_daily_tencent_dated(sym, 140) or []
                if not bars:
                    continue
                tgt = FT.fib_target(bars, len(bars) - 1) or {}
                act, reason = FT.fib_decision(cur, tgt.get('target'), cost)
                if act != 'sell':
                    continue
                self._wolf_done.add((sym, 'wolf_fib_target_sell', today))
                try:
                    sellable = int(((get_sellable_ledger(account_id=acct).get(sym) or {}).get('sellable', 0)) or 0)
                except Exception:
                    sellable = 0
                # --- ledger 9.302 (user caliber): the 0.618 resistance leg must HALVE THE WHOLE position ---
                #     old: volume = T-sleeve only (sellable - base floor) => only ~100 shares were sold ---
                if str(os.getenv('WOLF_FIB_HALF_BASE', '0')).strip().lower() in ('1', 'true', 'yes', 'on'):
                    vol = (max(sellable, 0) // 2 // 100) * 100
                else:
                    vol = (max(sellable - base_floor_shares(acct, sym, volume=sellable), 0) // 100) * 100
                rid = self._insert_wolf_trigger(sym, 'wolf_fib_target_sell', q,
                                                "[G2 0.618止盈] " + reason, account_id=acct)
                if vol < 100:
                    continue
                gw = gateway_execute(sym, 'sell', cur, vol, reason="[G2 0.618止盈] " + reason, trigger_id=rid,
                                     decision_source='rule', account_id=acct)
                print(f"[TMonitor] G2 0.618止盈 {sym} {vol}股@{cur} [{acct}]: {gw.get('status')}")
                try:
                    t_db.update_trigger_status(rid, 'executed' if gw.get('status') == 'success' else 'blocked',
                                               reason=f"G2 0.618止盈 {vol}股@{cur} [{acct}]: {gw.get('status')}")
                except Exception as _eN12:
                    # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                    from app.services import gate_alarm as _gaNote
                    _gaNote.note("t_monitor:line1527", _eN12)
                    pass
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] fib_target异常: {e}")

    def _check_passive_stop(self) -> None:
        """G4：被动止盈线「**只上移、不破不卖**」（狼大 2025-06-09 / 2025-07-17 / 2026-07-01）。

        线 = max(近 13 日最低价, 已有线)（只上移）；浮盈 >100% 时并用 MA13/中轨；
        跌破且有浮盈 → 卖 **T 仓**（保留底仓），受 ④ 时点门约束（13:00–14:30 不执行）。
        开关 `WOLF_PASSIVE_STOP=0`。
        """
        try:
            from app.services import wolf_passive_stop as PS
            if not PS.enabled():
                return
            held = [p for p in self._positions() if float(p.get('volume') or 0) > 0]
            if not held:
                return
            from app.services.t_gateway import gateway_execute, get_sellable_ledger, base_floor_shares
            today = datetime.now().strftime('%Y%m%d')
            tok, treason = _stop_time_ok()
            xq = sorted({_normalize_symbol(p['symbol']) for p in held})
            quotes = fetch_tencent_quote([_normalize_symbol(_s) for _s in (xq or [])])
            for p in held:
                sym = _normalize_symbol(p['symbol'])
                acct = p['account_id']
                q = quotes.get(sym) or {}
                cur = float(q.get('current') or 0)
                cost = float(p.get('avg_price') or 0)
                if cur <= 0:
                    continue
                bars = _fetch_daily_tencent_dated(sym, 60) or []
                if not bars:
                    continue
                prof = ((cur / cost - 1.0) * 100.0) if cost > 0 else 0.0
                line = PS.sync_line(acct, sym, bars, prof)     # 只上移
                act, reason = PS.passive_decision(cur, line, cost, time_ok=tok)
                if act != 'sell':
                    continue
                if (sym, 'wolf_passive_stop_sell', today) in self._wolf_done:
                    continue
                self._wolf_done.add((sym, 'wolf_passive_stop_sell', today))
                try:
                    sellable = int(((get_sellable_ledger(account_id=acct).get(sym) or {}).get('sellable', 0)) or 0)
                except Exception:
                    sellable = 0
                vol = (max(sellable - base_floor_shares(acct, sym, volume=sellable), 0) // 100) * 100
                rid = self._insert_wolf_trigger(sym, 'wolf_passive_stop_sell', q,
                                                "[G4 被动止盈] " + reason, account_id=acct)
                if vol < 100:
                    continue
                gw = gateway_execute(sym, 'sell', cur, vol, reason="[G4 被动止盈] " + reason, trigger_id=rid,
                                     decision_source='rule', account_id=acct)
                print(f"[TMonitor] G4 被动止盈线跌破 {sym} {vol}股@{cur} [{acct}]: {gw.get('status')}")
                if gw.get('status') == 'success':
                    self._after_sell(sym, acct, 'wolf_passive_stop_sell', '被动止盈线跌破')
                try:
                    t_db.update_trigger_status(rid, 'executed' if gw.get('status') == 'success' else 'blocked',
                                               reason=f"G4 被动止盈 {vol}股@{cur} [{acct}]: {gw.get('status')}")
                except Exception as _eN13:
                    # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                    from app.services import gate_alarm as _gaNote
                    _gaNote.note("t_monitor:line1588", _eN13)
                    pass
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] passive_stop异常: {e}")

    def _after_sell(self, sym, account, kind, reason="") -> None:
        """G3（2026-09-14）：**破线类**卖出成交后"删票"（登记观察池黑名单，带 TTL）。

        狼大 2025-02-06「最下面那根线一旦破了 **卖出然后删票**」/ 2025-04-03「破之前新低的，**直接删票**」/
        2021-01-22「这两根破了**这个标我就不看了**」。只影响**买入侧候选**，不影响卖出（保护不能失效）。
        """
        try:
            from app.services import wolf_ticket_ban as TB
            if str(kind) in ("stop_loss", "custom_support_sell", "wolf_passive_stop_sell"):
                TB.ban(account, sym, reason or str(kind))
        except Exception as e:
            print(f"[TMonitor] 删票登记失败 {sym}: {e}")

    def _check_position_discipline(self) -> None:
        """去弱留强(P1-6, 2026-09-10): 反弹语境内, 减 T 仓最弱的持仓。

        狼大 2026-04-23「反弹的时候卖弱的 留强的 不要搞反了 / **不要觉得哪个反弹多就卖** 留那种没波动的」。
        判据 = **反弹幅度**(反弹最多的是强票, 要留; 没波动的才是弱票 —— 与直觉相反)。
        语境 = 该持仓**所属主题**的结构已确认(主题浪, **不是大盘浪**) + 大盘非系统性下跌;
              依据狼大 2026-05-26「机构目的就是逼大家趋弱留强…这是明牌」→ 下跌段做等于替机构接盘。
        作用域 = 只减 T 仓(复用既有 wolf_defensive_t_reduce 管道), **不动底仓**。
        """
        try:
            if os.getenv("WOLF_POSITION_DISC", "1").strip() in ("0", "false", "no"):
                return
            _minp = int(os.getenv("WOLF_POSITION_DISC_MIN", "3"))
            pos_list = [p for p in self._positions() if float(p.get('volume') or 0) > 0]
            if len(pos_list) < _minp:
                return
            import sys as _sp
            _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "main_line")
            if _p not in _sp.path:
                _sp.path.insert(0, _p)
            from wolf_context import systemic_block, theme_of_symbol, theme_structure
            _sb, _sr = systemic_block()
            if _sb:
                return
            import position_discipline as PD
            # G5（2026-09-14）：**分方向**——高位方向"卖强留弱"、低位方向"留强丢弱"（狼大 2026-09-04 15:07）
            try:
                from app.services import wolf_direction_position as DP
            except Exception:
                DP = None
            items = []
            _dir_cache = {}
            for p in pos_list:
                sym = _normalize_symbol(p.get('symbol'))
                th = theme_of_symbol(sym)
                st = theme_structure(th)
                # 只在"结构确认过的反弹"里做(狼大: 反弹的时候); 主题未确认 → 该票不参与
                if str(st.get("stage") or "") != "confirmed":
                    continue
                # 用 _daily_dated（带日期 + 实时源兜底）而不是 _prev_daily：
                # ① _prev_daily 丢掉日期 key，传给 rebound_pct 会去比较 dict → 抛异常（静默失效第 8 例；
                #    已由 rebound_pct 兼容 list 兜底，但这里给带日期的正确形态）；
                # ② _prev_daily 只读 data/{stock_5m_bt,recent_sync}，那两个目录是 2026-09-03 的一次性导出
                #    → 去弱留强此前一直在用**过期日线**算反弹幅度。
                _pd_map = {_b['date']: _b for _b in self._daily_dated(sym, 6)}
                if DP is not None:
                    if th not in _dir_cache:
                        _dir_cache[th] = DP.branch_of(th)
                    _dp = _dir_cache[th]
                else:
                    _dp = {"verdict": "UNKNOWN", "branch": "low_sell_weak", "reason": "无方向模块"}
                items.append({"symbol": sym, "theme": th, "dir_pos": _dp.get("verdict"),
                              "dir_branch": _dp.get("branch"), "dir_reason": _dp.get("reason"),
                              "rebound": PD.rebound_pct(_pd_map)})
            _hi = [it for it in items if str(it.get("dir_pos") or "").upper() == "HIGH"]
            if _hi:
                print("[TMonitor] 方向高位位次(G5): %s" % ", ".join(
                    "%s=%s(%s)" % (it["theme"], it.get("dir_pos"), (it.get("dir_reason") or "")[:28])
                    for it in _hi), flush=True)
            res = PD.select_weak(items)
            if res.get("skip") or not res.get("sells"):
                if res.get("skip"):
                    print(f"[TMonitor] 去弱留强不动作: {res['skip']}", flush=True)
                return
            today = datetime.now().strftime('%Y%m%d')
            quotes = fetch_tencent_quote([_normalize_symbol(s["symbol"]) for s in res["sells"]])
            for s in res["sells"]:
                sym = s["symbol"]
                if (sym, 'wolf_defensive_t_reduce', today) in self._wolf_done:
                    continue
                q = quotes.get(sym) or {}
                _br = s.get("branch") or "low_sell_weak"
                if _br == "high_sell_strong":
                    reason = ("分方向去弱留强[高位方向→卖强留弱]: 反弹%+.2f%% 为该方向最强"
                              "(分支分化%.2f%%, 方向=%s/%s) → 减T仓; "
                              "狼大2026-09-04「高位方向…卖强的 留弱的 拉升后都走」"
                              % (s.get("rebound") or 0, res.get("spread") or 0,
                                 s.get("dir_pos"), (s.get("dir_reason") or "")[:40]))
                else:
                    reason = ("分方向去弱留强[低位/中位→留强丢弱]: 反弹%+.2f%% 为持仓最弱"
                              "(强弱分化%.2f%%) → 减T仓; 狼大2026-04-23「反弹的时候卖弱的 留强的」"
                              % (s.get("rebound") or 0, res.get("spread") or 0))
                self._insert_wolf_trigger(sym, 'wolf_defensive_t_reduce', q, reason)
                self._wolf_done.add((sym, 'wolf_defensive_t_reduce', today))
                print(f"[TMonitor] 去弱留强减T {sym}: {reason}", flush=True)
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] position_discipline异常: {e}")

    def _check_index_level_stop(self) -> None:
        """③ 指数大级别止损（2026-09-10，狼大 2026-08-27 549楼原话）。

        狼大:「**只看指数大级别**如果不走大5浪而转为下跌1浪就止损」。
        信号 = wolf_context.index_level_stop(): 指数(上证) wave_state.level == "down"（**只取大级别**,
               不掺 sub_level/operation, 依他"只看大级别"的原话）, 带**新鲜度护栏**(浪型数据过期则不触发)。

        动作 = 对账户内所有持仓执行止损:
          · 卖出量复用 _stop_exit_volume（收盘窗口→清仓 / 盘中→减半仓, 与个股止损同一分级;
            理由: 不新造第二套量级规则, 避免 S7 那种并行口径）;
          · 受 ④ 时点门约束(_stop_time_ok: 13:00-14:30 仅预警不执行);
          · 走网关 is_stop_loss=True（止损单豁免日亏损熔断）。
        开关: WOLF_INDEX_LEVEL_STOP=0 关闭。去抖: 每标的每日一次。
        """
        try:
            if os.getenv("WOLF_INDEX_LEVEL_STOP", "1").strip() in ("0", "false", "no"):
                return
            import sys as _sp
            _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "main_line")
            if _p not in _sp.path:
                _sp.path.insert(0, _p)
            # ③层加宽（2026-09-14）：他的"尾段/结束"在系统里主要是 d4 系列/顶态子浪，不只是 down。
            # 关键点重放校准：2026 年 6 次"尾段"→d4/4-x+defense/t_only；2022"反弹浪走完"→d4/C杀、d4/4-5；2021-01-21→d3/3-5。
            # 开关 WOLF_INDEX_TOP_WIDEN=0 → 退回旧口径（只认 level=='down'）。
            _today = datetime.now().strftime('%Y%m%d')
            # ③ 附加护栏（2026-09-14）：锚点（wave_pivots）过旧也提示 —— 它是浪型判定的输入，
            # 刷新链断了会导致"判定看似新鲜、依据却是旧的"。仅告警，不改行为。
            try:
                import sys as _s3, os as _o3
                _s3.path.insert(0, _o3.path.join(_o3.path.dirname(_o3.path.dirname(_o3.path.dirname(_o3.path.abspath(__file__)))), "jobs"))
                from wave_pivots_pg import age_days as _pv_age
                _pa = _pv_age()
                _lim = int(os.getenv("WOLF_PIVOTS_MAX_STALE_DAYS", "10"))
                if _pa is not None and _lim > 0 and _pa > _lim:
                    _tkp = ("__pivots__", "stale", _today)
                    if _tkp not in _STOP_HOLD_WARNED:
                        _STOP_HOLD_WARNED.add(_tkp)
                        print("[TMonitor] ⚠️ wave_pivots 已 %d 天未重建（阈值 %d）→ 浪型判定的锚点可能滞后" % (_pa, _lim))
            except Exception as _eN14:
                # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                from app.services import gate_alarm as _gaNote
                _gaNote.note("t_monitor:line1734", _eN14)
                pass
            _widen = os.getenv("WOLF_INDEX_TOP_WIDEN", "1").strip() not in ("0", "false", "no")
            _act = None
            if _widen:
                from wolf_context import index_top_state
                _act, _why = index_top_state(today=_today)
                _stop = _act is not None
            else:
                from wolf_context import index_level_stop
                _stop, _why = index_level_stop(today=_today)
                _act = "clear" if _stop else None
            if not _stop:
                return
            _tok, _treason = _stop_time_ok()
            if not _tok:
                _tk = ("__index__", "timegate", _today)
                if _tk not in _STOP_HOLD_WARNED:
                    _STOP_HOLD_WARNED.add(_tk)
                    print(f"[TMonitor] 指数级止损被时点门拦下(仅预警): {_why} | {_treason}")
                return
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            _acct = POS_ACCOUNT
            _ledger = get_sellable_ledger(account_id=_acct) or {}
            _rows = []
            for p in self._positions(_acct):
                if float(p.get('volume') or 0) <= 0:
                    continue
                _sym = _normalize_symbol(p.get('symbol'))
                _sellable = int((_ledger.get(_sym) or {}).get("sellable", 0) or 0)
                if _sellable > 0:
                    _rows.append((_sym, _sellable))
            if not _rows:
                return
            _quotes = fetch_tencent_quote([_normalize_symbol(s for s, _ in _rows)])
            _close_win = _in_close_window()
            for _sym, _sellable in _rows:
                if (_sym, 'wolf_index_level_stop', _today) in self._wolf_done:
                    continue
                _q = _quotes.get(_sym) or {}
                _cur = float(_q.get("current") or 0)
                if _cur <= 0:
                    continue
                _vol, _mode = _stop_exit_volume(_sellable, _close_win)
                if _act == "reduce":
                    # 顶态 = **减仓级**（不是清仓）：不论是否收盘窗口都只减半
                    _vol = max(((_vol // 2) // 100) * 100, 100) if _vol >= 200 else _vol
                    _mode = "top_reduce"
                if _vol <= 0:
                    continue
                _gw = gateway_execute(_sym, "sell", _cur, _vol,
                                      reason="指数级%s→%s（%s）" % ("顶态减仓" if _act == "reduce" else "大级别止损", _mode, _why),
                                      decision_source="rule", is_stop_loss=True, account_id=_acct)
                print(f"[TMonitor] 指数级止损 {_sym} @ {_cur} x{_vol}[{_mode}]: {_gw.get('status')} | {_why}")
                if _gw.get("status") == "success":
                    self._after_sell(_sym, _acct, "stop_loss", "指数大级别止损")
                self._wolf_done.add((_sym, 'wolf_index_level_stop', _today))
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] index_level_stop异常: {e}")

    def _ambush_entry_day(self, acct: str, sym: str) -> str:
        """本段持仓的**入场日**（FIFO 口径：按成交**逆序**累加到当前持仓量为止 ✓）。"""
        try:
            from sqlalchemy import text as _t
            from app.database import SessionLocal
            with SessionLocal() as _s:
                _rows = _s.execute(_t(
                    "SELECT trade_date, direction, volume FROM paper_trades "
                    "WHERE account_id=:a AND symbol=:s AND COALESCE(voided,0)=0 ORDER BY id DESC"),
                    {"a": acct, "s": sym}).fetchall()
            # 当前持股数（FIFO 重建的"目标"✓；取不到 ⇒ 回退旧行为 ✓）
            _target = 0
            try:
                from sqlalchemy import text as _tv
                from app.database import SessionLocal as _SV
                with _SV() as _sv:
                    _r = _sv.execute(_tv(
                        "SELECT COALESCE(volume,0) FROM paper_positions "
                        "WHERE account_id=:a AND symbol=:s"), {"a": acct, "s": sym}).fetchone()
                    _target = int((_r[0] if _r else 0) or 0)
            except Exception:
                _target = 0
            _acc = 0
            _entry = ""
            for _d, _di, _v in _rows:
                if str(_di).startswith("买"):
                    _acc += int(_v or 0)
                    # ⚠️ 2026-09-26 修（账本 §9.276）：**只有覆盖当前持仓的买日才算"入场"** ✓
                    #   旧写法「先赋值再判 break」✗ ⇒ 最新一笔是卖就 return 空 ✗
                    if _target <= 0 or _acc >= _target:
                        _entry = str(_d).replace("-", "")
                else:
                    _acc -= int(_v or 0)
                # 继续往回走，取**最早**覆盖当前持仓的买日 ✓（**不再 break** ✗）
            return _entry
        except Exception as _e:
            print("[TMonitor] 埋伏纪律：入场日取数失败 %s: %s" % (sym, str(_e)[:60]), flush=True)
            return ""

    def _check_intraday_t_stop(self) -> None:
        """**日内 T 仓的紧止损**（`WOLF_T_STOP_PCT`，库内默认 0 = 关 ✓）——狼大 2026-02-02 的口径 ✓。

        他的原话：「尾盘抄底加仓 ⇒ 随后放量跌破新低 ⇒ **亏半个点就把刚加的仓位全部出清**」✓
        ⇒ 他给的是「**当日新加的短线仓**」一个极紧的止损（**不是**波段建仓 ✓）。

        量化（账本 §9.152，趋势突破腿 43 笔）：
          · 紧止损 **不能**用在趋势/埋伏腿上 ✗ —— 持有 10 日中位 **+7.26% ⇒ −0.50%/−1.00%** ✗✗
            （止损只是把赢家变成 −1% 的输家 ✓）
          · 本函数**只作用于"当天买入"的仓位** ✓（`_entry_day == 今天`），且**排除埋伏腿** ✓
            （埋伏腿有自己的宽止损纪律 ✓ §9.143）
        """
        try:
            _pct = float(os.getenv("WOLF_T_STOP_PCT", "0") or 0)
            if _pct <= 0:
                return
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            _acct = T_MONITOR_ACCOUNT
            _ledger = get_sellable_ledger(account_id=_acct) or {}
            _today = datetime.now().strftime("%Y%m%d")
            _DBG = str(os.getenv("WOLF_AMBUSH_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "on")
            _pos = self._positions(_acct) or []
            for _p in _pos:
                _sym = str(_p.get("symbol") or "")
                if not _sym or float(_p.get("volume") or 0) <= 0:
                    continue
                if (_sym, "wolf_t_stop", _today) in self._wolf_done:
                    continue
                if self._ambush_entry_day(_acct, _sym) != _today:
                    continue                     # 只保护"当天新加"的 T 仓 ✓
                if self._is_ambush_position(_acct, _sym):
                    continue                     # 埋伏腿走自己的宽止损 ✓
                if self._is_trend_position(_acct, _sym):
                    continue                     # ★ 趋势腿**不加紧止损** ✗ ——
                    #   量化（账本 §9.152，趋势突破腿 43 笔）：持有 10 日中位 **+7.26% ⇒ −1.00%** ✗✗
                    #   （紧止损只是把赢家变成 −1% 的输家 ✓）；狼大"亏半个点出清"讲的是**日内加仓** ✓
                _sellable = int((_ledger.get(_sym) or {}).get("sellable", 0) or 0)
                if _sellable <= 0:
                    continue
                # ⚠️ 2026-09-26 修（账本 §9.228）：必须用**归一化符号**取行情 ✗
                #   实证：`fetch_tencent_quote([_normalize_symbol("SZ002792")])` ⇒ **`pnone_match`** ✗（取不到 ✓）
                #         `fetch_tencent_quote([_normalize_symbol("sz002792")])` ⇒ **`sz002792`** ✓（有 current ✓）
                #   ⇒ 大写符号**永远拿不到现价** ⇒ `_cur=0` ⇒ 该段逻辑**静默失效** ✗
                _qsym = _normalize_symbol(_sym)
                _q = fetch_tencent_quote([_normalize_symbol(_qsym)]) or {}
                _qo = (_q.get(_qsym) or _q.get(_sym) or _q.get(str(_sym).lower()) or {})
                _cur = float((_qo.get("current") or _qo.get("price") or 0) or 0)
                _cost = float(_p.get("avg_price") or 0) or 0
                if _cur <= 0 or _cost <= 0:
                    continue
                if _cur <= _cost * (1 - _pct / 100.0):
                    _why = ("日内T仓紧止损（狼大 2026-02-02「亏半个点出清」）："
                            "现价 %.2f ≤ 成本 %.2f×(1−%.2f%%)" % (_cur, _cost, _pct))
                    _gw = gateway_execute(_sym, "sell", _cur, _sellable, reason=_why,
                                          decision_source="ai_led", account_id=_acct)
                    print("[TMonitor] 日内T紧止损 %s x%d: %s | %s" % (
                        _sym, _sellable, str(_gw.get("status"))[:18], _why[:60]), flush=True)
                    self._wolf_done.add((_sym, "wolf_t_stop", _today))
        except Exception as _e:
            print("[TMonitor] 日内T紧止损异常: %s" % str(_e)[:100], flush=True)

    def _is_trend_position(self, acct: str, sym: str) -> bool:
        """本段持仓是否由**趋势突破腿**建立（买入理由含 `trend_break_buy` ✓）。"""
        try:
            from sqlalchemy import text as _t
            from app.database import SessionLocal
            with SessionLocal() as _s:
                _n = _s.execute(_t(
                    "SELECT COUNT(*) FROM paper_trades WHERE account_id=:a AND symbol=:s "
                    "AND COALESCE(voided,0)=0 AND direction LIKE '买%' "
                    "AND COALESCE(reason,'') LIKE :p"),
                    {"a": acct, "s": sym, "p": "%trend_break_buy%"}).scalar() or 0
            return int(_n) > 0
        except Exception:
            return False

    def _is_ambush_position(self, acct: str, sym: str) -> bool:
        """本段持仓是否由**埋伏腿**建立（查买入理由含 `wolf_ambush_buy` ✓）。"""
        try:
            from sqlalchemy import text as _t
            from app.database import SessionLocal
            with SessionLocal() as _s:
                _n = _s.execute(_t(
                    "SELECT COUNT(*) FROM paper_trades WHERE account_id=:a AND symbol=:s "
                    "AND COALESCE(voided,0)=0 AND direction LIKE '买%' "
                    "AND COALESCE(reason,'') LIKE :p"),
                    {"a": acct, "s": sym, "p": "%wolf_ambush_buy%"}).scalar() or 0
            return int(_n) > 0
        except Exception:
            return False

    # ── 突破加仓／回踩确认加仓（账本 §9.304 ✓ 用户要求 ✓；两个开关**库内默认关** ✓）──
    # ── B：十字星后不突破 ⇒ 撤/减（账本 §9.340 ✓ 狼大 2026-02-12 原话 ✓；开关默认关 ✓）──
    def _check_doji_fail(self) -> None:
        """狼大 2026-02-12 ✓：「**十字星后第二天没有突破，上面卖单一点没撤还加大了…
        然后板块没有带动效应…所以就撤了**」✓

        · ①**十字星后不突破** ✓ ⇒ 本函数可算（日线档 ＋ 当日过滤 ✓）
        · ②卖单加大 ✗／③板块无带动 ✗ ⇒ **本仓无对应数据** ✗ ⇒ 日志显式标注"未实现" ✓（不假装 ✓）
        · 动作 ✓：**减半**（他的通用减仓口径 ✓）
        """
        import datetime as _dt
        if str(os.getenv("WOLF_DOJI_FAIL_EXIT", "0")).strip().lower() not in ("1", "true", "yes", "on"):
            return
        try:
            _today = ""
            try:
                _b8 = os.path.basename(str(os.environ.get("DATA_DIR", "")).rstrip("/"))
                _today = _b8 if (len(_b8) == 8 and _b8.isdigit()) else ""
            except Exception:
                _today = ""
            _acct = T_MONITOR_ACCOUNT
            for _p in self._discipline_positions():
                _sym = str(_p.get("symbol") or "")
                _vol = float(_p.get("volume") or 0)
                if not _sym or _vol <= 0:
                    continue
                if self._is_ambush_position(_acct, _sym):
                    continue
                _bd = []
                try:
                    for _b in (self._daily_dated(_sym, 20) or []):
                        _d = str(_b.get("date") or _b.get("trade_date") or "").replace("-", "")[:8]
                        if _d and (not _today or _d <= _today):
                            _bd.append((_d, _b))
                except Exception:
                    _bd = []
                if len(_bd) < 3:
                    continue
                _bd.sort(key=lambda t: t[0])
                # 找最近一根**十字星**（|收-开| <= 0.15 × 振幅 ✓），且它**不是今天**
                _do = None
                for _d, _b in reversed(_bd[:-1][-3:]):
                    try:
                        _o, _h, _l, _c = (float(_b.get("open") or 0), float(_b.get("high") or 0),
                                          float(_b.get("low") or 0), float(_b.get("close") or 0))
                    except Exception:
                        continue
                    if _h > _l and _o > 0 and abs(_c - _o) <= 0.15 * (_h - _l):
                        _do = (_d, _h, _o, _c)
                        break
                if not _do:
                    continue
                _dd, _dh, _dop, _dc = _do
                _td, _tb = _bd[-1]
                try:
                    _tc = float(_tb.get("close") or 0)
                except Exception:
                    _tc = 0.0
                if _tc <= 0 or _dh <= 0:
                    continue
                if _tc > _dh:
                    continue                      # **已突破十字星高点** ✓ ⇒ 不触发 ✓
                _leg = int(max(_vol / 2, 100) // 100 * 100)
                _rs = ("[B 十字星不突破] 十字星 %s（高 %.2f）⇒ 次日未突破（现 %.2f ≤ %.2f ✓）；"
                       "⚠️他另两个条件**本仓未实现**：②上面卖单是否加大 ✗ ③板块是否带动 ✗"
                       "（2026-02-12「十字星后第二天没有突破…所以就撤了」）" % (_dd, _dh, _tc, _dh))
                _r = gateway_execute(_sym, "sell", _tc, _leg, reason=_rs,
                                     account_id=_acct, is_stop_loss=True)
                print("[TMonitor] B 十字星不突破 ⇒ 减半 %s %d股@%.3f: %s｜%s"
                      % (_sym, _leg, _tc, (_r or {}).get("status"), str((_r or {}).get("reason") or "")[:60]),
                      flush=True)
        except Exception as _e:
            print("[TMonitor] B 十字星退出异常: %s" % str(_e)[:70], flush=True)

    def _check_breakout_add(self) -> None:
        """狼大原话 ✓：
          「我的操作是**突破加仓 回踩确认加仓** 中间不做大级别操作 **向下破线止损**」（2022-06-08）
          「**突破下跌趋势第一根开头我加一半 突破后的第二根红K尾盘 打满**」
          「**固定仓位50%，剩下的做T等突破或回踩打满**」（2025-02-11）
          「带量(上证持续2000E以上)**站上2930那再加仓**」（2016-05-17）
        ⇒ 只对**非埋伏仓** ✓（埋伏仓另有"回踩不破前低"体系 ✓）；每票每轮**最多加 2 次** ✓（他的"最多买 2 笔"）
        """
        _bo = str(os.getenv("WOLF_BREAKOUT_ADD", "0")).strip().lower() in ("1", "true", "yes", "on")
        _rt = str(os.getenv("WOLF_RETEST_ADD", "0")).strip().lower() in ("1", "true", "yes", "on")
        if not (_bo or _rt):
            return
        try:
            from app.services.t_gateway import gateway_execute
            _acct = T_MONITOR_ACCOUNT
            _today = ""
            try:
                _b8 = os.path.basename(str(os.environ.get("DATA_DIR", "")).rstrip("/"))
                _today = _b8 if (len(_b8) == 8 and _b8.isdigit()) else ""
            except Exception:
                _today = ""
            # 记账：{sym: {"n": 次数, "d": 最后加仓日}} ✓（按运行根持久化 ✓）
            try:
                from app.services.state_paths import state_dir as _sd
                _pf = os.path.join(_sd(), "breakout_add.json")
            except Exception:
                _pf = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "breakout_add.json")
            _bk = {}
            try:
                if os.path.exists(_pf):
                    with open(_pf, encoding="utf-8") as _f:
                        _bk = json.load(_f) or {}
            except Exception:
                _bk = {}
            for _p in self._discipline_positions():
                _sym = str(_p.get("symbol") or "")
                _vol = float(_p.get("volume") or 0)
                if not _sym or _vol <= 0:
                    continue
                # ⚠️ 入口留痕（账本 §9.309 补）：能区分"没行情 ✗"／"埋伏仓被排除 ✓"／"额度用尽 ✓"
                try:
                    if str(os.getenv("WOLF_ADD_DEBUG", "1")).strip().lower() in ("1", "true", "yes", "on"):
                        print("[加仓候选·入口] %s 持仓%.0f 埋伏=%s 已加%d次"
                              % (_sym, _vol, self._is_ambush_position(_acct, _sym),
                                 int((_bk.get(_sym) or {}).get("n") or 0)), flush=True)
                except Exception:
                    pass
                if self._is_ambush_position(_acct, _sym):
                    continue                      # 埋伏仓走自己的体系 ✓
                _st = _bk.get(_sym) or {}
                # ⚠️ 2026-09-30（账本 §9.321）：**分桶计数** ✓（突破／回踩各自 ≤2 ✓）
                #   他 2022-06-08「突破加仓 回踩确认加仓」⇒ 两次独立机会 ✓ ⇒ 各自成桶 ✓
                if int(_st.get("n") or 0) >= 2 and int(_st.get("n_rt") or 0) >= 2:
                    continue
                if int(_st.get("n") or 0) >= 2 and str(os.getenv("WOLF_RETEST_ADD_INDEPENDENT", "0")) \
                        .strip().lower() not in ("1", "true", "yes", "on"):
                    continue
                if _today and str(_st.get("d") or "") == _today:
                    continue                      # 当日只加一次 ✓
                _q = {}
                # ⚠️ 2026-09-30 修（账本 §9.310）：行情取数**必须用归一化符号** ✓
                #   实测：`_quote_now("SZ002156")` ⇒ `{'pnone_match': None}` ✗ ⇒ `_cur=0` ⇒ **静默跳过** ✗
                #   （这是本仓第 6 次撞同一个坑 ✗ —— 归一化后 `sz002156` 才能取到 ✓）
                # ⚠️ 2026-09-30 修（账本 §9.310）：`_quote_now(symbols)` 收的是**列表** ✓
                #   我原先传字符串 ✗ ⇒ 它按"逐字符"处理 ⇒ 返回 `pnone_match` ✗ ⇒ `_cur=0` ⇒ 静默跳过 ✗
                for _sq in (_normalize_symbol(_sym), str(_sym).lower(), _sym):
                    try:
                        _qr = self._quote_now([_sq]) or {}
                    except Exception:
                        _qr = {}
                    _q = _qr.get(_sq) or _qr.get(str(_sq).lower()) or _qr.get(str(_sq).upper()) or {}
                    if not _q:
                        for _k2, _v2 in (_qr or {}).items():
                            if isinstance(_v2, dict) and float(_v2.get("current") or 0) > 0:
                                _q = _v2
                                break
                    if float(_q.get("current") or _q.get("price") or 0) > 0:
                        break
                _cur = float(_q.get("current") or _q.get("price") or 0)
                _low = float(_q.get("low") or 0)
                _op = float(_q.get("open") or 0)
                # ⚠️ 账本 §9.314：**优先直读回测数据面** ✓（引擎同源 ✓；离线环境的唯一可靠来源 ✓）
                _sp = {}
                try:
                    _sp = _sandbox_price(_sym, _today) or {}
                except Exception:
                    _sp = {}
                if float(_sp.get("cur") or 0) > 0:
                    _cur = float(_sp["cur"])
                    _low = float(_sp.get("low") or _cur)
                    _op = float(_sp.get("open") or _cur)
                if _cur <= 0:
                    # ⚠️ 2026-09-30 修（账本 §9.313）：回测里取数替身**没有旁路报价** ✗
                    #   ⇒ 改用**当日沙箱的 as-of 日线**（引擎同源 ✓），
                    #   且**按当日过滤** ✓（防未来数据污染 ✗ —— 探针里见过补齐到 2026-09-29 ✗）
                    try:
                        _bd3 = self._daily_dated(_sym, 12) or []
                        _ok3 = []
                        for _b3 in _bd3:
                            _d3 = str(_b3.get("date") or _b3.get("trade_date") or "").replace("-", "")[:8]
                            if _d3 and (not _today or _d3 <= _today):
                                _ok3.append(_b3)
                        if _ok3:
                            _cl3 = [float(x.get("close") or 0) for x in _ok3 if x.get("close")]
                            _lo3 = [float(x.get("low") or 0) for x in _ok3 if x.get("low")]
                            _op3 = [float(x.get("open") or 0) for x in _ok3 if x.get("open")]
                            if _cl3:
                                _cur = _cl3[-1]
                                _low = _lo3[-1] if _lo3 else _cur
                                _op = _op3[-1] if _op3 else _cur
                                print("[加仓候选] %s 报价缺失 ⇒ 用当日 as-of 日线兜底 ✓（收%.2f 低%.2f）"
                                      % (_sym, _cur, _low), flush=True)
                    except Exception:
                        pass
                if _cur <= 0:
                    # ⚠️ 补响（账本 §9.310）：这里原先**静默** ✗ ⇒ "为什么没加"根本查不到 ✓
                    try:
                        print("[加仓候选·跳过] %s 行情取不到（_cur=0；试过 ok/lower/raw ✓）" % _sym,
                              flush=True)
                    except Exception:
                        pass
                    continue
                # 关键位＝**前 20 日最高**（与 `trend_break_buy` 的入场口径同源 ✓；
                #   原写法依赖不存在的 `_snapshot_for` ✗ ⇒ 会恒为 0 ⇒ 功能空转 ✗）
                _res = float(_sp.get("res") or 0)
                try:
                    # ⚠️ 账本 §9.313：**必须按当日过滤** ✓（探针里见过补齐到 2026-09-29 ✗）
                    _bd2 = self._daily_dated(_sym, 60) or []
                    _hs = []
                    for _b2 in _bd2:
                        _d2 = str(_b2.get("date") or _b2.get("trade_date") or "").replace("-", "")[:8]
                        _h2 = _b2.get("high")
                        if _h2 and _d2 and (not _today or _d2 <= _today):
                            _hs.append((_d2, float(_h2)))
                    # 当日那根**不算**"前高" ✓ ⇒ 只取严格早于当日的（或最后 20 根 ✓）
                    _prev = [h for _d, h in _hs if not _today or _d < _today]
                    if len(_prev) >= 5:
                        _res = max(_prev[-20:])
                    elif len(_hs) >= 5:
                        _res = max([h for _d, h in _hs][:-1] or [h for _d, h in _hs])
                except Exception:
                    _res = 0.0
                _vr = float(_sp.get("vr") or 0)
                try:
                    _vr = _vr or float(wolf_leg_vol_ratio(_q, symbol=_sym) or 0)
                except Exception:
                    _vr = 0.0
                if _vr <= 0:
                    # ⚠️ 账本 §9.313：报价缺失时量比也为 0 ✗ ⇒ 用**当日量/5 日均量**兜底 ✓
                    try:
                        _bv2 = []
                        for _b4 in (self._daily_dated(_sym, 12) or []):
                            _d4 = str(_b4.get("date") or _b4.get("trade_date") or "").replace("-", "")[:8]
                            _v4 = _b4.get("vol")
                            if _v4 and _d4 and (not _today or _d4 <= _today):
                                _bv2.append((_d4, float(_v4)))
                        if len(_bv2) >= 2:
                            _last_v = _bv2[-1][1]
                            _base = [v for _d5, v in _bv2[-6:-1]] or [v for _d5, v in _bv2[:-1]]
                            if _base:
                                _vr = round(_last_v / (sum(_base) / len(_base)), 3)
                    except Exception:
                        pass
                _add_vol = int(max(_vol * 0.5, 100) // 100 * 100)     # 「加一半」✓
                # ⚠️ 2026-09-30（账本 §9.323）：**两个独立判定** ✓（用户：「不能用 elif」✓）
                #   他 2022-06-08「**突破加仓 回踩确认加仓**」✓ ⇒ 是**两次机会** ✓
                #   ⇒ 各自判各自的 ✓：两条都成立 ⇒ **都记录** ✓（由"每日一笔"与"分桶上限"约束 ✓）
                _fires = []
                # ⚠️ 2026-09-30（账本 §9.327）：回踩腿加**缩量**判据 ✓（用户「加上」✓）
                #   他的原话 ✓：「这次回踩力度我感觉有点小，但是**量没杀出来**的话
                #               这里**不太是进场位**」✓ ⇒ 回踩要**缩量** ✓
                #   呼应 ✓：「**补仓必须等回落到支撑并缩量，冲高不补**」（2025-06-25）✓
                #   度量 ✓ `_vr` ＝ 当日量 / 5 日均量 ✓（`_vr < 阈值` ⇒ 缩量 ✓）
                _rt_max_vr = float(os.getenv("WOLF_RETEST_ADD_MAX_VR", "1.0") or 1.0)
                _rt_vol_ok = True
                if str(os.getenv("WOLF_RETEST_ADD_VOL_GATE", "0")).strip().lower() \
                        in ("1", "true", "yes", "on"):
                    _rt_vol_ok = (_vr > 0) and (_vr < _rt_max_vr)
                if _rt and _rt_vol_ok and _res > 0 and _low > 0 and _low <= _res < _cur:
                    _fires.append(("n_rt", "回踩确认加仓（当日最低 %.2f **回踩到关键位 %.2f 并收回**"
                                           "（收 %.2f）✓，狼大 2022-06-08「突破加仓 **回踩确认加仓**」✓）"
                                  % (_low, _res, _cur)))
                if _bo and _res > 0 and _cur > _res and _vr >= 1.2:
                    _fires.append(("n", "突破加仓（带量突破关键位 %.3f ✓ 量比 %.2f ✓，狼大 2022-06-08"
                                        "「突破加仓」/ 2016-05-17「带量站上…再加仓」✓）" % (_res, _vr)))
                # 分桶额度（突破 ≤2 ✓／回踩 ≤2 ✓；回踩独立于突破 ✓）
                _fires = [(_b, _w) for (_b, _w) in _fires
                          if int(_st.get(_b) or 0) < 2
                          or (_b == "n_rt" and str(os.getenv("WOLF_RETEST_ADD_INDEPENDENT", "0"))
                              .strip().lower() in ("1", "true", "yes", "on") and int(_st.get("n_rt") or 0) < 2)]
                if not _fires:
                    try:
                        print("[加仓候选·跳过] %s 现价%.2f 关键位%.2f 低%.2f 量比%.2f "
                              "（突破需 现价>关键位∧量比>=1.2；回踩需 低<=关键位<现价∧缩量）"
                              "｜已加 突破%d/回踩%d 次"
                              % (_sym, _cur, _res, _low, _vr, int(_st.get("n") or 0),
                                 int(_st.get("n_rt") or 0)), flush=True)
                    except Exception:
                        pass
                    continue
                # **每日一笔**（同票同日 ✓；两条判定都成立时按桶序取第一条 ✓）
                if _today and str(_st.get("d") or "") == _today:
                    try:
                        print("[加仓候选·跳过] %s 今日已加过 ⇒ 延后 ✓（命中 %d 条判定）"
                              % (_sym, len(_fires)), flush=True)
                    except Exception:
                        pass
                    continue
                if len(_fires) > 1:
                    print("[加仓候选] %s **两条判定同时成立** ✓（回踩 ＋ 突破，各自独立 ✓）⇒ "
                          "**当日名额先给回踩** ✓（挂关键位 ✓、不受加仓口径闸限制 ✓），"
                          "突破那条留待下一日 ✓" % _sym, flush=True)
                _bucket, _why = _fires[0]
                # ⚠️ 2026-09-30（账本 §9.324）：**回踩腿必须买在"关键位"** ✓
                #   原写法一律用 `_cur`（收盘价 39.97 ✗）⇒ 用户：「回踩的是 39.190，
                #   为什么加仓价是 39.970？」✗ —— 那是**追高** ✗
                #   他的口径 ✓：「**回踩确认加仓**」（2022-06-08）＋「**挂前一天的低点 能买进去就做正T**」
                #   ＋「**冲上去一定不能追**」✓ ⇒ 回踩腿**挂关键位** ✓（当日最低 39.19 ≤ 39.61 ⇒
                #   **限价单能成交** ✓，回测也真实 ✓）
                _px = float(_res) if _bucket == "n_rt" and _res > 0 else _cur
                _r = gateway_execute(_sym, "buy", _px, _add_vol, reason=_why,
                                     decision_source="risk", account_id=_acct)
                _ok = str((_r or {}).get("status") or "")
                _rr = str((_r or {}).get("reason") or "")[:120]
                _lv = str((_r or {}).get("level") or "")
                print("[TMonitor] %s 买 %d股@%.3f: %s｜%s｜**被拦理由=%s（%s）**"
                      % (_sym, _add_vol, _px, _ok, _why[:80], _rr, _lv), flush=True)
                if _ok in ("success", "submitted", "filled"):
                    _st = dict(_st)
                    _st[_bucket] = int(_st.get(_bucket) or 0) + 1
                    _st["d"] = _today
                    _st["red"] = bool(_op > 0 and _cur > _op)
                    _bk[_sym] = _st
                    try:
                        with open(_pf, "w", encoding="utf-8") as _f:
                            json.dump(_bk, _f, ensure_ascii=False, indent=1)
                    except Exception:
                        pass
        except Exception as _e:
            print("[TMonitor] 突破/回踩加仓异常: %s" % str(_e)[:80], flush=True)

    def _check_ambush_promote_add(self) -> None:
        """**转正后主动加仓一次** ✓（账本 §9.244；`WOLF_AMBUSH_PROMOTE_ADD=1` ✓）。

        量化口径（长样本 ＋ 样本外分割 ✓）：**转正日 ＋ 3 自然日内 ＋ 价 ≤ 转正日价×1.04 ⇒ 买一次** ✓
          A 不重置 +524.96%／−10.0%（比例 52.5）｜**B +715.75%／−13.0%（比例 55.1）** ✓✓
          ｜C"马上加" +664.30% ✗ ⇒ **「不追高 ≤4%」本身创造价值** ✓
        语料 ✓：「**突破下跌趋势第一根开头我加一半** 突破后的**第二根红K尾盘打满**」✓＋「**不追高**」✓

        ⚠️ 为什么必须"**主动**"✗：实测（§9.244）转正已发生多只 ✓，但 `转正加仓放行` **0 次** ✗ ——
          因为**没有买入腿愿意买它们**（系统在这两只上产生的全是**卖出/兑现腿** ✗）
          ⇒ 只做"放行"＝**被动** ✗ ⇒ 与量化的 B 口径**对不上** ✗ ⇒ 这里补上**主动买一次** ✓
        """
        if str(os.getenv("WOLF_AMBUSH_PROMOTE_ADD", "0")).strip().lower() not in ("1", "true", "yes", "on"):
            return
        try:
            import json as _js
            _root = os.path.dirname(os.environ.get("DATA_DIR", "/app/data")) or "/app/data"
            _pf = os.path.join(_root, "ambush_promoted.json")
            if not os.path.exists(_pf):
                return
            with open(_pf, encoding="utf-8") as _f:
                _rec = _js.load(_f) or {}
        except Exception:
            return
        _acct = T_MONITOR_ACCOUNT
        _pct = 0.0
        try:
            _pct = abs(float(os.getenv("WOLF_AMBUSH_SIZE_PCT", "0") or 0))
        except Exception:
            _pct = 0.0
        if _pct <= 0:
            return
        try:
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            _led = get_sellable_ledger(account_id=_acct) or {}
        except Exception:
            return
        # 权益：现金 ＋ 持仓市值（与账户口径一致 ✓）
        _eqA = 250000.0
        try:
            from sqlalchemy import text as _tq
            from app.database import SessionLocal as _SQ
            with _SQ() as _sq:
                _c = float(_sq.execute(_tq(
                    "SELECT COALESCE(available_cash,0) FROM paper_account_info WHERE account_id=:a"),
                    {"a": _acct}).scalar() or 0)
                _m = float(_sq.execute(_tq(
                    "SELECT COALESCE(SUM(volume*avg_price),0) FROM paper_positions "
                    "WHERE account_id=:a AND COALESCE(volume,0)>0"), {"a": _acct}).scalar() or 0)
            _eqA = max(1.0, _c + _m)
        except Exception:
            pass
        _today = datetime.now().strftime("%Y%m%d")
        _changed = False
        # ⚠️ 键可能是大写（记录写的是大写 ✓）而持仓 symbol 可能是小写 ✗ ⇒ 统一按大写匹配 ✓（§9.250）
        _recU = {str(k).upper(): v for k, v in _rec.items()}
        for _sym, _r in list(_recU.items()):
            try:
                if _r.get("added"):
                    continue
                _pd = str(_r.get("pday") or "")
                _ppx = float(_r.get("ppx") or 0)
                if not _pd or _ppx <= 0:
                    continue
                _gap = (datetime.strptime(_today, "%Y%m%d") - datetime.strptime(_pd, "%Y%m%d")).days
                if _gap < 0 or _gap > 3:
                    continue
                _qs = _normalize_symbol(_sym)
                _q = self._quote_now(_qs) or {}
                _cur = float(((_q.get(_qs) or _q.get(_sym) or {}).get("current")
                              or ((_q.get(_qs) or _q.get(_sym) or {}).get("price")) or 0) or 0)
                if _cur <= 0:
                    # **回退到日线收盘** ✓（与纪律**同口径** ✓）：行情替身没到位时也能工作 ✓
                    try:
                        _bdA = self._daily_dated(_sym, 60) or []
                        _lastA = [b for b in _bdA if b.get("close")]
                        if _lastA:
                            _lastA.sort(key=lambda b: str(b.get("date") or b.get("trade_date") or ""))
                            _cur = float(_lastA[-1].get("close") or 0) or 0
                    except Exception:
                        _cur = 0.0
                if _cur <= 0:
                    # **可见性** ✗：行情与日线都空时也必须留痕 ✓（否则与"没跑"分不清 ✓）
                    if str(os.getenv("WOLF_AMBUSH_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "on"):
                        print("[埋伏转正加仓·跳过] %s 行情与日线都空 ✗（距转正 %d 天）" % (_sym, _gap), flush=True)
                    continue
                if _cur > _ppx * 1.04:
                    # **不追高**拦住 ✓（量化：B 口径优于"马上加" ✓ —— 这条纪律本身创造价值 ✓）
                    if str(os.getenv("WOLF_AMBUSH_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "on"):
                        print("[埋伏转正加仓·跳过] %s 现价 %.2f > 转正价 %.2f×1.04=%.2f（不追高 ✓，距转正 %d 天）"
                              % (_sym, _cur, _ppx, _ppx * 1.04, _gap), flush=True)
                    continue
                # ⚠️ 2026-09-30（账本 §9.344 ✓ 用户：「当天最高才 70.76，为什么买入价是 71？」✗）——
                #   **成交价必须落在当日区间内** ✓。实测 ✗：SH603228 01-19 以 **71.366** 成交，
                #   而当日区间是 **68.59 ~ 70.76** ✗（71.366 其实落在 **01-16** 的区间 ✓）
                #   ⇒ 取数那一档给的是**上一交易日的陈旧行情** ✗ ⇒ 成交价越过了当日最高 ✗
                #   ⇒ 用**当日 as-of 档**（引擎同源 ✓）校验：越界 ⇒ **收敛到当日收盘** ✓ ＋ 大声留痕 ✓
                try:
                    _spA = _sandbox_price(_sym, _today) if "_sandbox_price" in globals() else {}
                except Exception:
                    _spA = {}
                try:
                    _hA = float((_spA or {}).get("high") or 0) or 0.0
                except Exception:
                    _hA = 0.0
                try:
                    _lA = float((_spA or {}).get("low") or 0) or 0.0
                except Exception:
                    _lA = 0.0
                if _hA > 0 and _lA > 0 and not (_lA <= _cur <= _hA):
                    _cur_new = float((_spA or {}).get("cur") or 0) or _cur
                    print("[TMonitor] 转正加仓成交价越界 %s：取数给 %.3f，当日区间 %.2f~%.2f "
                          "=> 收敛到当日收盘 %.2f（防不可能成交价）"
                          % (_sym, _cur, _lA, _hA, _cur_new), flush=True)
                    _cur = _cur_new if _lA <= _cur_new <= _hA else _hA
                _sh = int(_eqA * (_pct / 100.0) / _cur) // 100 * 100
                if _sh < 100:
                    continue
                _gw = gateway_execute(_sym, "buy", _cur, _sh,
                                      reason="埋伏转正加仓（加仓=B：转正日 %s 价 %.2f ⇒ 3 日内且 ≤×1.04 ✓）"
                                             % (_pd, _ppx),
                                      decision_source="risk", account_id=_acct)
                print("[TMonitor] 埋伏转正加仓 %s 买 %d股@%.2f: %s｜理由=%s（转正日 %s 价 %.2f）"
                      % (_sym, _sh, _cur, str(_gw.get("status"))[:16],
                         str(_gw.get("reason") or "-")[:60], _pd, _ppx), flush=True)
                if str(_gw.get("status")) in ("success", "filled", "ok", "accepted"):
                    _r["added"] = True
                    _changed = True
            except Exception as _eA:
                print("[TMonitor] 转正加仓异常 %s: %s" % (_sym, str(_eA)[:70]), flush=True)
        if _changed:
            try:
                with open(_pf, "w", encoding="utf-8") as _f:
                    _js.dump(_rec, _f, ensure_ascii=False)
            except Exception:
                pass

    def run_discipline_checks(self) -> None:
        """**纪律检查的"统一入口"** ✓（账本 §9.243）—— 生产主循环与**回测**都必须走它 ✓。

        背景（用户「这次通宇通讯怎么没加仓」追到底的**最终根因** ✓✗）：
          · 回测 `jobs/bt_prod_run.py` 是**直接调** `mon._round(day)` ＋ 少数 `_arm_*` ✗，
            而**纪律家族**（含 **`_check_ambush_discipline`** ✓）**不在 `_round()` 里** ✗
          · ⇒ **回测从不执行这些纪律** ✗✗ ⇒ 埋伏纪律的日志永远是 0 条 ✓
            （连带：板上减半/被动止盈/13 日规则/G9 避险… 在回测里**全是死的** ✗）
          ⇒ 修法：把这一族抽成**一个入口** ✓，生产主循环与回测**共用** ✓，杜绝再次分叉 ✓
        """
        for _fn in (
            self._settle_tsell_pending, self._ambush_warn_trim, self._settle_pullback_sell,
            self._check_plan_triggers, self._check_wolf_t_rules, self._check_roundtrip_sell,
            self._check_defensive_t_reduce, self._check_board_half, self._check_profit_take,
            self._check_weekend_hedge, self._check_hedge_refill, self._check_fib_target,
            self._check_passive_stop, self._check_boll_sell, self._check_boll_mid_exit,
            self._check_position_discipline, self._check_index_level_stop,
            self._check_logic_time_stop, self._check_ambush_discipline,
            self._check_ambush_promote_add,   # **转正后主动加仓一次** ✓（账本 §9.244）
            self._check_breakout_add,        # **突破加仓／回踩确认加仓** ✓（账本 §9.304 ✓ 两个开关默认关 ✓）
            self._check_doji_fail,           # **B：十字星后不突破 ⇒ 减半** ✓（账本 §9.340 ✓ 默认关 ✓）
            self._check_intraday_t_stop,
        ):
            try:
                _fn()
            except Exception as _eD:
                try:
                    from app.services import alert_hub as _ahD
                    _ahD.note("t_monitor.discipline:%s" % getattr(_fn, "__name__", "?"), _eD)
                except Exception:
                    print("[TMonitor] 纪律 %s 异常: %s" % (getattr(_fn, "__name__", "?"), str(_eD)[:80]), flush=True)

    def _check_ambush_discipline(self) -> None:
        """「埋伏」纪律：**小仓埋伏 ⇒ 固定 N 交易日持有 + 宽止损**（用户 2026-09-25 两步方案之①）。

        量化（账本 §9.143，2,095 个「强势+回调」信号）：
          · 固定 5 日 **−1.18%** ✗｜20 日 −1.95% ✗｜**40 日 +4.74%（58%）** ✓｜**60 日 +7.05%（60%）** ✓
          · **紧止损 −10% 是灾难** ✗✗：中位掉到 **−10.49%**、为正 33% ✗
            （埋伏的入场点本就在回调里 ⇒ 一破线就走 = **刚进就出** ✗；V8 中位持有仅 2 日即证 ✓）
          · 宽止损 **−20%** 只损失 1.9pp ✓；均线/前低出场 −1.89%/−5.03% ✗ ⇒ **任何价格型出场都在砍赢家** ✗
        开关（**库内默认 0 = 关** ⇒ 生产零影响 ✓）：`WOLF_AMBUSH_HOLD_DAYS`、`WOLF_AMBUSH_STOP_PCT`
        """
        try:
            _days = int(float(os.getenv("WOLF_AMBUSH_HOLD_DAYS", "0") or 0))
            _stop = float(os.getenv("WOLF_AMBUSH_STOP_PCT", "0") or 0)
            if _days <= 0 and _stop <= 0:
                return
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            _acct = T_MONITOR_ACCOUNT
            _ledger = get_sellable_ledger(account_id=_acct) or {}
            _today = datetime.now().strftime("%Y%m%d")
            for _p in self._positions(_acct):
                _sym = str(_p.get("symbol") or "")
                if not _sym or float(_p.get("volume") or 0) <= 0:
                    continue
                if (_sym, "wolf_ambush_exit", _today) in self._wolf_done:
                    continue
                # 2026-09-26 修（作用面审计 ✓）：**只对本段持仓由埋伏腿建立的票生效** ✗
                #   原实现遍历**所有持仓** ⇒ 会把趋势腿/低吸腿也按"固定 50 日 + 宽止损 −20%"管 ✗
                #   —— 那是**埋伏腿专属纪律**，不该外溢 ✓（§9.170）
                if not self._is_ambush_position(_acct, _sym):
                    continue
                _sellable = int((_ledger.get(_sym) or {}).get("sellable", 0) or 0)
                if _sellable <= 0:
                    continue
                _entry = self._ambush_entry_day(_acct, _sym)
                if not _entry:
                    continue
                # ⚠️ 2026-09-26 修（账本 §9.237）：`_daily_dated` 在本地缓存过期时会走**回退取数** ✗
                #   而该路径会 import **PySide6**（vnpy 依赖 ✗，本机无此模块 ✗）
                #   ⇒ **整个 `_check_ambush_discipline` 每次都在这里抛异常** ✗✗
                #   ⇒ 里面**一行都没执行** ✓（实证：`埋伏纪律异常: No module named 'PySide6'` ✗）
                #   ⇒ 纪律的 50 日/宽止损/转正/最高价更新 **全都静默失效** ✗（这才是根因 ✓）
                try:
                    _bd = self._daily_dated(_sym, 90) or []
                except Exception as _eBD:
                    print("[TMonitor] 埋伏纪律：日线取数失败(按空处理，不中断纪律) %s: %s"
                          % (_sym, str(_eBD)[:60]), flush=True)
                    _bd = []
                # ── **转正**（账本 §9.220）：浮盈曾达阈值 ⇒ 不再是"埋伏档" ✓
                #   转正后：①**不再受 50 日/宽止损约束** ✗ ②改走「**自最高价回撤 trail% 出**」✓
                #   语料：「13 日内需要碰新高…否则这个票呆的意义就不大」✓（碰上去了 ⇒ 逻辑成立 ✓）
                _qsym2 = _normalize_symbol(_sym)
                # ⚠️ 2026-09-26 修（账本 §9.241）：**行情源在回测里取不到** ✗
                #   `t_monitor` 在**模块顶部**就 `from … import fetch_tencent_quote` ✓，
                #   而回测的 as-of 替身是**之后**替换**模块属性**的 ✗ ⇒ **监控拿到的仍是真函数** ✗
                #   ⇒ 沙箱/回测内**无网络** ⇒ 行情为空 ⇒ `_cur = 0`
                #   ⇒ **不更新最高价／不转正／不触发出场** ✗✗（第四层原因 ✓）
                #   ⇒ 修法：取不到就**回退到日线收盘**（as-of 同源、且复权 ✓）
                _q = self._quote_now(_qsym2) or {}
                # ⚠️ 2026-09-26 修（账本 §9.227）：字段名错了 ✗ —— `fetch_tencent_quote` 返回的是
                #   **`current`**（全仓库口径 ✓），而此处原先读 **`price`** ✗ ⇒ `_cur` 恒为 0
                #   ⇒ ①**最高价永不更新** ✗（实测 002792 现价 60.78 而最高价仍 44.66 ✗）
                #      ②**埋伏纪律的两条离场永不触发** ✗（宽止损/50 日都判不出来 ✓）
                #   ⇒ 两个字段都读（优先 current ✓，兼容 price ✓）
                # ⚠️ 2026-09-26 修（账本 §9.228）：**键也要归一化** ✗
                #   病灶：`fetch_tencent_quote` 在回测里返回的键是**归一化符号**（如 sz002792 ✓），
                #   而 `_q.get(_sym)`（SZ002792 ✗）取不到 ⇒ `_cur` 仍为 0 ⇒ 修复①无效 ✗
                #   （实证：日志 `sell 100股@52.1` ✓ 而 `highest_price` 仍 44.66 ✗）
                _qo = (_q.get(_qsym2) or _q.get(_sym) or _q.get(str(_sym).lower()) or {})
                _cur = float((_qo.get("current") or _qo.get("price") or _qo.get("last") or 0) or 0)
                if _cur <= 0:
                    _cur = float(_p.get("last_price") or 0) or 0
                if _cur <= 0 and _bd:
                    # 回退：**日线最后一根的收盘**（回测里 `_daily_dated` 为 as-of/复权 ✓）
                    try:
                        _lastb = [b for b in _bd if b.get("close")]
                        if _lastb:
                            _cur = float(sorted(_lastb, key=lambda b: str(b.get("date") or b.get("trade_date") or ""))[-1].get("close") or 0) or 0
                    except Exception:
                        pass
                _cost = float(_p.get("avg_price") or 0) or 0
                _promoted = False
                if self._ambush_promote_pct() > 0:
                    _hi = float(_p.get("highest_price") or 0) or 0.0
                    _cost0 = float(_p.get("avg_price") or 0) or 0.0
                    # ⚠️ 2026-09-26 修（账本 §9.225）：`highest_price` 原先**只有生产端**
                    #   `stop_loss_monitor._update_highest_price()` 在更新 ✗ ⇒ **回测里它不跑**
                    #   ⇒ 最高价永远等于成本 ⇒ **转正永远触发不了** ✗（实测 5 只持仓全部 +0.0% ✗）
                    #   ⇒ 这里**自己更新**（用监控已取到的现价 ✓），并**用更新后的值**判转正 ✓
                    # **合理性护栏** ✗：只接受与成本同量级的价格（±50% ✓）
                    #   实证：曾把成本 11.21 的票写成 **9.27** ✗（复权/不复权两个价格空间混用 ✓）
                    # ⚠️ 2026-09-26 修（账本 §9.272）：**参照物不能是成本** ✗
                    #   旧写法用「成本 ±50%」⇒ **涨超 50% 的赢家会被当成脏数据清成 0** ✗
                    #     实证：SZ002792 成本 46.01 ⇒ 上限 69.02 ⇒ 它冲到 **73.35** ✗ ⇒ 被清 0 ✗
                    #     ⇒ 移动止盈/50 日/宽止损**全部静默失效** ✗ ⇒ 峰值 73.35 一路跌到
                    #       **45.33** 才被普通腿卖掉（−419.60 ✗，次日反弹 52.17 ✗）
                    #   新参照＝**当日最后一根日线的收盘** ✓（与现价**同一价格空间** ✓）⇒ 带宽 ±30%
                    #     ⇒ 仍能拦住"复权/不复权混用"✗（实证 11.21 被写成 9.27 ✗），且**不伤赢家** ✓
                    _refc = 0.0
                    try:
                        _lb = [b for b in _bd if b.get("close")]
                        if _lb:
                            _refc = float(sorted(_lb, key=lambda b: str(b.get("date") or b.get("trade_date") or ""))[-1].get("close") or 0)
                    except Exception:
                        _refc = 0.0
                    if _refc > 0 and not (0.7 * _refc <= _cur <= 1.3 * _refc):
                        _cur = 0.0
                    if _cur > _hi:
                        try:
                            from sqlalchemy import text as _th
                            from app.database import SessionLocal as _SH
                            with _SH() as _sh:
                                _sh.execute(_th(
                                    # 账本 §9.534 ✓（用户「修复」✓）：**最高价只升不降** ✗ ⇒ 直接赋值会把 HWM 改小 ✗
                                      #   （实测：反复重置后 `SH603629` 成本 49.03 的记录最高被改成 42.41 ✗
                                      #    ⇒ 转正判据 `highest_price ≥ 成本×1.1` 永假 ⇒ 不止盈 ✗）
                                      #   ⇒ 用 `GREATEST` ✓
                                      "UPDATE paper_positions SET highest_price="
                                      "GREATEST(COALESCE(highest_price,0), :h), updated_at=now() "
                                    "WHERE account_id=:a AND symbol=:s"),
                                    {"h": _cur, "a": _acct, "s": _sym})
                                _sh.commit()
                            _hi = _cur
                        except Exception as _eH:
                            print("[TMonitor] 最高价更新异常(不影响其它): %s" % str(_eH)[:70], flush=True)
                    # ⚠️ 2026-09-29 修（账本 §9.291）：**持仓行会被成交重写 ⇒ `highest_price` 丢失** ✗
                    #   实证：成交过的票 `最高=成本` ✗；没成交的才保住（SH603061 255.35 ✓）
                    #   ⇒ 移动止盈基数被清 ⇒ 永不触发 ✗（002792 留痕 `最高=0.00` 全程 ✗）
                    #   ⇒ **改从日线档算"入场以来的最高"** ✓（数据驱动 ✓，不受重写影响 ✓）
                    try:
                        _hb = []
                        for _bb in (_bd or []):
                            _d8b = str(_bb.get("date") or _bb.get("trade_date") or "").replace("-", "")[:8]
                            if _entry and _d8b >= str(_entry)[:8]:
                                _hv = _bb.get("high")
                                if _hv:
                                    _hb.append(float(_hv))
                        if _hb:
                            _peak_bar = max(_hb)
                            if _peak_bar > _hi:
                                # 写回 ✓（若行被重写，下次仍能自愈 ✓）
                                try:
                                    from sqlalchemy import text as _tb
                                    from app.database import SessionLocal as _SB
                                    with _SB() as _sb:
                                        _sb.execute(_tb(
                                            # 账本 §9.534 ✓（用户「修复」✓）：**最高价只升不降** ✗ ⇒ 直接赋值会把 HWM 改小 ✗
                                      #   （实测：反复重置后 `SH603629` 成本 49.03 的记录最高被改成 42.41 ✗
                                      #    ⇒ 转正判据 `highest_price ≥ 成本×1.1` 永假 ⇒ 不止盈 ✗）
                                      #   ⇒ 用 `GREATEST` ✓
                                      "UPDATE paper_positions SET highest_price="
                                      "GREATEST(COALESCE(highest_price,0), :h), updated_at=now() "
                                            "WHERE account_id=:a AND symbol=:s"),
                                            {"h": _peak_bar, "a": _acct, "s": _sym})
                                        _sb.commit()
                                except Exception:
                                    pass
                                _hi = _peak_bar
                    except Exception:
                        pass
                    _promoted = bool(_cost0 > 0 and _hi >= _cost0 * (1 + self._ambush_promote_pct() / 100.0))
                    # **D 口径**：也接受「**碰新高**」（他的原话 ✓）—— 最高价 > **买入前 20 日最高** ✓
                    _ph0 = 0.0
                    if not _promoted and self._promote_on_newhigh():
                        _ph0 = self._prior_high_before_entry(_sym, _entry)
                        if _ph0 > 0 and _hi > _ph0:
                            _promoted = True
                    if not _promoted:
                        try:
                            import json as _jsR
                            _rootR = os.path.dirname(os.environ.get("DATA_DIR", "/app/data")) or "/app/data"
                            _pfR = os.path.join(_rootR, "ambush_promoted.json")
                            if os.path.exists(_pfR):
                                with open(_pfR, encoding="utf-8") as _fR:
                                    if str(_sym).upper() in {str(k).upper() for k in (_jsR.load(_fR) or {})}:
                                        _promoted = True
                        except Exception:
                            pass
                    if _promoted:
                        # ⚠️ 2026-09-30（账本 §9.345 ✓ 用户：「不是分钟重放吗？」✓ 正是 ✓）——
                        #   本该用**当根分钟**的价 ✓，实测 ✗：01-19 记下的 `ppx=71.37`，
                        #   而当日区间是 **68.59~70.76** ✗（71.37 属**上一交易日** ✓）
                        #   ⇒ `_cur` 的源在**日切时点**仍指向昨日 ✗ ⇒ 日期与价格**错配** ✗
                        #   ⇒ 记录前先**用当日 as-of 档校验** ✓：越界 ⇒ 收敛到当日收盘 ✓（源头防线 ✓）
                        _pxR = _cur or _hi
                        try:
                            _spR = _sandbox_price(_sym, _today)
                        except Exception:
                            _spR = {}
                        try:
                            _hR = float((_spR or {}).get("high") or 0) or 0.0
                            _lR = float((_spR or {}).get("low") or 0) or 0.0
                            _cR = float((_spR or {}).get("cur") or 0) or 0.0
                        except Exception:
                            _hR = _lR = _cR = 0.0
                        if _hR > 0 and _lR > 0 and not (_lR <= _pxR <= _hR) and _cR > 0:
                            print("[埋伏纪律] %s 转正价**越界**：取数给 %.3f，当日区间 %.2f~%.2f"
                                  " => 收敛到当日收盘 %.2f（防跨日错配）"
                                  % (_sym, _pxR, _lR, _hR, _cR), flush=True)
                            _pxR = _cR
                        self._record_promotion(_sym, _today, _pxR)   # 供"加仓=B"用 ✓
                _held = sum(1 for _b in _bd if str(_b.get("date") or "") >= _entry)
                # **诊断留痕**（账本 §9.242；`WOLF_AMBUSH_DEBUG=1` ✓）—— 每票每日一行 ✓
                #   目的：一眼看出"纪律有没有跑到这只票、拿到什么价、转正与否" ✓
                #   （此前四层原因全因"看不见"才拖了很久 ✗）
                if str(os.getenv("WOLF_AMBUSH_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "on"):
                    print("[埋伏纪律] %s 现价=%.2f 成本=%.2f 最高=%.2f 持有=%s日 转正=%s 50日=%s 止盈线=%.2f"
                          % (_sym, _cur, _cost, float(_p.get("highest_price") or 0), _held,
                             "是" if _promoted else "否", _days,
                             (float(_p.get("highest_price") or 0) * (1 - self._ambush_trail_pct() / 100.0))
                             if _promoted and float(_p.get("highest_price") or 0) > 0 else 0.0), flush=True)
                _why = ""
                if _promoted:
                    # **转正 ⇒ 改走移动止盈**（自最高价回撤 trail% ✓）—— 理由含「埋伏纪律」⇒ 能过总闸 ✓
                    _trail = self._ambush_trail_pct()
                    _hi2 = float(_p.get("highest_price") or 0) or 0.0
                    if _hi2 > 0 and _trail > 0 and _cur > 0 and _cur <= _hi2 * (1 - _trail / 100.0):
                        _why = ("埋伏纪律：转正移动止盈 现价 %.2f ≤ 最高 %.2f×(1−%.0f%%)"
                                % (_cur, _hi2, _trail))
                elif (not _promoted) and self._no_newhigh_exit_days() > 0 and _held >= self._no_newhigh_exit_days():
                    # **他原话后半截**（账本自记"从未出手" ✓）：13 日内没碰新高 ⇒ 逻辑不成立 ⇒ 离场 ✓
                    _why = ("埋伏纪律：%d 个交易日内未碰新高（买入前 20 日最高 %.2f，现最高 %.2f）⇒ 离场"
                            % (self._no_newhigh_exit_days(), _ph0 if '_ph0' in dir() else 0.0, _hi))
                elif _days > 0 and _held >= _days:
                    _why = "埋伏纪律：持有 %d/%d 交易日到期 ⇒ 离场" % (_held, _days)
                elif _stop > 0 and _cost > 0 and _cur > 0 and _cur <= _cost * (1 - _stop / 100.0):
                    _why = ("埋伏纪律：宽止损 现价 %.2f ≤ 成本 %.2f×(1−%.0f%%)" % (_cur, _cost, _stop))
                if not _why:
                    continue
                _gw = gateway_execute(_sym, "sell", _cur or None, _sellable,
                                      reason=_why, decision_source="ai_led",
                                      account_id=_acct)
                print("[TMonitor] 埋伏纪律离场 %s x%d: %s | %s" % (
                    _sym, _sellable, _why, str(_gw.get("status"))[:20]), flush=True)
                self._wolf_done.add((_sym, "wolf_ambush_exit", _today))
        except Exception as _e:
            import traceback as _tb
            print("[TMonitor] 埋伏纪律异常: %s\n%s" % (str(_e)[:100], _tb.format_exc()[-700:]), flush=True)
            try:
                from app.services import alert_hub as _ah2
                _ah2.note("t_monitor.ambush_discipline", _e)
            except Exception:
                pass

    def _check_logic_time_stop(self) -> None:
        """① 后半句：**建仓初期「逻辑与时间」离场**（2026-09-11，狼大 2026-03-05 同一句原话）。

        狼大:「我说一下我用的 **买入有时间** 然后13日内跌破波段低点的-3%没有收回 直接止损，
              **13日内需要碰新高或者新高。否则这个票呆的意义就不大，证明自己的买入逻辑和时间有问题**」

        → 建仓后 13 个交易日是**观察窗**：窗口走完仍**从未碰过前高**（=建仓日之前 13 根日K 的最高价，
          与波段低点同窗同源）→ 买入逻辑与时间都不成立 → **离场**（不是等它慢慢跌到止损线）。
        → 判据由 wolf_early_stop.logic_time_stop 给出（纯函数）：
          · **窗口未走完（持有 < 13 交易日）一律不动作**（不能买三天没新高就卖）;
          · 盘中最高计入「碰新高」（腾讯 quote.high）—— 否则窗口最后一天的盘中触碰会漏判;
          · 数据不足（前高算不出）→ **不动作**（fail-open，不凭缺失数据卖票）;
          · 「无利空」前提沿用（黑天鹅导致的不创新高属"别的逻辑"，见 negative_event）。

        动作 = 卖出量复用 _stop_exit_volume（收盘窗口→清仓 / 盘中→减半仓，与止损同一分级，
          不新造第二套量级规则）; 受 ④ 时点门约束; 走网关 is_stop_loss=True（止血优先）。
        开关: WOLF_LOGIC_TIME_STOP=0 关闭。去抖: 每标的每日一次。
        """
        try:
            if os.getenv("WOLF_LOGIC_TIME_STOP", "1").strip() in ("0", "false", "no"):
                return
            from app.services.wolf_early_stop import logic_time_stop as _logic_ts
            _today = datetime.now().strftime('%Y%m%d')
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            _acct = POS_ACCOUNT
            _ledger = get_sellable_ledger(account_id=_acct) or {}
            _cands = []
            for p in self._positions(_acct):
                if float(p.get('volume') or 0) <= 0:
                    continue
                _sym = _normalize_symbol(p.get('symbol'))
                _sellable = int((_ledger.get(_sym) or {}).get("sellable", 0) or 0)
                if _sellable > 0:
                    _cands.append((_sym, _sellable))
            if not _cands:
                return
            _quotes = fetch_tencent_quote([_normalize_symbol(s for s, _ in _cands)])
            _close_win = _in_close_window()
            for _sym, _sellable in _cands:
                try:
                    if (_sym, 'wolf_logic_time_stop', _today) in self._wolf_done:
                        continue
                    _bd = self._buy_date(_sym, _today)
                    if not _bd:
                        continue
                    _q = _quotes.get(_sym) or {}
                    _cur = float(_q.get("current") or 0)
                    if _cur <= 0:
                        continue
                    _exit, _why = _logic_ts(self._daily_dated(_sym, 40), _bd,
                                            extra_high=_q.get("high"),
                                            symbol=_sym, today=_today)
                    if not _exit:
                        continue
                    _tok, _treason = _stop_time_ok()
                    if not _tok:
                        _tk = (_sym, "logictime", _today)
                        if _tk not in _STOP_HOLD_WARNED:
                            _STOP_HOLD_WARNED.add(_tk)
                            print(f"[TMonitor] 逻辑时间离场被时点门拦下(仅预警) {_sym}: {_why} | {_treason}")
                        continue
                    _vol, _mode = _stop_exit_volume(_sellable, _close_win)
                    if _vol <= 0:
                        continue
                    _gw = gateway_execute(_sym, "sell", _cur, _vol,
                                          reason="建仓初期逻辑时间离场→%s（%s）" % (_mode, _why),
                                          decision_source="rule", is_stop_loss=True, account_id=_acct)
                    print(f"[TMonitor] 逻辑时间离场[{_mode}] {_sym} @ {_cur} x{_vol}: "
                          f"{_gw.get('status')} | {_why}")
                    self._wolf_done.add((_sym, 'wolf_logic_time_stop', _today))
                except Exception as _e1:
                    print(f"[TMonitor] 逻辑时间离场 {_sym} 异常: {str(_e1)[:120]}")
        except Exception as e:
            self._status['errors'] += 1
            print(f"[TMonitor] logic_time_stop异常: {e}")

    def _roll_wolf_legs(self, today: str) -> int:
        """狼大持续腿跨日结转（2026-09-03 修复生产监控条件丢失）。

        背景：狼大做T条件(249/250/252/253/254 等)按“交易日”建行
        (唯一键 account+symbol+trigger_kind+trade_date)，是非消费式持续腿
        (命中后保持 active，5分钟冷却防刷)；而 t_monitor 每轮只读“当日”
        active 条件。此前持续腿只在建仓当天(trade_date=当天)存在，跨日后
        昨日行 status 仍 active 但因日期不是今日 → list_active_conditions
        读不到 → 报告显示“没有监控条件”。

        本函数幂等：把交易日 today 之前仍 active 的狼大表达式腿，按
        (symbol, trigger_kind) 取最近一日，复制一份到 today（保留表达式/
        价格/止损等全部配置）；今日已有同键行(任意状态, 含人工停用/消费)
        则跳过——不复活用户已停用/已消费的腿；成功后归档旧日源行。
        """
        rolled = 0
        try:
            prev = t_db.list_active_conditions(
                account_id=T_MONITOR_ACCOUNT, before_trade_date=today)
            wolf = [c for c in prev if _is_wolf_t_condition(c)]
            # G0(2026-09-14): 已删除机制的"死腿"不再跨日结转（见 _rollable 与 gap-audit G0）
            wolf = _rollable(wolf)
            if not wolf:
                return 0
            keys_today = {(k.get("symbol"), k.get("trigger_kind"))
                          for k in t_db.list_condition_keys(T_MONITOR_ACCOUNT, today)}
            # prev 已按 trade_date DESC, id DESC → 首次出现即最近一日
            latest: Dict[Any, Dict[str, Any]] = {}
            for c in wolf:
                key = (c.get("symbol"), c.get("trigger_kind"))
                if key not in latest:
                    latest[key] = c
            expired_ids: List[int] = []
            for (sym, kind), src in latest.items():
                if (sym, kind) in keys_today:
                    continue
                copy = {k: v for k, v in src.items()
                        if k not in ("id", "created_at", "armed_at",
                                     "last_triggered_at", "trigger_count_today",
                                     "trade_date", "status")}
                copy.update({
                    "account_id": T_MONITOR_ACCOUNT,
                    "symbol": sym,
                    "trigger_kind": kind,
                    "trade_date": today,
                    "status": "active",
                })
                copy.setdefault("armed", 1)
                # 2026-09-03：跨日结转时刷新个股换手基准（近5已完成交易日均值，
                # 每日重算一次）——旧 wolf 条件无 benchmark 时兜底 0.5%，使系统
                # vol_ratio 与行情量比系统性差一个量级（药明 4.49 vs 1.50）。
                try:
                    prof = copy.get("benchmark_turnover_profile") or {}
                    ct_date = str(prof.get("computed_at") or "")[:10].replace("-", "")
                    if not prof.get("same_minute_avg") or ct_date != today:
                        from app.services.t_turnover_profile import compute_turnover_profile
                        _np = compute_turnover_profile(sym)
                        if _np:
                            copy["benchmark_turnover_profile"] = _np
                except Exception as e:
                    print(f"[TMonitor] 换手基准刷新失败 {sym}: {e}")
                cid = t_db.upsert_condition(copy)
                if cid:
                    rolled += 1
                    if src.get("id"):
                        expired_ids.append(int(src["id"]))
            for cid in expired_ids:
                t_db.update_condition_state(cid, status="expired")
        except Exception as e:
            print(f"[TMonitor] 狼大持续腿跨日结转失败: {e}")
        if rolled:
            print(f"[TMonitor] 狼大持续腿跨日结转 {rolled} 条 → {today}")
            self._status["wolf_legs_rolled"] = f"{today}:{rolled}"
        return rolled

    def _arm_stock_exit_legs(self, today: str) -> int:
        """A方案(2026-09-08 用户拍板): 盘前/启动时对 stock 持仓自动布持续卖腿——
        黄线离场(custom_vwap_sell) / T出前高(high_sell, m5.t_sell) / 破位离场(custom_support_sell)。
        S3(2026-09-10): 回撤跟踪腿(custom_trail_sell)已停止新增(自造机制, quote.trail_break 恒 False)。
        幂等: 当日已有同键(symbol+trigger_kind)行(任意状态含manual/AI/auto)跳过, 不覆盖人工护栏。
        卖量仍由 _round 按 sellable−底仓floor 推导(底仓保护, 无T仓空间则自然跳过)。"""
        armed = 0
        try:
            import psycopg2 as _pg2
            _conn = _pg2.connect(os.getenv("DATABASE_URL",
                                           "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
            _cur = _conn.cursor()
            # ⚠️ 2026-09-19 修：这里原先写死 account_id='stock'（**读生产账户持仓**），却把腿布到
            #   `T_MONITOR_ACCOUNT` 上 ⇒ 回放里实测：drabjan6 空仓，却因为**生产 stock 账户**持有
            #   快克智能/科瑞技术，而被布上三条卖出条件单（custom_vwap_sell/high_sell/custom_support_sell,
            #   publisher=auto_exit），触发后又被 [G8] 拦成 blocked —— 看起来像"错配票又进候选了"。
            #   生产环境 T_MONITOR_ACCOUNT 默认就是 'stock' ⇒ 本修在生产**逐位不变**，只是回测不再串账户。
            _cur.execute("SELECT DISTINCT symbol FROM paper_positions WHERE account_id=%s AND volume > 0",
                         (T_MONITOR_ACCOUNT,))
            syms = [str(r[0]) for r in _cur.fetchall()]
            _cur.close(); _conn.close()
        except Exception as e:
            print(f"[TMonitor] stock持仓读取失败: {e}")
            return 0
        if not syms:
            return 0
        templates = [
            ("custom_vwap_sell", {"and": [{"op": "==", "field": "quote.vwap_break", "value": True}]}),
            ("high_sell", {"and": [{"op": "==", "field": "minute.m5.t_sell", "value": True}]}),
            # 步骤④/②: 跌破最近支撑位卖出腿（auto_exit 持续腿, 放量立减/缩量反抽减/尾盘确认）
            ("custom_support_sell", {"and": [{"op": "==", "field": "quote.break_support", "value": True}]}),
        ]
        keys_today = {(str(k.get("symbol")), str(k.get("trigger_kind")))
                       for k in t_db.list_condition_keys(T_MONITOR_ACCOUNT, today)}
        for sym in syms:
            for kind, expr in templates:
                if (sym, kind) in keys_today:
                    continue
                try:
                    cid = t_db.upsert_condition({
                        "account_id": T_MONITOR_ACCOUNT, "symbol": sym,
                        "trigger_kind": kind, "direction": "sell",
                        "trade_date": today, "status": "active", "armed": 1,
                        "publisher": "auto_exit", "expression": expr,
                    })
                    if cid:
                        armed += 1
                except Exception as e:
                    print(f"[TMonitor] auto_exit 布腿失败 {sym} {kind}: {e}")
        if armed:
            print(f"[TMonitor] stock持仓自动布卖出腿 {armed} 条 → {today}")
        return armed

    def _daily_maintain(self) -> dict:
        """每日一次（每交易日首次轮询前）：归档昨日条件 + 为缺条件的持仓补生成当日双条件。

        规则兜底：只对“今日无任何 active 条件”的持仓标的生成（不覆盖 AI/手动已有条件）；
        顺带让 V反/探针等 t 账户持仓（T+1 后可卖）每天自动获得做T条件。
        """
        res = {"expired": 0, "filled": 0}
        # ── 除权复权（`WOLF_EXRIGHTS_ADJUST`，库内默认 0；用户 2026-09-22 拍板"修 3"）──
        #   必须在**当日任何下单之前**跑一次：除权日的价格按新基准跳低，而我们的持仓是固定股数记账
        #   ⇒ 不调整的话既会出现假回撤，又会在卖出时按 `paper_trades` 的旧买入价记一笔假亏损
        #   （实测 T5 唯一一笔 SH603061：−5,960，是账面最大单笔亏损，纯属除权未复权）。
        #   放在 `_daily_maintain`：每交易日首次轮询前调用一次，生产与回测**同一段代码**。
        try:
            from app.services import wolf_exrights as _exr
            if _exr.enabled():
                _r = _exr.apply_day(T_MONITOR_ACCOUNT, datetime.now().strftime("%Y%m%d"))
                if _r.get("adjusted") or _r.get("errors") or _r.get("skipped"):
                    print("[TMonitor] 除权复权：调整 %d 只 · 跳过 %d · 异常 %d"
                          % (len(_r.get("adjusted") or []), len(_r.get("skipped") or []),
                             len(_r.get("errors") or [])), flush=True)
                    res["exrights"] = {"adjusted": len(_r.get("adjusted") or []),
                                       "skipped": len(_r.get("skipped") or []),
                                       "errors": _r.get("errors") or []}
        except Exception as _exe:
            print("[TMonitor] 除权复权异常(忽略，按未复权口径继续): %s" % str(_exe)[:100], flush=True)
        try:
            res["expired"] = t_db.expire_daily_conditions()
        except Exception as e:
            print(f"[TMonitor] 每日条件归档失败: {e}")
        try:
            # 来源：持仓口径账户（默认 stock；paper_positions），开盘前亦可稳定读取；
            # 条件仅补齐“今日尚无 active 条件”的标的后，交由盘中报价驱动是否触发。
            from app.services.t_pool import build_t_conditions, calc_t_quality
            for pos in self._positions():
                sym = pos.get("symbol")
                avg = float(pos.get("avg_price") or 0)
                if not sym or avg <= 0:
                    continue
                if t_db.list_active_conditions(symbol=sym):
                    continue
                amp = None
                try:
                    q = calc_t_quality(sym)
                    amp = (q.get("factors") or {}).get("amp_median")
                except Exception:
                    amp = None
                for cond in build_t_conditions(avg, amp):
                    cond = {**cond, "account_id": t_db.ACCOUNT_T,
                            "symbol": sym, "trade_date": None}
                    if t_db.upsert_condition(cond):
                        res["filled"] += 1
        except Exception as e:
            print(f"[TMonitor] 每日条件补生成失败: {e}")
        # 2026-09-16（用户拍板）：底仓 floor 盘前一次性认账——历史卖超的标的 floor 会永久大于持仓，
        # 使卖腿推导量恒 0（实测 588170/512480/002409）。新口径在"floor > 可卖−100"时把锚重标为
        # 可卖×ratio 并留痕，此后只随新增买入增长（不会被卖出一笔笔啃掉）。见 t_base_floor 模块头。
        try:
            from app.services.t_base_floor import maintain as _bf_maintain
            _bf = _bf_maintain(accounts=tuple(_position_accounts()), persist=True)
            res["floor_checked"] = _bf.get("checked", 0)
            if _bf.get("rebased") or _bf.get("reset"):
                print(f"[TMonitor] 底仓 floor 认账: rebase={_bf.get('rebased')} reset={_bf.get('reset')}")
                print(f"[TMonitor] 底仓 floor 快照: {_bf.get('floors')}")
        except Exception as e:
            print(f"[TMonitor] 底仓 floor 认账失败（不影响其它维护）: {type(e).__name__}: {str(e)[:80]}")
        print(f"[TMonitor] 每日维护: 归档{res['expired']}条, 补生成{res['filled']}条")
        return res

    def _start_ai_maintain(self) -> None:
        """每日自动 AI 维护：后台线程为持仓重建当日做T条件（AI 优先、规则兜底）。"""
        if self._ai_thread and self._ai_thread.is_alive():
            return
        self._status["ai_maintain_running"] = True
        self._ai_thread = threading.Thread(target=self._ai_maintain_loop, daemon=True,
                                           name="t-ai-maintain")
        self._ai_thread.start()

    def _ai_maintain_loop(self) -> None:
        """AI 维护主体：对 t 账户持仓逐标的 auto_gen_conditions_for_build（AI→规则回退→双腿补齐）。"""
        res = {"ai_ok": 0, "rule_fallback": 0, "fail": 0, "skipped_user": 0}
        try:
            from app.services.t_build import auto_gen_conditions_for_build
            today = datetime.now().strftime("%Y%m%d")
            for pos in self._positions():
                sym = pos.get("symbol")
                avg = float(pos.get("avg_price") or 0)
                if not sym or avg <= 0:
                    continue
                # 人工条件优先：今日已有 publisher=user 的条件则不动
                try:
                    acts = t_db.list_active_conditions(symbol=sym)
                    if any((c.get("publisher") or "") == "user" for c in acts):
                        res["skipped_user"] += 1
                        continue
                except Exception as _eN15:
                    # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                    from app.services import gate_alarm as _gaNote
                    _gaNote.note("t_monitor:line2093", _eN15)
                    pass
                try:
                    ok = auto_gen_conditions_for_build(sym, avg, trade_date=today)
                    if ok:
                        res["ai_ok"] += 1
                    else:
                        res["rule_fallback"] += 1
                except Exception as e:
                    res["fail"] += 1
                    print(f"[TMonitor] AI维护 %s 异常: {e}" % sym)
                time.sleep(2)  # 多标的错峰，避免 LLM 桥连发
        except Exception as e:
            print(f"[TMonitor] AI维护循环异常: {e}")
        self._status["ai_maintain_running"] = False
        self._status["ai_maintained"] = f"{datetime.now():%Y-%m-%d %H:%M} {res}"
        print(f"[TMonitor] AI维护完成: {res}")

    def _round(self):
        """单轮：拉 regime → 读条件 → 并发取价 → 构建字段快照 → 表达式/默认逻辑评估 → 写触发。"""
        # 1) regime 前置（每轮一次，缓存 5s）
        regime_state = compute_regime()

        # 2) 当日有效条件（只读目标账户 + 只跑狼大做T表达式条件, 屏蔽其他做T）
        conditions = t_db.list_active_conditions(account_id=T_MONITOR_ACCOUNT)
        if str(os.getenv("WOLF_DEBUG_COND", "") or "").strip():
            try:
                _k = {}
                for _c in conditions:
                    _k[str(_c.get("trigger_kind"))] = _k.get(str(_c.get("trigger_kind")), 0) + 1
                print("[DBG_LIST] 账户=%s 取到条件单 %d 条｜腿型=%s" % (T_MONITOR_ACCOUNT, len(conditions), dict(sorted(_k.items(), key=lambda x: -x[1])[:8])), flush=True)
            except Exception as _eL:
                print("[DBG_LIST] 失败 %s" % str(_eL)[:60], flush=True)
        conditions = [c for c in conditions if _is_wolf_t_condition(c)]
        if str(os.getenv("WOLF_DEBUG_COND", "") or "").strip():
            try:
                _k2 = {}
                for _c in conditions:
                    _k2[str(_c.get("trigger_kind"))] = _k2.get(str(_c.get("trigger_kind")), 0) + 1
                print("[DBG_LIST] 过滤后 %d 条｜腿型=%s" % (len(conditions), dict(sorted(_k2.items(), key=lambda x: -x[1])[:8])), flush=True)
            except Exception:
                pass
        if not conditions:
            return
        self._status["conditions_checked"] = len(conditions)

        # 3) 并发取价（核心标的）
        # ⚠️ 2026-09-22 修：原实现 `list({c["symbol"] for c in conditions})[:MAX_CORE_SYMBOLS]`
        #   —— **集合乱序取前 20** ⇒ 池子一变大就变成"随机 20 只被评估、其余静默不评估"。
        #   实测（T6 新臂，乙+丙+net 把候选池放大之后）：0105 条件 70 条/36 只、0107 86 条/44 只，
        #   而上限恒为 20 ⇒ **每天 16–24 只（55%）的条件腿连报价都没取到、当天永远不会触发**
        #   （002156 的 `trend_break_buy` 就是这么被吞掉的：腿在 t_conditions 里，但没进前 20）。
        #   现在：①上限可配（`WOLF_MAX_CORE_SYMBOLS`，默认 20 = 旧行为）；
        #        ②**持仓优先**（止损/卖腿最要紧），其余按代码排序 ⇒ 结果确定、不再随机；
        #        ③截断时打一条日志（原来完全静默）。
        try:
            _cap = max(int(float(os.getenv("WOLF_MAX_CORE_SYMBOLS", str(MAX_CORE_SYMBOLS)) or MAX_CORE_SYMBOLS)), 1)
        except Exception:
            _cap = MAX_CORE_SYMBOLS
        _all_syms = {c["symbol"] for c in conditions}
        try:
            _held = {s for s in _all_syms if self._held_today(s)}
        except Exception:
            _held = set()
        # ── 候选优先度排序（2026-09-22 用户拍板："先做 T0/T1/T2-a~d"）──────────────
        #   `WOLF_CORE_PRIORITY`（**库内默认 0 = 旧行为**：持仓优先 + 代码升序）。
        #   为什么需要（实测）：C2′ 同日准入是**流式**的 —— 前 K=3 笔免检、之后要 ≥ 运行中位数；
        #   而开盘后第一轮会把 ~90 只标的的腿**同一轮里全部评估** ⇒ **谁先被评估谁占免检名额**。
        #   实测拦量：T5 1,432 条 / T6(到 0113) 231 条 ⇒ 顺序不是形式问题。
        #   分层（零成本、确定性；对齐方向层）：
        #     T0 持仓票（止损/卖腿必须有报价）
        #     T1 有卖腿的票（保护动作先评估；「破线直接走」）
        #     T2a 主题档：mainline_select.mainline > second > 其他（方向层口径）
        #     T2b 有底仓 > 无底仓 —— 已由 T0 覆盖（同时持仓票必然在最前）
        #     T2c 在 253/254 低吸腿候选池（当日 legs_switch）内 > 不在
        #     T2d 代码升序（兜底，保证确定性）
        try:
            _prio_on = str(os.getenv("WOLF_CORE_PRIORITY", "0")).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            _prio_on = False
        if _prio_on:
            try:
                symbols = sorted(_all_syms, key=self._core_sort_key(conditions, _held))[:_cap]
            except Exception as _pre:
                print("[TMonitor] 候选优先度排序异常(回落代码序): %s" % str(_pre)[:80], flush=True)
                symbols = (sorted(_held) + sorted(_all_syms - _held))[:_cap]
        else:
            symbols = (sorted(_held) + sorted(_all_syms - _held))[:_cap]
        if len(_all_syms) > _cap:
            _tk = datetime.now().strftime("%Y%m%d")
            if getattr(self, "_core_cap_warned", "") != _tk:
                self._core_cap_warned = _tk
                print("[TMonitor] ⚠️ 条件标的 %d 只 > 上限 %d（WOLF_MAX_CORE_SYMBOLS）⇒ 本轮只评估"
                      "持仓优先的前 %d 只，其余 %d 只**本轮不评估**"
                      % (len(_all_syms), _cap, _cap, len(_all_syms) - _cap), flush=True)
        quotes = self._fetch_quotes_concurrent(symbols)

        # 3.5) 止损扫描（持仓标的现价 ≤ stop_loss_price → 止损卖腿，独立于条件触发）
        try:
            from app.services.t_gateway import gateway_execute, get_sellable_ledger
            ledger = get_sellable_ledger(T_MONITOR_ACCOUNT)
        except Exception as e:
            print(f"[TMonitor] 止损扫描初始化失败: {e}")
            ledger = {}

        # 4) 逐条件判断（表达式优先；无表达式回退默认复合确认逻辑）
        # 2026-09-08 防双卖: 同轮同一标的只允许成交一条卖腿(trail/vwap/high 同时命中时互斥,
        # 否则各自按轮初旧账本各卖一次把底仓卖穿, 512480 14:14 事故)
        self._sold_this_round = set()
        written = 0
        _go_caution = {"computed": False, "reason": None}   # A6/A9 谨慎条件：每轮惰性算一次
        for cond in conditions:
            symbol = cond["symbol"]
            if not _board_tradable(symbol):
                continue
            if str(cond.get("direction") or "") == "sell" and symbol in self._sold_this_round:
                continue
            # ── A5 日内做T的**正向时间窗**（2026-09-11, 狼大 2025-04-15 条件2）──
            # 「当日只做上午 9.45-10.00 下午 2.00-2.30 这两个时间段的交易，尽量避免开盘直接买卖
            #   和平稳时间的来回T」。**只作用于日内做T买腿**（low_buy 正T低吸 / custom_prevlow 挂前低回踩），
            # 不作用于 253 建仓腿（属他自己划出去的"大级别买卖"）、卖腿与止损（止损另有 ④ 时点门）。
            if str(cond.get("direction") or "") == "buy":
                try:
                    from app.services.wolf_trade_window import allowed as _tw_allowed, applies_to as _tw_applies
                    if _tw_applies(cond.get("trigger_kind")):
                        _tw_ok, _tw_why = _tw_allowed()
                        if not _tw_ok:
                            _tk = (symbol, "tw", datetime.now().strftime('%Y%m%d'))
                            if _tk not in _STOP_HOLD_WARNED:
                                _STOP_HOLD_WARNED.add(_tk)
                                # flush=True 必须加：容器里 stdout 非 TTY 会缓冲，不加就看不到这条（本轮踩过）
                                print(f"[TMonitor] 日内做T时间窗外，跳过买腿 {symbol}"
                                      f"({cond.get('trigger_kind')}): {_tw_why}", flush=True)
                            continue
                except Exception as _twe:
                    print(f"[TMonitor] 时间窗判定异常(放行) {symbol}: {str(_twe)[:80]}", flush=True)
                # ── A6/A9 谨慎4条（2026-09-11, 狼大 2025-04-15 条件4 后段）──
                # 「如果当出现以下情况时，操作谨慎，尽量不要加仓进场：1 黄线迅速下穿白线，放量。
                #   2 黄白线交织，缩大量。3 白线迅速上穿黄线：缩量，4 接近大指数级别压力位附近」
                # 与 A5 同一组腿型（日内做T买腿=加仓/回补语义），命中则**拦**；
                # 交叉类条件需 spread 历史，样本不足时不触发（fail-open，绝不误拦）。
                try:
                    from app.services.wolf_gap_open import block_reason as _go_block
                    from app.services.wolf_trade_window import applies_to as _tw_applies2
                    if _tw_applies2(cond.get("trigger_kind")):
                        if not _go_caution["computed"]:      # 每轮只算一次（内含多源取数）
                            _go_caution["computed"] = True
                            _go_caution["reason"] = _go_block()
                        if _go_caution["reason"]:
                            _ck = (symbol, "gapcaution", datetime.now().strftime('%Y%m%d'))
                            if _ck not in _STOP_HOLD_WARNED:
                                _STOP_HOLD_WARNED.add(_ck)
                                print(f"[TMonitor] 谨慎条件命中，跳过加仓/回补买腿 {symbol}"
                                      f"({cond.get('trigger_kind')}): {_go_caution['reason']}", flush=True)
                            continue
                except Exception as _goe:
                    print(f"[TMonitor] 高低开/缺口判定异常(放行) {symbol}: {str(_goe)[:80]}", flush=True)
            quote = quotes.get(_normalize_symbol(symbol))
            if not quote or not quote.get("current"):
                continue
            # 无底仓预拦截：
            #   卖腿：sellable=0 不触发（T+0 当日买回次日才可卖，卖腿必须有券）
            #   买腿：迭代#58（用户需求）——无底仓放行触发，等价"条件单建仓"：
            #     低吸/custom(buy) 命中后按建仓规模买入开仓（量/风控由网关+建仓规模兜底）；
            #     14:45 后禁新开仓仍由时段门拦截。有底仓时仍是加仓语义（底仓 30%）。
            # 狼大持续腿触发冷却（2026-09-02）：分时T出/黄线等表达式腿命中后
            # 5 分钟不再重复写触发（非消费式持续腿防刷；分时形态天然低频，黄线靠此限频）
            if _is_wolf_t_condition(cond):
                _lt = cond.get("last_triggered_at") or ""
                if _lt:
                    try:
                        from datetime import datetime as _dt
                        # 2026-09-25 修 bug（账本 §9.124）：PG 的 timestamp 列经 psycopg2 取出来是
                        #   **datetime 对象** ⇒ `strptime(datetime)` 抛 TypeError ⇒ 被 except 吞 ⇒
                        #   **5 分钟去重冷却在回测里从未生效** ✗（`[GATE-ALARM] … t_monitor:line2252
                        #   TypeError: strptime() argument 1 must be str` 出现 176 次 ✓ 被新报警机制抓到 ✓）
                        _last = _lt if isinstance(_lt, _dt) else _dt.strptime(str(_lt), "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - _last).total_seconds() < 300:
                            continue
                    except Exception as _eN16:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line2252", _eN16)
                        pass
            try:
                pos_item = (ledger or {}).get(symbol) or {}
                cond_kind = cond.get("trigger_kind", "low_buy")
                if cond_kind in ("high_sell_then_buy_back", "high_sell") \
                        and int(pos_item.get("sellable", 0) or 0) <= 0:
                    continue
            except Exception as _eN17:
                # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                from app.services import gate_alarm as _gaNote
                _gaNote.note("t_monitor:line2260", _eN17)
                pass
            try:
                # 2026-09-09 根治: 条件缺当日换手基准 → 现场补算一次(当日缓存), 0.5% 仅最后保险
                self._ensure_benchmark_profile(cond)
                # 止损前置检查（每标的每轮一次：现价 ≤ 止损价 且 当日未止损过）
                self._check_stop_loss(symbol, quote, ledger)
                # 构建该标的字段快照（供表达式求值）
                snapshot = self._build_snapshot(cond, quote, regime_state)
                _hit_c = self._evaluate_condition(cond, quote, regime_state, snapshot)
                # 2026-09-26 定向调试（`WOLF_DEBUG_COND`，默认空 = 关闭 ✓，生产零影响 ✓）：
                #   命中/未命中都打印关键字段 ⇒ 用来分清"**根本没被评估**"还是"评估了但字段不满足" ✓
                _dbg_c = ""
                try:
                    _dbg_c = str(os.getenv("WOLF_DEBUG_COND", "") or "").strip()
                except Exception:
                    _dbg_c = ""
                if _dbg_c and _dbg_c in str(cond.get("trigger_kind") or ""):
                    try:
                        _sn = snapshot or {}
                        _q = _sn.get("quote") or {}
                        print("[DBG_COND] %s %s hit=%s dip_prev_low=%s prev_low_dist=%s m5_dump=%s cur=%s avg=%s" % (
                            cond.get("symbol"), cond.get("trigger_kind"), bool(_hit_c),
                            _q.get("dip_prev_low"), _q.get("prev_low_dist_pct"), _q.get("m5_dump"),
                            _q.get("current"), _q.get("average")), flush=True)
                    except Exception as _eD:
                        print("[DBG_COND] 打印失败: %s" % str(_eD)[:60], flush=True)
                if _hit_c:
                    # ── G3「卖出后删票」在**买入执行口**再查一次（2026-09-21）──────────────────
                    #   语料 2025-02-06「卖出然后删票」/ 2025-04-03「破之前新低的直接删票」。
                    #   为什么必须在这里查：布腿时的候选域过滤只能挡**新挂的腿**；实测天津普林/苏州科达
                    #   在 T3 里的买入，腿是**禁令之前**就挂好的（0122/0123/0128 成交的腿挂于 0112–0116），
                    #   所以「卖出后删票」要真正生效，必须在**腿触发那一刻**再判一次。
                    #   生产与回测走同一段代码 ⇒ 一处修两边都生效。开关 WOLF_TICKET_BAN_FIX（默认 0）。
                    self._write_trigger(cond, quote, regime_state, snapshot, ledger)
                    written += 1
            except Exception as e:
                print(f"[TMonitor] 条件评估异常 {symbol}: {e}")
        self._status["triggers_written"] += written

    def _ensure_benchmark_profile(self, cond: Dict[str, Any]) -> None:
        """2026-09-09 根治: cond 缺当日换手基准时现场补算一次(当日缓存), 0.5% 仅最后保险。
        与 arm()/跨日结转同函数 compute_turnover_profile；写回 DB 供后续轮与次日结转复用。"""
        sym = cond.get("symbol")
        if not sym:
            return
        today = datetime.now().strftime("%Y%m%d")
        cache = getattr(self, "_bench_cache", None)
        if cache is None:
            cache = self._bench_cache = {}
        if cache.get(sym) == today:
            return
        cache[sym] = today          # 当日只尝试一次（失败保留 0.5% 兜底, 防每轮重拉）
        try:
            prof = cond.get("benchmark_turnover_profile") or {}
            if isinstance(prof, str):
                import json as _json
                prof = _json.loads(prof) if prof else {}
            ct = str(prof.get("computed_at") or "")[:10].replace("-", "")
            if prof.get("same_minute_avg") and ct == today:
                return
        except Exception as _eN18:
            # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
            from app.services import gate_alarm as _gaNote
            _gaNote.note("t_monitor:line2303", _eN18)
            pass
        try:
            from app.services.t_turnover_profile import compute_turnover_profile
            np_ = compute_turnover_profile(sym)
        except Exception as e:
            print(f"[TMonitor] 基准补算失败 {sym}: {e}")
            np_ = None
        if not np_:
            return
        cond["benchmark_turnover_profile"] = np_
        try:
            import app.services.t_db as _tdb
            persist = {k: v for k, v in cond.items()
                       if k not in ("id", "created_at", "armed_at",
                                    "last_triggered_at", "trigger_count_today")}
            persist["status"] = "active"
            persist.setdefault("armed", 1)
            _tdb.upsert_condition(persist)
        except Exception as e:
            print(f"[TMonitor] 基准写库失败 {sym}: {e}")

    def _index_intraday_dd(self) -> float:
        """上证指数当日盘中最大回撤%（从日高逐bar更新；30s TTL 缓存）。
        狼大正T买点(1-12『利用盘中大盘带下来的机会做正T』)：
        dd∈[2%,3%) → 低吸信号；dd≥3% 系统性风险不买（验证 docs/zt-dip-verification-report.md）。"""
        now = time.time()
        if now - _index_dd_cache["at"] < 30:
            return _index_dd_cache["value"]
        try:
            from app.services.t_data_sources import fetch_tencent_mkline
            bars = fetch_tencent_mkline("sh000001", freq="m5", count=60)
            today8 = datetime.now().strftime("%Y%m%d")
            today_bars = [b for b in (bars or []) if _bar_date8(b.get("time")) == today8]
            if len(today_bars) < 10:
                _index_dd_cache["at"] = now; _index_dd_cache["value"] = 0.0
                return 0.0
            dh = 0.0; mdd = 0.0
            for b in sorted(today_bars, key=lambda x: str(x.get("time"))):
                hi = float(b.get("high") or 0); cl = float(b.get("close") or 0)
                if hi > dh: dh = hi
                if dh > 0 and cl > 0:
                    mdd = max(mdd, (dh - cl) / dh * 100)
            _index_dd_cache["at"] = now; _index_dd_cache["value"] = round(mdd, 3)
            return round(mdd, 3)
        except Exception as e:
            print(f"[TMonitor] 指数盘中回撤计算失败: {e}")
            return 0.0

    def _stock_m5_dump(self, symbol: str) -> float:
        """**个股**最新 5min 单根跌幅%（较前一根收盘；急杀为负；30s TTL/票 ✓）。

        依据（账本 §9.175，9 主题主板逐日）：把"急杀"按**大盘×个股**切开后——
          · **大盘与个股同时急杀**：+5 中位 **+1.57%**／为正 **61%** ✓✓（唯一正期望 ✓）
          · 只有**个股**急杀：−0.51%／47% ✗
          · 只有**大盘**急杀：−0.85%／42% ✗（与语料「指数跳水…不要想着抄底」✓ 一致）
        语料两层（2025-06-05）：「**主线板块筑底行情**…急杀可以买」（大盘/板块级 ✓）
          ＋「**板块/个股急杀**…慢慢买」（个股级 ✓）⇒ **合取**才对齐 ✓
        """
        try:
            now = time.time()
            key = str(symbol or "").upper()
            ent = _m5_stock_dump_cache.get(key)
            if ent and now - ent["at"] < 30:
                return ent["value"]
            from app.services.t_data_sources import fetch_tencent_mkline
            pre = "sh" if key.startswith("SH") else "sz"
            bars = fetch_tencent_mkline(pre + key[2:], freq="m5", count=60)
            bars = sorted(bars or [], key=lambda b: str(b.get("time")))
            dump = 0.0
            if len(bars) >= 2:
                c0 = float(bars[-1].get("close") or 0)
                c1 = float(bars[-2].get("close") or 0)
                if c0 > 0 and c1 > 0:
                    dump = (c0 - c1) / c1 * 100
            _m5_stock_dump_cache[key] = {"at": now, "value": round(dump, 3)}
            return round(dump, 3)
        except Exception as e:
            from app.services import gate_alarm as _ga
            _ga.note("t_monitor:_stock_m5_dump", e)
            return 0.0

    def _index_m5_dump(self) -> float:
        """上证指数最新5min单根跌幅%（较前一根收盘；C档急杀信号≥0.4；30s TTL）。
        狼大'盘中带下来'的分时形态——验证 backtest_zt_signal_compare: 单根>=0.4% 16天 T+1+0.82%。"""
        now = time.time()
        if now - _m5_dump_cache["at"] < 30:
            return _m5_dump_cache["value"]
        try:
            from app.services.t_data_sources import fetch_tencent_mkline
            bars = fetch_tencent_mkline("sh000001", freq="m5", count=60)
            bars = sorted(bars or [], key=lambda b: str(b.get("time")))
            dump = 0.0
            if len(bars) >= 2:
                c0 = float(bars[-1].get("close") or 0)
                c1 = float(bars[-2].get("close") or 0)
                if c0 > 0 and c1 > 0:
                    dump = (c0 - c1) / c1 * 100
            _m5_dump_cache["at"] = now; _m5_dump_cache["value"] = round(dump, 3)
            return round(dump, 3)
        except Exception as e:
            print(f"[TMonitor] 指数急杀计算失败: {e}")
            return 0.0

    def _stock_prev_low(self, symbol: str) -> float:
        """**前一交易日 5min 最低价**（价格，不是布尔 ✓；30s TTL/票 ✓）。

        用途（账本 §9.182）：埋伏腿要判「**回踩到位但不破**前低」✓
          · 语料（2026-02-02）：「**大盘没过前低是前提**，观察板块/个股**也没低于前低**是基础条件」✓
          · 数据：`不破前低 + 50 日` 中位 **+6.29%／为正 63%／回撤 −6.9%** ✓
            vs `触及前低`（正T 挂单口径 ✗）**+0.46%／回撤 −11.7%** ✗ ⇒ 差 5~8 倍
        （注意：他 2025-03-06「**挂前一天的低点**，能买进去就做正T」✓ 是**正T 挂单**口径 ⇒
          对应 `quote.dip_prev_low`（触及 ✓），**不是**埋伏腿该用的那条 ✓）
        """
        try:
            now = time.time()
            key = str(symbol or "").upper()
            ent = _prev_low_px_cache.get(key)
            if ent and now - ent["at"] < 30:
                return float(ent["value"] or 0)
            from app.services.t_data_sources import fetch_minute_bars
            bars = fetch_minute_bars(symbol, freq="m5", count=320) or []
            _today = datetime.now().strftime("%Y%m%d")
            groups: Dict[str, list] = {}
            for b in sorted(bars, key=lambda x: str(x.get("time") or x.get("trade_time"))):
                t = str(b.get("time") or b.get("trade_time") or "")
                d8 = t[:10].replace("-", "")
                if d8 and d8 != _today:
                    groups.setdefault(d8, []).append(b)
            val = 0.0
            if groups:
                last = groups[sorted(groups)[-1]]
                lows = [float(x.get("low") or 0) for x in last]
                lows = [x for x in lows if x > 0]
                if lows:
                    val = min(lows)
            _prev_low_px_cache[key] = {"at": now, "value": val}
            return val
        except Exception as _e:
            from app.services import gate_alarm as _ga
            _ga.note("t_monitor:_stock_prev_low", _e)
            return 0.0

    # ── 账本 §9.498 ✓：**他的线组买点（buy_255）的字段产出** ✓ ──────────────────────
    #   他的原话：「好票跌到事先画好的线（**13/34/60/144**）→ 提前挂单买、与指数无关」✓
    #   量化（§9.495 ✓）：线组买点 T+5 均值 **+1.29%**／中位 +0.73%／左尾 5.0%
    #     vs 我们 254 的 **−0.11%／−0.45%／8.3%** ✓
    #   ⚠️ **无前视** ✓：线只用「**截至前一交易日**」的收盘 ✓；"当天"取 `DATA_DIR` 目录名（回放日 ✓），
    #      取不到才退 `datetime.now()`（生产 ✓）
    def _daily_closes_before_today(self, symbol: str):
        """截至**前一交易日**的日线收盘（relay ✓；缓存 300s ✓；失败 → [] ✓ fail-closed）。"""
        now = time.time()
        key = str(symbol)
        hit = _daily_closes_cache.get(key)
        if hit and (now - hit[0] < 300):
            return hit[1]
        out = []
        try:
            import os as _os, sys as _sys
            _core = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "core")
            if _core not in _sys.path:
                _sys.path.insert(0, _core)
            from tushare_relay import relay_items          # 账本 §9.498：日线走 relay ✓（用户指定）
            _s = str(symbol).strip().upper()
            _code = ("%s.%s" % (_s[2:], _s[:2])) if (len(_s) >= 8 and _s[:2] in ("SH", "SZ", "BJ")) else _s
            _b = _os.path.basename(str(_os.getenv("DATA_DIR") or "").strip())
            _today = _b if (len(_b) == 8 and _b.isdigit()) else datetime.now().strftime("%Y%m%d")
            import datetime as _dt
            _start = (_dt.datetime.strptime(_today, "%Y%m%d") - _dt.timedelta(days=420)).strftime("%Y%m%d")
            _f, _items = relay_items("daily", ts_code=_code, start_date=_start, end_date=_today)
            _fi = {k: i for i, k in enumerate(_f or [])}
            _d_i, _c_i = _fi.get("trade_date"), _fi.get("close")
            if _d_i is not None and _c_i is not None:
                out = [float(r[_c_i]) for r in sorted(_items, key=lambda r: str(r[_d_i])) if str(r[_d_i]) < _today]
        except Exception:
            out = []
        _daily_closes_cache[key] = (now, out)
        return out

    def _ma_lines(self, symbol: str) -> dict:
        """MA13/34/60/144（截至前一交易日 ✓）。数据不足 → 该根为 0.0（不可用 ✓）。"""
        cl = self._daily_closes_before_today(symbol) or []
        out = {}
        for w in (13, 34, 60, 144):
            out[w] = (sum(cl[-w:]) / float(w)) if len(cl) >= w else 0.0
        return out

    def _stock_dip_ma_line(self, symbol: str, low: float, cur: float) -> bool:
        """当日最低触到某根线（13/34/60/144 ✓）且**收回线上** ⇒ True ✓（配 vol_ratio ≤ 0.9 ✓）。"""
        try:
            if not (low > 0 and cur > 0):
                return False
            for w, m in (self._ma_lines(symbol) or {}).items():
                if m and low <= m and cur >= m:
                    return True
        except Exception:
            return False
        return False

    def _stock_dip_prev_low(self, symbol: str) -> bool:
        """个股当日5min最低 ≤ 前一交易日5min最低×(1+tol)（A档：触及/跌破前日低点）。

        狼大 2025-03-06（逐字）:「就是**挂前一天的低点** 能买进去就做正T 买不进去证明涨了 不用动 主升浪的做法」
        → 他的话是"挂**前一天的低点**"本身，即 **tol = 0**。
        `WOLF_DIP_PREVLOW_TOL`（2026-09-15 参数对齐新增）：**默认 0.0 = 语料值**；
        历史值是 0.005（自设容差，参数总账 §2-C1）。置 0.005 可回退旧行为。
        配 vol_ratio<=0.9 缩量（`BUY_254_EXPR`）。
        fetch_minute_bars m5 count=320 ≈ 6.5 交易日，取最近非今日组的 min low。30s TTL。"""
        now = time.time()
        key = symbol
        if (now - _prev_low_cache.get("at", 0) < 30 and _prev_low_cache.get("sym") == key
                and _prev_low_cache.get("tol") == DIP_PREVLOW_TOL):
            return _prev_low_cache.get("value", False)
        try:
            from app.services.t_data_sources import fetch_minute_bars
            bars = fetch_minute_bars(symbol, freq="m5", count=320) or []
            if len(bars) < 100:
                return False
            today = datetime.now().strftime("%Y%m%d")
            by_day = {}
            for b in sorted(bars, key=lambda x: str(x.get("time") or x.get("trade_time"))):
                # 时间戳为 12 位 YYYYMMDDHHMM：取前 8 位得到交易日期（旧 [:10] 会带小时导致
                # today 永远匹配不上、前日分组错乱 → A档 dip_prev_low/254 恒 False, 2026-09-07 修复）
                t = str(b.get("time") or b.get("trade_time"))[:8]
                by_day.setdefault(t, []).append(b)
            days = sorted(by_day.keys())
            if len(days) < 2:
                return False
            today_low = min(float(b["low"]) for b in by_day.get(today, [by_day[days[-1]][0]]))
            prev_day = days[-2] if today in days else days[-1]
            prev_low = min(float(b["low"]) for b in by_day[prev_day])
            ok = prev_low > 0 and today_low <= prev_low * (1.0 + DIP_PREVLOW_TOL)
            # C1 影子（2026-09-15）：记录"按语料值 tol=0 不触发、但按历史自设 tol=0.005 会触发"的情形
            #   → 灰度期量化"对齐后会少成交多少/少成交的是哪些票"，不改真实行为。
            try:
                if _dip_shadow_enabled() and DIP_PREVLOW_TOL > CORPUS_DIP_TOL:
                    # 当前用宽档（legacy 0.005）而语料值是 0.0 → 记录"按语料值不会成交"的那些票
                    strict = prev_low > 0 and today_low <= prev_low * (1.0 + CORPUS_DIP_TOL)
                    if ok and not strict:
                        _dip_shadow_record(symbol, prev_low, today_low)
            except Exception as _se:
                print("[TMonitor] dip 影子记录失败: %s" % str(_se)[:60])
            _prev_low_cache.update({"at": now, "sym": key, "value": ok, "tol": DIP_PREVLOW_TOL})
            return ok
        except Exception as e:
            print(f"[TMonitor] 前日低点计算失败 {symbol}: {e}")
            return False

    def _build_snapshot(self, cond: Dict[str, Any], quote: dict,
                        regime_state: dict) -> Dict[str, Any]:
        """构建字段快照（Agent 自由表达式可引用的全部字段）。

        字段注册表见 t_expr.FIELD_REGISTRY；此处按需采集（quote 实时 + 量比 + 分钟线衍生 + regime + 持仓 + 指数）。
        """
        symbol = cond["symbol"]
        snapshot: Dict[str, Any] = {}

        # quote.*（腾讯 qt 实时）
        _cur = float(quote.get("current", 0) or 0)
        _avg = float(quote.get("average", 0) or 0)
        # 波段支撑/压力位（步骤① 2026-09-08, support_resistance.compute_levels, 模块10min TTL缓存）
        _sup = []
        _res = []
        try:
            from app.services.support_resistance import compute_levels as _sr_compute
            _sr = _sr_compute(symbol)
            _sup = sorted([float(x["price"]) for x in _sr.get("support", []) if x.get("price")])
            _res = sorted([float(x["price"]) for x in _sr.get("resistance", []) if x.get("price")])
        except Exception as _sre:
            print(f"[TMonitor] 支撑/压力字段失败 {symbol}: {str(_sre)[:80]}")
        _s1 = _sup[-1] if _sup else 0.0
        _s2 = _sup[-2] if len(_sup) >= 2 else _s1
        _r1 = _res[0] if _res else 0.0
        _r2 = _res[1] if len(_res) >= 2 else _r1
        # ── 狼大口径的准备量（MA13／MA34／量能／"首次"判定 ✓；账本 §9.263）──
        _ma13_v = _ma34_v = 0.0
        _vol_ok_v = _first_break_v = False
        try:
            _pdk = self._prev_daily(symbol, 34) or []
            # 注意：`_prev_daily` 返回**截至上一交易日**的序列 ✓（不含当日 ✓）
            #   ⇒ MA 与"昨收"都从它算 ⇒ "首次跌破"＝**今日跌破、昨日还在上方** ✓
            if len(_pdk) >= 14:
                _cl = [float(x.get("close") or 0) for x in _pdk]
                _vl = [float(x.get("vol") or 0) for x in _pdk]
                _ma13_v = sum(_cl[-13:]) / 13.0 if len(_cl) >= 13 else 0.0
                _ma34_v = sum(_cl[-34:]) / 34.0 if len(_cl) >= 34 else 0.0
                _pma13 = sum(_cl[-14:-1]) / 13.0 if len(_cl) >= 14 else 0.0
                _pma34 = sum(_cl[-35:-1]) / 34.0 if len(_cl) >= 35 else 0.0
                _pc = _cl[-1]
                _v5 = (sum(_vl[-5:]) / 5.0) if len(_vl) >= 5 else 0.0
                _v1 = float(quote.get("vol", 0) or 0) or _vl[-1]
                _vol_ok_v = bool(_v5 > 0 and _v1 > _v5 * 1.2)          # 「放量跌破确认」✓
                _first_break_v = bool(
                    (_ma13_v > 0 and _cur <= _ma13_v and _pc > _pma13) or
                    (_ma34_v > 0 and _cur <= _ma34_v and _pc > _pma34))  # 「首次」✓
        except Exception:
            pass
        snapshot["quote"] = {
            "current": _cur,
            "open": float(quote.get("open", 0) or 0),
            "high": float(quote.get("high", 0) or 0),
            "low": float(quote.get("low", 0) or 0),
            "pre_close": float(quote.get("pre_close", 0) or 0),
            "change_pct": float(quote.get("change_pct", 0) or 0),
            "turnover_rate": float(quote.get("turnover_rate", 0) or 0),
            "amplitude": float(quote.get("amplitude", 0) or 0),
            "vol": float(quote.get("vol", 0) or 0),
            "amount": float(quote.get("amount", 0) or 0),
            "average": _avg,
            # 分时黄线跌破（狼大8-04『绝对不能破的点就是日均线那条黄线 一旦突发跌破直接走』）
            # ⚠️ 账本 §9.427（用户「收敛到 B5」）：**黄线离场只在尾盘半小时 ∧ 破当日新低生效** ✓
            #   量化（3,482 票日）：全天候破均价 ⇒ 触发 97% ✓、卖点比当日收盘低 0.45% ✗；
            #   B5 ⇒ 触发 29% ✓、仅低 0.10% ✓（卖早降 78% ✓、churn 降 70% ✓）；
            #   他 2026-08-04 原话「在这个半小时内有个绝对不能破的点…一旦突发跌破直接走」✓
            #   开关 `WOLF_VWAP_SELL_B5`（**库内默认 0 ＝ 关 ⇒ 生产逐字不变** ✓）
            "vwap_break": bool(_avg > 0 and _cur < _avg and (lambda _ok: _ok)(
                (str(os.getenv("WOLF_VWAP_SELL_B5", "0")).strip().lower() not in ("1", "true", "yes", "on"))
                or (datetime.now().strftime("%H:%M") >= "14:30"
                    and float(quote.get("low", 0) or 0) > 0
                    and _cur <= float(quote.get("low", 0) or 0) * 1.001))),
            "dip_prev_low": self._stock_dip_prev_low(symbol),
            # 账本 §9.498 ✓：**他的线组买点（buy_255）字段**（触 13/34/60/144 ＋ 缩量 ✓）
            "ma13": (self._ma_lines(symbol) or {}).get(13, 0.0),
            "ma34": (self._ma_lines(symbol) or {}).get(34, 0.0),
            "ma60": (self._ma_lines(symbol) or {}).get(60, 0.0),
            "ma144": (self._ma_lines(symbol) or {}).get(144, 0.0),
            "dip_ma_line": self._stock_dip_ma_line(symbol, float(quote.get("low", 0) or 0),
                                                   float(quote.get("current", 0) or 0)),
            # 个股 5min 单根跌幅%（急杀为负 ✓）—— 与 index.m5_dump 做**合取**用（账本 §9.175）
            "m5_dump": self._stock_m5_dump(symbol),
            # 现价距**前一交易日 5min 最低**的百分比%（>0 = 未破 ✓；≈0~1 = 回踩到位 ✓）—— 埋伏腿触发用（§9.182）
            "prev_low_dist_pct": (lambda _pl, _cur: (round((_cur / _pl - 1) * 100, 3) if (_pl > 0 and _cur > 0) else None))(
                self._stock_prev_low(symbol), float(quote.get("current", 0) or 0)),
            # S3 删除(2026-09-10): 原"动态回撤保护 trail_break"(现价≤当日高点×(1-振幅自适应阈值))。
            # 狼大不用百分比移动止损(他用"3-5点兑现"与"收盘破位"), 属审计 §5.2 认定的自造机制 → 已删除。
            # 字段保留但恒 False, 避免存量条件(t_triggers/t_conditions 中引用 quote.trail_break 的表达式)求值报错。
            "trail_break": False,
            "support_l1": _s1,
            "support_l2": _s2,
            "resistance_l1": _r1,
            "resistance_l2": _r2,
            # ── `break_support` 的**两套口径**（账本 §9.263）──────────────────────────
            #   旧（自造 ✗）：现价 ≤「现价下方**最近**支撑」（20/60 日低点／局部极值／MA20/60 ✓）
            #     ⇒ 比狼大**更近** ✗ ⇒ 轻微回落就触发（实测 002156 卖飞 ✓）
            #   新（**他的原话** ✓，开关 `WOLF_BREAK_SUPPORT_TREND=1` ✓）：
            #     「**可以不追高 但是卖点只有跌破支撑位**」✓
            #     「**3日5日最多10日…机构单子一般都是挂在 10日 13日 34**」✓ ⇒ 取 **MA13／MA34** ✓
            #     「如果这个位置**放量跌破确认**」✓ ＋「**缩量到缺口支撑位的时候不割肉**」✓
            #     ⇒ **首次跌破 MA13 或 MA34** ∧ **放量（>1.2×5 日均量）** ✓
            "ma13": _ma13_v,
            "ma34": _ma34_v,
            "vol_up_ok": bool(_vol_ok_v),
            "break_support": (bool(_first_break_v and _vol_ok_v)
                              if str(os.getenv("WOLF_BREAK_SUPPORT_TREND", "0")).strip().lower()
                              in ("1", "true", "yes", "on")
                              else bool(_s1 > 0 and _cur <= _s1)),
        }
        # ── A10 BOLL（2026-09-11, wolf_boll_levels）──
        # 他 2025-04-15「个股在区间震荡的时候碰到了自己各种压力位，比如均线或BOLL上轨」；
        # 2026-04-29「触及上方大级别压力位(如BOLL上轨)时，逢高卖出部分底仓、锁定利润」。
        # 带 5 分钟缓存 + 失败即 None（不影响其它字段）。
        try:
            from app.services.wolf_boll_levels import levels as _boll_levels, touch_upper as _boll_touch
            _bl = _boll_levels(symbol)
            if _bl:
                snapshot["quote"]["boll_upper"] = float(_bl["upper"])
                snapshot["quote"]["boll_mid"] = float(_bl["mid"])
                snapshot["quote"]["boll_lower"] = float(_bl["lower"])
                snapshot["quote"]["boll_upper_touch"] = bool(
                    _boll_touch(_bl, _cur, float(quote.get("high", 0) or 0)))
            else:
                snapshot["quote"].update({"boll_upper": 0.0, "boll_mid": 0.0,
                                          "boll_lower": 0.0, "boll_upper_touch": False})
        except Exception as _be:
            print(f"[TMonitor] BOLL 计算异常(忽略) {symbol}: {str(_be)[:70]}", flush=True)
            snapshot["quote"].update({"boll_upper": 0.0, "boll_mid": 0.0,
                                      "boll_lower": 0.0, "boll_upper_touch": False})
        # vol_ratio（盘中量比归一）
        vr = self._calc_volume_ratio(cond, quote)
        snapshot["vol_ratio"] = vr if vr is not None else 0.0
        # 量价关系派生字段（放量/缩量/上涨/下跌/放量上涨/缩量下跌/跌到企稳等，贴近交易语言）
        snapshot["quote"].update(self._build_vol_price(snapshot["quote"], vr))
        # minute.*（分钟线衍生，低频）
        snapshot["minute"] = self._build_minute_snapshot(symbol, quote)
        # 企稳引用（minute.m1.bounce → quote.stabilised）
        m1_bounce = bool(snapshot.get("minute", {}).get("m1", {}).get("bounce", False))
        snapshot["quote"]["stabilised"] = m1_bounce
        # regime.*
        snapshot["regime"] = {
            "state": regime_state.get("regime", "ACTIVE"),
            "gate_low_buy": regime_state.get("gate_low_buy", "ALLOWED"),
            "gate_high_sell": regime_state.get("gate_high_sell", "ALLOWED"),
            "interpret_sign": int(regime_state.get("interpret_sign", 1)),
        }
        # position.*（监控账户持仓）
        snapshot["position"] = self._build_position_snapshot(
            symbol, cond.get("account_id", T_MONITOR_ACCOUNT))
        # index.*（指数实时，复用本轮 regime 已拉取的报价 + 盘中回撤=正T买点信号）
        snapshot["index"] = {
            "hs300_drop": float(regime_state.get("index_drop", 0) or 0),
            "sh_drop": 0.0,
            "sz_drop": 0.0,
            "intraday_dd": self._index_intraday_dd(),
            "m5_dump": self._index_m5_dump(),
        }
        # 黄白线（A4, 2026-09-11）：日内强弱总开关 —— 黄(等权)在上→做T成功率高；白(加权)在上→减少做T。
        # 狼大 2025-04-15 条件3。取数在新浪(约16s/次) → 模块内 TTL 缓存 + fail-open，取不到给 None。
        try:
            from app.services.wolf_index_breadth import snapshot as _hb_snap
            _hb = _hb_snap() or {}
            snapshot["index"].update({
                "huang_bai_spread": _hb.get("huang_bai_spread"),
                "huang_bai_side": _hb.get("huang_bai_side"),
                "huang_bai_equal": _hb.get("huang_bai_equal"),
                "huang_bai_index": _hb.get("huang_bai_index"),
                "bai_on_top": (_hb.get("huang_bai_side") == "bai"),
            })
        except Exception as _hbe:
            print(f"[TMonitor] 黄白线取数异常(忽略): {str(_hbe)[:80]}")
        # tech.*（技术指标：KDJ/MACD/RSI/MA，复用 get_realtime_indicators，带缓存）
        snapshot["tech"] = self._build_tech_snapshot(symbol, snapshot["quote"])
        # external.*（外部风险字段组）—— 2026-09-16 默认**不再采集**（用户拍板"下掉"）
        snapshot["external"] = _external_snapshot_fields()
        return snapshot

    def _build_vol_price(self, q: Dict[str, Any], vol_ratio: float) -> Dict[str, Any]:
        """量价关系派生字段（贴近交易语言，Agent 可直接用单字段表达复合语义）。

        - volume_expand: 放量（量比 ≥ 1.5）
        - volume_shrink: 缩量（量比 ≤ 0.7）
        - price_up: 上涨（涨跌幅 > 0）
        - price_down: 下跌（涨跌幅 < 0）
        - up_with_volume: 放量上涨（价涨 ∧ 量比 ≥ 1.5）
        - up_with_low_volume: 缩量上涨（价涨 ∧ 量比 ≤ 0.7）
        - down_with_volume: 放量下跌（价跌 ∧ 量比 ≥ 1.5）
        - down_with_low_volume: 缩量下跌（价跌 ∧ 量比 ≤ 0.7）
        - panic_drop: 恐慌放量下跌（价跌超 2% ∧ 量比 ≥ 2.0）
        - near_day_low: 接近日内低点（现价 ≤ 日内最低 × 1.01）
        - stabilised: 企稳（分时不再创新低，见 minute.m1.bounce，此处引用）
        """
        current = float(q.get("current", 0) or 0)
        pre_close = float(q.get("pre_close", 0) or 0)
        day_low = float(q.get("low", 0) or 0)
        change_pct = float(q.get("change_pct", 0) or 0)
        v = vol_ratio if vol_ratio is not None else 0.0
        up = change_pct > 0
        down = change_pct < 0
        expand = v >= 1.5
        shrink = v <= 0.7
        return {
            "volume_expand": expand,
            "volume_shrink": shrink,
            "price_up": up,
            "price_down": down,
            "up_with_volume": up and expand,
            "up_with_low_volume": up and shrink,
            "down_with_volume": down and expand,
            "down_with_low_volume": down and shrink,
            "panic_drop": change_pct <= -2.0 and v >= 2.0,
            "near_day_low": (current > 0 and day_low > 0 and current <= day_low * 1.01),
            "stabilised": False,  # 由 minute.m1.bounce 提供（此处占位，快照合并时覆盖）
        }

    def _build_tech_snapshot(self, symbol: str, quote: dict) -> Dict[str, Any]:
        """技术指标字段（基于分钟线数据自算：MACD/KDJ/RSI/MA，日内实时、无 Tushare 依赖）。

        分钟级技术指标对做T触发更贴近（日内短线），且复用已有三源分钟线数据；
        字段名对齐 t_expr.FIELD_REGISTRY 的 tech.*（macd_dif/dea/bar、kdj_k/d/j、rsi_6/12/24、ma5/10/20/60）。
        """
        result = {
            "ma5": 0.0, "ma10": 0.0, "ma20": 0.0, "ma60": 0.0,
            "macd_dif": 0.0, "macd_dea": 0.0, "macd_bar": 0.0,
            "macd_golden_cross": False,
            "kdj_k": 50.0, "kdj_d": 50.0, "kdj_j": 50.0,
            "kdj_golden_cross": False, "kdj_overbought": False,
            "rsi_6": 50.0, "rsi_12": 50.0, "rsi_24": 50.0,
            "rsi_overbought": False, "rsi_oversold": False,
            "above_ma5": False, "above_ma20": False,
        }
        try:
            from app.services.t_data_sources import fetch_minute_bars
            m5 = fetch_minute_bars(symbol, freq="m5", count=120)
            if not m5 or len(m5) < 9:
                return result  # 分钟线不足，用默认值（保守）
            closes = [float(b["close"]) for b in m5]
            highs = [float(b["high"]) for b in m5]
            lows = [float(b["low"]) for b in m5]
            current = float(quote.get("current", 0) or 0)

            # MA
            result["ma5"] = _sma(closes, 5)
            result["ma10"] = _sma(closes, 10)
            result["ma20"] = _sma(closes, 20)
            result["ma60"] = _sma(closes, 60)
            # MACD (12,26,9)
            dif, dea, bar = _calc_macd_from_closes(closes)
            result["macd_dif"] = dif
            result["macd_dea"] = dea
            result["macd_bar"] = bar
            result["macd_golden_cross"] = dif > dea
            # KDJ (9,3,3) — 用最近9根高低 + 当前价
            k, d, j = _calc_kdj_from_bars(highs, lows, closes, current)
            result["kdj_k"], result["kdj_d"], result["kdj_j"] = k, d, j
            result["kdj_golden_cross"] = k > d
            result["kdj_overbought"] = j > 100 or k > 80
            # RSI
            result["rsi_6"] = _calc_rsi(closes, 6)
            result["rsi_12"] = _calc_rsi(closes, 12)
            result["rsi_24"] = _calc_rsi(closes, 24)
            result["rsi_overbought"] = result["rsi_6"] >= 80
            result["rsi_oversold"] = result["rsi_6"] <= 20
            result["above_ma5"] = current > result["ma5"] > 0
            result["above_ma20"] = current > result["ma20"] > 0
        except Exception as e:
            print(f"[TMonitor] 技术指标快照失败 {symbol}: {e}")
        return result

    def _build_minute_snapshot(self, symbol: str, quote: dict) -> Dict[str, Any]:
        """分钟线衍生字段（m1/m5）+ 做T信号(T1缩转放/分时T出)。取不到时给保守默认，避免误触发。"""
        result = {"m1": {}, "m5": {}}
        try:
            from app.services.t_data_sources import fetch_minute_bars
            m1 = fetch_minute_bars(symbol, freq="m1", count=120)
            m5 = fetch_minute_bars(symbol, freq="m5", count=60)
            if m1:
                today8 = datetime.now().strftime("%Y%m%d")
                today_lows = [b["low"] for b in m1 if _bar_date8(b.get("time")) == today8]
                result["m1"] = {
                    "low_today": min(today_lows) if today_lows else 0.0,
                    "last_close": float(m1[-1]["close"]),
                    "bounce": self._stabilize_not_new_low(symbol, float(quote.get("current", 0) or 0)),
                }
            if m5:
                closes = [float(b["close"]) for b in m5]
                result["m5"] = {
                    "last_close": closes[-1] if closes else 0.0,
                    "ma5": _sma(closes, 5),
                    "ma10": _sma(closes, 10),
                    "ma20": _sma(closes, 20),
                }
                # ── 做T信号（狼大体系, t_signal 逻辑）──
                t1, t_sell = _t_signals_from_m5(m5)
                result["m5"]["t1_shrink_expand"] = bool(t1)   # T1 缩转放(正T买点)
                result["m5"]["t_sell"] = bool(t_sell)         # 分时T出(第一次高点后停量+二次拉升无量不过前高)
        except Exception as e:
            print(f"[TMonitor] 分钟线快照失败 {symbol}: {e}")
        return result

    def _build_position_snapshot(self, symbol: str,
                                    account_id: Optional[str] = None) -> Dict[str, Any]:
        """持仓字段（监控账户）。"""
        try:
            from app.services.t_gateway import get_sellable_ledger
            ledger = get_sellable_ledger(account_id or T_MONITOR_ACCOUNT)
            item = ledger.get(symbol) or {}
            avg = float(item.get("avg_price", 0) or 0)
            vol = int(item.get("volume", 0) or 0)
            pnl = 0.0
            # pnl_pct 需要现价，由调用方回填（此处用 quote 现价在快照里算）
            return {
                "sellable": int(item.get("sellable", 0) or 0),
                "volume": vol,
                "avg_price": avg,
                "pnl_pct": 0.0,
            }
        except Exception:
            return {"sellable": 0, "volume": 0, "avg_price": 0.0, "pnl_pct": 0.0}

    def _prio_ctx(self, today: str) -> dict:
        """当日优先度上下文（每天只读一次）：{main, second, theme, pool}。失败 ⇒ 空（回落代码序）。"""
        c = getattr(self, "_prio_cache", None)
        if isinstance(c, dict) and c.get("_day") == today:
            return c
        import json as _j
        D = os.environ.get("DATA_DIR", "/app/data")
        ctx = {"_day": today, "main": "", "second": "", "theme": {}, "pool": set()}
        try:
            with open(os.path.join(D, "main_line_state.json"), encoding="utf-8") as f:
                _ms = (_j.load(f) or {}).get("mainline_select") or {}
            ctx["main"] = str(_ms.get("mainline") or "")
            ctx["second"] = str(_ms.get("second") or "")
        except Exception as _eN19:
            # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
            from app.services import gate_alarm as _gaNote
            _gaNote.note("t_monitor:line2698", _eN19)
            pass
        for _fn, _is_pool in (("legs_switch.jsonl", True), ("legs.jsonl", False)):
            try:
                with open(os.path.join(D, _fn), encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = _j.loads(line)
                        except Exception:
                            continue
                        _s = str(o.get("symbol") or "")
                        if not _s:
                            continue
                        if o.get("theme"):
                            ctx["theme"].setdefault(_s, str(o["theme"]))
                        if _is_pool:
                            ctx["pool"].add(_s)
            except Exception:
                continue
        self._prio_cache = ctx
        if ctx["main"] or ctx["theme"]:
            print("[TMonitor] 候选优先度上下文：主线=%s 次线=%s 票→主题 %d 只、低吸腿池 %d 只"
                  % (ctx["main"] or "-", ctx["second"] or "-", len(ctx["theme"]), len(ctx["pool"])), flush=True)
        return ctx

    def _core_sort_key(self, conditions, held):
        """T0 持仓 → T1 有卖腿 → T2a 主题档 → T2c 在低吸腿池 → T2d 代码升序。"""
        ctx = self._prio_ctx(datetime.now().strftime("%Y%m%d"))
        _sell = {str(c.get("symbol")) for c in (conditions or [])
                 if str(c.get("direction") or "").strip().lower() == "sell"}
        _main, _second = ctx.get("main") or "", ctx.get("second") or ""
        _theme, _pool = ctx.get("theme") or {}, ctx.get("pool") or set()

        def _k(sym):
            _th = _theme.get(str(sym)) or ""
            _rank = 0 if (_th and _th == _main) else (1 if (_th and _th == _second) else 2)
            return (0 if sym in held else 1,          # T0 持仓
                    0 if sym in _sell else 1,         # T1 有卖腿
                    _rank,                            # T2a 方向层
                    0 if sym in _pool else 1,         # T2c 低吸腿候选池
                    str(sym))                         # T2d 兜底
        return _k

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

    # ── 条件评估 ──
    def _evaluate_condition(self, cond: Dict[str, Any], quote: dict,
                            regime_state: dict, snapshot: Optional[dict] = None) -> bool:
        """条件评估：有 expression 走自由表达式求值；无则回退默认复合确认逻辑（实时路径）。"""
        return evaluate_condition_at(cond, quote, regime_state, snapshot, datetime.now())

    def _pass_common_gates(self, cond: Dict[str, Any], regime_state: dict) -> bool:
        """表达式通过后的通用护栏：regime GATE + 时段 + 状态机（实时路径）。"""
        return pass_common_gates(cond, regime_state, datetime.now())

    def _evaluate_default(self, cond: Dict[str, Any], quote: dict,
                          regime_state: dict, snapshot: Optional[dict] = None) -> bool:
        """默认复合企稳确认（实时路径）。"""
        return evaluate_default_at(cond, quote, regime_state, snapshot, datetime.now())

    def _calc_volume_ratio(self, cond: Dict[str, Any], quote: dict) -> Optional[float]:
        """盘中量比归一（实时路径）。"""
        return calc_volume_ratio_at(cond, quote, datetime.now())

    def _stabilize_not_new_low(self, symbol: str, current: float) -> bool:
        """分时企稳（实时路径，m1 分钟线判断）。"""
        return stabilize_not_new_low_at(symbol, current, datetime.now())

    # ── 写触发事件 ──
    def _write_trigger(self, cond: Dict[str, Any], quote: dict, regime_state: dict,
                       snapshot: Optional[dict] = None, ledger: Optional[dict] = None):
        """写入 t_triggers(pending, snapshot{suggest_bid/ask, slippage_budget, confidence, fields})。"""
        current = float(quote.get("current", 0) or 0)
        trigger_kind = cond.get("trigger_kind", "low_buy")
        symbol = cond["symbol"]
        if ticket_ban_blocked(symbol, _is_buy_side(cond)):
            return None                       # G3 删票名单：买入侧不再复发
        if intraday_crush_blocked(symbol, _is_buy_side(cond), str(trigger_kind or "")):
            return None                       # C2′+C2″ 盘中午判据
        # 滑点/成交价溢价：默认 0.1%（生产现状）；回测 pins 置 `WOLF_FILL_PREMIUM_PCT=0` 砍掉
        slippage = fill_premium_pct() / 100.0
        gate = check_gate(trigger_kind, regime_state)
        mode = "human_confirm" if gate["mode"] == "human_confirm" else "auto"
        # 连续命中计数（同条件当日连续命中未实质改善 → 唤醒时提示 AI 调整/冷却）
        consecutive_hits = self._consecutive_hits(cond.get("id"), cond["symbol"])

        _fp2 = fill_prices(current)
        trig = {
            "account_id": cond.get("account_id", T_MONITOR_ACCOUNT),
            "condition_id": cond.get("id"),
            "symbol": cond["symbol"],
            "event_type": trigger_kind,
            "trigger_price": cond.get("target_price"),
            "quote_price": current,
            "suggest_bid_price": _fp2[0],
            "suggest_ask_price": _fp2[1],
            "slippage_budget": slippage,
            "snapshot": {
                "quote_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "trigger_price": cond.get("target_price"),
                "quote_price": current,
                "suggest_bid_price": round(current * (1 - slippage), 3),
                "suggest_ask_price": round(current * (1 + slippage), 3),
                "slippage_budget": slippage,
                "confidence": "expr_trigger" if cond.get("expression") else "low_buy_confirm",
                "turnover_rate": quote.get("turnover_rate"),
                "amplitude": quote.get("amplitude"),
                "expression_summary": _expr_summary(cond.get("expression")),
                "fields": snapshot or {},   # 触发时刻字段快照（Agent 决策直接用，不再重复取价）
                "consecutive_hits": consecutive_hits,   # AI 主导：连续命中计数
            },
            "mode": mode,
            "direction": "buy" if _is_buy_side(cond) else "sell",
        }
        try:
            from app.services import t_trigger_mute as _tm1
            if _tm1.is_muted(symbol, trigger_kind):
                return None                      # 当日静默（该腿今天不再生成重复触发）
        except Exception as _eN20:
            # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
            from app.services import gate_alarm as _gaNote
            _gaNote.note("t_monitor:line2835", _eN20)
            pass
        trig_id = t_db.insert_trigger(trig)
        if trig_id:
            # 状态机（2026-09-02 修订）：狼大形态条件（分时T出/黄线/急杀/缩量触低等
            # 表达式腿）= **非消费式持续腿**——命中后保持 active+armed（不销毁），
            # 由 _round 5 分钟冷却防刷；使"买腿回补 T仓 → 卖腿持续监控 T出/黄线"的
            # 做T循环闭环（狼大：底仓不动、T仓高抛低吸反复做）。
            # 其他做T条件仍消费式（迭代#56：触发即销毁，由 AI 重建移动基准）。
            # 2026-09-08: manual_guard 护栏卖腿命中即一次性消费；auto_exit(自动离场)持续监控
            _one_shot_guard = (str(cond.get("trigger_kind") or "") in ("custom_level_sell", "custom_vwap_sell")
                               and str(cond.get("publisher") or "") == "manual_guard")
            if _is_wolf_t_condition(cond) and not _one_shot_guard:
                t_db.update_condition_state(
                    cond.get("id"),
                    armed=1,
                    status="active",
                    last_triggered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    trigger_count_today=int(cond.get("trigger_count_today") or 0) + 1,
                )
            else:
                t_db.update_condition_state(
                    cond.get("id"),
                    armed=0,
                    status="consumed",
                    last_triggered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    trigger_count_today=int(cond.get("trigger_count_today") or 0) + 1,
                )
            print(f"[TMonitor] 触发写入 #{trig_id} {cond['symbol']} {trigger_kind} "
                  f"mode={mode} consec_hits={consecutive_hits} @ {current}")
            # 迭代#58d（用户需求）：无需人工确认——MANUAL_ONLY（谨慎/下跌市低吸闸门）
            # 只作标记（mode 字段），不拦截自动执行；命中即按网关自动买入/卖出。
            # 其余硬风控（STOP_ALL/日亏熔断/连续亏损/裸空/跌停）仍在网关层把关。
            # 自动执行闭环（迭代#57，用户需求）：条件命中自动执行（止损/止盈自动卖出、
            # 低吸自动买入，volume 优先用 AI 在条件里设定的股数），不再逐次等 AI 决策；
            # 迭代#58：无底仓买腿 = 条件单建仓，量按建仓规模（单笔上限÷现价）。
            # 执行完成后报告 AI 复盘并重建新条件。
            try:
                from app.services.t_gateway import gateway_execute
                side = "buy" if _is_buy_side(cond) else "sell"
                # G8（2026-09-14）狼大 2025-07-17「任何时候 看见机器人板块出上影线 立马停止做T」：
                # 该标的最近一根**已完成**日线出上影线 → 当日停止做T（不新开做T买腿、不做兑现类卖腿）；
                # **保护性卖出**（止损/破位/被动止盈线）不受影响。取不到日线 → 不停机（不猜）。
                _g8_stop, _g8_why = False, ""
                try:
                    from app.services import wolf_day_rules as _DR8
                    if _DR8.enabled():
                        _bars_sh = _fetch_daily_tencent_dated(symbol, 3) or []
                        _st8, _why8 = _DR8.shadow_stop(_bars_sh, 1)
                        # ⚠️ 2026-09-30（账本 §9.330 ✓ 用户「G8 只管做T类，保护/减仓类照跑」✓）：
                        #   原保护名单只有 3 个（stop_loss／custom_support_sell／wolf_passive_stop_sell）✗
                        #   ⇒ **黄线减仓 `custom_vwap_sell` 不在内** ✗ ⇒ 被 G8 当"兑现类"停掉 ✗
                        #   实测代价：SH603690 01-26 浮亏 −13% 时黄线腿被 G8 静默吃掉 ⇒ 一路走到 −19% ✗
                        #   ⇒ 按用户口径扩名单（**减仓/保护类照跑** ✓；兑现类仍停 ✓）
                        _G8_PROTECT_EXTRA = {"custom_vwap_sell", "wolf_defensive_t_reduce",
                                             "custom_level_sell", "custom_trail_sell",
                                             "wolf_confirm_sell", "wolf_boll_upper_sell",
                                             "wolf_board_half_sell"}
                        _protect8_kinds = {"stop_loss", "custom_support_sell", "wolf_passive_stop_sell"}
                        if str(os.getenv("WOLF_G8_PROTECT_REDUCE", "0")).strip().lower() \
                                in ("1", "true", "yes", "on"):
                            _protect8_kinds |= _G8_PROTECT_EXTRA
                        _protect8 = (side == "sell" and str(trigger_kind) in _protect8_kinds)
                        # 2026-09-25 用户拍板：G8 作用面收窄回语料口径（**只停做T**；
                        #   置 WOLF_G8_KINDS 后建仓类腿型豁免；库内默认空 = 旧行为逐字不变）
                        if _st8 and not _protect8 and _DR8.applies_to(trigger_kind):
                            _g8_stop, _g8_why = True, _why8
                except Exception as _ge8:
                    from app.services import gate_alarm as _ga2
                    _ga2.alarm("G8:%s" % symbol, _ge8, {"symbol": symbol}, fail_closed=False)
                    print(f"[TMonitor] G8 判定失败（按原口径执行）: {str(_ge8)[:60]}")
                cond_vol = int(cond.get("volume") or 0)
                if cond_vol > 0:
                    volume = (cond_vol // 100) * 100
                else:
                    # 回退规则：买腿 30% 底仓（min 100）；无底仓 = 建仓规模（单笔上限÷现价）；
                    # 卖腿 30%（保留底仓 100）
                    pos_item = (ledger or {}).get(symbol) or {}
                    sellable = int(pos_item.get("sellable", 0) or 0)
                    if side == "buy":
                        if sellable > 0:
                            volume = max(int(sellable * 0.3), 100)
                            # 2026-09-07 试仓档(tranche_ladder): 254/253 低吸且标的在主线候选(ambush/trial)
                            # → 以档位上限放行(可沉淀底仓), 主升确认(normal, -1)走正常; none 保持做T原量
                            if trigger_kind in PREVLOW_M5_KINDS:
                                try:
                                    import sys as _tl
                                    _tl.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                    "..", "..", "apps", "main_line"))
                                    from tranche_ladder import allowed_buy_volume
                                    _tlv, _tlr = allowed_buy_volume(
                                        symbol, trigger_kind, {"current": current},
                                        ledger, cond.get("account_id", T_MONITOR_ACCOUNT))
                                    if _tlv == -1:
                                        pass  # normal(主升确认): 正常建仓逻辑
                                    elif _tlv > 0:
                                        volume = max(_tlv, volume)   # 档位上限(不缩水原做T量)
                                    print(f"[TMonitor] tranche buy {symbol} {trigger_kind}: {_tlv} ({_tlr})")
                                except Exception as _tle:
                                    print(f"[TMonitor] tranche_ladder err: {str(_tle)[:80]}")
                        else:
                            try:
                                from app.services.t_build import build_sizing
                                sizing = build_sizing(symbol, current)
                                volume = int(sizing.get("suggest_volume") or 0)
                            except Exception:
                                volume = 0
                    else:
                        # 狼大『T出=出 T 仓，黄线跌破直接走』（2026-09-02 修订）：
                        # 卖腿一次卖光 T 仓（sellable-100），底仓 100 不动——
                        # 不再 30% 分批：T仓单批大时 30% 卖不完，违背"当天低吸
                        # 当天 T出"节奏，且单日多次分批卖与狼大"每天进出一次"不符。
                        # T仓=0（只剩底仓）时 max_sell=0 → blocked 不卖底仓。
                        # 2026-09-03：底仓保留数按标的覆盖（默认100；SH588170 ETF 底仓66,900），
                        # 大底仓标的卖腿只清 T仓（持仓-底仓），绝不清底仓。
                        from app.services.t_gateway import base_floor_shares
                        _floor = base_floor_shares(
                            cond.get("account_id", T_MONITOR_ACCOUNT), symbol, volume=sellable)
                        max_sell = max(sellable - _floor, 0) if sellable > _floor else 0
                        # 2026-09-19 用户拍板 A：**破位/减仓语义**的腿可穿透底仓（语料 2025-11-23/
                        #   2025-08-13「跌破高开下沿/开盘点位 → 减仓到 40% 甚至更低」）。
                        #   否则只剩底仓时量恒为 0 ⇒ blocked + 当日静默 ⇒ 只能拖到 14:45 破位清仓
                        #   （实测 SH600183：jan10 −1,314 / jan11 −1,640 都是这么亏出来的）。
                        #   穿透量按 _stop_exit_volume 口径：盘中减半、尾盘清仓。
                        try:
                            from app.services import t_capacity as _tcap
                            # 趋势转弱（下跌结构）也允许穿底仓：2026-09-19 深夜补，见 t_capacity 注释
                            _sw = False
                            try:
                                from app.services import wolf_stock_structure as _ssw
                                _sw = _ssw.is_downtrend(current, self._daily_dated(symbol, 25))
                            except Exception:
                                _sw = False
                            if _tcap.base_penetrate_allowed(trigger_kind, str(cond.get("reason") or ""),
                                                            struct_weak=_sw):
                                _pen, _pm = _stop_exit_volume(sellable, _in_close_window(), _floor)
                                if int(_pen or 0) > max_sell:
                                    print(f"[TMonitor] 底仓穿透 {symbol} {trigger_kind}: "
                                          f"{max_sell}→{int(_pen)} 股（{_pm}；"
                                          f"{'趋势转弱(破MA20且MA10<MA20)' if _sw else '破位/减仓语义'}）", flush=True)
                                    max_sell = int(_pen)
                        except Exception as _bpe:
                            print("[TMonitor] 底仓穿透判定异常(忽略): %s" % str(_bpe)[:80], flush=True)
                        volume = max_sell
                    volume = (volume // 100) * 100
                # S2 删除(2026-09-10): 原"④破位禁低吸"(现价<=算法波段支撑位 support_l1 → 禁 254/253 低吸)。
                # 狼大语料无"波段支撑位"概念(他用前低与黄线), 属审计 §5.2 认定的自造机制 → 已删除。
                # 是否接刀由狼大原口径把关: 254 触前日低+缩量、253 指数急杀, 以及 gateway 硬闸门。
                exec_ok = False
                # ── 个股结构门（蓝图:149「下跌不做」；2026-09-19 用户拍板 A）──────────────────
                #   实测 jan11 0128：SH603019（破 MA5/10/20）−148、SZ002579（破三条均线且给亏损仓加仓）−1,550；
                #   正T买入腿原先只看形态/价格（低吸价差/量能/regime），不看个股结构 ⇒ 下跌结构里照样接刀。
                if side == "buy":
                    try:
                        from app.services import wolf_stock_structure as _ss
                        if _ss.enabled():
                            _ok_ss, _why_ss = _ss.verdict(symbol, current, self._daily_dated(symbol, 25))
                            if not _ok_ss:
                                t_db.update_trigger_status(trig_id, "blocked", reason=str(_why_ss)[:250])
                                print("[TMonitor] 个股结构门拦买腿 %s: %s" % (symbol, str(_why_ss)[:110]), flush=True)
                                return      # 本腿不执行（_write_trigger 的调用方不取返回值）
                    except Exception as _sse:
                        print("[TMonitor] 个股结构门异常(放行) %s: %s" % (symbol, str(_sse)[:70]), flush=True)
                # ★ HPV 位置闸覆盖「急杀/前低低吸」，放在**两条执行分支之前**（`WOLF_HPV_LOWDIP`，库内默认 0）
                #   2026-09-25 二次修正（账本 §9.114）：首版打在 `if _no_hold_build:`（253/254 建仓链）内，
                #   但回测走的是 **gateway 分支**（`_trade_executor is None` ⇒ `_no_hold_build=False`）
                #   ⇒ 补丁被跳过、601069@20260130 照旧被买入 ✗ ⇒ 现挪到分支之前，两条路都覆盖 ✓
                try:
                    import os as _osH2
                    if side == "buy" and str(trigger_kind) in PREVLOW_M5_KINDS \
                            and str(_osH2.getenv("WOLF_HPV_LOWDIP", "0")).strip() == "1":
                        from app.services import wolf_high_pos_vol as _hpv3
                        # 2026-09-25 三次修正（账本 §9.116）：本作用域**没有** `today` ⇒
                        #   首版用 `today` 直接 NameError ⇒ 被 except 吞掉 ⇒ **fail-open 102 行** ✗
                        #   改用标准取法（回测里时钟被钉住 ⇒ 拿到的就是模拟当天 ✓，与 line 759 同口径）
                        _d8h = datetime.now().strftime("%Y%m%d")
                        if _hpv3.enabled() and _hpv3.applies(self._held_today(symbol)):
                            _b3, _w3 = _hpv3.verdict(_hpv3.bars_asof(symbol, _d8h, 30),
                                                     held=self._held_today(symbol))
                            if _b3:
                                print("[HPV] 高位放量不买(低吸路径) %s@%s: %s" % (symbol, _d8h, str(_w3)[:90]), flush=True)
                                t_db.update_trigger_status(trig_id, "blocked", reason="[HPV] " + str(_w3)[:200])
                                return
                except Exception as _he3:
                    # 2026-09-25（账本 §9.117）：**异常不再静默放行** ⇒ 报警落盘 + 可拦（WOLF_GATE_FAIL_CLOSED）
                    from app.services import gate_alarm as _ga
                    if _ga.alarm("HPV_低吸路径:%s:%s" % (symbol, trigger_kind), _he3,
                                 {"symbol": symbol, "kind": str(trigger_kind)}, fail_closed=True):
                        t_db.update_trigger_status(trig_id, "blocked",
                                                   reason="[GATE-FAIL-CLOSED] HPV 低吸路径判定异常 ⇒ 拦（待修）")
                        return
                # ⑥ 253/254 无底仓建仓 → 走狼大建仓链(而非做T gateway)；注 self._trade_executor 时生效，否则回退 gateway
                _no_hold_build = (
                    side == "buy"
                    and trigger_kind in PREVLOW_M5_KINDS
                    and ((ledger or {}).get(symbol, {}).get("sellable", 0) or 0) <= 0
                    and self._trade_executor is not None
                )
                if _no_hold_build:
                    # ★ HPV 位置闸首次覆盖「急杀/前低低吸」（`WOLF_HPV_LOWDIP`，库内默认 0 = 旧行为）
                    #   2026-09-25 修接线缺口（账本 §9.109）：HPV 判据本就存在、且对 601069@20260130 明确输出
                    #   「不买」，但它此前只挂在 ~957 的闸门区，`custom_m5dump`/`custom_prevlow` 这条执行路径
                    #   **没经过它** ⇒ 三个涨停后低开 −8%、上影 91%、量比 2.63 的票被买入 ✗。
                    try:
                        import os as _osH
                        if str(_osH.getenv("WOLF_HPV_LOWDIP", "0")).strip() == "1":
                            from app.services import wolf_high_pos_vol as _hpv2
                            if _hpv2.enabled():
                                _b2, _w2 = _hpv2.verdict(_hpv2.bars_asof(symbol, today, 30),
                                                         held=self._held_today(symbol))
                                if _b2:
                                    print("[HPV] 高位放量不买(低吸路径) %s@%s: %s"
                                          % (symbol, today, str(_w2)[:90]), flush=True)
                                    t_db.update_trigger_status(trig_id, "blocked", reason="[HPV] " + str(_w2)[:200])
                                    return
                    except Exception as _he2:
                        print("[HPV] 低吸路径判定异常(放行) %s: %s" % (symbol, str(_he2)[:70]), flush=True)
                    # 2026-09-07 试仓档方向门: 非主线候选(TOP1∪TOP2)标的无底仓不低吸建仓(防乱建)
                    _tier_none = False
                    try:
                        import sys as _tl2
                        _tl2.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "..", "..", "apps", "main_line"))
                        from tranche_ladder import tier_for
                        _tier_none = tier_for(symbol)[0] == "none"
                    except Exception:
                        _tier_none = False
                    if _tier_none:
                        t_db.update_trigger_status(trig_id, "blocked", reason="非主线候选TOP1∪TOP2, 不低吸建仓(试仓档)")
                        print(f"[TMonitor] 试仓档拦无底仓建仓 {symbol}")
                    else:
                        try:
                            import datetime as _dtw
                            from app.services import wolf_253_build as _W
                            _today = _dtw.datetime.now().strftime("%Y%m%d")
                            if trigger_kind == "custom_m5dump":
                                _r = _W.build_253(self._trade_executor, symbol, quote, now_str=str(current),
                                                  account=cond.get("account_id", T_MONITOR_ACCOUNT),
                                                  snapshot=snapshot)  # P0-1: 透传快照供日志记录 m5_dump
                            else:
                                # 254 首现→建小底仓并记 base_254；其后 3 日内再次命中→分步回补(≤2次)
                                _chain = _W._chain_state().get(symbol) or {}
                                _vr = float(snapshot.get("vol_ratio") or 0)
                                if not _chain.get("base_254_date"):
                                    _r = _W.build_253(self._trade_executor, symbol, quote, now_str=str(current),
                                                      account=cond.get("account_id", T_MONITOR_ACCOUNT),
                                                      snapshot=snapshot)  # P0-1: 同上
                                    # 2026-09-08 修复(600004整天blocked): 仅建仓成功才记 base_254——
                                    # 首建失败(data_unavailable等)若也标记, 当日后续254命中全走refill被same_day拦, 全天建仓0
                                    if _r.get("status") == "success":
                                        _W.mark_base_254(symbol, _today)
                                else:
                                    _r = _W.refill_253(self._trade_executor, symbol, quote, _vr, _today,
                                                       account=cond.get("account_id", T_MONITOR_ACCOUNT))
                            exec_ok = _r.get("status") == "success"
                            print(f"[TMonitor] 狼大253/254建仓 {symbol}: {_r.get('status')} {str(_r.get('reason') or '')[:40]}")
                            t_db.update_trigger_status(trig_id, "executed" if exec_ok else "blocked",
                                                       reason="狼大253/254建仓: %s" % (_r.get("reason") or _r.get("status")))
                        except Exception as _we:
                            print(f"[TMonitor] 狼大253/254建仓异常 {symbol}: {_we}")
                            t_db.update_trigger_status(trig_id, "blocked", reason="wolf_253_build_exc")
                elif _g8_stop:
                    t_db.update_trigger_status(trig_id, "blocked", reason="[G8] " + _g8_why)
                    print(f"[TMonitor] G8 上影线停机 {symbol}（{_g8_why}）→ 跳过 {side} {trigger_kind}")
                    try:    # 2026-09-19：命中即当日静默（狼大「看见出上影线 立马停止做T」= 当日停做 T）
                        from app.services import t_trigger_mute as _tm2
                        _tm2.mute(symbol, trigger_kind, "G8 上影线停机: " + str(_g8_why)[:60])
                    except Exception as _eN21:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line3091", _eN21)
                        pass
                elif volume > 0:
                    # 板块级 G3 不做T门(2026-09-07): 持仓所属主题处洗盘收敛期 → 存量T仓不自动T出
                    # (盘前 sector_g3_state.json, 见 apps/main_line/sector_g3.py; env WOLF_NO_T_GATE=1 启用)
                    _g3_block, _g3_reason = False, ""
                    if side == "sell" and os.getenv("WOLF_NO_T_GATE", "0") != "0":
                        try:
                            import sys as _sg
                            _sg.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                            "..", "..", "apps", "main_line"))
                            from no_t_gate import g3_sell_blocked
                            _g3_block, _g3_reason = g3_sell_blocked(symbol)
                        except Exception as _ge:
                            print(f"[TMonitor] no_t_gate err: {str(_ge)[:80]}")
                    # 撤销式 T出(2026-09-07): high_sell(t_sell) 触发 → 进入观察期(不立即卖),
                    # 期间放量过前高则撤销(主升未完), 无新高且超 TSELL_DELAY_S 由 _settle_tsell_pending 执行
                    _tsell_defer = False
                    if not _g3_block and side == "sell" and trigger_kind == "high_sell" and _TSELL_UNDO                             and symbol not in _TSELL_PENDING:
                        try:
                            import time as _tm
                            _tb = self._today_bars(symbol)
                            if _tb:
                                _ctx = _tsell_hi_base(_tb)
                                if _ctx:
                                    _TSELL_PENDING[symbol] = {
                                        "hi": _ctx[0], "base": _ctx[1], "volume": int(volume),
                                        "trig_id": trig_id, "ts": _tm.time(),
                                        "account_id": cond.get("account_id", T_MONITOR_ACCOUNT)}
                                    t_db.update_trigger_status(
                                        trig_id, "claimed",
                                        reason=f"T出撤销式观察(hi={_ctx[0]:.3f}, {int(TSELL_DELAY_S)}s内放量过前高则撤销)")
                                    print(f"[TMonitor] T出进入撤销式观察 {symbol} hi={_ctx[0]:.3f} vol={volume}")
                                    _tsell_defer = True
                        except Exception as _te:
                            print(f"[TMonitor] tsell defer err: {str(_te)[:100]}")
                    # ②量能分层(2026-09-08): 跌破类离场腿缩量(<PULLBACK_VOL_RATIO)不立即卖——
                    # 进反抽减等待(claimed), 反抽回支撑/黄线上沿或14:45尾盘确认后由 _settle_pullback_sell 执行
                    _pb_defer = False
                    if (not _g3_block and not _tsell_defer and side == "sell"
                            and trigger_kind in ("custom_vwap_sell", "custom_trail_sell", "custom_support_sell")
                            and os.getenv("AUTO_PULLBACK_SELL", "1") != "0"
                            and symbol not in _PULLBACK_SELL):
                        try:
                            _vr = float((snapshot or {}).get("vol_ratio") or 0) if snapshot else 0.0
                            if 0 < _vr < PULLBACK_VOL_RATIO:
                                _q = (snapshot or {}).get("quote") or {}
                                _sup = float(_q.get("support_l1") or 0)
                                _vwap = float(_q.get("average") or 0) or float(quote.get("average") or 0)
                                try:
                                    _pc = float(_q.get("pre_close") or quote.get("pre_close") or 0)
                                except Exception:
                                    _pc = 0.0
                                _ref_up = pullback_ref_up(_sup, _vwap, _pc)
                                if _ref_up <= 0:
                                    _ref_up = round(float(current) * 1.005, 4)
                                _PULLBACK_SELL[symbol] = {"kind": trigger_kind, "trig_id": trig_id,
                                                          "volume": int(volume), "ref_up": round(_ref_up, 4),
                                                          "sup": round(_sup, 4), "vwap": round(_vwap, 4),
                                                          "account_id": cond.get("account_id", T_MONITOR_ACCOUNT),
                                                          "ts": time.time()}
                                t_db.update_trigger_status(
                                    trig_id, "claimed",
                                    reason="缩量破位(%s量比%.2f<%.1f), 反抽减等待(ref_up=%.3f)" % (trigger_kind, _vr, PULLBACK_VOL_RATIO, _ref_up))
                                print(f"[TMonitor] {trigger_kind} 缩量破位进入反抽等待 {symbol} ref_up={_ref_up:.3f} vr={_vr}")
                                _pb_defer = True
                        except Exception as _pbe:
                            print(f"[TMonitor] pullback defer err: {str(_pbe)[:80]}")
                    # ── 开盘不追高闸·条件单补线（2026-09-23；WOLF_OC_COND_BUY 默认 0）──
                    # 见文件头 `_oc_cond_buy_block`：条件单路径原先不传 trigger_id ⇒ 网关两台闸门全跳过。
                    _ocb, _ocwhy = (False, "")
                    if str(side).lower() in ("buy", "买入"):
                        _ocb, _ocwhy = _oc_cond_buy_block(current, quote, trigger_kind)
                    if _ocb:
                        exec_ok = False
                        print(f"[TMonitor] 条件单开盘不追高拦截 {symbol} {trigger_kind}: {str(_ocwhy)[:90]}")
                        t_db.update_trigger_status(trig_id, "blocked",
                                                   reason="[OC-COND] " + str(_ocwhy)[:200])
                    elif _g3_block:
                        exec_ok = False
                        print(f"[TMonitor] G3门拦截 {symbol} 卖腿: {_g3_reason}")
                        t_db.update_trigger_status(trig_id, "blocked", reason=_g3_reason + "（G3门）")
                    elif _tsell_defer or _pb_defer:
                        exec_ok = False   # 延迟执行(由 _settle_tsell_pending/_settle_pullback_sell 处理)
                    else:
                        # 2026-09-26 修：**必须传 `trigger_id`** ✗ —— 本口原先漏传 ⇒
                        #   网关里靠 trigger_id 的两台闸门（**低吸额度** + **低吸趋势票门**）**全被跳过** ✗
                        #   实测（T35 一月）：`低吸额度：` 0 行、`低吸趋势票门拦住` 0 行，
                        #   而低吸仍成交 11 笔、亏 **−6,700** ✗ ⇒ 与"一月仍依赖通富微电"直接相关 ✓
                        #   （同类教训：HPV 闸曾挂在 `if _no_hold_build:` 分支里 ✗）
                        gw = gateway_execute(symbol, side, current, volume,
                                             reason=f"条件命中自动执行（{trigger_kind}）",
                                             decision_source="ai_led",
                                             trigger_id=trig_id,
                                             condition_id=cond.get("id"),
                                             account_id=cond.get("account_id", T_MONITOR_ACCOUNT))
                        exec_ok = gw.get("status") == "success"
                        if exec_ok and side == "sell":
                            # 同轮互斥: 该标的本轮已有卖腿成交, 其余离场腿本轮不再执行
                            self._sold_this_round.add(symbol)
                            self._after_sell(symbol, cond.get("account_id", T_MONITOR_ACCOUNT),
                                             trigger_kind, str(cond.get("reason") or ""))
                        print(f"[TMonitor] 自动执行 {symbol} {side} {volume}股@{current}: "
                              f"{gw.get('status')} {str(gw.get('reason') or '')[:40]}")
                        # 执行结果写入触发事件（供审计/复盘）
                        t_db.update_trigger_status(
                            trig_id, "executed" if exec_ok else "blocked",
                            reason=f"自动执行 {side} {volume}股 @{current}: {gw.get('status')} | {str(gw.get('reason') or '')[:120]} | level={gw.get('level')}")
                elif volume <= 0:
                    # ── ⚠️ 2026-09-30（账本 §9.336 ✓ 用户「跌停了为什么还是没有卖出」✓）──────────
                    #   **保护/减仓类 ⇒ 穿透底仓止血** ✓（不再被"无T仓可卖"静默 ✗）
                    #   依据 ✓：语料「**止血动作必须能执行**」；实测代价 ✗：SZ002792 跌停两连
                    #   （01-14 −10% / 01-15 −10%）期间 量=0 ⇒ **一条卖单都没有** ✗（73.35→46.14 ✓）
                    _RED_KINDS_B = ("custom_vwap_sell", "custom_support_sell", "custom_level_sell",
                                    "custom_trail_sell", "wolf_defensive_t_reduce", "wolf_confirm_sell",
                                    "wolf_boll_upper_sell", "wolf_board_half_sell",
                                    "wolf_passive_stop_sell", "stop_loss", "wolf_early_swing_sell")
                    _red_exempt = (side == "sell"
                                   and str(os.getenv("WOLF_SELL_EXEMPT_REDUCE", "0")).strip().lower()
                                   in ("1", "true", "yes", "on")
                                   and str(trigger_kind) in _RED_KINDS_B)
                    _posv = 0
                    if _red_exempt:
                        try:
                            from app.services.t_gateway import get_sellable_ledger as _gsl2
                            _posv = int(((_gsl2(account_id=cond.get("account_id", T_MONITOR_ACCOUNT))
                                          or {}).get(symbol) or {}).get("sellable", 0) or 0)
                        except Exception:
                            _posv = 0
                        # ⚠️ 2026-09-30（账本 §9.342 ✓ 用户「怎么上涨的时候保护性减仓？」✗）——
                        #   ① **真危难才允许穿透**：浮亏 ✓ 或 真止损/破位类腿 ✓（浮盈时**不得**"止血" ✗）
                        _TRUE_STOP = ("stop_loss", "wolf_passive_stop_sell",
                                      "wolf_early_swing_sell", "custom_support_sell")
                        _cost = 0.0
                        try:
                            _cost = float(_p.get("avg_price") or _p.get("cost") or 0) or 0.0
                        except Exception:
                            _cost = 0.0
                        _cur_px = float(current or 0)
                        _in_loss = bool(_cost > 0 and _cur_px > 0 and _cur_px < _cost)
                        _is_true_stop = str(trigger_kind) in _TRUE_STOP
                        if not (_in_loss or _is_true_stop):
                            print("[TMonitor] 保护/减仓穿透底仓否决 %s：浮盈（现价%.3f >= 成本%.3f）"
                                  "且腿型 %s 非止损/破位 => 不许止血（他：突破减半剩下的吃溢价）"
                                  % (symbol, _cur_px, _cost, trigger_kind), flush=True)
                            _red_exempt = False
                            _posv = 0
                        else:
                            # ② **卖量收敛到"同轮剩余额度"**（持仓一半 − 已减 ✓）⇒ 不再一把清仓 ✗
                            try:
                                from app.services import t_gateway as _gwM
                                from sqlalchemy import text as _thM
                                from app.database import SessionLocal as _SHM
                                with _SHM() as _shM:
                                    _rM = _shM.execute(_thM(
                                        "SELECT COALESCE(SUM(volume),0) FROM paper_trades "
                                        "WHERE account_id=:a AND symbol=:s AND direction LIKE '卖%' "
                                        "AND COALESCE(voided,0)=0 AND trade_date >= ("
                                        " SELECT MIN(trade_date) FROM paper_trades WHERE account_id=:a "
                                        " AND symbol=:s AND direction LIKE '买%' AND COALESCE(voided,0)=0)"),
                                        {"a": cond.get("account_id", T_MONITOR_ACCOUNT), "s": symbol}).fetchone()
                                    _soldM = int((_rM[0] if _rM else 0) or 0)
                                _roomM = int(max(_posv // 2 - _soldM, 0) // 100 * 100)
                                if _roomM < _posv:
                                    print("[TMonitor] 保护/减仓·穿透底仓**收敛** %s %d→%d 股"
                                          "（已减 %d／持仓 %d ⇒ 同轮上限一半 ✓）"
                                          % (symbol, _posv, _roomM, _soldM, _posv), flush=True)
                                _posv = _roomM
                            except Exception:
                                pass
                    if _red_exempt and _posv > 0:
                        try:
                            _gwR = gateway_execute(symbol, "sell", current, _posv,
                                                   reason="[保护/减仓·穿透底仓止血] " + str(cond.get("reason") or "")[:90],
                                                   account_id=cond.get("account_id", T_MONITOR_ACCOUNT),
                                                   trigger_id=trig_id, is_stop_loss=True)
                            _okR = str((_gwR or {}).get("status") or "") in ("success", "submitted", "filled")
                            print("[TMonitor] 保护/减仓·穿透底仓止血 %s sell %d股@%.3f: %s｜%s"
                                  % (symbol, _posv, float(current or 0), (_gwR or {}).get("status"),
                                     str((_gwR or {}).get("reason") or "")[:60]), flush=True)
                            t_db.update_trigger_status(
                                trig_id, "executed" if _okR else "blocked",
                                reason="保护/减仓·穿透底仓止血 %d股: %s" % (_posv, str((_gwR or {}).get("status"))))
                            if _okR:
                                try:
                                    self._after_sell(symbol, cond.get("account_id", T_MONITOR_ACCOUNT),
                                                     trigger_kind, str(cond.get("reason") or ""))
                                except Exception:
                                    pass
                            _red_exempt = False     # 已处理 ⇒ 不走下面的静默 ✗
                        except Exception as _eR:
                            print("[TMonitor] 穿透底仓止血异常: %s" % str(_eR)[:70], flush=True)
                    # 量推导为 0（卖腿仅剩底仓无T仓可卖 / 无底仓建仓规模不可用）→ 直接标记跳过，
                    # 避免孤儿 pending 事件（降级轮询兜底）；持仓仅100股(底仓)时不再当作"裸空"错误
                    _no_t_shop = (side == "sell")
                    # ⚠️ 2026-09-30（账本 §9.336 补）：**只有"没走止血"时才标 blocked** ✓
                    #   否则会把上面刚成功的"穿透底仓止血"**覆盖成 blocked** ✗（审计失真 ✗）
                    if _red_exempt:
                        t_db.update_trigger_status(
                            trig_id, "blocked",
                            reason=f"自动执行量推导为 0（{side}，{'仅底仓无T仓可卖，跳过卖出' if _no_t_shop else '无底仓建仓规模不可用'}）")
                    if _no_t_shop and _red_exempt:
                        try:    # 2026-09-19：无 T 仓可卖 ⇒ 当日该腿静默（实测这一条占 blocked 的 53%）
                            from app.services import t_trigger_mute as _tm3
                            _tm3.mute(symbol, trigger_kind, "无T仓可卖（底仓保护）")
                        except Exception as _eN22:
                            # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                            from app.services import gate_alarm as _gaNote
                            _gaNote.note("t_monitor:line3204", _eN22)
                            pass
                # 消费式条件自动重建（迭代#56b/57）：本条件已 consumed，该标的仍有
                # 持仓且无其他 active 条件 → AI 重新评估生成新条件（移动基准）。
                # 执行后报告 AI = 调 AI 条件生成（含现价），失败回退规则公式。
                try:
                    from app.services.t_db import list_active_conditions
                    remain = list_active_conditions(
                        symbol=symbol,
                        account_id=cond.get("account_id", T_MONITOR_ACCOUNT))
                    # 成交后刷新持仓（无底仓建仓场景：执行前无持仓/成本）
                    fresh_item = {}
                    try:
                        from app.services.t_gateway import get_sellable_ledger
                        fresh_item = (get_sellable_ledger(
                            cond.get("account_id", T_MONITOR_ACCOUNT))
                            .get(symbol) or {})
                    except Exception as _eN23:
                        # 2026-09-25（账本 §9.118）：静默兜底 ⇒ 接报警落盘（不改行为；gate_failures.jsonl 可复盘）
                        from app.services import gate_alarm as _gaNote
                        _gaNote.note("t_monitor:line3221", _eN23)
                        pass
                    pos_volume = int(fresh_item.get("volume") or 0)
                    if not remain and pos_volume > 0:
                        from app.services.t_build import auto_gen_conditions_for_build, no_rebuild_symbols
                        # 迭代#58g：只减不补等禁重建标的——触发后不自动重建
                        # （防止消费式重建给它们补出低吸买腿）
                        if symbol in no_rebuild_symbols():
                            print(f"[TMonitor] 禁重建标的 {symbol}：触发后不自动重建（只减不补等语义）")
                        else:
                            avg_price = float(fresh_item.get("avg_price") or 0)
                            if avg_price <= 0 and exec_ok:
                                avg_price = float(gw.get("price") or current)  # 无底仓建仓：无历史成本，用成交价
                            if avg_price > 0:
                                from datetime import date
                                today = date.today().strftime("%Y%m%d")
                                ok = auto_gen_conditions_for_build(
                                    symbol, avg_price, trade_date=today,
                                    quote_price=current,
                                    account_id=cond.get("account_id",
                                                        T_MONITOR_ACCOUNT))
                                if ok:
                                    print(f"[TMonitor] 消费式条件自动重建 {symbol}（AI 重新评估，当日 @{current}）")
                except Exception as e:
                    print(f"[TMonitor] 条件自动重建失败 {symbol}: {e}")
            except Exception as e:
                print(f"[TMonitor] 自动执行失败（降级标记）: {e}")
                from app.services.t_bridge import agent_review_and_execute
                agent_review_and_execute(trig)

    def _consecutive_hits(self, condition_id: Optional[int], symbol: str) -> int:
        """同条件当日连续命中计数：从最新 t_triggers 往前数连续 ai_decided/await_retry/pending。"""
        if not condition_id:
            return 0
        try:
            from sqlalchemy import text
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                # ⚠️ 2026-09-19：按"当日"比较必须用 **Python 钉钟**（created_at 也由 Python 钟写入）。
                #   原用 CURRENT_DATE（DB 真钟）⇒ 回放里恒真 ⇒ 连续命中跨日累加（误给 AI"冷却"提示）。
                _today = datetime.now().strftime("%Y-%m-%d")
                rows = db.execute(text(
                    "SELECT status FROM t_triggers "
                    "WHERE condition_id = :cid AND symbol = :sym "
                    "AND to_char(created_at, 'YYYY-MM-DD') = :today "
                    "ORDER BY id DESC LIMIT 10"
                ), {"cid": condition_id, "sym": symbol, "today": _today}).mappings().all()
                n = 0
                for r in rows:
                    st = r.get("status")
                    if st in ("pending", "ai_decided", "await_retry"):
                        n += 1
                    else:
                        break
                return n
            finally:
                db.close()
        except Exception:
            return 0

    def _check_stop_loss(self, symbol: str, quote: dict, ledger: dict):
        """止损扫描（生产）：持仓标的现价 ≤ stop_loss_price → 止损卖腿（reason=stop_loss）。

        - 每标的每轮一次（符号条件共享同一止损价，取条件表中非零止损价）
        - **阶段化止损线（2026-09-10, 狼大止损六层之①/④）**：
          建仓初期（<= WOLF_EARLY_STOP_DAYS=13 交易日）→ **波段低点 ×(1-3%)**（狼大 2026-03-05 原话，
          波段低点以**建仓日**为锚锁定，见 wolf_early_stop）；已成趋势后 → 既有 `stop_loss_price`
          （六层之②趋势线法语料无参数，用户决策暂用 stop_loss_price）。
          **「无利空」前提**：该标的有持有期内的意外事件/黑天鹅级利空（data/wolf_negative_events.json）
          → **不套结构线**，退回 `stop_loss_price`（狼大: 那是"别的逻辑", 非自己逻辑被证伪）。
          `WOLF_EARLY_STOP=0` 退回"一律 stop_loss_price"；`WOLF_NEG_EVENT=0` 忽略利空前提。
        - 当日已止损过（t_triggers 含当日 stop_loss 事件）则跳过，防止重复卖
        - **卖量分级（P2-4 完整落地, 2026-09-10）**：收盘时段(>=14:55)确认破位 → **清仓(含底仓)**;
          盘中确认破位 → 减半仓。见 _stop_exit_volume / _in_close_window。
          （原 docstring 写"卖量 = 可卖底仓全部", 与当时代码实际的"减半仓"不符, 已更正。）
        - 破位是否"确认"由 _stop_close_confirm(假跌破守卫)判定; 走网关 ai_led 档位(is_stop_loss=True)
        - 止损后冻结该标的全部条件（armed=0）
        """
        try:
            from app.services import t_db
            from app.services.t_gateway import gateway_execute
            item = (ledger or {}).get(symbol) or {}
            sellable = int(item.get("sellable", 0) or 0)
            if sellable <= 0:
                return
            current = float(quote.get("current", 0) or 0)
            if current <= 0:
                return
            stop_price = None
            conds = t_db.list_active_conditions(symbol=symbol,
                                                account_id=T_MONITOR_ACCOUNT)
            for c in conds or []:
                sp = float(c.get("stop_loss_price") or 0)
                if sp > 0:
                    stop_price = sp
                    break
            # ── ①+④ 阶段化止损线（2026-09-10, 狼大止损六层之①/④）──
            # 狼大 2026-03-05「13日内跌破波段低点的-3%没有收回 直接止损」;
            #        2026-03-06「**已经成为趋势后**…这个就没意义了…转为我之前说的趋势波段止盈止损方法
            #                    也就是用**趋势线**的方法…不是一个策略用到底的」。
            # → 建仓初期(<=13 交易日)用"建仓时点锁定的波段低点 -3%"; 已成趋势后维持既有 stop_loss_price
            #   （六层之②趋势线法: 语料**无参数**, 用户 2026-09-10 决策"先用 stop_loss_price"，回测后再定）。
            # 波段低点以**建仓日**为锚重算（等价于建仓时锁定, 不随行情滚动）—— 见 wolf_early_stop 模块头。
            _src = "none"
            if os.getenv("WOLF_EARLY_STOP", "1").strip() not in ("0", "false", "no"):
                try:
                    from app.services.wolf_early_stop import resolve_stop as _resolve_stop
                    _bd = self._buy_date(symbol)
                    _stop_price, _src, _sreason = _resolve_stop(
                        stop_price, self._daily_dated(symbol, 40), _bd,
                        symbol=symbol, today=datetime.now().strftime('%Y%m%d'))
                    if _src in ("wolf_early_swing", "neg_event"):
                        _tk_s = (symbol, "earlyswing", datetime.now().strftime('%Y%m%d'))
                        if _tk_s not in _STOP_HOLD_WARNED:
                            _STOP_HOLD_WARNED.add(_tk_s)
                            print(f"[TMonitor] ①波段逻辑止损线({_src}) {symbol}: {_sreason}")
                    stop_price = _stop_price
                except Exception as _ee:
                    print(f"[TMonitor] 建仓初期止损线解析异常(退回 stop_loss_price) {symbol}: {str(_ee)[:100]}")
            # ── ② 趋势线法（2026-09-14 落地）：**已成趋势后**换成他的趋势线口径 ──
            # 狼大 2026-03-06「已经成为趋势后…用**趋势线**的方法…不是一个策略用到底的」；
            #      2021-01-28「用 13日 34日做强弱分类，**60日是我的底线**，甚至没站稳 34日**带量**下穿的我都会砍掉」；
            #      2026-01-12「收黑K跌破5日线…**减仓**避一下」。
            # 运行条件：WOLF_TREND_STOP=1（默认）且已超出建仓初期窗口（① 未接管时）。
            # 回退：WOLF_TREND_STOP=0 → 回到既有 stop_loss_price 口径（本文档下称"现状"）。
            _trend_act = None
            try:
                from app.services import wolf_trend_stop as _TS
                if _TS.enabled():
                    _bd_t = self._buy_date(symbol)
                    _held = None
                    if _bd_t:
                        try:
                            from app.services.wolf_early_stop import held_trading_days as _htd
                            _held = _htd(self._daily_dated(symbol, 90), _bd_t)
                        except Exception:
                            _held = None
                    _early_days = int(os.getenv("WOLF_EARLY_STOP_DAYS", "13"))
                    if _held is None or _held > _early_days:
                        _tv = _TS.evaluate(self._daily_dated(symbol, 90))
                        # **口径替换**：成趋势后按他的②只用趋势线；AI 建仓时给的 stop_loss_price 默认不再并行生效
                        # （否则它总在趋势线之上、永远先触发 → ②等于没上）。要保留旧线：WOLF_TREND_KEEP_COND_STOP=1。
                        _keep = os.getenv("WOLF_TREND_KEEP_COND_STOP", "0").strip() not in ("0", "false", "no")
                        # fail-open：趋势线数据不足（含测试桩/新股）→ **保留**既有 stop_loss_price，
                        # 不因为"算不出趋势线"而把保护撤掉（与 ① 的 fail-open 同口径）。
                        if not _keep and bool((_tv.get("state") or {}).get("ok")):
                            stop_price = None
                        if _tv.get("action") == "notice":
                            _tk_n = (symbol, "trend_notice", datetime.now().strftime('%Y%m%d'))
                            if _tk_n not in _STOP_HOLD_WARNED:
                                _STOP_HOLD_WARNED.add(_tk_n)
                                print(f"[TMonitor] ②趋势线提示(不自动卖) {symbol}: {_tv['why']}")
                        if _tv.get("action") in ("exit", "reduce") and _tv.get("line"):
                            # 2026-09-26 用户「把②趋势线止损/减仓也加进豁免名单」✓（账本 §9.201）：
                            #   **埋伏仓豁免本规则** ✗ —— 病灶：T35 里埋伏票被它卖了 17 笔 ✓
                            #   （其中止损 11 笔合计 **−3,246** ✗），而长样本量化显示该形态需 **40~60 日** ✓
                            #   开关沿用 `WOLF_AMBUSH_SELL_EXEMPT`（库内默认 0 ✓）
                            _skip_trend = False
                            if str(os.getenv("WOLF_AMBUSH_SELL_EXEMPT", "0")).strip().lower() in ("1", "true", "yes", "on"):
                                try:
                                    if self._is_ambush_position(T_MONITOR_ACCOUNT, symbol):
                                        if _tk_t not in _STOP_HOLD_WARNED:
                                            _STOP_HOLD_WARNED.add(_tk_t)
                                            print(f"[TMonitor] ②趋势线豁免埋伏仓 {symbol}: {_tv['why']}"
                                                  " （埋伏仓只走 50 交易日 + 宽止损 −20%）", flush=True)
                                        stop_price = 0.0
                                        _skip_trend = True      # 2026-09-26：此处**不是循环** ⇒ 用标志位跳过 ✗（原写 continue ⇒ SyntaxError ✗）
                                except Exception as _eAmbEx:
                                    print("[TMonitor] ②趋势线豁免查询异常(按不豁免处理): %s"
                                          % str(_eAmbEx)[:70], flush=True)
                            if not _skip_trend:
                                stop_price = float(_tv["line"])
                                _trend_act = _tv["action"]
                                _tk_t = (symbol, "trend", datetime.now().strftime('%Y%m%d'))
                                if _tk_t not in _STOP_HOLD_WARNED:
                                    _STOP_HOLD_WARNED.add(_tk_t)
                                    print(f"[TMonitor] ②趋势线止损({_tv['action']}) {symbol}: {_tv['why']} "
                                          f"line={stop_price}")
            except Exception as _te:
                print(f"[TMonitor] ②趋势线判定异常(跳过) {symbol}: {str(_te)[:100]}")
            if not stop_price or current > stop_price:
                return
            # ── P2-4(2026-09-10): 收盘确认 / 假跌破守卫 ──
            # 狼大 2026-01-29「今天没跌破我没出, 我说了 **收盘跌破我才出**」;
            #        2026-01-12「**等收盘确认破位出清**」。
            # 复用既有纯函数 t_stop_loss_guard.evaluate_stop —— 该函数此前**只在 t_backtest 里被调用,
            # 生产从未接线**(其 docstring 却称"回测与实盘共用", 属"文档声称 > 代码实现"的又一例)。
            # 口径: 以**最近一根 5min bar 的收盘**作为"收盘价"判定 ——
            #   · tick 插针(bar 收盘未破) → 不执行(假跌破/收回幅度/缩量贴支撑/分钟企稳 由守卫细判);
            #   · 触及价取 min(bar.low, 现价), 以反映"tick 已破但 bar 未收完"的情形。
            # 未确认 → 本轮不执行(日志当日去抖); **数据不足 → 按原口径执行**(不改变既有行为, 避免因缺数据漏止损)。
            if os.getenv("WOLF_STOP_CLOSE_CONFIRM", "1").strip() not in ("0", "false", "no"):
                try:
                    from app.services import t_build as _tb
                    _v = _stop_close_confirm(current, stop_price, self._today_bars(symbol),
                                             self._prev_daily(symbol, 20), _tb._params())
                    if _v is not None and str(_v.get("action")) == "hold":
                        _tk = (symbol, round(float(stop_price), 3), datetime.now().strftime('%Y%m%d'))
                        if _tk not in _STOP_HOLD_WARNED:
                            _STOP_HOLD_WARNED.add(_tk)
                            print(f"[TMonitor] 止损未确认(收盘口径) {symbol} stop={stop_price} "
                                  f"cur={current}: {_v.get('reason')}")
                        return
                except Exception as _ge:
                    print(f"[TMonitor] 止损守卫异常(按原口径执行) {symbol}: {str(_ge)[:100]}")
            # ── ④ 止损时点约束（2026-09-10）──
            # 狼大 2026-03-23:「每天的止损绝对不应该是下午1点到2点半这个时间。。。要么你早上卖 要么你尾盘卖」。
            # 该时段(默认 [13:00,14:30)) 只预警不执行; 收盘清仓(>=14:55)不受影响。
            _tok, _treason = _stop_time_ok()
            if not _tok:
                _tk2 = (symbol, "timegate", datetime.now().strftime('%Y%m%d'))
                if _tk2 not in _STOP_HOLD_WARNED:
                    _STOP_HOLD_WARNED.add(_tk2)
                    print(f"[TMonitor] 止损被时点门拦下(仅预警) {symbol} stop={stop_price} cur={current}: {_treason}")
                return
            # ── 当日已止损过则跳过（2026-09-11 修复"止损重复执行"）──
            # 事故复现（生产 2026-09-11 09:36:25 / 09:36:29, SH588170）:
            #   本函数**按条件逐个调用**（_round 的 `for cond in ...` 里每条件调一次），
            #   原去抖查的是 t_triggers.event_type='stop_loss'，而本路径**从来不写这种行**
            #   （成功时只 update_condition_state(armed=0)）→ 该查询恒为空 → **去抖形同虚设**。
            #   同标的两个条件 → 同一轮里执行两次"盘中减半仓" 17000+17000 = **34000 全部清光**
            #   （狼大"底仓不卖"被违反：本意只减半）。
            # 修复: ①内存当日去抖（同轮/同日即时生效，不依赖 DB 时序）；
            #       ②成功后在 t_triggers 落一行 event_type='stop_loss' 作为**审计与跨重启去抖**依据。
            _dkey = (symbol, datetime.now().strftime('%Y%m%d'))
            if _dkey in self._stop_done_day:
                return
            from sqlalchemy import text
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                # ⚠️ 2026-09-19 修（用户报"买了卖不出去"排查中发现）：原用 CURRENT_DATE（DB 真钟）
                #   比较 created_at ⇒ 回放里**恒真** ⇒ 某票一旦有过一次 stop_loss 触发，
                #   **整个回放期间不再止损**（实测 drabjan7 的 SH603660 踩过）。改为 Python 钉钟日。
                _today = datetime.now().strftime("%Y-%m-%d")
                done = db.execute(text(
                    "SELECT 1 FROM t_triggers WHERE symbol = :sym AND event_type = 'stop_loss' "
                    "AND to_char(created_at, 'YYYY-MM-DD') = :today LIMIT 1"
                ), {"sym": symbol, "today": _today}).scalar()
            finally:
                db.close()
            if done:
                self._stop_done_day.add(_dkey)
                return
            # ── 卖量分级（P2-4 完整落地, 2026-09-10）──
            # 狼大 2026-01-29「今天没跌破我没出, 我说了 **收盘跌破我才出**」;
            #        2026-01-12「**等收盘确认破位出清**」。
            #   · **收盘时段**（>= WOLF_CLOSE_BREAK_HM, 默认 14:55）确认破位 → **清仓**（全部可卖, 含 100 股工程底仓）;
            #   · 盘中确认破位 → **减半仓**（保留底仓继续做T, 原语义不变, 避免盘中插针被全清）。
            # 所谓"确认破位"由上方 `_stop_close_confirm`（假跌破守卫: 收盘确认/收回幅度/分钟企稳/缩量/支撑位）判定。
            volume, _exit_mode = _stop_exit_volume(sellable, _in_close_window())
            if _trend_act == "reduce":
                # ②「收黑K跌破5日线…减仓避一下」= 减仓档（不论是否收盘窗口都只减半）
                volume = max(((volume // 2) // 100) * 100, 100) if volume >= 200 else volume
                _exit_mode = "trend_reduce"
            if volume <= 0:
                return
            _reason = ("收盘确认破位→清仓（狼大: 收盘跌破我才出）" if _exit_mode == "close_clear"
                       else "②趋势线减仓：收黑K破5日线（狼大 2026-01-12「减仓避一下」）" if _exit_mode == "trend_reduce"
                       else "②趋势线止损：跌破 60 日底线 / 34 日带量下穿（狼大 2021-01-28）")
            gw = gateway_execute(symbol, "sell", current, volume,
                                 reason=_reason, decision_source="ai_led",
                                 is_stop_loss=True,
                                 account_id=T_MONITOR_ACCOUNT)
            print(f"[TMonitor] 止损触发[{_exit_mode}] {symbol} @ {current} x{volume}: {gw.get('status')}")
            # 迭代#58g：仅在止损**成交**后冻结当日条件——
            # 此前无条件冻结：T+1 当日买入 sellable=0 时止损被拒（rejected），
            # 仍把低吸/高抛条件冻成 armed=0 并与消费式重建打架（每轮刷屏）。
            if gw.get("status") == "success":
                # 先打内存标记: 同一轮里该标的的后续条件**不得**再执行一次止损
                self._stop_done_day.add(_dkey)
                try:
                    # status='executed'（终态）—— 审计行**绝不能被认领**：
                    # claim_pending_trigger 按 status='pending' 无差别取第一条(不筛 event_type)，
                    # 留 pending 会被 t_bridge 当成待办再执行一次卖出。
                    t_db.insert_trigger({
                        "account_id": T_MONITOR_ACCOUNT,
                        "condition_id": (conds or [{}])[0].get("id") if conds else None,
                        "symbol": symbol, "event_type": "stop_loss",
                        "trigger_price": stop_price, "quote_price": current,
                        "direction": "sell", "mode": "auto",
                        "reason": "%s | stop=%s mode=%s vol=%s" % (_reason, stop_price, _exit_mode, volume),
                    }, status="executed")
                except Exception as _te:
                    print(f"[TMonitor] 止损审计行写入失败 {symbol}: {str(_te)[:80]}")
                for c in conds or []:
                    cid = c.get("id")
                    if cid:
                        t_db.update_condition_state(cid, armed=0)
        except Exception as e:
            print(f"[TMonitor] 止损扫描异常 {symbol}: {e}")


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
        except (TypeError, ValueError):
            pass
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
            except (ValueError, IndexError, TypeError):
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


def _stop_close_confirm(current: float, stop_price: float, today_bars, prev_daily,
                        params: Optional[dict] = None):
    """P2-4(2026-09-10): 止损的**收盘确认 / 假跌破**判定（纯函数, 便于单测）。

    狼大 2026-01-29「今天没跌破我没出, 我说了 **收盘跌破我才出**」;
           2026-01-12「**等收盘确认破位出清**」。
    复用既有 t_stop_loss_guard.evaluate_stop（此前只在 t_backtest 里被调用, 生产未接线）。

    口径: 以**最近一根 5min bar 的收盘**作为"收盘价"; 触及价取 min(bar.low, 现价),
          以反映"tick 已破但 bar 未收完"的情形。

    返回:
      · None            → **不做限制**(无 m5 数据 / 守卫异常) → 调用方按原口径执行止损
      · {"action":"stop"} → 确认破位, 调用方执行
      · {"action":"hold", ...} → 未确认, 调用方本轮不执行
    """
    if not today_bars:
        return None
    try:
        from app.services.t_stop_loss_guard import evaluate_stop
        b = today_bars[-1]
        low = float(b.get("low") or current) or current
        bar = {"low": min(low, current),
               "close": float(b.get("close") or 0),
               "vol": float(b.get("vol") or 0),
               "time": str(b.get("time") or "")}
        dly = None
        if prev_daily:
            dly = [{"low": (v or {}).get("low")} for _, v in sorted(prev_daily.items())]
        return evaluate_stop(bar, today_bars, stop_price, params or {}, daily_bars=dly)
    except Exception as e:
        print(f"[TMonitor] 止损守卫异常(按原口径执行): {str(e)[:100]}")
        return None

_prev_low_cache = {"at": 0.0, "sym": "", "value": False}
_daily_closes_cache: Dict[str, Any] = {}   # 账本 §9.498 ✓ 日线收盘缓存（按标的 ✓ 300s ✓）


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
    """启动 T 监控（**先装全局异常钩子** ✓ —— 用户「统一走QQ推送」✓）。"""
    try:
        from app.services import alert_hub as _ah3
        _ah3.install()
    except Exception:
        pass
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

def intraday_timing_blocked(symbol: str, is_buy: bool, pre_close: Optional[float] = None) -> bool:
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
        ok, why = _ic.timing_verdict(symbol, now.strftime("%Y%m%d"), now.strftime("%H%M"), pre_close=pre_close)
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
    for root in ['stock_5m_bt', 'recent_sync']:
        p = _os.path.join(D, root, code6 + '.json')
        try:
            d = _j.load(open(p, encoding='utf-8'))
        except Exception:
            continue
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
        except (ValueError, TypeError):
            pass
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
        except (ValueError, TypeError):
            pass

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
