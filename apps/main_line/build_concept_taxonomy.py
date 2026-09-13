# -*- coding: utf-8 -*-
"""build_concept_taxonomy.py — 主线开集 taxonomy: concept→theme→sub-direction (dsh)
种子锚点: fusion_mainline.THEME_CONCEPTS (既有9主题+概念); 在此基础上把 position_class_result 的活跃概念
用 dsh 归到主题(允许造新主题), 再对每个主题 dsh 聚成子方向(上限 MAX_SUBS_PER_THEME=写死科技类子方向数 9)。
输出: data/concept_taxonomy.json {version, ts, themes:{theme:{subs:{sub:[concepts]}}}, concept_theme:{concept:theme}}
用法: python -u apps/main_line/build_concept_taxonomy.py [--full]   (无--full: 增量分类新概念)
"""
import os, sys, json, time, re, collections, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fusion_mainline import THEME_CONCEPTS
except Exception:
    THEME_CONCEPTS = {}
DATA = os.environ.get("DATA_DIR", "data")
CHAT_URL = os.getenv("WAVE_CHAT_URL", "http://marcus-dsh:3001/chat")
TAX = "concept_taxonomy.json"
MAX_SUBS_PER_THEME = 9   # 用户指定: 按写死科技类子方向数(国算/液冷/存储/材料/芯片/光通信/AI应用/AI终端/铜缆电源)

def _norm(s): return str(s).replace(" ", "").replace("　", "")
def load(p):
    try: return json.load(open(os.path.join(DATA, p), encoding="utf-8"))
    except Exception: return {}

def _extract_json(text):
    """稳健提取最外层完整 JSON 对象(支持嵌套/markdown代码栅栏)。"""
    if not text: return {}
    t = text.strip()
    try: return json.loads(t)
    except Exception: pass
    fence = chr(96) * 3
    if fence in t:
        parts = t.split(fence)
        t = parts[1] if len(parts) >= 3 else t
        t = t.strip()
        try: return json.loads(t)
        except Exception: pass
    i = t.find("{")
    if i < 0: return {}
    depth = 0
    for j in range(i, len(t)):
        if t[j] == "{": depth += 1
        elif t[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[i:j+1])
                except Exception:
                    return {}
                if isinstance(obj, dict) and isinstance(obj.get("reply"), str):
                    r = obj["reply"].strip()
                    f = chr(96) * 3
                    if f in r:
                        parts = r.split(f)
                        r = parts[1] if len(parts) >= 3 else r
                        r = r.strip()
                    try:
                        return json.loads(r)
                    except Exception:
                        return _extract_json(r)
                return obj
    return {}

def _llm(prompt, sid):
    body = json.dumps({"message": prompt, "session_id": sid + "_" + time.strftime("%Y%m%d_%H%M")}).encode()
    req = urllib.request.Request(CHAT_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        reply = (resp.read() or b"").decode("utf-8", "ignore")
    return _extract_json(reply)

def _classify_theme(concepts, anchors):
    if not concepts: return {}
    prompt = ("你是A股主线主题分类器。把下列活跃东财概念每个归入一个主线主题。"
              "已有主题及锚点概念(尽量沿用, 不要改名): " + json.dumps(anchors, ensure_ascii=False) +
              "; 若某概念属于全新主题请新建一个主题名(2-6字); 每个概念恰好一个主题, 不许凭空造概念。"
              "只输出JSON: {\"concept_theme\":{\"概念\":\"主题\"}}" +
              "\n候选概念: " + json.dumps(concepts, ensure_ascii=False))
    return (_llm(prompt, "tax_theme") or {}).get("concept_theme") or {}

def _group_subs(theme, concepts):
    if not concepts: return {}
    prompt = ("你是主线子方向分类器。把主题 '%s' 的东财概念聚成若干子方向组(每个子方向一个简短中文名), "
              "每个概念恰好一组, 不凭空增加概念; 组数尽量不超过 %d 个(若超过, 把最相近的归并成一个大组)。"
              "只输出JSON: {\"subs\":{\"组名\":[\"概念\",...]}}" +
              "\n主题概念: " + json.dumps(concepts, ensure_ascii=False)) % (theme, MAX_SUBS_PER_THEME)
    return (_llm(prompt, "tax_sub") or {}).get("subs") or {}

def _cap_subs(subs):
    """保证 <= MAX_SUBS_PER_THEME: 按组内概念数降序, 多余组并入最大的组。"""
    items = list((subs or {}).items())
    if not items: return {}
    items.sort(key=lambda kv: -len(set(kv[1])))
    if len(items) <= MAX_SUBS_PER_THEME:
        return dict(items)
    keep = dict(items[:MAX_SUBS_PER_THEME])
    extra = [c for _, arr in items[MAX_SUBS_PER_THEME:] for c in arr]
    # 并入最大的组
    largest = items[0][0]
    keep[largest] = list(keep.get(largest, [])) + [c for c in extra if c not in keep[largest]]
    return keep

def _sanitize_subs(theme, concepts, subs):
    """只保留给定概念; 每个概念恰好一组; 未归组概念各自成组; 最后 cap。"""
    conc_set = [_norm(c) for c in concepts]
    out = {}; assigned = set()
    for label, arr in (subs or {}).items():
        keep = []
        for c in arr:
            nc = _norm(c)
            hit = next((o for o in concepts if _norm(o) == nc or nc in _norm(o) or _norm(o) in nc), None)
            if hit:
                keep.append(hit); assigned.add(_norm(hit))
        if keep: out.setdefault(label, keep)
    for c in concepts:
        if _norm(c) not in assigned:
            out.setdefault(c, []).append(c)
    return _cap_subs(out)

def build(full=False):
    pos = load("position_class_result.json")
    active = sorted({str(v.get("name")) for v in pos.values() if isinstance(v, dict) and v.get("name")})
    # 已有 anchors
    anchors = {th: list(cons) for th, cons in THEME_CONCEPTS.items()}
    tax = load(TAX)
    concept_theme = dict((tax.get("concept_theme") or {}) if isinstance(tax, dict) else {})
    if full or not concept_theme:
        for th, cons in THEME_CONCEPTS.items():
            for c in cons:
                concept_theme.setdefault(_norm(c), th)
    # 新增活跃概念分类
    # 兼容概念名可能带空格差异; 用归一概念做键, 保留原名为 value
    new_cons = [c for c in active if _norm(c) not in concept_theme]
    err = None
    BATCH = int(os.getenv("TAX_BATCH", "40"))
    if new_cons:
        for bi in range(0, len(new_cons), BATCH):
            batch = new_cons[bi:bi + BATCH]
            try:
                got = _classify_theme(batch, anchors)
                for c, th in (got.get("concept_theme") or {}).items():
                    orig = next((o for o in batch if _norm(o) == _norm(c) or _norm(c) in _norm(o) or _norm(o) in _norm(c)), None)
                    if orig:
                        concept_theme[_norm(orig)] = th
                        anchors.setdefault(th, []).append(orig)
            except Exception as e:
                err = (err or "") + " batch%d:%s;" % (bi, str(e)[:60])
            # 未归类的按 anchors 试探: 命中某主题概念则归该主题, 否则独立概念→主题=概念名
            for c in batch:
                if _norm(c) in concept_theme: continue
                hit_th = next((th for th, cons in THEME_CONCEPTS.items() if any(_norm(k) in _norm(c) or _norm(c) in _norm(k) for k in cons)), None)
                concept_theme[_norm(c)] = hit_th or c
            print("tax: batch %d/%d assigned=%d concepts_now=%d" % (bi // BATCH + 1, (len(new_cons) + BATCH - 1) // BATCH, sum(1 for x in batch if _norm(x) in concept_theme), len(concept_theme)), flush=True)
    # 主题→概念 反查
    theme_concepts = collections.defaultdict(set)
    for c, th in concept_theme.items():
        theme_concepts[th].add(c)
    # 每个主题聚子方向
    themes = {}
    tkeys = sorted(theme_concepts.keys())
    for i, th in enumerate(tkeys, 1):
        clist = sorted(theme_concepts[th])
        groups = {}
        try:
            groups = _group_subs(th, clist)
        except Exception:
            groups = {}
        themes[th] = {"subs": _sanitize_subs(th, clist, groups)}
        print("tax: theme %d/%d [%s] concepts=%d subs=%d" % (i, len(tkeys), th, len(clist), len(themes[th]["subs"])), flush=True)
    out = {"version": time.strftime("%Y-%m-%d"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "themes": themes, "concept_theme": concept_theme, "llm_err": err}
    json.dump(out, open(os.path.join(DATA, TAX), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("taxonomy: themes=%d concepts=%d subs=%d new=%d err=%s" %
          (len(themes), len(concept_theme), sum(len(t['subs']) for t in themes.values()), len(new_cons), err))
    return out

if __name__ == "__main__":
    full = "--full" in sys.argv
    r = build(full=full)
    if r: print(json.dumps(r.get("themes"), ensure_ascii=False, indent=1)[:2000])
