# -*- coding: utf-8 -*-
"""bt_sub_universe_dsh.py — 历史日期上用 dsh 派生子方向的回测(信号代理层)
对每个历史日期: 取该日 main_line_state 的 main_line → THEME_CONCEPTS 概念 → dsh 聚子方向(≤9)
→ 用 concept_hist 在该日期重算各子方向资金(5d净流入) → 复现 rotation_healthy/sucking/inflow/top1_share。
同时计算 旧写死 SUB_UNIVERSE 同口径, 对比子方向数/资金流入侧变化。
用法: python -u apps/main_line/bt_sub_universe_dsh.py [--dates 2025-09-18,2025-09-19,2026-01-17,2026-02-25,2026-08-27]
"""
import os, sys, json, time, re, collections, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fusion_mainline import THEME_CONCEPTS
except Exception:
    THEME_CONCEPTS = {}
try:
    from rotation_universe import SUB_UNIVERSE, META_GROUPS, _norm, DATA
except Exception:
    SUB_UNIVERSE = {}; META_GROUPS = set(); _norm = lambda s: str(s).replace(" ", "").replace("　", ""); DATA = os.environ.get("DATA_DIR", "data")
CHAT_URL = os.getenv("WAVE_CHAT_URL", "http://marcus-dsh:3001/chat")
MAX_SUBS = 9
ROT_NET_DAYS = int(os.getenv("ROT_NET_DAYS", "10"))
ROT_NET_MIN = float(os.getenv("ROT_NET_MIN", "0"))
ROT_TOP_N = int(os.getenv("ROT_TOP_N", "3"))
ROT_CONFIRM_DAYS = int(os.getenv("ROT_CONFIRM_DAYS", "3"))

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
                # /chat 可能返回 {"reply": "<json string>"} 包装
                if isinstance(obj, dict) and isinstance(obj.get("reply"), str):
                    r = obj["reply"].strip()
                    # 去掉可能的外部代码栅栏
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

def _wolf_examples(theme, concepts):
    """从 SUB_UNIVERSE 抽取与 theme 概念重叠的子方向, 作为狼大/行业级子方向归类的 few-shot 示例。"""
    cset = set(_norm(c) for c in concepts)
    ex = {}
    for sub, kws in SUB_UNIVERSE.items():
        if sub in META_GROUPS: continue
        matched = [k for k in kws if any(_norm(k) in cs or cs in _norm(k) for cs in cset)]
        if matched:
            ex[sub] = matched
    if not ex: return ""
    return ("参考狼大/行业级子方向归类示例(子方向名: [概念]): " + json.dumps(ex, ensure_ascii=False) +
            "。请按此行业级风格把给定概念归类: 语义相同的概念归到同一子方向, 没有合适示例的可单独成组或归入最相近组。")

def _skeleton(theme, concepts):
    """确定性把概念归并到 SUB_UNIVERSE 骨架(狼式行业级子方向), 每个概念覆盖, 组数有界, 不靠 dsh 聚合。"""
    cset = set(_norm(c) for c in concepts)
    skel = {}
    for sub, kws in SUB_UNIVERSE.items():
        if sub in META_GROUPS: continue
        if any(any(_norm(k) in cs or cs in _norm(k) for k in kws) for cs in cset):
            skel[sub] = kws
    out = {}; assigned = set()
    for c in concepts:
        nc = _norm(c); best = None; bests = 0
        for sub, kws in skel.items():
            hit = sum(1 for k in kws if _norm(k) in nc or nc in _norm(k))
            if hit > bests:
                bests = hit; best = sub
        if best:
            out.setdefault(best, []).append(c); assigned.add(nc)
        else:
            out.setdefault(c, []).append(c); assigned.add(nc)
    return out

def dsh_subs(theme, concepts):
    """两步: ①无上限预分组(丢给dsh, 加狼式few-shot, 保全覆盖) ②组内合并精简(归并相似组, 仍保全覆盖, 不截断)。"""
    if not concepts: return {}
    ex = _wolf_examples(theme, concepts)
    prompt = ("你是主线子方向分类器。把主题 '%s' 的东财概念按行业级子方向归类: 相似概念归并(参考狼大示例), 一个概念也可单独成组; "
              "组名用简要中文; 只输出JSON: {\"subs\":{\"组名\":[\"概念\",...]}}"
              "\n" + ex + "\n主题概念: " + json.dumps(concepts, ensure_ascii=False)) % (theme,)
    r1 = _sanitize(theme, concepts, (_llm(prompt, "bt_sub") or {}).get("subs") or {})
    if len(r1) > MAX_SUBS:
        try:
            r2 = dsh_merge(theme, r1, concepts)
            if r2: return r2
        except Exception:
            pass
    return r1

def dsh_merge(theme, groups, concepts):
    """组内合并精简: 把第一轮分组按狼式/行业级骨架归并成更少更语义化的组, 不丢弃任何概念。"""
    gjson = json.dumps(groups, ensure_ascii=False)
    ex = _wolf_examples(theme, concepts)
    prompt = ("你是主线子方向合并器。下面主题 '%s' 的第一轮子方向分组, 请把相似/重叠的子方向按狼大/行业级风格合并成更少、更语义化的组"
              "(建议 3-%d 组, 允许按语义重组), 合并后每个概念必须仍恰好属于一组, 不得丢弃任何概念; 只输出JSON: {\"subs\":{\"组名\":[\"概念\",...]}}"
              "\n" + ex + "\n第一轮分组: " + gjson) % (theme, MAX_SUBS)
    r = (_llm(prompt, "bt_merge") or {}).get("subs") or {}
    return _sanitize(theme, concepts, r)

def _cap(subs):
    items = list((subs or {}).items()); items.sort(key=lambda kv: -len(set(kv[1])))
    if len(items) <= MAX_SUBS: return dict(items)
    keep = dict(items[:MAX_SUBS]); largest = items[0][0]
    extra = [c for _, arr in items[MAX_SUBS:] for c in arr]
    keep[largest] = list(keep[largest]) + [c for c in extra if c not in keep[largest]]
    return keep

def _sanitize(theme, concepts, subs):
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
        if _norm(c) not in assigned: out.setdefault(c, []).append(c)
    return out

def fund_at(hist, date, kws):
    """date=YYYY-MM-DD; 对 hist 里 name 命中 kws 的概念, 取 <=date 的 net_amount 最后5日求和(亿)。"""
    tot = 0.0
    for c, a in hist.items():
        if not isinstance(a, dict) or not isinstance(a.get("name"), str): continue
        nm = a["name"]
        if not any(_norm(k) in _norm(nm) for k in kws): continue
        dates = a.get("dates") or []
        net = a.get("net_amount") or []
        s = 0.0; cnt = 0
        dtn = str(date).replace("-", "")   # concept_hist 日期是 YYYYMMDD, 归一后再比较
        for d, v in reversed(list(zip(dates, net))):
            if v is None: continue
            if str(d) > dtn: continue
            s += float(v); cnt += 1
            if cnt >= ROT_NET_DAYS: break
        tot += s
    return tot

def proxies(hist, date, SU):
    """生产口径: 按子方向 ROT_NET_DAYS 日净流入 + 幅度门槛 + TOP-N领头 算 rotation_healthy/sucking。"""
    nets = {}
    for sub, kws in SU.items():
        if sub in META_GROUPS: continue
        nets[sub] = fund_at(hist, date, kws)
    if not nets: return {"n_subs":0}
    subs = list(nets.keys())
    sig = [s for s in subs if abs(nets.get(s, 0)) >= ROT_NET_MIN] or subs
    top_sub = max(sig, key=lambda s: abs(nets[s]))
    by = sorted(sig, key=lambda s: abs(nets[s]), reverse=True)[:ROT_TOP_N]
    leader_sum = sum(abs(nets[s]) for s in by) or 1
    top1_share = abs(nets[by[0]]) / leader_sum if by else 0.0
    leader_in = [s for s in by if nets[s] > 0]
    in_subs = [s for s in subs if nets[s] > 0]
    healthy = bool(len(leader_in) >= 2 and top1_share < 0.65)
    sucking = bool(top1_share >= 0.65 or len(leader_in) <= 1)
    return {"n_subs": len(nets), "inflow_subs": in_subs, "top1_sub": top_sub,
            "top1_share": round(top1_share, 2), "healthy": healthy, "sucking": sucking,
            "nets": {k: round(v, 2) for k, v in nets.items()}}

def date_of(path):
    p = os.path.basename(path)
    # main_line_state_YYYY-MM-DD.json
    if p.startswith("main_line_state_") and p.endswith(".json"):
        core = p[len("main_line_state_"):-5]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", core): return core
    return None

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dates = args[0].split(",") if args else None
    hist = load("concept_hist.json")
    mlfiles = sorted([p for p in __import__("glob").glob(os.path.join(DATA, "main_line_state_*.json"))])
    rows = []
    for p in mlfiles:
        d = date_of(p)
        if not d: continue
        if dates and d not in dates: continue
        st = json.load(open(p, encoding="utf-8"))
        main = st.get("main_line") or ""
        if not main: continue
        cons = THEME_CONCEPTS.get(main) or []
        if not cons: continue
        # dsh 子方向 (两步: 预分组+合并精简, 无截断)
        try:
            ds = dsh_subs(main, cons)
        except Exception as e:
            ds = _sanitize(main, cons, {})
            print("dsh err at", d, str(e)[:60], flush=True)
        dsh_cover = len({_norm(c) for arr in ds.values() for c in arr})
        # per-concept 回退
        pc = {c: [c] for c in cons}
        # 旧写死 SUB_UNIVERSE
        old = {s: kws for s, kws in SUB_UNIVERSE.items() if s not in META_GROUPS}
        # 确定性骨架归并(狼式行业级): 把概念归并到 SUB_UNIVERSE 骨架, 保覆盖
        skel = _skeleton(main, cons)
        skel_cover = len({_norm(c) for arr in skel.values() for c in arr})
        row = {"date": d, "main_line": main,
               "dsh": {"subs": list(ds.keys()), **proxies(hist, d, ds)},
               "perconcept": {"subs": list(pc.keys()), **proxies(hist, d, pc)},
               "skeleton": {"subs": list(skel.keys()), **proxies(hist, d, skel)},
               "old": {"subs": list(old.keys()), **proxies(hist, d, old)}}
        rows.append(row)
        print("%-12s main=%-14s dsh_n=%2d/%d skel_n=%2d/%d inflow=%d healthy=%s | pc_n=%2d old_n=%2d" % (
            d, main, len(ds), dsh_cover, len(skel), skel_cover, len(row["dsh"]["inflow_subs"]), row["dsh"]["healthy"],
            len(pc), len(old)), flush=True)
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows,
           "note": "dsh=open-set动态子方向(≤9); perconcept=THEME_CONCEPTS每概念一组; old=写死SUB_UNIVERSE。资金=concept_hist net_amount 5日(亿), PIT按日。"}
    json.dump(out, open(os.path.join(DATA, "bt_sub_universe_dsh.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE data/bt_sub_universe_dsh.json rows=%d" % len(rows), flush=True)

if __name__ == "__main__":
    main()
