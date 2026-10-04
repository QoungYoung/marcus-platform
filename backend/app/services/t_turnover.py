# -*- coding: utf-8 -*-
"""换手治理（狼大语料，2026-09-18 用户"加上吧"）。

语料依据（docs/，全部带行号）：
  · wolf-playbook.md:148（2021-01-29）「股票只要有波动就能套利，所以为什么说**不要左右横跳**，
    因为横跳你是**新仓**，**没有套利空间**，只能祈求单边行情」
  · wolf-playbook.md:367-373「反对的是**没有套利空间/无逻辑的来回切**，不是"不许换票"」——他轮动市
    主动换股且有 **3% 阈值**；故本模块按"有无逻辑"分：同价位/短时间反向＝无逻辑（拦），价差够＝放行。
  · wolf-playbook.md:287「当日**只做上午 9:45–10:00、下午 14:00–14:30** 两个时间段，尽量避免开盘
    直接买卖和**平稳时段来回 T**（有消息刺激的个股例外）」
  · wolf-playbook.md:309-310「尽量不要**同一价位附近**…激情来回交易」「不要在板块快速轮动期间来回切换」
  · wolf-behavior-blueprint.md:231（震荡期）「**每天只挂 2 个单子**纯薅」；2025-02-07「一个票最多买 2 笔、
    卖 2 笔…越动收益越低」
  · wolf-behavior-blueprint.md:232「已止损出局、等回到卖出位置 → **下去不补，上来回到这个位置才补**」

四条闸（开关 WOLF_TURNOVER_GUARD，库内默认 0；回测驱动 setdefault=1）：
  ① same_price_churn：同标的在 N 分钟内、方向相反、价位差 <X% → 拦（"同价来回"）
  ② refill_price_gate：**买入**时若该标的最近一笔卖出价高于现价（即"跌下去了"）→ 拦（"下去不补"）
  ③ t_window_ok：非保护性（非止损/破位）的做T动作只允许 09:45–10:00 与 14:00–14:30
  ④ cooldown_days：同标的同方向 N 个交易日内只允许 1 次（跨日反复）
全部 fail-open（取数失败 → 放行），并在 stats() 暴露计数。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple


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


GUARD_ON = str(os.getenv("WOLF_TURNOVER_GUARD", "0")).strip().lower() in ("1", "true", "yes", "on")
CHURN_MINUTES = float(os.getenv("WOLF_TURNOVER_CHURN_MIN", "15"))
CHURN_PCT = float(os.getenv("WOLF_TURNOVER_CHURN_PCT", "0.5"))
T_WINDOWS = [w.strip() for w in os.getenv("WOLF_TURNOVER_WINDOWS", "09:45-10:00,14:00-14:30").split(",") if w.strip()]
COOLDOWN_DAYS = int(float(os.getenv("WOLF_TURNOVER_COOLDOWN_DAYS", "2")))
# ── ④ 跨日冷却是否按**真实交易日差**判（`WOLF_COOLDOWN_REAL_DAYS`，库内默认 0 = 旧行为逐字不变）──
#   2026-09-22 修 bug（T6 复盘时查出）：原实现**从未使用 COOLDOWN_DAYS 做比较**，只判
#   `最近同向成交日 < 今天` ⇒ 语义退化成「**同一票只要卖过一次，之后任何一天都不许再卖**」
#   （而文案写着"未满 2 个交易日" —— 与实现不符）。
#   实测后果（T6/drabt6 全窗）：止盈类卖腿 **676 条只成交 13 条**，其中「跨日冷却」拦掉 **130 条**；
#   被拦腿的价格比该票后来实际卖出的加权均价高 **+1.11%**、T+3 **+1.50%**、T+5 **+2.39%**
#   ⇒ 拦错方向（该卖没卖）。语料「一个票最多买 2 笔、**卖 2 笔**」也不支持"只能卖一次"。
#   开关开 = 按真实交易日差判（≥ COOLDOWN_DAYS 个交易日 ⇒ 放行）；日历取不到 ⇒ **保守拦**（旧行为）。
#   本开关**只放宽、不收紧**：任何旧行为放行的情形，开开关后依然放行。
_REAL_CD = str(os.getenv("WOLF_COOLDOWN_REAL_DAYS", "0")).strip().lower() in ("1", "true", "yes", "on")
# 放宽的作用方向：`sell`（默认，= 本次目标"让止盈腿能卖"）/ `both`（买侧同 bug 一并修）
_REAL_CD_SCOPE = str(os.getenv("WOLF_COOLDOWN_REAL_SCOPE", "sell")).strip().lower() or "sell"
_REFILL_GATE = str(os.getenv("WOLF_TURNOVER_REFILL_GATE", "1")).strip().lower() not in ("0", "false", "no")
# ── ③ 时段窗的**作用面白名单**（2026-09-19 用户拍板 A：按语料缩面）──────────────────────
# 语料（playbook:287）的适用范围他自己划了界：「这里说的买卖方法一般对应的**单日或者 3 日内**，
# 如果是**大级别的买入和卖出是另外一个方法**」；同一条语料的 A5 模块（wolf_trade_window）据此写明
# 「**只有** low_buy/custom_prevlow 受时间窗约束；custom_m5dump（253 急杀开小底仓）属**建仓/大级别**
#  不受；卖腿/止损也不受」。
# 而本闸原先的 ③ 对**所有非保护性委托**（买+卖）一律套时段窗 ⇒ 实测把建仓腿、防御性减仓
# （wolf_defensive_t_reduce）、目标止盈（wolf_fib_target_sell）也拦了（jan10 拦 298 条里 297 条是卖腿）。
# 现按语料缩到「做T类」白名单；不在名单里的腿型**不受**③约束（①同价来回/②下去不补/④跨日冷却 照旧全适用）。
TW_KINDS = [x.strip() for x in os.getenv(
    "WOLF_TURNOVER_TW_KINDS",
    "low_buy,custom_prevlow,wolf_zheng_t_buy,custom_vwap_sell,custom_support_sell,high_sell,wolf_dao_t_sell"
).split(",") if x.strip()]
# 腿型取不到（既无 trigger 也无 condition）时的口径：默认仍拦（保守，维持旧行为）；
# WOLF_TURNOVER_TW_UNKNOWN=allow ⇒ 放行（「非做T不设限」的更宽口径）。
TW_UNKNOWN_ALLOW = str(os.getenv("WOLF_TURNOVER_TW_UNKNOWN", "0")).strip().lower() in ("1", "true", "yes", "on")

# ── ③ 时段窗的**豁免**腿型（`WOLF_TURNOVER_TW_EXEMPT`，库内默认空 ⇒ 旧行为逐字不变）──────────
#   2026-09-25 用户拍板：「把 custom_prevlow 移出做T类白名单」。
#   依据 2026-09-19 已定的口径「**时段窗只对做T类腿型生效**（建仓/减仓/止盈不受限）」——
#   `custom_prevlow` 是**前低低吸/加仓**语义（不是做T回补），此前放进 TW_KINDS 属归属错误。
#   证据（docs/wolf-buy-parameter-ledger.md §9.93）：
#     · 兆易创新(603986) 1 月三条腿各被一闸拦死，其中 `custom_prevlow`（限价 250.48/250.05）
#       被时段窗拦（`做T时段窗：11:25/13:05 不在 09:45-10:00,14:00-14:30`）⇒ 强势票全天在窗口外、一笔未成；
#     · 同现象早前已记录：`t_bridge.py:87`「SH600183 0105 10:20 低吸腿被做T时段窗拦掉（一笔没成交）」；
#     · 规模：三臂 75 天时段窗拦截中 `custom_prevlow` 占 6,822 / 6,862 / 5,497 次。
#   生产默认**空**（零影响）；回测由 `jobs/bt_env_pins.sh` 置 'custom_prevlow'。
TW_EXEMPT_KINDS = [x.strip() for x in os.getenv("WOLF_TURNOVER_TW_EXEMPT", "").split(",") if x.strip()]


def applies_to(trigger_kind: Optional[str]) -> bool:
    """该腿型是否受 ③ 时段窗约束（做T类白名单；豁免名单优先；空/未知见 TW_UNKNOWN_ALLOW）。"""
    k = str(trigger_kind or "").strip()
    if k and k in TW_EXEMPT_KINDS:      # 2026-09-25 豁免清单（默认空 ⇒ 旧行为逐字不变）
        return False
    if not k:
        return not TW_UNKNOWN_ALLOW
    return k in TW_KINDS
_PROTECT = ("止损", "破位", "被动止盈", "顶态", "清仓", "避险", "风控", "反抽失败")

_STATS: Dict[str, Any] = {"churn_block": 0, "refill_block": 0, "window_block": 0, "cooldown_block": 0,
                          "window_exempt": 0, "checked": 0, "errors": 0}


def enabled() -> bool:
    return GUARD_ON


def stats() -> Dict[str, Any]:
    return {"on": GUARD_ON, "churn_min": CHURN_MINUTES, "churn_pct": CHURN_PCT,
            "windows": T_WINDOWS, "cooldown_days": COOLDOWN_DAYS, "refill_gate": _REFILL_GATE, **_STATS}


def stats_reset() -> None:
    for k in ("churn_block", "refill_block", "window_block", "cooldown_block", "checked", "errors"):
        _STATS[k] = 0


def in_t_window(hhmm: str, windows: Optional[list] = None) -> bool:
    """③ 做T时段窗（纯函数）：语料 9:45–10:00 与 14:00–14:30。"""
    for w in (windows or T_WINDOWS):
        try:
            a, b = w.split("-")
            if a.strip() <= hhmm <= b.strip():
                return True
        except Exception as _e_sil1:
            _silent_alert("t_turnover.py:111", _e_sil1)
            continue
    return False


def churn_hit(last_side: str, last_price: float, side: str, price: float,
              minutes_ago: float, pct: float = None) -> Tuple[bool, str]:
    """① 同价来回（纯函数）：方向相反、时间近、价位差小 → 命中。"""
    p = CHURN_PCT if pct is None else float(pct)
    if not last_price or not price or str(last_side) == str(side):
        return False, ""
    if minutes_ago is None or minutes_ago > CHURN_MINUTES:
        return False, ""
    diff = abs(price / last_price - 1) * 100
    if diff < p:
        return True, ("同价来回：%.0f 分钟前刚 %s@%.3f，本次 %s@%.3f 价差仅 %.2f%%（<%.1f%%）"
                      "—— 语料：左右横跳＝新仓、没有套利空间（playbook:148/309）"
                      % (minutes_ago, last_side, last_price, side, price, diff, p))
    return False, ""


def refill_blocked(last_sell_price: float, price: float) -> Tuple[bool, str]:
    """② 回补价闸（纯函数）：买入时若现价低于最近卖价 → 拦（"下去不补"）。"""
    if not _REFILL_GATE or not last_sell_price or not price:
        return False, ""
    if price < last_sell_price:
        return True, ("下去不补：最近卖出价 %.3f，现价 %.3f 更低 —— 语料：等回到卖出位置才补"
                      "（blueprint:232）" % (last_sell_price, price))
    return False, ""


def _cooldown_elapsed(d0: str, today: str) -> Optional[int]:
    """`d0 → today` 的**真实交易日数**（供 ④ 跨日冷却按日差判）。

    复用甲（禁令 TTL）修复时写的 `wolf_ticket_ban._cal_days_between`：
    ① 本地 bars 库区间查询（回测/有本地库）→ ② 窗口版交易日历 → ③ 生产 relay `resolve_trade_days`；
    **取不到 ⇒ None** ⇒ 调用方保守拦（与旧行为一致，不放大）。
    """
    try:
        from app.services.wolf_ticket_ban import _cal_days_between
        return _cal_days_between(d0, today)
    except Exception:
        return None


def _recent(account_id: str, symbol: str, limit: int = 12) -> list:
    """最近成交流水（必须 autocommit + statement_timeout，避免长事务卡住迁移 ALTER）。"""
    import psycopg2
    conn = psycopg2.connect(os.getenv("DATABASE_URL", ""), connect_timeout=4)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET statement_timeout=4000")
        cur.execute("SELECT direction, price, created_at, trade_date FROM paper_trades "
                    "WHERE account_id=%s AND symbol=%s AND COALESCE(voided,0)=0 "
                    "ORDER BY created_at DESC LIMIT %s", (account_id, symbol, limit))
        return cur.fetchall()
    finally:
        conn.close()


def check(symbol: str, side: str, price: float, account_id: str, reason: str = "",
          is_stop_loss: bool = False, now=None, trigger_kind: str = "") -> Tuple[bool, str]:
    """闸门总入口：返回 (ok, why)。保护性卖出（止损/破位/风控）一律放行。

    trigger_kind：腿型（t_triggers.event_type / t_conditions.trigger_kind）。**③ 时段窗只对做T类
    腿型生效**（见 TW_KINDS 与 applies_to）；① ② ④ 与腿型无关，照旧全适用。
    """
    if not GUARD_ON:
        return True, ""
    _STATS["checked"] += 1
    _r = str(reason or "")
    # ── 保护性委托豁免：**只对卖出**（2026-09-22 用户拍板 A：修一条 69% 买腿被旁路的 bug）──
    #   原实现 `if is_stop_loss or any(k in reason for k in _PROTECT): return True, ""` 对买卖一视同仁，
    #   而 `reason` 是**调用方传进来的自由文本**（AI 决策理由 / 腿的理由串）——AI 写买入理由时常用
    #   「未跌破止损」「不属破位」「无风控否决」这类**否定句**，命中关键词 ⇒ **①②③④ 四条闸整段跳过**。
    #   实测（环旭电子 601231 2026-03-03）：09:40 的 `wolf_zheng_t_buy` 本应被 ③ 做T时段窗
    #   （09:40 不在 09:45–10:00）拦下，但理由里有「未跌破止损」⇒ 放行 ⇒ 买在 −10% 瀑布日的起点 47.39，
    #   当日收跌停 43.58、次日 42.94 割掉（−3,611）。同一笔只把那句换成中性词，立刻变「拦」。
    #   全库口径：1,411 笔买成交里 **972 笔（69%）** 是"窗外 + 理由含保护性词" ⇒ 这道闸对他们等于不存在。
    #   开关 `WOLF_TURNOVER_PROTECT_SELL_ONLY`（**库内默认 0 = 旧行为**，生产逐位不变；回测由 pins 置 1）。
    _is_buy = str(side or "").strip().lower() in ("buy", "买入")
    _protect_sell_only = str(os.getenv("WOLF_TURNOVER_PROTECT_SELL_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")
    if is_stop_loss:
        return True, ""
    # 2026-09-22 "A′"：保护性判定改为**结构化腿型**（`t_protect`；开关 `WOLF_PROTECT_STRUCTURED` 关时=旧文本行为）
    _struct = False
    try:
        from app.services import t_protect as _tp
        _struct = _tp.is_protective(trigger_kind=trigger_kind, reason=_r, side=side)
    except Exception:
        _struct = any(k in _r for k in _PROTECT)
    if _struct:
        return True, ""
    if (not _protect_sell_only) or (not _is_buy):
        # 旧路径（开关关时 used；开关开时 t_protect 已按结构化判过，这里不再看文本）
        try:
            from app.services import t_protect as _tp2
            if not _tp2.structured_on():
                if any(k in _r for k in _PROTECT):
                    return True, ""
        except Exception:
            if any(k in _r for k in _PROTECT):
                return True, ""
    import datetime as _dt
    now = now or _dt.datetime.now()
    hhmm = now.strftime("%H:%M")
    _side_cn = "买入" if side == "buy" else "卖出"
    _tw_applies = applies_to(trigger_kind)
    try:
        rows = _recent(account_id, symbol)
    except Exception:
        _STATS["errors"] += 1
        return True, ""
    try:
        # ③ 时段窗（**只对做T类腿型**；建仓/减仓/止盈等非做T动作不受此约束）
        if not _tw_applies:
            _STATS["window_exempt"] += 1
        elif not in_t_window(hhmm):
            _STATS["window_block"] += 1
            return False, ("做T时段窗：%s 不在 %s 内（语料 playbook:287「只做上午 9:45–10:00、"
                           "下午 14:00–14:30，避免平稳时段来回 T」；腿型 %s 属做T类）"
                           % (hhmm, ",".join(T_WINDOWS), trigger_kind or "未标注"))
        _last_side = _last_price = None
        _last_sell = None
        for r in rows:
            d, p, ca, td = str(r[0]), float(r[1] or 0), r[2], str(r[3] or "")
            if _last_side is None:
                _last_side, _last_price = d, p
                try:
                    _ts = ca if not isinstance(ca, str) else _dt.datetime.fromisoformat(ca)
                    _mins = (now - _ts).total_seconds() / 60.0
                except Exception:
                    _mins = None
            if d in ("卖出", "sell") and _last_sell is None:
                _last_sell = p
        # ① 同价来回
        _hit, _why = churn_hit(_last_side or "", _last_price or 0, _side_cn, float(price or 0),
                               _mins if '_mins' in dir() else None)
        if _hit:
            _STATS["churn_block"] += 1
            return False, _why
        # ② 回补价闸（仅买入）
        if side == "buy" and _last_sell:
            _b, _w2 = refill_blocked(_last_sell, float(price or 0))
            if _b:
                _STATS["refill_block"] += 1
                return False, _w2
        # ④ 跨日冷却（同方向）
        #   ⚠️ 2026-09-30（账本 §9.319 补）：**影子模式也要能评估** ✓
        #     原写法 `if COOLDOWN_DAYS > 0` ⇒ 设 0 之后整段**不可达** ✗ ⇒ 影子行永不出现 ✗
        _SHADOW = str(os.getenv("WOLF_TURNOVER_COOLDOWN_SHADOW", "0")).strip().lower() \
            in ("1", "true", "yes", "on")
        if COOLDOWN_DAYS > 0 or _SHADOW:
            _today = now.strftime("%Y-%m-%d")
            _same_dir = [r for r in rows if str(r[0]) == _side_cn]
            if _same_dir:
                _d0 = str(_same_dir[0][3] or "")
                if _d0 and _d0 < _today:
                    _cd_eff = COOLDOWN_DAYS or int(float(os.getenv("WOLF_TURNOVER_COOLDOWN_DAYS_SHADOW", "2")))
                    _blk = True
                    if _REAL_CD and (_REAL_CD_SCOPE == "both" or _side_cn == "卖出"):
                        _el = _cooldown_elapsed(_d0, _today)
                        # 取不到交易日历时 `_el is None` ⇒ 保守拦（与旧行为一致，不放大）
                        if _el is not None and _el >= COOLDOWN_DAYS:
                            _blk = False
                            _STATS["cooldown_pass"] = _STATS.get("cooldown_pass", 0) + 1
                    if _blk:
                        # ⚠️ 2026-09-30（账本 §9.319）：**影子模式** ✓ —— 只记录、不拦截 ✓
                        #   用户口径 ✓：「现在直接关掉吧」⇒ 行为上等于关掉 ✓；
                        #   但保留"本该被拦"的样本 ✓ ⇒ 量化证据链不断 ✗
                        if _SHADOW:
                            print("[跨日冷却·影子] %s 上次%s在 %s（未满 %d 个交易日）⇒ **放行** ✓"
                                  % (symbol, _side_cn, _d0, _cd_eff), flush=True)
                            _STATS["cooldown_shadow"] = _STATS.get("cooldown_shadow", 0) + 1
                            return True, ""
                        _STATS["cooldown_block"] += 1
                        return False, ("跨日冷却：%s 上次%s在 %s，未满 %d 个交易日（语料 2025-02-07"
                                       "「一个票最多买 2 笔、卖 2 笔…越动收益越低」/blueprint:231）"
                                       % (symbol, _side_cn, _d0, _cd_eff))
    except Exception:
        _STATS["errors"] += 1
    return True, ""
