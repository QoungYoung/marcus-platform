# -*- coding: utf-8 -*-
"""leader_v2.py — 「龙头/核心」尺子 v2：按狼大 2025-12-06 长文对齐（2026-09-21 用户拍板）

────────────────────────────────────────────────────────────────
**语料（2025-12-06 14:13 原文，全文本轮才取到）**
  · 入选前提：「这个票的走势一定要**强于板块走势**，**板块同期要优于大盘走势**（板块K线强于所属指数K线）」
  · 九个要素（他的原话顺序）：强赚钱效应、趋势领跑、基本面业绩强势、板块内体量大、
    板块产业链核心中枢或稀缺零件、题材联动性、估值便宜、政策点火、资金不断放量买盘大于卖盘并且维持5日线上
  · **风向标**要素构成：趋势领跑 > 政策点火 > 强赚钱效应 > 题材联动性 > 资金买盘>卖盘且维持5日线上
    > 产业链核心中枢/稀缺零件 > 估值便宜 > 板块内体量大 > 基本面业绩
    「**风向标就是短时期板块内最强走势个股**」⇒ 会换
  · **龙头** = 「**一定是风向标里产生**」
  · **核心标的**要素构成：政策点火 > 强赚钱效应 > 题材联动性 > **板块内体量大** > 基本面业绩强势
    > 资金买盘>卖盘且维持5日线上 > 产业链核心中枢/稀缺零件 > 趋势领跑 > 估值反转
    「核心…**一旦出现就绑定板块，不能更换，他活板块活，他死板块死**」
  · **判断板块核心标的 3 个要点**：① **板块内总市值前 5**；② 营收拐点/扭亏/增速加速（要看基本面）；
    ③ 题材联动性
  · 禁止：买**板块杂毛**；核心起不来时选杂毛补涨；在风向标/核心/龙头之间反复切换追涨

**v1（我们原来的尺子）的出入**（见台账 2026-09-21 节）：
  leader = (r60 分位 + 20日成交额分位 + **涨停数分位**)/3 —— 只覆盖九要素里的"强赚钱效应/趋势领跑"，
  体量用成交额代理（≠市值）、且**涨停数是他从未提过的因子**（奖励游资妖股）。

**v2 口径（本模块；只用回测 as-of 可得的字段）**
  硬门（他的前提 + 3 要点里可算的项）：
    G1 板块强于大盘：概念等权 ret20 > 指数 ret20
    G2 个股强于板块：个股 ret20 > 概念等权 ret20
    G3 **板块内总市值前 N**（N = 环境变量 WOLF_LEADER_V2_MV_TOPN，默认 5 ⇒ 他的要点①字面实现）
    G4 维持 5 日线上：收盘 >= MA5（他的第 6 要素的一半）
  打分（权重 = 他的要素排序，仅取可得项；缺项**记入 missing 且不给权重**）：
    政策点火(9)   ← main_line_state.catalyst[theme]（主题级）
    强赚钱效应(8) ← 个股 ret20 分位
    题材联动性(7) ← 概念等权 ret5 分位（板块自身动能）
    板块内体量大(6) ← total_mv 概念内分位
    资金买盘(4)   ← 主题主力净流入近 3 日（theme_mf_daily，代理）
    趋势领跑(1)   ← 相对强度（个股 ret20 − 概念 ret20）分位
  **缺项（诚实标注，本版算不了）**：基本面业绩强势(5)、产业链核心中枢/稀缺零件(3)、估值反转(0/最低)
    ⇒ v2 仍是"无基本面"的尺子，只是补齐了政策/联动/体量/资金/相对强度，并**去掉了涨停数**。

  **v2.1（2026-09-21 晚，数据补齐）**：语料里被标为"算不了"的三项，实测上游都可得（见台账
  「leader v2 三项缺口的取数审计」）：
    · 基本面业绩 → `jobs/bt_fund_asof.py`：fina_indicator(ts_code=) 逐票 + income(period=) 全市场
      + forecast_vip(ann_date=) 当日全市场业绩预告；**as-of 严格按 ann_date ≤ cut**（同一报告期常有
      多次修订公告，不能取最新版）。开关 WOLF_LEADER_V2_EARN，默认 0。
    · 产业链核心中枢 → DB `stock_concept_map` × `data/chain_dict_final_<theme>.json` 的
      segments(role/label/concepts) ⇒ 判定该票落在链的哪个环节（实测：立昂微=上游材料/设备、
      兆易创新=中游芯片设计、长电/通富=下游封装测试）。开关 WOLF_LEADER_V2_CHAIN，默认 0。
      ⚠️ 只覆盖 10 大主题 70 个概念（深南电路 PCB 命中 0）；语料里"**稀缺零件/不可替代性**"是
         产品级判断，**本项目无数据源**，本模块只做"环节中心度"代理，不外推该含义。
    · 估值反转 → `daily_basic(trade_date=)` 全市场 pe_ttm/pb/total_mv（1.9s/日，单次硬上限 5000 行）。
      开关 WOLF_LEADER_V2_VAL，默认 0。
  三项各自 fail-open：取不到数据 = 该项不参与打分（不给权重、不加硬门），绝不改变原有行为。

开关：WOLF_LEADER_V2=0（库内默认关 ⇒ 生产零影响；回测 pins 置 1）
      子开关 WOLF_LEADER_V2_EARN / _CHAIN / _VAL = 0（默认关，逐项灰度）
      数据目录 WOLF_FUND_TAG（缺省取 SZ_TAG；为空则三项整体跳过）
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple


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


ENV = "WOLF_LEADER_V2"

# 权重 = 他的"核心标的"要素排序（9 政策点火 > 8 强赚钱效应 > 7 题材联动 > 6 板块内体量 >
#        5 基本面业绩 > 4 资金买盘 > 3 产业链中枢 > 1 趋势领跑 > 0.5 估值反转）
# 后三项由子开关控制；关掉时不进 WEIGHTS（缺项不惩罚也不奖励）
WEIGHTS: Dict[str, float] = {
    "catalyst": 9.0,        # 政策点火
    "money_effect": 8.0,    # 强赚钱效应
    "linkage": 7.0,         # 题材联动性（板块动能）
    "size": 6.0,            # 板块内体量大（市值）
    "fund": 4.0,            # 资金买盘>卖盘 且维持5日线上（资金部分）
    "lead": 1.0,            # 趋势领跑（相对强度）
}
# 数据补齐后的三项（按他的要素序插进正确位置）
W_EARN = 5.0                # 基本面业绩强势（核心标的序第 5 位；3 要点②"要看基本面"）
W_CHAIN = 3.0               # 板块产业链核心中枢
W_VAL = 0.5                 # 估值便宜/估值反转（他排在最后一位）
MISSING: Tuple[str, ...] = (
    "板块产业链「稀缺零件/不可替代性」 — 产品级数据源缺失（现用环节中心度代理，见 chain_feat）",
    "链字典只覆盖 10 大主题 70 个概念 — 主题外个股（如 PCB/深南电路）命中 0，按缺失处理",
)


def enabled() -> bool:
    """v2 是否启用（**只看本体** `WOLF_LEADER_V2`，默认 0）—— 语义**未改** ✓。"""
    return str(os.getenv(ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


# ── ★ 2026-10-09 修「静默失效」（用户拍板「先修静默失效」）────────────────────────
#   背景（实测 ✗）：t3→t36 的臂脚本**只设子开关**（EARN/CHAIN/VAL=1）却**没设本体**
#   `WOLF_LEADER_V2` ⇒ `enabled()` 恒 False ⇒ `trend_channel` 里 `_lv2 = None` ⇒
#   子开关**全部惰性**（只在 v2 内部被读）✗ ⇒ 台账「A3 leader v2 三项全开」实际跑的
#   还是 **v1（含"涨停数 lim"因子 ⇒ 奖励游资妖股）** ✗，而且**从不报错** ✗✗。
#   本函数**不改行为**（是否启用仍由本体决定 ✓），只保证这种错配**再也不会静默** ✓。
#   ⚠️ 刻意**不**把"子开关=1"自动当作启用：那会**追溯改变仍在跑的臂**的口径 ✗；
#      要真正启用请显式设 `WOLF_LEADER_V2=1`（并在 A/B 里作为独立一臂验证 ✓）。
_SUB_SWITCHES = ("WOLF_LEADER_V2_EARN", "WOLF_LEADER_V2_CHAIN", "WOLF_LEADER_V2_VAL")
_WARNED = {"done": False}


def sub_switches_on():
    """已开的 v2 子开关（用于审计/告警）✓"""
    return [k for k in _SUB_SWITCHES if _on(k)]


def ruler() -> str:
    """当前实际生效的龙头尺子名（供日志/产物标注 ✓）"""
    return "v2" if enabled() else "v1"


def warn_if_misconfigured() -> None:
    """打印**一次**(每进程)实际生效的尺子；子开关开而本体未开 ⇒ **醒目告警** ✓（绝不抛 ✓）"""
    try:
        if _WARNED["done"]:
            return
        _WARNED["done"] = True
        if enabled():
            print("[leader_v2] 龙头尺子 = **v2**（WOLF_LEADER_V2=1 ✓ 子开关=%s）"
                  % (",".join(sub_switches_on()) or "无"), flush=True)
            return
        _subs = sub_switches_on()
        if _subs:
            print("[leader_v2] ⚠️⚠️ 龙头尺子 = **v1**（本体 WOLF_LEADER_V2 未开）"
                  "，但检测到已开的 v2 子开关：%s ✗\n"
                  "             ⇒ v2 关闭时这些子开关**全部惰性**（不生效）✗；"
                  "要真正启用请显式设 WOLF_LEADER_V2=1 ✓" % ",".join(_subs), flush=True)
        else:
            print("[leader_v2] 龙头尺子 = v1（未启用 v2）", flush=True)
    except Exception as _e:
        print("[leader_v2] 尺子告警失败: %s" % str(_e)[:80], flush=True)


def mv_topn() -> int:
    try:
        return int(os.getenv("WOLF_LEADER_V2_MV_TOPN", "5") or 5)
    except Exception:
        return 5


# ───────────────────────── 三块补齐：业绩 / 产业链中枢 / 估值（子开关，默认全关）
def _on(name: str) -> bool:
    return str(os.getenv(name, "0")).strip().lower() in ("1", "true", "yes", "on")


def earn_on() -> bool:
    return _on("WOLF_LEADER_V2_EARN")


def chain_on() -> bool:
    return _on("WOLF_LEADER_V2_CHAIN")


def val_on() -> bool:
    return _on("WOLF_LEADER_V2_VAL")


def fund_tag() -> str:
    return (os.getenv("WOLF_FUND_TAG") or os.getenv("SZ_TAG") or "").strip()


def _chain_val(m: Dict[str, Any]) -> Optional[float]:
    """产业链项 = 0.5×在链上 + 0.5×环节稀缺度分位（未覆盖 ⇒ None）。"""
    on = chain_feat(m.get("_chain"))
    if on is None:
        return None
    sc = (m.get("_v2p") or {}).get("chain_scarcity")
    return 0.5 * on + 0.5 * (float(sc) if sc is not None else 0.5)


def weights() -> Dict[str, float]:
    """当前生效权重（含已开启的补齐项）。"""
    w = dict(WEIGHTS)
    if earn_on():
        w["earnings"] = W_EARN
    if chain_on():
        w["chain"] = W_CHAIN
    if val_on():
        w["valuation"] = W_VAL
    return w


def _fund_mod():
    """惰性加载取数层（jobs/bt_fund_asof.py）；不可用返回 None（fail-open）。"""
    import importlib
    try:
        return importlib.import_module("bt_fund_asof")
    except ImportError as _e_sil1:
        _silent_alert("leader_v2.py:146", _e_sil1)
    import pathlib
    for p in pathlib.Path(__file__).resolve().parents:
        cand = p / "jobs" / "bt_fund_asof.py"
        if cand.exists():
            sys.path.insert(0, str(p / "jobs"))
            try:
                return importlib.import_module("bt_fund_asof")
            except Exception:
                return None
    return None


def earn_feat(state: Optional[Dict[str, Any]]) -> Optional[float]:
    """业绩状态 → 0–1。good(预增/扭亏/营收+净利双增)=1.0  flat=0.5  bad=0.0  未知=None"""
    if not state or not state.get("ok"):
        return None
    return {"good": 1.0, "flat": 0.5, "bad": 0.0}.get(str(state.get("flag")), None)


def chain_feat(state: Optional[Dict[str, Any]]) -> Optional[float]:
    """产业链「在链上」→ 1.0；链字典未覆盖 → None（缺失，不参与打分）。

    ⚠️ 2026-09-21 修正：原先用"命中环节数≥2 ⇒ 1.0"做中枢度，补链后**饱和**了 —— 概念一多，
      连立讯精密(16 环节)/科大讯飞(17 环节)/紫光股份(12 环节)全变 1.0，等于奖励"概念堆砌"，
      与语料「核心中枢或**稀缺零件**」正好相反。
      现改为两块各占一半（见 chain_val）：① 在链上（本函数，0/1）② **环节稀缺度** =
      命中环节里最窄那个的成员数，在当日候选池里取分位（窄者高）。②是**参数无关**的
      （不设固定阈值），语义直接对应"稀缺零件"。
    """
    if not state or not state.get("ok"):
        return None
    return 1.0


def val_feat(state: Optional[Dict[str, Any]]) -> Optional[float]:
    """估值 → 0–1。便宜=1.0  中性=0.5  贵=0.0  未知=None"""
    if not state or not state.get("ok"):
        return None
    return {"cheap": 1.0, "flat": 0.5, "rich": 0.0}.get(str(state.get("flag")), None)


_FUND_MEMO: Dict[Any, Any] = {}          # (tag, day, symbol) -> 三块状态；同进程内同一 as-of 只取一次


def attach_fund(members: Sequence[Dict[str, Any]], day: str,
                tag: Optional[str] = None, name_of=None) -> int:
    """**就地**给成员补 _earn/_val/_chain 三块状态；返回补齐条数。
    只在对应子开关打开时取数；tag 为空 / 取数失败 / 数据缺失 ⇒ 该成员该项保持 None（fail-open）。"""
    if not (earn_on() or chain_on() or val_on()):
        return 0
    F = _fund_mod()
    if F is None:
        return 0
    tg = (tag if tag is not None else fund_tag())
    if not tg:
        return 0
    n = 0
    for m in members:
        sym = m.get("symbol") or m.get("ts_code")
        if not sym:
            continue
        try:
            ck = (tg, day, str(sym))
            hit = _FUND_MEMO.get(ck)
            if hit is None:
                nm = name_of(sym) if callable(name_of) else (m.get("name") or "")
                hit = {
                    "earn": F.earnings_state(sym, day, tg) if earn_on() else None,
                    "val": F.val_state(sym, day, tg) if val_on() else None,
                    "chain": F.chain_state(code=sym, name=nm) if chain_on() else None,
                }
                _FUND_MEMO[ck] = hit           # 同一票常在多个概念分组里出现 ⇒ 必须记忆，否则重复取数
            if hit.get("earn") is not None:
                m["_earn"] = hit["earn"]
            if hit.get("val") is not None:
                m["_val"] = hit["val"]
            if hit.get("chain") is not None:
                m["_chain"] = hit["chain"]
            n += 1
        except Exception as _e_sil2:
            _silent_alert("leader_v2.py:227", _e_sil2)
            continue
    return n


def _pct_rank(vals: Sequence[float]) -> List[float]:
    """升序百分位 0–1，并列取平均名次（与 pipeline 同口径）。"""
    n = len(vals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: vals[i])
    rk = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            rk[order[k]] = avg / n
        i = j + 1
    return rk


def hard_gates(stock: Dict[str, Any], ctx: Dict[str, Any],
               mv_topn_n: Optional[int] = None) -> Tuple[bool, List[str]]:
    """他的前提 + 3 要点里可算的项。返回 (是否全部满足, 未通过原因列表)。"""
    miss: List[str] = []
    try:
        s20 = float(stock.get("ret20")) if stock.get("ret20") is not None else None
        c20 = float(ctx.get("concept_ret20")) if ctx.get("concept_ret20") is not None else None
        i20 = float(ctx.get("index_ret20")) if ctx.get("index_ret20") is not None else None
    except Exception:
        s20 = c20 = i20 = None
    if c20 is not None and i20 is not None and not (c20 > i20):
        miss.append("G1 板块不强于大盘(%.2f<=%.2f)" % (c20, i20))
    if s20 is not None and c20 is not None and not (s20 > c20):
        miss.append("G2 个股不强于板块(%.2f<=%.2f)" % (s20, c20))
    n = int(mv_topn_n if mv_topn_n is not None else mv_topn())
    rank = stock.get("mv_rank")
    if rank is not None and n > 0 and int(rank) > n:
        miss.append("G3 非板块内总市值前%d(第%d)" % (n, int(rank)))
    close, ma5 = stock.get("close"), stock.get("ma5")
    if close is not None and ma5 is not None and not (float(close) >= float(ma5)):
        miss.append("G4 未维持5日线上(%.2f<%.2f)" % (float(close), float(ma5)))
    # G5 基本面业绩（他的 3 要点②"营收拐点/扭亏/增速加速（要看基本面）"）
    #    只在业绩块开关打开且**确实取到数据**时才判；数据缺失一律放行（fail-open）。
    ef = stock.get("earn_flag")
    if earn_on() and ef == "bad":
        miss.append("G5 基本面恶化(%s)" % (stock.get("earn_note") or "首亏/续亏/大幅预减"))
    return (len(miss) == 0), miss


def score(feats: Dict[str, Optional[float]]) -> float:
    """按可得项加权归一（缺项跳过，不惩罚也不奖励）。feats 的值 = 分位 0–1。"""
    num = 0.0
    den = 0.0
    for k, w in weights().items():
        v = feats.get(k)
        if v is None:
            continue
        num += float(v) * w
        den += w
    return round(num / den, 4) if den > 0 else 0.0


def rank_concept(members: Sequence[Dict[str, Any]], ctx: Dict[str, Any],
                 index_ret20: Optional[float] = None) -> List[Dict[str, Any]]:
    """**纯函数**：对一个概念的成员按 v2 打分并排序（就地写 _v2_score / _v2_gate / _v2_missing）。"""
    if not members:
        return []
    ctx = dict(ctx or {})
    if index_ret20 is not None:
        ctx["index_ret20"] = index_ret20
    for src, dst in (("ret20", "money_effect"), ("mv", "size"), ("ret5", "linkage")):
        vals = [(m.get(src) if m.get(src) is not None else -1e9) for m in members]
        for m, v in zip(members, _pct_rank(vals)):
            m.setdefault("_v2p", {})[dst] = v
    # 环节稀缺度：环节宽度（成分股数）**升序**分位 ⇒ 越窄越接近"稀缺零件"（无固定阈值）
    pres = [(i, (members[i].get("_chain") or {}).get("width")) for i in range(len(members))]
    pres = [(i, float(w)) for i, w in pres if w]
    if pres:
        rk = _pct_rank([-w for _i, w in pres])
        for (i, _w), r in zip(pres, rk):
            members[i].setdefault("_v2p", {})["chain_scarcity"] = r
    rel = [((m.get("ret20") or 0.0) - (ctx.get("concept_ret20") or 0.0)) for m in members]
    for m, v in zip(members, _pct_rank(rel)):
        m.setdefault("_v2p", {})["lead"] = v
    mv_sorted = sorted(members, key=lambda m: -(m.get("mv") or 0.0))
    for i, m in enumerate(mv_sorted, start=1):
        m["_v2_mv_rank"] = i
    out: List[Dict[str, Any]] = []
    for m in members:
        feats = {
            "catalyst": m.get("catalyst"),
            "money_effect": m["_v2p"].get("money_effect"),
            "linkage": m["_v2p"].get("linkage"),
            "size": m["_v2p"].get("size"),
            "fund": m.get("fund"),
            "lead": m["_v2p"].get("lead"),
            "earnings": earn_feat(m.get("_earn")),
            "chain": _chain_val(m),
            "valuation": val_feat(m.get("_val")),
        }
        _e = m.get("_earn") or {}
        ok, why = hard_gates({"ret20": m.get("ret20"), "mv": m.get("mv"), "close": m.get("close"),
                              "ma5": m.get("ma5"), "mv_rank": m.get("_v2_mv_rank"),
                              "earn_flag": _e.get("flag"),
                              "earn_note": ((_e.get("ftype") or "") + " " + str(_e.get("np_yoy"))).strip()},
                             ctx)
        m["_v2_score"] = score(feats)
        m["_v2_gate"] = bool(ok)
        m["_v2_missing"] = why
        out.append(m)
    out.sort(key=lambda m: (-(1 if m.get("_v2_gate") else 0), -(m.get("_v2_score") or 0.0)))
    return out
