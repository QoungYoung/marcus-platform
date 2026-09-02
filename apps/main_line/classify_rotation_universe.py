# -*- coding: utf-8 -*-
"""classify_rotation_universe.py — 每周自动分类新增/未归类东财概念（LLM 兜底）
范围: DB stock_concept_map ∩ position_class_result(活跃 moneyflow 概念) 且未命中 SUB_UNIVERSE，
按候选排序取前 N 交给 marcus-dsh /chat 归到现有组；输出 data/rotation_universe_classified.json。
失败/无候选 → 不改基线(rotation_universe 仍用硬编码词表)。
用法: python -u apps/main_line/classify_rotation_universe.py [top_n]
"""
import os, sys, json, time, re, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rotation_universe import SUB_UNIVERSE, META_GROUPS, _norm, DATA

def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    fpath = os.path.join(DATA, "rotation_universe_classified.json")
    classified = load(fpath)
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
    pos = load(os.path.join(DATA, "position_class_result.json"))
    active = {str(v.get("name")) for v in pos.values() if isinstance(v, dict) and v.get("name")}
    def matched(nm):
        n2 = _norm(nm)
        return any(any(_norm(k) in n2 for k in kws) for kws in SUB_UNIVERSE.values())
    cands = [nm for nm in db_names if nm in active and not matched(nm) and nm not in known_concepts]
    print("uncategorized active concepts:", len(cands), flush=True)
    if not cands:
        json.dump({"date": time.strftime("%Y-%m-%d"), "additions": adds, "note": "no new concepts"},
                  open(fpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return 0
    cands = sorted(cands)[:top_n]
    groups = "、".join(list(META_GROUPS) + [g for g in SUB_UNIVERSE if g not in META_GROUPS])
    anchors = json.dumps({g: SUB_UNIVERSE[g][:6] for g in SUB_UNIVERSE}, ensure_ascii=False)[:1600]
    prompt = ("你是主线细分宇宙分类器。把下列未分类东财概念归入现有组；明显不属于任何科技/AI组则忽略不输出。"
              "只输出JSON：{\"additions\":[{\"concept\":\"xx\",\"group\":\"组名\"}]}\n"
              "组列表：" + groups + "\n组锚点：" + anchors + "\n候选概念：" + json.dumps(cands, ensure_ascii=False))
    try:
        body = json.dumps({"message": prompt, "session_id": "ruclass_" + time.strftime("%Y%m%d")}).encode()
        req = urllib.request.Request(os.getenv("WAVE_CHAT_URL", "http://marcus-dsh:3001/chat"), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            reply = (resp.read() or b"").decode("utf-8", "ignore")
    except Exception as e:
        print("LLM err", e); return 0
    parsed = {}
    m = re.search(r'\{[^{}]*\}', reply, re.S)
    if m:
        try: parsed = json.loads(m.group(0))
        except Exception: parsed = {}
    new_items = []
    for a in (parsed.get("additions") or []):
        c = a.get("concept"); g = a.get("group")
        if c and g in SUB_UNIVERSE and c not in known_concepts:
            new_items.append({"concept": c, "group": g})
    adds = adds + new_items
    json.dump({"date": time.strftime("%Y-%m-%d"), "additions": adds, "last_candidates": cands[:50]},
              open(fpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("classified additions total:", len(adds), "new:", len(new_items), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
