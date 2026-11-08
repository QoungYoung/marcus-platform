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
"""derive_sub_universe.py — 关联当前主线, 从 taxonomy 切出子方向宇宙
读 main_line_state(main_line + candidates) + data/concept_taxonomy.json,
为每个主题取 taxonomy 中该主题的子方向(组), 写 data/rotation_sub_universe.json:
  {version, main_line, candidates, subs:{sub:[concepts]}, ts}
- 主题不在 taxonomy → 回退 THEME_CONCEPTS 每概念一组(仍动态, 不写死)
- main_line/version 均未变 → 复用缓存, 不重算
用法: python -u apps/main_line/derive_sub_universe.py
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fusion_mainline import THEME_CONCEPTS
except Exception:
    THEME_CONCEPTS = {}
DATA = os.environ.get("DATA_DIR", "data")
CACHE = "rotation_sub_universe.json"
TAX = "concept_taxonomy.json"

def load(p):
    _pg_si = _state_io.maybe_load(p) if _state_io else None
    if _pg_si is not None:
        return _pg_si
    try: return json.load(open(os.path.join(DATA, p), encoding="utf-8"))
    except Exception: return {}

def _themes():
    st = load("main_line_state.json")
    if not st: return []
    main = st.get("main_line") or ""
    cands = list(st.get("candidates") or [])
    return ([main] if main else []) + [c for c in cands if c]

def derive():
    themes = _themes()
    if not themes:
        print("no main_line_state; skip"); return None
    main = themes[0]
    cands = themes[1:]
    tax = load(TAX)
    cache = load(CACHE)
    ver = (tax or {}).get("version") or "v0"
    # 未变 → 复用
    if cache and cache.get("main_line") == main and (cache.get("candidates") or []) == cands        and cache.get("version") == ver and cache.get("subs"):
        print("derive: reuse cache version=%s main=%s" % (ver, main)); return cache
    subs = {}
    t_themes = (tax or {}).get("themes") or {}
    for th in themes:
        if not th: continue
        ts = (t_themes.get(th) or {}).get("subs")
        if ts:
            for s, arr in ts.items():
                subs.setdefault(s, list(arr))
        else:
            for c in (THEME_CONCEPTS.get(th) or []):
                if c: subs.setdefault(c, [c])
    if not subs:
        print("derive: no subs for main=%s; keep fallback" % main); return cache
    out = {"version": ver, "main_line": main, "candidates": cands, "subs": subs,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(out, open(os.path.join(DATA, CACHE), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("derive: WROTE %s main=%s themes=%d subs=%d version=%s" % (CACHE, main, len(themes), len(subs), ver))
    return out

if __name__ == "__main__":
    r = derive()
    if r: print(json.dumps(list(r.get("subs", {}).keys()), ensure_ascii=False, indent=1)[:1500])
    if "--refresh-result" in sys.argv:
        try:
            import rotation_universe as _ru
            _ru.main()   # 写 rotation_universe_result.json（crowding_blacklist 已于 2026-09-13 真删）
        except Exception as e:
            print("rotation_universe refresh err:", str(e)[:120])
