# -*- coding: utf-8 -*-
"""做T系统 · 执行风控网关（唯一放行者）+ 当日可卖额度账本 + 熔断。

依据 final-t-plan.md §⑥ 与 spec t-execution-risk：
- 三权分立：Worker 事件发生器 / Agent 复核决策者 / 网关唯一放行者（本模块 = 网关）
- 网关三阶校验：硬闸门(裸空/跌停/STOP_ALL/白名单 O(1)) → 账本(可卖底仓断言/买腿≤可卖底仓/日亏回转额熔断) → 建议层(单笔%/冷却/价差成本比/频次护栏仅告警)
- 二段实时断言：落单前重拉最新持仓/价格/跌停/熔断
- 当日可卖额度原子账本：卖腿扣减(UPDATE...RETURNING)、买腿回补、卖出在途锁
- 可卖底仓分档 L0-L3；异常升级 6 类清单；STOP_ALL/日亏熔断；孤儿单处置；滑点/价差过滤
"""
from .t_leg_kinds import BUY_LEG_KINDS, is_buy_leg, LOWDIP_KINDS, is_lowdip, PREVLOW_M5_KINDS, is_prevlow_m5  # noqa: F401  §9.496 单一来源

_LOWDIP_KINDS = LOWDIP_KINDS     # §9.496 单一来源：加新腿类型只改 t_leg_kinds.py
from datetime import datetime, timedelta
import os


def _armdb_root(start: str) -> str:
    """向上找到含 `jobs/arm_db.py` 的仓库根（账本 §9.539：写死层数会数错 ✗）。"""
    p = os.path.dirname(start)
    while p and p != "/" and not os.path.exists(os.path.join(p, "jobs", "arm_db.py")):
        p = os.path.dirname(p)
    return p or os.path.dirname(start)
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text

from app.database import SessionLocal
from app.services import t_db
from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote, std_symbol
from app.services.t_regime import compute_regime as _rg_compute_regime
# ★ 账本 §9.659 ✓：环境门可切到**狼大浪型口径** ✓（`WOLF_WAVE_GATE_ONLY=1` ✓）
def compute_regime(*a, **k):
    """环境门 —— **只用狼大浪型口径** ✓（账本 §9.709 ✓：用户「旧的 t_regime 直接删掉，已证明是负收益」✓）。"""
    try:
        from app.services.wave_gate import check_gate as _wg_check
        if True:
            _seg8 = ""
            try:
                _sg = os.path.basename(os.path.normpath(os.environ.get("DATA_DIR", "") or ""))
                if len(_sg) == 8 and _sg.isdigit():
                    _seg8 = _sg
            except Exception:
                _seg8 = ""
            # ★ §9.708 ✓：必须把**模拟日**传进去 ✗（否则现场补只能拿 None ⇒ "无数据" ⇒ 沿用旧档 ✗）
            g = _wg_check("low_buy", as_of=_seg8 or None)
            # ★ 补齐旧 compute_regime 的键 ✓（§9.659 再修正：避免下游取键 KeyError ✗）
            _g = g.get("gate")
            return {"regime": "HALT" if _g == "BLOCKED" else "ACTIVE",
                    "gate_low_buy": _g, "gate_high_sell": _g,
                    "interpret_sign": 1, "index_drop": 0.0,
                    "state": g.get("mode") or "auto",
                    "why": g.get("why"), "src": "wave_gate"}
    except Exception as _e_wg2:
        print("[t_gateway] wave_gate 不可用 ⇒ **放行**（不再回退 t_regime ✓）: %s" % str(_e_wg2)[:80], flush=True)
    return {"regime": "ACTIVE", "gate_low_buy": "ALLOWED", "gate_high_sell": "ALLOWED",
            "interpret_sign": 1, "index_drop": 0.0, "state": "auto",
            "why": "wave_gate 不可用（放行 ✓）", "src": "wave_gate_fallback"}
from trade_direction import is_buy, is_sell  # noqa: E402 统一方向词表


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


ACCOUNT_T = "t"

# ── 狼大做T标的"底仓保留"股数（默认 100 股 = 药明底仓铁律口径）──
# 大底仓标的（ETF 等）覆盖为实际底仓，防止卖腿把底仓当 T仓 一次清光
# （2026-09-03：SH588170 科创半导体ETF 66,900 股为底仓，T仓=持仓-底仓）
T_BASE_FLOOR_OVERRIDES = {
    "SH588170": 66900,
}


def _fifo_net_position(account_id: str, symbol: str) -> Optional[int]:
    """paper_trades(未void) 买入-卖出 累计净持仓(权威口径, 与 vnpy bridge 一致)。
    无成交流水返回 None; 任何异常返回 None(调用方回退 paper_positions)。"""
    symbol = std_symbol(symbol)      # 2026-09-17：paper_trades 存标准形态，小写查不到 → 静默判 0
    try:
        db = SessionLocal()
        try:
            v = db.execute(text(
                "SELECT COALESCE(SUM(CASE WHEN direction IN ('买入','buy') THEN volume ELSE -volume END), 0) "
                "FROM paper_trades WHERE account_id = :a AND symbol = :s "
                "AND (voided = 0 OR voided IS NULL)"),
                {"a": account_id, "s": symbol}).scalar()
            return int(v or 0)
        finally:
            db.close()
    except Exception:
        return None


def _cum_buy_volume(account_id: str, symbol: str) -> Optional[int]:
    """累计未void买入量(底仓锚定基数: 只升不降, 卖出不缩小底仓)。"""
    symbol = std_symbol(symbol)      # 2026-09-17：底仓锚按标准形态取数（小写 → 恒 0 → 底仓被低估）
    try:
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
    except Exception:
        return None


def base_floor_shares(account_id: str, symbol: str, volume: Optional[int] = None) -> int:
    """底仓保留下限(狼大'底仓不动/T出半')。

    **2026-09-16 口径升级（用户拍板）**：委托 `app.services.t_base_floor`——锚仍是「累计未void买入」
    （卖出不动锚，保留 2026-09-07 防 588170 连卖超卖的原始意图），但补齐两点：
      ① **一次性认账(rebasing)**：当 floor > 可卖 − 100 股（T 仓被锁死）时，把该标的的锚重标为
         「可卖 × ratio」并留痕；此后 floor 只随**新增买入**增长，卖出不动它 → 既解锁、又不被啃掉。
         依据：2026-09-16 实测 SH588170(持仓23200/累计买入109000)、SH512480(1300/8000)、
         SZ002409(200/400) 的卖腿推导量恒 0；而反过来"floor 跟持仓走"会几何侵蚀底仓（09-07 已证否）。
      ② **分档 ratio**：按浪型 operation 取档（build 2/3 / t_only·side 1/2 / defense 1/3 / exit 0），
         依据狼大 2026-01-17「主升趋势就75%以上…调整就50% 有风险就30 下跌趋势就不做」。
    开关 `T_BASE_FLOOR_REBASE=0` 退回旧口径；新口径内部异常时本函数也回退旧实现（不改变可用性）。
    无成交流水(外部同步仓)时回退 volume 动态 / 旧覆盖 / 100。"""
    symbol = std_symbol(symbol)      # 2026-09-17：底仓锚/覆盖表(如 SH588170)按标准形态取数
    # ── 亏损豁免（WOLF_MA5_EXIT / WOLF_BASE_FLOOR_LOSS_EXEMPT，库内默认关；回测驱动打开）──
    # 语料「破 5 日线丢」「不把挣大钱的票拿到亏本」：浮亏且破 MA5、或浮亏超阈值 ⇒ 底仓锚归零。
    # 实测 3 月：快克智能 SH603203 因底仓锚"仅底仓无T仓可卖"被挡 3,701 次，3 月已实现 −4,279。
    try:
        from app.services.wolf_exit_rules import floor_exempt
        if floor_exempt(account_id, symbol):
            return 0
    except Exception as _e_sil1:
        try:
            from app.services import alert_hub as _ah_sil
            _ah_sil.note_silent("t_gateway.py:102", _e_sil1)
        except Exception:
            print("[silent:t_gateway.py:102] %s: %s" % (type(_e_sil1).__name__, str(_e_sil1)[:110]), flush=True)
    try:
        from app.services.t_base_floor import base_floor_shares as _impl
        return _impl(account_id, symbol, volume=volume,
                     override=T_BASE_FLOOR_OVERRIDES.get(symbol))
    except Exception as e:
        print(f"[t-gate] 底仓新口径失败，回退旧实现: {type(e).__name__}: {str(e)[:90]}")
    keep = float(os.getenv("T_BASE_KEEP_RATIO", "0.5"))
    base = _cum_buy_volume(account_id, symbol)
    if base and int(base) > 0:
        return max(int(int(base) * keep), 100)
    if volume is not None and int(volume) > 0:
        return max(int(int(volume) * keep), 100)
    ov = T_BASE_FLOOR_OVERRIDES.get(symbol)
    return int(ov) if ov is not None else 100

# ── 执行账户白名单（2026-09-02 用户决策：只有狼大做T可以操作）──
# 默认只放行股票任务账户 stock；t 账户（做T/V反/ETF动量/建仓）一律拒绝。
# 如需临时人工管理 t 账户持仓：T_EXEC_ALLOWED_ACCOUNTS=stock,t 后重启 worker。
EXEC_ALLOWED_ACCOUNTS = {
    a.strip() for a in os.getenv("T_EXEC_ALLOWED_ACCOUNTS", "stock").split(",") if a.strip()
}

# 底仓风控开关（灰度用；默认开）
T_STOP_GUARD_ENABLED = os.getenv("T_STOP_GUARD_ENABLED", "1") != "0"
# BASE_LOSS_HALF_PCT = 3.0      # 改为狼大一致后不再拦买腿; 仅参考(如需恢复先取消注释)
# BASE_LOSS_CLEAR_PCT = 5.0     # 同上; 狼大『急跌不割肉/3-3确认不清仓』, 固定浮亏%止损非狼大
MAX_DAILY_BUY_LEGS = 2        # 单标单日低吸（买腿成交）次数上限
# 买腿超上限的处理方式（`WOLF_BUY_CAP_CLAMP`，库内默认 0 = **旧行为逐字不变**：超限即拒）
#   2026-09-25 用户拍板实现。证据（账本 §9.106）：通富微电建仓 1400 股 → 0106 减 500 → 剩 900 股，
#   而加仓腿固定请求 1000 股、上限=可卖底仓×1=900 ⇒ `volume > max_buy` **整笔拒** 56 次 ⇒
#   形成「持仓 900 < 请求 1000 ⇒ 永远加不进去」的死结（**只差 100 股**）✗
#   置 1 ⇒ **缩量到上限**：1000→900 成交 ⇒ 次日可卖 1800 ⇒ 下次 1000 ≤ 1800 ✓ ⇒ 仓位可逐级打上去
#   （他语料「分步打满 75%→90%→100%」的前提；该语料此前只在死模块 t_playbook.py 里有文字 ✗）
T_BUY_CAP_CLAMP = os.getenv("WOLF_BUY_CAP_CLAMP", "0").strip() == "1"
# 买腿分档上限开关（L1 档买腿≤可卖底仓×0.5）——关闭后买腿≤可卖底仓全额（AI 自由跑用）
T_BUY_TIER_LIMIT_ENABLED = os.getenv("T_BUY_TIER_LIMIT_ENABLED", "1") != "0"
# 日回转额上限开关（日累计回转额≤3×净值）——关闭后不做回转额拦截（AI 自由跑用）
T_TURNOVER_LIMIT_ENABLED = os.getenv("T_TURNOVER_LIMIT_ENABLED", "1") != "0"
# 新建仓买量锚开关（2026-09-22 用户拍板"先做 3"）：账上确无该标的的买腿改锚「资金」而非「可卖底仓」。
# 库内默认 **0**（= 逐字旧行为，生产零影响）；回测由 `jobs/bt_env_pins.sh` 置 1。
_BUILD_CAP_ANCHOR = os.getenv("WOLF_BUILD_CAP_ANCHOR", "0").strip().lower() in ("1", "true", "yes", "on")
# L3 v1（2026-09-25）：**建仓腿按「缺口/步长」定量（把锚从"上限"变成"下限"）**。
# 语料 2026-06-15「突破下跌趋势第一根开头我加一半 突破后的第二根红K尾盘 打满」＋
# 2025 段「分步打满（75%→90%→100%）」⇒ 建仓的**下单量应由缺口决定**，而不是由 AI 建议量决定。
# 实测（账本 §9.33）：建仓腿实付量由 AI 给（0401 SZ002294 只买 300 股 = 7.7% 权益），
# 而 `WOLF_BUILD_CAP_ANCHOR` 只抬**上限** ⇒ 缺口/步长（≈16%）几乎从未用满 ⇒ 仓位长期 17~25%（他 ≥65%）。
_BUILD_SIZE_FLOOR = os.getenv("WOLF_BUILD_SIZE_FLOOR", "0").strip().lower() in ("1", "true", "yes", "on")
# 资金锚的现金兜底系数（留出费用/滑点；<=0 或读不到现金则不做这道收敛）
_BUILD_CAP_CASH_SAFETY = float(os.getenv("WOLF_BUILD_CAP_CASH_SAFETY", "0.98") or 0.98)
# 受资金锚影响的**建仓类腿型**（做T回补腿不在内：它们的"买腿≤可卖底仓"是有意为之的防堆仓）
_BUILD_CAP_KINDS = {k.strip() for k in str(os.getenv(
    "WOLF_BUILD_CAP_KINDS", "trend_break_buy,buy_253,buy_254,custom_buy,wolf_build")).split(",") if k.strip()}


# ── 卖腿「底仓 floor」执行（`WOLF_SELL_FLOOR_ENFORCE`，库内默认 0 = 逐字旧行为）──────────────
# 病灶（2026-09-22 实测）：
#   · 提示词自己要求「`volume`=计划卖出量（**≤可卖底仓，保留底仓**）」（`prompt_seeds.py:968`），
#     但**网关从不执行 floor**：`validate_order_at` 的卖腿分支只做"裸空"断言
#     （`sellable < volume` 才拒），且 `sellable <= floor` 时直接 `pass`（跳过检查）；
#   · 结果：AI 显式给量的止盈/高抛腿能把**底仓**一起卖掉。实测 T5 清仓 31 次中
#     **18 次是止盈/高抛腿把持仓打到 0**（4 月 12/16），理由原文「高抛卖腿且**可卖底仓200股充足**」；
#     典型 SH600584 0410：可卖 400 / floor 200，当天 100+100+**200** 三笔卖光
#     （第三笔正落在 `sellable<=floor` 的 `pass` 分支）。
#   · 后果：底仓归零 → 写 reset → 账户进入"建仓→高抛清仓→再建仓"循环：
#     实测 T5 清仓次数 1月2 / 2月3 / **3月10 / 4月16**，持仓期中位 8→10→3→3 天，
#     3-12 起「老仓（买入 ≥4 交易日）占比」**恒为 0%** ⇒ 底仓再也建不起来
#     ⇒ 反弹段（市场 +9.6%、持仓端 +22.7%）组合只吃到 +5.7%。
# 口径：把卖量**收敛到「T 仓空间」= max(可卖 − 底仓 floor, 0)**（只收紧、不新增买入）；
#   **口径（2026-09-22 修正）**：只作用于**止盈类**卖腿（`sell_floor_applies`，正向判据，
#   与 0.5d 方案③同源）；止损/破位/减仓/未知**一律放行**（语料「止血动作必须能执行」+ 允许「减仓到 40%/50%」）；
#   开关关 / 取数失败 ⇒ 原样返回（fail-safe，绝不放大）。
_SELL_FLOOR_ENFORCE = os.getenv("WOLF_SELL_FLOOR_ENFORCE", "0").strip().lower() in ("1", "true", "yes", "on")


# ★★ 账本 §9.718 ✓（用户 2026-10-07 复盘拍板方向 ✓：「**强的留 弱的丢**」）：
#   问题（实测 ✓）：SZ002156 被**一路卖到 0 股** ✗ ——
#     01-05/06 建仓 1800 股 ⇒ 01-08~01-19 六笔卖出（40.47/42.39/43.15/42.66/40.30/46.68 ✓）⇒ 持 0 ✗
#     而它随后涨到 **56.78** ✗ ⇒ 该票"已实现 +4,212" vs "持有不动 **+31,054**" ⇒ **差 −26,842** ✗
#       （全样本里**唯一**明确的"操作反噬"✓；其余 18 只操作都是正贡献 ✓）
#   根因 ✓：卖腿 floor（`WOLF_SELL_FLOOR_ENFORCE` ✓）**只作用于"止盈类"** ✓，
#     而该票的卖出理由多是「**防御性减仓**」「**量能分层离场**」「前次 wait 的④已不成立」✗
#     ⇒ 被 `sell_floor_applies` **豁免** ✓ ⇒ **底仓被一起卖掉** ✗
#     本意（"止血动作必须能执行"✓）没错 ✓，但**趋势强时**这类减仓**不是止血**、是做T噪声 ✗
#   口径（照用户原话 ✓）：**趋势强（MA 多头/未破位 ✓）时，做T的减仓腿只动 T 仓、不碰底仓** ✓
#     判据复用建仓那道 **`t_build.trend_gate`** ✓（同一套 MA20 方向/均线排列/反弹陷阱 ✓，不另立口径 ✓）
#     ★ **止损腿（is_stop_loss=True）永不介入** ✓ —— 「止血动作必须能执行」✓ 仍优先 ✓
#   开关：`WOLF_STRONG_TREND_T_ONLY`（**库内默认 0** ⇒ 生产零影响 ✓；回测由 pins 置 1 ✓）
_STRONG_T_ONLY = str(os.getenv("WOLF_STRONG_TREND_T_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")


def strong_trend_t_only_enabled() -> bool:
    """开关是否打开 ✓（供测试与自检 ✓）。"""
    return bool(_STRONG_T_ONLY)


def _sim_day8() -> str:
    """当前**模拟日**（回测 = DATA_DIR 末段 ✓；生产 = 今天 ✓）。与 wave_gate.sim_day8 同口径 ✓。"""
    try:
        _seg = os.path.basename(os.path.normpath(os.environ.get("DATA_DIR", "") or ""))
        if len(_seg) == 8 and _seg.isdigit():
            return _seg
    except Exception as _e_sd:
        print("[gateway] _sim_day8 取 DATA_DIR 末段失败: %s" % str(_e_sd)[:60], flush=True)
    try:
        import datetime as _d8
        return _d8.datetime.now().strftime("%Y%m%d")
    except Exception:
        return ""


_STRONG_TREND_CACHE: Dict[str, bool] = {}


def strong_trend(symbol: str) -> bool:
    """该标的当前是否**趋势强** ✓ —— 复用建仓判据 `t_build.trend_gate`（不另立口径 ✓）。

    ★ 缓存粒度 = **(标的, 模拟日)** ✓：卖腿可能一分钟来好几条 ✓，而趋势判据一天内不变 ✓
      ⇒ 避免每条卖腿都去取数（实测取数在离线环境会**卡住** ✗ ⇒ 缓存同时也是一道保险 ✓）。
    取数失败/判据异常 ⇒ **False**（= 不介入 ⇒ 保持旧行为 ✓，fail-safe ✓）。
    """
    _key = "%s|%s" % (str(symbol or ""), _sim_day8())
    if _key in _STRONG_TREND_CACHE:
        return _STRONG_TREND_CACHE[_key]
    _res = False
    try:
        from app.services.t_build import trend_gate as _tg
        _ok, _why = _tg(symbol)
        _res = bool(_ok)
    except Exception as _e_st:
        print("[gateway] 趋势强判定失败(不介入): %s" % str(_e_st)[:70], flush=True)
        _res = False
    _STRONG_TREND_CACHE[_key] = _res
    return _res


def sell_floor_applies(reason: str = "", is_stop_loss: bool = False, kind: str = "") -> bool:
    """底仓 floor 是否作用于这笔卖腿 —— **只作用于「止盈类」**（与网关 0.5d 方案③同一判据）。

    ⚠️ 为什么改用**正向**判据，而不是"豁免关键词"（2026-09-22 自查修正，重要）：
      第一版写成 `_SELL_FLOOR_EXEMPT_KW`（止损/破位/清仓/减仓/风控/避险/离场…）的**反向**排除，
      结果被 AI 的**理由文案**骗到 —— 止盈腿的说明里常写「**未破止损**」「未跌破止损（②不成立）」
      「无止损价可破」，全都含「止损」二字 ⇒ **假豁免**。
      实测 T10 前 8 天 20 笔卖腿：7 笔是止盈类，其中 **4 笔被误放行** ⇒ 这条闸几乎等于没开
      （日志里 0 次收敛就是这么来的）。
      正向列表 `wolf_base_hold.is_take_reason`（高抛/fib/profit_take/止盈）不会被这种文案骗到，
      而且**与 0.5d 那道兄弟闸（方案③）同源**，两道闸的作用面天然一致。
    语义：止盈类 ⇒ True（受 floor 约束）；止损/破位/减仓/未知/取不到判据 ⇒ False（**一律放行**，fail-safe）。
    """
    if is_stop_loss:
        return False
    txt = str(kind or reason or "")
    if not txt:
        return False
    try:
        from app.services import wolf_base_hold as _bh
        return bool(_bh.is_take_reason(txt))
    except Exception:
        return False


def sell_floor_cap(account_id: str, symbol: str, volume: int,
                   sellable: Optional[int] = None,
                   reason: str = "", is_stop_loss: bool = False,
                   kind: str = "", force: bool = False) -> Tuple[int, str]:
    """把**止盈类**卖腿量收敛到 T 仓空间。返回 (允许卖量, 归因)。默认关/非止盈类 ⇒ 原值。

    `force=True`（账本 §9.718 ✓）：**趋势强**时对**减仓/离场类**也生效 ✓
      —— 仍然**只收紧、不放大** ✓，且**止损腿永不 force** ✓（止血优先 ✓）。
    """
    v = int(volume or 0)
    if v <= 0:
        return v, "底仓floor未介入（无量）"
    if not _SELL_FLOOR_ENFORCE:
        return v, "底仓floor未介入（WOLF_SELL_FLOOR_ENFORCE=0）"
    if is_stop_loss and force:
        force = False          # ★ 止损腿永不 force ✓（「止血动作必须能执行」✓）
    if not force and not sell_floor_applies(reason, is_stop_loss, kind):
        return v, "底仓floor未介入（非止盈类：止损/破位/减仓/未知一律放行）"
    try:
        sel = int(sellable) if sellable is not None else \
            int((ledger_item(get_sellable_ledger(account_id), symbol) or {}).get("sellable") or 0)
        if sel <= 0:
            return v, "底仓floor未介入（无券可卖）"
        floor = int(base_floor_shares(account_id, symbol, volume=sel) or 0)
        room = max(sel - floor, 0)
        room = (room // 100) * 100
        if v > room:
            return room, ("底仓floor：可卖 %d − 底仓 %d ⇒ 只放行 T 仓 %d 股（原报 %d）"
                          % (sel, floor, room, v))
        return v, "底仓floor：在 T 仓空间内（可卖 %d − 底仓 %d = %d ≥ %d）" % (sel, floor, room, v)
    except Exception as e:
        return v, "底仓floor取数失败(放行原值): %s" % str(e)[:60]


def _is_build_cap_kind(kind: Optional[str]) -> bool:
    """该买腿是否属"建仓类"（⇒ 适用资金锚）。空/未知 ⇒ False（走旧口径，绝不放大）。"""
    return str(kind or "").strip() in _BUILD_CAP_KINDS

# ── 参数（P4 敏感度扫描标定，当前保守档初值） ──
MAX_SINGLE_ORDER_PCT = 0.05        # 单笔 ≤ 净值 5%（建议层）  # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
DAILY_LOSS_BREAKER_PCT = 0.02      # 日亏 2% 熔断（硬闸门）  # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
DAILY_LOSS_WARN_PCT = 0.01         # 日亏 1% 预警（建议层）
MAX_SELL_FLOOR_RATIO = 1.0         # 买腿 ≤ 可卖底仓（L2 默认 1:1）
COOLDOWN_AFTER_LOSS_MIN = 15       # 亏损后冷却（标准档 15min）  # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
SLIPPAGE_PCT = 0.0003             # 滑点参数化假设 0.03%  # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)（做T低价吃bid/高价抛ask, 实际滑点小；原0.1%高估）
COST_RATIO_LIMIT = 0.2             # 滑点+手续费 > 价差空间 20% 不触发
MIN_T_SPREAD_FILTER = 0.002        # 最低价差过滤（相对价 0.2%）
MAX_DAILY_TURNOVER_RATIO = 3.0     # 日累计回转额 ≤ 3×净值（主指标）  # ⛔自设(无语料依据, 见 docs/wolf-buy-parameter-ledger.md §4)
# S7 统一口径(2026-09-10): wolf 回补单日上限。与 wolf_253_build.WOLF_REFILL_MAX_PER_DAY 同源(同一 env),
# 使执行层护栏与策略层 refill_253 不再各用一套规则。狼大「来来回回做几次就行了」→ 默认 2。
MAX_WOLF_REFILL_PER_DAY = int(os.getenv("WOLF_REFILL_MAX_PER_DAY", "2"))
FLOOR_LOWER_RATIO = 0.5            # 底仓保留下限（市值 ≥ 成本 50%）
TRIGGER_EXEC_TIMEOUT_MIN = 2       # human_confirm 超时 2min → cancelled
# 2026-09-17 修复（P1）：claimed_at 的超时判定必须与写入端同钟源。
#   · 默认：`t_db.claim_pending_trigger` 用 **Python 钟**写 claimed_at（与这里的 datetime.now() 同源），
#     生产两钟本来就一致 ⇒ 行为中性；回测里 Python 被钉到 as-of、DB 是真钟，修复前差值恒为负
#     → **永不超时**，本该转 human 的单仍走 agent。
#   · 遗留行（claimed_at 由 DB `now()` 写入、或跨容器钟源不一致）会出现"未来时间"：
#     `now - claimed_at < -T_GATE_CROSS_CLOCK_TOL_S` 时按**已超时**处理（fail-closed → human，
#     宁可人工确认也不放行）。`T_GATE_CROSS_CLOCK_TIMEOUT=0` 可关掉这条兜底。
def _cross_clock_tol_s() -> float:
    """跨钟源兜底的容忍秒数（-tol 以下才算"未来时间"）；返回 0 = 关闭该兜底。

    开关（运行时读取，便于灰度）：`T_GATE_CROSS_CLOCK_TIMEOUT=0` 关闭；`T_GATE_CROSS_CLOCK_TOL_S` 调容忍度。
    """
    if os.getenv("T_GATE_CROSS_CLOCK_TIMEOUT", "1").strip().lower() in ("0", "false", "no"):
        return 0.0
    try:
        return float(os.getenv("T_GATE_CROSS_CLOCK_TOL_S", "60"))
    except ValueError:
        return 60.0

# 可卖底仓分档
TIER_L0 = "L0"  # 禁用低吸（下跌市/近跌停）
TIER_L1 = "L1"  # 买腿 ≤ 可卖底仓×0.5
TIER_L2 = "L2"  # 买腿 ≤ 可卖底仓×1.0（默认）
TIER_L3 = "L3"  # 买腿 ≤ 可卖底仓×1.5 + 日回转额上限


# ────────────────────────────────────────────────────────────────
# t 账户净值（统一基准，替换散落的 initial=200000 硬编码）
# ────────────────────────────────────────────────────────────────

def t_net_asset(account_id: str = "t") -> float:
    """读取指定账户当前净值 = 可用资金 + 冻结资金 + 持仓市值（以 paper_account_info 为准）。

    account_id 默认 't'（做T账户）。做T交易在 stock 账户时应传 'stock'（validate_order 已按账户口径修正）。
    调额（POST /t/account/capital-adjust）后自动反映新值。
    读取失败时回退注册资金（paper_accounts.initial_capital），再退 200000 保守值。
    """
    try:
        db = SessionLocal()
        try:
            acct = db.execute(text(
                "SELECT available_cash, frozen_cash FROM paper_account_info WHERE account_id = '%s'" % account_id
            )).mappings().first()
            positions = db.execute(text(
                "SELECT volume, avg_price FROM paper_positions WHERE account_id = '%s' AND volume > 0" % account_id
            )).mappings().all()
            if acct is None:
                reg = db.execute(text(
                    "SELECT initial_capital FROM paper_accounts WHERE account_id = '%s'" % account_id
                )).mappings().first()
                return float(reg["initial_capital"] or 200000) if reg else 200000.0
            available = float(acct.get("available_cash") or 0)
            frozen = float(acct.get("frozen_cash") or 0)
            pos_value = sum(float(p["volume"] or 0) * float(p["avg_price"] or 0) for p in positions)
            total = available + frozen + pos_value
            return round(total, 2) if total > 0 else 200000.0
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] t_net_asset 读取失败: {e}")
        return 200000.0


def _account_available_cash(account_id: str) -> Optional[float]:
    """账户**可用现金**（供"新建仓资金锚"兜一道）。

    ⚠️ 返回 `None` = **读失败/未知**（调用方按"不加这道约束"处理，fail-open）；
    返回 `0.0` = **确实没钱**（调用方必须拦 —— 两者混用会造成"零现金仍下单"的洞）。
    """
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT available_cash FROM paper_account_info WHERE account_id = :a"
            ), {"a": account_id}).mappings().first()
            return float(row["available_cash"] or 0) if row else None
        finally:
            db.close()
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────
# 账本（当日可卖额度）
# ────────────────────────────────────────────────────────────────

def get_sellable_ledger(account_id: str = "t") -> Dict[str, Dict[str, Any]]:
    """读取指定账户"当日可卖额度"账本（基于 paper_positions + 当日成交推算）。

    可卖额度 = 持仓（昨日及以前净买入，T+1 可卖）− 今日已卖 + 今日买回成交回补。
    简化实现：以 paper_positions 当日持仓为基数，扣除今日买入（当日不可卖）得到可卖部分。
    2026-09-02: 加 account_id 参数(默认 t 兼容), 支持股票任务账户 stock 做T。
    """
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            # 今日买入量（T+1 锁定，不可卖）
            buys = db.execute(text(
                "SELECT symbol, COALESCE(SUM(volume), 0) AS v FROM paper_trades "
                "WHERE account_id = :acc AND direction IN ('买入','buy') "
                "AND (voided = 0 OR voided IS NULL) "
                "AND substr(created_at, 1, 10) = :today GROUP BY symbol"
            ), {"acc": account_id, "today": today}).mappings().all()
            buy_map = {r["symbol"]: int(r["v"]) for r in buys}
            # 持仓
            pos = db.execute(text(
                "SELECT symbol, volume, frozen, avg_price FROM paper_positions "
                "WHERE account_id = :acc AND volume > 0"
            ), {"acc": account_id}).mappings().all()
            ledger = {}
            for p in pos:
                symbol = p["symbol"]
                volume = int(p["volume"] or 0)
                today_buy = buy_map.get(symbol, 0)
                # 2026-09-07 修复(588170 连卖): volume 以 paper_trades FIFO 净持仓为权威
                # (bridge 播种曾把成交后的持仓覆盖回旧值 → 系统以为没卖 → 重复卖)；
                # paper_positions 无成交记录(外部同步仓)时回退其 volume。并软同步表。
                fifo = _fifo_net_position(account_id, symbol)
                if fifo is not None and fifo >= 0 and abs(fifo - volume) > 0:
                    db.execute(text(
                        "UPDATE paper_positions SET volume = :v, updated_at = :u "
                        "WHERE account_id = :a AND symbol = :s"),
                        {"v": fifo, "u": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         "a": account_id, "s": symbol})
                    volume = fifo
                # 可卖 = 持仓 − 今日买入（当日买回部分 T+1 锁定）
                # ETF 例外（2026-09-14）：ETF 是 **T+0**，当日买入当日可卖 —— 狼大做 T 的主要载体就是 ETF
                # （2025-04-03「T+0 2 个点我就够了」、2026-09-02「ETF 能 T 出 2 个点即合格」）。
                # 开关 WOLF_ETF_T0=0 退回"一律 T+1"。
                _etf_t0 = os.getenv("WOLF_ETF_T0", "1").strip() not in ("0", "false", "no")
                try:
                    from app.services.roundtrip_sell import is_etf as _is_etf
                    _is_e = bool(_etf_t0 and _is_etf(symbol))
                except Exception:
                    _is_e = False
                sellable = volume if _is_e else max(volume - today_buy, 0)
                ledger[symbol] = {
                    "symbol": symbol,
                    "volume": volume,
                    "today_buy": today_buy,
                    "sellable": sellable,
                    "avg_price": float(p["avg_price"] or 0),
                }
            db.commit()
            return ledger
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 读取可卖账本失败: {e}")
        return {}


def _atomic_decrement_sellable(symbol: str, qty: int) -> bool:
    """卖腿下单原子扣减可卖额度（当前以 paper_positions 为基础，由引擎撮合天然保证 T+1）。

    说明：模拟盘撮合（PaperTradingEngine）在卖出时校验持仓与 T+1 规则；
    此处提供账本级断言（可卖≥下单量），与引擎校验双保险。
    """
    ledger = get_sellable_ledger()
    item = ledger_item(ledger, symbol)
    if not item:
        return False
    return item["sellable"] >= qty


def is_sell_in_transit(symbol: str, account_id: str = "t") -> bool:
    """卖出在途锁定：当日有未确认成交的卖单则该标的锁买腿。"""
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            row = db.execute(text(
                "SELECT 1 FROM paper_orders WHERE account_id = :acc AND symbol = :symbol "
                "AND direction IN ('卖出','sell') AND status IN ('提交中', '部分成交') "
                "AND substr(created_at, 1, 10) = :today LIMIT 1"
            ), {"acc": account_id, "symbol": symbol, "today": today}).fetchone()
            return row is not None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 卖出在途查询失败: {e}")
        return False


# ────────────────────────────────────────────────────────────────
# 熔断 / 状态
# ────────────────────────────────────────────────────────────────

def check_breakers() -> Tuple[bool, str]:
    """日亏损熔断 + STOP_ALL + 连续亏损 → 返回 (触发, 原因)。"""
    risk = t_db.get_risk_state() or {}
    if risk.get("stop_all"):
        return True, "STOP_ALL 已触发"
    if risk.get("manual_lock"):
        return True, f"人工锁定: {risk.get('lock_reason') or 'manual'}"
    # 2026-09-11 删除：原 `daily.get("risk_breaker")` 日亏损熔断守卫 —— 该列**从来没有任何写入方**
    # （只有这两处读、0 处写，属死守卫），按用户决定删除而非补一个无依据的阈值。
    # 仍在生效的风控：stop_all / manual_lock / 连续亏损≥3（均来自 risk_state 表）。
    # 连续亏损
    if int(risk.get("consecutive_losses") or 0) >= 3:
        return True, "连续亏损 ≥ 3 次，临时禁自动"
    return False, ""


def _realized_today(account: str = "t") -> float:
    """**当日已实现盈亏（权威口径）= paper_trades 当日 profit 合计**（未 void）。

    ⚠️ 2026-09-11 修：原来 `_daily_pnl_pct()` 只读 `t_daily_state.realized_pnl`，
    而该列**从来没被写过**（`_update_daily_ledger` 的注释写着"realized_pnl 由引擎成交推送补全"，
    但没有任何调用方补；生产实测 10 个交易日全为 0，同期 paper_trades 有 22 笔非零 profit）
    → 结果是 **"日亏 1% 预警"（本文件 line 549/775 与 t_build B7）从来没触发过**。
    改为直接以 paper_trades 为准（该表由成交写入，是事实来源）。
    """
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            # ⚠️ paper_trades.voided 是 **integer** 列（不是 boolean）——
            #    写成 COALESCE(voided, false) 会 DatatypeMismatch（生产实测踩过）
            row = db.execute(text(
                "SELECT COALESCE(SUM(profit), 0) FROM paper_trades "
                "WHERE account_id = :a AND trade_date = :d AND COALESCE(voided, 0) = 0"
            ), {"a": account, "d": datetime.now().strftime("%Y-%m-%d")}).fetchone()
            return float(row[0] or 0) if row else 0.0
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 当日已实现盈亏读取失败: {type(e).__name__}: {str(e)[:70]}")
        daily = t_db.get_daily_state() or {}
        return float(daily.get("realized_pnl") or 0)


def _daily_pnl_pct() -> float:
    """当日已实现盈亏 / 初始资金 百分比（近似，基准用 t 账户当前净值）。

    口径见 `_realized_today()`（paper_trades 当日 profit 合计）。
    """
    initial = t_net_asset()
    realized = _realized_today()
    return realized / initial * 100 if initial else 0.0


# ────────────────────────────────────────────────────────────────
# 可卖底仓分档
# ────────────────────────────────────────────────────────────────

def _floor_tier(regime: str, near_limit_down: bool) -> str:
    if near_limit_down or regime == "HALT":
        return TIER_L0
    if regime == "CAUTIOUS":
        return TIER_L1
    return TIER_L2


def _max_buy_volume_ex(symbol: str, tier: str, ledger: Optional[dict] = None,
                       price: Optional[float] = None,
                       condition_id: Optional[int] = None,
                       kind: Optional[str] = None) -> Tuple[int, str]:
    """按分档计算买腿上限（股数）+ **归因说明**（2026-09-17 用户要求把拦阻原因说清楚）。

    为什么要有 why：旧文案 `当前档位 L2 禁止低吸` 把两件不相干的事混在一起——
    `tier` 是**行情档位**（`_floor_tier`：HALT/近跌停→L0、CAUTIOUS→L1、其余→L2），
    而真正拦下来的往往是**无底仓**（可卖=0）且不是条件单建仓（`condition_id=None`，
    `wolf_*` 触发都是这种）。旧文案让人误以为"档位禁止低吸"，排查时被带偏。

    口径（与旧实现完全一致，只是加说明）：
      T_BUY_TIER_LIMIT_ENABLED=0（AI 自由跑）→ 不限档位上限；
      tier=L0 → 0；可卖<=0 → 仅条件单建仓走 `build_sizing`，否则 0；
      L1 → 0.5×可卖；L2 → 1×可卖；L3 → 1.5×可卖（额度上限在建议层）。
    返回的 why 以 `买腿上限0｜` 开头，便于日志/看板按前缀分类。
    """
    if ledger is None:
        ledger = get_sellable_ledger()
    item = ledger_item(ledger, symbol)
    sellable = item["sellable"] if item else 0
    if not T_BUY_TIER_LIMIT_ENABLED:
        # 两分法（狼大语料：底仓≥65% + 日内T 20%；`WOLF_T_CAPACITY_2TIER` 默认关）
        # 为什么挂在这里：档位上限关掉后本函数是"买腿股数"的**唯一收敛点**，
        #   而回测实测的病灶正是"每笔只买 200 股、平均只投出 43.9%"。
        # 关时逐字返回旧值，开时才施加"建仓补缺口 / 做T受 20% 额度"两个锚。
        try:
            from app.services import t_capacity as _cap
            if _cap.enabled():
                # ⚠️ 2026-09-18 修：此处原先引用 `T_MONITOR_ACCOUNT`，但本模块**从未定义/导入**它
                #   ⇒ 每次买腿都抛 NameError 被外层 except 吞掉 ⇒ **两分法（底仓≥65%/日内T≤20%）从未生效**
                #   （实测日志 `两分法买腿收敛失败(放行旧值) … name 'T_MONITOR_A…'`）——这正是"底仓一直上不到 65%"
                #   的直接原因之一：没有任何机制在建仓时按缺口加量。改为按环境变量取账户（与 t_monitor 同口径）。
                _acct = os.getenv("T_MONITOR_ACCOUNT", "stock")
                _eq = _cap.account_equity(_acct, ledger=ledger)
                if _eq:
                    _pv = 0.0
                    for _s, _it in (ledger or {}).items():
                        try:
                            _pv += float(_it.get("volume") or 0) * float(_it.get("avg_price") or 0)
                        except Exception as _e_sil1:
                            _silent_alert("t_gateway.py:551", _e_sil1)
                            continue
                    # 传"无上限"进去，让两分法自己决定收敛：建仓→缺口/3 上限 + 最小有效规模；
                    # 做T/加仓→20% 日内T 预算上限。（旧行为在开关关时就是无上限，故这里不额外收紧）
                    _v, _why = _cap.clamp_buy_volume(10**9, float(price or 0),
                                                     _eq, _pv, is_build=(sellable <= 0))
                    return _v, "买腿不设档位上限（T_BUY_TIER_LIMIT_ENABLED=0）｜" + _why
        except Exception as _ce:
            print("[t-gateway] 两分法买腿收敛失败(放行旧值) %s: %s" % (symbol, str(_ce)[:70]))
        return max(sellable, 10**9), "买腿不设档位上限（T_BUY_TIER_LIMIT_ENABLED=0，AI 自由跑）"
    if tier == TIER_L0:
        return 0, f"买腿上限0｜行情档位 L0（HALT/近跌停）：今日不做T买腿"
    # ── 新建仓买量锚（`WOLF_BUILD_CAP_ANCHOR`，库内默认 **0**；2026-09-22 用户拍板"先做 3"）──────
    # 病灶（实测 T6 0401→0428 整整 3 周空转）：买腿上限锚在「**可卖底仓** × 档位系数」上 ⇒
    #   · 空仓 ⇒ 可卖 0 ⇒ 上限 0（非条件单的文案是"账上无仓可回补"）；
    #   · 一手仓/小仓 ⇒ 上限 100/900/1200 ⇒ 建仓腿直接被拦
    #     （实测原文「买腿 200 超过档位 L2 上限 100（买腿≤可卖底仓）」，T6 全窗 8 个决策、16 条，
    #      例 SH603061 @315 元、SZ001309 @360 元、SH600641 要 1600 只有 1200）。
    #   ⇒ **空仓状态无法自愈**：唯一能重开仓的建仓腿，被它自己的"可卖底仓"锁死。
    # 作用面（两条边界，缺一不可）：
    #   ① **只对建仓类腿型**（`WOLF_BUILD_CAP_KINDS`，默认 trend_break_buy / buy_253 / buy_254 /
    #      custom_buy / wolf_build）—— `wolf_zheng_t_buy`/`custom_prevlow` 这类**做T回补腿**
    #      "买腿≤可卖底仓"是有意为之（防堆仓），**一律走旧口径**（也守住狼大「没抄底没资格T」）；
    #   ② **只抬不压**：资金锚只在新上限 > 旧上限时生效，否则原样落回旧分支
    #      ⇒ 不可能因为本开关"多拦"任何一笔。
    # 资金锚 = min(底仓缺口 / `WOLF_BUILD_STEP_FRACTION`, 可用现金 × `WOLF_BUILD_CAP_CASH_SAFETY`)，
    #   复用两分法 `t_capacity.clamp_buy_volume(is_build=True)`（含最小有效规模 MIN_BUILD_NOTIONAL）。
    # 生产零影响：开关默认 0 ⇒ 逐字旧行为；取不到总资产/异常 ⇒ 回退旧分支（fail-safe，不放大）。
    if _BUILD_CAP_ANCHOR and price and float(price) > 0 and _is_build_cap_kind(kind):
        try:
            from app.services import t_capacity as _capb
            _acct_b = os.getenv("T_MONITOR_ACCOUNT", "stock")
            _eq_b = _capb.account_equity(_acct_b, ledger=ledger)
            if _eq_b:
                _pv_b = 0.0
                for _s_b, _it_b in (ledger or {}).items():
                    try:
                        _pv_b += float(_it_b.get("volume") or 0) * float(_it_b.get("avg_price") or 0)
                    except Exception as _e_sil2:
                        _silent_alert("t_gateway.py:589", _e_sil2)
                        continue
                _v_b, _why_b = _capb.clamp_buy_volume(10 ** 9, float(price), float(_eq_b), _pv_b,
                                                      is_build=True)
                _cash_b = _account_available_cash(_acct_b)      # None = 读失败 ⇒ 不加这道约束
                if _cash_b is not None:
                    _cap_cash = int(_cash_b * _BUILD_CAP_CASH_SAFETY / float(price) / 100) * 100
                    if _cap_cash < _v_b:
                        _v_b = max(_cap_cash, 0)
                        _why_b += "；按可用资金 %.0f 元收敛到 %d 股" % (_cash_b, _v_b)
                # 旧口径上限（用于"只抬不压"的比较）
                if sellable <= 0:
                    _old_cap = 0
                elif tier == TIER_L1:
                    _old_cap = int(sellable * 0.5)
                elif tier == TIER_L2:
                    _old_cap = int(sellable)
                else:
                    _old_cap = int(sellable * 1.5)
                if _v_b > _old_cap:
                    return _v_b, ("新建仓资金锚（WOLF_BUILD_CAP_ANCHOR=1，腿型=%s）：锚=底仓缺口/步长 与可用资金，"
                                  "不再受「可卖底仓×档位=%d」所限｜" % (kind, _old_cap)) + _why_b
        except Exception as _be:
            print("[t-gateway] 新建仓资金锚失败(回退旧口径) %s: %s" % (symbol, str(_be)[:70]))
    if sellable <= 0:
        # 无底仓：条件单建仓（买腿=开仓），上限取建仓规模建议股数
        if condition_id and price and price > 0:
            try:
                from app.services.t_build import build_sizing
                sz = build_sizing(symbol, price)
                if sz.get("pass"):
                    _v = int(sz.get("suggest_volume") or 0)
                    return _v, f"无底仓→按条件单建仓规模放行 {_v} 股（build_sizing）"
                _r = str(sz.get("reason") or sz.get("reasons") or "")[:80]
                return 0, f"买腿上限0｜无底仓、条件单建仓规模未通过（build_sizing 未放行{('：' + _r) if _r else ''}）"
            except Exception as _e:
                return 0, f"买腿上限0｜无底仓、条件单建仓规模计算失败（{str(_e)[:60]}）"
        return 0, ("买腿上限0｜无底仓且非条件单（可卖=0、condition_id 为空）："
                   "这是回补买腿，账上无仓可回补（行情档位 %s 与本次拦截无关）" % tier)
    if tier == TIER_L1:
        return int(sellable * 0.5), f"档位 L1（谨慎）：上限 0.5×可卖 {sellable}"
    if tier == TIER_L2:
        return sellable, f"档位 L2：上限 1×可卖 {sellable}"
    # L3：1.5× 但配日回转额上限（此处按 1.5× 计算，额度上限在建议层）
    return int(sellable * 1.5), f"档位 L3：上限 1.5×可卖 {sellable}"



# ── L3 v1 第二半：**腿型当日额度**（`WOLF_ZT_DAY_BUDGET`，库内默认 0=关）────────────────
# 语料（逐字）2026-03-31「必须是有底仓、自己一直关注的方向（没有底仓的不做）；**只用10%仓位做T**」
#   ＋ 2026-08-… 「用10%仓位做做T」/「用10%仓位或半仓内做T」。
# 与 ③（单笔 ≤10%）的区别：③ 管"单笔"，本额度管"**当日累计**"（他原话是仓位/额度口径）。
# 核算方式：**进程内当日累加器**（key=(account, day)）—— 回测里每天一个进程 ⇒ 当日精确；
#   进程重启会清零（fail-open：允许更多，不误拦）。只对 `wolf_zheng_t_buy`（正T买腿）生效。
_ZT_DAY_BUDGET = float(os.getenv("WOLF_ZT_DAY_BUDGET", "0") or 0)
_ZT_USED: Dict[Tuple[str, str], float] = {}


def zt_day_budget_pct() -> float:
    """当日正T买额度（占**总资产**百分比）；0 = 关。"""
    try:
        v = float(os.getenv("WOLF_ZT_DAY_BUDGET", "0") or 0)
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def clamp_zt_day_budget(volume: int, price: float, equity: Optional[float], used_today: float,
                        pct: Optional[float] = None, lot: int = 100) -> Tuple[int, str]:
    """当日正T买额度（纯函数）：剩余额度 = pct%·总资产 − 当日已用；超出 ⇒ 向下取整手缩量。

    关 / 无量无价 / 权益不可用 ⇒ 原样返回（fail-open，不臆造）。缩到 0 ⇒ 调用方拒绝该腿。
    """
    v = int(volume or 0)
    p = float(price or 0)
    _pct = zt_day_budget_pct() if pct is None else float(pct or 0)
    if _pct <= 0:
        return v, "当日正T额度未开（WOLF_ZT_DAY_BUDGET=0）"
    if v <= 0 or p <= 0:
        return v, "当日正T额度未介入（无量/无价）"
    try:
        eq = float(equity or 0)
    except Exception:
        eq = 0.0
    if eq <= 0:
        return v, "当日正T额度未介入（总资产不可用）"
    budget = eq * _pct / 100.0
    remain = budget - float(used_today or 0)
    if remain <= 0:
        return 0, "当日正T额度已用尽（%.0f/%.0f 元）" % (used_today, budget)
    if p * v <= remain:
        return v, "当日正T额度内（用 %.0f/%.0f）" % (used_today + p * v, budget)
    _lot = max(int(lot or 100), 1)
    v2 = int(remain / p / _lot) * _lot
    return max(v2, 0), "当日正T额度：剩余 %.0f 元/上限 %.0f 元 ⇒ %d→%d 股" % (remain, budget, v, v2)


def zt_used_today(account_id: str, day: str) -> float:
    return float(_ZT_USED.get((account_id, str(day)), 0.0))


def zt_add_used(account_id: str, day: str, amount: float) -> None:
    _ZT_USED[(account_id, str(day))] = zt_used_today(account_id, day) + float(amount or 0)



# ── 低吸腿当日额度（腿型额度预算；账本 §9.70，用户 2026-09-25 拍板"落地"）──────────────
# 依据：四臂真实成交 FIFO 归因 —— 低吸(253/254) 264 笔、名义占 **53%**、已实现 **−0.51%/名义**；
#   而趋势突破腿 70 笔、+5.75%。把额度从低吸挪到趋势突破：已实现 **+67k → +231k**（单位 0.54% → 2.12%），
#   且名义占用 −13%。⇒ 本开关只**压低吸腿的当日总量**（不抬别的腿；挪动由"趋势突破腿"自己的条件决定）。
# 口径：`WOLF_LOWDIP_DAY_BUDGET` = 当日低吸腿累计名义 ≤ 该百分比 × 总资产（0 = 关，库内默认）。
_LD_USED: Dict[Tuple[str, str], float] = {}


def is_lowdip_kind(kind: Optional[str]) -> bool:
    k = str(kind or "").strip()
    return bool(k) and (k in _LOWDIP_KINDS or k.startswith("wolf_253") or k.startswith("wolf_254"))


def lowdip_day_budget_pct() -> float:
    """当日低吸腿额度（占总资产百分比）；0 = 关。"""
    try:
        v = float(os.getenv("WOLF_LOWDIP_DAY_BUDGET", "0") or 0)
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def clamp_lowdip_day_budget(volume: int, price: float, equity: Optional[float], used_today: float,
                            pct: Optional[float] = None, lot: int = 100) -> Tuple[int, str]:
    """低吸腿当日额度（纯函数）：剩余 = pct%·总资产 − 当日已用；超出 ⇒ 向下取整手缩量；用尽 ⇒ 0 股。

    开关关 / 权益取不到 ⇒ **原样返回**（fail-open，不因取数拦腿）。
    """
    v = int(volume or 0)
    p = float(price or 0)
    q = lowdip_day_budget_pct() if pct is None else float(pct or 0)
    if q <= 0:
        return v, "低吸额度未开（WOLF_LOWDIP_DAY_BUDGET=0）"
    if v <= 0 or p <= 0:
        return v, "低吸额度未介入（无量/无价）"
    if not equity or float(equity) <= 0:
        return v, "低吸额度未介入（权益取不到）"
    budget = float(equity) * q / 100.0
    remain = budget - float(used_today or 0)
    if remain <= 0:
        return 0, "低吸额度已用尽（已用 %.0f / 额度 %.0f = %.1f%%×%.0f）" % (used_today, budget, q, float(equity))
    if p * v <= remain:
        return v, "低吸额度内（用 %.0f/%.0f）" % (used_today + p * v, budget)
    _lot = max(int(lot or 100), 1)
    v2 = int(remain / p / _lot) * _lot
    return max(v2, 0), "低吸额度：剩余 %.0f 元（%.1f%%×%.0f − 已用 %.0f）⇒ %d→%d 股" % (
        remain, q, float(equity), used_today, v, v2)


def _ambush_open_count(account_id: str) -> int:
    """当前**持有的埋伏仓只数**（持仓中期权在、且买入理由含 `wolf_ambush_buy` ✓）。"""
    try:
        from sqlalchemy import text as _t6
        with SessionLocal() as _s6:
            return int(_s6.execute(_t6(
                "SELECT COUNT(DISTINCT p.symbol) FROM paper_positions p WHERE p.account_id=:a "
                "AND COALESCE(p.volume,0) > 0 AND EXISTS (SELECT 1 FROM paper_trades t "
                "WHERE t.account_id=:a AND t.symbol=p.symbol AND COALESCE(t.voided,0)=0 "
                "AND t.direction LIKE '买%' AND COALESCE(t.reason,'') LIKE :p)"),
                {"a": account_id, "p": "%wolf_ambush_buy%"}).scalar() or 0)
    except Exception:
        return 0


def _ambush_position(account_id: str, symbol: str) -> bool:
    """本段持仓是否由**埋伏腿**建立（买入理由含 `wolf_ambush_buy` ✓）。"""
    try:
        from sqlalchemy import text as _t2
        with SessionLocal() as _s2:
            _n = _s2.execute(_t2(
                "SELECT COUNT(*) FROM paper_trades WHERE account_id=:a AND symbol=:s "
                "AND COALESCE(voided,0)=0 AND direction LIKE '买%' "
                "AND COALESCE(reason,'') LIKE :p"),
                {"a": account_id, "s": symbol, "p": "%wolf_ambush_buy%"}).scalar() or 0
        return int(_n) > 0
    except Exception:
        return False


def _lowdip_trend_ok(account_id: str, symbol: str) -> bool:
    """低吸「摊薄」前提：该标的**由趋势突破腿建过仓、且当前仍有持仓** ✓。

    依据（账本 §9.138/§9.139）：狼大的摊薄前提是「**这只票会回来**」✗；
    而实测我们 **40 笔低吸全部**发生在「票上**没有**趋势底仓」的票上 ⇒
    摊薄实际变成了「在下跌中加仓」✗（历史 **0 样本** ⇒ 无法回测，只能前瞻验证 ✓）。
    取数失败 ⇒ **放行**（fail-open，不因取数拦腿 ✓）。
    """
    try:
        from sqlalchemy import text as _t
        with SessionLocal() as _s:
            _held = _s.execute(_t(
                "SELECT COALESCE(SUM(CASE WHEN direction LIKE '买%' THEN volume ELSE -volume END),0) "
                "FROM paper_trades WHERE account_id=:a AND symbol=:s AND COALESCE(voided,0)=0"),
                {"a": account_id, "s": symbol}).scalar() or 0
            if int(_held) <= 0:
                return False
            _n = _s.execute(_t(
                "SELECT COUNT(*) FROM paper_trades WHERE account_id=:a AND symbol=:s "
                "AND COALESCE(voided,0)=0 AND direction LIKE '买%' AND COALESCE(reason,'') LIKE :p"),
                {"a": account_id, "s": symbol, "p": "%trend_break%"}).scalar() or 0
            return int(_n) > 0
    except Exception as _e:
        print("[gateway] 低吸趋势票门：取数失败（放行）: %s" % str(_e)[:70], flush=True)
        return True


def lowdip_used_today(account_id: str, day: str) -> float:
    return float(_LD_USED.get((account_id, str(day)), 0.0))


def lowdip_add_used(account_id: str, day: str, amount: float) -> None:
    _LD_USED[(account_id, str(day))] = lowdip_used_today(account_id, day) + float(amount or 0)


def build_size_floor(symbol: str, price: float, volume: int,
                     trigger_id: Optional[int] = None, condition_id: Optional[int] = None,
                     account_id: str = ACCOUNT_T,
                     ledger: Optional[dict] = None) -> Tuple[int, str]:
    """L3 v1：**建仓腿的下单量下限** = `min(底仓缺口/步长, 可用现金×安全系数)`（整手向下取整）。

    返回 `(股数, 归因)`；开关关 / 非建仓类腿型 / 无量无价 / 权益或现金取不到 ⇒ **原样返回**（fail-open）。
    只**抬量**（`max(请求量, 锚)`），从不压量 —— 压量仍由既有档位/资金校验负责。
    """
    v = int(volume or 0)
    if not _BUILD_SIZE_FLOOR or v <= 0 or float(price or 0) <= 0:
        return v, "L3 建仓下限未开（WOLF_BUILD_SIZE_FLOOR=0）"
    try:
        kind = _turnover_kind(trigger_id, condition_id)
        if not _is_build_cap_kind(kind):
            return v, "L3 建仓下限未介入（非建仓类腿型：%s）" % (kind or "-")
        from app.services import t_capacity as _capf
        _led = ledger if ledger is not None else get_sellable_ledger(account_id)
        eq = _capf.account_equity(account_id, ledger=_led)
        if not eq:
            return v, "L3 建仓下限未介入（权益不可用）"
        pv = 0.0
        for _s, _it in (_led or {}).items():
            try:
                pv += float(_it.get("volume") or 0) * float(_it.get("avg_price") or 0)
            except Exception as _e_sil3:
                _silent_alert("t_gateway.py:834", _e_sil3)
                continue
        anchor_v, why = _capf.clamp_buy_volume(10 ** 9, float(price), float(eq), pv, is_build=True)
        cash = _account_available_cash(account_id)
        if cash is not None:
            cap_cash = int(cash * _BUILD_CAP_CASH_SAFETY / float(price) / 100) * 100
            if cap_cash < anchor_v:
                anchor_v = max(cap_cash, 0)
                why += "；按可用资金 %.0f 元收敛到 %d 股" % (cash, anchor_v)
        if anchor_v > v:
            return int(anchor_v), ("L3 建仓下限（腿型=%s）：锚=底仓缺口/步长 与可用资金 ⇒ %d→%d 股｜%s"
                                   % (kind, v, anchor_v, why))
        return v, "L3 建仓下限未介入（锚 %d ≤ 请求 %d）" % (anchor_v, v)
    except Exception as e:
        print("[t-gateway] L3 建仓下限失败(忽略): %s: %s" % (type(e).__name__, str(e)[:60]))
        return v, "L3 建仓下限异常(忽略)"



def _max_buy_volume(symbol: str, tier: str, ledger: Optional[dict] = None,
                    price: Optional[float] = None,
                    condition_id: Optional[int] = None,
                    kind: Optional[str] = None) -> int:
    """兼容入口：只返回买腿上限股数（归因见 `_max_buy_volume_ex`）。"""
    return _max_buy_volume_ex(symbol, tier, ledger, price=price, condition_id=condition_id,
                              kind=kind)[0]


def resolve_buy_cap(symbol: str, price: Optional[float] = None,
                    account_id: str = ACCOUNT_T,
                    condition_id: Optional[int] = None) -> int:
    """系统档位买量上限（与 validate_order_at 同口径：regime 档位 + 可卖底仓/建仓规模）。

    供执行层做 min(AI建议量, 系统上限) 截断（2026-09-08 用户拍板）——
    AI 可输出建议金额/股数，但最终不超系统上限。
    """
    try:
        regime_state = compute_regime()
        regime = regime_state.get("regime", "ACTIVE")
        try:
            quote = self_quote(symbol)
        except Exception:
            quote = None
        tier = _floor_tier(regime, bool(quote and _near_limit_down(quote)))
        ledger = get_sellable_ledger(account_id)
        cap = _max_buy_volume(symbol, tier, ledger, price=price, condition_id=condition_id)
        # AI自由跑(档位关闭)时仍有底仓的标的收敛到可卖底仓（min(建议,上限) 语义，2026-09-08）——
        # 只有无底仓建仓(condition 路径)才放开给 build_sizing 规模
        if not T_BUY_TIER_LIMIT_ENABLED:
            _sellable = int((ledger_item(ledger, symbol) or {}).get("sellable") or 0)
            if _sellable > 0:
                cap = min(cap, _sellable)
        return cap
    except Exception as e:
        print(f"[t-gate] resolve_buy_cap 失败: {e}")
        return 0


def ledger_item(ledger: Optional[Dict[str, Dict[str, Any]]], symbol: str) -> Optional[Dict[str, Any]]:
    """账本取项（**大小写/形态容错**）：精确 → 标准形态 → 小写 → 去后缀 依次尝试。

    为什么需要（2026-09-17 死腿事故）：账本键来自 `paper_positions`（SH603259），而
    `_check_profit_take`/斐波路径写进 t_triggers 的是腾讯小写形态（sh603259）→ 原样
    `ledger.get(symbol)` 查不到 → 恒判「无足够可卖底仓（裸空拦截）」→ 纪律卖腿结构性死掉。
    本函数让**任何形态的调用方**都能查到真实可卖额度（治标兜底；治本是写库前 `std_symbol`）。
    """
    if not ledger or not symbol:
        return None
    for key in (symbol, std_symbol(symbol), str(symbol).strip().upper(), str(symbol).strip().lower()):
        if key and key in ledger:
            return ledger[key]
    _s = str(symbol).strip().upper()
    if "." in _s:                       # 600519.SH ↔ SH600519
        code, _, market = _s.partition(".")
        for key in (market + code, (market + code).lower()):
            if key in ledger:
                return ledger[key]
    return None


def resolve_sell_cap(symbol: str, account_id: str = ACCOUNT_T) -> int:
    """系统可卖上限（卖出 = 可卖 − 底仓 floor，2026-09-02 狼大口径：底仓不动）。"""
    try:
        ledger = get_sellable_ledger(account_id)
        item = ledger_item(ledger, symbol) or {}
        sellable = int(item.get("sellable") or 0)
        floor = base_floor_shares(account_id, symbol, volume=sellable)
        return max(sellable - floor, 0)
    except Exception as e:
        print(f"[t-gate] resolve_sell_cap 失败: {e}")
        return 0


# ────────────────────────────────────────────────────────────────
# 三阶校验网关
# ────────────────────────────────────────────────────────────────

def _breakers_from_ctx(risk: Dict[str, Any], daily: Dict[str, Any]) -> Tuple[bool, str]:
    """从注入的 risk/daily 状态判定熔断（回测与实盘共用）。"""
    if risk.get("stop_all"):
        return True, "STOP_ALL 已触发"
    if risk.get("manual_lock"):
        return True, f"人工锁定: {risk.get('lock_reason') or 'manual'}"
    # 2026-09-11 删除：`daily.get("risk_breaker")` 日亏损熔断（死守卫：0 处写入，见上）。
    if int(risk.get("consecutive_losses") or 0) >= 3:
        return True, "连续亏损 ≥ 3 次，临时禁自动"
    return False, ""


def validate_order_at(symbol: str, side: str, price: float, volume: int,
                      ctx: Dict[str, Any],
                      condition_id: Optional[int] = None,
                      trigger_id: Optional[int] = None,
                      reason: str = "",
                      decision_source: str = "agent",
                      allow_human_override: bool = False,
                      is_stop_loss: bool = False) -> Dict[str, Any]:
    """做T下单网关校验（三阶 + 二段断言）——状态全注入版（回测与实盘共用）。

    与 validate_order 行为完全一致，仅把实时依赖改为从 ctx 读取：
        regime: str             regime 档位（compute_regime().regime 或历史近似）
        quote: Optional[dict]   行情快照（current/pre_close/change_pct...；None 跳过涨跌停/追价断言）
        ledger: dict            get_sellable_ledger() 结果
        net_asset: float        t 账户净值（回测用固定假设）
        daily: dict             get_daily_state() 结果
        risk: dict              get_risk_state() 结果
        sell_in_transit: bool   卖出在途锁（回测由账本判定）
        trigger_status: Optional[str]  触发事件状态（回测由回测事件表提供；None 时查 t_triggers）
        cost_ratio_ok: Optional[bool]  价差/成本比预判（None 时内部调 _cost_ratio_ok）

    decision_source: agent/ai_led（ai_led 与 agent 同档风控，不豁免任何校验；
    区别仅在于 ai_led 允许无触发事件的主动买卖——孤儿单校验仅在 trigger_id 给定时生效）。
    """
    result = {"pass": False, "mode": "blocked", "level": "hard", "reason": "", "warn": []}
    symbol = std_symbol(symbol)      # 2026-09-17：与小写调用方（纪律腿）对齐，避免账本/涨跌停误判
    try:
        regime = ctx.get("regime", "ACTIVE")
        quote = ctx.get("quote")
        ledger = ctx.get("ledger") or {}
        net_asset = float(ctx.get("net_asset") or 200000.0)
        daily = ctx.get("daily") or {}
        risk = ctx.get("risk") or {}
        account_id = ctx.get("account_id", ACCOUNT_T)

        # ── 第一阶：硬闸门（O(1) 快路径） ──
        # 1) account 白名单
        if ACCOUNT_T != "t":
            result["reason"] = "非白名单账户"
            return result
        # 2) STOP_ALL / 熔断
        broken, why = _breakers_from_ctx(risk, daily)
        if broken:
            result["reason"] = why
            return result
        # 3) 裸空/无卖腿拦截（卖出必须有持仓；买入若无底仓且为低吸则拒）
        if side == "sell":
            item = ledger_item(ledger, symbol)
            # 持仓仅底仓（默认100股铁律 / ETF大底仓覆盖）时无T仓可卖 → 非裸空错误，
            # 做T卖腿量已在 TMonitor 推导为 0/跳过，这里直接跳过卖出检查（用户需求：持仓仅100股时跳过卖出检查）
            _floor = base_floor_shares(account_id, symbol, volume=(item.get("volume") or item.get("sellable"))) if item else 0
            if item and item["sellable"] <= _floor:
                pass
            elif not item or item["sellable"] < volume:
                result["reason"] = "无足够可卖底仓（裸空拦截）"
                return result
        elif side == "buy":
            # 无底仓买入：仅条件单触发路径（condition_id/trigger_id 提供）放行——
            # 迭代#58（用户需求）：未建仓标的通过监控条件命中直接建仓开仓；
            # 量受 _max_buy_volume 建仓规模上限（单笔/单标）约束。
            # 无触发事件的主动裸买（非自由跑）仍拒绝——新开仓走独立建仓流程。
            no_pos = symbol not in ledger
            if no_pos and T_BUY_TIER_LIMIT_ENABLED \
                    and not condition_id and not trigger_id:
                result["reason"] = "无底仓标的禁止裸买（新开仓走独立建仓流程或发布条件单）"
                return result
        # 4) 跌停禁买 / 涨停禁卖
        if quote:
            limit_status = _limit_status(quote, side)
            if limit_status == "block":
                result["reason"] = f"{'跌停' if side == 'buy' else '涨停'}禁单"
                return result
        # 5) 孤儿单（trigger 已 cancelled/executed 不重复下单）
        if trigger_id:
            trig_status = ctx.get("trigger_status")
            trig = {"status": trig_status} if trig_status is not None else _get_trigger(trigger_id)
            if trig and trig.get("status") not in ("pending", "claimed", "auto_ready", "human_confirm"):
                result["reason"] = f"触发事件状态异常: {trig.get('status')}"
                return result

        # ── 第二阶：账本（确定性规则） ──
        # S6 删除(2026-09-10): 原"底仓浮亏 ≤ −3% 减半 / ≤ −5% 清仓锁定"(_base_loss_guard)调用已移除。
        # 该守卫早已退化为无条件放行(action 恒为 pass), 与狼大「底仓不动」相冲, 属审计 §5.2 认定的自造机制。
        # _base_loss_guard 函数体保留但不被调用, 供回溯历史行为。
        # 可卖底仓分档 + 买腿上限
        near_limit = bool(quote and _near_limit_down(quote))
        tier = _floor_tier(regime, near_limit)
        if side == "buy":
            # C4 参数对齐（2026-09-15，**默认关**）：ETF 波动下限。
            #   狼大 2026-08-21「选半导体仅仅只是因为他波动大 **ETF都有3个点以上的波动**
            #   不然选个别的1个点的ETF没意思」→ 只对 **ETF** 生效（他的话就是讲 ETF；
            #   ② 已三次证明他的板块级/品种级判据搬到个股级会变负，故个股不套用）。
            #   开关 WOLF_ETF_VOL_GATE=1 开启；数据不足 → 放行。
            try:
                from app.services.wolf_etf_vol import enabled as _ev_on, etf_vol_ok as _ev_ok, is_etf as _is_etf
                if _ev_on() and _is_etf(symbol):
                    _ok, _why = _ev_ok(symbol)
                    if not _ok:
                        result["level"] = "soft"
                        result["reason"] = "ETF 波动不足（狼大 2026-08-21「ETF都有3个点以上的波动」）: " + _why
                        return result
            except Exception as _e:
                print("[t_gateway] etf_vol 检查跳过: %s" % str(_e)[:80])
            # wolf 回补护栏（2026-09-08 用户拍板：588170 void脱节重复加仓根因）
            # ① 当日 wolf 正T回补笔数超上限 → 拦截（防连续回补堆仓）
            #    S7 统一(2026-09-10): 上限改读 WOLF_REFILL_MAX_PER_DAY(默认2),
            #    与 wolf_253_build.WOLF_REFILL_MAX_PER_DAY 同源, 消除"gateway 当日1笔 vs
            #    refill_253 3日内2次"两套并行规则；狼大「来来回回做几次就行了」支持放宽到 2。
            # ② 当日存在已撤销(回滚)卖单 → 拦截（回补语义与账本脱节，需人工确认）
            _ev = None
            if trigger_id:
                try:
                    _ev = (_get_trigger(trigger_id) or {}).get("event_type")
                except Exception:
                    _ev = None
            if _ev == "wolf_zheng_t_buy":
                _n_refill = _wolf_buy_executed_today(symbol, account_id)
                if _n_refill >= MAX_WOLF_REFILL_PER_DAY:
                    result["level"] = "ledger"
                    result["reason"] = "wolf回补当日已成交%d笔，上限%d笔(防连续回补堆仓)" % (
                        _n_refill, MAX_WOLF_REFILL_PER_DAY)
                    return result
                if _voided_sell_today(symbol, account_id) > 0:
                    result["level"] = "ledger"
                    result["reason"] = "当日存在已撤销卖单(账本已回滚)，wolf回补需人工确认，禁止自动回补"
                    return result
            # 低吸加仓次数上限（单标单日买腿成交 ≤ MAX_DAILY_BUY_LEGS）
            buy_legs = ctx.get("daily_buy_legs")
            if buy_legs is None:
                buy_legs = _daily_buy_legs(symbol, ctx.get("account_id", ACCOUNT_T))
            if buy_legs >= MAX_DAILY_BUY_LEGS:
                result["level"] = "ledger"
                result["reason"] = f"低吸加仓次数超限（当日已 {buy_legs} 笔 ≥ {MAX_DAILY_BUY_LEGS}）"
                return result
            max_buy, _cap_why = _max_buy_volume_ex(symbol, tier, ledger, price=price,
                                                    condition_id=condition_id, kind=_ev)
            if max_buy <= 0:
                result["level"] = "ledger"
                # 归因写清楚（旧文案「当前档位 L2 禁止低吸」把行情档位与"无底仓"混为一谈）
                result["reason"] = _cap_why
                return result
            if volume > max_buy:
                if T_BUY_CAP_CLAMP and max_buy > 0:
                    # 缩量到上限（**继续走后续断言**，不是放行）：调用方读 result["clamp_volume"] 下单
                    result["warn"] = list(result.get("warn") or []) + [
                        "买腿 %d 超过档位 %s 上限 %d ⇒ 缩量到 %d（WOLF_BUY_CAP_CLAMP=1）"
                        % (volume, tier, max_buy, max_buy)]
                    result["clamp_volume"] = int(max_buy)
                    volume = int(max_buy)
                else:
                    result["level"] = "ledger"
                    result["reason"] = f"买腿 {volume} 超过档位 {tier} 上限 {max_buy}（买腿≤可卖底仓）"
                    return result
            # 卖出在途锁：半边腿未落定不启动另一半
            if ctx.get("sell_in_transit", False):
                result["level"] = "ledger"
                result["reason"] = "卖出在途，禁止启动买腿（半边腿未落定）"
                return result
        # 日亏损熔断（第二阶重复确认）——只拦买腿（防继续加仓/开仓放大亏损），
        # 不拦卖腿：止损/高抛离场是止血动作，必须放行（否则深跌时无法止损）
        realized = float(daily.get("realized_pnl") or 0)
        pnl_pct = realized / net_asset * 100 if net_asset else 0.0
        if side == "buy" and pnl_pct <= -DAILY_LOSS_BREAKER_PCT * 100:
            result["level"] = "ledger"
            result["reason"] = f"日亏损熔断（{pnl_pct:.2f}%）"
            return result
        # S5 删除(2026-09-10): 原"日累计回转额 ≤ 3×净值"上限已删除。
        # 狼大语料无对应规则, 属审计 §5.2 认定的自造机制。常量 MAX_DAILY_TURNOVER_RATIO 保留定义
        # 仅供 t_build 等处的 import 兼容与复盘观测, 不再用于拦截。
        # 注: 日亏损熔断(T_DAILY_LOSS_LIMIT_ENABLED)与本项无关, 未在本次删除范围。

        # ── 第三阶：建议层（仅告警/限频，不拒热路径） ──
        warns = []
        # 单笔 ≤ 净值 5%
        if price * volume > net_asset * MAX_SINGLE_ORDER_PCT:
            warns.append(f"单笔超净值5%（建议）")
        # 价差/成本比过滤（决策生成层已做，此处复核）
        cost_ok = ctx.get("cost_ratio_ok")
        if cost_ok is None:
            cost_ok = _cost_ratio_ok(symbol, price)
        if not cost_ok:
            warns.append("滑点+手续费占价差空间过高（建议不触发）")
        # 日亏预警
        if pnl_pct <= -DAILY_LOSS_WARN_PCT * 100:
            warns.append("日亏接近预警线（限频）")
        result["warn"] = warns

        # ── 二段实时断言（落单前最新） ──
        # 最新价格断言：低吸不打在远离 target 的追价上
        if side == "buy" and quote:
            current = float(quote.get("current", 0) or 0)
            if current > price * 1.02:  # 当前价高于委托价 2% 以上（追价风险）
                result["level"] = "assert"
                result["reason"] = f"最新价 {current} 高于委托价 {price} 超 2%（追价风险）"
                return result

        result.update({"pass": True, "mode": "auto", "level": "ok"})
        return result
    except Exception as e:
        result["reason"] = f"网关校验异常: {e}"
        return result


def validate_order(symbol: str, side: str, price: float, volume: int,
                   condition_id: Optional[int] = None,
                   trigger_id: Optional[int] = None,
                   reason: str = "",
                   decision_source: str = "agent",
                   is_stop_loss: bool = False,
                   account_id: str = ACCOUNT_T) -> Dict[str, Any]:
    """做T下单网关校验（实时路径）——构造实时 ctx 后委托 validate_order_at。
    2026-09-02: account_id 参数(默认 t)——ledger/在途/买腿按账户, net_asset/daily/risk 保持全局风控口径。"""
    symbol = std_symbol(symbol)      # 2026-09-17：小写调用方也要查到同一份账本/行情
    quote = self_quote(symbol)
    regime_state = compute_regime()
    ctx = {
        "regime": regime_state.get("regime", "ACTIVE"),
        "quote": quote,
        "ledger": get_sellable_ledger(account_id),
        "net_asset": t_net_asset(account_id),  # 修正：做T交易在 stock 账户，应读该账户净资（而非 t 账户）
        "daily": t_db.get_daily_state() or {},
        "risk": t_db.get_risk_state() or {},
        "sell_in_transit": is_sell_in_transit(symbol, account_id) if side == "buy" else False,
        "account_id": account_id,
    }
    return validate_order_at(symbol, side, price, volume, ctx,
                             condition_id=condition_id, trigger_id=trigger_id,
                             reason=reason, decision_source=decision_source,
                             is_stop_loss=is_stop_loss)


def self_quote(symbol: str) -> Optional[dict]:
    """拉取单只实时行情（网关用）。"""
    q = fetch_tencent_quote([_normalize_symbol(symbol)])
    return q.get(_normalize_symbol(symbol))


def _limit_status(quote: dict, side: str) -> str:
    """跌停/涨停判断（腾讯 qt 无直接字段，用 change_pct 近似：|涨跌幅| ≥ 9.8% 视为封板）。"""
    try:
        chg = float(quote.get("change_pct", 0) or 0)
        if side == "buy" and chg <= -9.8:
            return "block"
        if side == "sell" and chg >= 9.8:
            return "block"
    except (TypeError, ValueError) as _e_sil2:
        try:
            from app.services import alert_hub as _ah_sil
            _ah_sil.note_silent("t_gateway.py:1183", _e_sil2)
        except Exception:
            print("[silent:t_gateway.py:1183] %s: %s" % (type(_e_sil2).__name__, str(_e_sil2)[:110]), flush=True)
    return "ok"


def _near_limit_down(quote: dict) -> bool:
    try:
        chg = float(quote.get("change_pct", 0) or 0)
        return chg <= -8.0  # 接近跌停（8% 内）视为高风险区
    except (TypeError, ValueError):
        return False


def _base_loss_guard(symbol: str, side: str, quote: Optional[dict],
                     ledger: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """狼大式买腿风控：取消"浮亏 % 禁买"（非狼大，且狼大'急跌不割肉'方向相反）。
    狼大的低吸本就由**技术触发**把关（253/254 缩量企稳/触前低/破位确认），
    真正破位减仓/被动止盈由 **卖侧/止损组件** 处理；买腿不应再因固定浮亏%被拦。
    故这里一律放行 action=pass；唯数据缺失时也放行（不误伤）。
    备注：BASE_LOSS_HALF_PCT/CLEAR_PCT 保留供参考，不再用于拦买腿。
    """
    # 放行买腿：狼大低吸看技术条件，不由浮亏%拦（急跌不割肉）
    return {"action": "pass", "reason": "wolf_consistent: 低吸由技术条件(253/254/缩量企稳)把关，浮亏%不拦买腿"}


def _wolf_buy_executed_today(symbol: str, account_id: str) -> int:
    """当日 wolf 正T回补(wolf_zheng_t_buy)已执行笔数（按账户/标的，t_triggers executed）。"""
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            row = db.execute(text(
                "SELECT COUNT(*) FROM t_triggers "
                "WHERE account_id = :acc AND symbol = :sym "
                "AND event_type = 'wolf_zheng_t_buy' AND status = 'executed' "
                "AND substr(created_at::text, 1, 10) = :today"
            ), {"acc": account_id, "sym": symbol, "today": today}).scalar()
            return int(row or 0)
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] wolf_executed_today 失败: {e}")
        return 0


def _voided_sell_today(symbol: str, account_id: str) -> int:
    """当日已撤销(回滚)卖单笔数（paper_trades voided=1 & 卖出 & 当日）。"""
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            row = db.execute(text(
                "SELECT COUNT(*) FROM paper_trades "
                "WHERE account_id = :acc AND symbol = :sym "
                "AND direction IN ('卖出','sell') AND voided = 1 "
                "AND substr(created_at::text, 1, 10) = :today"
            ), {"acc": account_id, "sym": symbol, "today": today}).scalar()
            return int(row or 0)
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] voided_sell_today 失败: {e}")
        return 0


def _daily_buy_legs(symbol: str, account_id: str = "t") -> int:
    """单标当日低吸（买腿）成交次数：paper_trades 当日买入笔数（按账户）。"""
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            n = db.execute(text(
                "SELECT COUNT(*) FROM paper_trades "
                "WHERE account_id = :acc AND direction IN ('买入','buy') "
                "AND (voided = 0 OR voided IS NULL) "
                "AND substr(created_at, 1, 10) = :today AND symbol = :sym"
            ), {"acc": account_id, "today": today, "sym": symbol}).scalar()
            return int(n or 0)
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 低吸次数查询失败: {e}")
        return 0


def _fee_pct(symbol: str) -> float:
    """单边手续费(小数, 按真实佣金)：A股 万0.86 / ETF 万0.5。返回小数。"""
    try:
        s = str(symbol)
        code6 = s[2:8] if s[:2] in ("SH", "SZ", "BJ") else s
        # 5开头=沪ETF/基金, 1开头=深ETF/基金 → 万0.5；其余股票 → 万0.86
        return 0.00005 if len(code6) == 6 and code6[0] in ("5", "1") else 0.000086
    except Exception:
        return 0.000086

def _cost_ratio_ok(symbol: str, price: float) -> bool:
    """滑点+手续费 vs 价差空间：>20% 不值得做。
    佣金按用户实际费率：A股 万0.86，ETF 万0.5（原 0.001=万10 高估，已改为按标的分档）。"""
    try:
        from app.services.t_pool import calc_t_quality
        q = calc_t_quality(symbol)
        spread = float(q.get("spread", 0) or 0)
        cost_pct = SLIPPAGE_PCT * 2 + _fee_pct(symbol)  # 双边滑点 + 真实单边手续费
        if spread <= 0:
            return False
        return cost_pct / (spread / 100) <= COST_RATIO_LIMIT
    except Exception:
        return True


def _turnover_kind(trigger_id: Optional[int], condition_id: Optional[int]) -> str:
    """换手闸 ③ 时段窗需要的**腿型**（trigger.event_type 优先，退回 condition.trigger_kind）。

    2026-09-19 用户拍板 A：时段窗只对做T类腿型生效 ⇒ 网关必须把腿型传下去；取不到时返回空串，
    由 t_turnover.applies_to 按 TW_UNKNOWN_ALLOW（默认仍拦，保守）决定。
    """
    try:
        if trigger_id:
            _t = _get_trigger(int(trigger_id)) or {}
            _k = str(_t.get("event_type") or "").strip()
            if _k:
                return _k
    except Exception as _e_sil3:
        try:
            from app.services import alert_hub as _ah_sil
            _ah_sil.note_silent("t_gateway.py:1305", _e_sil3)
        except Exception:
            print("[silent:t_gateway.py:1305] %s: %s" % (type(_e_sil3).__name__, str(_e_sil3)[:110]), flush=True)
    try:
        if condition_id:
            from app.services import t_db as _tdb
            _c = _tdb.get_condition(int(condition_id)) or {}
            return str(_c.get("trigger_kind") or "").strip()
    except Exception as _e_sil4:
        try:
            from app.services import alert_hub as _ah_sil
            _ah_sil.note_silent("t_gateway.py:1312", _e_sil4)
        except Exception:
            print("[silent:t_gateway.py:1312] %s: %s" % (type(_e_sil4).__name__, str(_e_sil4)[:110]), flush=True)
    return ""


def _get_trigger(trigger_id: int) -> Optional[dict]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT * FROM t_triggers WHERE id = :id"
            ), {"id": trigger_id}).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 查询触发事件失败: {e}")
        return None


# ────────────────────────────────────────────────────────────────
# 异常升级判定（6 类清单）
# ────────────────────────────────────────────────────────────────

def _gateway_now() -> datetime:
    """网关侧统一取时：与 `t_db.claim_pending_trigger` 写 claimed_at 的钟同源（都是 Python 进程钟）。"""
    return datetime.now()


def _claimed_age_seconds(claimed_at, now: Optional[datetime] = None) -> Optional[float]:
    """claimed_at 距"现在"的秒数；解析不出来返回 None（调用方按"判不了"处理 = 维持原行为）。

    2026-09-17：兼容 psycopg 返回的 datetime 与字符串两种形态；带时区的值先去掉 tzinfo
    （库列是 `timestamp without time zone`，与 Python 天真钟同口径比）。

    ⚠️ 这里**刻意用鸭子类型而非 `isinstance(x, datetime)`**：回测的钉钟
    （`jobs/bt_run_pinned.pin_clock`）会把 `datetime.datetime` 换成子类，一旦本模块在钉钟之后被导入，
    `from datetime import datetime` 拿到的是那个子类，而 psycopg 返回的是真 `datetime` 实例 →
    isinstance 判假 → 超时判定被静默跳过（正是本次要修的病症）。
    """
    try:
        claimed_dt = claimed_at
        if isinstance(claimed_dt, str):
            claimed_dt = datetime.strptime(claimed_dt.strip()[:19], "%Y-%m-%d %H:%M:%S")
        if getattr(claimed_dt, "tzinfo", None) is not None:
            claimed_dt = claimed_dt.replace(tzinfo=None)
        return ((now or _gateway_now()) - claimed_dt).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return None


def classify_escalation(symbol: str, side: str, trigger: Optional[dict] = None,
                        regime: str = "ACTIVE") -> Tuple[str, str]:
    """异常升级 6 类清单 → 返回 (是否升级, 原因)。升级目标：agent / human。

    ①软件异常 ②歧义 ③首开非底仓 ④regime极端 ⑤连续触风控 ⑥孤儿单
    """
    risk = t_db.get_risk_state() or {}

    # ④ regime=极端 → human
    if regime == "HALT":
        return "human", "regime=HALT 极端市况强制人工"

    # ⑤ 连续触风控
    if int(risk.get("consecutive_losses") or 0) >= 2:
        return "human", "连续触犯风控，强制人工+临时禁自动"

    # ③ 首次对非底仓池标的触发（无底仓）
    #   2026-09-21：可选改为 **AI 审核**（`WOLF_NEWPOS_AI_REVIEW`，库内默认关）。
    #   为什么：升级到 human 在 AI 主导/回测架构里没人确认 ⇒ 2min 超时 cancelled（黑洞）。
    ledger = get_sellable_ledger()
    if side == "buy" and symbol not in ledger:
        try:
            from app.services import wolf_t_base_gate as _tbg
            if _tbg.ai_review():
                return "agent", "首开非底仓标的（新开仓风险）→ 改由 AI 审核"
        except Exception as _e_sil5:
            try:
                from app.services import alert_hub as _ah_sil
                _ah_sil.note_silent("t_gateway.py:1388", _e_sil5)
            except Exception:
                print("[silent:t_gateway.py:1388] %s: %s" % (type(_e_sil5).__name__, str(_e_sil5)[:110]), flush=True)
        return "human", "首开非底仓标的（新开仓风险）"

    # ⑥ 孤儿单/账实不一致
    if trigger and trigger.get("status") == "claimed" and trigger.get("claimed_at"):
        age_s = _claimed_age_seconds(trigger["claimed_at"], _gateway_now())
        if age_s is not None:
            if age_s > TRIGGER_EXEC_TIMEOUT_MIN * 60:
                return "human", "孤儿单超时未确认（账实漂移风险）"
            tol_s = _cross_clock_tol_s()
            if tol_s and age_s < -tol_s:
                # claimed_at 比进程钟还"未来" ⇒ 该行不是本进程钟写的（DB now() 遗留行 / 跨容器钟源）
                # 无法用 Python 钟度量年龄 → fail-closed：按已超时处理，转人工确认。
                return "human", "孤儿单 claimed_at 与进程时钟不同源（超前 %.0fs，按超时转人工）" % (-age_s,)

    # ② 歧义：接近熔断线
    pnl_pct = _daily_pnl_pct()
    if pnl_pct <= -DAILY_LOSS_WARN_PCT * 100:
        return "agent", f"接近日亏预警线（{pnl_pct:.2f}%）需复核"

    # ① 软件异常由网关内嵌处理（此处无独立触发）
    return "auto", ""


# ────────────────────────────────────────────────────────────────
# 网关执行入口（供 Agent/Worker 调用，最终放行者）
# ────────────────────────────────────────────────────────────────


# ── 腿批准闸 · 执行口二次把关（2026-09-19 实测补漏）────────────────────────────
# 背景：`leg_gate.approve` 原先**只在布腿器里**调用 ⇒ 主营校验只对"布腿"生效
#   （实测 drabjan2：SH603203 在 08:18 布腿口被 `LEG_REJECT ... 主营校验 dsh=无关` 拒了 3 次，
#    但同一账户仍在 2026-01-07 10:45 从**低吸建议价**直接买入 1000 股 @37.433，
#    order drabjan2_order000002）⇒ 闸没盖住"建议买价/条件单/agent 直接下单"这条路径，
#    看起来像"主营校验没生效"。
# 口径：**只拦买入**（卖出/止损永不拦）；开关 `WOLF_EXEC_GATE`（库内默认关、回测默认开）；
#   任何异常 fail-open（闸故障不能变成停摆）。
EXEC_GATE_ON = str(os.getenv("WOLF_EXEC_GATE",
                             "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")


def _leg_gate_exec(symbol: str, account_id: str = ACCOUNT_T, reason: str = "") -> Optional[str]:
    """执行口过一遍腿批准闸 → None=放行 / 字符串=拒绝原因。"""
    if not EXEC_GATE_ON:
        return None
    try:
        import sys as _s
        _am = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "apps", "main_line")
        if _am not in _s.path:
            _s.path.insert(0, _am)
        import leg_gate as _lg
        if not getattr(_lg, "ON", False):
            return None
        _th = None
        try:
            from wolf_context import theme_of_symbol as _tos
            _th = _tos(symbol)
        except Exception:
            _th = None
        # ⚠️ 2026-09-30（账本 §9.389 ✓ 用户「把主题参数也一起修掉」✓）：
        #   原先用 `theme_of_symbol(symbol)` 取"这只票**被贴的**主题" ✗ ⇒ 贴标错就误拒 ✗
        #   实测：「主类=电池 不属主题[AI/算力/科技]」✗，而当天主线是「新能源/电池」✓
        #   开关 ✓：`WOLF_EXEC_GATE_THEME_ANY`（库内默认 0 ＝ 关 ⇒ 生产逐字不变 ✓）
        if str(os.getenv("WOLF_EXEC_GATE_THEME_ANY", "0")).strip().lower() in ("1", "true", "yes", "on"):
            try:
                import theme_main_class as _tmc
                _mcs = set(_tmc.main_classes(symbol=symbol) or [])
                _all = set()
                for _v in (_tmc.THEME_MAIN_CLASS or {}).values():
                    _all |= set(_v or [])
                if _mcs and (_mcs & _all):
                    return None            # 主类命中任一主题 ⇒ 放行 ✓（错配票仍会被下面的原逻辑挡 ✓）
            except Exception as _e2:
                print("[gateway] 执行口主题校验(any)异常(放行) %s: %s" % (symbol, str(_e2)[:60]), flush=True)
        _ok, _why = _lg.approve(str(symbol), _th or "", stage="执行")
        if not _ok:
            print("[gateway] 执行口拦下买腿 %s：%s" % (symbol, str(_why)[:120]), flush=True)
            return "腿批准闸(执行口): %s" % str(_why)
        return None
    except Exception as _e:
        print("[gateway] 执行口腿闸异常(放行) %s: %s" % (symbol, str(_e)[:80]), flush=True)
        return None


def _decision_gate(symbol: str, trigger_id: Optional[int] = None, account_id: str = ACCOUNT_T,
                   reason: str = "") -> Optional[str]:
    """每日决策对象准入（2026-09-12，G1 接线）：**判据先于成交**。

    返回 None = 放行；返回字符串 = 拒绝原因（调用方据此 blocked）。

    三条硬约束：
      1. **只拦买入**（`side == "buy"` 才调用）——卖出/止损永不拦（止血动作必须能执行）
      2. `WOLF_DECISION_GATE=0`（默认）时整体不生效 → 现有行为不变
      3. `WOLF_DECISION_GATE_SHADOW=1` 时**只记录不拦**（灰度观察用），日志写
         `DATA_DIR/decision_gate_log.jsonl`
    """
    try:
        from app.services import daily_decision as _dd
    except Exception:
        return None
    try:
        if not _dd.gate_enabled():
            return None
        ok, why = _dd.entry_allowed_cached()   # 60s 进程内缓存，避免高频触发反复查库
        if ok:
            return None
        shadow = os.getenv("WOLF_DECISION_GATE_SHADOW", "0").strip().lower() not in ("0", "false", "no", "")
        _log_decision_refusal(symbol, why, shadow, account_id=account_id, trigger_id=trigger_id, note=reason)
        if shadow:
            print(f"[t-gate] 决策准入(shadow，仅记录不禁单) {symbol}: {why}")
            return None
        return f"决策对象准入拒绝: {why}"
    except Exception as e:
        # 准入机制自身出错时**放行**（不能让新机制变成新的静默停摆源），但要留痕
        print(f"[t-gate] 决策准入异常（放行）{symbol}: {type(e).__name__}: {str(e)[:80]}")
        return None


def _log_decision_refusal(symbol: str, why: str, shadow: bool, account_id: str = "",
                          trigger_id: Optional[int] = None, note: str = "") -> None:
    """拒绝/灰度记录：追加 JSONL（DATA_DIR/decision_gate_log.jsonl），失败不影响下单流程。"""
    try:
        import json as _json
        import time as _time
        # 运行期以环境变量 DATA_DIR 为准；没有才退回 workspace_detector（该模块只在容器内可导入，
        # 单测环境不可导入 —— 2026-09-12 实测：直接 import 会让日志静默写不出来）
        _dir = os.environ.get("DATA_DIR")
        if not _dir:
            try:
                from workspace_detector import DATA_DIR as _D
                _dir = str(_D)
            except Exception:
                _dir = os.path.join(os.getcwd(), "data")
        line = _json.dumps({"ts": int(_time.time()), "symbol": symbol, "account": account_id,
                            "trigger_id": trigger_id, "shadow": bool(shadow), "reason": why,
                            "note": note}, ensure_ascii=False)
        with open(os.path.join(_dir, "decision_gate_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as _e_sil6:
        try:
            from app.services import alert_hub as _ah_sil
            _ah_sil.note_silent("t_gateway.py:1528", _e_sil6)
        except Exception:
            print("[silent:t_gateway.py:1528] %s: %s" % (type(_e_sil6).__name__, str(_e_sil6)[:110]), flush=True)


def _trade_cap() -> int:
    """G6 每日每标的成交笔数上限（默认 2；0=关闭）。狼大 2025-02-07「一个票最多买 2 笔 卖 2 笔」。"""
    try:
        # ✅语料: 2025-02-07「一个票最多买2笔 卖2笔」
        return max(int(float(os.getenv("WOLF_MAX_TRADES_PER_SYMBOL_PER_DAY", "2"))), 0)
    except (TypeError, ValueError):
        return 2


def trade_cap_ok(done: int, cap: int, is_stop_loss: bool = False, reason: str = "",
                 trigger_kind: Optional[str] = None, side: str = "sell") -> bool:
    """G6 判定（纯函数，便于单测）：未达上限 → True；**保护性（止血/风控）卖出豁免**。

    ⚠️ 2026-09-22 用户拍板 "A′"：豁免判据由 **reason 文本**（`"止损" in reason`）改为
      **结构化腿型**（`t_protect.is_protective`）。原因：AI 的卖出理由里常写「**未跌破止损**」，
      命中关键词 ⇒ 豁免 ⇒ 「一个票最多卖 2 笔」形同虚设（实测 2026-01-07 同一分钟同价卖了 3 笔、1,400 股）。
      开关 `WOLF_PROTECT_STRUCTURED`（库内默认 0 = 逐字旧行为）。
    """
    if cap <= 0:
        return True
    try:
        from app.services import t_protect as _tp
        if _tp.is_protective(trigger_kind=trigger_kind, is_stop_loss=is_stop_loss,
                             reason=reason, side=side):
            return True
    except Exception:
        if is_stop_loss or ("止损" in str(reason)) or ("破位" in str(reason)):
            return True
    return int(done) < int(cap)


def _count_today_trades(account_id: str, symbol: str, side: str) -> int:
    """当日该账户该标的的成交笔数（paper_trades，剔除 voided）。取数失败 → 0（不因统计失败而拦单）。"""
    symbol = std_symbol(symbol)      # 2026-09-17：G6「一个票最多卖 2 笔」按标准形态计数（小写 → 恒 0）
    try:
        import datetime as _dt
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL",
                                          "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor()
        _dir = "买入" if side == "buy" else "卖出"
        cur.execute("SELECT count(*) FROM paper_trades WHERE account_id=%s AND symbol=%s AND direction=%s "
                    "AND trade_date=%s AND COALESCE(voided,0)=0",
                    (account_id, symbol, _dir, _dt.date.today().strftime("%Y-%m-%d")))
        n = int((cur.fetchone() or [0])[0] or 0)
        cur.close(); conn.close()
        return n
    except Exception as e:
        print(f"[gateway] G6 笔数统计失败({symbol}): {str(e)[:60]}")
        return 0


def _sell_time_gate_ok(reason: str = "", trigger_kind: Optional[str] = None) -> tuple:
    """卖出时点门（2026-09-16 落地）：**非保护性**卖出禁在 [13:00, 14:30)。

    狼大 2026-03-23「**每天的止损绝对不应该是下午1点到2点半**这个时间。。。要么你早上卖 要么你尾盘卖」。
    此前该门只存在于纪律卖腿（t_monitor._stop_time_ok）→ **agent 自主交易走网关时可绕过**
    （2026-09-16 13:36 就发生过一次：监控器被禁止做的事，agent 做了）。
    保护性卖出豁免：is_stop_loss / 止损 / 破位 / 被动止盈 / 顶态清仓 等止血与风控动作。
    返回 (ok, why)。开关 WOLF_SELL_TIME_GATE=0 关闭。
    """
    if os.getenv("WOLF_SELL_TIME_GATE", "1").strip() in ("0", "false", "no"):
        return True, ""
    # 2026-09-22 "A′"：保护性判定改为**结构化腿型**（旧行为 = reason 文本，开关关时逐字保留）
    try:
        from app.services import t_protect as _tp
        if _tp.is_protective(trigger_kind=trigger_kind, reason=reason):
            return True, ""
    except Exception:
        if any(k in str(reason or "") for k in ("止损", "破位", "被动止盈", "顶态", "清仓", "避险", "风控")):
            return True, ""
    try:
        hm = datetime.now().strftime("%H:%M")
    except Exception:
        return True, ""
    if "13:00" <= hm < "14:30":
        return False, ("时点门：13:00–14:30 是非保护性卖出的禁区"
                       "（狼大 2026-03-23「要么早上卖 要么你尾盘卖」）→ 请顺延到 14:30 之后或尾盘")
    return True, ""


# ── 浪型"不动"闸（账本 §9.287）───────────────────────────────────────────────
#   他：「主升浪中间的调整我一般不看的…一旦卖了很难买回来」✓／「买不进去证明涨了，不用动」✓／
#       「一个票最多买 2 笔 卖 2 笔 后面如果是主升浪的话越动收益越低」✓
#   我们的浪型"**动作层**"与他的原话对照 **6/7 ≈ 86% 对得上**（§9.286 ✓）
#   ⇒ 浪型给出 `操作=参与（build/side/t_only）` ⇒ **当天不做"减仓性"操作** ✓
#   保留**保护性**离场 ✓（止损/避险/风控/被动止盈/顶态/清仓/破位 ✓）
# ⚠️ 2026-09-29 收窄（账本 §9.289）：只拦「**因"弱"而卖**」✗；
#   **因"高位/保护"而卖一律放行** ✓（「到压力位/BOLL 上轨减半」「板上减半/吃一口减一半」
#   「周末/事件前减半避险」「破位收盘确认才走」「止损只在指数大级别破位」✓）
_WAVE_HOLD_REDUCE_KINDS = {
    "custom_vwap_sell",        # 破分时均价（"走弱" ✓ 拦）
    "wolf_defensive_t_reduce", # 去弱留强（"因弱" ✓ 拦）
    "wolf_dao_t_sell",         # 道氏/T 技术卖（"因弱" ✓ 拦）
    "custom_level_sell",       # 自定义位（技术走弱 ✓ 拦）
    "custom_trail_sell",       # 移动（技术走弱 ✓ 拦）
    "custom_support_sell",     # 破支撑（技术走弱 ✓ 拦；**破位止损**走 is_stop_loss ⇒ 已放行 ✓）
}
# **放行**清单（留痕用 ✓，便于复核 ✓）
_WAVE_HOLD_ALLOW_KINDS = {
    "wolf_fib_target_sell",    # 0.618 压力位兑现 ✓
    "wolf_profit_take_sell",   # 小赚兑现（「3-5 点兑现」✓）
    "wolf_board_half_sell",    # 板上减半（「吃一口减一半」「板上减了」✓）
    "wolf_confirm_sell",       # 确认卖 ✓
    "high_sell",               # T 出前高（高位 ✓）
}
_WAVE_HOLD_PROTECT_KW = ("止损", "破位", "被动止盈", "顶态", "清仓", "避险", "风控", "反抽失败")


def _wave_hold_action() -> str:
    """读浪型当前**动作**（`build/side/t_only/defense/exit` ✓）；不新鲜/读不到 ⇒ 返回空 ✓（保守不拦 ✓）"""
    try:
        if str(os.getenv("WOLF_WAVE_HOLD_NO_REDUCE", "0")).strip().lower() not in ("1", "true", "yes", "on"):
            return ""
        import json as _jw
        _roots = [os.path.dirname(os.environ.get("DATA_DIR", "/app/data")) or "/app/data",
                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data"),
                  "/home/fengx/marcus-platform/data"]
        # ① 先按**当日**找历史档 ✓（回测量化用 ✓）
        _base0 = os.path.basename(str(os.environ.get("DATA_DIR", "")).rstrip("/"))
        if len(_base0) == 8 and _base0.isdigit():
            for _r in _roots:
                for _sub in ("wave_state_history", "wave_state_by_day"):
                    _ph = os.path.join(_r, _sub, _base0 + ".json")
                    if os.path.exists(_ph):
                        with open(_ph, encoding="utf-8") as _fh:
                            _sh = _jw.load(_fh) or {}
                        if str(_sh.get("operation") or ""):
                            return str(_sh.get("operation"))
        for _r in _roots:
            _p = os.path.join(_r, "wave_state.json")
            if not os.path.exists(_p):
                continue
            with open(_p, encoding="utf-8") as _f:
                _st = _jw.load(_f) or {}
            _d = str(_st.get("date") or "").replace("-", "")[:8]
            _op = str(_st.get("operation") or "")
            if not _d or not _op:
                continue
            # 新鲜度：state 日期要落在 DATA_DIR 的当日附近（±10 天 ✓，防陈旧状态误导 ✓）
            _base = os.path.basename(str(os.environ.get("DATA_DIR", "")).rstrip("/"))
            if len(_base) == 8 and _base.isdigit():
                try:
                    import datetime as _dw
                    _a = _dw.datetime.strptime(_d, "%Y%m%d")
                    _b = _dw.datetime.strptime(_base, "%Y%m%d")
                    if abs((_b - _a).days) > 10:
                        continue
                except Exception as _e_sil7:
                    try:
                        from app.services import alert_hub as _ah_sil
                        _ah_sil.note_silent("t_gateway.py:1680", _e_sil7)
                    except Exception:
                        print("[silent:t_gateway.py:1680] %s: %s" % (type(_e_sil7).__name__, str(_e_sil7)[:110]), flush=True)
            return _op
    except Exception:
        return ""
    return ""


def gateway_execute(symbol: str, side: str, price: float, volume: int,
                    condition_id: Optional[int] = None,
                    trigger_id: Optional[int] = None,
                    reason: str = "",
                    decision_source: str = "agent",
                    is_stop_loss: bool = False,
                    account_id: str = ACCOUNT_T) -> Dict[str, Any]:
    """做T下单唯一入口：网关校验通过才调用执行器撮合。

    三权分立：Agent/AI 决策后提交 → 本网关（唯一放行者）→ MarcusVNPyExecutor(account_id)。
    2026-09-02: 加 account_id 参数(默认 t 兼容)——支持股票任务账户 stock 做T。
    decision_source: agent（触发复核路径）/ ai_led（AI 主动决策，无触发事件也可下单）。
    is_stop_loss: 止损离场卖腿——豁免日亏损熔断/回转额上限（止血动作必须执行）。
    执行器失败/被拒 → 更新 t_triggers 为 blocked + 审计。
    """

    # ── 浪型"不动"闸（账本 §9.287 ✓）：操作=参与（build/side/t_only）⇒ 当天不做**减仓性**操作 ✓
    if side == "sell" and not is_stop_loss:
        _wop = _wave_hold_action()
        if _wop in ("build", "side", "t_only"):
            _k_w = str(_turnover_kind(trigger_id, condition_id) or "")
            _r_w = str(reason or "")
            if _k_w in _WAVE_HOLD_ALLOW_KINDS:
                _k_w = ""                     # 高位/保护类 ⇒ **放行** ✓
            if _k_w in _WAVE_HOLD_REDUCE_KINDS or (not _k_w and any(
                    x in _r_w for x in ("去弱留强", "减T", "破黄线", "跌破分时均价", "黄线"))):
                if not any(x in _r_w for x in _WAVE_HOLD_PROTECT_KW):
                    print("[gateway] %s 浪型动作=%s（参与）⇒ **今天不折腾** ✓（狼大:主升浪来了不动 ✓）｜腿=%s"
                          % (symbol, _wop, _k_w or _r_w[:24]), flush=True)
                    return {"status": "blocked",
                            "reason": "浪型动作=%s（参与）⇒ 当日不做减仓性操作（狼大「主升浪中间的调整我一般不看的，这种一旦卖了很难买回来」✓）" % _wop,
                            "level": "WAVE_HOLD"}
    # 符号规范化（2026-09-17 死腿事故，唯一放行口兜底）：调用方（纪律腿/agent/接口）可能传
    # 腾讯小写形态（sz002587）。账本、`base_floor_shares`、`_count_today_trades`（G6 笔数）、
    # 成交记录全部按**系统标准形态**（SZ002587）取数——少归一一次就会出现"查不到底仓/笔数上限失效"
    # 这类静默错判。放在本函数入口 = 任何调用路径都跑不掉。
    symbol = std_symbol(symbol)
    # ── G3「卖出后删票」最后一道闸（2026-09-21）───────────────────────────────────────
    #   语料 2025-02-06「卖出然后删票」/ 2025-04-03「破之前新低的直接删票」。
    #   为什么放在**本函数入口**：这里是"做T下单唯一入口"⇒ 任何调用方（触发复核 / AI 主动决策 /
    #   接口手动 / 纪律腿）都跑不掉。触发写入侧（t_monitor 两条路径）已各有一道，本层是兜底：
    #   实测漏网的那 4 笔（002134 0114 10:55、603068 0120/0121/0126）就是走做T规则腿绕过了第一版补丁。
    #   开关 WOLF_TICKET_BAN_FIX（库内默认 0 ⇒ 生产零影响）；失败 fail-open。
    if str(side).lower() in ("buy", "买入"):
        try:
            from app.services import t_monitor as _tm_ban
            if _tm_ban.ticket_ban_blocked(symbol, True):
                if trigger_id:
                    try:
                        t_db.update_trigger_status(trigger_id, "blocked", reason="G3 删票名单：卖出后不再买入")
                    except Exception as _e_sil8:
                        try:
                            from app.services import alert_hub as _ah_sil
                            _ah_sil.note_silent("t_gateway.py:1738", _e_sil8)
                        except Exception:
                            print("[silent:t_gateway.py:1738] %s: %s" % (type(_e_sil8).__name__, str(_e_sil8)[:110]), flush=True)
                return {"status": "blocked", "reason": "G3 删票名单：该票已按语料『卖出后删票』登记，禁止再买入",
                        "level": "TICKET_BAN"}
        except Exception as _e_sil9:
            try:
                from app.services import alert_hub as _ah_sil
                _ah_sil.note_silent("t_gateway.py:1742", _e_sil9)
            except Exception:
                print("[silent:t_gateway.py:1742] %s: %s" % (type(_e_sil9).__name__, str(_e_sil9)[:110]), flush=True)
    from app.core.trading.marcus_trade import MarcusVNPyExecutor
    from paper_engine import PaperTradingEngine

    # 0.0) **挂单日终作废 → 释放冻结资金/冻结股**（2026-09-18；开关 WOLF_EXPIRE_STALE_ORDERS 默认关）
    #   背景：A 股挂单当日有效，但本系统没有这道收尾 ⇒ 实测一笔未成交买单把钱冻结整窗
    #   （drab_on SZ002602 1400@19.081 冻结 26,726.76 元直到 0203；drab_off 25,718.95 元同理），
    #   把仓位压到 ~11%（与"底仓到不了 65%"同源）。每进程每日只跑一次，成本可忽略。
    try:
        from app.services import t_order_expiry as _oex
        if _oex.enabled():
            _oex.expire_once_per_day(account_id)
    except Exception as _oe:
        print("[gateway] 挂单作废钩子失败(忽略): %s" % str(_oe)[:80], flush=True)
    from workspace_detector import DATA_DIR

    # 0) 账户白名单（2026-09-02：只有狼大做T(stock)可以操作；t 账户拒绝）
    if account_id not in EXEC_ALLOWED_ACCOUNTS:
        msg = f"账户 {account_id} 不在执行白名单 {sorted(EXEC_ALLOWED_ACCOUNTS)}（T_EXEC_ALLOWED_ACCOUNTS）"
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "blocked", reason=msg)
        return {"status": "blocked", "reason": msg, "level": "HARD"}

    # 0.1) **换手治理**（WOLF_TURNOVER_GUARD，库内默认关）：同价来回 / 下去不补 / 做T时段窗 / 跨日冷却
    #      语料：playbook:148「左右横跳＝新仓、没有套利空间」、:287 做T时段窗、:309 同价位激情来回、
    #      blueprint:231「每天只挂 2 个单子」、:232「下去不补，上来回到这个位置才补」。
    try:
        from app.services import t_turnover as _tw
        if _tw.enabled():
            # 腿型透传：③ 时段窗只对做T类腿型生效（建仓/减仓/止盈不受限；2026-09-19 用户拍板 A）
            # ⚠️ 2026-09-26（账本 §9.257）：**转正后加仓**要豁免做T时段窗 ✓
            #   它**不是做T**（是"突破式加仓" ✓），而调用点在 **09:15** 跑 ⇒ 不豁免就**永远被挡** ✗
            #   这里**从理由里识别**腿型 ✓（`gateway_execute` 没有 `trigger_kind` 形参 ✗，只能这样 ✓）
            _tkA = str(locals().get("trigger_kind", "") or "")
            if not _tkA and "埋伏转正加仓" in str(reason or ""):
                _tkA = "wolf_ambush_promote_add"
            # ⚠️ 2026-09-30（账本 §9.315）：**突破加仓／回踩确认加仓也要豁免时段窗** ✓
            #   他的原话 ✓：「我的操作是**突破加仓 回踩确认加仓** 中间不做大级别操作 向下破线止损」（2022-06-08）
            #   —— 这是**建仓/加仓**动作 ✓，不是"做T" ✗ ⇒ 时段窗（做T纪律 ✓）不该约束它 ✓
            #   实测 ✗：`被拦理由=做T时段窗：09:15 不在 09:45-10:00,14:00-14:30 内` ✓
            if not _tkA and ("突破加仓" in str(reason or "") or "回踩确认加仓" in str(reason or "")):
                _tkA = "wolf_breakout_add"
            # ⚠️ 2026-09-30（账本 §9.334 ✓）：**保护/减仓类也要豁免做T时段窗** ✗
            #   实测 ✓：SH603690 01-30 黄线减仓已算出 **1800 股**（止血 ✓）、G8 也放行了 ✓，
            #   却被 `做T时段窗：09:35 不在 09:45-10:00,14:00-14:30 内` 拦下 ✗ ⇒ **还是没卖出** ✓
            #   语料 ✓：「**止血动作必须能执行**」；而时段窗是**做T纪律**（`t_turnover` ③ ✓
            #   模块自述「**不在白名单的腿型不受③约束**」✓ —— ①同价来回/②下去不补/④跨日冷却**照旧适用** ✓）
            #   ⇒ 按腿型把它移出"做T类" ✓（只解 ③ ✓，不动 ①②④ ✓）
            _REDUCE_KINDS_G = ("custom_vwap_sell", "custom_support_sell", "custom_level_sell",
                               "custom_trail_sell", "wolf_defensive_t_reduce", "wolf_confirm_sell",
                               "wolf_boll_upper_sell", "wolf_board_half_sell",
                               "wolf_passive_stop_sell", "stop_loss", "wolf_early_swing_sell")
            if (not _tkA
                    and str(os.getenv("WOLF_SELL_EXEMPT_REDUCE", "0")).strip().lower()
                    in ("1", "true", "yes", "on")
                    and str(_turnover_kind(trigger_id, condition_id) or "") in _REDUCE_KINDS_G):
                _tkA = "wolf_reduce_protect"
            # ⚠️ 2026-09-26（账本 §9.270）：**已转正的仓位不再受「做T时段窗」约束** ✓
            #   时段窗是**做T的纪律** ✓（语料 playbook:287 自己划界：只对应「单日或…」的做T ✓）
            #   ⇒ 高抛/止盈/黄线**不是做T** ✓ ⇒ 对"已交回普通管理"的仓（转正 ✓）不该套用它 ✗
            #   实测（本轮 ✓）：被它挡最多的**正是 3 只已转正的票** ✗
            #     002792 **140 次** ✗｜603660 **96 次** ✗｜002384 **40 次** ✗（合计 318 次 ✓）
            if (str(os.getenv("WOLF_PROMOTED_SKIP_TWINDOW", "0")).strip().lower() in ("1", "true", "yes", "on")
                    and side == "sell"):
                try:
                    import json as _jsT
                    _rootT = os.path.dirname(os.environ.get("DATA_DIR", "/app/data")) or "/app/data"
                    _pfT = os.path.join(_rootT, "ambush_promoted.json")
                    if os.path.exists(_pfT):
                        with open(_pfT, encoding="utf-8") as _fT:
                            if str(symbol).upper() in {str(k).upper() for k in (_jsT.load(_fT) or {})}:
                                _tkA = "wolf_promoted_normal"     # 不在做T白名单 ⇒ `applies_to` False ✓
                except Exception as _e_sil10:
                    try:
                        from app.services import alert_hub as _ah_sil
                        _ah_sil.note_silent("t_gateway.py:1815", _e_sil10)
                    except Exception:
                        print("[silent:t_gateway.py:1815] %s: %s" % (type(_e_sil10).__name__, str(_e_sil10)[:110]), flush=True)
            _tok, _twhy = _tw.check(symbol, side, float(price or 0), account_id,
                                    reason=reason, is_stop_loss=is_stop_loss,
                                    trigger_kind=(_tkA or _turnover_kind(trigger_id, condition_id)))
            if not _tok:
                if trigger_id:
                    t_db.update_trigger_status(trigger_id, "blocked", reason=str(_twhy)[:250])
                return {"status": "blocked", "reason": _twhy, "level": "TURNOVER"}
    except Exception as _twe:
        print("[gateway] 换手闸异常(忽略): %s" % str(_twe)[:80], flush=True)

    # 0.2) **不追高闸**（WOLF_NO_CHASE，库内默认关；2026-09-19 用户拍板 A，落地台账 §29.1）
    #   语料：2025-04-03「冲上去一定不能追」、2025-10-09「任何时候不追高追涨」、
    #        2025-04-15 条件6「想追进去的…在下午 2.00-2.30 进行回补」。
    #   只拦**买腿**；腿保留活性 ⇒ 到 14:00–14:30 会被重新评估并放行（这就是「延后回补」的实现）。
    try:
        from app.services import wolf_no_chase as _nc
        if _nc.enabled() and str(side).lower() in ("buy", "买入") and trigger_id:
            _trg = _get_trigger(int(trigger_id)) or {}
            _sn = _trg.get("snapshot") or {}
            if isinstance(_sn, str):
                try:
                    import json as _json_nc
                    _sn = _json_nc.loads(_sn)
                except Exception:
                    _sn = {}
            _oknc, _whync = _nc.verdict(float(_trg.get("quote_price") or 0) or float(price or 0),
                                        float(_trg.get("suggest_bid_price") or 0),
                                        rise_pct=(_sn or {}).get("day_rise_pct"),
                                        quantile=(_sn or {}).get("day_quantile"))
            if not _oknc:
                t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whync)[:250])
                return {"status": "blocked", "reason": _whync, "level": "NO_CHASE"}
    except Exception as _nce:
        print("[gateway] 不追高闸异常(忽略): %s" % str(_nce)[:80], flush=True)

    # 0.2a) **狼大做T时间窗**（`WOLF_TRADE_WINDOW`；D2，2026-09-21 用户拍板"把 D2 补上"）
    #   语料 2025-04-15 条件2「**当日只做上午 9.45-10.00 下午 2.00-2.30 这两个时间段的交易，
    #   尽量避免开盘直接买卖和平稳时间的来回T**」。
    #   为什么在**执行口**再判一次（`t_monitor._round` 里已有一条）：
    #     ① `_round` 只遍历**条件腿**（`custom_prevlow` / `custom_m5dump` / `trend_break_buy`…），
    #        而本臂实测买成交 **100% 是 `wolf_zheng_t_buy`**（走 `_check_wolf_t_rules` 另一条路径）
    #        ⇒ 只改 `_round` 等于没接上（第 N 次同族"静默失效"，台账 §续4/§L）；
    #     ② 执行口是两条路径的**唯一共同收口**，且生产与回测共用同一段代码。
    #   ⚠️ 生效范围由 `WOLF_TW_KINDS` 决定（原白名单只有低吸腿 ⇒ 开了等于没开；pins 里已显式扩到
    #      `wolf_zheng_t_buy` 并附影响面）。腿**保留活性** ⇒ 回到窗口内自然放行 = 他的"只在两个时段做"。
    try:
        from app.services import wolf_trade_window as _twg
        if _twg.enabled() and str(side).lower() in ("buy", "买入") and trigger_id:
            _kg = _turnover_kind(trigger_id, condition_id)
            if _twg.applies_to(_kg):
                _okg, _whyg = _twg.allowed()
                if not _okg:
                    t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whyg)[:250])
                    return {"status": "blocked", "reason": _whyg, "level": "TRADE_WINDOW"}
    except Exception as _twge:
        print("[gateway] 做T时间窗异常(忽略): %s" % str(_twge)[:80], flush=True)

    # 0.2b) **开盘「快速拉升不追」**（`WOLF_OPEN_CHASE`，库内默认 0；D1，2026-09-21 用户拍板"把 D1 补上"）
    #   语料 2025-04-15 条件6「如果**当日开盘高开快速拉升**，或者**低开快速拉升想追进去的**，
    #   在下午2.00-2.30这个时间段进行回补」＋条件2「尽量避免开盘直接买卖」、2026-01-12「不要开盘买」。
    #   与 0.2 的区别：0.2 的参照物是**建议买价**（挂的前低/线价）⇒ 触发价=现价的腿天然豁免；
    #   本闸的参照物是**当日开盘价** ⇒ 正是苏州科达 0112 那种"开盘已冲上去"的腿。
    #   数据源两形状都要认：wolf_t_rules 腿写 `snapshot.open`（2026-09-21 补的字段）；
    #   条件腿写 `snapshot.fields.quote.open`。**两处都取不到 ⇒ fail-open**（绝不误拦）。
    try:
        from app.services import wolf_no_chase as _nco
        if _nco.open_enabled() and str(side).lower() in ("buy", "买入") and trigger_id:
            _trgo = _get_trigger(int(trigger_id)) or {}
            _sno = _trgo.get("snapshot") or {}
            if isinstance(_sno, str):
                try:
                    import json as _json_oc
                    _sno = _json_oc.loads(_sno)
                except Exception:
                    _sno = {}
            _sno = _sno or {}
            _open_px = _sno.get("open")
            if not _open_px:
                _flo = _sno.get("fields") or {}
                _qo = (_flo.get("quote") or {}) if isinstance(_flo, dict) else {}
                _open_px = _qo.get("open")
            _oko, _whyo = _nco.open_verdict(float(_trgo.get("quote_price") or 0) or float(price or 0),
                                            _open_px,
                                            kind=_turnover_kind(trigger_id, condition_id))
            if not _oko:
                t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whyo)[:250])
                return {"status": "blocked", "reason": _whyo, "level": "OPEN_CHASE"}
    except Exception as _oce:
        print("[gateway] 开盘不追高闸异常(忽略): %s" % str(_oce)[:80], flush=True)

    # 0.2c) **买入时点闸（B/C）**（`WOLF_FALLING_GATE` / `WOLF_WEAK_DEFER`，库内默认 0；
    #   2026-09-22 用户拍板 "A+B+C"）
    #   B：现价 < 当日 VWAP ∧（当日自开盘逐根走低 ∨ 跌破当日开盘）⇒ **不在下跌中买**
    #      （语料 2025-06-05「急杀可以买，缓跌不买」；实测 478/1,332 笔、单笔 −92 vs 其余 +3、跨臂 21/28 更差）
    #   C：当日为跌 ∧ 现价 < VWAP ∧ 未到 `WOLF_WEAK_DEFER_HM`(1445) ⇒ **延后到尾盘再评**
    #      （语料 2025-05-23「尾盘能回来就尾盘买 急什么」、2026-01-12「买点只有尾盘」）
    #   起点个案：环旭电子 601231 2026-03-03 —— 09:40 的 `wolf_zheng_t_buy` 因理由含「未跌破止损」
    #   被换手闸整段旁路（见 t_turnover 的 A 修复），买在 47.39（当日最高 48.50）⇒ 当日收跌停 43.58。
    #   数据源与 C2′/C2″ 同一套分钟档（`intraday_crush`）；取不到 ⇒ fail-open。
    if str(side).lower() in ("buy", "买入"):
        try:
            try:
                import intraday_crush as _icg
            except ImportError:
                from main_line import intraday_crush as _icg
            if _icg.falling_on() or _icg.weak_defer_on():
                _pc_g = None
                if trigger_id:
                    _trg3 = _get_trigger(int(trigger_id)) or {}
                    _sn3 = _trg3.get("snapshot") or {}
                    if isinstance(_sn3, str):
                        try:
                            import json as _js3
                            _sn3 = _js3.loads(_sn3)
                        except Exception:
                            _sn3 = {}
                    _sn3 = _sn3 or {}
                    _pc_g = _sn3.get("pre_close")
                    if not _pc_g:
                        _fl3 = _sn3.get("fields") or {}
                        _q3 = (_fl3.get("quote") or {}) if isinstance(_fl3, dict) else {}
                        _pc_g = _q3.get("pre_close")
                _now3 = datetime.now()
                if _icg.falling_v2_on():
                    # B 闸 v2（`WOLF_FALLING_GATE_V2`，库内默认 0）：在 v1 的"缓跌形状"骨架上，
                    #   豁免他四类买点（到线 / 到前低 / 破一下当天拉回 / 急杀）——语料与判据见
                    #   `intraday_crush.falling_v2` 的 docstring。线值取 13/34/60/144（**他的话**）。
                    _f3 = _icg.feats((str(symbol)[2:8] + "_" + str(symbol)[:2])
                                     if str(symbol)[:2] in ("SH", "SZ") else str(symbol),
                                     _now3.strftime("%Y%m%d"), _now3.strftime("%H%M"), min_bars=2)
                    if _f3:
                        _lines = []
                        try:
                            try:
                                import wolf_ma_line_entry as _ml
                            except ImportError:
                                from main_line import wolf_ma_line_entry as _ml
                            _lines = _ml.line_values(str(symbol), _now3.strftime("%Y%m%d"), _f3.get("px"))
                        except Exception as _mle:
                            print("[gateway] 到线判据线值取数失败(不豁免): %s" % str(_mle)[:60], flush=True)
                        _bad2, _why2 = _icg.falling_v2(_f3, {"lines": _lines, "prev_low": _f3.get("prev_low")})
                        _ok3 = not _bad2
                        _why3 = _why2
                    else:
                        _ok3, _why3 = True, "缺分钟档→放行"
                else:
                    _ok3, _why3 = _icg.timing_verdict(symbol, _now3.strftime("%Y%m%d"),
                                                      _now3.strftime("%H%M"), pre_close=_pc_g)
                if not _ok3:
                    if trigger_id:
                        t_db.update_trigger_status(trigger_id, "blocked", reason=str(_why3)[:250])
                    return {"status": "blocked", "reason": _why3, "level": "BUY_TIMING"}
        except Exception as _tge:
            print("[gateway] 买入时点闸异常(忽略): %s" % str(_tge)[:80], flush=True)

    # 0.25) **放量破位门**（WOLF_DIP_VOL_GATE，库内默认关；2026-09-19 用户拍板 A）
    #   语料：254 低吸 =「破前低 + **缩量**」；2025-06-25「只要看见没量就别追」；
    #        2026-01-12「等收盘确认破位」。放量破前低 = 下跌未止，不是低吸位。
    #   数据：形态腿看 snapshot.prev_low/today_low/vol_ratio（t_monitor 写入）；
    #        条件腿看 snapshot.fields.vol_ratio / fields.quote.low / trigger_price（= 前低锚）。缺数据 fail-open。
    try:
        from app.services import wolf_dip_volume_gate as _dg
        if _dg.enabled() and str(side).lower() in ("buy", "买入") and trigger_id:
            _trg2 = _get_trigger(int(trigger_id)) or {}
            _sn2 = _trg2.get("snapshot") or {}
            if isinstance(_sn2, str):
                try:
                    import json as _json_dg
                    _sn2 = _json_dg.loads(_sn2)
                except Exception:
                    _sn2 = {}
            _fl2 = (_sn2 or {}).get("fields") or {}
            _q2 = (_fl2.get("quote") or {}) if isinstance(_fl2, dict) else {}
            _pl2 = (_sn2 or {}).get("prev_low") or _trg2.get("trigger_price")
            _tl2 = (_sn2 or {}).get("today_low") or (_q2 or {}).get("low")
            _vr2 = (_sn2 or {}).get("vol_ratio")
            if _vr2 is None and isinstance(_fl2, dict):
                _vr2 = _fl2.get("vol_ratio")
            _okdg, _whydg = _dg.verdict(_pl2, _tl2, _vr2, price=float(price or 0), side="buy")
            if not _okdg:
                t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whydg)[:250])
                return {"status": "blocked", "reason": _whydg, "level": "DIP_VOL"}
    except Exception as _dge:
        print("[gateway] 放量破位门异常(忽略): %s" % str(_dge)[:80], flush=True)

    # 0.3) **加仓口径闸**（WOLF_ADDON_CAP，库内默认关；2026-09-20 用户拍板"先加①②"）
    #   语料：2026-06-15「突破下跌趋势**第一根开头我加一半** 突破后的**第二根红K尾盘 打满**
    #        然后之后就只等卖点 中间会有调仓 但是**没有减仓**」；
    #        2026-04-10「卖出去的怕忍不住买回来怎么…找**低位的线**挂进去…**挂远一点**」；
    #        2025-04-03「**冲上去一定不能追**」。
    #   实测（y26 SH603773）：我们 0306 的进场 38.581 与他的 0.618 位 38.55 几乎一致，但
    #   0311（当日收涨停）/0312/0316/0317 在 41.68~42.48 追高加仓/买回，把均价从 39.02 抬到 42.19（高 8.1%）
    #   ⇒ 同一波下跌里他 −6.5%、我们 −14.2%。本闸拦「超两批 / 高于本轮首笔买入价 +X%」的加仓与买回。
    #   做T买回在不创新高且价格在上限内时**照常放行**（不掐死做T）。
    if str(side).lower() in ("buy", "买入"):
        # ── **加仓＝B：转正日重置本轮锚**（账本 §9.235；`WOLF_AMBUSH_PROMOTE_ADD`，库内默认 0 = 关 ✓）──
        #   量化（长样本 21 个月 ＋ 样本外分割 ✓）：A 不重置 +524.96%／−10.0%（比例 52.5）
        #     **B 转正日重置 ＋ 3 自然日内 ＋ 价 ≤ 转正日价×1.04 ⇒ +715.75%／−13.0%（比例 55.1）** ✓✓
        #     且 **C"马上加"只有 +664.30%** ✗ ⇒ 「**不追高 ≤4%**」本身创造价值 ✓；两段都优于 A ✓
        #   语料 ✓：「突破下跌趋势**第一根开头我加一半** 突破后的**第二根红K尾盘打满**」✓（突破后极短时间 ✓）
        #            ＋「**不追高**」✓（保护四原则之一 ✓）
        _pa_skip = False
        if str(os.getenv("WOLF_AMBUSH_PROMOTE_ADD", "0")).strip().lower() in ("1", "true", "yes", "on"):
            try:
                import json as _jsA
                _DA = os.environ.get("DATA_DIR", "/app/data")
                _pA = os.path.join(_DA, "ambush_promoted.json")
                if os.path.exists(_pA):
                    with open(_pA, encoding="utf-8") as _fA:
                        _allA0 = _jsA.load(_fA) or {}
                        _recA = {}
                        for _kA, _vA in _allA0.items():
                            if str(_kA).upper() == str(symbol).upper():
                                _recA = _vA or {}
                                break
                    if _recA and not _recA.get("added"):
                        _pd = str(_recA.get("pday") or "")
                        _ppx = float(_recA.get("ppx") or 0)
                        # ⚠️ "今天"必须取**模拟日** ✗ —— 回测里 `DATA_DIR` 的末段就是当日（`<root>/<YYYYMMDD>` ✓），
                        #   而 `datetime.now()` 未必被 patch 到本模块 ✗（实测差异可达 200+ 天 ✗）
                        _todayA = ""
                        try:
                            _seg2 = os.path.basename(os.path.normpath(os.environ.get("DATA_DIR", "") or ""))
                            if len(_seg2) == 8 and _seg2.isdigit():
                                _todayA = _seg2
                        except Exception:
                            _todayA = ""
                        if not _todayA:
                            _todayA = datetime.now().strftime("%Y%m%d")
                        try:
                            _gap = (datetime.strptime(_todayA, "%Y%m%d") - datetime.strptime(_pd, "%Y%m%d")).days
                        except Exception:
                            _gap = 99
                        if 0 <= _gap <= 3 and _ppx > 0 and float(price or 0) <= _ppx * 1.04:
                            _pa_skip = True
                            print("[gateway] 转正加仓放行(加仓=B)：%s 转正日 %s 价 %.2f ≤ %.2f×(1+4%%)，"
                                  "距转正 %d 天 ✓" % (symbol, _pd, float(price or 0), _ppx, _gap), flush=True)
                            _recA["added"] = True
                            with open(_pA, encoding="utf-8") as _fB:
                                _allA = _jsA.load(_fB) or {}
                            _allA[symbol] = _recA
                            with open(_pA, "w", encoding="utf-8") as _fC:
                                _jsA.dump(_allA, _fC, ensure_ascii=False)
            except Exception as _ePA:
                print("[gateway] 转正加仓放行异常(按原流程): %s" % str(_ePA)[:70], flush=True)
        try:
            from app.services import wolf_addon_cap as _ac
            # ⚠️ 2026-09-26（账本 §9.269；用户拍板「a」✓）：
            #   **转正后加仓**要**豁免「加仓口径闸」** ✗ —— 那道闸的锚是「**埋伏建仓那一笔**」✓
            #     ⇒ 要求 价 ≤ 首笔×1.04 ∧ 距首笔 ≤3 自然日 ✓
            #   而**转正通常发生在建仓后 8～11 天**且价已 > 首笔 +4% ✗
            #     ⇒ **两条同时满足的概率≈0** ⇒ 转正加仓**必然被拦** ✗（实测 603660：首笔 11.21 ⇒ 上限 11.66，而价 12.22 ✗）
            #   ⇒ 按用户拍板：**转正加仓只服从量化的 B 口径**（锚＝转正日 ✓）
            #     开关 `WOLF_PROMOTE_ADD_EXEMPT`（**库内默认 0 = 关** ✓；pins 开 ✓）
            _pa_exempt = (str(os.getenv("WOLF_PROMOTE_ADD_EXEMPT", "0")).strip().lower()
                          in ("1", "true", "yes", "on")
                          and "埋伏转正加仓" in str(reason or ""))
            if _pa_skip or _pa_exempt:
                _ac = None
                if _pa_exempt:
                    print("[gateway] %s 转正加仓 ⇒ **豁免加仓口径闸** ✓（只服转正日锚：3 日内 ∧ ≤ 转正价×1.04 ✓）"
                          % symbol, flush=True)
            # ⚠️ 2026-09-30（账本 §9.321）：**回踩确认加仓独立** ✓ —— 用户选 (b) ✓
            #   他 2022-06-08「突破加仓 **回踩确认加仓**」✓ ⇒ 回踩是**突破之后独立的一次机会** ✓
            #   ⇒ 不该被"突破那 2 笔"的上限吃掉 ✗（否则与他的原话冲突 ✗）
            if _ac is not None and "回踩确认加仓" in str(reason or "") and \
                    str(os.getenv("WOLF_RETEST_ADD_INDEPENDENT", "0")).strip().lower() \
                    in ("1", "true", "yes", "on"):
                print("[gateway] %s 回踩确认加仓 ⇒ **豁免加仓口径闸** ✓（他 2022-06-08："
                      "突破加仓 / 回踩确认加仓 是两次独立机会 ✓）" % symbol, flush=True)
                _ac = None
            if _ac is not None and _ac.enabled():
                _oka, _whya = _ac.check(account_id, symbol, int(volume or 0),
                                        datetime.now().strftime("%Y%m%d"), add_price=float(price or 0))
                if not _oka:
                    if trigger_id:
                        t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whya)[:250])
                    return {"status": "blocked", "reason": _whya, "level": "ADDON_CAP"}
        except Exception as _ace:
            print("[gateway] 加仓口径闸异常(忽略): %s" % str(_ace)[:80], flush=True)

    # 0.15) **候选腿质量闸**（WOLF_LEG_QUALITY，库内默认关；2026-09-18 立项）
    #   ① 无底仓建仓：现算 build_score，< build_score_min(0.78) → 不批；③ 有底仓加仓：首买已满 N 日
    #   且浮亏 ≤ -2% → 不批。根因：T_BUY_TIER_LIMIT_ENABLED=0 时 build_sizing 被绕过 ⇒ 打分门槛从未生效。
    if side == "buy":
        try:
            from app.services import t_leg_quality as _lq
            if _lq.enabled():
                _led = get_sellable_ledger(account_id) or {}
                _row = _led.get(symbol) or {}
                _vol = int(float(_row.get("volume") or 0))
                _okq, _whyq = _lq.check(symbol, account_id, float(price or 0), _vol > 0,
                                        avg_cost=float(_row.get("avg_price") or 0),
                                        day=datetime.now().strftime("%Y%m%d"))
                if not _okq:
                    if trigger_id:
                        t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whyq)[:250])
                    return {"status": "blocked", "reason": _whyq, "level": "LEGQUALITY"}
        except Exception as _lqe:
            print("[gateway] 候选腿质量闸异常(忽略): %s" % str(_lqe)[:80], flush=True)

    # 0.2) D) **最小有效规模**（2026-09-18 用户"都改"；`WOLF_MIN_BUY_NOTIONAL`，库内默认 0=关）
    #   实测：暖启臂 18 笔买入里 11 笔 ≤1 万，其中 7 笔只有 100 股 ≈ 2,000 元（0.8% 仓位）——
    #   这类碎单对仓位毫无意义却会消耗 G6 笔数/回转额。原"最小有效规模 8,000 元"只在两分法
    #   **建仓**路径（可卖=0）生效，加仓/T 腿没有下限 ⇒ 在这里对**所有**买腿兜底：
    #   低于门槛 → 抬到整手门槛量；若超出后续档位/容量上限，则由 validate_order 正常拒绝。
    try:
        _min_notional = float(os.getenv("WOLF_MIN_BUY_NOTIONAL", "0") or 0)
        if side == "buy" and _min_notional > 0 and float(price or 0) > 0 and int(volume or 0) > 0:
            _amt = float(price) * int(volume)
            if _amt < _min_notional:
                import math as _m
                _need = int(_m.ceil(_min_notional / float(price) / 100.0) * 100)
                print("[gateway] 买腿 %s %d 股(%.0f 元) 低于最小有效规模 %.0f 元 → 抬到 %d 股"
                      % (symbol, int(volume), _amt, _min_notional, _need), flush=True)
                volume = _need
    except Exception as _me:
        print("[gateway] 最小有效规模处理失败(忽略): %s" % str(_me)[:70], flush=True)

    # 0.2e) ③ **正T买单笔名义上限**（2026-09-25；`WOLF_ZHENGT_MAX_NOTIONAL`，库内默认 0=关）
    #   语料（逐字）：2026-03-31「必须是有底仓、自己一直关注的方向（没有底仓的不做）；**只用10%仓位做T**」。
    #   实测依据（账本 §9.18③/§9.24）：正T买是四臂的亏损中心（T13 72 腿 −16,418 / T14 65 −13,413 /
    #   T11 41 −11,566），且 >10% 名义那批的收益率也系统性更差 ⇒ 10% 上限既缩规模又压掉最差的一批；
    #   反事实增益 +3,536 / +2,897 / +3,420 / +1,800（四臂全正）。
    #   作用面：**仅** `wolf_zheng_t_buy`（正T腿）；建仓腿/条件腿不受影响。缩到 0 股 ⇒ 拒绝该腿。
    try:
        from app.services import t_capacity as _tc
        _zt_pct = _tc.zhengt_cap_pct()
        if (side == "buy" and _zt_pct > 0 and float(price or 0) > 0
                and int(volume or 0) > 0 and trigger_id):
            _trg_zt = _get_trigger(int(trigger_id)) or {}
            if str(_trg_zt.get("event_type") or "") == "wolf_zheng_t_buy":
                _eq_zt = _tc.account_equity(account_id, get_sellable_ledger(account_id), price_hint=float(price))
                _v_zt, _why_zt = _tc.clamp_zhengt_notional(int(volume), float(price), _eq_zt, _zt_pct)
                if _v_zt != int(volume):
                    print("[gateway] ③ 正T名义上限：%s %d→%d 股（%s）"
                          % (symbol, int(volume), _v_zt, str(_why_zt)[:90]), flush=True)
                volume = int(_v_zt)
                if volume <= 0:
                    if trigger_id:
                        t_db.update_trigger_status(int(trigger_id), "blocked",
                                                   reason=("③ 正T名义上限：%s" % str(_why_zt))[:250])
                    return {"status": "blocked", "reason": _why_zt, "level": "ZHENGT_CAP"}
    except Exception as _ze:
        print("[gateway] 正T名义上限异常(忽略): %s" % str(_ze)[:80], flush=True)

    # 0.2e2) **L3 v1：正T买"当日累计额度"**（`WOLF_ZT_DAY_BUDGET`，库内默认 0=关）
    #   语料 2026-03-31「只用10%仓位做T」⇒ 当日累计口径（③ 只管单笔）。
    try:
        _ztb = zt_day_budget_pct()
        if (side == "buy" and _ztb > 0 and float(price or 0) > 0 and int(volume or 0) > 0 and trigger_id):
            _trg_b = _get_trigger(int(trigger_id)) or {}
            if str(_trg_b.get("event_type") or "") == "wolf_zheng_t_buy":
                _day_b = datetime.now().strftime("%Y%m%d")
                from app.services import t_capacity as _capd   # 本地 import（模块级不引入依赖）
                _eq_b2 = _capd.account_equity(account_id, get_sellable_ledger(account_id),
                                              price_hint=float(price))
                _used_b = zt_used_today(account_id, _day_b)
                _v_b2, _why_b2 = clamp_zt_day_budget(int(volume), float(price), _eq_b2, _used_b, _ztb)
                if _v_b2 != int(volume):
                    print("[gateway] 日额度：%s %d→%d 股（%s）" % (symbol, int(volume), _v_b2, str(_why_b2)[:90]),
                          flush=True)
                volume = int(_v_b2)
                if volume <= 0:
                    t_db.update_trigger_status(int(trigger_id), "blocked",
                                               reason=("日额度：%s" % str(_why_b2))[:250])
                    return {"status": "blocked", "reason": _why_b2, "level": "ZT_DAY_BUDGET"}
    except Exception as _zbe:
        print("[gateway] 当日正T额度异常(忽略): %s" % str(_zbe)[:80], flush=True)

    # 0.2e3) **L3：腿型额度比例**（正T买 : 建仓腿 = 1 : `WOLF_LEG_RATIO_ZT_TB`；库内默认 0=关）
    #   用户 2026-09-25 拍板：抄 T5 的 **1:1.3**（T5 是四臂里唯一赚钱的，其资金结构=正 alpha 腿拿到足够名义）。
    #   口径：**正T买当日累计额度 = 上一交易日建仓腿名义 / ratio**（拿不到参照 ⇒ 不介入，fail-open）。
    #   与 `WOLF_ZT_DAY_BUDGET` 叠加时取更严者；只缩不放大。
    try:
        from app.services import t_capacity as _tcr
        _r_ratio = _tcr.leg_ratio()
        if (side == "buy" and _r_ratio > 0 and float(price or 0) > 0
                and int(volume or 0) > 0 and trigger_id):
            _trg_r = _get_trigger(int(trigger_id)) or {}
            if str(_trg_r.get("event_type") or "") == "wolf_zheng_t_buy":
                _day_r = datetime.now().strftime("%Y%m%d")
                _ref_r = _tcr.ratio_ref_notional(_day_r)
                _v_r, _why_r = _tcr.clamp_ratio_budget(int(volume), float(price),
                                                       zt_used_today(account_id, _day_r), _ref_r, _r_ratio)
                if _v_r != int(volume):
                    print("[gateway] 腿型比例：%s %d→%d 股（%s）" % (symbol, int(volume), _v_r, str(_why_r)[:90]),
                          flush=True)
                volume = int(_v_r)
                if volume <= 0:
                    t_db.update_trigger_status(int(trigger_id), "blocked",
                                               reason=("腿型比例：%s" % str(_why_r))[:250])
                    return {"status": "blocked", "reason": _why_r, "level": "LEG_RATIO"}
    except Exception as _re2:
        print("[gateway] 腿型比例异常(忽略): %s" % str(_re2)[:80], flush=True)

    # 0.2e4) **低吸腿当日额度**（`WOLF_LOWDIP_DAY_BUDGET`，库内默认 0=关；账本 §9.70 用户拍板落地）
    #   只压"低吸(253/254)"腿型的当日累计名义，不动其它腿型（挪动由趋势突破腿自身条件决定）。
    try:
        if (side == "buy" and lowdip_day_budget_pct() > 0 and float(price or 0) > 0
                and int(volume or 0) > 0 and trigger_id):
            _k_ld = _turnover_kind(trigger_id, condition_id)
            if is_lowdip_kind(_k_ld):
                _day_ld = datetime.now().strftime("%Y%m%d")
                try:
                    # 与 ③（0.2e2）同一取法：t_capacity.account_equity(account, ledger, price_hint)
                    from app.services import t_capacity as _tce
                    _eq_ld = _tce.account_equity(account_id, get_sellable_ledger(account_id),
                                                 price_hint=float(price))
                except Exception as _eqe:
                    _eq_ld = None
                    print("[gateway] 低吸额度：权益取数失败（不介入）: %s" % str(_eqe)[:60], flush=True)
                if _eq_ld:
                    _v_ld, _why_ld = clamp_lowdip_day_budget(int(volume), float(price), _eq_ld,
                                                             lowdip_used_today(account_id, _day_ld))
                    if _v_ld != int(volume):
                        print("[gateway] 低吸额度：%s %d→%d 股（%s）" % (symbol, int(volume), _v_ld, str(_why_ld)[:90]),
                              flush=True)
                    volume = int(_v_ld)
                    if volume <= 0:
                        t_db.update_trigger_status(int(trigger_id), "blocked",
                                                   reason=("低吸额度：%s" % str(_why_ld))[:250])
                        return {"status": "blocked", "reason": _why_ld, "level": "LOWDIP_BUDGET"}
    except Exception as _lde:
        print("[gateway] 低吸额度异常(忽略): %s" % str(_lde)[:80], flush=True)

    # 0.2e5) 低吸腿「**只在趋势腿已建仓的票上做**」（`WOLF_LOWDIP_ON_TREND_ONLY`，库内默认 0）──
    #   用户 2026-09-25「试试吧」：语料里低吸=建仓/**加仓摊薄**（618 建仓 / 786 补 / 小跌小买 ✓），
    #   而摊薄的前提是**这票会回来**（结构已确认/趋势在 ✓）；实测我们 40 笔低吸**全部**发生在
    #   「票上没有趋势底仓」的票上 ✗ ⇒ 摊薄实为「下跌中加仓」✗（历史 0 样本 ⇒ 只能前瞻验证 ✓）
    if str(os.getenv("WOLF_LOWDIP_ON_TREND_ONLY", "0")).strip() == "1":
        try:
            if side == "buy" and int(volume or 0) > 0 and trigger_id:
                _k_to = _turnover_kind(trigger_id, condition_id)
                # 埋伏腿（wolf_ambush_buy）= 低位**先手建仓** ⇒ 豁免本闸 ✓（它不是"摊薄"✗）
                if (is_lowdip_kind(_k_to) and str(_k_to) != "wolf_ambush_buy"
                        and not _lowdip_trend_ok(account_id, symbol)):
                    _why_to = ("低吸仅限趋势腿已建仓的票（票上无趋势底仓 ⇒ 不是摊薄而是首建/加仓下跌 ✗）"
                               "（WOLF_LOWDIP_ON_TREND_ONLY=1）")
                    print("[gateway] 低吸趋势票门拦住 %s: %s" % (symbol, _why_to[:80]), flush=True)
                    t_db.update_trigger_status(int(trigger_id), "blocked", reason=_why_to[:250])
                    return {"status": "blocked", "reason": _why_to, "level": "LOWDIP_TREND_ONLY"}
        except Exception as _lte:
            print("[gateway] 低吸趋势票门异常(放行): %s" % str(_lte)[:80], flush=True)

    # ── 0.2e6) **埋伏腿小仓档**（`WOLF_AMBUSH_SIZE_PCT`，**库内默认 0 = 关** ✓）──────────
    #   用户 2026-09-26（藏格矿业 800 股 ≈ 账户 27% ✗）：「为什么埋伏股数这么多？」
    #   病灶：埋伏腿进了 `T_BUILD_KINDS`（为过语料前置门② ✓）⇒ 走**普通建仓规模**（单笔 15% ✗）
    #   语料：「**分账户小额买，一次只买一点点**」（§9.150 引用 ✓）⇒ 埋伏应是**试仓小仓** ✓
    #   实现：按**权益×PCT%÷现价**压上限（只压不抬 ✓）；同日去重由 `WOLF_AMBUSH_ONE_PER_DAY` 管 ✓
    try:
        _amb_pct = float(os.getenv("WOLF_AMBUSH_SIZE_PCT", "0") or 0)
    except Exception:
        _amb_pct = 0.0
    if side == "buy" and _amb_pct > 0 and int(volume or 0) > 0:
        try:
            _k_amb = _turnover_kind(trigger_id, condition_id) if trigger_id else ""
        except Exception:
            _k_amb = ""
        if str(_k_amb) == "wolf_ambush_buy":
            # ── **同时持仓上限**（`WOLF_AMBUSH_MAX_POSITIONS`，库内默认 0 = 关 ✓）──────
            #   量化（账本 §9.196，全历史 21 个月 ✓）：
            #     不限 ⇒ +97.02%／**最大回撤 −34.3%** ✗｜**≤15 ⇒ +58.58%／−21.6%** ✓（比例几乎不变 ✓）
            #   动机：2026-07「**止损群**」✗（同一周 6 只各亏 ~5,000 ⇒ 单月 −30.6% ✗）
            #   只拦**新建仓** ✓（同一票的加仓不占新名额 ✓）
            try:
                _max_pos = int(float(os.getenv("WOLF_AMBUSH_MAX_POSITIONS", "0") or 0))
            except Exception:
                _max_pos = 0
            # ── **指数层预警 ⇒ 降防御档**（账本 §9.205；`WOLF_AMBUSH_WARN_*`，库内默认全关 ✓）──
            #   量化：无预警 +58.58%／回撤 **−21.6%** ✗；**预警⇒防御档 5 只 +75.93%／回撤 −7.5%** ✓✓
            #   样本外分段亦成立 ✓（2026 动荡段 +35.12%／−7.9% vs 现状 +17.96%／−19.0% ✓）
            _warn_cap = 0
            try:
                _wma = float(os.getenv("WOLF_AMBUSH_WARN_MA", "0") or 0)
                _wbw = float(os.getenv("WOLF_AMBUSH_WARN_BREADTH", "0") or 0)
                if _wma > 0 or _wbw > 0:
                    from app.services import t_monitor as _tmW
                    if _tmW.ambush_warn_active():
                        _warn_cap = int(float(os.getenv("WOLF_AMBUSH_WARN_DEF_CAP", "5") or 5))
                        if _warn_cap > 0 and _max_pos <= 0:
                            _max_pos = _warn_cap          # 预警时即使没开持仓上限，也按防御档约束新开 ✓
            except Exception:
                _warn_cap = 0
            if _warn_cap > 0 and _max_pos > _warn_cap:
                _max_pos = _warn_cap
            if _max_pos > 0:
                _already = False
                try:
                    _already = _ambush_position(account_id, symbol)
                except Exception:
                    _already = False
                if not _already and _ambush_open_count(account_id) >= _max_pos:
                    _why_mp = ("埋伏同时持仓上限：当前埋伏仓已达 %d 只 ⇒ 不再新开"
                               "（WOLF_AMBUSH_MAX_POSITIONS=%d）" % (_max_pos, _max_pos))
                    print("[gateway] %s %s" % (symbol, _why_mp), flush=True)
                    if trigger_id:
                        t_db.update_trigger_status(int(trigger_id), "blocked", reason=_why_mp[:250])
                    return {"status": "blocked", "reason": _why_mp, "level": "AMBUSH_MAX_POSITIONS"}
            try:
                from app.services import t_capacity as _tce
                _eq = _tce.account_equity(account_id, get_sellable_ledger(account_id),
                                          price_hint=float(price)) or 0.0
            except Exception:
                _eq = 0.0
            # ── 「**小跌小买 大跌大买**」（语料 ✓）：按"**距持仓均价的跌幅**"分档放大单笔 ✓
            #   `WOLF_AMBUSH_SIZE_TIER_STEP`（每档跌幅%，库内默认 0 = 关 ✓）
            #   `WOLF_AMBUSH_SIZE_TIER_GAIN`（每档放大倍数，默认 0.5 ✓）
            #   `WOLF_AMBUSH_SIZE_MAX_MULT`（放大上限，默认 2 ✓）
            _amb_mult = 1.0
            try:
                _t_step = float(os.getenv("WOLF_AMBUSH_SIZE_TIER_STEP", "0") or 0)
                if _t_step > 0:
                    _t_gain = float(os.getenv("WOLF_AMBUSH_SIZE_TIER_GAIN", "0.5") or 0.5)
                    _t_max = float(os.getenv("WOLF_AMBUSH_SIZE_MAX_MULT", "2") or 2)
                    from sqlalchemy import text as _t5
                    with SessionLocal() as _s5:
                        _avg_t = float((_s5.execute(_t5(
                            "SELECT COALESCE(AVG(price),0) FROM paper_trades WHERE account_id=:a "
                            "AND symbol=:s AND direction LIKE '买%' AND COALESCE(reason,'') LIKE :p"),
                            {"a": account_id, "s": symbol, "p": "%wolf_ambush_buy%"}).scalar()) or 0)
                    if _avg_t > 0 and float(price or 0) > 0:
                        _drop_t = (1 - float(price) / _avg_t) * 100.0          # 距持仓均价的跌幅% ✓
                        _tiers = max(0.0, (_drop_t - _t_step) / _t_step) + 1.0 if _drop_t >= _t_step else 0.0
                        _amb_mult = min(1.0 + _t_gain * _tiers, max(1.0, _t_max))
                        if _amb_mult > 1.0:
                            print("[gateway] 埋伏加仓力度：%s 跌幅 %.1f%% ⇒ 放大 ×%.2f（小跌小买/大跌大买 ✓）"
                                  % (symbol, _drop_t, _amb_mult), flush=True)
            except Exception:
                _amb_mult = 1.0
            if _eq > 0 and float(price or 0) > 0:
                # 2026-09-26 用户「必须是整百股」✓：先算原始量，再**向下取整到 100 的整数倍** ✗
                _cap = (int(_eq * (_amb_pct * _amb_mult) / 100.0 / float(price)) // 100) * 100
                if _cap >= 100 and int(volume) > _cap:
                    print("[gateway] 埋伏小仓档：%s %d→%d 股（权益 %.0f×%.1f%%÷%.2f）"
                          % (symbol, int(volume), _cap, _eq, _amb_pct, float(price)), flush=True)
                    volume = _cap
    # 同日去重（`WOLF_AMBUSH_ONE_PER_DAY`，**库内默认 0 = 关** ✓）
    #   实测藏格矿业 01-30 **同日触发两次 ⇒ 买了两笔（400+400）** ✗
    if (side == "buy" and str(os.getenv("WOLF_AMBUSH_ONE_PER_DAY", "0")).strip().lower() in ("1", "true", "yes", "on")):
        try:
            _k_one = _turnover_kind(trigger_id, condition_id) if trigger_id else ""
        except Exception:
            _k_one = ""
        if str(_k_one) == "wolf_ambush_buy":
            from sqlalchemy import text as _t1
            with SessionLocal() as _s1:
                _n_today = _s1.execute(_t1(
                    "SELECT COUNT(*) FROM paper_trades WHERE account_id=:a AND symbol=:s "
                    "AND trade_date=:d AND direction LIKE '买%' "
                    "AND COALESCE(reason,'') LIKE :p"),
                    {"a": account_id, "s": symbol,
                     "d": datetime.now().strftime("%Y-%m-%d"), "p": "%wolf_ambush_buy%"}).scalar() or 0
            if int(_n_today) > 0:
                _why_one = "埋伏腿同日去重：本票今日已有埋伏买入 ⇒ 只建一仓（WOLF_AMBUSH_ONE_PER_DAY=1）"
                print("[gateway] %s %s" % (symbol, _why_one), flush=True)
                if trigger_id:
                    t_db.update_trigger_status(int(trigger_id), "blocked", reason=_why_one[:250])
                return {"status": "blocked", "reason": _why_one, "level": "AMBUSH_ONE_PER_DAY"}

    # ── 0.2e8) **埋伏腿的"加仓三项"**（狼大三条硬规则 ✓；三个开关**库内默认 0 = 关** ✓）──
    #   语料（逐字 ✓）：
    #     · 「**分批加法点，不到点位不加**」／「价格必须到位——要到他设定的**第一/第二加法点**，
    #        且**下跌幅度足够**」／「未到第二个加法点位、跳水也**不够幅度**，所以**不加**」✓
    #     · 「**分批小加**；**不到位置、不够幅度就不加**」✓
    #     · 「长波段的**补仓点要隔得很远**」✓
    #   实现（只对埋伏腿 ✓；取不到买入史 ⇒ 视作首次建仓 ⇒ 放行 ✓）：
    #     ① 点位：`price ≤ 上次买入价 ×(1 − n×STEP%)`（第 n 次加仓 ⇒ 第一/第二加法点 ✓）
    #     ② 幅度：`price ≤ 持仓均价 ×(1 − MIN_DROP%)`（跌得够多才加 ✓）
    #     ③ 间距：距上次买入 **≥ GAP 个自然日**（"补仓点要隔得很远" ✓；自然日近似，已标注 ✓）
    if side == "buy":
        try:
            _k_add = _turnover_kind(trigger_id, condition_id) if trigger_id else ""
        except Exception:
            _k_add = ""
        if str(_k_add) == "wolf_ambush_buy":
            _step = float(os.getenv("WOLF_AMBUSH_ADD_STEP_PCT", "0") or 0)
            _mind = float(os.getenv("WOLF_AMBUSH_ADD_MIN_DROP_PCT", "0") or 0)
            _gap = float(os.getenv("WOLF_AMBUSH_ADD_MIN_GAP_DAYS", "0") or 0)
            if _step > 0 or _mind > 0 or _gap > 0:
                _hist = []
                try:
                    from sqlalchemy import text as _t3
                    with SessionLocal() as _s3:
                        _hist = _s3.execute(_t3(
                            "SELECT trade_date, price FROM paper_trades "
                            "WHERE account_id=:a AND symbol=:s AND direction LIKE '买%' "
                            "AND COALESCE(reason,'') LIKE :p ORDER BY id"),
                            {"a": account_id, "s": symbol, "p": "%wolf_ambush_buy%"}).fetchall()
                except Exception:
                    _hist = []
                _why_add = ""
                if _hist:
                    _n_add = len(_hist)
                    _last_px = float(_hist[-1][1] or 0)
                    _last_d = str(_hist[-1][0])
                    _px_now = float(price or 0)
                    if _step > 0 and _last_px > 0 and _px_now > 0:
                        _need = _last_px * (1 - (_n_add * _step) / 100.0)
                        if _px_now > _need:
                            _why_add = ("埋伏加仓·点位未到：现价 %.3f > 第%d加法点 %.3f"
                                        "（上次买入 %.3f，档距 %.1f%%×%d）"
                                        % (_px_now, _n_add + 1, _need, _last_px, _step, _n_add))
                    if not _why_add and _mind > 0:
                        try:
                            from sqlalchemy import text as _t4
                            with SessionLocal() as _s4:
                                _avg = float((_s4.execute(_t4(
                                    "SELECT COALESCE(AVG(price),0) FROM paper_trades WHERE account_id=:a "
                                    "AND symbol=:s AND direction LIKE '买%' AND COALESCE(reason,'') LIKE :p"),
                                    {"a": account_id, "s": symbol, "p": "%wolf_ambush_buy%"}).scalar()) or 0)
                        except Exception:
                            _avg = 0.0
                        if _avg > 0 and _px_now > _avg * (1 - _mind / 100.0):
                            _why_add = ("埋伏加仓·幅度不够：现价 %.3f > 持仓均价 %.3f×(1−%.1f%%)"
                                        % (_px_now, _avg, _mind))
                    if not _why_add and _gap > 0 and _last_d:
                        try:
                            _dd = (datetime.now() - datetime.strptime(_last_d[:10], "%Y-%m-%d")).days
                        except Exception:
                            _dd = 999
                        if _dd < _gap:
                            _why_add = ("埋伏加仓·间距不够：距上次买入仅 %d 天 < %.0f 天"
                                        % (_dd, _gap))
                if _why_add:
                    print("[gateway] %s %s" % (symbol, _why_add), flush=True)
                    if trigger_id:
                        t_db.update_trigger_status(int(trigger_id), "blocked", reason=_why_add[:250])
                    return {"status": "blocked", "reason": _why_add, "level": "AMBUSH_ADD_RULE"}

    # ── 0.2e7) **埋伏仓豁免常规卖腿**（`WOLF_AMBUSH_SELL_EXEMPT`，**库内默认 0 = 关** ✓）────
    #   用户 2026-09-26（藏格矿业 02-02 被 `custom_support_sell` 破位清掉 ✗）：
    #   「为什么第二天还卖出了？」⇒ 埋伏仓的目标是「**50 交易日 + 宽止损 −20%**」✓
    #   ⇒ **破位/黄线类常规卖腿**不适用（否则 50 日纪律形同虚设 ✗）；
    #   止盈类（high_sell）与宽止损仍保留 ✓
    if (side == "sell" and str(os.getenv("WOLF_AMBUSH_SELL_EXEMPT", "0")).strip().lower() in ("1", "true", "yes", "on")):
        try:
            _k_se = _turnover_kind(trigger_id, condition_id) if trigger_id else ""
        except Exception:
            _k_se = ""
        # 豁免名单**可配置**（`WOLF_AMBUSH_SELL_EXEMPT_KINDS`，逗号分隔 ✓；
        #   库内默认 = 破位/黄线两类 ⇒ **逐字保持现状** ✓）
        #   2026-09-26 用户拍板「①④ 两条」✓：**高抛/止盈**（`high_sell`／`wolf_fib_target_sell`）
        #     与**防御性减仓**（`wolf_defensive_t_reduce`）也纳入 ⇒ pins 里列出来 ✓
        #   （注：日志里的 `[DOWN] 卖出委托` 只是**下单打印前缀** ✗，不是规则族 ✓）
        _ex_kinds = {x.strip() for x in str(
            os.getenv("WOLF_AMBUSH_SELL_EXEMPT_KINDS", "custom_support_sell,custom_vwap_sell")
        ).split(",") if x.strip()}
        # 兜底（账本 §9.198）：取不到腿型但理由是「确认制T出」⇒ 按 high_sell（高抛/止盈族）判定 ✓
        #   病灶：T出结算走网关但历史上不传 trigger_id ⇒ 腿型为空 ⇒ 豁免名单匹配不到 ⇒ 放行 ✗
        if not _k_se and str(reason or "").startswith("确认制T出"):
            _k_se = "high_sell"
        if str(_k_se) in _ex_kinds:
            try:
                # --- ledger 9.271: promoted positions must NOT be exempted (back to normal mgmt) ---
                _skip_ex = False
                if str(os.getenv('WOLF_PROMOTED_SKIP_EXEMPT', '0')).strip().lower() in ('1', 'true', 'yes', 'on'):
                    try:
                        import json as _jsE
                        _rootE = os.path.dirname(os.environ.get('DATA_DIR', '/app/data')) or '/app/data'
                        _pfE = os.path.join(_rootE, 'ambush_promoted.json')
                        if os.path.exists(_pfE):
                            with open(_pfE, encoding='utf-8') as _fE:
                                if str(symbol).upper() in {str(k).upper() for k in (_jsE.load(_fE) or {})}:
                                    _skip_ex = True
                    except Exception:
                        _skip_ex = False
                if _skip_ex:
                    print('[gateway] %s promoted -> exemption skipped (normal management)' % symbol, flush=True)
                _is_amb = (not _skip_ex) and _ambush_position(account_id, symbol)
            except Exception:
                _is_amb = False
            if _is_amb:
                _why_se = ("埋伏仓豁免常规卖腿：本仓由埋伏腿建立 ⇒ 只走「50 交易日 + 宽止损 −20%」"
                           "（破位/黄线类不适用；WOLF_AMBUSH_SELL_EXEMPT=1）")
                print("[gateway] %s %s" % (symbol, _why_se[:110]), flush=True)
                if trigger_id:
                    t_db.update_trigger_status(int(trigger_id), "blocked", reason=_why_se[:250])
                return {"status": "blocked", "reason": _why_se, "level": "AMBUSH_SELL_EXEMPT"}

    # 0.2f) **L3 v1：建仓腿下限**（`WOLF_BUILD_SIZE_FLOOR`，库内默认 0=关）
    #   把 `WOLF_BUILD_CAP_ANCHOR`（缺口/步长）从"上限"变成"**下限**"：AI 建议量小于锚时抬到锚。
    #   只抬不压；取不到权益/现金 ⇒ 原样（fail-open）。关 ⇒ 逐字旧行为。
    if side == "buy":
        try:
            _v_floor, _why_floor = build_size_floor(symbol, float(price or 0), int(volume or 0),
                                                   trigger_id=trigger_id, condition_id=condition_id,
                                                   account_id=account_id)
            if _v_floor != int(volume or 0):
                print("[gateway] %s" % str(_why_floor)[:160], flush=True)
            volume = int(_v_floor)
        except Exception as _fe:
            print("[gateway] L3 建仓下限异常(忽略): %s" % str(_fe)[:70], flush=True)

    # 0.5) 每日决策对象准入（2026-09-12）：**只拦买入**，卖出/止损永不拦
    if side == "buy" and not is_stop_loss:
        _blk = _decision_gate(symbol, trigger_id=trigger_id, account_id=account_id, reason=reason)
        if _blk:
            if trigger_id:
                t_db.update_trigger_status(trigger_id, "blocked", reason=_blk)
            return {"status": "rejected", "reason": _blk, "level": "DECISION"}

    # 0.5b) 腿批准闸 · 执行口二次把关（2026-09-19）：**只拦买入**，堵"建议买价/条件单/agent
    #       直接下单"绕过布腿闸的路径（实测 SH603203 就是这么进来的）。
    if side == "buy" and not is_stop_loss:
        _blk_lg = _leg_gate_exec(symbol, account_id=account_id, reason=reason)
        if _blk_lg:
            if trigger_id:
                t_db.update_trigger_status(trigger_id, "blocked", reason=_blk_lg)
            return {"status": "rejected", "reason": _blk_lg, "level": "LEG_GATE"}

    # 0.5c) 卖侧「日内做T仓 20%」预算（2026-09-19 用户拍板 B）：**只拦卖出**，止损/破位豁免。
    #   语料 2026-09-03「仓位不会低于65%收盘，日内做T仓位20%」；此前该 20% 只作用于买腿，
    #   卖腿不受约束 ⇒ data/_bt_jan5 实测 14 笔卖出 13 笔止盈高抛、把底仓卖光（仓位长期 10~30%）。
    if side == "sell" and not is_stop_loss:
        try:
            from app.services import t_capacity as _cap
            if _cap.enabled() and getattr(_cap, "SELL_SIDE", False):
                _eq = _cap.account_equity(account_id)
                _sold = _cap.sold_today_notional(account_id)
                _nv, _nwhy = _cap.clamp_sell_volume(int(volume or 0), float(price or 0), _eq,
                                                    trigger_kind=str(_turnover_kind(trigger_id, condition_id) or ""),
                                                    sold_today=_sold, reason=reason)
                if _nv <= 0:
                    print("[gateway] 卖侧预算拦截 %s %d 股：%s" % (symbol, int(volume or 0), _nwhy), flush=True)
                    if trigger_id:
                        t_db.update_trigger_status(trigger_id, "blocked", reason=_nwhy)
                    return {"status": "rejected", "reason": _nwhy, "level": "CAPACITY_T"}
                if _nv < int(volume or 0):
                    print("[gateway] 卖侧按日内T预算收敛 %s %d→%d 股：%s"
                          % (symbol, int(volume or 0), _nv, _nwhy), flush=True)
                    volume = _nv
        except Exception as _se:
            print("[gateway] 卖侧预算收敛失败(放行) %s: %s" % (symbol, str(_se)[:70]), flush=True)

    # 0.5d) **止盈类卖腿只卖 T 仓**（2026-09-21 用户拍板方案③；模块 wolf_base_hold）
    #   语料 2026-08-05「做T套利的仓位是做T套利的 底仓是底仓」/「分不清做T仓位和底仓的区别就是『贪』」。
    #   实测（drabj13 0105→0428）：高抛/斐波 103 笔、单笔卖中位=持仓 50%、25% 卖光 ⇒ 平均仓位只有 15.2%，
    #   底仓目标 65% 从未达到；只读叠加显示"底仓不动"能把仓位提到 41.8%（缺口÷3）/51.7%（缺口÷2）。
    #   只拦**止盈类**（高抛/fib/profit_take/止盈）；破位/减仓/止损类不受影响（他确实会减底仓）。
    if side == "sell" and not is_stop_loss:
        try:
            from app.services import wolf_base_hold as _bh
            if _bh.enabled() and _bh.is_take_reason(reason):
                # 2026-09-22：**止盈类也允许穿透底仓**时（`WOLF_BASE_EXEMPT_TAKE=1`，默认关），
                #   方案③ 不再把它压回"近 3 个交易日买入份额" —— 否则 t_monitor 那边刚按
                #   `_stop_exit_volume`（吃一口减一半）放出来的量，会在这里被再次压成 0（开了也白开）。
                #   开关关 ⇒ 逐字旧行为（方案③ 照旧生效）。
                _pen_take = False
                try:
                    from app.services import t_capacity as _tcap
                    _pen_take = bool(_tcap.base_penetrate_allowed("", str(reason or ""))) \
                        and bool(_tcap.take_penetrate_quota_ok(account_id, symbol))
                except Exception:
                    _pen_take = False
                if _pen_take:
                    print("[gateway] 止盈腿穿透底仓→跳过方案③ T仓上限 %s %d 股（本轮已用 %d 次，上限 %s）"
                          % (symbol, int(volume or 0), _tcap.take_penetrate_used(account_id, symbol),
                             os.getenv("WOLF_BASE_EXEMPT_TAKE_MAX", "2")), flush=True)
                _slv = _bh.sleeve_from_db(account_id, symbol)
                _bv, _bwhy = (int(volume or 0), "止盈腿穿透底仓（WOLF_BASE_EXEMPT_TAKE=1）") if _pen_take \
                    else _bh.cap_volume(int(volume or 0), _slv)
                if _bv <= 0:
                    print("[gateway] 底仓不动拦截 %s %d 股：%s" % (symbol, int(volume or 0), _bwhy), flush=True)
                    if trigger_id:
                        t_db.update_trigger_status(trigger_id, "blocked", reason=_bwhy)
                    return {"status": "rejected", "reason": _bwhy, "level": "BASE_HOLD"}
                if _bv < int(volume or 0):
                    print("[gateway] 底仓不动收敛 %s %d→%d 股：%s"
                          % (symbol, int(volume or 0), _bv, _bwhy), flush=True)
                    volume = _bv
        except Exception as _be:
            print("[gateway] 底仓不动收敛失败(放行) %s: %s" % (symbol, str(_be)[:70]), flush=True)

    # ── 0.5e) **压力位/加速类 ⇒ 减半全仓** ＋ **同轮累计 ≤ 50%**（账本 §9.306 ✓ 用户 ✓）─────
    #   他的原话 ✓：「**吃一口减一半**」（2026-09-01 楼678）／「**突破减半 剩下的吃溢价**」（2025-12-24）
    #            「**分档减：先减到半仓以下**」✓
    #   ⚠️ 这里的量**不是触发带来的** ✗（触发不带量 ⇒ 由 AI 建议量 ＋ 上面的收敛决定 ✓），
    #      所以必须在**这个位置**改 ✓（实测 `[gateway] …T仓上限 100 股` 就出在这里 ✓）
    if side == "sell" and not is_stop_loss and str(os.getenv("WOLF_BASE_HALF_CAP", "0")).strip().lower()             in ("1", "true", "yes", "on"):
        try:
            _HALF_KINDS = {"wolf_fib_target_sell", "wolf_boll_upper_sell",
                           "wolf_board_half_sell", "wolf_confirm_sell"}
            _kw = str(_turnover_kind(trigger_id, condition_id) or "")
            _rw = str(reason or "")
            _pos = 0
            try:
                from sqlalchemy import text as _th2
                from app.database import SessionLocal as _SH2
                with _SH2() as _sh2:
                    _r2 = _sh2.execute(_th2(
                        "SELECT COALESCE(volume,0) FROM paper_positions "
                        "WHERE account_id=:a AND symbol=:s"), {"a": account_id, "s": symbol}).fetchone()
                    _pos = int((_r2[0] if _r2 else 0) or 0)
            except Exception:
                _pos = 0
            if _pos > 0:
                # ① **压力位/加速类 ⇒ 至少减半**（他：「吃一口减一半」✓ ⇒ **不是"各 100 股"** ✗）
                if _kw in _HALF_KINDS:
                    _half = int(max(_pos // 2, 100) // 100 * 100)
                    if int(volume or 0) < _half:
                        print("[gateway] %s %s => 按他【减半】口径放大卖量 %d->%d 股（持仓 %d OK）"
                              % (symbol, _kw, int(volume or 0), _half, _pos), flush=True)
                        volume = _half
                # ② **同轮累计 ≤ 50%**（他：「突破减半 **剩下的吃溢价**」✓ ⇒ 不允许再啃 ✗）
                # ⚠️ 2026-09-30（账本 §9.367 ✓ 用户「豁免」✓）：**按腿型豁免** ✓
                #   原豁免只认 reason 关键词 ✗ ⇒ `high_sell`（理由写"高抛卖腿" ✗）不豁免
                #   ⇒ **跌停日的止损腿被"吃溢价"逻辑拦住** ✗（SZ002792 01-14 实证 ✓）
                #   他的口径 ✓：「破位收盘确认才走」／「止损只在指数大级别破位」
                #   ⇒ 个股跌停＝破位 ⇒ 该走 ⇒ 这里按**腿型**放行 ✓
                _CAP_EXEMPT_KINDS = ("custom_vwap_sell", "custom_support_sell", "custom_level_sell",
                                     "custom_trail_sell", "wolf_defensive_t_reduce", "wolf_confirm_sell",
                                     "wolf_boll_upper_sell", "wolf_board_half_sell",
                                     "wolf_passive_stop_sell", "stop_loss", "wolf_early_swing_sell",
                                     "high_sell")
                _cap_exempt_kind = (str(os.getenv("WOLF_SELL_EXEMPT_REDUCE", "0")).strip().lower()
                                    in ("1", "true", "yes", "on")
                                    and str(_kw) in _CAP_EXEMPT_KINDS)
                if (not _cap_exempt_kind) and not any(
                        x in _rw for x in ("清仓", "加速结束", "避险", "止损", "破位")):
                    _sold = 0
                    try:
                        with _SH2() as _sh3:
                            _r3 = _sh3.execute(_th2(
                                "SELECT COALESCE(SUM(volume),0) FROM paper_trades "
                                "WHERE account_id=:a AND symbol=:s AND direction LIKE '卖%' "
                                "AND COALESCE(voided,0)=0 AND trade_date >= ("
                                "  SELECT MIN(trade_date) FROM paper_trades WHERE account_id=:a "
                                "  AND symbol=:s AND direction LIKE '买%' AND COALESCE(voided,0)=0)"),
                                {"a": account_id, "s": symbol}).fetchone()
                            _sold = int((_r3[0] if _r3 else 0) or 0)
                    except Exception:
                        _sold = 0
                    _room = int(max(_pos // 2 - _sold, 0) // 100 * 100)
                    if _room <= 0:
                        print("[gateway] %s 同轮累计已减 %d 股 >= 持仓一半（%d）=> 拦住（他：突破减半，剩下的吃溢价）"
                              % (symbol, _sold, _pos), flush=True)
                        if trigger_id:
                            t_db.update_trigger_status(trigger_id, "blocked",
                                                       reason="同轮累计已达持仓一半（吃溢价保留 ✓）")
                        return {"status": "blocked", "level": "BASE_HALF_CAP",
                                "reason": "同轮累计已减 %d ≥ 持仓一半 ⇒ 剩下的按他原话吃溢价 ✓" % _sold}
                    if int(volume or 0) > _room:
                        print("[gateway] %s 同轮累计收敛 %d->%d 股（已减 %d / 持仓 %d）"
                              % (symbol, int(volume or 0), _room, _sold, _pos), flush=True)
                        volume = _room
        except Exception as _he:
            print("[gateway] 减半/累计闸异常(放行): %s" % str(_he)[:70], flush=True)

    # 0.6b) **建仓至少 2 手**（`WOLF_BUILD_MIN_LOTS`，库内默认 0；用户 2026-09-22 拍板"修 2b"）
    #   为什么：底仓 floor 的口径是「持仓 < 2 手 ⇒ 整仓即底仓」（`t_base_floor._rebase_floor`：
    #   `if sellable < 2*min_lot: return sellable`）⇒ **一手仓的 T 仓恒为 0** ⇒ 高抛/黄线/斐波/倒T
    #   这些止盈类卖腿在这类持仓上**永久失效**，只能等破位腿穿透底仓。
    #   实测代价（T5/drabt5）：一手仓 9 笔（占买入 21%）；145 条卖腿被拦时持仓正好 100 股；
    #   兆易创新 603986 0317 买 100 股 @307 → 0318 三年卖腿全被拦 → 0323 割在 277.55（−2,987）。
    #   口径：买后持仓必须 ≥ 2 手，否则**把这笔补到 2 手**；补完越限由下面三道校验拒单
    #   ⇒ 实际效果"要么 2 手、要么不买"。模块与完整依据见 `wolf_build_lots.py`。
    if str(side).lower() in ("buy", "买入"):
        try:
            from app.services import wolf_build_lots as _bl
            # ── **埋伏腿豁免"补到 2 手"**（账本 §9.218；`WOLF_AMBUSH_SKIP_MIN_LOTS`，库内默认 0 = 关 ✓）──
            #   用户 2026-09-26「豁免」✓。病灶：本规则把**埋伏小仓档**从 100 股**放大到 200 股** ✗
            #     （实测 5 笔：`SZ000408 400→100` 之后 `100→200` ⇒ 实际仓位 ≈ **8%**，小仓档被架空 ✗）
            #   规则的**原意**是「一手仓的 T 仓恒为 0 ⇒ 止盈腿永久失效」⇒ **为做 T 服务** ✓
            #   而**埋伏腿不做 T** ✓、要**长持 50 日** ✓ ⇒ 该理由**不适用** ✗ ⇒ 豁免 ✓
            _skip_min = str(os.getenv("WOLF_AMBUSH_SKIP_MIN_LOTS", "0")).strip().lower() in ("1", "true", "yes", "on")
            if _skip_min:
                try:
                    _k_bl = _turnover_kind(trigger_id, condition_id) if trigger_id else ""
                except Exception:
                    _k_bl = ""
                if str(_k_bl) == "wolf_ambush_buy":
                    print("[gateway] 埋伏腿豁免「补到 2 手」：%s 保持 %d 股（小仓 %s%% ✓）"
                          % (symbol, int(volume or 0), os.getenv("WOLF_AMBUSH_SIZE_PCT", "?")), flush=True)
                    _bl = None
            if _bl is not None and _bl.enabled():
                _led0 = get_sellable_ledger(account_id) or {}
                _it0 = ledger_item(_led0, symbol) or {}
                _nv, _nwhy = _bl.topup_volume(int(_it0.get("volume") or 0), int(volume or 0))
                if _nv != int(volume or 0):
                    print("[gateway] %s %s" % (symbol, _nwhy), flush=True)
                    volume = _nv
        except Exception as _ble:
            print("[gateway] 建仓最小手数判定异常(不抬高，放行): %s" % str(_ble)[:70], flush=True)

    # 0.65) **同价重复兑现互斥**（`WOLF_SELL_DEDUP`，库内默认 0；2026-09-22 用户拍板 "B′-1"）
    #   问题：三条不同模板的**止盈腿**碰巧同分钟同价各自命中 ⇒ 一次兑现被执行三次。
    #   实测（T6 2026-01-07 09:35 SZ002156）：高抛 500 + 斐波 600 + 防守减仓 300 = 1,400 股（持仓 1,800），
    #   两天清仓；而该票 0119 到 46.86、0120 到 51.01（对照 T5：同期只卖 700 股，该票已实现 +11,003 vs +2,427）。
    #   口径：止盈类在「同标的 + N 分钟内 + 同价 ±0.5% 已成交过」⇒ 本次不执行（先到者成交）。
    #   只作用止盈类；止损/破位/风控与防守减仓不在此列（各自另有 G6 笔数上限与时点门）。
    if str(side).lower() in ("sell", "卖出"):
        try:
            from app.services import t_sell_dedup as _sd
            if _sd.enabled():
                _okd, _whyd = _sd.check(account_id, symbol, float(price or 0),
                                        trigger_kind=_turnover_kind(trigger_id, condition_id),
                                        reason=reason)
                if not _okd:
                    if trigger_id:
                        t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whyd)[:250])
                    return {"status": "blocked", "reason": _whyd, "level": "SELL_DEDUP"}
        except Exception as _sde:
            print("[gateway] 同价去重闸异常(放行): %s" % str(_sde)[:80], flush=True)

    # 0.66) ★ 账本 §9.702 ✓（用户 2026-10-07 回测复盘）：**建仓类买腿的同价重复互斥**
    #   实测 ✗：汇成股份 SH688403 2026-01-21 **14:00 与 14:05 各买 23.05×2400**（同价同量 ✗）
    #     —— G6「一票最多买 2 笔」按**笔数**算 ⇒ 恰好 2 笔**在上限内**，放行 ✗；
    #     而 t_sell_dedup 只管卖出 ✗ ⇒ 没人管"同一分钟同价重复建仓" ✓
    #   口径（与卖出侧同款 ✓）：建仓类腿在「同标的 + WOLF_BUY_DEDUP_MIN 分钟内 + 同价 ±0.5% 已成交过」
    #     ⇒ 本次不执行（先到者成交 ✓）；**转正加仓/做T回补不受约束** ✓（那本来就是合法的第二笔 ✓）
    #   开关：WOLF_BUY_DEDUP（**库内默认 0** ⇒ 生产零影响 ✓；回测由 pins 置 1 ✓）
    if str(side).lower() in ("buy", "买入"):
        try:
            from app.services import t_buy_dedup as _bd
            if _bd.enabled():
                _okb, _whyb = _bd.check(account_id, symbol, float(price or 0),
                                        trigger_kind=_turnover_kind(trigger_id, condition_id),
                                        reason=reason)
                if not _okb:
                    if trigger_id:
                        t_db.update_trigger_status(trigger_id, "blocked", reason=str(_whyb)[:250])
                    return {"status": "blocked", "reason": _whyb, "level": "BUY_DEDUP"}
        except Exception as _bde:
            print("[gateway] 建仓同价去重闸异常(放行): %s" % str(_bde)[:80], flush=True)

    # 0.7) G6（2026-09-14）**一个票最多买 2 笔、卖 2 笔**（狼大 2025-02-07「一个票最多买2笔 卖2笔
    #      后面如果是主升浪的话越动收益越低」）—— 每日每标的成交笔数上限，落在**唯一下单入口**：
    #      · 覆盖所有买入路径（低吸/回补/加仓/ETF 调仓…）；
    #      · 卖出侧**豁免**止损类（is_stop_loss）与含"止损/破位"字样的保护性卖出；
    #      · 上限 WOLF_MAX_TRADES_PER_SYMBOL_PER_DAY（默认 2；0=关闭）。
    _cap = _trade_cap()
    if _cap > 0:
        _done = _count_today_trades(account_id, symbol, side)
        if not trade_cap_ok(_done, _cap, is_stop_loss=is_stop_loss, reason=reason, side=side,
                            trigger_kind=_turnover_kind(trigger_id, condition_id)):
            msg = ("[G6] 当日 %s %s 已 %d 笔 ≥ 上限 %d（狼大 2025-02-07「一个票最多买 2 笔 卖 2 笔」）"
                   % (symbol, side, _done, _cap))
            if trigger_id:
                t_db.update_trigger_status(trigger_id, "blocked", reason=msg)
            return {"status": "rejected", "reason": msg, "level": "ledger"}

    # 1) 校验
    # ── **整百股**（A 股**买入**必须是 100 的整数倍；用户 2026-09-26「必须是整百股」✓）──
    #   实测碎股：`SZ002792 223 股`／`SH603660 891 股`（均由"埋伏小仓档"的 `int()` 截断产生 ✗）
    #   放在 `validate_order` **之前** ⇒ 任何路径（小仓档／各类 clamp／额度／比例 ✓）都漏不出去 ✓
    #   开关 `WOLF_BUY_LOT_ROUND`（**库内默认 1＝开** ✓ —— 这是**交易所硬规则**，不是策略旋钮；
    #   生产经券商本就整百 ⇒ 本行对生产**无行为变化** ✓）
    if side == "buy" and int(volume or 0) > 0:
        if str(os.getenv("WOLF_BUY_LOT_ROUND", "1")).strip().lower() not in ("0", "false", "no", "off"):
            _v_lot = (int(volume) // 100) * 100
            if _v_lot != int(volume):
                print("[gateway] 整百股取整：%s %d→%d 股（WOLF_BUY_LOT_ROUND=1）"
                      % (symbol, int(volume), _v_lot), flush=True)
                volume = _v_lot
            if int(volume) <= 0:
                _why_lot = "整百股取整后不足 100 股 ⇒ 不足以成交一手（WOLF_BUY_LOT_ROUND=1）"
                print("[gateway] %s %s" % (symbol, _why_lot), flush=True)
                if trigger_id:
                    t_db.update_trigger_status(int(trigger_id), "blocked", reason=_why_lot[:250])
                return {"status": "blocked", "reason": _why_lot, "level": "BUY_LOT_ROUND"}

    # ── **埋伏仓卖出的"总闸"**（账本 §9.215；`WOLF_AMBUSH_SELL_EXEMPT`，库内默认 0 = 关 ✓）──────
    #   病灶（用户「这个卖出对吗」✓）：埋伏仓被 **AI 决策路径**卖掉 ✗
    #     · 0212 那笔理由原文：「…**不满足任何 wait/abandon 客观证据，按默认执行卖腿**」✗
    #       ⇒ **AI 路径不带腿型** ⇒ 以腿型为键的豁免名单**拦不到** ✗
    #     · 这是**同一类漏洞的第三次** ✗（前两次：条件腿自动执行漏 trigger_id ✓、T出结算漏传 trigger_id ✓）
    #   ⇒ 这次做**总闸**：埋伏仓的卖出，**只放行**两件事 ✓
    #       ①「**埋伏纪律**」离场（固定 50 交易日 / 宽止损 −20% ✓）
    #       ②「**预警降档**」（指数层预警 ⇒ 主动降档 ✓，这是我们要的 ✓）
    #     其余（AI 卖腿 / 破位 / 高抛 / 黄线 / 趋势线 / G4 / 板块半仓 …）**一律拦下** ✗
    if (side == "sell" and str(os.getenv("WOLF_AMBUSH_SELL_EXEMPT", "0")).strip().lower() in ("1", "true", "yes", "on")):
        try:
            _rs = str(reason or "")
            # 放行白名单（2026-09-26 修 ✗：新增**风险/避险类** ✓ —— 语料「周末/事件前减半避险」✓
            #   实测：[G9 周末避险] 不带前两个字样 ⇒ 会被总闸**误拦** ✗ ⇒ 必须放行风险规则 ✓）
            _ALLOW_SUB = ("埋伏纪律", "预警降档", "避险", "风险")
            # **转正后不再豁免** ✓（＝交回普通管理 ✓；账本 §9.220）
            _promo_ok = True
            # ⚠️ 2026-09-26 修（账本 §9.245）：**豁免判断必须与"转正口径"一致** ✗
            #   病灶（实测 SZ002792 ✓）：监控已按「**碰新高**」判它**转正** ✓（记录 `pday 20260107` ✓），
            #     而这里**只看浮盈 ≥10%** ✗（它的 highest 48.20 < 49.13 ✗）
            #     ⇒ **网关仍把它当"未转正的埋伏仓"** ✗ ⇒ 所有常规卖腿被"埋伏仓豁免"拦住 ✓
            #     ⇒ 结果：它 **01-05 之后一笔都没成交** ✗（既卖不出、也追不上 ✓）
            #   ⇒ 修法：**直接读转正记录**（`ambush_promoted.json` ✓，全运行共享 ✓）＝单一事实来源 ✓
            # ★ 账本 §9.537 ✓（用户「这个现在是不是直接读臂内的 sqlite」✓）：**改读臂库** ✓
            #   为什么 ✓：原实现靠 `_rootP = dirname(DATA_DIR)` ✗ ⇒ **DATA_DIR 层级一变就找不到** ✗
            #     （`DATA_DIR=<臂根>` 时它去找 `<臂根的父目录>/ambush_promoted.json` ✗ ⇒ 命中不到 ✗
            #      ⇒ 转正仓仍被当"埋伏仓"豁免 ✗ —— 实测智光电气 0309 已转正 ✓ 但 0310 仍被拦 46 次 ✗）
            #   ⇒ 臂库 `pos_meta.promoted` 由 `arm_db.db_path()` **统一解析路径** ✓ ⇒ 不可能读错 ✓
            #   开关 ✓：`WOLF_PROMO_FROM_ARMDB`（**默认 1** ✓，置 0 ＝ 只用旧的 JSON 路径 ✓）
            try:
                if str(os.getenv("WOLF_PROMO_FROM_ARMDB", "1")).strip().lower() in ("1", "true", "yes", "on"):
                    import sys as _sysA
                    _jobsA = os.path.join(_armdb_root(os.path.abspath(__file__)), "jobs")
                    if _jobsA not in _sysA.path:
                        _sysA.path.insert(0, _jobsA)
                    import arm_db as _adbA
                    _cA = _adbA.connect(account_id)
                    # ★ 账本 §9.614：列名修正 ✗ —— `arm_db.SCHEMA` 里是 **`account`** ✓，原写 `account_id` ✗
                    #   ⇒ 该查询**永远抛** `no such column: account_id` ✗ ⇒ **永远走兜底** ✗
                    _rA = _cA.execute("SELECT promoted FROM pos_meta WHERE account=? AND symbol=?",
                                      (account_id, symbol)).fetchone()
                    _cA.close()
                    if _rA and int(_rA[0] or 0) == 1:
                        _promo_ok = False        # 臂库记录已转正 ⇒ 不再豁免 ✓
                        print("[gateway] %s 已转正（臂库 ✓ pos_meta.promoted=1）⇒ 交回普通管理 ✓" % symbol,
                              flush=True)
                        raise StopIteration      # 跳到下一段（不再看旧 JSON ✓）
            except StopIteration:
                # ★ §9.631 ✓：`raise StopIteration` 是**故意控制流** ✗ ⇒ 不能当异常留痕
                #   （原来它 `note_silent` 了自己 ⇒ 每命中一次刷一条假告警 ✗）
                print("[gateway] 转正命中 ⇒ 走控制流跳转（非异常 ✓）", flush=True)
            except Exception as _eA:
                # ★ 真失败（查询挂了 ⇒ 走旧 JSON 兜底 ✓）⇒ **落盘留痕** ✓（只落盘、不推 QQ ✓）
                print("[gateway] 臂库转正查询失败（继续用旧路径 ✓）: %s" % str(_eA)[:70], flush=True)
                try:
                    from app.services import alert_hub as _ah_g2
                    _ah_g2.note_silent("t_gateway.臂库转正", _eA)
                except Exception as _e_g2:
                    print("[gateway] 臂库转正留痕失败: %s" % str(_e_g2)[:60], flush=True)
            try:
                import json as _jsP
                _rootP = os.path.dirname(os.environ.get("DATA_DIR", "/app/data")) or "/app/data"
                _pfP = os.path.join(_rootP, "ambush_promoted.json")
                if os.path.exists(_pfP):
                    with open(_pfP, encoding="utf-8") as _fP:
                        _recP = _jsP.load(_fP) or {}
                    # ⚠️ 大小写不敏感 ✓（账本 §9.250）：记录键是大写 `SZ002792` ✓，
                    #   而网关收到的 `symbol` 可能是小写 ✗ ⇒ 精确匹配会**漏判** ✗
                    #   （实测 01-12 命中 1 次 ✓、01-13 一次都没命中 ✗ ⇒ 仍豁免 71 次 ✗）
                    _keysP = {str(k).upper() for k in _recP.keys()}
                    if str(symbol).upper() in _keysP:
                            _promo_ok = False        # 已转正 ⇒ **不再豁免** ✓（交回普通管理 ✓）
                            print("[gateway] %s 已转正（记录 ✓）⇒ 不再享受埋伏豁免，交回普通管理 ✓" % symbol,
                                  flush=True)
            except Exception as _e_sil12:
                try:
                    from app.services import alert_hub as _ah_sil
                    _ah_sil.note_silent("t_gateway.py:2822", _e_sil12)
                except Exception:
                    print("[silent:t_gateway.py:2822] %s: %s" % (type(_e_sil12).__name__, str(_e_sil12)[:110]), flush=True)
            try:
                _pp = float(os.getenv("WOLF_AMBUSH_PROMOTE_PCT", "0") or 0)
                if _pp > 0:
                    from sqlalchemy import text as _t7
                    with SessionLocal() as _s7:
                        _rr = _s7.execute(_t7(
                            "SELECT COALESCE(highest_price,0), COALESCE(avg_price,0) FROM paper_positions "
                            "WHERE account_id=:a AND symbol=:s AND COALESCE(volume,0)>0"),
                            {"a": account_id, "s": symbol}).fetchone()
                    if _rr and float(_rr[1] or 0) > 0:
                        _promo_ok = float(_rr[0] or 0) < float(_rr[1]) * (1 + _pp / 100.0)
            except Exception:
                _promo_ok = True
            if not any(_k in _rs for _k in _ALLOW_SUB):
                if _promo_ok and _ambush_position(account_id, symbol):
                    # ⚠️ 2026-09-30（账本 §9.328）：**字面 % 必须转义** ✗
                    #   原字符串含「−20%」⇒ 又被 `%` 格式化 ⇒
                    #   `unsupported format character '?' (0xff09)` ✗ ⇒ **该闸一直"异常⇒按不拦处理"** ✗
                    #   ⇒ ⇒ 等于**长期未生效** ✓（本次修 ✓）
                    _why_m = ("埋伏仓卖出总闸：本仓由埋伏腿建立 ⇒ 只放行「埋伏纪律（50 日/宽止损 −20%%）」"
                              "「预警降档」与**风险/避险类**；本笔理由=%s（WOLF_AMBUSH_SELL_EXEMPT=1）"
                              % _rs[:40])
                    print("[gateway] %s %s" % (symbol, _why_m[:150]), flush=True)
                    if trigger_id:
                        t_db.update_trigger_status(int(trigger_id), "blocked", reason=_why_m[:250])
                    return {"status": "blocked", "reason": _why_m, "level": "AMBUSH_SELL_MASTER"}
        except Exception as _eM2:
            print("[gateway] 埋伏仓卖出总闸异常(按不拦处理): %s" % str(_eM2)[:80], flush=True)

    check = validate_order(symbol, side, price, volume,
                           condition_id=condition_id, trigger_id=trigger_id,
                           reason=reason, decision_source=decision_source,
                           is_stop_loss=is_stop_loss,
                           account_id=account_id)
    if not check["pass"]:
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "blocked", reason=check["reason"])
        return {"status": "rejected", "reason": check["reason"], "level": check.get("level")}

    # 1b) 卖腿「底仓 floor」执行（`WOLF_SELL_FLOOR_ENFORCE`，库内默认 0 ⇒ 逐字零变化）──────────
    #   校验层只断言了"裸空"（volume ≤ 可卖），**不执行 floor** ⇒ AI 显式给量的止盈腿会把底仓
    #   一起卖掉（详见模块顶部该开关的病灶说明）。这里把**止盈类**卖量收敛到 T 仓空间，只收紧、不放大；
    #   ⚠️ 判据必须是**正向**的"是不是止盈腿"——用"豁免关键词"会被文案「未破止损」骗到（见 sell_floor_applies）。
    if str(side).lower() in ("sell", "卖出"):
        # ★ §9.718 ✓：趋势强 ⇒ **减仓/离场类也只动 T 仓**（"强的留" ✓）—— 只收紧、不放大 ✓
        _force_floor = False
        if _STRONG_T_ONLY and not is_stop_loss:
            _force_floor = strong_trend(symbol)
            if _force_floor:
                print("[gateway] %s 趋势强 ⇒ 卖腿只动 T 仓（§9.718 ✓，不碰底仓）" % symbol, flush=True)
        _capv, _capwhy = sell_floor_cap(account_id, symbol, volume,
                                        reason=reason, is_stop_loss=is_stop_loss, force=_force_floor)
        if _capv != int(volume or 0):
            if _capv <= 0:
                _m = "底仓floor：%s 止盈腿收敛为 0（%s）⇒ 拒绝（非止盈类不受此限）" % (symbol, _capwhy)
                if trigger_id:
                    t_db.update_trigger_status(trigger_id, "blocked", reason=_m)
                return {"status": "rejected", "reason": _m, "level": "ledger"}
            print("[gateway] %s 卖量 %d→%d：%s" % (symbol, int(volume or 0), _capv, _capwhy), flush=True)
            volume = _capv

    # 2) 执行器撮合（按账户）
    engine = PaperTradingEngine(data_dir=str(DATA_DIR), account_id=account_id)
    executor = MarcusVNPyExecutor(engine=engine, account_id=account_id)
    try:
        # ── 风控减仓腿豁免“趋势约束”（2026-09-18 回测发现；开关默认关）────────────────
        # 现象（drab_on 0129）：derisk_cut（避开高标）8 条卖腿里 **5 条被趋势约束否掉**
        #   （“MA5>MA20 趋势完好，浮亏未触发硬止损，禁止手动卖出”）⇒ 规则算出来了却减不掉仓。
        # 语料 2026-01-30「避开高标、控制仓位」、08-21「出于仓位安全考虑…这两天T进去的仓位出来一半」
        #   都是**风控动作**、不是“手痒卖票”，故应与止损/破位同样豁免（既有先例：
        #   stop_loss_monitor.py:1798、long_term_pool_monitor.py:354 都传 skip_trend_constraint=True）。
        # 默认关 ⇒ 生产逐位不变；回测驱动 setdefault 打开（WOLF_TREND_VETO_RISKOFF=1）。
        _risk_off_skip = False
        if str(os.getenv("WOLF_TREND_VETO_RISKOFF", "0")).strip().lower() in ("1", "true", "yes", "on"):
            _rr = str(reason or "")
            _risk_off_skip = any(k in _rr for k in ("derisk_cut", "风控减仓", "避开高标", "weekend_hedge", "避险减仓"))
        if side == "buy":
            result = executor.buy(symbol=symbol, price=price, volume=volume, reason=reason or "做T低吸")
        else:
            result = executor.sell(symbol=symbol, price=price, volume=volume, reason=reason or "做T高抛",
                                   skip_trend_constraint=_risk_off_skip)
    except Exception as e:
        if trigger_id:
            # ⚠️ 2026-09-18：此处 reason 直接写库，实测撞过 `value too long for type character varying(256)`
            #   （derisk_cut 的理由超长）⇒ **整笔卖出被记为执行异常、卖不出去**。截断到 250。
            t_db.update_trigger_status(trigger_id, "blocked", reason=("执行异常: %s" % e)[:250])
        return {"status": "rejected", "reason": f"执行异常: {e}"}

    # 3) 结果回写
    # 执行器成功态为 'executed'（MarcusVNPyExecutor.buy/sell）；gateway 统一对外
    # 返回稳定 status：成功=success / 失败=rejected（避免 **result 把 status
    # 覆盖成 executed/failed 导致上游误判——迭代#58e：成交但触发标 blocked）
    ok = result.get("status") in ("success", "filled", "executed")
    if ok:
        # L3 v1：**成交后累加"当日正T买已用额度"**（仅 wolf_zheng_t_buy；用于 WOLF_ZT_DAY_BUDGET）
        try:
            _k_zt = _turnover_kind(trigger_id, condition_id)
            _px_ok = float(result.get("price", price) or price)
            _amt_ok = _px_ok * float(volume or 0)
            _day_ok = datetime.now().strftime("%Y%m%d")
            if side == "buy" and zt_day_budget_pct() > 0 and str(_k_zt) == "wolf_zheng_t_buy":
                zt_add_used(account_id, _day_ok, _amt_ok)
            if side == "buy" and lowdip_day_budget_pct() > 0 and is_lowdip_kind(_k_zt):
                lowdip_add_used(account_id, _day_ok, _amt_ok)
            # 腿型比例：建仓腿名义落盘（仅比例开关打开时写，避免多余 I/O）
            if side == "buy" and _is_build_cap_kind(_k_zt):
                try:
                    from app.services import t_capacity as _tcr2
                    if _tcr2.leg_ratio() > 0:
                        _tcr2.ratio_state_add(_day_ok, _amt_ok)
                except Exception as _e_sil13:
                    try:
                        from app.services import alert_hub as _ah_sil
                        _ah_sil.note_silent("t_gateway.py:2928", _e_sil13)
                    except Exception:
                        print("[silent:t_gateway.py:2928] %s: %s" % (type(_e_sil13).__name__, str(_e_sil13)[:110]), flush=True)
        except Exception as _e_sil14:
            try:
                from app.services import alert_hub as _ah_sil
                _ah_sil.note_silent("t_gateway.py:2930", _e_sil14)
            except Exception:
                print("[silent:t_gateway.py:2930] %s: %s" % (type(_e_sil14).__name__, str(_e_sil14)[:110]), flush=True)
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "executed",
                                       executed_price=float(result.get("price", price) or price))
        # 更新日账本
        _update_daily_ledger(symbol, side, price, volume, account=account_id)
        # B模型·等量换手(2026-09-08 落地): stock账户低吸买入成交 → 登记换手额度
        if side == "buy" and account_id in ("stock",):
            try:
                from app.services import roundtrip_sell
                fill_price = float(result.get("price", price) or price)
                roundtrip_sell.record_buy(symbol, fill_price, volume, account=account_id)
            except Exception as _rte:
                print(f"[t-gate] roundtrip 登记失败: {_rte}")
        return {**result, "status": "success"}
    if trigger_id:
        t_db.update_trigger_status(trigger_id, "blocked", reason=result.get("reason") or "撮合失败")
    return {**result, "status": "rejected", "reason": result.get("reason") or "撮合失败"}


def _update_daily_ledger(symbol: str, side: str, price: float, volume: int, account: str = "t"):
    """更新该账户的日账本（累计回转额/买卖计数/当日已实现盈亏）。

    if str(side).lower() == "sell" and not is_stop_loss:
        _ok_t, _why_t = _sell_time_gate_ok(reason, _turnover_kind(trigger_id, condition_id))
        if not _ok_t:
            return {"status": "deferred", "reason": _why_t, "symbol": symbol, "volume": volume}

    ⚠️ 2026-09-11 修：原来**没有 account 维度**（t_db.get_daily_state 硬编码 't'）→
    stock/golden_pit 的成交会污染 t 的账本（生产实测）。
    realized_pnl 过去没人写（注释说"由引擎成交推送补全"但无人补）→ 现按 paper_trades 当日 profit 合计补写。
    """
    try:
        daily = t_db.get_daily_state(account=account) or {}
        amount = float(daily.get("daily_turnover_amount") or 0) + price * volume
        buy_count = int(daily.get("buy_count") or 0) + (1 if side == "buy" else 0)
        sell_count = int(daily.get("sell_count") or 0) + (1 if side == "sell" else 0)
        # 2026-09-11：**补上原来没人写的 realized_pnl**（以 paper_trades 当日 profit 合计为准）
        payload = {
            "daily_turnover_amount": round(amount, 2),
            "buy_count": buy_count,
            "sell_count": sell_count,
        }
        try:
            payload["realized_pnl"] = round(_realized_today(account), 2)
        except Exception as _pe:
            print(f"[t-gate] 日账本 realized_pnl 补写失败(忽略): {str(_pe)[:60]}")
        t_db.upsert_daily_state(payload, account=account)
    except Exception as e:
        print(f"[t-gate] 日账本更新失败: {e}")
