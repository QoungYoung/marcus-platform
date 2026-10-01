# -*- coding: utf-8 -*-
"""stock_confirm_judge.py — 个股级确认链(三层联动之三): fusion TOP3 主题全量概念成分 confirm_chain → 确认比例
2026-09-07 数据层重构: 逐只 ts_code daily → 逐交易日(trade_cal) 全市场批量(daily trade_date, 5548行/次 0.1s)
2026-09-13: 数据源由 gzcloud 代理改为 datahubco(基础接口)+promax(聚合) 中继 (core/tushare_relay.py)
70个交易日×0.1s≈1分钟拉全缓存, 取代 360+ 次逐只请求(原~30min); 概念优先级(光模块/CPO/算力/AI应用 先行)+全量概念。
输出: data/stock_confirm_result.json (平铺 {概念:{theme,n,confirm,ratio,stocks}})
"""
import json, os, sys, time
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from confirm_chain import confirm_chain


# ── 个股逐日资金流 as-of（2026-09-22 用户："停掉 T6 立刻改完再重起"）────────────────────
#   背景：本文件原先写死 `confirm_chain(ser, None, v)` ⇒ S1 的「抛压减弱(net_stop)」从未参与判级，
#   而它驱动的正是 253/254 低吸腿的候选池（兆易创新 0408 被判「下跌中」的机制之一）。
#   数据：`data/_bt_fund/_ref/moneyflow/<6位码>.json`（tushare moneyflow 逐票拉，as-of 按 trade_date ≤ 当日）。
#   开关 `WOLF_STAGE_NET_ASOF`：**库内默认 0**（生产逐位不变）；取不到 → None → confirm_chain 行为与修复前一致（fail-open）。
_NET_ON = str(os.getenv("WOLF_STAGE_NET_ASOF", "0")).strip().lower() in ("1", "true", "yes", "on")


def _fund_mod():
    """惰性加载取数层（jobs/bt_fund_asof.py）；不可用返回 None（fail-open）。"""
    import importlib
    try:
        return importlib.import_module("bt_fund_asof")
    except ImportError:
        pass
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


def _net_for(ts_code, upto):
    """该票 ≤ upto 的逐日净流入 Series；开关关 / 无缓存 → None（fail-open，与修复前逐位一致）。"""
    if not _NET_ON:
        return None
    m = _fund_mod()
    if m is None or not hasattr(m, "net_series"):
        return None
    try:
        return m.net_series(ts_code, upto=upto)
    except Exception:
        return None

DATA = os.environ.get("DATA_DIR", "/app/data")
DB = os.path.join(DATA, "stock_pool.db")
MAX_STOCKS = int(os.getenv("STOCK_CONFIRM_MAX", "10"))
# 取数上限（仅"开启排序"时用；要能覆盖大概念的全部成员，如 光通信模块 111 只 / PCB 200 只）
MAX_FETCH = int(os.getenv("STOCK_CONFIRM_FETCH", "400"))
# 候选成员排序口径（账本 §9.48/§9.53；**库内默认空 = 逐字旧行为**）：
#   "" / "none"  = 旧行为（`LIMIT 10` + 数据库物理序 ⇒ 任意且不可复现）
#   "dist20h"    = 距 20 日收盘高点由近到远（「方向内选强、绝不后排」的可操作实现；四个月回放全正）
#   "lead_cond"  = 大盘 5 日跌（共振回调）⇒ 按 r5 降序（抗跌优先）；否则退化为 dist20h
#   "auto"       = lead_cond 的别名（同一条：条件切换）
ORDER_MODE = str(os.getenv("STOCK_CONFIRM_ORDER", "") or "").strip().lower()


# ── F2「选股域 ⊆ 执行域」**上移**到候选取数（2026-09-25 用户：「打开 F2，看能进名单吗」）────────
#   问题：`ORDER BY dist20h LIMIT 10` 发生在**板块过滤之前** ⇒ 10 个名额会被"账户买不了"的票占掉。
#     实测（账本 §9.97）新能源主线日进入判级的候选：主板 48%、北交所/科创板 27%、创业板 26%
#     ⇒ **超过一半名额是买不了的票** ⇒ 龙头（比亚迪/天赐材料/石大胜华…）连候选都进不去 ✗
#   本段：在**排序取前 10 之前**按账户口径剔除无权限板块。
#   开关复用 F2 `WOLF_PICK_BOARD_PREFILTER`（**库内默认 0 ⇒ 旧行为逐字不变**，生产零影响）；
#   口径与 `jobs/rotation_switch_arm.board_ok` 一致（cyb=创业板、kcb=科创板、bj=北交所）。
#   离线反事实（账本 §9.98）：先过滤再排序 ⇒ 无权限 4 只 37 次占位归零，可交易龙头进榜次数
#     石大胜华 12→**23**、天赐材料 4→**13**、比亚迪 4→5 ✓
BOARD_EXCLUDE = {x.strip() for x in os.getenv("WOLF_PICK_BOARD_EXCLUDE", "cyb,bj,kcb").split(",") if x.strip()}


def junk_before_rank_on() -> bool:
    """「窄杂毛门」上移到**排名之前**（`WOLF_JUNK_BEFORE_RANK`，库内默认 0 = 旧行为逐字不变）。

    用户 2026-09-25：「窄杂毛门提前，过了这个门才能进排名」。
    量化（账本 §9.123）：T26 判级名单 2,133 个占位里**杂毛占位 86 个（4.0%）**（与该门文档的
    "影响面 4%" 吻合 ✓）；**0107「数据中心」4 个名额里 2 个是杂毛**（002025/002544 ✗）⇒
    一半名额被浪费 ✗ ⇒ 门前移后杂毛不再占名额 ✓（低吸腿阶段那道门**保留**做双保险 ✓）。
    """
    return str(os.getenv("WOLF_JUNK_BEFORE_RANK", "0")).strip().lower() in ("1", "true", "yes", "on")


def pullback_slots() -> int:
    """「回调强票」预留名额数 K（`WOLF_PICK_PULLBACK_SLOTS`，**库内默认 0 = 关** ✓）。

    用户 2026-09-25 拍板（两步方案之②）：「让 `dist20h∈[0.85,0.95]` 的强票也能进名单」。
    依据：沪电股份 002463 二月正是「**回调中的老龙头**」（dist20h 0.86~0.95 ✗），
    因排序口径「越接近 20 日高点越优先」而在 PCB（约 200 只）里**挤不进 10 个名额** ✗；
    而语料「低位方向：找辨识度最高的老龙头**埋伏**」✓ 要求这类票**进得来** ✓
    ⚠️ 单独开启无用（§9.140：+5 中位 −0.94%/45% ✗）——必须与"埋伏腿的时间/止损纪律"配套 ✓
    """
    try:
        return max(0, int(os.getenv("WOLF_PICK_PULLBACK_SLOTS", "0") or 0))
    except Exception:
        return 0


def pullback_ratio_ok(code, close, day_n=60) -> bool:
    """「回调中的强票」：r60 ≥ 20% ∧ dist20h ∈ [0.85, 0.95]（纯函数 ✓，可单测）。"""
    try:
        ser = close[code].dropna()
        if len(ser) < day_n + 1:
            return False
        cl = [float(x) for x in ser.values[-(day_n + 1):]]   # 61 个收盘 = r60 ✓
        r60 = (cl[-1] / cl[0] - 1) * 100
        d20 = cl[-1] / max(cl[-20:])
        return (r60 >= 20.0) and (0.85 <= d20 <= 0.95)
    except Exception:
        return False


def board_prefilter_on() -> bool:
    """F2 开关（库内默认 0）。"""
    return os.getenv("WOLF_PICK_BOARD_PREFILTER", "0").strip() == "1"


def board_ok(code: str) -> bool:
    """账户是否有该板块的交易权限（口径同 `rotation_switch_arm.board_ok` 的默认值）。"""
    s = str(code or "").strip().upper()
    if not s:
        return True
    if "." in s:                       # ts_code（300750.SZ）
        c, m = s.split(".")[0], s.split(".")[-1]
    elif len(s) > 6 and s[:2] in ("SH", "SZ", "BJ"):    # xq 形态（SZ300750 / BJ920237）
        m, c = s[:2], s[2:]
    else:                              # 裸 6 位（按首位推断）
        c = s
        m = "SH" if c.startswith(("6", "5")) else "SZ"
    if "cyb" in BOARD_EXCLUDE and m == "SZ" and c.startswith(("300", "301")):
        return False
    if "kcb" in BOARD_EXCLUDE and m == "SH" and c.startswith(("688", "689")):
        return False
    if "bj" in BOARD_EXCLUDE and (m == "BJ" or c.startswith(("4", "8", "920"))):
        return False
    return True


def order_members(codes, close, mode=None, lookback=20, vol=None):
    """按口径给概念成员排序（纯函数，确定性）。

    返回排序后的代码列表；**任何数据不足/异常 ⇒ 退化为旧的物理序不可得，改用代码序**（可复现优先）。
    指标只用 `close`（90 日缓存已有），不需要新数据依赖：
      · dist20h：close[-1] / max(close[-20:])  ⇒ **越大越优先**（越接近 20 日高点）
      · lead_cond：若大盘 5 日收益 < 0 ⇒ 用 close[-1]/close[-6]-1（r5）降序；否则同 dist20h
    同分用代码序（`sorted` 稳定 + 显式 tiebreak），保证同一天同输入同输出。
    """
    mode = (ORDER_MODE if mode is None else str(mode or "").strip().lower())
    if mode in ("", "none", "off"):
        return list(codes)
    # ⚠️ 2026-09-30（账本 §9.404 ✓ 用户「先改2」✓）：**人气/弹性优先** ✓
    #   量化依据（§9.401 ✓）：人气/弹性龙头的**右尾肥 2~2.6 倍**（≥+30% 3.9% vs 1.5% ✓）
    #   代理 ✓（只用已有数据 ✓）：弹性＝vol[-1]/mean(vol[-60:]) ✓；动量＝close[-1]/close[-21]-1 ✓；
    #                              大涨天数＝近 20 日「日涨幅 ≥9%」的计数 ✓
    #   安全 ✓：数据不足/异常 ⇒ **落回原 dist20h 分支** ✓（fail-open ✓）
    if mode == "pop_lead":
        try:
            import pandas as _pd2
            _cols = set(getattr(close, "columns", []))
            _val = [c for c in codes if c in _cols]
            if len(_val) >= 2:
                _sub = close[_val].dropna(how="all")
                _z = {}
                if vol is not None and len(getattr(vol, "columns", [])):
                    _v = vol[[c for c in _val if c in set(vol.columns)]].dropna(how="all")
                    if len(_v) >= 21:
                        _base = _v.tail(60).mean()
                        _elas = (_v.iloc[-1] / _base).replace([float("inf"), float("-inf")], float("nan"))
                        _z["elas"] = _elas
                if len(_sub) >= 21:
                    _mom = (_sub.iloc[-1] / _sub.iloc[-21] - 1)
                    _z["mom"] = _mom
                    _ret = _sub.pct_change()
                    _big = (_ret.tail(20) >= 0.09).sum()
                    _z["big"] = _big
                if _z:
                    _w = {"elas": 0.5, "mom": 0.3, "big": 0.2}
                    _sc = {}
                    for _c in _val:
                        _tot = 0.0
                        _ok = True
                        for _k, _ser in _z.items():
                            _v0 = _ser.get(_c)
                            if _v0 is None or _pd2.isna(_v0):
                                _ok = False
                                break
                            _sd = float(_ser.std() or 0)
                            _tot += _w[_k] * ((float(_v0) - float(_ser.mean())) / _sd) if _sd > 0 else 0.0
                        if _ok:
                            _sc[_c] = _tot
                    if _sc:
                        _ord = sorted(_sc, key=lambda c: (-_sc[c], str(c)))
                        return _ord + sorted([c for c in codes if c not in _sc])
        except Exception as _e2:
            print("[stock_confirm] pop_lead 排序失败（退回 dist20h）: %s" % str(_e2)[:80], file=sys.stderr)
    cols = set(getattr(close, "columns", []))
    valid = [c for c in codes if c in cols]
    if not valid:
        return list(codes)
    try:
        import pandas as _pd
        sub = close[valid].dropna(how="all")
        if len(sub) < 2:
            return sorted(valid) + [c for c in codes if c not in cols]
        # 大盘 5 日（等权）——"共振回调"的判定
        tail = sub.tail(6)
        if len(tail) >= 6:
            mkt_r5 = float((tail.iloc[-1] / tail.iloc[0] - 1).mean())
        else:
            mkt_r5 = 0.0
        if mode.startswith("lead") and mkt_r5 < 0 and len(sub) >= 6:
            score = (sub.iloc[-1] / sub.iloc[-6] - 1)
        else:
            win = sub.tail(int(lookback))
            score = sub.iloc[-1] / win.max()
        score = score.replace([float("inf"), float("-inf")], float("nan"))
        order = sorted(score.dropna().index.tolist(), key=lambda c: (-float(score[c]), str(c)))
        rest = [c for c in codes if c not in order]
        return order + sorted(rest)
    except Exception as ex:
        print("[stock_confirm] 排序失败（退回代码序）: %s" % str(ex)[:70], file=sys.stderr)
        return sorted(valid) + [c for c in codes if c not in cols]
MAX_CONCEPTS = int(os.getenv("STOCK_CONFIRM_CONCEPTS", "99"))
PRIORITY_CONCEPTS = [x.strip() for x in os.getenv(
    "STOCK_CONFIRM_PRIORITY",
    "光通信模块,CPO概念,算力概念,AI应用,人工智能,DeepSeek概念,液冷概念,数据中心,ChatGPT概念,AI智能体").split(",") if x.strip()]


def _relay():
    """加载 core/tushare_relay.py（datahubco + promax，替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for p in pathlib.Path(__file__).resolve().parents:
        if (p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def _gz(api, **p):
    """Tushare 中继查询（返回 items 行列表，字段顺序 = fields 参数）。"""
    try:
        _fields, items = _relay().relay_items(api, fields="ts_code,trade_date,close,vol", **p)
        return items or []
    except Exception as e:
        print("TUSHARE_FAIL", api, str(e)[:120], file=sys.stderr)
        return []


def _trade_days():
    _fields, items = _relay().relay_items(
        "trade_cal", exchange="SSE", start_date="20260601",
        end_date=time.strftime("%Y%m%d"), fields="cal_date,is_open")
    days = sorted(x[0] for x in (items or []) if x[1] == 1)
    return days[-90:] if len(days) > 90 else days


def _fetch_market(days):
    """逐交易日全市场批量 → (close_df, vol_df) index=datetime 交易日期, columns=ts_code"""
    recs = []
    for d in days:
        for it in _gz("daily", trade_date=d):
            recs.append({"d": d, "ts": it[0], "close": float(it[2] or 0), "vol": float(it[3] or 0)})
        if len(recs) % 10000 == 0:
            print(f"[stock_confirm] fetched {len(recs)} rows", file=sys.stderr)
    df = pd.DataFrame(recs)
    df["dt"] = pd.to_datetime(df["d"], format="%Y%m%d")
    close = df.pivot_table(index="dt", columns="ts", values="close", aggfunc="last").sort_index()
    vol = df.pivot_table(index="dt", columns="ts", values="vol", aggfunc="last").sort_index()
    return close, vol


def main():
    # EXEC_PROBE
    CONFIRM_TOP_N = int(os.getenv("CONFIRM_TOP_N", "3"))
    try:
        import fusion_mainline as fm
        st = json.load(open(os.path.join(DATA, "main_line_state.json"), encoding="utf-8"))
        # 2026-09-13: 主题来源＝方向层池判定（mainline_top_themes）；fusion 已随研报线删除，不再作兜底
        themes = None
        try:
            from mainline_confirm_state import mainline_top_themes
            _gt = mainline_top_themes(CONFIRM_TOP_N)
            if _gt: themes = _gt[0]
        except Exception:
            pass
        if not themes:
            # 2026-09-13：fusion 已随研报线删除 → 直接落方向层主线
            themes = [st.get("main_line") or "AI/算力/科技"]
    except Exception:
        fm = None
        themes = ["AI/算力/科技"]
    print("[stock_confirm] TOP确认主题:", themes, file=sys.stderr)
    if not os.path.exists(DB):
        print("[stock_confirm] NO stock_pool.db", file=sys.stderr)
        return
    t0 = time.time()
    days = _trade_days()
    close, vol = _fetch_market(days)
    print(f"[stock_confirm] 全市场缓存 {len(days)}日 x {close.shape[1]}票  {time.time()-t0:.0f}s", file=sys.stderr)
    con = None
    import sqlite3
    con = sqlite3.connect(DB)
    out = {}
    for mt in themes:
        try:
            names = fm.THEME_CONCEPTS.get(mt, []) if fm is not None else []
        except Exception:
            names = []
        if not names:
            names = ["人工智能", "算力概念", "CPO概念", "光通信模块", "液冷概念"]
        names = [n for n in PRIORITY_CONCEPTS if n in names] + [n for n in names if n not in PRIORITY_CONCEPTS]
        for cname in names[:MAX_CONCEPTS]:
            try:
                cur = con.cursor()
                if ORDER_MODE not in ("", "none", "off"):
                    # 账本 §9.48：旧口径 = `LIMIT 10` + 无 ORDER BY ⇒ 取哪 10 只**任意且不可复现**
                    # （长飞光纤在「光通信模块」物理序第 46、中国巨石在「PCB」第 37 ⇒ 永远取不到）。
                    # 开排序后：先取全量成员（上限 MAX_FETCH），再按口径排序取前 MAX_STOCKS。
                    cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?",
                                (cname, MAX_FETCH))
                    _all = [r[0] for r in cur.fetchall()]
                    if board_prefilter_on():        # F2 上移：先剔无权限板块，再排序取前 10
                        _all = [c for c in _all if board_ok(c)]
                    # ── 「窄杂毛门」上移到**排名之前**（`WOLF_JUNK_BEFORE_RANK`，库内默认 0）──
                    #   用户 2026-09-25：「窄杂毛门提前，过了这个门才能进排名」✓
                    #   量化（账本 §9.123）：杂毛占判级名额 4.0%（86/2133 个占位）；
                    #   0107「数据中心」4 个名额里 2 个是杂毛 ⇒ 名额浪费一半 ✗
                    if junk_before_rank_on():
                        try:
                            import lowdip_junk_gate as _jg2
                            _d8j = os.getenv("WOLF_ASOF_DAY") or time.strftime("%Y%m%d")
                            _kept_j, _drop_j = [], []
                            for _c in _all:
                                _blk_j, _why_j = _jg2.blocked(_c, day=_d8j)
                                if _blk_j:
                                    _drop_j.append((_c, str(_why_j)[:44]))
                                else:
                                    _kept_j.append(_c)
                            if _drop_j:
                                print("[stock_confirm] 杂毛门(排名前)剔除 %d 只: %s"
                                      % (len(_drop_j), _drop_j[:4]), file=sys.stderr)
                            _all = _kept_j
                        except Exception as _e_j:
                            print("[stock_confirm] 杂毛门(排名前)异常(放行): %s" % str(_e_j)[:90],
                                  file=sys.stderr)
                    _ordered = order_members(_all, close, vol=vol)
                    # ⚠️ 2026-09-30（账本 §9.405）：**一次性探针** ✓ —— 把真正生效的口径打出来 ✓
                    #   因 launcher 会覆盖 pins ✗、`/proc/environ` 读不到 ✗ ⇒ 只能让代码自报 ✓
                    try:
                        import os as _os5
                        if str(_os5.getenv("WOLF_CONFIRM_MODE_PROBE", "0")).strip().lower() in ("1", "true", "yes", "on"):
                            print("[stock_confirm] order_mode=%s ✓ concept=%s members=%d top=%s"
                                  % (ORDER_MODE, cname, len(_all), _ordered[:5]), file=sys.stderr)
                    except Exception:
                        pass
                    _K = pullback_slots()
                    if _K > 0:
                        # 用户两步方案之②：为「回调中的强票」预留 K 个名额 ✓
                        #   （`dist20h∈[0.85,0.95] ∧ r60≥20%`；从**名额之外**剩下的池里按 r60 降序挑 ✓）
                        _head = _ordered[:max(0, MAX_STOCKS - _K)]
                        _rest = [c for c in _ordered[max(0, MAX_STOCKS - _K):]
                                 if pullback_ratio_ok(c, close)]
                        def _r60(_c):
                            try:
                                _v = [float(x) for x in close[_c].dropna().values[-61:]]
                                return (_v[-1] / _v[0] - 1) * 100 if len(_v) >= 61 else -999
                            except Exception:
                                return -999
                        _pb = sorted(_rest, key=lambda c: -_r60(c))[:_K]
                        if _pb:
                            print("[stock_confirm] 回调名额：为 %d 只强票预留（%s）"
                                  % (len(_pb), ", ".join(str(x)[:6] for x in _pb[:4])), file=sys.stderr)
                        # 2026-09-26：把「回调/埋伏候选」写成 as-of 文件 ⇒ 08:18 布腿据此发**埋伏腿**
                        #   （`wolf_ambush_buy`）✓ —— 它是"低位先手建仓"语义 ✓，必须豁免
                        #   「低吸只在趋势票上摊薄」那道闸 ✗（否则埋伏腿被自己的闸挡死 ✗）
                        # 2026-09-26 修：**合并累加**而不是覆盖 —— 实测当天有多趟判级（不同主题集合 ✓），
                        #   后一趟的空 `_pb` 会把先写好的名单**覆盖成空** ✗
                        #   （文件里 `{"day":"","symbols":[]}` 而日志明明选了 3 只 ✗ ⇒ 埋伏腿永远拿不到 ✓）
                        try:
                            import json as _js2
                            _ap = os.path.join(os.environ.get("DATA_DIR") or ".", "ambush_candidates.json")
                            _old = []
                            try:
                                _old = list((_js2.load(open(_ap, encoding="utf-8")) or {}).get("symbols") or [])
                            except Exception:
                                _old = []
                            _merged = list(dict.fromkeys(list(_old) + [str(x) for x in _pb]))
                            _js2.dump({"day": os.getenv("WOLF_ASOF_DAY") or "",
                                       "symbols": _merged},
                                      open(_ap, "w", encoding="utf-8"), ensure_ascii=False)
                            print("[stock_confirm] 埋伏候选落盘：本趟 %d 只 ⇒ 累计 %d 只"
                                  % (len(_pb), len(_merged)), file=sys.stderr)
                        except Exception as _ae:
                            print("[stock_confirm] 埋伏候选落盘失败: %s" % str(_ae)[:70], file=sys.stderr)
                        codes = _head + _pb
                    else:
                        codes = _ordered[:MAX_STOCKS]
                else:
                    cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?",
                                (cname, MAX_STOCKS))
                    codes = [r[0] for r in cur.fetchall()]
            except Exception as ex:
                print("[stock_confirm] concept err", cname, str(ex)[:60], file=sys.stderr)
                continue
            stocks = []
            for ts_code in codes:
                if ts_code not in close.columns:
                    continue
                ser = close[ts_code].dropna()
                if len(ser) < 40:
                    continue
                v = vol[ts_code].reindex(ser.index)
                _net = _net_for(ts_code, days[-1] if days else None)
                cc = confirm_chain(ser, _net, v)
                stocks.append({"code": ts_code, "stage": cc["stage"]})
            # 主营方向校验（dsh 无思考模式；开关 WOLF_THEME_MEMBER_CHECK，回测默认开、生产默认关）
            try:
                import os as _os2
                # 2026-09-19 用户澄清：本闸＝拦"概念错配"（电子城/华泰股份/科德教育…），
                # 是语料要求的正宗度校验、数据缺口用 dsh 代理 ⇒ 库内默认关、回测默认开
                _MEMBER_ON = str(_os2.getenv("WOLF_THEME_MEMBER_CHECK",
                                             "1" if _os2.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
                if _MEMBER_ON and stocks:
                    from theme_member_llm import is_member as _is_member
                    _codes = [str(x["code"]) for x in stocks]
                    _q = "SELECT ts_code, concept_name FROM stock_concept_map WHERE ts_code IN (%s)" % (",".join(["?"] * len(_codes)))
                    cur.execute(_q, _codes)
                    _own = {}
                    for _tc, _cn in cur.fetchall():
                        _own.setdefault(str(_tc), []).append(str(_cn))
                    # 2026-09-20：**批量预热**这一池（一次请求判 ≤N 只），把逐票串行外呼降到 1 次。
                    #   背景：13101 是串行服务（单发 2–7s），逐票路径一天几十票 ⇒ 几分钟；
                    #   且旧版 `bt_local_pro` 的 urlopen 包装把 POST 降级成 GET ⇒ 逐票外呼全 405。
                    try:
                        import theme_member_batch as _MB
                        if _MB.enabled() and _own:
                            _MB.warm([(str(x["code"]), _own.get(str(x["code"]), [])) for x in stocks], mt)
                    except Exception as _be:
                        print("[stock_confirm] 批量预热异常(忽略) %s" % str(_be)[:60], file=sys.stderr)
                    _keep = []
                    for _x in stocks:
                        _c = str(_x["code"])
                        _ok, _why = _is_member(_c, _own.get(_c, []), mt)
                        if _ok:
                            _keep.append(_x)
                        else:
                            print("MEMBER_REJECT %s theme=%s 概念=%s dsh=%s" % (_c, mt, cname, _why), file=sys.stderr)
                    if len(_keep) != len(stocks):
                        print("[stock_confirm] 主营校验 %s: %d → %d 只" % (cname, len(stocks), len(_keep)), file=sys.stderr)
                    stocks = _keep
            except Exception as _me:
                print("[stock_confirm] 主营校验异常(放行) %s" % str(_me)[:80], file=sys.stderr)
            n_confirm = sum(1 for s in stocks if s["stage"] in ("确认", "突破候选"))
            out[cname] = {"theme": mt, "n": len(stocks), "confirm": n_confirm,
                          "ratio": round(n_confirm / max(len(stocks), 1), 2), "stocks": stocks}
            print(f"[stock_confirm] {mt} > {cname} n={len(stocks)} 确认 {n_confirm}", file=sys.stderr)
    if con:
        con.close()
    json.dump(out, open(os.path.join(DATA, "stock_confirm_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[stock_confirm] WROTE stock_confirm_result.json 概念:{len(out)} ({time.time()-t0:.0f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
