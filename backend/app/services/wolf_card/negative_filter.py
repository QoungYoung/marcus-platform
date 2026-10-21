# -*- coding: utf-8 -*-
"""负样本运行时过滤器：只读 negative_audit 的 runtime_use 语义，不把 negative=true 当硬排除。

分层：
  blocked       结构性风险（ST/名称黑名单/核心不碰的银行红利）
  penalized     风格/主题偏好（医药、有色、公募重仓）
  watch         条件型（海外链/光模块/材料，等条件解除）
  tactical_only 只允许战术/ETF（券商）
  no_trade_gate 由 regime_gate 处理，本模块不碰
"""
from __future__ import annotations
import json, os, sqlite3
from typing import Any, Dict, Iterable, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")
POOL = os.path.join(ROOT, ".dsh-tmp", "buyside", "prod_data", "stock_pool.db")

_IND = None
_FUND = None


def _industries() -> Dict[str, str]:
    global _IND
    if _IND is not None:
        return _IND
    _IND = {}
    try:
        con = sqlite3.connect(POOL)
        for code, ind in con.execute("select ts_code,industry from stock_pool"):
            if code and ind:
                _IND[str(code)] = str(ind)
    except Exception:
        pass
    return _IND


def _fund_holdings() -> Dict[str, dict]:
    global _FUND
    if _FUND is not None:
        return _FUND
    _FUND = {}
    try:
        rows = json.load(open(os.path.join(DATA, "fund_holdings", "aggregate.json"), encoding="utf-8"))
        for r in rows:
            _FUND[str(r.get("symbol"))] = r
    except Exception:
        pass
    return _FUND


def _is_bank_div(c: dict) -> bool:
    ind = str(c.get("industry") or _industries().get(str(c.get("code") or ""), ""))
    name = str(c.get("name") or "")
    return ("银行" in ind) or ("保险" in ind) or ("红利" in name) or ("银行" in name)


def _is_broker(c: dict) -> bool:
    ind = str(c.get("industry") or _industries().get(str(c.get("code") or ""), ""))
    return ("证券" in ind) or ("券商" in str(c.get("theme") or "")) or ("券商" in str(c.get("name") or ""))


def _is_pharma(c: dict) -> bool:
    return "医药" in str(c.get("theme") or "") or "医药" in str(c.get("industry") or "")


def _is_nonferrous(c: dict) -> bool:
    ind = str(c.get("industry") or _industries().get(str(c.get("code") or ""), ""))
    return any(k in ind for k in ("有色", "黄金", "稀土", "铜", "铝"))


def _is_overseas_optical(c: dict) -> bool:
    txt = " ".join(str(c.get(k) or "") for k in ("theme", "segment", "name", "role"))
    return any(k in txt for k in ("海外链", "光模块", "CPO", "中际"))


def evaluate(candidates: Iterable[dict], sleeve: str = "core", date8: str | None = None) -> Dict[str, Any]:
    out = {"blocked": [], "penalized": [], "watch": [], "tactical_only": [],
           "meta": {"sleeve": sleeve, "date": date8, "kept": [],
                    "policy": "hard rules <=5; negative=true 不直接硬排除；penalty/watch 保留"}}
    for c in candidates:
        code = str(c.get("code") or "")
        name = str(c.get("name") or "")
        row = {"code": code, "name": name, "theme": c.get("theme")}
        # 1) 名称黑名单（特定标的/事件；北部湾港带时效）
        if "科大讯飞" in name or code == "002230.SZ":
            out["blocked"].append({**row, "reason": "大数据ETF/科大讯飞：特定标的负样本"})
            continue
        if "北部湾港" in name or code == "000582.SZ":
            out["blocked"].append({**row, "reason": "北部湾港：做局收尾（特定事件，需复核时效）"})
            continue
        # 2) 银行/红利：核心硬排除；T/战术只扣分
        if _is_bank_div(c):
            if sleeve == "core":
                out["blocked"].append({**row, "reason": "银行/红利：长期风格负样本，核心不碰"})
            else:
                out["penalized"].append({**row, "delta": -0.20, "reason": "银行/红利：非核心风格"})
                out["meta"]["kept"].append({**row, "negative_status": "penalized", "delta": -0.20})
            continue
        # 3) 券商：只允许战术
        if _is_broker(c):
            out["tactical_only"].append({**row, "reason": "券商：只做指数突破诱多，不做核心"})
            out["meta"]["kept"].append({**row, "negative_status": "tactical_only"})
            continue
        # 4) 医药/有色：扣分，不硬删
        if _is_pharma(c):
            out["penalized"].append({**row, "delta": -0.12, "reason": "医药：能力圈/风格扣分"})
            out["meta"]["kept"].append({**row, "negative_status": "penalized", "delta": -0.12})
            continue
        if _is_nonferrous(c):
            out["penalized"].append({**row, "delta": -0.10, "reason": "有色：能力圈/风格扣分"})
            out["meta"]["kept"].append({**row, "negative_status": "penalized", "delta": -0.10})
            continue
        # 5) 海外链/光模块：条件观察，不是永久排除
        if _is_overseas_optical(c):
            out["watch"].append({**row, "condition": "海外链/光模块：等重新估值或 FCC/大光破位风险解除"})
            out["meta"]["kept"].append({**row, "negative_status": "watch"})
            continue
        # 6) 公募重仓：只扣分，不硬删
        f = _fund_holdings().get(code)
        if f and (int(f.get("n_funds") or 0) >= 5 or float(f.get("sum_float_ratio") or 0) >= 2.5):
            out["penalized"].append({**row, "delta": -0.10,
                                     "reason": "公募/ETF 重仓：n_funds=%s sum_float_ratio=%s" % (
                                         f.get("n_funds"), f.get("sum_float_ratio"))})
            out["meta"]["kept"].append({**row, "negative_status": "penalized", "delta": -0.10})
            continue
        out["meta"]["kept"].append({**row, "negative_status": "ok"})
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--theme", default="")
    ap.add_argument("--sleeve", default="core")
    a = ap.parse_args()
    print(json.dumps(evaluate([{"code": a.code, "name": a.name, "theme": a.theme}], a.sleeve), ensure_ascii=False, indent=1))
