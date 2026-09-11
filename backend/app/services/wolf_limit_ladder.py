# -*- coding: utf-8 -*-
"""wolf_limit_ladder.py — 涨停梯队 / 连板结构（B4，2026-09-11）。

────────────────────────────────────────────────────────────────
狼大原话（XLS 2025-04-21，**他的每日复盘流程**）:
  「看第10的涨停板方向主要是观察**哪些梯队结构完整**，**哪些集中毕业照**，
    **哪些方向上板失败**，用这些来**判断板块的强弱从而推断出接下来要做的方向**」
────────────────────────────────────────────────────────────────

**定位**：这是他**复盘（盘后）**用的判据，不是盘中信号。所以本模块产出一个**盘后状态文件**，
供次日盘前/决策上下文使用（与他的流程一致：晚上复盘 → 预判明天做哪个方向）。

**数据源**：Tushare `limit_list_d`（走本仓既有的 tushare 代理，生产实测 0.1–0.4s；**EOD 数据**）。
  字段含 `limit_times`(连板数)、`open_times`(炸板次数)、`up_stat`(近期涨停统计)、`industry`(行业)、`name`。
  ⚠️ 该代理**忽略 `limit_type` 参数**（U/Z/D 返回同一批）→ 必须**按返回的 `limit` 列本地过滤**：
     U=涨停 / Z=炸板 / D=跌停。

**判据（照他的三个词直接落地，不额外发明）**：
  · **梯队结构完整**：主题内同时存在 1板、2板，且（有 3板以上 或 2板 ≥2 家）
    → "底部有承接、高度有延续"。
  · **集中毕业照**：涨停家数不少（≥ GRAD_MIN_N）却 **最高只有 1 板**（无高度）
    → 集体一日游，他不追。
  · **上板失败**：炸板率（Z/(U+Z)）≥ FAIL_RATE → 当日该方向多次冲板未成。
  · 其余 = 一般。

**安全/开关**：`WOLF_LIMIT_LADDER=0` 关闭；`WOLF_LIMIT_LADDER_FILE` 改状态文件名；
  取数失败 → 返回 None 且**不覆盖已有状态文件**（fail-safe）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

STATE_FILE = "wolf_limit_ladder.json"
# 阈值默认值（**狼大原话未给数字** → 属可调参数，非"自造机制"）
# 标定（2026-08-20~09-10，16 个交易日 × 39 行业 = 725 个 industry-day 样本）：
#   GRAD_MIN_N=3 → 毕业照触发率 6.6%（噪音大）；=4 → 2.5%；=6 → 0.8%（漏掉小毕业照）
#   取 4。典型正样本：2026-08-20 生物制品 14 家涨停**全部首板**(max_times=1)、化学制药 12 家全首板。
GRAD_MIN_N = 4          # "集中毕业照"判据的涨停家数下限
FAIL_RATE = 0.40        # 炸板率阈值


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return os.getenv("WOLF_LIMIT_LADDER", "1").strip() not in ("0", "false", "no")


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/app/data"),
                        os.getenv("WOLF_LIMIT_LADDER_FILE", STATE_FILE))


def _i(v) -> int:
    """安全取整：NaN / None / '' / 非数值 → 0（tushare 的 limit_times 有 NaN，直接 int() 会崩）。"""
    try:
        f = float(v)
        return 0 if f != f else int(f)      # f != f 捕获 NaN
    except (TypeError, ValueError):
        return 0


def _f(v) -> float:
    try:
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def fetch(date8: str) -> Optional[List[Dict[str, Any]]]:
    """某交易日的涨跌停/炸板明细 → rows；失败返回 None。

    ⚠️ 实测该 tushare 代理**忽略 limit_type**（传 U / Z / D 都返回同一批）→ 不带该参数，
       一律按返回的 `limit` 列本地过滤（U=涨停 / Z=炸板 / D=跌停）。
    """
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        df = pro.limit_list_d(trade_date=date8)
        if df is None or len(df) == 0:
            return None
        out = []
        for _, r in df.iterrows():
            out.append({
                "ts_code": str(r.get("ts_code") or ""),
                "name": str(r.get("name") or ""),
                "industry": str(r.get("industry") or ""),
                "limit": str(r.get("limit") or "").upper(),
                "limit_times": _i(r.get("limit_times")),
                "open_times": _i(r.get("open_times")),
                "pct_chg": _f(r.get("pct_chg")),
            })
        return out
    except Exception as e:
        print(f"[limit_ladder] 取数失败({date8}): {type(e).__name__}: {str(e)[:90]}")
        return None


def classify_ladder(rows: List[Dict[str, Any]], n_u: int, n_z: int,
                    max_times: int, ladder: Dict[str, int]) -> str:
    """按狼大三个词给主题打标签 → 'complete' / 'graduation' / 'failed' / 'plain'。

    优先级：**上板失败**(炸板率高) > **集中毕业照**(有量无高度) > **结构完整** > 一般。
    阈值可调：WOLF_LL_GRAD_MIN_N / WOLF_LL_FAIL_RATE。
    """
    grad_min = _env_f("WOLF_LL_GRAD_MIN_N", GRAD_MIN_N)
    fail_rate = _env_f("WOLF_LL_FAIL_RATE", FAIL_RATE)
    if n_u + n_z > 0 and (n_z / float(n_u + n_z)) >= fail_rate and n_z >= 2:
        return "failed"
    if n_u >= grad_min and max_times <= 1:
        return "graduation"
    if ladder.get("1", 0) >= 1 and ladder.get("2", 0) >= 1 and (
            ladder.get("3+", 0) >= 1 or ladder.get("2", 0) >= 2):
        return "complete"
    return "plain"


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按**主题**（我们自己的主题映射）+ **行业**（tushare 行业）聚合涨停梯队。"""
    try:
        import sys
        for _p in ("/app/apps/main_line",):
            if os.path.isdir(_p) and _p not in sys.path:
                sys.path.insert(0, _p)
        from wolf_context import theme_of_symbol
    except Exception:
        theme_of_symbol = None       # 取不到就只用 industry 维度

    def _agg(key_fn) -> Dict[str, Dict[str, Any]]:
        g: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            k = key_fn(r)
            if not k:
                continue
            d = g.setdefault(k, {"u": [], "z": [], "d": []})
            lt = r.get("limit")
            if lt == "U":
                d["u"].append(r)
            elif lt == "Z":
                d["z"].append(r)
            elif lt == "D":
                d["d"].append(r)
        out = {}
        for k, d in g.items():
            if not d["u"] and not d["z"]:
                continue                       # 只有跌停的主题不进梯队榜
            times = [int(x["limit_times"] or 0) for x in d["u"]]
            ladder = {"1": sum(1 for t in times if t == 1),
                      "2": sum(1 for t in times if t == 2),
                      "3+": sum(1 for t in times if t >= 3)}
            mx = max(times) if times else 0
            rec = {"u_n": len(d["u"]), "z_n": len(d["z"]), "d_n": len(d["d"]),
                   "max_times": mx, "ladder": ladder,
                   "names": [x["name"] for x in sorted(d["u"], key=lambda y: -int(y["limit_times"] or 0))[:5]]}
            rec["tag"] = classify_ladder(rows, rec["u_n"], rec["z_n"], mx, ladder)
            out[k] = rec
        return out

    themes = _agg(lambda r: (theme_of_symbol(r["ts_code"]) if theme_of_symbol else None)) \
        if theme_of_symbol else {}
    inds = _agg(lambda r: r.get("industry") or "")
    return {"by_theme": themes, "by_industry": inds,
            "totals": {"u": sum(1 for r in rows if r["limit"] == "U"),
                       "z": sum(1 for r in rows if r["limit"] == "Z"),
                       "d": sum(1 for r in rows if r["limit"] == "D")}}


def scan_and_save(date8: Optional[str] = None, rows: Optional[List[Dict[str, Any]]] = None,
                  dry_run: bool = False) -> Dict[str, Any]:
    """取数 → 聚合 → 落状态文件。**取数失败不动原文件**（fail-safe）。"""
    import datetime as _dt
    d8 = date8 or _dt.date.today().strftime("%Y%m%d")
    rs = rows if rows is not None else fetch(d8)
    if rs is None:
        return {"ok": False, "reason": "fetch_failed", "date": d8}
    res = summarize(rs)
    res.update({"ok": True, "date": d8})
    if not dry_run:
        try:
            p = _path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f"[limit_ladder] 落盘失败: {str(e)[:90]}")
            res["ok"] = False
    t = res["totals"]
    print(f"[limit_ladder] {d8} 涨停{t['u']} 炸板{t['z']} 跌停{t['d']} | "
          f"主题 {len(res['by_theme'])} 个 / 行业 {len(res['by_industry'])} 个"
          f"{' [dry-run]' if dry_run else ''}")
    for k, v in sorted(res["by_theme"].items(), key=lambda kv: -kv[1]["u_n"])[:6]:
        print(f"    {k}: {v['u_n']}涨停 最高{v['max_times']}板 梯队{v['ladder']} 炸{v['z_n']} → {v['tag']}")
    return res


def load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _fmt_date(d) -> str:
    s = str(d or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else (s or "未知日期")


def directive() -> str:
    """给复盘/次日决策上下文的提示（他用法：据此判断接下来做哪个方向）。

    主题映射（theme_of_symbol）覆盖的是**我们可交易的主题池**，涨停股里大部分不在其中 →
    主题榜为空时回退到**行业**维度（tushare industry），保证任何时候都有可用信息。
    """
    st = load()
    if not st:
        return ""
    tag_cn = {"complete": "梯队结构完整", "graduation": "集中毕业照(有量无高度)",
              "failed": "上板失败(炸板高)", "plain": "一般"}
    src = st.get("by_theme") or {}
    dim = "主题"
    if not src:
        src = st.get("by_industry") or {}
        dim = "行业"
    if not src:
        return ""
    lines = ["📈 %s 涨停梯队[%s]（狼大 2025-04-21 复盘流程：「看涨停板方向…"
             "判断板块的强弱从而推断接下来要做的方向」）" % (_fmt_date(st.get("date")), dim)]
    for k, v in sorted(src.items(), key=lambda kv: -kv[1]["u_n"])[:5]:
        lines.append("  - %s：%d涨停 最高%d板（%d/%d/%d 板）炸板%d → **%s**"
                     % (k, v["u_n"], v["max_times"], v["ladder"]["1"], v["ladder"]["2"],
                        v["ladder"]["3+"], v["z_n"], tag_cn.get(v["tag"], v["tag"])))
    return "\n".join(lines)
