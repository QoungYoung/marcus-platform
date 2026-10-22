# -*- coding: utf-8 -*-
"""浪型/策略建议 agent（2026-10-21 ✓）—— 取事实 → 让 DSH agent 结合**狼大的规则**给建议 → 推 QQ。

为什么这么做（用户 2026-10-21：「接入 agent，让他结合工具分析给出建议，推送 QQ」）：
  · 事实由**工具**取（确定性 ✓ 可复算 ✓）：三指数量价/缺口、银行与红利被压程度、候选池位置、
    当前浪型档位、今日结构信号；
  · 建议由 **agent** 出（DSH /chat ✓ 与 event_briefing_agent 同一套路 ✓），prompt 里**必须**带他的规则原文 ✓；
  · **DSH 不可用 ⇒ 走确定性规则版兜底** ✓（绝不静默 ✗，并注明 ✓）。

定位（务必守住 ✓）：**只读分析 + 提示** ✓ —— 不下单 ✗、不改 `wave_state` ✗、不参与闸门 ✗。

用法::
    python jobs/wave_advice_agent.py --dry       # 只打印（含 agent 原文 ✓）
    python jobs/wave_advice_agent.py             # 推 QQ ✓
    python jobs/wave_advice_agent.py --no-llm    # 只跑规则版兜底 ✓
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "jobs")):
    if p not in sys.path:
        sys.path.insert(0, p)

from adj_quote import qfq_bars, kind_of        # noqa: E402  前复权取数 ✓（core/adj_quote.py ✓）
from tushare_relay import relay_query          # noqa: E402
from wave_structure_alert import structures    # noqa: E402  结构判定同源 ✓

END = "20261031"
INDEXES = [("000001.SH", "上证"), ("399006.SZ", "创业板"), ("000688.SH", "科创50")]
CAND = [("002463.SZ", "沪电股份", "PCB"), ("603228.SH", "景旺电子", "PCB"), ("600183.SH", "生益科技", "PCB"),
        ("002281.SZ", "光迅科技", "二线光"), ("000988.SZ", "华工科技", "二线光"),
        ("600487.SH", "亨通光电", "二线光"), ("002409.SZ", "雅克科技", "材料"),
        ("603688.SH", "石英股份", "材料"), ("603938.SH", "三孚股份", "材料"),
        ("600206.SH", "有研新材", "材料")]

# ★ 2026-10-21：**彻底去掉帖子/个人内容** ✗（用户：万一他哪天不更新，我们就完蛋了 ✓）
#   改为读 `config/wave_advice_policy.json` —— **我们自己的操作纪律** ✓（入库 ✓ 版本化 ✓ 每条带出处 ✓）。
#   每条的来源都是本项目自己的产物（档位表 / tier_target_pct / E1~E5 实验结果 ✓），不依赖任何论坛 ✓。
POLICY_FILE = (os.environ.get("WAVE_ADVICE_POLICY") or "").strip() or "config/wave_advice_policy.json"

POLICY_FALLBACK = """（内置兜底 · 无外部文件）
P1 operation=defense ⇒ 买类禁、卖类不受限；总仓 ≤30%
P2 买侧：候选池（PCB/二线光/半导体材料）且 距MA20 ≥ -3% ∧ 量比 ≥1.2 才小仓试探，否则观察
P3 卖侧：反抽进上方未回补缺口且量比<0.9 ⇒ 减；跌破 MA20/分型低点 ⇒ 减
P4 结构只作观察锚（实测无方向优势 ✗），红三兵 10 日唯一弱正向 ✓
P5 取不到就写「缺」✗ 不猜 P6 只读分析 ✓ 不是投资建议"""


def load_policy():
    """→ (policy_text, meta{version, n, src}) ✓"""
    try:
        d = json.load(open(POLICY_FILE, encoding="utf-8"))
        items = d.get("policy") or []
        if not items:
            raise RuntimeError("empty policy")
        lines = ["**本项目自己的操作纪律**（不引用任何个人/论坛 ✓ 每条带编号便于引用）："]
        for it in items:
            lines.append("  [%s] %s：%s" % (it.get("id"), it.get("topic") or "", it.get("text") or ""))
        lines.append("  （来源：%s）" % ("；".join(d.get("sources") or [])[:400]))
        return "\n".join(lines), {"version": d.get("version") or "", "n": len(items)}
    except Exception as e:
        print("[wave-advice] 读政策文件失败（%s）⇒ 用内置兜底 ✗" % str(e)[:80])
        return POLICY_FALLBACK, {"version": "", "n": 0}


def build_candidates(limit=12):
    """**动态候选池**（2026-10-22 ✓ 取代写死的 CAND ✗）

    为什么改：原 `CAND` 是手写的 10 只 ✗ ⇒ 建议每天只盯这 10 只 ✓
    （用户问「为什么如此关注沪电」✗ —— 因为它在写死的列表里 ✓，不是系统选的 ✗）。
    现在按系统自己的结论生成 ✓ 三级优先：
      ① `core_p1/bellwether_state_latest.json` 的 `bellwether_code`（每个细分一只龙头 ✓）
      ② `core_select/latest.json` 的 `picks`（当日选股 ✓）
      ③ 回退：原 CAND（fail-open ✓ 宁可退保守也不空 ✗）
    """
    D = os.environ.get("DATA_DIR", "").strip() or "data"
    out, seen = [], set()

    def _add(code, name=None, grp=None):
        code = str(code or "").strip().upper()
        if not code or code in seen or len(out) >= limit:
            return
        if not (code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ")):
            return
        # ★ 只留**主板**（用户明确买不了科创板 688 / 创业板 30x / 北交所 8xx·4xx ✗）
        if code.startswith(("688", "689", "300", "301", "8", "4", "92")):
            return
        seen.add(code)
        out.append((code, name or code, grp or "-"))

    try:
        bw = json.load(open(os.path.join(D, "core_p1", "bellwether_state_latest.json"), encoding="utf-8"))
        for x in (bw.get("items") or []):
            if x.get("bellwether_code"):
                _add(x["bellwether_code"], None, x.get("subtheme"))
    except Exception:
        pass
    try:
        sl = json.load(open(os.path.join(D, "core_select", "latest.json"), encoding="utf-8"))
        for it in (sl.get("items") or []):
            for pk in (it.get("picks") or []):
                _add(pk.get("symbol") or pk.get("code"), pk.get("name"), it.get("subtheme"))
    except Exception:
        pass
    if len(out) < 3:                      # 系统数据缺失 ⇒ 回退写死列表 ✓
        for c, n, g in CAND:
            _add(c, n, g)
        print("[wave-advice] 系统候选不足 ⇒ 回退内置 CAND（%d 只）✗" % len(out))
    else:
        print("[wave-advice] 动态候选池 %d 只 ✓（风向标+选股）" % len(out))
    return out


def load(code, fn="index_daily", start="20260101"):
    """→ [{"d","o","h","l","c","amt"}] 升序 ✓（下游字段名不变 ✓ prompt 依赖它 ✗ 别改）

    ★ 2026-10-22 修（P0-1）：**ETF/个股一律走前复权** ✓（`core/adj_quote.qfq_bars`）；
      指数 `index_daily` **不复权** ✓ 原样保留（指数无拆分/分红 ✓）。
      为什么：512480.SH 2026-07-03 1:2 份额折算 ✗ ⇒ 原始价那天 -50.7% 假暴跌 ✗
      ⇒ dist_ma20_pct / dist_hi60_pct / low5 全被带偏 ⇒ 误判「跌破 MA20 ⇒ 减」✗。
      所以下面算 ma20 / low5 / hi5 / 距离一律吃**复权价** ✓。
    """
    if fn == "index_daily" or kind_of(code) == "index":
        df = relay_query("index_daily", ts_code=code, start_date=start, end_date=END)
        rs = []
        for _, r in (df.iterrows() if df is not None else []):
            rs.append({"d": str(r["trade_date"]), "o": float(r["open"]), "h": float(r["high"]),
                       "l": float(r["low"]), "c": float(r["close"]), "amt": float(r.get("amount") or 0)})
        return sorted(rs, key=lambda x: x["d"])
    # ETF ⇒ fund_daily + fund_adj ✓；个股 ⇒ daily + adj_factor ✓（都由 qfq_bars 自动判断 ✓）
    return [{"d": x["d"], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "amt": x["amt"]}
            for x in qfq_bars(code, start, END)]


def _ts_symbol(code):
    """`588170.SH` → `sh588170` ✓（腾讯口径 ✓；.CSI 等指数它没有这个 symbol ✗ 直接空 ✓）"""
    c = (code or "").strip().upper()
    if "." not in c:
        return ""
    num, mkt = c.split(".", 1)
    return {"SH": "sh" + num, "SZ": "sz" + num}.get(mkt, "")


def _fnum(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x       # NaN/空 ⇒ None ✓ 不猜 ✗


def realtime_quotes(codes):
    """腾讯实时 → {code: {"price_today","pct_today","volx_today","amt_today_yi"}} ✓

    ★ 2026-10-22（P0-2）：原来的 `volx` 是**上一交易日收盘**口径 ✗（rs[-1].amt / 20 日均额），
      但决策问的是**今天放没放量** ✓ ⇒ 必须用当日实时量比 ✓。
      口径（用户给定 ✓ 腾讯 `qt.gtimg.cn`，GBK 解码 ✓，`~` 分割 ✓）：
        索引 3 = 现价 ✓ ｜ 32 = 涨跌幅 % ✓ ｜ 37 = 成交额（万元 ✓） ｜ 49 = 量比 ✓
      失败 ⇒ 只打日志 ✓ 字段给 None ✓，**绝不中断 facts()** ✗。
    """
    out = {}
    syms = {_ts_symbol(c): c for c in codes}
    syms = {s: c for s, c in syms.items() if s}
    if not syms:
        return out
    try:
        url = "http://qt.gtimg.cn/q=" + ",".join(sorted(syms))
        req = urllib.request.Request(url, headers={"Referer": "http://finance.qq.com"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read()
        for line in raw.decode("gbk", "ignore").split(";"):
            if "=" not in line:
                continue
            sym, body = line.split("=", 1)
            sym = sym.strip()
            if sym.startswith("v_"):
                sym = sym[2:]
            code = syms.get(sym)
            f = body.strip().strip('"').split("~")
            if not code or len(f) < 50:
                continue
            amt = _fnum(f[37])
            out[code] = {"price_today": _fnum(f[3]),
                         "pct_today": _fnum(f[32]),
                         "volx_today": _fnum(f[49]),
                         "amt_today_yi": round(amt / 1e4, 0) if amt else None}
        print("[wave-advice] 实时量比 ✓ 取到 {} / {} 只（腾讯 qt.gtimg.cn ✓）".format(len(out), len(syms)))
    except Exception as e:
        print("[wave-advice] 实时接口失败（{}）⇒ volx_today/pct_today/price_today 给 None ✗".format(str(e)[:100]))
    return out


def open_gaps(rs, n=60):
    out = []
    for k in range(max(1, len(rs) - n), len(rs)):
        if rs[k]["h"] < rs[k - 1]["l"]:
            lo, hi = rs[k]["h"], rs[k - 1]["l"]
            if not any(x["h"] >= hi for x in rs[k + 1:]):
                out.append({"date": rs[k]["d"], "lo": round(lo, 2), "hi": round(hi, 2)})
    return out


def facts():
    f = {"date": "", "indexes": {}, "candidates": [], "wave": None}
    # ★ 2026-10-22（P0-2）：一次批量拿**当日实时**量比 ✓（10 只候选 + 3 个指数 ✓ 一个请求 ✓）
    cands = build_candidates()          # ★ 动态池（风向标+选股 ✓ 已过滤非主板 ✓）
    rt = realtime_quotes([c for c, _n in INDEXES] + [c for c, _n, _g in cands])
    sh = None
    for code, name in INDEXES:
        rs = load(code)
        if len(rs) < 65:
            continue
        f["date"] = rs[-1]["d"]
        amt20 = statistics.mean([x["amt"] for x in rs[-21:-1]])
        hi60 = max(x["h"] for x in rs[-60:])
        q = rt.get(code) or {}
        f["indexes"][name] = {
            "close": round(rs[-1]["c"], 2), "close_date": rs[-1]["d"],
            "amt_yi": round(rs[-1]["amt"] / 1e5, 0),
            "volx": round(rs[-1]["amt"] / amt20, 2) if amt20 else None,   # ★ 上一交易日收盘口径 ✗ 只作参考
            "volx_today": q.get("volx_today"),        # ★ 当日实时量比 ✓（判断放量只用它 ✓）
            "pct_today": q.get("pct_today"),          # ★ 当日实时涨跌幅 % ✓
            "price_today": q.get("price_today"),      # ★ 当日实时现价 ✓
            "dist_hi60_pct": round((rs[-1]["c"] / hi60 - 1) * 100, 1),
            "open_gaps": open_gaps(rs)[-3:],
            "ma20": round(statistics.mean([x["c"] for x in rs[-20:]]), 2),
            "low5": round(min(x["l"] for x in rs[-5:]), 2),
            "hi5": round(max(x["h"] for x in rs[-5:]), 2),
            "structures": [{"kind": k, "action": a, "note": n}
                           for k, a, n in structures(rs, len(rs) - 1)]}
        if code == "000001.SH":
            sh = rs
    try:
        bk, hl = load("399986.SZ"), load("000922.CSI")
        if sh and len(bk) > 6 and len(hl) > 6:
            r5 = lambda a: (a[-1]["c"] / a[-6]["c"] - 1) * 100
            f["bank_rel5"] = round(r5(bk) - r5(sh), 2)
            f["dividend_rel5"] = round(r5(hl) - r5(sh), 2)
    except Exception:
        pass
    for code, name, grp in cands:
        try:
            rs = load(code, "daily")
            # ★ 2026-10-22（P0-1）：rs 已是**前复权**价 ✓ ⇒ 下面 ma20/low5/hi5/距离 全部是复权口径 ✓
            #   （折价/拆分当天不会再出现假的「跌破 MA20」✗ ✓）
            ma20 = statistics.mean([x["c"] for x in rs[-20:]])
            amt20 = statistics.mean([x["amt"] for x in rs[-21:-1]])
            hi60 = max(x["h"] for x in rs[-60:])
            q = rt.get(code) or {}
            f["candidates"].append({
                "name": name, "grp": grp, "close": round(rs[-1]["c"], 2),
                # ★ 2026-10-22：close 是**上一交易日**收盘（K线最后一根 ✓ 通常不是今天 ✗）
                #   而 price_today 是今日实时 ⇒ 两者不同日 ✗ 不许做一致性校验 ✗（agent 曾误报"矛盾"✗）
                "close_date": rs[-1]["d"],
                "dist_ma20_pct": round((rs[-1]["c"] / ma20 - 1) * 100, 2),
                "ma20": round(ma20, 2),
                "low5": round(min(x["l"] for x in rs[-5:]), 2),
                "hi5": round(max(x["h"] for x in rs[-5:]), 2),
                "volx": round(rs[-1]["amt"] / amt20, 2) if amt20 else None,   # ★ 上一交易日收盘口径 ✗
                "volx_today": q.get("volx_today"),        # ★ 当日实时量比 ✓（买侧 P2 看它 ✓）
                "pct_today": q.get("pct_today"),          # ★ 当日实时涨跌幅 % ✓
                "price_today": q.get("price_today"),      # ★ 当日实时现价 ✓
                "dist_hi60_pct": round((rs[-1]["c"] / hi60 - 1) * 100, 1),
                # ★ 2026-10-21 修：原来**没给结构** ✗ ⇒ agent 只能说"分型低点缺"✗（它守 P5 没猜 ✓ 是对的 ✓）；
                #    now 每个候选都带 structures（note 里有底分/顶分的关键位 ✓）
                "structures": [{"kind": k, "action": a, "note": n}
                               for k, a, n in structures(rs, len(rs) - 1)]})
        except Exception:
            continue
    f["candidates"].sort(key=lambda x: -(x.get("dist_ma20_pct") or -99))
    f["wolf_recent"] = wolf_recent()
    # ★ 2026-10-22（用户要求 #1）：把**各层结论**汇进 facts ⇒ 输出一张"今天做什么"决策卡片 ✓
    #   浪型 f["wave"] 已在上面取 ✓；这里补 主线 / 选股 / 风向标 / 入场时机 ✓
    def _rj(rel, limit=None):
        try:
            d = json.load(open(os.path.join(os.environ.get("DATA_DIR", "").strip() or "data", rel), encoding="utf-8"))
            return d if limit is None else d
        except Exception:
            return None

    # ★ 只取**紧凑投影**（原始 JSON 近 30KB ✗ 会把 prompt 挤爆 ⇒ 每层只留关键字段 ✓）
    f["layers"] = {}
    _D = os.environ.get("DATA_DIR", "").strip() or "data"
    try:
        ml = _rj("wolf_mainline_select.json") or {}
        f["layers"]["mainline"] = {"date": ml.get("date"), "mainline": ml.get("mainline")}
    except Exception:
        pass
    try:
        sl = _rj(os.path.join("core_select", "latest.json")) or {}
        items = sl.get("items") or []
        top = []
        for it in sorted(items, key=lambda x: -len(x.get("picks") or []))[:6]:
            top.append({"sub": it.get("subtheme"), "rule": it.get("rule"),
                        "picks": [p.get("symbol") or p.get("code") for p in (it.get("picks") or [])[:2]]})
        f["layers"]["select"] = {"date": sl.get("date"), "n_subthemes": len(items),
                                 "n_picks": sl.get("n_picks"), "top": top}
    except Exception:
        pass
    try:
        bw = _rj(os.path.join("core_p1", "bellwether_state_latest.json")) or {}
        its = bw.get("items") or []
        f["layers"]["bellwether"] = {"date": bw.get("date"),
                                     "top": [{"sub": x.get("subtheme"), "state": x.get("state"),
                                              "code": x.get("bellwether_code")} for x in its[:6]]}
    except Exception:
        pass
    try:
        et = _rj("entry_timing.json")
        f["layers"]["entry_timing"] = ({"open": (et or {}).get("open"),
                                        "history": ((et or {}).get("history") or [])[-2:]}
                                       if et else {"state": "无信号（今日未触发 ✓）"})
    except Exception:
        pass
    try:
        base = (os.environ.get("MARCUS_API_URL") or "http://localhost:8000/api/v1").rstrip("/")
        with urllib.request.urlopen(base + "/wave/state", timeout=20) as r:
            f["wave"] = (json.loads(r.read().decode("utf-8")) or {}).get("state")
    except Exception as e:
        f["wave"] = {"_error": str(e)[:80]}
    # ★ 重排键序（放在**末尾** ✓ 此时 layers/wave 都已生成 ✓）—— layers 必须靠前 ✓
    #   实测踩坑：layers 曾排在 JSON 第 9352 字 ✗ 而 prompt 截断 7000 ✗ ⇒ 被切掉 ✗
    #   ⇒ agent 报「layers 三项均为缺」✗（它没错 ✓ 是我们没送到 ✗）
    f = {k: f.get(k) for k in ("date", "layers", "wave", "indexes", "candidates",
                               "bank_rel5", "dividend_rel5", "wolf_recent") if k in f}
    return f


def wolf_recent(days=2, limit=8):
    """读 `data/wolf_posts.json`（由 jobs/wolf_thread_sync.py 抓 ✓）→ 他最近几日发言 ✓。

    ★ 定位（用户 2026-10-21 ✓）：**只作参考** ✓ —— 纪律仍是 `config/wave_advice_policy.json`（我们自己的 ✓）；
      他断更/帖子没了 ⇒ 这里返回空 ✓ **不影响任何东西** ✓（用户：「等他帖子什么时候没了再说」✓）。
    """
    try:
        d = json.load(open(os.path.join(os.environ.get("DATA_DIR", "").strip() or "data",
                                        "wolf_posts.json"), encoding="utf-8"))
        floors = [f for f in (d.get("floors") or []) if f.get("is_op") and f.get("text")]
        import datetime as _dt
        cut = (_dt.date.today() - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
        recent = [f for f in floors if (f.get("date") or "")[:10] >= cut]
        recent = recent[-limit:]
        return [{"date": (f.get("date") or "")[:16], "text": (f.get("text") or "")[:150]} for f in recent]
    except Exception:
        return []


def rules_advice(f):
    """确定性规则版（DSH 不可用时的兜底 ✓ 也用于和 agent 结果并列展示 ✓）"""
    sh = (f.get("indexes") or {}).get("上证") or {}
    # ★ 2026-10-22（P0-2）：规则版也优先吃**当日实时**量比 ✓（volx_today ✓）；
    #   实时接口挂了才退回 `volx`（上一交易日收盘口径 ✗ 只作兜底 ✓，绝不静默换口径 ✗）。
    vx = sh.get("volx_today") if sh.get("volx_today") is not None else sh.get("volx")
    gaps = (sh.get("open_gaps") or [])
    cl = sh.get("close")
    out = []
    in_gap = [g for g in gaps if cl and g["lo"] <= cl <= g["hi"]]
    near_gap = [g for g in gaps if cl and cl < g["lo"] and (g["lo"] / cl - 1) * 100 <= 2.0]
    if in_gap and (vx or 9) < 0.9:
        out.append("**减**：上证已反抽进缺口 %s 且量能萎靡（量比 %.2f）✓ 依据 [P3] ①" %
                   ("/".join("%.0f~%.0f" % (g["lo"], g["hi"]) for g in in_gap), vx or 0))
    elif near_gap:
        out.append("**盯卖点**：上方缺口 %s 就在 %.1f%% 内 ✓ 反抽进去且量比 <0.9 ⇒ 减" % (
            "/".join("%.0f~%.0f" % (g["lo"], g["hi"]) for g in near_gap),
            min((g["lo"] / cl - 1) * 100 for g in near_gap)))
    else:
        out.append("卖侧：没有触及上方缺口 ✓ 按纪律继续持有/借反抽减")
    bk, dv = f.get("bank_rel5"), f.get("dividend_rel5")
    if dv is not None and dv <= -1:
        out.append("**换强**：红利 5 日相对 %+.2f%%（弱于大盘 ✓）⇒ 可换到候选方向（PCB/二线光/材料 ✓）；银行 %s" % (
            dv, ("%+.2f%%（没被压，先不动）" % bk) if bk is not None else "缺"))
    cond_fang = (vx or 0) >= 1.2 and (bk is not None and bk <= -1.0)
    if cond_fang:
        out.append("**可加仓（小仓）**：量比 %.2f + 银行 %+.2f%% ⇒ 两个条件满足 ✓（注：闸门现为 defense，买类需你手动确认 [P1]）" % (vx, bk))
    else:
        out.append("**不补 T**：加仓需 量比 ≥1.2 且 银行下跌 —— 今天量比 %s、银行 %s ⇒ 不满足 [P1][P2] ✗" % (
            ("%.2fx" % vx) if vx else "缺", ("%+.2f%%" % bk) if bk is not None else "缺"))
    top = [c for c in (f.get("candidates") or []) if (c.get("dist_ma20_pct") or -99) > -3]
    if top:
        t0 = top[0]
        tvx = t0.get("volx_today") if t0.get("volx_today") is not None else t0.get("volx")
        out.append("**观察首选**：{}（距 MA20 {:+.2f}%，今日量比 {:.2f}）—— [P2] 站上 MA20 且量比 ≥1.2 才谈试探".format(
            "、".join("{}".format(c["name"]) for c in top[:2]), t0["dist_ma20_pct"], tvx or 0))
    else:
        out.append("**候选全在 MA20 下方** ⇒ 按 [P2] 只观察 ✗（宁可错过，不追高）")
    return out


def chat_url():
    return (os.environ.get("WAVE_ADVICE_CHAT_URL") or os.environ.get("EVENT_BRIEFING_CHAT_URL")
            or os.environ.get("MAIN_LINE_CHAT_URL") or os.environ.get("PI_SERVER_URL")
            or "http://localhost:3001/chat")


def http_json(url, payload=None, timeout=300):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def llm_advice(f, policy_text=None):
    if policy_text is None:
        policy_text, _m = load_policy()
    prompt = [
        "你是严谨的 A 股复盘助手。请**严格按下面这套纪律**给建议，不要自创逻辑，不要预测点位。",
        "每条建议必须标注引用的纪律编号（如 [P2]）✓；取不到的字段写「缺」✗ 不要猜。",
        "",
        "=== 你可以使用的工具（生产 DSH 已注册 MARCUS 工具组 ✓ 2026-10-21 实测 24 个 ✓）===",
        "**只允许调用这些只读函数**：",
        "· `get_portfolio_positions` —— 我的真实持仓与浮盈（**权威** ✓ 用它，不要用别的口径 ✓）",
        "· `get_market_state` —— 市场/浪型状态（含 operation/mode ✓）",
        "· `get_stock_technical` / `get_stock_quote` —— 个股技术面/实时报价（候选池 ✓）",
        "· `get_etf_kline` / `get_intraday_minute` —— K线/分钟（需要时 ✓）",
        "· `get_stock_moneyflow` / `get_t_realtime_indicators` / `get_floor_overview` / `list_*` / `calc_position` —— 只读参考 ✓",
        "⚠️ **绝对禁止**调用任何**写/交易**函数 ✗：`place_order` / `cancel_order` / `build_t_position` /",
        "   `create_t_condition` / `auto_gen_conditions` / `rebalance_floors` / `update_golden_pit_etf_config` /",
        "   `run_t_backtest` / `write` / `edit` / `bash` —— 一个都不许 ✗（这是只读复盘 ✓ 不是交易 ✓）。",
        "⚠️ 工具失败就写「缺」✓ 每个工具最多试一次 ✗ 不要换接口碰运气 ✗。",
        "⚠️ **口径说明（2026-10-22 加 ✓ 曾误报「矛盾」✗）**：",
        "   · `close` / `close_date` = **上一交易日收盘**（K线最后一根 ✓ 通常不是今天 ✗）",
        "   · `price_today` / `pct_today` / `volx_today` = **今日实时** ✓",
        "   · 两者**不是同一天** ✗ ⇒ **绝不要因为 close ≠ price_today 就判定数据矛盾** ✗（差额就是今天的涨跌幅 ✓）",
        "⚠️ **判断买侧（P2）必须用「今日」量比与阴阳**：用 FACTS 的 `volx_today` ✓，也可用 `get_stock_quote` 复核 ✓；",
        "   FACTS 里的 `volx` 是**上一交易日**口径 ✗；判断今日是否放量**必须用 `volx_today`** ✓。",
        "   （2026-10-22 教训：沪电昨日 volx 1.09 ✗ 今日 volx_today 1.37 ✓ —— 差一步就判反了 ✓）",
        "（FACTS 里的数字由我们的脚本取 ✓；与工具冲突时：**持仓以工具为准 ✓**，其余以 FACTS 为准 ✓）",
        policy_text,
        "",
        "★ 若 FACTS.wave.down_tag == 'B反'：说明当前是**B反**（下跌/调整段里的反弹 ✓ 他 2026-08 做的就是这段）⇒",
        "   口径固定为「**只做 T、不追高、不新建**」✓（我们的闸门此时只放宽 T 仓比例 ✓ 买类仍禁 ✗）；",
        "   若 down_tag 为空 ⇒ 不是 B反 ✓ 按常规档位判断 ✓。",
        "",
        "以下是他**最近的公开发言摘录**（只作参考 ✓；与纪律冲突时**以纪律为准** ✓；为空就是没抓到 ✓）：",
        json.dumps(f.get("wolf_recent") or [], ensure_ascii=False)[:1800],
        "",
        "=== FACTS（工具取数，确定性 ✓ 缺失就写「缺」）===",
        json.dumps(f, ensure_ascii=False)[:12000],
        "",
        "输出要求 —— 必须是**一张『今天做什么』决策卡片**（中文，纯文本，不要 markdown 表格）：",
        "『① 今天该不该动』：看 FACTS.layers.entry_timing（市场级入场 = 下跌段 ∧ 放量 ∧ 收阳 ✓）",
        "   · 已触发 ✓ ⇒ 可以加仓/建仓，给出仓位%；未触发 ✗ ⇒ **只允许 probe/不动**，写明原因 ✓",
        "『② 动什么』：结合 FACTS.layers.mainline（主线）+ FACTS.layers.select（选股 top）+ bellwether（风向标）",
        "   · 最多列 **3 只**，每只给归属细分；没有合格的就写「空仓等待」✓ 不许凑数 ✗",
        "『③ 动多少』：浪型档位上限（defense 30% / side 40% / t_only 50% / build 75%）× 入场状态",
        "   · 必须给出**这一次买入的金额或比例** ✓ 不许只写「可考虑」✗",
        "『④ 持仓怎么办』：用 get_portfolio_positions ✓ 逐只给 持有/减半/清仓 + 关键价位；没有持仓写「缺」✓",
        "『⑤ 不该做』：最多 2 条（追高 / 补 T / 接刀 等）",
        "最后单独一行：『以上为按既定规则的复盘，不是投资建议。』",
        "总字数 600 字以内，不要寒暄。",
    ]
    last = None
    for attempt in (1, 2):
        try:
            out = http_json(chat_url(), {"message": "\n".join(prompt),
                                         "session_id": "waveadvice_%s" % (f.get("date") or "x")})
            txt = str((out or {}).get("reply") or "").strip()
            if txt:
                return txt
            last = RuntimeError("empty reply")
        except Exception as e:
            last = e
            print("[wave-advice] agent 第 %d 次失败: %s" % (attempt, str(e)[:100]))
    raise last


def push(text):
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[wave-advice] 推送跳过：未配置 QQ_BOT_RECIPIENT ✗")
            return False
        ok = True
        for i in range(0, len(text), 1100):
            ok = bool(send_c2c_message(openid, text[i:i + 1100])) and ok
        print("[wave-advice] 推送%s（%d 字）" % ("成功 ✓" if ok else "失败 ✗", len(text)))
        return ok
    except Exception as e:
        print("[wave-advice] 推送异常: %s" % str(e)[:120])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    f = facts()
    policy_text, pmeta = load_policy()
    rules = rules_advice(f)
    print("纪律：%d 条 ✓ 版本 %s ｜（**不依赖任何论坛/个人** ✓）" % (pmeta["n"], pmeta["version"] or "-"))
    print("事实：指数 %d 个 ✓ 候选 %d 个 ✓ 浪型 %s" % (
        len(f.get("indexes") or {}), len(f.get("candidates") or []),
        "有" if (f.get("wave") or {}).get("operation") else "缺"))
    print("\n【规则版】\n- " + "\n- ".join(rules))

    body, used = None, False
    if not args.no_llm:
        try:
            body = llm_advice(f, policy_text)
            used = True
            print("\n【agent 版】\n" + body)
        except Exception as e:
            print("\n[wave-advice] agent 不可用（%s）⇒ 走规则版兜底 ✓" % str(e)[:100])

    wave = (f.get("wave") or {})
    # ★ 2026-10-22：标题日期要用**今天**（运行日 ✓），数据日单独标 —— 盘中收盘数据还是昨天的 ✗
    _today = time.strftime("%Y%m%d")
    head = "🐺 策略建议（%s）｜ 浪型 %s·%s·%s" % (
        _today, wave.get("level") or "缺", wave.get("sub_level") or "", wave.get("operation") or "")
    if f.get("date") and f["date"] != _today:
        head += "\n（数据截至 %s 收盘 ✓ 今日盘中请以工具实时值为准 ✓）" % f["date"]
    head += "\n（纪律：本项目自有 %d 条 ✓ 不依赖任何个人/论坛）" % pmeta["n"]
    if used:
        text = head + "\n\n" + body
    else:
        text = head + "\n\n" + "\n".join("- " + x for x in rules) + \
               "\n\n⚠️ agent（DSH）本次不可用，以上为确定性规则版 ✓（口径同他的原话）\n" \
               "以上为按既定规则的复盘，不是投资建议。"
    print("\n" + text)
    if args.dry:
        print("\n（--dry ✓ 不推送）")
        return 0
    push(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
