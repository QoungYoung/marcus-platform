# -*- coding: utf-8 -*-
"""chain_dict_refine.py v2 — AI 自举优化词典(2026-09-08, 校准协议: 纯规则起点 -> AI 优化 -> 客观指标对比 v0.6)
用户只熟农业 -> 其余主题无人审, 需 AI 自律; 校准: 给农业'朴素词典'(概念直译+脏概念), 看 AI 能否自己
收敛到 v0.6 质量(召回 金健/登海/苏垦/深粮/牧原/伊利/温氏..., 剔除 泸州老窖/海南机场/杭钢...)。
每轮: 规则层 eval(客观指标) -> AI 读结果+候选概念纯度档案 -> 输出新词典 -> 规则验证 -> 保留改进 -> 循环。
用法: python3 chain_dict_refine.py 农业 [--rounds 3]
"""
import sys, os, json, sqlite3, time, re
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')
VERIFY_MV_N = 20

# ---- 朴素起点(概念直译粗分, 含脏概念) ----
NAIVE_AGRI = {
 '农业': [
   {'role': 'upstream', 'label': '上游·种植/粮食',
    'concepts': ['农业种植', '粮食概念', '土地流转'],
    'kw': ['种植', '粮食', '土地', '农业', '种子']},
   {'role': 'mid', 'label': '中游·养殖/水产/乳业',
    'concepts': ['水产养殖', '乳业', '生态农业', '乡村振兴'],
    'kw': ['养殖', '水产', '乳', '生态', '乡村']},
   {'role': 'downstream', 'label': '下游·农资/农化',
    'concepts': ['农药兽药', '粮食概念'],
    'kw': ['农药', '兽药', '化肥', '农化']},
 ],
}
# 目标与杂质(农业客观校验名单, 用户可目检)
TARGETS = ['北大荒', '隆平高科', '苏垦农发', '登海种业', '金健米业', '深粮控股', '神农种业',
           '牧原股份', '伊利股份', '温氏股份', '海大集团', '圣农发展', '亚钾国际', '扬农化工', '新安股份']
IMPURITY = ['泸州老窖', '海南机场', '杭钢股份', '徐工机械', '中联重科', '重庆银行']

def cand_concepts(db):
    kws = ['农业', '种', '粮食', '猪', '鸡', '饲', '乳', '水产', '渔', '农药', '化肥', '兽',
           '生态', '土地', '乡村', '食用菌', '预制菜', '供销', '棉花', '农产品', '粮油', '转基因', '肉鸡', '生猪']
    alln = [r[0] for r in db.execute('SELECT DISTINCT concept_name FROM stock_concept_map')]
    sel = set()
    for n in alln:
        if any(k in n for k in kws) and not any(b in n for b in ['板块', '指数']):
            sel.add(n)
    return sorted(sel)

def run_rules(segs, names, db, pro):
    import chain_map as cm
    mv, mv_date = cm.fetch_mv(pro, time.strftime('%Y%m%d'))
    res = []
    for seg in segs:
        codes, big, seen_c, seen_b = [], [], set(), set()
        audit = []
        for c in seg['concepts']:
            c12 = cm.concept_stocks(db, c, 12)
            cfull = cm.concept_stocks(db, c, 100)
            for ts in c12:
                if ts not in seen_c: seen_c.add(ts); codes.append(ts)
            for ts in cfull:
                if ts not in seen_b: seen_b.add(ts); big.append(ts)
            mt = sorted([t for t in cfull if t in mv], key=lambda t: -mv[t])[:4]
            audit.append({'concept': c, 'n': len(cfull), 'top': [names.get(t, '') for t in mt]})
        mv_sorted = sorted(big, key=lambda t: -mv.get(t, 0)) if mv else []
        pool = list(dict.fromkeys([t for t in mv_sorted[:VERIFY_MV_N] if mv.get(t)] + codes[:6]))
        vd = {}
        for ts in pool:
            vd[ts] = cm.fina_verdict(pro, ts, seg, names); time.sleep(0.08)
        ver = [vd[t] for t in pool if vd[t]['ok']]
        ver.sort(key=lambda v: -mv.get(v['ts'], 0))
        rej = sorted([vd[t] for t in pool if not vd[t]['ok']], key=lambda v: -mv.get(v['ts'], 0))
        res.append({'label': seg['label'], 'concepts': seg['concepts'], 'kw': seg['kw'], 'audit': audit,
                    'leading': [v['name'] for v in ver[:3]],
                    'verified_all': [v['name'] for v in ver],
                    'rejected_top': [v['name'] for v in rej[:6]]})
    return res

def eval_res(res):
    va = set()
    for s in res: va.update(s['verified_all'])
    lead = set()
    for s in res: lead.update(s['leading'])
    return {'target_hit': sum(1 for t in TARGETS if t in va),
            'target_total': len(TARGETS),
            'impurity_in_verified': [t for t in IMPURITY if t in va],
            'impurity_in_leading': [t for t in IMPURITY if t in lead],
            'leading': {s['label']: s['leading'] for s in res}}

def build_prompt(theme, cands, res, metrics, round_i, prev_rationale):
    sys_p = ('你是 A股产业链词典构建专家。把给定主题的粗词典优化为「纯净、召回足」的环节词典。'
             '已知: 概念表收录脏(异业大盘股混入), 机制只按 概念成分mv前%d+概念序前6 建候选池, 然后主营词kw核实。'
             '你要输出每环节: concepts(从候选概念表精选纯概念, 宁缺毋滥, 剔除会引入异业大盘股的概念) + kw(覆盖该环节主营文本的关键词, 可含单字根)。'
             '原则: 1)真龙头小市值也能被召回(靠纯概念而非大市值); 2)异业(白酒/机械/机场/银行)必须进不了; '
             '3)环节语义贴合产业链(种源-种植收储加工; 养殖饲料乳水产; 农资农化)。'
             '输出严格JSON: {"segments": [{"role": "upstream|mid|downstream", "label": "中文", '
             '"concepts": [候选概念名], "kw": [词]}]}。concepts 必须来自提供的候选清单。'
             '若上一轮结果不满意请给出与上轮不同的结构调整并说明 rationale。' % VERIFY_MV_N)
    user = json.dumps({'theme': theme, 'round': round_i,
                       'candidate_concepts': cands,
                       'current_dict_and_results': res,
                       'metrics': metrics,
                       'prev_rationale': prev_rationale,
                       'must_recall': TARGETS}, ensure_ascii=False)
    return sys_p, user

def main():
    theme = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else '农业'
    rounds = 3
    if '--rounds' in sys.argv:
        rounds = int(sys.argv[sys.argv.index('--rounds') + 1])
    from app.api.market import _get_tushare_pro
    import chain_map as cm
    pro = _get_tushare_pro()
    sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    names = dict(zip(sb['ts_code'], sb['name'])) if sb is not None else {}
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    cands = cand_concepts(db)
    print('== 自举优化 |', theme, '| rounds', rounds, '| 候选概念', len(cands), '==', flush=True)
    segs = NAIVE_AGRI[theme]
    history = []
    prev_rat = ''
    for rd in range(1, rounds + 1):
        print('-- round', rd, '--', flush=True)
        res = run_rules(segs, names, db, pro)
        m = eval_res(res)
        print('   eval: target', m['target_hit'], '/', m['target_total'],
              '| impurity in verified', m['impurity_in_verified'],
              '| leading', {k: v for k, v in m['leading'].items()}, flush=True)
        history.append({'round': rd, 'segs': segs, 'res': res, 'metrics': m})
        if rd == rounds: break
        sys_p, user = build_prompt(theme, cands, res, m, rd, prev_rat)
        try:
            a = cm.llm_retry(sys_p, user)
            ns = a.get('segments') or []
            if not ns: break
            ok_conc = {c: True for c in cands}
            clean = []
            for s in ns[:4]:
                cc = [c for c in (s.get('concepts') or []) if c in ok_conc]
                if cc:
                    clean.append({'role': s.get('role', 'mid'), 'label': s.get('label', ''), 'concepts': cc,
                                  'kw': (s.get('kw') or [])[:40]})
            if not clean: break
            segs = clean
            prev_rat = (a.get('rationale') or '')[:300]
        except Exception as e:
            print('   llm err', str(e)[:80], flush=True); break
    # 对比 v0.6 参考
    ref = AGRI_REF.get(theme)
    out = {'theme': theme, 'date': time.strftime('%Y%m%d'), 'generator': 'chain_dict_refine_v2_selfbuild',
           'rounds': history, 'v06_reference': ref,
           'final_eval': eval_res(history[-1]['res']) if history else None,
           'final_segs': history[-1]['segs'] if history else None}
    json.dump(out, open(os.path.join(DATA, 'chain_dict_selfbuild_' + theme + '.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('WROTE /app/data/chain_dict_selfbuild_' + theme + '.json', flush=True)

AGRI_REF = {'农业': AGRI_V06 if False else None}
# v0.6 参考词典(仅用于报告对照)
AGRI_REF['农业'] = {
 '上游': {'concepts': ['种子', '转基因', '粮食种植', '粮食概念'], 'kw_n': 22},
 '中游': {'concepts': ['生猪养殖', '肉鸡养殖', '水产养殖', '渔业', '饲料', '乳业'], 'kw_n': 14},
 '下游': {'concepts': ['农药兽药', '生态农业'], 'kw_n': 15},
}

if __name__ == '__main__':
    main()
