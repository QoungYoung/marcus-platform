# -*- coding: utf-8 -*-
"""classify_rotation_universe.py — 概念/股票池入池后的细分宇宙分类（每周周末跑）
- 默认增量: 只分类新增/未归类活跃概念(按资金强度取 top_n)
- --full : 首次全量分类(分批循环); 之后每周自动去不存在旧概念(cleanup)+增量分类新概念
范围: DB stock_concept_map ∩ position_class_result(活跃 moneyflow 概念) 未命中 SUB_UNIVERSE。
输出: data/rotation_universe_classified.json {date, additions, retired}
用法: python -u apps/main_line/classify_rotation_universe.py [top_n] [--full]
"""
import os, sys, json, time, re, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rotation_universe import SUB_UNIVERSE, META_GROUPS, _norm, DATA

def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

def _llm_classify(batch, groups, anchors):
    prompt = ("你是主线细分宇宙分类器。把下列未分类东财概念归入现有组；明显不属于任何组则忽略不输出。"
              "只输出JSON：{\"additions\":[{\"concept\":\"xx\",\"group\":\"组名\"}]}\n"
              "组列表：" + groups + "\n组锚点：" + anchors + "\n候选概念：" + json.dumps(batch, ensure_ascii=False))
    body = json.dumps({"message": prompt, "session_id": "ruclass_" + time.strftime("%Y%m%d_%H%M")}).encode()
    req = urllib.request.Request(os.getenv("WAVE_CHAT_URL", "http://marcus-dsh:3001/chat"), data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        reply = (resp.read() or b"").decode("utf-8", "ignore")
    parsed = {}
    m = re.search(r'\{[^{}]*\}', reply, re.S)
    if m:
        try: parsed = json.loads(m.group(0))
        except Exception: parsed = {}
    return parsed.get("additions") or []

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fpath = os.path.join(DATA, "rotation_universe_classified.json")
    classified = load(fpath)
    full = ("--full" in sys.argv) or (not classified.get("full_done"))  # 首次全量, 之后增量
    top_n = int(args[0]) if args else 30
    adds = classified.get("additions") or []
    known_concepts = {a.get("concept") for a in adds if isinstance(a, dict)}
    conn = None
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
        cur = conn.cursor(); cur.execute("select distinct concept_name from stock_concept_map")
        db_names = [r[0] for r in cur.fetchall()]; cur.close()
    except Exception as e:
        print("DB err", e); return 0
    finally:
        if conn: conn.close()
    db_set = set(db_names)
    # 每周清理: 去掉已不存在的旧概念
    retired = [a for a in adds if a.get("concept") not in db_set]
    if retired:
        adds = [a for a in adds if a.get("concept") in db_set]
        print("retired stale:", len(retired), flush=True)
    pos = load(os.path.join(DATA, "position_class_result.json"))
    active = {str(v.get("name")) for v in pos.values() if isinstance(v, dict) and v.get("name")}
    def matched(nm):
        n2 = _norm(nm)
        return any(any(_norm(k) in n2 for k in kws) for kws in SUB_UNIVERSE.values())
    cands = [nm for nm in db_names if nm in active and not matched(nm) and nm not in known_concepts]
    print("uncategorized active concepts:", len(cands), "full=", full, flush=True)
    if not cands:
        json.dump({"date": time.strftime("%Y-%m-%d"), "additions": adds, "full_done": True,
                   "retired": [a.get("concept") for a in retired]}, open(fpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return 0
    netmap = {}
    for v in pos.values():
        if not isinstance(v, dict): continue
        nm = str(v.get("name") or "")
        fs = v.get("fund_flow") or {}
        try: s = float(fs.get("strength") or 0)
        except Exception: s = 0.0
        netmap[nm] = s
    cands.sort(key=lambda nm: (netmap.get(nm, 0.0) > 0, abs(netmap.get(nm, 0.0))), reverse=True)
    groups = "、".join(list(META_GROUPS) + [g for g in SUB_UNIVERSE if g not in META_GROUPS])
    anchors = json.dumps({g: SUB_UNIVERSE[g][:6] for g in SUB_UNIVERSE}, ensure_ascii=False)[:1600]
    batches = [cands[i:i + top_n] for i in range(0, len(cands), top_n)] if full else [cands[:top_n]]
    new_total = 0
    for bi, batch in enumerate(batches, 1):
        try:
            new_items = _llm_classify(batch, groups, anchors)
        except Exception as e:
            print("LLM err batch", bi, ":", str(e)[:80], flush=True)
            time.sleep(3)
            try:
                new_items = _llm_classify(batch, groups, anchors)
            except Exception as e2:
                print("LLM retry err:", str(e2)[:80], flush=True); continue
        for a in new_items:
            c = a.get("concept"); g = a.get("group")
            if c and g in SUB_UNIVERSE and c not in known_concepts:
                adds.append(a); known_concepts.add(c); new_total += 1
        json.dump({"date": time.strftime("%Y-%m-%d"), "additions": adds, "full_done": True,
                   "retired": [a.get("concept") for a in retired]}, open(fpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("batch", bi, "/", len(batches), "new_total", new_total, flush=True)
        time.sleep(1)
    print("DONE additions_total:", len(adds), "new:", new_total, "retired:", len(retired), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
