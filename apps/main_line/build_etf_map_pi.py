# -*- coding: utf-8 -*-
"""build_etf_map_pi.py — 主题->ETF 映射 AI 审核(用服务器 marcus-dsh Pi /chat, 2026-09-08)
用户要求: 人审换成服务器 dsh 镜像(Pi)审核。流程: fund_basic(market=E) -> 关键词预筛 ->
逐主题 POST http://marcus-dsh:3001/chat(禁止工具, 输出严格JSON) -> primary/optional/rejected
产物: /app/data/etf_theme_map_pi.json (+ diff vs 人审 curated)"""
import sys, os, json, time, requests
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')
KW = {
 'AI/算力/科技': ['人工智能', 'AI', '计算机', '通信', '云计算', '软件', '大数据', '算力'],
 '半导体/芯片': ['半导体', '芯片', '集成电路'],
 '新能源/电池': ['新能源', '电池', '锂', '光伏', '储能', '新能源车', '碳中和'],
 '军工/航天': ['军工', '国防', '航空', '航天'],
 '资源/周期': ['有色', '资源', '稀土', '黄金', '煤炭', '钢铁', '大宗'],
 '金融': ['证券', '银行', '金融', '保险'],
 '消费/内需': ['消费', '白酒', '食品', '家电', '主要消费', '旅游'],
 '医药': ['医药', '医疗', '创新药', '生物'],
 '稳增长/基建': ['基建', '建材', '一带一路', '中字头', '央企'],
 '机器人/智能制造': ['机器人', '工业母机', '机床', '智能制造'],
 '汽车/智驾': ['汽车', '车', '零部件', '智能驾驶'],
 '传媒/游戏': ['传媒', '游戏', '动漫', '影视', '文娱'],
 '电力/公用': ['电力', '公用', '绿电', '核电', '特高压', '能源'],
 '农业': ['农业', '养殖', '粮食', '种业', '乡村', '畜牧', '乳业', '农'],
}
PREFIX = ('这是一个离线ETF主题映射审核任务。禁止调用任何工具、禁止交易建议，只基于我提供的数据判断。'
          '标准: 1)ETF跟踪指数与主题覆盖一致; 2)选市场主流宽基代表不选细分子赛道; '
          '3)上市日期须早于2026-01-01; 4)排除LOF/REIT/联接/分级/增强类。'
          '只输出严格JSON(不要代码围栏不要解释): '
          '{"theme":"...","primary":[{"ts_code":"...","name":"...","reason":"一句话"}],'
          '"optional":[{"ts_code":"...","name":"...","reason":"一句话"}],'
          '"rejected":[{"ts_code":"...","reason":"..."}]}')

def review(theme, concepts, cands, cur_note=''):
    m = PREFIX + cur_note + ' 主题=' + theme + ' 概念=' + ','.join(concepts) + ' 候选ETF=' + json.dumps(cands, ensure_ascii=False)
    r = requests.post('http://marcus-dsh:3001/chat', json={'message': m, 'session_id': 'etf-map-' + theme}, timeout=240)
    if r.status_code != 200:
        return None, 'HTTP ' + str(r.status_code) + ' ' + (r.text or '')[:100]
    reply = (r.json().get('reply') or '').strip()
    mark = chr(96) * 3
    if reply.startswith(mark + 'json'): reply = reply[len(mark) + 4:]
    elif reply.startswith(mark): reply = reply[len(mark):]
    if reply.endswith(mark): reply = reply[:-len(mark)]
    i, j = reply.find('{'), reply.rfind('}')
    if i < 0 or j <= i:
        return None, 'no-json: ' + reply[:160]
    try:
        return json.loads(reply[i:j + 1]), None
    except Exception as e:
        return None, 'json-err: ' + str(e)[:100]

def main():
    from app.api.market import _get_tushare_pro
    from fusion_mainline import THEME_CONCEPTS
    pro = _get_tushare_pro()
    fb = pro.fund_basic(market='E', status='L', fields='ts_code,name,list_date')
    all_etf = []
    if fb is not None and not fb.empty:
        for _, row in fb.iterrows():
            all_etf.append({'ts_code': str(row['ts_code']), 'name': str(row['name']), 'list': str(row['list_date'] or '')[:10]})
    import re
    bad = re.compile(r'REIT|LOF|联接|分级|增强|QDII')
    try:
        from build_etf_flow import ETFS as OLD_MAP
    except Exception:
        OLD_MAP = {}
    results = []
    for th, kws in KW.items():
        concepts = THEME_CONCEPTS.get(th, [])
        old_ts = [t for t, _ in OLD_MAP.get(th, [])]
        cur_note = (' 当前映射=' + ','.join(old_ts) + ' (请复核: 保留或换更主流宽基, primary<=2)') if old_ts else ''
        cand = [e for e in all_etf if any(k.lower() in e['name'].lower() for k in kws) and not bad.search(e['name']) and e['list'] < '20260101']
        cand = sorted(cand, key=lambda e: e['list'])[:7]
        if not cand:
            print(th, '无候选', flush=True); continue
        a, err = None, 'retry'
        for attempt in range(3):
            a, err = review(th, concepts, cand, cur_note)
            if not err: break
            print(th, 'attempt', attempt + 1, 'FAIL', err[:80], flush=True)
            time.sleep(3)
        if err:
            results.append({'theme': th, 'primary': [], 'optional': [], 'rejected': [], 'error': err})
            continue
        results.append(a)
        pk = ', '.join(e.get('ts_code', '?') for e in a.get('primary', []))
        print('%-12s primary: %s  optional: %s' % (th, pk, ', '.join(e.get('ts_code', '') for e in a.get('optional', []))), flush=True)
        time.sleep(1.0)
    try:
        from build_etf_flow import ETFS as old
    except Exception:
        old = {}
    diff = {}
    for a in results:
        ai_set = {e['ts_code'] for e in a.get('primary', [])}
        old_set = {t for t, _ in old.get(a['theme'], [])}
        diff[a['theme']] = {'old': sorted(old_set), 'ai_primary': sorted(ai_set),
                            'removed': sorted(old_set - ai_set), 'added': sorted(ai_set - old_set)}
    out = {'date': '20260908', 'method': 'pi_server_chat_review_v1', 'themes': results, 'diff_vs_curated': diff}
    json.dump(out, open(os.path.join(DATA, 'etf_theme_map_pi.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE /app/data/etf_theme_map_pi.json')
    for th, d in diff.items():
        if d['removed'] or d['added']:
            print('DIFF', th, '删', d['removed'], '增', d['added'])

if __name__ == '__main__':
    main()