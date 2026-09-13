# -*- coding: utf-8 -*-
"""concept_communities.py — 数据驱动概念社区发现(子方向自演化, 不依赖狼大/dsh聚合)
用 概念→成员股 Jaccard 重叠 + 概念资金流(concept_hist net_amount)相关, 做无监督社区发现(union-find),
把同一子方向的概念自动聚成社区。dsh 只需给社区起名, 不负责分组。
输出: data/concept_communities.json {theme: {community_id: {"label":概念, "concepts":[...], "n_stocks":int}}}
用法: python -u apps/main_line/concept_communities.py  [AI/算力/科技 消费/内需]
"""
import os, sys, json, collections, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fusion_mainline import THEME_CONCEPTS
except Exception:
    THEME_CONCEPTS = {}
try:
    from rotation_universe import _norm
except Exception:
    _norm = lambda s: str(s).replace(" ", "").replace("　", "")
DATA = os.environ.get("DATA_DIR", "data")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
JACCARD_TH = float(os.getenv("JACCARD_TH", "0.12"))
CORR_TH = float(os.getenv("CORR_TH", "0.55"))
USE_CORR = os.getenv("USE_CORR", "1").strip() in ("1", "true", "yes", "on")

def load(p):
    try: return json.load(open(os.path.join(DATA, p), encoding="utf-8"))
    except Exception: return {}

def concept_stocks():
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT concept_name, ts_code FROM stock_concept_map")
        out = collections.defaultdict(set)
        for cn, ts in cur.fetchall():
            out[str(cn)].add(str(ts))
        cur.close(); conn.close()
        return out
    except Exception:
        return collections.defaultdict(set)

class UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb: self.p[rb] = ra

def jaccard(a, b):
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)

def corr(name_a, name_b, hist):
    """concept_hist 里 name 匹配到的概念净流入序列 pearson 相关。"""
    def ser(name):
        for c, a in hist.items():
            if not isinstance(a, dict): continue
            if (a.get("name") or "") == name:
                d = a.get("dates") or []; v = a.get("net_amount") or []
                return {str(dd): float(vv) for dd, vv in zip(d, v) if vv is not None}
        return {}
    A = ser(name_a); B = ser(name_b)
    keys = sorted(set(A) & set(B))
    if len(keys) < 5: return 0.0
    import numpy as np
    x = np.array([A[k] for k in keys]); y = np.array([B[k] for k in keys])
    if x.std() == 0 or y.std() == 0: return 0.0
    r = float(np.corrcoef(x, y)[0, 1])
    return max(0.0, r)

def communities(theme, cs, hist):
    """对 theme 的 THEME_CONCEPTS 概念做社区发现, 返回 {label:[concepts]}。"""
    cons = [c for c in (THEME_CONCEPTS.get(theme) or []) if c]
    if not cons: return {}
    uf = UF()
    for i in range(len(cons)):
        for j in range(i + 1, len(cons)):
            a, b = cons[i], cons[j]
            jac = jaccard(cs[a], cs[b])
            jac_ok = jac >= JACCARD_TH
            corr_ok = USE_CORR and (corr(a, b, hist) >= CORR_TH)
            if jac_ok or corr_ok:
                uf.union(a, b)
    groups = collections.defaultdict(list)
    for c in cons:
        groups[uf.find(c)].append(c)
    # 每个社区: 用成员股最多(或净流入最多)的概念做 label
    out = {}
    for gid, c_list in groups.items():
        best = max(c_list, key=lambda c: len(cs[c]))
        out[best] = {"concepts": c_list, "n_stocks": len(set().union(*[cs[c] for c in c_list]))}
    return out

def main():
    hist = load("concept_hist.json")
    cs = concept_stocks()
    themes = [a for a in sys.argv[1:] if not a.startswith("--")] or ["AI/算力/科技", "消费/内需", "医药"]
    res = {}
    for th in themes:
        comm = communities(th, cs, hist)
        res[th] = comm
        print("==== 主题:", th, " 概念数:", len(THEME_CONCEPTS.get(th) or []), " 社区数:", len(comm), flush=True)
        for label, d in sorted(comm.items(), key=lambda kv: -kv[1]["n_stocks"]):
            print("  社区[%s] n_stocks=%d 概念=%s" % (label, d["n_stocks"], "、".join(d["concepts"])), flush=True)
    json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "jaccard_th": JACCARD_TH, "corr_th": CORR_TH,
               "themes": res}, open(os.path.join(DATA, "concept_communities.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE data/concept_communities.json", flush=True)

if __name__ == "__main__":
    main()
