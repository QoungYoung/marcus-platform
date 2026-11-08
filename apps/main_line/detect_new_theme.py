# 2026-11-08：main_line_state / position_class 改读 PG（state_pg）；找不到模块则完全不干预
try:
    import state_io as _state_io
except Exception:
    try:
        import os as _os_si, sys as _sys_si
        _r_si = _os_si.path.dirname(_os_si.path.dirname(_os_si.path.abspath(__file__)))
        _p_si = _os_si.path.join(_r_si, "apps", "main_line")
        if _p_si not in _sys_si.path:
            _sys_si.path.insert(0, _p_si)
        import state_io as _state_io
    except Exception:
        _state_io = None

# -*- coding: utf-8 -*-
"""detect_new_theme.py — 新主线涌现检测(默认 observe/dry-run)
读 concept_hist(资金/强度) + position_class_result(活跃概念) + concept_taxonomy(归属度),
检测: 一组未被现有主题很好覆盖(归属弱/孤儿) 且同时转强(5d净流入>0 + r20>0 + 广度≥MIN_STOCKS) 的概念簇 → nascent 主题候选。
默认只写 data/nascent_theme_candidates.json + 打印(不下发/不入taxonomy); ENABLE_NEW_THEME=1 时才接 dsh 命名并写入 taxonomy。
用法: python -u apps/main_line/detect_new_theme.py
"""

# ── DSN 收口（scripts/migrate_hardcoded_dsn.py 自动插入，见 core/dsn.py）──
import os as _os_dsn, sys as _sys_dsn
_ROOT_DSN = _os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.dirname(_os_dsn.path.abspath(__file__))))
if _ROOT_DSN not in _sys_dsn.path:
    _sys_dsn.path.insert(0, _ROOT_DSN)
from core import dsn as _dsn_src  # noqa: E402
import os, sys, json, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", "data")
MIN_CONCEPTS = int(os.getenv("MIN_CONCEPTS", "3"))
MIN_DAYS = int(os.getenv("MIN_DAYS", "3"))
MIN_STOCKS = int(os.getenv("MIN_STOCKS", "8"))
ENABLE = os.getenv("ENABLE_NEW_THEME", "0").strip() in ("1", "true", "yes", "on")

def load(p):
    _pg_si = _state_io.maybe_load(p) if _state_io else None
    if _pg_si is not None:
        return _pg_si
    try: return json.load(open(os.path.join(DATA, p), encoding="utf-8"))
    except Exception: return {}

def _ser(a, key):
    idx = a.get("dates") or []
    vals = a.get(key) or []
    return list(zip(idx, vals))

def _strength(a, days=5):
    """5d 净流入和 + r20 相对强度; 返回 (inflow>0, r20>0)"""
    try:
        pts = [(d, float(v)) for d, v in _ser(a, "net_amount") if v is not None]
        if not pts: return False, False
        last5 = sum(v for _, v in pts[-min(5, len(pts)):])
        inflow = last5 > 0
        closes = [float(v) for _, v in _ser(a, "close") if v is not None]
        r20 = False
        if len(closes) >= 21 and closes[-21] > 0:
            r20 = closes[-1] / closes[-21] - 1 > 0
        return inflow, r20
    except Exception:
        return False, False

def _concept_stocks():
    try:
        import psycopg2
        conn = psycopg2.connect(_dsn_src.main())
        cur = conn.cursor(); cur.execute("SELECT concept_name, ts_code FROM stock_concept_map")
        out = {}
        for cn, ts in cur.fetchall():
            out.setdefault(str(cn), set()).add(str(ts))
        cur.close(); conn.close(); return out
    except Exception:
        return {}

def main():
    hist = load("concept_hist.json")
    pos = load("position_class_result.json")
    active = {str(v.get("name")) for v in pos.values() if isinstance(v, dict) and v.get("name")}
    active_codes = [c for c, a in hist.items() if isinstance(a, dict) and a.get("name") in active]
    tax = load("concept_taxonomy.json")
    cth = (tax.get("concept_theme") or {}) if isinstance(tax, dict) else {}
    cstocks = _concept_stocks()
    strong_orphans = []
    for c in active_codes:
        a = hist[c]; nm = str(a.get("name") or "")
        if not nm: continue
        inflow, r20 = _strength(a, MIN_DAYS)
        if not (inflow and r20): continue
        theme = cth.get(nm)
        # 弱覆盖/孤儿: 未归入"成熟"主题(自身成主题 或 主题概念很少)
        covered = bool(theme) and theme != nm and len([k for k in cth if cth[k] == theme]) >= MIN_CONCEPTS
        if not covered:
            strong_orphans.append({"concept": nm, "theme": theme, "stocks": len(cstocks.get(nm, set()))})
    # 簇: 按概念数>=MIN_CONCEPTS 且 广度(sum distinct stocks)>=MIN_STOCKS
    total_stocks = len(set().union(*[set(cstocks.get(x["concept"], set())) for x in strong_orphans])) if strong_orphans else 0
    found = bool(len(strong_orphans) >= MIN_CONCEPTS and total_stocks >= MIN_STOCKS)
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "enabled": ENABLE,
           "min_concepts": MIN_CONCEPTS, "min_days": MIN_DAYS, "min_stocks": MIN_STOCKS,
           "nascent": found, "total_stocks": total_stocks,
           "candidates": strong_orphans[:40]}
    json.dump(out, open(os.path.join(DATA, "nascent_theme_candidates.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if found:
        print("NEW_THEME CANDIDATE: %d concepts / %d stocks; candidates=%s" %
              (len(strong_orphans), total_stocks, "、".join(x["concept"] for x in strong_orphans[:10])), flush=True)
        if ENABLE:
            # 接 dsh 命名并写入 taxonomy(复杂): 此处仅打日志, 由 build_concept_taxonomy 承担命名
            print("ENABLE_NEW_THEME=1: 交由 build_concept_taxonomy 命名+拆子方向", flush=True)
    else:
        print("detect_new_theme: no nascent cluster (strong_orphans=%d stocks=%d)" % (len(strong_orphans), total_stocks), flush=True)
    return out

if __name__ == "__main__":
    main()
