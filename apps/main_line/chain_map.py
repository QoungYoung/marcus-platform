# -*- coding: utf-8 -*-
"""chain_map 生成器 v3 (2026-09-08): 环节龙头 = 规则层(成分池->fina主营核实->mv排序) + AI裁决层
流程: 每环节 成分池(mv龙头定位) -> fina主营核实剔伪 -> 核实集 total_mv 排序取 top3; rejected 边界股送 DeepSeek
语义复核(依据 fina 主营文本+销售额), in_segment 且 conf>=0.85 回补 leading(source=ai); borderline(0.5<=conf<0.85)/
unknown 保留审计并置 needs_verify; AI 的 suggested_kw 累积到 data/chain_kw_suggestions.json 供每周词典校准自举。
v3 相对 v2: rejected 不再静默丢弃——每个被规则拒绝的候选都有 AI 复核或审计记录; 纯规则版可用 --no-ai 保持 v2 行为。
用法: python3 chain_map.py [YYYYMMDD] [--no-ai]
输出: data/chain_map_{date}.json + data/chain_map_{date}_v2_cmp.json + data/chain_kw_suggestions.json(追加)
"""
import sys, os, json, sqlite3, time, requests, urllib3
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
urllib3.disable_warnings()
DATA = os.environ.get('DATA_DIR', '/app/data')

SEED = {
 "AI/算力/科技": [
   {"role": "upstream", "label": "上游·芯片/光器件/材料设备",
    "concepts": ["AI芯片", "存储芯片", "光通信模块", "CPO概念", "PCB", "液冷概念"],
    "kw": ["芯片", "半导体", "光模块", "光器件", "PCB", "液冷", "制冷", "散热", "存储", "处理器", "CPU", "面板", "显示", "晶圆", "设备"]},
   {"role": "mid", "label": "中游·算力基建/云",
    "concepts": ["算力概念", "数据中心", "云计算", "边缘计算", "东数西算"],
    "kw": ["算力", "服务器", "数据中心", "IDC", "云计算", "算网"]},
   {"role": "downstream", "label": "下游·模型/应用/终端",
    "concepts": ["AI应用", "AIGC概念", "多模态AI", "AI智能体", "AI语料", "DeepSeek概念", "ChatGPT概念", "Kimi概念", "智谱AI", "AI眼镜", "人工智能"],
    "kw": ["AI", "人工智能", "大模型", "应用", "软件", "语料", "智能体", "眼镜", "数字人", "信息服务", "互联网"]},
 ],
 "传媒/游戏": [
   {"role": "upstream", "label": "内容/研发制作",
    "concepts": ["网络游戏", "影视概念", "短剧互动游戏"],
    "kw": ["游戏", "影视", "剧", "内容", "研发", "IP"]},
   {"role": "downstream", "label": "平台/分发/服务",
    "concepts": ["在线教育", "职业教育", "体育产业"],
    "kw": ["平台", "教育", "培训", "体育", "赛事", "发行"]},
 ],
 "消费/内需": [
   {"role": "upstream", "label": "品牌/制造",
    "concepts": ["白酒", "新消费", "消费电子概念"],
    "kw": ["白酒", "酒", "消费", "电子", "品牌", "制造"]},
   {"role": "downstream", "label": "渠道/场景",
    "concepts": ["免税概念", "新零售", "零售概念", "旅游概念", "文娱消费"],
    "kw": ["免税", "零售", "商超", "旅游", "景区", "餐饮", "文娱"]},
 ],
}
LEAD_TOP_N = 3
VERIFY_POOL_MV_N = 12
VERIFY_POOL_CONCEPT_N = 6
AI_CONF_MIN = 0.85
AI_BORDERLINE_MIN = 0.5
AI_MAX_PER_SEG = 10

def concept_stocks(db, cname, limit):
    try:
        cur = db.cursor()
        cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?", (cname, limit))
        return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print('concept err', cname, e); return []

def fetch_mv(pro, date8):
    """全市场 daily_basic(total_mv 万元); 当日盘后未入库时逐日回退"""
    d = date8
    for _ in range(6):
        try:
            df = pro.daily_basic(trade_date=d, fields='ts_code,total_mv')
            if df is not None and not df.empty and len(df) > 1000:
                return {str(r['ts_code']): float(r['total_mv']) for _, r in df.iterrows()}, d
        except Exception as e:
            print('mv', d, 'err', str(e)[:80], flush=True)
        d = str(int(d) - 1)
    return {}, None

def fina_verdict(pro, ts, seg, names):
    v = {'ts': ts, 'name': names.get(ts, ''), 'mainbz': [], 'hit': [], 'ok': False}
    try:
        df = pro.fina_mainbz(ts_code=ts)
        if df is not None and not df.empty:
            df = df.sort_values('bz_sales', ascending=False)
            items = df[~df['bz_item'].isin(['行业', '产品', '地区'])].head(6).to_dict('records')
            tops = [str(r.get('bz_item') or '')[:40] for r in items]
            v['mainbz'] = tops
            hits = [t for t in tops if any(k.lower() in t.lower() for k in seg['kw'])]
            v['hit'] = hits[:3]
            v['ok'] = len(hits) > 0
    except Exception as e:
        v['err'] = str(e)[:80]
    return v

def load_fina_bz(pro, ts):
    try:
        df = pro.fina_mainbz(ts_code=ts)
        if df is None or df.empty: return []
        df = df.sort_values('bz_sales', ascending=False)
        items = df[~df['bz_item'].isin(['行业', '产品', '地区'])].head(8)
        out = []
        for _, r2 in items.iterrows():
            bz = str(r2.get('bz_item') or '')[:40]
            try: s = float(r2.get('bz_sales') or 0)
            except Exception: s = 0
            out.append({'bz': bz, 'sales': int(s)})
        return out
    except Exception: return []

SYSTEM = (
'你是 A股产业链成分研究员。判断公司主营是否真正属于给定产业链环节(该环节是整条产业链的细分场景)。'
'口径: 1) fina主营 bz_item 文本(带销售额)是首要依据, 先看主营是什么、是否主导; '
'2) 概念成分归属只是线索不可当依据(概念表收录脏, 常有跨界巨无霸); '
'3) 只回答“是否属于该环节”, 公司属于同一条产业链的其它环节判 out; '
'4) 主营名目与环节无字面重叠不等于 out(词表可能有盲区), 要按语义判断; '
'5) 信息不足或主营过杂无法确定判 unknown, 不要硬猜。'
'输出严格 JSON(仅对象, 不要代码围栏): {"verdict": "in_segment"|"out"|"unknown", "confidence": 0~1, '
'"reason": "一句话依据(<=40字)", "suggested_kw": [若 in_segment 给出 1-3 个能代表主营的环节词, 否则空数组]}')

_AI_ENABLED = None
def ai_enabled():
    global _AI_ENABLED
    if _AI_ENABLED is None:
        try:
            sys.path.insert(0, '/app/core')
            from core.api_client import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL
            _AI_ENABLED = bool(DEEPSEEK_API_KEY and DEEPSEEK_API_HOST)
        except Exception:
            _AI_ENABLED = False
    return _AI_ENABLED

def llm_call(system_prompt, user_prompt):
    sys.path.insert(0, '/app/core')
    from core.api_client import DEEPSEEK_API_KEY, DEEPSEEK_API_HOST, DEEPSEEK_MODEL
    url = "https://" + DEEPSEEK_API_HOST + "/v1/chat/completions"
    body = {"model": DEEPSEEK_MODEL, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}],
        "temperature": 0.1, "max_tokens": 900}
    r = requests.post(url, headers={"Authorization": "Bearer " + DEEPSEEK_API_KEY,
                                    "Content-Type": "application/json"},
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=90)
    content = r.json()["choices"][0]["message"]["content"]
    return parse_json(content)

def parse_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content[3:]
        if content.startswith("json"): content = content[4:]
    if content.endswith("```"): content = content[:-3]
    i, j = content.find("{"), content.rfind("}")
    if i < 0 or j < i: raise ValueError("no json: " + content[:120])
    return json.loads(content[i:j+1])

def llm_retry(system_prompt, user_prompt):
    last = ''
    for _ in range(2):
        try:
            return llm_call(system_prompt, user_prompt)
        except Exception as e:
            last = str(e)
            time.sleep(1.2)
    return {"verdict": "unknown", "confidence": 0.0, "reason": "llm_err:" + last[:50], "suggested_kw": []}

def ai_review_one(pro, ts, name, seg, theme):
    bz = load_fina_bz(pro, ts)
    user = json.dumps({"theme": theme, "segment": seg["label"], "role": seg.get("role"),
                       "环节概念": seg.get("concepts", []),
                       "company": {"ts": ts, "name": name, "fina主营top(销售额万元)": bz},
                       "规则初筛": "rejected: 主营文本未命中环节关键词"}, ensure_ascii=False)
    return llm_retry(SYSTEM, user)

def kw_suggest_append(theme, seg, ts, name, verdict):
    """累积 AI suggested_kw 到 data/chain_kw_suggestions.json (同票同段同词去重)"""
    p = os.path.join(DATA, 'chain_kw_suggestions.json')
    try:
        acc = json.load(open(p, encoding='utf-8'))
    except Exception:
        acc = {'entries': []}
    known = set()
    for e in acc['entries']:
        for w in e.get('kw', []):
            known.add((e['theme'], e['seg'], e['ts'], w))
    new = [w for w in (verdict.get('suggested_kw') or []) if (theme, seg['label'], ts, w) not in known]
    if new:
        acc['entries'].append({'theme': theme, 'seg': seg['label'], 'ts': ts, 'name': name,
                               'kw': new, 'confidence': verdict.get('confidence'),
                               'reason': (verdict.get('reason') or '')[:60], 'date': time.strftime('%Y%m%d')})
        json.dump(acc, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return len(new)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    no_ai = '--no-ai' in sys.argv
    date8 = args[0] if args else None
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    names = {}
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        if sb is not None and not sb.empty: names = dict(zip(sb['ts_code'], sb['name']))
    except Exception as e:
        print('stock_basic fail', str(e)[:80])
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    t0 = time.time()
    import datetime
    today = datetime.date.today().strftime('%Y%m%d')
    date8 = date8 or today
    try:
        from app.api.market import _get_tushare_pro as _p
        cal = _p().trade_cal(exchange='SSE', start_date='20260801', end_date=date8, is_open='1')
        if cal is not None and not cal.empty:
            date8 = str(cal['cal_date'].astype(str).max()).replace('-', '')
    except Exception as e:
        print('trade_cal fail, use date=', date8, str(e)[:80], flush=True)
    mv, mv_date = fetch_mv(pro, date8)
    use_ai = (not no_ai) and ai_enabled()
    print('chain_map v3 | date', date8, '| mv_date', mv_date, '| ai', 'ON' if use_ai else ('off(--no-ai)' if no_ai else 'UNAVAILABLE'), flush=True)
    out = {'date': date8, 'mv_date': mv_date, 'generated_by': 'chain_map_v3_mv_verified_ai',
           'method': 'rules(mv+fina核实) top3 -> rejected 边界股 AI 语义复核回补; conf>=%.2f 回补, 词典自举' % AI_CONF_MIN,
           'ai': {'enabled': use_ai, 'conf_min': AI_CONF_MIN}, 'themes': []}
    cmp_rows = []
    seen_promoted = set()   # 跨段去重标记用
    for theme, segs in SEED.items():
        t_seg = []
        for seg in segs:
            codes, big, seen_c, seen_b = [], [], set(), set()
            for c in seg['concepts']:
                for ts in concept_stocks(db, c, 12):
                    if ts not in seen_c: seen_c.add(ts); codes.append(ts)
                for ts in concept_stocks(db, c, 100):
                    if ts not in seen_b: seen_b.add(ts); big.append(ts)
            mv_sorted = sorted(big, key=lambda c: -mv.get(c, 0)) if mv else []
            pool = list(dict.fromkeys([c for c in mv_sorted[:VERIFY_POOL_MV_N] if mv.get(c)] + codes[:VERIFY_POOL_CONCEPT_N]))
            verdicts = {}
            for ts in pool:
                verdicts[ts] = fina_verdict(pro, ts, seg, names); time.sleep(0.12)
            verified = [verdicts[ts] for ts in pool if verdicts[ts]['ok']]
            verified.sort(key=lambda v: -mv.get(v['ts'], 0))
            for i, v in enumerate(verified):
                v['mv'] = round(mv.get(v['ts'], 0), 0); v['mv_rank'] = i + 1
            rejected = sorted([verdicts[ts] for ts in pool if not verdicts[ts]['ok']],
                              key=lambda v: -mv.get(v['ts'], 0))
            for v in rejected:
                v['mv'] = round(mv.get(v['ts'], 0), 0)
                v['reason'] = 'mainbz_not_matched_by_kw'
            # ---- AI 裁决层 ----
            ai_stats = {'reviewed': 0, 'promoted': 0, 'out': 0, 'unknown': 0, 'borderline': 0, 'err': 0}
            kw_new = 0
            promoted_ai = []
            borderline = []
            for rv in rejected[:AI_MAX_PER_SEG]:
                if not use_ai: break
                ai_stats['reviewed'] += 1
                a = ai_review_one(pro, rv['ts'], rv.get('name', ''), seg, theme)
                v = a.get('verdict', 'unknown'); conf = float(a.get('confidence') or 0)
                rv['ai'] = {'verdict': v, 'confidence': round(conf, 2), 'reason': (a.get('reason') or '')[:60]}
                if v == 'in_segment' and conf >= AI_CONF_MIN:
                    ai_stats['promoted'] += 1
                    if rv['ts'] in seen_promoted: rv['cross_seg'] = True
                    seen_promoted.add(rv['ts'])
                    promoted_ai.append(rv)
                    kw_new += kw_suggest_append(theme, seg, rv['ts'], rv.get('name', ''), a)
                elif v == 'in_segment' and conf >= AI_BORDERLINE_MIN:
                    ai_stats['borderline'] += 1; borderline.append(rv)
                elif v == 'out':
                    ai_stats['out'] += 1
                elif v == 'unknown':
                    ai_stats['unknown'] += 1
                else:
                    ai_stats['err'] += 1
                time.sleep(0.4)
            # final leading: rule verified + ai promoted 统一按 mv 排 top3
            final_pool = []
            for v in verified:
                v2 = dict(v); v2['source'] = 'rule_mv'; final_pool.append(v2)
            for rv in promoted_ai:
                rv2 = dict(rv); rv2['source'] = 'ai_review'; rv2['ok'] = True; final_pool.append(rv2)
            final_pool.sort(key=lambda x: -x.get('mv', 0))
            leading = final_pool[:LEAD_TOP_N]
            ai_kept = [v for v in rejected if v['ts'] not in [x['ts'] for x in promoted_ai + borderline]]
            needs_verify = (len(leading) < LEAD_TOP_N) or bool(ai_stats['unknown']) or bool(borderline)
            seg_node = {'role': seg['role'], 'label': seg['label'], 'concepts': seg['concepts'],
                        'pool_n': len(big), 'mv_date': mv_date, 'candidates': codes[:6],
                        'leading_verified': leading, 'rejected': ai_kept, 'needs_verify': needs_verify,
                        'ai_reviewed': ai_stats['reviewed'], 'ai_promoted': promoted_ai, 'ai_borderline': borderline,
                        'ai_stats': ai_stats, 'kw_suggest_new': kw_new, 'method': 'mv_verified+ai' if use_ai else 'mv_verified'}
            t_seg.append(seg_node)
            old_names = [verdicts[ts]['name'] for ts in codes[:2] if ts in verdicts]
            new_names = [x['name'] + ('*' if x.get('source') == 'ai_review' else '') for x in leading]
            ai_prom_names = [x['name'] for x in promoted_ai]
            cmp_rows.append({'theme': theme, 'seg': seg['label'], 'old_top2': old_names, 'new_leading': new_names,
                             'ai_promoted': ai_prom_names, 'ai_stats': ai_stats, 'needs_verify': needs_verify,
                             'pool_n': len(big)})
            print('[' + theme + '][' + seg['label'] + '] pool=' + str(len(big)) + ' verified=' + str(len(verified))
                  + ' NEW ' + str(new_names) + ' | ai_reviewed=' + str(ai_stats['reviewed']) + ' promoted=' + str(ai_prom_names)
                  + ' out=' + str(ai_stats['out']) + ' unknown=' + str(ai_stats['unknown']) + ' borderline=' + str(ai_stats['borderline'])
                  + ' kw_new=' + str(kw_new) + ' needs_verify=' + str(needs_verify), flush=True)
        out['themes'].append({'theme': theme, 'segments': t_seg,
                              'completeness': {'segments': len(t_seg), 'has_leader': any(len(s['leading_verified']) for s in t_seg)}})
        print('[' + theme + '] 环节' + str(len(t_seg)) + ' 用时' + str(int(time.time() - t0)) + 's', flush=True)
    p = os.path.join(DATA, 'chain_map_' + date8 + '.json')
    with open(p, 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, indent=1)
    cp = os.path.join(DATA, 'chain_map_' + date8 + '_v2_cmp.json')
    with open(cp, 'w', encoding='utf-8') as f:
        json.dump({'date': date8, 'mv_date': mv_date, 'ai': out['ai'], 'rows': cmp_rows}, f, ensure_ascii=False, indent=1)
    print('WROTE', p); print('WROTE', cp)

if __name__ == '__main__':
    main()