# -*- coding: utf-8 -*-
"""wolf_volume_gate.py — G10 量能门槛（2026-09-12，语料逐条精读所得）。

═══════════════════════════════════════════════════════════════════════════
狼大原文（NGA 精确时刻，逐条精读，全部照抄）:

  · **真假突破的量能门槛（核心）** 2026-09-03 14:44
    「这里**不上3WE的突破就是诱多** 简单直接的结论。」
    同日前文 2026-09-03 14:14「连续三天的半导体下打拉回 结合银行保险双突破 还差一个券商
    那接下来可能出现顶级突破走势 **如果放量不够 就是诱多 如果放量很大+好消息 就是真突破**」

  · **地量口径** 2026-08-20 13:51
    「现在只要磕头放量就行…**2WE是地量了** 只要放量就没事了。」

  · **2WE 以下 = 没人追** 2026-08-26 10:51
    「你看拉了指数1个点 量能只放了400E 而且是**2WE以下的微微放量** 非常少。
     证明这里没人追 **那我也不会追**。」

  · **缩量拉尾盘 = 谨慎** 2026-08-25 14:36
    「量是继续缩的 虽然缩的不大 但是黄线是继续拉升的状态…这种就是我说不太好的现象了
     虽然不卖 但是这里买不了一点…所以这种**缩量拉尾盘，我保持谨慎**。」

  · **放量转缩量拉指数 = 其他方向赚不到钱** 2026-08-26 19:43
    「还是**放量转缩量**的情况下指数拉出1%，你其他能挣到钱嘛？挣不到的」

  · **放量才能涨 / 缩量不跌** 2026-08-20 14:50
    「**放量才能涨**这是肯定的 但是**缩量他就不跌了**」

⚠️ **口径不明、因此不写进代码**（诚实留档）:
  2026-08-19 13:43「现在只要量能放到**1500E** 然后开始缩量…不放到1500E才是麻烦」
  2026-08-24 10:15「这里重点是 能不能放量**2000E**以上 不然半导体也只是比光存相对安全」
  → 1500E / 2000E 与「2WE 是地量」**不在同一量纲**（若指全市场成交额则低于地量，自相矛盾）。
    他在 08-19 14:06 说「现在到700多E」，说明这两个数更像**盘中增量或局部（板块/指数）口径**，
    语料不足以确定 → 只做留档，不实现为门槛。
═══════════════════════════════════════════════════════════════════════════

**口径**
  · 全市场成交额（亿元）= Σ tushare `daily(trade_date)` 的 `amount`（原始单位千元）÷ 1e5
  · 地量: < `WOLF_VG_DI_CEIL`（默认 20,000 亿 = 2WE）
  · 突破级: ≥ `WOLF_VG_BREAKOUT`（默认 30,000 亿 = 3WE）
  · 关键整数位: `WOLF_VG_KEY_LEVELS`（默认 "3800,3900,4000"），距离 < `WOLF_VG_NEAR_PCT`（默认 **1.5%**）算"到了关口"
    —— 1.5% 是对他两次实喊的标定：2026-09-03 上证 3942（距 4000 有 1.47%）他喊「不上3WE的突破就是诱多」；09-01 上证 3979（0.5%）「不过4000怎么诱多」
  · **诱多风险** = 「指数在关键整数位附近」∧「成交额未达突破级」→ 按他 2026-09-03 的口径即为诱多

**落点**：提示层（进 `wolf_discipline.discipline_context`），关闭时 `directive()` 返回空。
开关 `WOLF_VOLUME_GATE`，**默认关**（新机制默认关，需显式打开）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

IDX_TS = "000001.SH"
STATE_FILE = "wolf_volume_gate.json"
AMOUNT_KDIV = 1e5  # tushare amount 单位千元 → 亿元

# 交易时段（A 股）：上午 9:30-11:30（120 分）+ 下午 13:00-15:00（120 分）
_SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))
_TOTAL_MIN = 240.0


def enabled() -> bool:
    """`WOLF_VOLUME_GATE` 默认 **0（关）**。"""
    return os.getenv("WOLF_VOLUME_GATE", "0").strip().lower() not in ("0", "false", "no", "")


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"),
                        os.getenv("WOLF_VG_FILE", STATE_FILE))


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def key_levels() -> List[float]:
    raw = os.getenv("WOLF_VG_KEY_LEVELS", "3800,3900,4000")
    out: List[float] = []
    for x in str(raw).split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.append(float(x))
        except ValueError:
            continue
    return sorted(out)


# ───────────────────────── 数据层 ─────────────────────────

def market_amount(days: Sequence[str]) -> Dict[str, float]:
    """{YYYYMMDD: 全市场成交额（亿元）}；`pro.daily(trade_date=)` 单日全市场，1 请求/日。"""
    out: Dict[str, float] = {}
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        for d in days:
            try:
                df = pro.daily(trade_date=str(d).replace("-", ""))
            except Exception as e:
                print(f"[volume_gate] daily({d}) 失败: {type(e).__name__}: {str(e)[:60]}")
                continue
            if df is None or len(df) == 0 or "amount" not in df.columns:
                continue
            try:
                total = float(df["amount"].astype(float).sum())
            except Exception:
                continue
            if total > 0:
                out[str(d).replace("-", "")] = round(total / AMOUNT_KDIV, 1)
    except Exception as e:
        print(f"[volume_gate] market_amount 失败: {type(e).__name__}: {str(e)[:70]}")
    return out


def index_closes(n: int = 10) -> List[float]:
    """上证最近 n 个交易日收盘价（升序，最后一个是最近）。失败 → []。"""
    try:
        import datetime as _dt
        from app.services.t_backtest_data import _fetch_tushare_index_daily
        end = _dt.date.today().strftime("%Y%m%d")
        start = (_dt.date.today() - _dt.timedelta(days=int(n * 2.2) + 20)).strftime("%Y%m%d")
        bars = sorted(_fetch_tushare_index_daily(IDX_TS, start, end) or [],
                      key=lambda b: str(b.get("trade_date")))
        return [float(b.get("close")) for b in bars[-int(n):] if b.get("close") is not None]
    except Exception as e:
        print(f"[volume_gate] index_closes 失败: {type(e).__name__}: {str(e)[:60]}")
        return []


def index_close() -> Optional[float]:
    """上证最近一根日线收盘（失败 → None）。"""
    cs = index_closes(1)
    return cs[-1] if cs else None


# ───────────────────────── 判据层（纯函数，可测） ─────────────────────────

def elapsed_frac(hhmm: str) -> float:
    """交易时段已过去的比例（0~1）。'14:30' → 0.875；盘后 → 1.0。"""
    try:
        h, m = str(hhmm).strip().split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:
        return 1.0
    done = 0.0
    for s, e in _SESSIONS:
        if t >= e:
            done += (e - s)
        elif t > s:
            done += (t - s)
    return max(0.0, min(1.0, done / _TOTAL_MIN))


def project_day_amount(amount_so_far: float, hhmm: str) -> float:
    """盘中把已成交额按已过时段比例线性折算为全日预估（他"实时对比"的粗代理）。"""
    f = elapsed_frac(hhmm)
    if f <= 0:
        return 0.0
    return round(float(amount_so_far) / f, 1)


def classify(total_yi: float, ma5_yi: Optional[float] = None, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """量能分级：`地量`（<2WE）/ `常态` / `突破级`（≥3WE）；附与 MA5 之比。"""
    c = cfg or {}
    di = float(c.get("di_ceil", _env_f("WOLF_VG_DI_CEIL", 20000.0)))
    br = float(c.get("breakout", _env_f("WOLF_VG_BREAKOUT", 30000.0)))
    t = float(total_yi or 0)
    if t >= br:
        tag = "突破级"
    elif t < di:
        tag = "地量"
    else:
        tag = "常态"
    ratio = None
    if ma5_yi and float(ma5_yi) > 0:
        ratio = round(t / float(ma5_yi), 3)
    return {"tag": tag, "amount": round(t, 1),
            "di_ceil": di, "breakout": br, "ratio_vs_ma5": ratio,
            "below_di": t < di, "at_breakout": t >= br}


def _side(level: float, close: float, path: Optional[Sequence[float]] = None,
          lookback: int = 10) -> str:
    """判断当前处在关口的哪一侧（**这是 2026-09-12 修的关键语义**）。

      · `above`    : 收在关口上方
      · `broken`   : 收在关口下方，但**最近 N 日曾收在关口上方** → 是「跌破关口（破位）/ 反抽回下方」
      · `approach` : 收在关口下方，且最近 N 日**一直在下方** → 才是「上攻关口」

    为什么要分：他 2026-09-01「不过4000怎么诱多」与 2026-09-03「不上3WE的突破就是诱多」
    说的都是**自下而上攻关口**；而 2026-08-25「顶多就是指数破位后的止损」说明**跌破关口**
    在他的体系里是**止损触发**，不是诱多。原实现只向上找关口，会把"跌破后收回下方"误判成"上攻诱多"。
    """
    if close >= level:
        return "above"
    ps = [float(x) for x in (path or []) if x is not None][-int(lookback):]
    if any(x > level for x in ps):
        return "broken"
    return "approach"


def key_level_gap(close: Optional[float], levels: Optional[Sequence[float]] = None,
                  tol_pct: Optional[float] = None,
                  path: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """指数距**最近的、在其上方或贴近的**关键整数位有多远（%），并给出所处的一侧。

    `path` = 最近若干日收盘价（用于区分「上攻」与「跌破」）。
    """
    lv = sorted(levels if levels is not None else key_levels())
    tol = float(tol_pct if tol_pct is not None else _env_f("WOLF_VG_NEAR_PCT", 1.5))
    if not close or not lv:
        return {"level": None, "gap_pct": None, "near": False, "tol_pct": tol, "side": ""}
    c = float(close)
    above = [x for x in lv if x >= c]
    lvl = min(above) if above else max(lv)
    gap = round((lvl - c) / c * 100.0, 3)
    side = _side(lvl, c, path)
    return {"level": lvl, "gap_pct": gap, "near": abs(gap) <= tol, "tol_pct": tol, "side": side}


def fake_breakout_risk(cls: Dict[str, Any], gap: Dict[str, Any]) -> bool:
    """诱多风险 = **自下而上攻关口**（side=approach）∧ 量能未达突破级（2026-09-03 原话口径）。"""
    return bool(gap.get("near") and gap.get("side") == "approach" and not cls.get("at_breakout"))


def breakdown_risk(gap: Dict[str, Any]) -> bool:
    """破位风险 = 收在此前曾站上的关口**下方**（他 2026-08-25：「顶多就是指数破位后的止损」）。"""
    return gap.get("side") == "broken"


def evaluate(series: Dict[str, float], close: Optional[float] = None,
             levels: Optional[Sequence[float]] = None, cfg: Optional[Dict[str, Any]] = None,
             path: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """series: {YYYYMMDD: 亿元}；path: 最近若干日收盘价（判「上攻 vs 跌破」用）。"""
    ds = sorted(series.keys())
    if not ds:
        return {"ok": False, "reason": "no_series"}
    amounts = [float(series[d]) for d in ds]
    last_d, last = ds[-1], amounts[-1]
    ma5 = round(sum(amounts[-5:]) / len(amounts[-5:]), 1) if amounts else None
    cls = classify(last, ma5, cfg=cfg)
    gap = key_level_gap(close, levels, path=path)
    idx_chg = None
    ps = [float(x) for x in (path or []) if x is not None]
    if close and ps and ps[-1]:
        idx_chg = round((float(close) - ps[-1]) / ps[-1] * 100.0, 3)
    res = {"ok": True, "as_of": last_d, "series": {d: series[d] for d in ds[-10:]},
           "ma5": ma5, "level": cls, "key_level": gap, "close": close, "path": list(path or [])[-10:],
           "index_chg_pct": idx_chg,
           "fake_breakout_risk": fake_breakout_risk(cls, gap),
           "breakdown_risk": breakdown_risk(gap),
           "prev_delta_pct": (round((last - amounts[-2]) / amounts[-2] * 100.0, 2)
                              if len(amounts) >= 2 and amounts[-2] else None)}
    res["directive"] = directive_text(res)
    return res


def directive_text(res: Dict[str, Any]) -> str:
    """把判据结果写成一行可注入的提示（不读文件、不做开关判断 → 便于单测）。

    三个分支**必须分开**（2026-09-12 修）：他 2026-09-01/09-03 讲的是**上攻关口**放不出量=诱多；
    而 2026-08-25「顶多就是指数破位后的止损」说明**跌破关口**是止损触发；两者不是一回事。
    """
    lv = res.get("level") or {}
    g = res.get("key_level") or {}
    head = ("📊 量能门槛（狼大 2026-09-03「不上3WE的突破就是诱多」／2026-08-20「2WE是地量了」）"
            "｜全市场 %s 亿（MA5 %s 亿%s）→ **%s**"
            % ("{:,.0f}".format(float(lv.get("amount") or 0)),
               "{:,.0f}".format(float(res.get("ma5") or 0)),
               ("，较前日 %+.1f%%" % res["prev_delta_pct"]) if res.get("prev_delta_pct") is not None else "",
               lv.get("tag") or "?"))
    if g.get("level") is None:
        return head
    c_txt = ("%.2f" % float(res.get("close"))) if res.get("close") else "—"
    lvl, gap_pct, side = float(g["level"]), abs(float(g.get("gap_pct") or 0)), g.get("side") or ""
    if side == "broken":
        tail = "｜上证 %s **收在关口 %.0f 下方**（近 %d 日曾在其上方）" % (
            c_txt, lvl, len(res.get("path") or []) or 0)
    elif side == "above":
        tail = "｜上证 %s **已站上关口 %.0f**" % (c_txt, lvl)
    else:
        tail = "｜上证 %s 距关口 %.0f 还有 %.2f%%（**自下而上**）" % (c_txt, lvl, gap_pct)
    if res.get("breakdown_risk"):
        chg = res.get("index_chg_pct")
        if chg is not None and chg > 0:
            # 他 2026-08-21 14:43「指数4-4高点4000附近后因为利空回踩的第二次**可能的主力诱多反抽小级别行情**」
            # → 这种"跌下来的关口下方的反抽"在他是**做T**场景，不是加仓场景
            return (head + tail + "｜⚠️ 关口已跌破，今日为**破位后的反抽**（他 2026-08-21 称之为"
                    "「主力诱多反抽小级别行情」）→ **只做T、不加仓**；量能不足则反抽随时结束")
        return (head + tail + "｜⚠️ **跌破关口 = 破位**：按他 2026-08-25「顶多就是指数破位后的止损」"
                "这是**止损/减仓评估触发**（不是诱多）；若随后缩量反抽回关口下方，"
                "他 2026-09-07 称之为『散户绞肉机』→ **不追、只做T**")
    if res.get("fake_breakout_risk"):
        return head + tail + "｜⚠️ **攻关口而量能未达突破级 → 按他口径判定为诱多：不追高、不加仓**"
    if lv.get("at_breakout"):
        return head + tail + "｜✅ 已达突破级量能（真突破仍需「放量很大+好消息」配合，他 2026-09-03）"
    if lv.get("below_di"):
        return head + tail + "｜⚠️ 地量（他 2026-08-26「2WE 以下的微微放量…那我也不会追」）→ 只看不做"
    return head + tail


# ───────────────────────── 采集/落盘 ─────────────────────────

def run(days: int = 10, save: bool = True, series: Optional[Dict[str, float]] = None,
        close: Optional[float] = None, path: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """采集 → 判据 → 落 `wolf_volume_gate.json`。`series`/`close`/`path` 可注入（测试/回放）。"""
    if series is None:
        if not enabled():
            return {"ok": False, "reason": "disabled"}
        try:
            import datetime as _dt
            from app.services.t_backtest_data import resolve_trade_days
            end = _dt.date.today()
            start = end - _dt.timedelta(days=int(days * 2.2) + 10)
            ds = resolve_trade_days(start.strftime("%Y%m%d"), end.strftime("%Y%m%d")) or []
            ds = sorted([str(d)[:8] for d in ds if str(d)[:8].isdigit()])[-int(days):]
        except Exception as e:
            print(f"[volume_gate] 交易日历失败: {type(e).__name__}: {str(e)[:60]}")
            ds = []
        if not ds:
            return {"ok": False, "reason": "no_trade_days"}
        series = market_amount(ds)
    if path is None:
        cs = index_closes(10)
        path = cs
        if close is None and cs:
            close = cs[-1]
    close = close if close is not None else index_close()
    res = evaluate(series or {}, close, path=path)
    res["close"] = close
    if res.get("ok") and save:
        try:
            p = _path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"[volume_gate] 落盘失败: {str(e)[:80]}")
            res["ok"] = False
    if res.get("ok"):
        print(f"[volume_gate] {res['as_of']} 成交 {res['level']['amount']:.0f} 亿 "
              f"MA5 {res['ma5']:.0f} → {res['level']['tag']}｜诱多风险={res['fake_breakout_risk']}")
    return res


def load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def directive() -> str:
    """量能门槛提示；关闭时返回空（避免"假开关"：只停 job 但旧状态文件仍被注入）。"""
    if not enabled():
        return ""
    st = load()
    if not st or not st.get("ok"):
        return ""
    return st.get("directive") or directive_text(st)
