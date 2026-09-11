# -*- coding: utf-8 -*-
"""wolf_gap_open.py — 高低开 + 跳空缺口 + 量能实时对比（A6/A8/A9 前半，2026-09-11）。

═══════════════════════════════════════════════════════════════════════
狼大原文（XLS **2025-04-15**「我的买卖做T方法」，同一楼整段，逐字）:

  条件4：量能，这个非常重要，一定要随时看股票软件里面 **与上一日的量能实时对比**。
    如果是**低开，缩量，黄线高于白线，在不破指数下支撑的前提下**这个**现象1**，
    及如果是**高开，放量，黄线高于白线，在不接近上方压力位**，及**开盘15分钟后不跌破
    当日高开的跳空缺口下方(也就是昨天最高位)**这个**现象2**。
    这两个情况**加仓做进攻板块**(也就是最近的大消费和泛科技)的**成功率会比较高**。
  条件5：如果达成条件4的情况下，如果当日想做更极致操作，那就在当日分时接近下方支撑位置
    缩量转放量的时候进行进场操作。        ← 已由 A7（m5.t1_shrink_expand）覆盖
  条件6：如果当日开盘**高开快速拉升，或者低开快速拉升想追进去的**，在**下午2.00-2.30**
    这个时间段进行**回补**，这个时候确保**分时上涨放量，回调缩量**的情况下，
    去补**进攻板块里面涨得还不多的**。      ← 时段由 A5 的 14:00-14:30 窗覆盖；见下"落点"

  「如果当出现以下情况时，**操作谨慎，尽量不要加仓进场**：
     1：黄线迅速下穿白线，放量。 2：**黄白线交织，缩大量**。 3 白线迅速上穿黄线：缩量，
     4：接近大指数级别压力位附近」
    （注：第 2 条此前在抽取入库时被"短句最小长度过滤"**静默丢掉**，本轮回原始 XLS 补齐）
═══════════════════════════════════════════════════════════════════════

**数据口径（全部来自他的原话或本仓既有模块，不自造）**
  · 跳空缺口下沿 = **昨日最高价**（他原话括注「也就是昨天最高位」）
  · 量能 = **今日累计成交量 / 昨日同期累计成交量**（他原话「与上一日的量能实时对比」）
    → 腾讯 m5（带日期、跨多日、每交易日 48 根）→ 可精确对齐"同一时刻"
  · 黄/白线 = `wolf_index_breadth`（A4，指数对代理）
  · 指数下支撑 / 上方压力位 = 本仓既有 `support_resistance.compute_levels('sh000001')`；
    取不到时**回退**为指数自身前低/20 日最高（他的用法里"前低=支撑、前高=压力"，
    见 2026-05-13「冲击前高4230.11压力位」/ 2025-11-23「上面压力对应3922附近」），
    并在返回值里显式标注 `level_src`，不静默。

**落点与边界**
  · 现象1/2 是**正向偏好**（"成功率会比较高"）→ 做**提示层**（进纪律上下文 + 快照字段），
    **不做硬门**：要求"必须是这两个形态之一才允许做T"会把他没说的约束强加上去。
  · 谨慎 4 条是**禁令**（"尽量不要加仓进场"）→ 做**硬门**：命中即拦**加仓/回补类买腿**
    （与 A5 同一组腿型 `low_buy`/`custom_prevlow`）。`WOLF_GAP_CAUTION=0` 关。
  · 样本不足（进程刚起、spread 历史 <2 条）→ 交叉类谨慎条件**不触发**（fail-open，宁可不拦），
    并在 `note` 里写明，便于事后核对是"没命中"还是"没数据"。
  · **仅适用实盘**：本模块没有 "as-of 时刻" 语义（`gap_state` 用当日全部分钟 bar），
    历史回放会用到"当时还没发生"的 bar → 不要拿它做回测判据（回测应另写 as-of 版本）。
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

IDX = "sh000001"
IDX_TS = "000001.SH"

_D_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
_M5_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}


def enabled() -> bool:
    return os.getenv("WOLF_GAP_OPEN", "1").strip() not in ("0", "false", "no")


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


# ─────────────────────────── 数据层 ───────────────────────────

def index_daily(force: bool = False) -> List[dict]:
    """上证日线（约 40 个交易日）。失败 → []（不抛）。"""
    now = time.time()
    if not force and _D_CACHE["value"] and now - _D_CACHE["at"] < 600:
        return _D_CACHE["value"]
    try:
        import datetime as _dt
        from app.services.t_backtest_data import _fetch_tushare_index_daily
        end = _dt.date.today().strftime("%Y%m%d")
        start = (_dt.date.today() - _dt.timedelta(days=90)).strftime("%Y%m%d")
        bars = _fetch_tushare_index_daily(IDX_TS, start, end) or []
        bars = sorted(bars, key=lambda b: str(b.get("trade_date") or ""))
        if bars:
            _D_CACHE.update({"at": now, "value": bars})
        return bars or _D_CACHE["value"] or []
    except Exception as e:
        print(f"[gap_open] 指数日线失败: {type(e).__name__}: {str(e)[:80]}")
        return _D_CACHE["value"] or []


def index_m5(force: bool = False) -> List[dict]:
    """上证 m5（跨多日，用于"与上一日实时对比"）。失败 → []。"""
    now = time.time()
    if not force and _M5_CACHE["value"] and now - _M5_CACHE["at"] < 120:
        return _M5_CACHE["value"]
    try:
        from app.services.t_data_sources import fetch_tencent_mkline
        bars = fetch_tencent_mkline(IDX, freq="m5", count=500) or []
        if bars:
            _M5_CACHE.update({"at": now, "value": bars})
        return bars or _M5_CACHE["value"] or []
    except Exception as e:
        print(f"[gap_open] 指数 m5 失败: {type(e).__name__}: {str(e)[:80]}")
        return _M5_CACHE["value"] or []


# ─────────────────────────── 判据层 ───────────────────────────

def _today8() -> str:
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y%m%d")


def _pick_days(days: Dict[str, List[dict]], date8: Optional[str]) -> Optional[List[str]]:
    """定位"今日"和"昨日"两个交易日 key。

    **关键守卫**：m5 取数失败时缓存里最后一根可能是**上一个交易日** → 若不加判断，
    就会拿昨天的缺口/开盘形态给今天的交易做门（静默错判）。故：
      · 显式传 date8（回放/验证用）→ 用它；
      · 否则要求 m5 最后一天 == **今天**（东八区），否则返回 None（不判，fail-open）；
      · `WOLF_GO_ALLOW_STALE=1` 可强制用最后一天（仅供离线回看）。
    """
    ks = sorted(days.keys())
    if len(ks) < 2:
        return None
    if date8:
        if date8 not in days:
            return None
        i = ks.index(date8)
        return ks[max(0, i - 1):i + 1] if i >= 1 else None
    if ks[-1] != _today8() and os.getenv("WOLF_GO_ALLOW_STALE", "0").strip() not in ("1", "true", "yes"):
        return None
    return ks[-2:]


def split_days(bars: List[dict]) -> Dict[str, List[dict]]:
    """m5 bars → {YYYYMMDD: [bars 时间升序]}。"""
    out: Dict[str, List[dict]] = {}
    for b in bars or []:
        d = str(b.get("time") or "")[:8]
        if len(d) == 8:
            out.setdefault(d, []).append(b)
    for d in out:
        out[d].sort(key=lambda b: str(b.get("time")))
    return out


def volume_ratio(bars: Optional[List[dict]] = None, date8: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """**条件4 口径**：今日累计量 ÷ 昨日同期累计量（同一 HHMM 时刻）。

    → {'ratio', 'today_cum', 'prev_cum', 'at', 'kind': 'shrink'|'expand'|'flat'} 或 None。
    昨日同期用"时间 <= 当前 HHMM 的最后一根"对齐（m5 共 48 根/日，无集合竞价缺口）。
    """
    bars = bars if bars is not None else index_m5()
    days = split_days(bars)
    pair = _pick_days(days, date8)
    if not pair:
        return None
    pd_, td = pair[0], pair[1]          # _pick_days 返回**升序** [昨日, 今日]（曾在此写反过）
    tb = days[td]
    if not tb:
        return None
    cur_hm = str(tb[-1].get("time"))[8:12]
    pv = days.get(pd_) or []
    pb = [b for b in pv if str(b.get("time"))[8:12] <= cur_hm]
    if not pb:
        return None
    tc = sum(float(b.get("vol") or 0) for b in tb)
    pc = sum(float(b.get("vol") or 0) for b in pb)
    if pc <= 0:
        return None
    r = tc / pc
    eps = _env_f("WOLF_GO_VOL_EPS", 0.05)
    kind = "expand" if r > 1 + eps else ("shrink" if r < 1 - eps else "flat")
    return {"ratio": round(r, 3), "today_cum": tc, "prev_cum": pc,
            "at": f"{td[4:6]}-{td[6:8]} {cur_hm[:2]}:{cur_hm[2:]}", "kind": kind,
            "prev_date": pd_}


def gap_state(daily: Optional[List[dict]] = None, m5: Optional[List[dict]] = None,
              date8: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """今日开盘形态 + 跳空缺口。缺口下沿 = **昨日最高价**（他原话括注）。"""
    daily = daily if daily is not None else index_daily()
    m5 = m5 if m5 is not None else index_m5()
    days = split_days(m5)
    pair = _pick_days(days, date8)
    if not daily or not pair:
        return None
    td = pair[-1]
    tb = days[td]
    if not tb:
        return None
    today_open = float(tb[0].get("open") or 0)
    mg = [b for b in daily if str(b.get("trade_date") or "") < td]
    if not mg:
        return None
    prev = mg[-1]
    ph, pc, pl = float(prev.get("high") or 0), float(prev.get("close") or 0), float(prev.get("low") or 0)
    if today_open <= 0 or ph <= 0:
        return None
    if today_open > ph:
        kind, gap_low, gap_high = "gap_up", ph, today_open
    elif today_open < pc:
        kind, gap_low, gap_high = "low_open", None, None
    else:
        kind, gap_low, gap_high = "flat", None, None
    # 开盘 15 分钟后（09:45 起）是否跌破缺口下沿
    lo_after15 = None
    if gap_low:
        af = [b for b in tb if str(b.get("time"))[8:12] >= "0945"]
        if af:
            lo_after15 = min(float(b.get("low") or 0) for b in af)
    return {"kind": kind, "date": td, "open": today_open,
            "prev_high": ph, "prev_low": pl, "prev_close": pc,
            "gap_low": gap_low, "gap_high": gap_high,
            "low_after_0945": lo_after15,
            "gap_broken": (lo_after15 is not None and gap_low is not None and lo_after15 < gap_low),
            "last": float(tb[-1].get("close") or 0),
            "high": max(float(b.get("high") or 0) for b in tb),
            "low": min(float(b.get("low") or 0) for b in tb)}


def index_levels(force: bool = False) -> Dict[str, Any]:
    """指数的下支撑 / 上方压力位 → {'support','resistance','src'}。

    主口径 = 本仓既有 `support_resistance.compute_levels`（与个股同一套，不另立标准）；
    取不到 → 回退为**前低 / 20 日最高**（他的"前低=支撑、前高=压力"用法），src 标注来源。
    """
    try:
        from app.services.support_resistance import compute_levels
        lv = compute_levels(IDX) or {}
        sup = [x.get("price") for x in (lv.get("support") or [])]
        res = [x.get("price") for x in (lv.get("resistance") or [])]
        if sup and res:
            return {"support": float(sup[0]), "resistance": float(res[0]), "src": "support_resistance"}
    except Exception:
        pass
    daily = index_daily()
    if not daily:
        return {"support": None, "resistance": None, "src": "none"}
    prev = daily[-1]
    win = _env_f("WOLF_GO_PRESSURE_WIN", 20)
    recent = daily[-int(win):]
    return {"support": float(prev.get("low") or 0) or None,
            "resistance": max([float(b.get("high") or 0) for b in recent] or [0]) or None,
            "src": f"fallback(前低/近{int(win)}日最高)"}


def hb_state() -> Dict[str, Any]:
    """黄白线（A4）→ {'side','spread','warn'}；不可用则 side=None。"""
    try:
        from app.services.wolf_index_breadth import huang_bai
        hb = huang_bai() or {}
        return {"side": hb.get("side"), "spread": hb.get("spread"),
                "stale": hb.get("stale"), "proxy": hb.get("proxy")}
    except Exception:
        return {"side": None, "spread": None}


def caution_reasons(gap: Optional[dict] = None, vol: Optional[dict] = None,
                    levels: Optional[dict] = None, hb: Optional[dict] = None,
                    cross: Optional[str] = None, whipsaw: Optional[int] = None,
                    price: Optional[float] = None) -> List[str]:
    """**谨慎 4 条**（"尽量不要加仓进场"）→ 命中的原因列表（空 = 不触发）。

    1 黄线迅速下穿白线 ∧ 放量     → cross=='down' ∧ vol.kind=='expand'
    2 黄白线交织 ∧ 缩大量         → whipsaw>=2 ∧ ratio <= 交织缩量阈值
    3 白线迅速上穿黄线 ∧ 缩量     → cross 非空 ∧ vol.kind=='shrink'
    4 接近大指数级别压力位        → 距压力位 <= near%（默认 1%，他未给数 → 可调参数）
    """
    out: List[str] = []
    gap = gap or {}
    px = price if price is not None else (gap.get("last") or 0)
    near = _env_f("WOLF_GO_NEAR_PRESSURE_PCT", 1.0)
    res = (levels or {}).get("resistance")
    vk = (vol or {}).get("kind")
    r = (vol or {}).get("ratio")
    if cross == "down" and vk == "expand":
        out.append("谨慎1: 黄线迅速下穿白线且放量")
    if whipsaw is not None and whipsaw >= 2 and r is not None and r <= _env_f("WOLF_GO_WHIPSAW_VOL", 0.8):
        out.append("谨慎2: 黄白线交织且缩大量")
    if cross is not None and vk == "shrink":
        out.append("谨慎3: 黄白线交叉且缩量")
    if res and px > 0 and (float(res) - px) / px * 100.0 <= near:
        out.append(f"谨慎4: 接近大指数级别压力位({float(res):.2f}, 距{(float(res)-px)/px*100:.2f}%)")
    return out


def evaluate(now: Optional[float] = None, force: bool = False,
             date8: Optional[str] = None) -> Dict[str, Any]:
    """全量评估 → 现象1/2 + 谨慎 4 条 + 硬门结论。任何环节缺失都不抛、只记 note。"""
    if not enabled():
        return {"ok": False, "reason": "disabled"}
    note: List[str] = []
    gap = gap_state(date8=date8)
    vol = volume_ratio(date8=date8)
    lv = index_levels(force=force)
    hb = hb_state()
    cross = whipsaw = None
    try:
        from app.services.wolf_index_breadth import cross_recent, whipsaw_recent
        cross, whipsaw = cross_recent(now), whipsaw_recent(now)
    except Exception:
        pass
    if gap is None:
        note.append("开盘/缺口数据不可用")
    if vol is None:
        note.append("量能对比数据不可用")
    if hb.get("side") is None:
        note.append("黄白线不可用/纠缠")
    if cross is None and whipsaw is None:
        note.append("黄白线历史样本不足→交叉类谨慎条件暂不判(fail-open)")

    # ── 现象1：低开 + 缩量 + 黄>白 + 不破指数下支撑 ──
    px = (gap or {}).get("last") or 0
    sup = lv.get("support")
    near = _env_f("WOLF_GO_NEAR_PRESSURE_PCT", 1.0)
    res = lv.get("resistance")
    f1 = None
    if gap and vol and hb.get("side") == "huang":
        if gap["kind"] == "low_open" and vol["kind"] == "shrink":
            if sup is None:
                f1 = False
                note.append("现象1: 缺支撑位→不判")
            else:
                f1 = bool(px >= float(sup))
    elif gap and gap["kind"] == "low_open":
        f1 = False

    # ── 现象2：高开(有跳空缺口) + 放量 + 黄>白 + 不近压力 + 09:45 后不破缺口下沿 ──
    f2 = None
    if gap and vol and hb.get("side") == "huang" and gap["kind"] == "gap_up":
        not_near = True if not res else ((float(res) - px) / px * 100.0 > near if px else False)
        if gap["gap_broken"]:
            f2 = False
        else:
            f2 = bool(vol["kind"] == "expand" and not_near)

    caution = caution_reasons(gap, vol, lv, hb, cross, whipsaw, px)
    return {"ok": True, "gap": gap, "vol": vol, "levels": lv, "huang_bai": hb,
            "cross": cross, "whipsaw": whipsaw,
            "form1": f1, "form2": f2,
            "add_ok": bool(f1 or f2),
            "caution": caution, "block_add": bool(caution) and _caution_on(),
            "note": note, "ts": int(now or time.time())}


def _caution_on() -> bool:
    return os.getenv("WOLF_GAP_CAUTION", "1").strip() not in ("0", "false", "no")


def block_reason(ev: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """硬门用：命中谨慎 4 条 → 返回原因串；否则 None。"""
    if not enabled() or not _caution_on():
        return None
    ev = ev if ev is not None else evaluate()
    if ev.get("block_add"):
        return "；".join(ev.get("caution") or [])
    return None


def directive() -> str:
    """给纪律上下文的提示块（现象1/2 为正向偏好，谨慎4条为禁令）。"""
    ev = evaluate()
    if not ev.get("ok"):
        return ""
    g, v, hb = ev.get("gap") or {}, ev.get("vol") or {}, ev.get("huang_bai") or {}
    if not g:
        return ""
    kind_cn = {"gap_up": "高开跳空", "low_open": "低开", "flat": "平开"}.get(g.get("kind"), "?")
    vk_cn = {"expand": "放量", "shrink": "缩量", "flat": "量平"}.get(v.get("kind"), "量能?")
    lines = ["🕳 高低开/缺口（狼大 2025-04-15 条件4）｜%s 开%.2f 缺口下沿(昨高)%.2f %s｜量能 %s×（%s）｜黄白线 %s"
             % (kind_cn, g.get("open") or 0, g.get("prev_high") or 0,
                "已破缺口" if g.get("gap_broken") else ("未破缺口" if g.get("gap_low") else "-"),
                v.get("ratio"), vk_cn, hb.get("side") or "交织/不可用")]
    if ev.get("form1"):
        lines.append("  ✅ 现象1 成立（低开+缩量+黄>白+不破支撑）→ 加仓做进攻板块成功率较高")
    if ev.get("form2"):
        lines.append("  ✅ 现象2 成立（高开+放量+黄>白+不破缺口+不近压力）→ 同上")
    if ev.get("caution"):
        lines.append("  ⛔ 谨慎（他的原话：操作谨慎，尽量不要加仓进场）：" + "；".join(ev["caution"]))
    if ev.get("note"):
        lines.append("  （未判/降级: %s）" % "；".join(ev["note"]))
    return "\n".join(lines)
