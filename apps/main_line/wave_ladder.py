# -*- coding: utf-8 -*-
"""wave_ladder.py — 他的**浪型假设链 + 自动降级**（2026-10-15 原话落地为影子）。

狼大原话（帖 47288722，2026-10-15 10:17，lou 1224）：
  · 「之前的判断是一级一级的降低的 —— 首先判断了整波为**调整浪**，可能性有
     **失败4-5 / 成功4-5 / 下跌1浪**这三种，**当每一种达不到的时候自动降级为下一种**。
     这就是技术在股市里的作用：通过**硬性条件**调整**操作策略、预期管理、仓位管理**。」
原话（同日 09:56，lou 1221，降级实例）：
  · 「今天收盘后**如果没有收回跳空跌幅** ⇒ 整波浪型从**主升阶段4浪调整**转为**主跌1浪**；
    后续预期从**5浪上升**转为**大B浪反弹**；操作策略转为**自救**；持仓精简为个别算力和部分题材。」
原话（2026-08-05 17:09）：
  · 「4-5 对应就是高位科技的 C 杀，**可能 4-5 是失败5**」⇒ 「失败」= 不创新高的截断 5 浪。

**档位顺序**（2026-10-15 用户拍板，按时间推进而非他字面顺序）：
    4浪调整 → 成功4-5（整理结束·创出新高）→ 失败4-5（冲高不过前高后回落）→ 下跌1浪（破位）
  · **单向**：`rung = max(上一档, 当日判定档)` —— 他的链条只降不升 ✓；
    要“回档”（如 08-05 他改口「肯定按 4 接 5 做」）必须人工 `reset`，不自动回退 ✓。
  · **操作/预期**（每档对他实际姿态的对照，见各档 `expect`/`operation`）：
    4浪调整 → 吃反抽、以安全带走利润为主（`t_only`；他 07-31~08-03 只做半导体 ETF ✓）
    成功4-5 → 调整结束、主升末段在走（`side`；他 08-05「按 4 接 5 做」✓）
    失败4-5 → 反抽夭折、不新建仓（`defense`；他 10-09~10-13 避海外链、只吃差价 ✓）
    下跌1浪 → 自救、精简持仓（`exit`；他 10-15「操作策略转为自救」✓）

口径（带 ⛔ 的是我们的代理，他没给量化；**教材依据**见本文件「结构位（教材铁律②）」一节，出处
中山证券投资者教育《波浪理论》https://www.zszq.com/tzzyd/article/292 —— 该页原文即“两条基本数浪规则”）:
  · **未收回跳空** = 当日 `high < 前收`（真跳空）∧ `close < 前收`（跌幅未收回）——他的原话条件 ✓；
  · **创新高** = `close >= 前 60 日收盘最高`（成功4-5 的硬证据 ⛔ 窗口自定）；
  · **主跌1浪的破位**（2026-10-15 换教材口径）= 收盘 < **主升 1 浪顶**（铁律②「第四个浪的底部不可以低于
    第一个浪的浪顶」；实测 3174.27/2024-05-20，他引用的 3674.40 是再前一个主级 H ⇒ 可用
    `WOLF_WAVE_LADDER_W1` 钉住）∨ **跳空且破 20 日低位** ∨ 关键位；**单日跳空只降一档**到「失败4-5」
    （实测：2026-08-19 一个 0.73% 跳空直接判主跌1浪 ⇒ 9~10 月误判两个月 ✗）；
  · **失败4-5** = 近 20 日反弹**摸到前高附近**（高点 ≥ `NEAR_MIN`×前 60 日最高，⛔ 0.95）
    ∧ 未创 60 日新高 ∧ 自该高点回落 ≥ `FADE_MIN`(⛔ 3%)
    —— “摸到前高附近”这一条是 2026-10-15 实测补的：**少了它，深调里任何一次 3% 反弹
       都会把档位误判成「失败4-5」**（首测 2026-07-31 就误报，而他当时说的是「调整浪」✗）；
  · 数据：`data/index_daily_<code>.json`（东财日线，`[date, close, high, amount]` ✓ 已实测），
    缺失回落 `data/指数数据/index_daily/<code>.csv`；**取不到一律返回 None（fail-open）** ✓；
  · 操作/仓位口径复用 `wave_alloc.WAVE_ALLOC`（同一张表，避免两处各写一份 ✓）。

离线验收（`python jobs/eval_wave_ladder.py`，2026 全年 as-of 回放，RESET=5%：8 次降级 / 5 次重定浪 / 无非法升档 ✓）：
  · 2026-03-23 [3] 下跌1浪 —— 他 03-23 在防、03-25「减回 50% 内」✓
  · 2026-04-09 [3]（锁着）—— 他 04-09「白线在上 肯定没有买点 ⇒ 减回 50-55%」✓
  · 2026-07-31 [0] 4浪调整（`t_only`）—— 他「整波为调整浪」✓
  · 2026-08-05 [1] 成功4-5（`side`，「止跌确认：20日低点 3764.15 冻结13日 ∧ 距低+3.0% ∧ 站上MA20」）
    —— 他说「看到上面守了 3800 ⇒ 肯定按 4 接 5 做」并在半导体吃 20 个点 ✓✓
  · 2026-10-09 ~ 10-14 [3] 下跌1浪（`exit`）—— 他「避海外链、只吃差价」→ 10-15「转为自救」✓
  · **2026-08「偏保守」已修**（2026-10-15 第二轮）：rung1 不再只认「创 60 日新高」，改为**止跌确认**
    = 20 日低点**冻结 ≥5 日** ∧ **距低 ≥3%** ∧ **站上 MA20** ⇒ 2026-08-05（他说「按 4 接 5 做」的
    同一天）判出 [1] 成功4-5/`side` ✓。两窗口实测可分：8 月距低 +3.0~5.4% ∧ 低点自 07-17 冻结；
    10 月距低 ≤2.3% ∧ 低点一路刷新（3823→3811→3797→3767）✓。
    **残余风险 ⚠️**：rung1 会**放行建仓**（`side`）⇒ 若误报的代价大于误漏，请收紧 `OFF_LOW`/`LOW_AGE`。
    2026-08-19「一跳空直接判主跌1浪」的偏差**已修**（单日跳空只降一档）✓。

开关：`WOLF_WAVE_LADDER`（默认 **0** ⇒ **影子**：只落盘 + 只读，不改任何交易行为）
      `WOLF_WAVE_LADDER_FLOOR`（默认 0 ⇒ 不启用；>0 时额外作为“关键位”，跌破即主跌1浪）
自检：`python apps/main_line/wave_ladder.py`
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

DATA = os.environ.get("DATA_DIR", "data")
def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


MIN_BARS = 80                      # 少于这个根数不给结论（fail-open）
HI_WIN = 60                        # 新高/破位窗口
RALLY_WIN = 20                     # 反弹窗口
RALLY_MIN = 0.03                   # ⛔ 代理：认定“冲出过一段”
FADE_MIN = 0.03                    # ⛔ 代理：认定“已经回落”
NEAR_MIN = _envf("WOLF_WAVE_LADDER_NEAR", 0.98)    # ⛔ 代理：认定“摸到前高附近”
GAP_MIN = _envf("WOLF_WAVE_LADDER_GAP_MIN", 0.005)  # ⛔ 代理：真跳空的最小幅度（2026 全年只有 5 天 >0.5%）
RESET_RALLY = _envf("WOLF_WAVE_LADDER_RESET", 0.05)  # ⛔ 代理：反抽 ≥5% ⇒ 空头结构被破坏 ⇒ 重定浪型
# ↑ 2026-10-15 扫 8%/6%/5%：8% ⇒ 7-17 判夭折后**锁死到 10-14**（他 8 月在买半导体 ✗）；
#   5% ⇒ 07-31/08-05 回到「4浪调整(试探)」、08-19 才转防守 ✓ 与他的公开姿态一致 ✓
OFF_LOW = _envf("WOLF_WAVE_LADDER_OFF_LOW", 0.03)  # rung1 止跌确认：距 20 日低点反弹 ≥3%（⛔ 代理）
LOW_AGE = int(_envf("WOLF_WAVE_LADDER_LOW_AGE", 5))   # rung1 止跌确认：20 日低点已冻结 ≥5 个交易日
# ↑ 两窗口实测（2026-10-15）：窗口A（他 08-05「按4接5做」并吃半导体）距低 +3.0~5.4% ∧ 低点自 07-17 冻结；
#   窗口B（他 10 月上「调整/反抽、避海外链」）距低 ≤2.3% ∧ 20 日低点一路被刷新（3823→3811→3797→3767）⇒ 分得开 ✓
ZIGZAG_PCT = _envf("WOLF_WAVE_LADDER_ZIGZAG", 0.10)     # 主级 zigzag 阈值（10% ⇒ 上证一年几次拐点）
PCT_FINE = _envf("WOLF_WAVE_LADDER_ZIGZAG_FINE", 0.05)  # 细级 zigzag（只出预警位，不触发降级）
ORIGIN_BARS = int(_envf("WOLF_WAVE_LADDER_ORIGIN_BARS", 900))  # 主升起点回溯窗口（≈3.5 年）
LOW60_TRIGGER = os.getenv("WOLF_WAVE_LADDER_LOW60", "0").strip() in ("1", "true", "yes")
# ↑ 2026-10-15 起**默认关**：60 日新低是早期拍的代理；换教材「铁律②」后它退为参考量，=1 可 A/B 回旧行为 ✓

RUNGS: List[Dict[str, str]] = [
    {"key": "w4_adj", "name": "4浪调整",
     "expect": "调整未完，等 4-5 收尾（只吃反抽）", "operation": "t_only",
     "hold": "未创新高 ∧ 未摸前高后夭折 ∧ 未破 60 日收盘低位 ∧ 无未收回跳空"},
    {"key": "w45_ok", "name": "成功4-5",
     "expect": "调整末腿走完 ⇒ 主升末段（5浪上升）", "operation": "side",
     "hold": "收盘创 60 日新高 ∨ **止跌确认**（低点冻结 ≥5 日 ∧ 距低 ≥3% ∧ 站上 MA20）"},
    {"key": "w45_fail", "name": "失败4-5",
     "expect": "反抽夭折（冲高不过前高后回落）", "operation": "defense",
     "hold": "近20日摸到前高附近 ∧ 未创60日新高 ∧ 已回落达标"},
    {"key": "down1", "name": "下跌1浪",
     "expect": "大B浪反弹（自救：只降不空）", "operation": "exit",
     "hold": "未收回跳空 ∨ 收盘 < 主升 1 浪顶（铁律②失效）∨ 跌破关键位"},
]
RUNG_INDEX = {r["key"]: i for i, r in enumerate(RUNGS)}

# 仓位/操作口径复用 wave_alloc（缺依赖时退回本地副本，值保持一致 ✓）
try:  # pragma: no cover - 依赖环境
    from wave_alloc import WAVE_ALLOC as _ALLOC  # type: ignore
except Exception:  # pragma: no cover
    _ALLOC = {"build": (0.40, 0.35, 0.25, 1.0), "t_only": (0.70, 0.20, 0.10, 1.0),
              "side": (0.60, 0.25, 0.15, 1.0), "defense": (0.60, 0.25, 0.15, 0.30),
              "exit": (0.60, 0.25, 0.15, 0.70)}


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：至少留痕，**绝不抛** ✓。"""
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass


# ─────────────────────────── 开关 ───────────────────────────
def enabled() -> bool:
    return os.getenv("WOLF_WAVE_LADDER", "0").strip() in ("1", "true", "yes")


def floor_level() -> float:
    try:
        return float(os.getenv("WOLF_WAVE_LADDER_FLOOR", "0") or 0)
    except ValueError:
        return 0.0


def index_code() -> str:
    return os.getenv("WOLF_WAVE_LADDER_INDEX", "000001.SH").strip() or "000001.SH"


def _env_pin_w1() -> float:
    """`WOLF_WAVE_LADDER_W1` > 0 ⇒ 人工钉住「1 浪顶」（如钉他引用的 3674.40 做 A/B）。"""
    try:
        v = float(os.getenv("WOLF_WAVE_LADDER_W1", "0") or 0)
        return v if v > 0 else 0.0
    except ValueError:
        return 0.0


# ─────────────────────────── 数据层 ───────────────────────────
def _json_path(code: str) -> Optional[str]:
    name = "index_daily_%s.json" % code.replace(".", "_")
    for cand in (os.path.join(DATA, name), os.path.join("data", name)):
        if os.path.exists(cand):
            return cand
    return None


def _csv_path(code: str) -> Optional[str]:
    name = "%s.csv" % code
    for cand in (os.path.join(DATA, "指数数据", "index_daily", name),
                 os.path.join("data", "指数数据", "index_daily", name)):
        if os.path.exists(cand):
            return cand
    return None


def _load_json(code: str) -> List[Tuple[str, float, float, float]]:
    out: List[Tuple[str, float, float, float]] = []
    p = _json_path(code)
    if p:
        try:
            rows = (json.load(open(p, encoding="utf-8")) or {}).get("rows") or []
            for r in rows:
                try:
                    out.append((str(r[0])[:8], float(r[1]), float(r[2]), float(r[3] or 0)))
                except (TypeError, ValueError, IndexError) as _e:
                    _silent_alert("wave_ladder.py:json_row", _e)
        except Exception as _e:
            _silent_alert("wave_ladder.py:json_load", _e)
    return out


def _load_csv(code: str) -> List[Tuple[str, float, float, float]]:
    out: List[Tuple[str, float, float, float]] = []
    p = _csv_path(code)
    if p:
        try:
            import csv as _csv
            with open(p, encoding="utf-8") as fh:
                for c in _csv.DictReader(fh):
                    try:
                        d = str(c.get("trade_date"))[:10].replace("-", "")
                        cl = float(c.get("close"))
                        hi = float(c.get("high") or cl)
                        am = float(c.get("amount") or 0)
                        out.append((d, cl, hi, am))
                    except (TypeError, ValueError) as _e:
                        _silent_alert("wave_ladder.py:csv_row", _e)
        except Exception as _e:
            _silent_alert("wave_ladder.py:csv_load", _e)
    return out


def load_rows(code: Optional[str] = None) -> List[Tuple[str, float, float, float]]:
    """→ [(date8, close, high, amount)] 升序；**两源合并**（CSV 2016+ 长历史 ∪ JSON 近一年，
    同日以 JSON 为准）；取不到返回 []（fail-open）。
    长历史是**铁律② 的前提**：JSON 只回溯到 2024-11 ⇒ 看不到 2024-10-08 那个 1 浪顶（3674）。"""
    code = code or index_code()
    merged: Dict[str, Tuple[str, float, float, float]] = {}
    for r in _load_csv(code):
        merged[r[0]] = r
    for r in _load_json(code):
        merged[r[0]] = r
    return [merged[k] for k in sorted(merged)]


# ────────────── 结构位（教材「铁律②」，2026-10-15 接入） ──────────────
# 出处：中山证券投资者教育《波浪理论》https://www.zszq.com/tzzyd/article/292
#   「数浪的两条基本规则：……（二）**第四个浪的底部，不可以低于第一个浪的浪顶**」
#   「（一）第三浪永远不允许是第一至第五浪中最短的一个浪」；补充规则一＝**交替规则**。
# 意义：这就是“4浪调整”被证伪的**客观失效线** —— 比我们早期拍的“60 日新低”有依据 ✓。
def pivots(rows: Sequence[Tuple[str, float, float, float]], asof: str,
           pct: Optional[float] = None) -> List[Tuple[str, str, float, str]]:
    """**因果** zigzag 拐点（只用 ≤ asof 的数据；极值反向 ≥ pct 才确认 ⇒ 无未来函数 ✓）。
    → [(confirm_date, pivot_date, value, type)]；H 取 high、L 取 close。"""
    pct = ZIGZAG_PCT if pct is None else pct
    hist = [r for r in rows if r[0] <= str(asof)[:8]]
    seg: List[Tuple[str, str, float, str]] = []
    if len(hist) < 2:
        return seg
    ext_i, ext_v, ext_t = 0, hist[0][1], "?"
    for i in range(1, len(hist)):
        c, h = hist[i][1], hist[i][2]
        if ext_t == "?":
            if c <= ext_v * (1 - pct):
                ext_i, ext_v, ext_t = i, c, "L"
            elif c >= ext_v * (1 + pct):
                ext_i, ext_v, ext_t = i, c, "H"
            continue
        if ext_t == "H":
            if h > ext_v:
                ext_i, ext_v = i, h
            elif c <= ext_v * (1 - pct):
                seg.append((hist[i][0], hist[ext_i][0], ext_v, "H"))
                ext_i, ext_v, ext_t = i, c, "L"
        else:
            if c < ext_v:
                ext_i, ext_v = i, c
            elif c >= ext_v * (1 + pct):
                seg.append((hist[i][0], hist[ext_i][0], ext_v, "L"))
                ext_i, ext_v, ext_t = i, c, "H"
    return seg


DEGREES = [("D1", _envf("WOLF_WAVE_LADDER_D1", 0.20)), ("D2", ZIGZAG_PCT), ("D3", PCT_FINE)]
# ↑ 他的「级别」/「浪中有浪」＝ 同一序列在**不同阈值**下的拐点层级（教材：波浪等级的划分）。
SUB_LABELS = ["4-1", "4-2", "4-3", "4-4", "4-5"]
SUB_FALLING = _envf("WOLF_WAVE_LADDER_SUB_FALLING", 0.02)   # 认定「下一段已在进行」的最小回落


def degree_pivots(rows: Sequence[Tuple[str, float, float, float]],
                  asof: str) -> Dict[str, List[Tuple[str, str, float, str]]]:
    """三级拐点：D1 主级(20%) / D2 中继(10%) / D3 次级(5%) —— 对应他的「级别」。"""
    return {name: pivots(rows, asof, pct) for name, pct in DEGREES}


def sub_legs(rows: Sequence[Tuple[str, float, float, float]], asof: str,
             st: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把**主级顶之后的次级拐点**按他的编号标成 4-1…4-5（4-1＝第一段下跌、4-2＝反弹…）。

    实测（000001.SH，as-of 2026-10-14，主级顶 2026-05-14 之后）：
      4-1 = 3959.34(06-08 跌) / 4-2 = 4163.10(06-22 弹) / 4-3 = 3764.15(07-17 跌) / 4-4 = 3990.30(08-18 弹)，
      其后回落 ⇒ 当前为 **4-5 形成中**（＝他 10-15 说“下跌1浪”前的那一段）。
    他自己也是这个编号：08-04「一旦资金测试成功 **4-4** 就能走出来了」、08-11「如果不是 3900 的话那就直接
    开启 **4-5**？」⇒ 编号口径一致 ✓（4-5 走完 = 调整末腿到位 ⇒ 他的「成功4-5」，即本模块 rung1 ✓）。"""
    st = st or structural_levels(rows, asof)
    pk, pk_d = st["prior_peak"], st["prior_peak_date"]
    out: Dict[str, Any] = {"stage": "", "forming": "", "legs": [], "peak": pk, "peak_date": pk_d}
    if not pk:
        return out
    after = [p for p in pivots(rows, asof, PCT_FINE) if p[1] > pk_d][:len(SUB_LABELS)]
    for i, p in enumerate(after):
        out["legs"].append({"label": SUB_LABELS[i], "kind": "down" if p[3] == "L" else "up",
                            "date": p[1], "confirm": p[0], "value": p[2]})
    if out["legs"]:
        last = out["legs"][-1]
        out["stage"] = last["label"]
        nxt = len(out["legs"])
        close = rows[-1][1] if rows else 0.0
        if last["kind"] == "up" and nxt < len(SUB_LABELS) and close < last["value"] * (1 - SUB_FALLING):
            out["forming"] = SUB_LABELS[nxt]
    return out


def structural_levels(rows: Sequence[Tuple[str, float, float, float]], asof: str) -> Dict[str, Any]:
    """铁律② 需要的结构位（全部**因果取** ✓，取不到就 None ⇒ 上层 fail-open）：
      · origin       = 回溯 `ORIGIN_BARS` 内**最低的主级 L** ＝ 本轮主升起点；
      · wave1_top    = origin 之后**第一个主级 H** ＝「1 浪顶」⇒ **4 浪底的理论失效线**（触发降级）；
      · wave1_top_minor = 同起点之后第一个**细级** H（降一级的 1 浪顶，只作预警，不触发）；
      · prior_peak   = 最近一个主级 H（＝主级前高）；prev_peak = 再前一个主级 H（次级参考位）。
    实测（000001.SH，as-of 2026-10-14）：origin 2704.09(2024-09-13) → 主级 H 3674.40(2024-10-08)、
      prior_peak 4242.57(2026-05-13)、prev_peak 3674.40、wave1_top_minor 4029.50(2025-11-13)；
      收盘 3767.23 **仍在 3674.40 之上** ⇒ 铁律② 未破（他 10-15 的降级来自**跳空**那条规则 ✓）。"""
    z = pivots(rows, asof)
    out: Dict[str, Any] = {"origin": None, "origin_date": "", "wave1_top": None, "wave1_top_date": "",
                           "wave1_top_minor": None, "wave1_top_minor_date": "", "prior_peak": None,
                           "prior_peak_date": "", "prev_peak": None, "prev_peak_date": "", "pivots": len(z)}
    hs = [p for p in z if p[3] == "H"]
    if hs:
        out["prior_peak"], out["prior_peak_date"] = hs[-1][2], hs[-1][1]
        if len(hs) > 1:
            out["prev_peak"], out["prev_peak_date"] = hs[-2][2], hs[-2][1]
    hist = [r for r in rows if r[0] <= str(asof)[:8]]
    lo_date = hist[-ORIGIN_BARS][0] if len(hist) > ORIGIN_BARS else ""
    ls = [p for p in z if p[3] == "L" and (not lo_date or p[1] >= lo_date)]
    if ls:
        o = min(ls, key=lambda p: p[2])
        out["origin"], out["origin_date"] = o[2], o[1]
        after = [p for p in z if p[3] == "H" and p[1] > o[1]]
        if after:
            out["wave1_top"], out["wave1_top_date"] = after[0][2], after[0][1]
        afterf = [p for p in pivots(rows, asof, PCT_FINE) if p[3] == "H" and p[1] > o[1]]
        if afterf:
            out["wave1_top_minor"], out["wave1_top_minor_date"] = afterf[0][2], afterf[0][1]
    return out


# ─────────────────────────── 特征 ───────────────────────────
def features(rows: Sequence[Tuple[str, float, float, float]], asof: str) -> Optional[Dict[str, Any]]:
    """as-of 日的硬条件快照（**只用 ≤ asof 的数据** ✓）。"""
    hist = [r for r in rows if r[0] <= str(asof)[:8]]
    if len(hist) < MIN_BARS:
        return None
    d, close, high, amount = hist[-1]
    prev_close = hist[-2][1]
    win = hist[-(HI_WIN + 1):-1]                     # 前 60 根（不含当日）
    hi60_prev = max(r[1] for r in win)
    lo60_prev = min(r[1] for r in win)
    w = hist[-RALLY_WIN:]
    lo20 = min(r[1] for r in w)
    hi20 = max(r[1] for r in w)
    amt_prev = hist[-2][3]
    st = structural_levels(rows, asof)
    sub = sub_legs(rows, asof, st)
    w1 = _env_pin_w1() or st["wave1_top"]
    return {
        "date": d, "close": close, "high": high, "prev_close": prev_close, "amount": amount,
        "wave1_top": w1, "wave1_top_date": st["wave1_top_date"], "wave1_top_minor": st["wave1_top_minor"],
        "prior_peak": st["prior_peak"], "prior_peak_date": st["prior_peak_date"],
        "prev_peak": st["prev_peak"], "origin": st["origin"],
        "origin_date": st["origin_date"], "pivots": st["pivots"],
        "sub_stage": sub["stage"], "sub_forming": sub["forming"], "sub_legs": sub["legs"],
        "break_wave1": bool(w1 and close < w1),
        "hi60_prev": hi60_prev, "lo60_prev": lo60_prev, "hi20": hi20, "lo20": lo20,
        "ma60": round(sum(r[1] for r in hist[-61:-1]) / 60, 2) if len(hist) >= 61 else 0.0,
        "below_ma60": bool(len(hist) >= 61 and close < sum(r[1] for r in hist[-61:-1]) / 60),
        "new_high60": bool(close > hi60_prev),   # 严格突破：等于前高不算“创新高” ✓
        "break_low60": bool(close < lo60_prev),
        "gap_pct": round((prev_close - high) / prev_close, 4) if prev_close else 0.0,
        "gap_unrecovered": bool((prev_close - high) / prev_close >= GAP_MIN and close < prev_close),
        "gap_break": bool((prev_close - high) / prev_close >= GAP_MIN and close < prev_close
                          and len(hist) >= 61 and close < sum(r[1] for r in hist[-61:-1]) / 60),
        "break_floor": bool(floor_level() and close < floor_level()),
        "break_low20": bool(close < min(r[1] for r in hist[-21:-1])),
        "ma20": round(sum(r[1] for r in w) / len(w), 2) if w else 0.0,
        "above_ma20": bool(w and close > sum(r[1] for r in w) / len(w)),
        "low_age": int(len(w) - 1 - min(range(len(w)), key=lambda i: w[i][1])) if w else 0,
        "off_low20": round(close / lo20 - 1, 4) if lo20 else 0.0,
        "bottom_ok": bool(w and len(w) >= 20
                          and (len(w) - 1 - min(range(len(w)), key=lambda i: w[i][1])) >= LOW_AGE
                          and (close / lo20 - 1) >= OFF_LOW
                          and close > sum(r[1] for r in w) / len(w)),
        "gap_break20": bool((prev_close - high) / prev_close >= GAP_MIN and close < prev_close
                            and close < min(r[1] for r in hist[-21:-1])),
        "rally_amp": round(hi20 / lo20 - 1, 4) if lo20 else 0.0,
        "rally_new_high": bool(hi20 > hi60_prev),
        "hi20_ratio": round(hi20 / hi60_prev, 4) if hi60_prev else 0.0,
        "near_prev_high": bool(hi60_prev and hi20 >= NEAR_MIN * hi60_prev),
        "fade": round(1 - close / hi20, 4) if hi20 else 0.0,
        "shrink": bool(amt_prev and amount < amt_prev),
    }


def stage_of(f: Dict[str, Any]) -> int:
    """当日判定档（只看当日硬条件，不带上一次）→ 0..3。

    rung3（主跌1浪）**只认结构性破位**：收盘 < 主升 1 浪顶（铁律②）∨ **跳空且破 20 日低位** ∨ 关键位；
    单日跳空**只降一档**到 rung2（反抽结束 ⇒ 防守）——2026-08-19 实测教训：一跳空直接判主跌1浪，
    会把 9~10 月（他在「调整/反抽」✓）误判两个月 ✗；60 日新低退为参考量（`WOLF_WAVE_LADDER_LOW60=1` 才参与）。"""
    if (f["break_wave1"] or f["break_floor"] or f["gap_break20"]
            or (LOW60_TRIGGER and f["break_low60"])):
        return RUNG_INDEX["down1"]
    if (f["gap_unrecovered"]
            or ((not f["rally_new_high"]) and f["near_prev_high"]
                and f["rally_amp"] >= RALLY_MIN and f["fade"] >= FADE_MIN)):
        return RUNG_INDEX["w45_fail"]
    if f["new_high60"] or f["bottom_ok"]:
        return RUNG_INDEX["w45_ok"]
    return RUNG_INDEX["w4_adj"]


def _reasons(f: Dict[str, Any], idx: int) -> str:
    hits = []
    if f["gap_unrecovered"]:
        hits.append("跳空未收回(高%s<前收%s)" % (round(f["high"], 2), round(f["prev_close"], 2)))
    if f["break_wave1"]:
        hits.append("收盘%s < 主升1浪顶%s（铁律②失效，起点%s）" % (
            round(f["close"], 2), round(f["wave1_top"], 2), f["origin_date"] or "-"))
    if LOW60_TRIGGER and f["break_low60"]:
        hits.append("收盘%s破前60日低位%s" % (round(f["close"], 2), round(f["lo60_prev"], 2)))
    if f["break_floor"]:
        hits.append("跌破关键位%s" % floor_level())
    if hits:
        return "；".join(hits)
    if idx == RUNG_INDEX["w45_fail"]:
        return "近20日摸到前高%s的%s%%后回落%s%%（未创新高）" % (
            round(f["hi60_prev"], 2), round(f["hi20_ratio"] * 100, 1), round(f["fade"] * 100, 1))
    sub = ("（%s%s 段）" % (f.get("sub_stage") or "-", "→" + f["sub_forming"] if f.get("sub_forming") else ""))
    if idx == RUNG_INDEX["w45_ok"]:
        if f["new_high60"]:
            return "收盘%s创60日新高(前高%s)" % (round(f["close"], 2), round(f["hi60_prev"], 2))
        return "止跌确认：20日低点%s 冻结%d日 ∧ 距低+%s%% ∧ 站上MA20(%s)%s" % (
            round(f["lo20"], 2), f["low_age"], round(f["off_low20"] * 100, 1), round(f["ma20"], 2), sub)
    return "无破位无新高（前高%s，近20日最高%s=%s%%）⇒ 仍在调整区间%s" % (
        round(f["hi60_prev"], 2), round(f["hi20"], 2), round(f["hi20_ratio"] * 100, 1), sub)


# ─────────────────────────── 判定 ───────────────────────────
def evaluate(rows: Sequence[Tuple[str, float, float, float]], asof: str,
             prev: Optional[int] = None, latch_start: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """as-of 判定 + **单向锁**（只降不升）：`rung = max(prev, 当日档)` ✓。

    释放（**重定浪型**，两条硬条件，都对应他本人行为）：
      ① 当日创 60 日新高 —— 结构转上；
      ② 自锁账起点（`latch_start`）以来低点反抽 ≥ `RESET_RALLY` —— 空头结构被破坏。
    **没有这两条，一次破位会把档位永久锁死在主跌1浪** ✗（实测：2026-06-08 一跳空锁死后，
    7/31~10/14 全部误报主跌1浪，而他 8/05 在「按 4 接 5 做」、10/09 在吃反抽 ✓）。
    """
    f = features(rows, asof)
    if f is None:
        return None
    raw = stage_of(f)
    reset, why = False, ""
    if prev is not None and int(prev) > raw:
        if f["new_high60"]:
            reset, why = True, "创60日新高 ⇒ 重定浪型"
        elif latch_start:
            seg = [r[1] for r in rows if latch_start <= r[0] <= str(asof)[:8]]
            hi, lo = (max(seg), min(seg)) if seg else (0.0, 0.0)
            if lo and hi / lo - 1 >= RESET_RALLY:
                reset, why = True, "自低点反抽%.1f%% ⇒ 重定浪型" % ((hi / lo - 1) * 100)
    idx = raw if (reset or prev is None) else max(int(prev), raw)
    r = RUNGS[idx]
    w1, w2, w3, invest = _ALLOC.get(r["operation"], (0.6, 0.25, 0.15, 1.0))
    return {"date": f["date"], "rung_index": idx, "key": r["key"], "name": r["name"],
            "expect": r["expect"], "operation": r["operation"], "invest": invest,
            "weights": [w1, w2, w3], "day_stage": raw, "reset": reset, "reset_reason": why,
            "degraded": bool(prev is not None and idx > prev and not reset),
            "reasons": _reasons(f, idx), "features": f,
            "sub_stage": ("%s%s" % (f.get("sub_stage") or "", ("→" + f["sub_forming"]) if f.get("sub_forming") else "")),
            "levels": {"wave1_top": f["wave1_top"], "wave1_top_date": f["wave1_top_date"],
                       "wave1_top_minor": f["wave1_top_minor"], "prior_peak": f["prior_peak"],
                       "prev_peak": f["prev_peak"], "origin": f["origin"]}}


def walk(rows: Sequence[Tuple[str, float, float, float]], start: Optional[str] = None,
         end: Optional[str] = None, prev: Optional[int] = None) -> List[Dict[str, Any]]:
    """逐日回放（as-of 逐日推进，带单向锁 + 重定浪）→ 每次**降档/重定浪**都记一条 ✓。"""
    out: List[Dict[str, Any]] = []
    cur: Optional[int] = prev
    latch: Optional[str] = None
    for r in rows:
        d = r[0]
        if start and d < start:
            continue
        if end and d > end:
            break
        ev = evaluate(rows, d, cur, latch)
        if ev is None:
            continue
        if ev["reset"]:
            latch = None
        if ev["rung_index"] > 0 and (cur is None or ev["rung_index"] > cur):
            latch = d
        cur = ev["rung_index"]
        out.append(ev)
    return out


# ─────────────── 影子 / 接管（生产消费点的**唯一**入口） ───────────────
ADOPT_CACHE: Dict[str, Any] = {}


def ladder_operation(asof: Optional[str] = None) -> Optional[str]:
    """「若按浪型假设链走，今天的 operation 是什么」——**按需现算**（不依赖 artifact 是否新鲜），
    结果按 as-of 缓存；无数据/异常 ⇒ None（fail-open ✓）。"""
    key = (str(asof)[:8] if asof else "latest")
    if key in ADOPT_CACHE:
        return ADOPT_CACHE[key]
    op: Optional[str] = None
    try:
        rows = load_rows()
        if rows:
            ev = evaluate(rows, str(asof)[:8] if asof else rows[-1][0])
            op = (ev or {}).get("operation")
    except Exception as _e:
        _silent_alert("wave_ladder.py:ladder_operation", _e)
    ADOPT_CACHE[key] = op
    return op


def shadow_log(prod_op: Optional[str], lop: Optional[str], note: str = "") -> None:
    """影子双跑留痕 → `logs/wave_ladder_ab.jsonl`（同一 (日, 两个 op) 只记一次；**绝不抛** ✓）。"""
    try:
        d = time.strftime("%Y%m%d")
        key = "%s|%s|%s" % (d, prod_op, lop)
        if ADOPT_CACHE.get("logged") == key:
            return
        ADOPT_CACHE["logged"] = key
        for base in (os.environ.get("LOG_DIR", "logs"), "logs", DATA, "."):
            try:
                os.makedirs(base, exist_ok=True)
                with open(os.path.join(base, "wave_ladder_ab.jsonl"), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "date": d,
                                         "prod_op": prod_op, "ladder_op": lop,
                                         "adopted": lop if enabled() else prod_op,
                                         "switch": "on" if enabled() else "shadow", "note": note},
                                        ensure_ascii=False) + chr(10))
                return
            except OSError as _e:
                _silent_alert("wave_ladder.py:shadow_log", _e)
    except Exception as _e:
        _silent_alert("wave_ladder.py:shadow_log_outer", _e)


def adopt_operation(prod_op: Optional[str], asof: Optional[str] = None) -> Optional[str]:
    """**生产消费点唯一入口**：`WOLF_WAVE_LADDER=0`（默认）⇒ 原样返回生产 op（生产逐字不变 ✓），
    同时把「浪型链会说什么」写进影子日志；`=1` 且算得出 ⇒ 由浪型链接管；算不出 ⇒ 回落生产 op ✓。"""
    try:
        lop = ladder_operation(asof)
        shadow_log(prod_op, lop)
        if enabled() and lop:
            return lop
    except Exception as _e:                     # 兜底：入口绝不把异常带给生产 ✓
        _silent_alert("wave_ladder.py:adopt_operation", _e)
    return prod_op


def read_ladder(state_file: str = "wave_ladder.json") -> Optional[Dict[str, Any]]:
    """读影子产物（供上层/复盘）。取不到返回 None（fail-open ✓）。"""
    for cand in (os.path.join(DATA, state_file), os.path.join("data", state_file)):
        try:
            if os.path.exists(cand):
                return json.load(open(cand, encoding="utf-8"))
        except Exception as _e:
            _silent_alert("wave_ladder.py:read", _e)
    return None


def write_artifact(ev: Optional[Dict[str, Any]], state_file: str = "wave_ladder.json") -> bool:
    """落盘 `wave_ladder.json`（影子档：**只在 WOLF_WAVE_LADDER=1 时才可真影响交易** ✓）。"""
    if not ev:
        return False
    try:
        os.makedirs(DATA, exist_ok=True)
        obj = dict(ev)
        obj["switch"] = "on" if enabled() else "shadow"
        obj["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = os.path.join(DATA, state_file + ".tmp")
        json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(DATA, state_file))
        return True
    except Exception as _e:
        _silent_alert("wave_ladder.py:write", _e)
        return False


def operation_for_gate(ev: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """给上层消费的**唯一入口**：开关关着 ⇒ 恒 None（生产逐字不变 ✓）。"""
    if not enabled():
        return None
    ev = ev or read_ladder()
    return (ev or {}).get("operation") or None


if __name__ == "__main__":
    rows = load_rows()
    print("bars:", len(rows), rows[0][0] if rows else "-", "->", rows[-1][0] if rows else "-")
    anchors = ["2026-07-31", "2026-08-05", "2026-09-01", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
    cur = None
    for a in anchors:
        ev = evaluate(rows, a.replace("-", ""), cur)
        if ev is None:
            print(a, "-> 无数据(fail-open)")
            continue
        cur = ev["rung_index"]
        print("%s -> [%d]%s | op=%s invest=%s | 当日档=%d | %s" % (
            a, ev["rung_index"], ev["name"], ev["operation"], ev["invest"], ev["day_stage"], ev["reasons"]))
    if rows:
        write_artifact(evaluate(rows, rows[-1][0]))
        print("artifact:", read_ladder() is not None, "| switch:", "on" if enabled() else "shadow")
