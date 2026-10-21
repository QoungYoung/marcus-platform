# -*- coding: utf-8 -*-
"""L4 instrument watch：方向 -> ETF/个股角色候选。

原则：
  · ETF 先用 etf_theme_map_pi；159516/588170 作为“工具 override”，不是个股偏好；
  · 个股用 dated chain_map 的 leading_verified / verified_all，按 segment role 标上游/中游/下游；
  · 不用 wolf_mention_universe（帖子派生）做生产；
  · negative_filter 只做风险分类，不把“他不做”当永久黑名单。
"""
from __future__ import annotations
import glob, json, os
from typing import Any, Dict, List

try:
    from .negative_filter import evaluate as negative_evaluate
except ImportError:  # 直接 python 脚本运行时
    from negative_filter import evaluate as negative_evaluate

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")

MANUAL_ETF = {
    "159516.SZ": {"name": "半导体设备ETF", "theme": "半导体/芯片", "sleeve": "T", "role": "ETF工具/设备"},
    "588170.SH": {"name": "科创半导体ETF", "theme": "半导体/芯片", "sleeve": "T", "role": "ETF工具/科创"},
    "159316.SZ": {"name": "港股创新药ETF", "theme": "医药", "sleeve": "low_position", "role": "ETF工具/港股创新药"},
}


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _etf_map() -> Dict[str, Any]:
    d = _read_json(os.path.join(DATA, "etf_theme_map_pi.json")) or {}
    return {t["theme"]: t for t in d.get("themes", [])}


def _chain_map(date8: str):
    p = os.path.join(DATA, "chain_map_%s.json" % date8)
    if os.path.exists(p):
        return _read_json(p), p
    # 回测不拿未来：找不到当天就返回空，不回退到最新
    return None, "missing"


def _etf_candidates(theme: str, sleeve: str, date8: str) -> List[dict]:
    # 优先用 etf_watch 的可观测排序（流动性/强度/角色）
    try:
        try:
            from .etf_watch import rank_etfs
        except ImportError:
            from etf_watch import rank_etfs
        ranked = rank_etfs(theme, date8, top=10)
        if ranked:
            out = []
            for x in ranked:
                out.append({"code": x.get("code"), "name": x.get("name"), "theme": theme,
                            "sleeve": x.get("sleeve") or sleeve, "role": "ETF工具/%s" % x.get("role"),
                            "score": x.get("score"), "reason": x.get("reason"), "source": "etf_watch"})
            return out
    except Exception:
        pass
    out = []
    # 他的工具 ETF 优先（159516/588170），不是个股偏好
    for code, x in MANUAL_ETF.items():
        if x["theme"] == theme:
            out.append({"code": code, "name": x["name"], "theme": theme,
                        "sleeve": x["sleeve"], "role": x["role"], "source": "manual_etf"})
    t = _etf_map().get(theme) or {}
    for key, tag in (("primary", "core"), ("optional", "T")):
        for x in t.get(key) or []:
            out.append({"code": x.get("ts_code"), "name": x.get("name"), "theme": theme,
                        "sleeve": sleeve or tag, "role": "ETF工具", "source": "etf_theme_map_pi/%s" % key})
    # 去重
    seen, uniq = set(), []
    for x in out:
        if x["code"] and x["code"] not in seen:
            seen.add(x["code"]); uniq.append(x)
    return uniq


def _stock_candidates(theme: str, cm: dict) -> List[dict]:
    if not cm:
        return []
    out = []
    for t in cm.get("themes", []):
        if t.get("theme") != theme:
            continue
        for seg in t.get("segments", []):
            role = seg.get("role") or "mid"
            label = seg.get("label")
            leaders = seg.get("leading_verified") or []
            lead_codes = {x.get("ts") for x in leaders}
            for x in leaders:
                out.append({"code": x.get("ts"), "name": x.get("name"), "theme": theme,
                            "segment": label, "segment_role": role, "role": "leader",
                            "mv": x.get("mv"), "source": "chain_map/leading_verified"})
            for x in (seg.get("verified_all") or []):
                if x.get("ts") in lead_codes:
                    continue
                out.append({"code": x.get("ts"), "name": x.get("name"), "theme": theme,
                            "segment": label, "segment_role": role, "role": "second",
                            "mv": x.get("mv"), "source": "chain_map/verified_all"})
    # 去重，leader 优先，mv 降序
    best = {}
    for x in out:
        c = x.get("code")
        if not c:
            continue
        if c not in best or (x["role"] == "leader" and best[c]["role"] != "leader"):
            best[c] = x
    return sorted(best.values(), key=lambda x: (0 if x["role"] == "leader" else 1, -(x.get("mv") or 0)))


def watch(direction: str, date8: str, sleeve: str = "core", top_etf: int = 5, top_stock: int = 10) -> Dict[str, Any]:
    cm, cm_src = _chain_map(date8)
    etfs = _etf_candidates(direction, sleeve, date8)
    stocks = _stock_candidates(direction, cm)
    # 先截断，避免负样本评估太大
    etfs = etfs[:top_etf]
    stocks = stocks[:top_stock]
    neg = negative_evaluate(etfs + stocks, sleeve=sleeve, date8=date8)
    status = {x["code"]: x for x in neg["meta"].get("kept", [])}
    def _attach(x):
        s = status.get(x["code"]) or {}
        return {**x, "negative_status": s.get("negative_status", "ok"), "negative_delta": s.get("delta", 0)}
    etfs = [_attach(x) for x in etfs if x["code"] in status]
    stocks = [_attach(x) for x in stocks if x["code"] in status]
    # 负样本只做注解/扣分，不改变 role/mv 排序（否则 public-fund penalty 会把真龙头排到后面）
    stocks.sort(key=lambda x: (0 if x.get("role") == "leader" else 1,
                               -(x.get("mv") or 0)))
    out = {
        "direction": direction, "date": date8, "sleeve": sleeve,
        "etf": etfs,
        "stocks": stocks,
        "negative": {k: v for k, v in neg.items() if k != "meta"},
        "quality": {
            "chain_map": {"source": cm_src, "date": (cm or {}).get("date"), "mv_date": (cm or {}).get("mv_date")},
            "etf_map_date": (_read_json(os.path.join(DATA, "etf_theme_map_pi.json")) or {}).get("date"),
            "note": "chain_map 规则版 --no-ai；未使用帖子提及池",
        },
    }
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--direction", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--sleeve", default="core", choices=("core", "T", "low_position", "theme"))
    ap.add_argument("--top-etf", type=int, default=5)
    ap.add_argument("--top-stock", type=int, default=10)
    a = ap.parse_args()
    print(json.dumps(watch(a.direction, a.date, a.sleeve, a.top_etf, a.top_stock), ensure_ascii=False, indent=1))
