# -*- coding: utf-8 -*-
"""scan_theme_gaps.py — 域外主线方向扫描 v2(2026-09-08)
输入: concept_hist(521) + THEME_CONCEPTS + chain_map SEED 全概念(更细覆盖域)
流程: 过滤条件型概念(涨停/新高/微盘等) -> 产业方向词典归类(域外且未覆盖) -> 强度/资金/Wolf标注支持
产物: /app/data/theme_gap_scan.json (候选清单: 方向x概念数x r20/r60 x 资金符号比 x Wolf mention x 建议)
"""
import sys, os, json, re
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

# 条件型/非产业概念(名称含即排除)
JUNK = ['昨日', '首板', '涨停', '连板', '炸板', '新高', '热股', '微盘', '超跌', '高送', '次新',
        '预增', '预减', '扭亏', '破净', '回购', '举牌', '解禁', '减持', '转债', '融资融券',
        '沪股通', '深股通', 'MSCI', '富时', '标普', 'ST', '股权', '重组', '并购', '创投',
        '含可转债', '分拆', '税延', '送转', '摘帽', '退市', '风险', '低价', '小盘', '中盘',
        '大盘', '绩优', '白马', '茅指数', '宁组合', '百元', '活跃', '精选', '板块', '指数', '创业板综']
# 产业方向词典: 方向 -> 概念名包含词(域外判定用)
DIRS = {
 '有色/工业金属(铜铝锌铅锡镍钴钨钼)': ['铜缆', '铜', '铝', '锌', '铅', '锡', '镍', '钴', '钨', '钼', '锑', '小金属', '有色', '工业金属', '金属新材料'],
 '面板/显示(LCD/OLED/MiniLED)': ['OLED', 'MicroLED', 'MiniLED', '面板', 'LED', '光学光电子', '显示'],
 '贵金属': ['黄金', '白银', '贵金属'],
 '稀土磁材': ['稀土', '磁材', '磁性材料'],
 'PCB/覆铜板': ['PCB', '覆铜板', '铜箔'],
 '液冷/散热': ['液冷', '散热', '制冷'],
 '低空/飞行器': ['低空', '飞行汽车', 'eVTOL', '无人机', '通用航空'],
 '卫星/航天应用': ['卫星', '商业航天', '航天'],
 '机器人细分': ['减速器', '丝杠', '执行器', '灵巧手', '空心杯', '传感器'],
 '汽车细分': ['激光雷达', '毫米波', '线控', '热管理', '一体化压铸', 'eVTOL'],
 '半导体细分': ['氮化镓', '碳化硅', '第三代半导体', '第四代半导体', '先进封装', 'Chiplet', '光刻'],
 'AI应用细分': ['AI眼镜', '智能体', 'AI语料', 'AI手机', 'AI PC', 'CPO'],
 '算力细分': ['液冷', 'IDC', '算力', '东数西算'],
 '电力细分': ['虚拟电厂', '特高压', '绿电', '核电', '雅下'],
 '消费细分': ['免税', '零售', '旅游', '酒店', '乳业', '调味品', '预制菜', '白酒', '啤酒', '食品'],
 '农业细分': ['种', '转基因', '养殖', '猪', '鸡', '饲料', '水产', '渔业', '乳', '土地', '乡村', '农'],
 '医药细分': ['CXO', '减肥药', '创新药', '中药', '疫苗', '血液制品', '医美', '眼科', '口腔', '辅助生殖'],
 '军工细分': ['军工', '航空', '航天', '船舶', '导弹', '雷达', '红外', '导航'],
 '新能源细分': ['固态电池', '钠离子', '储能', '光伏', '风电', '氢能', '充电桩', '锂'],
 '金融细分': ['券商', '证券', '银行', '保险', '参股', '数字货币', '跨境支付', '金融科技'],
 '基建细分': ['基建', '一带一路', '水泥', '钢铁', '建材', '水利', '铁路', '港口', '工程'],
 '传媒细分': ['游戏', '影视', '短剧', '动漫', '教育', '体育'],
 '化工细分': ['化工', '化纤', '染料', '钛白粉', '氟化工', '煤化工', '化肥', '农药'],
 '医药商业': ['药店', '医药商业', '中药'],
 '其他汽车': ['整车', '零部件', '汽车'],
 '综合/未知': [],
}
# Wolf 标注 theme 词 -> 方向支持计数(直接用原词)
def main():
    import chain_map as cm
    from fusion_mainline import THEME_CONCEPTS
    cov = set()
    for cons in THEME_CONCEPTS.values(): cov.update(cons)
    for segs in cm.SEED.values():
        for s in segs: cov.update(s.get('concepts', []))
    hist = json.load(open(os.path.join(DATA, 'concept_hist.json'), encoding='utf-8'))
    labels = json.load(open(os.path.join(DATA, 'wolf_labels_v2.json'), encoding='utf-8'))['mainline']
    from collections import Counter
    label_cnt = Counter(str(l.get('theme')) for l in labels)
    def pxlast(v, off):
        cl = v.get('close') or []
        for j in range(len(cl) - 1 - off, -1, -1):
            if cl[j] is not None: return cl[j]
        return None
    gaps = {}
    scanned = 0
    for k, v in hist.items():
        name = v.get('name') or k
        if name in cov: continue
        if any(w in name for w in JUNK): continue
        scanned += 1
        dir_hit = None
        for d, words in DIRS.items():
            if d == '综合/未知': continue
            if any(w and w in name for w in words):
                dir_hit = d; break
        if dir_hit is None: dir_hit = '其他-待归类'
        c0 = pxlast(v, 20); c1 = pxlast(v, 0)
        if not c0 or not c1 or c0 <= 0: continue
        r20 = (c1 / c0 - 1) * 100
        g = gaps.setdefault(dir_hit, {'concepts': [], 'r20s': [], 'wolf': set()})
        g['concepts'].append(name); g['r20s'].append(round(r20, 1))
        for lname in label_cnt:
            if lname and (lname in name or name in lname or any(w and w in lname for w in DIRS.get(dir_hit, []))):
                g['wolf'].add(lname)
    out = []
    for d, g in gaps.items():
        if len(g['concepts']) < 1: continue
        out.append({'direction': d, 'concept_n': len(g['concepts']),
                    'r20_avg': round(sum(g['r20s']) / len(g['r20s']), 1),
                    'r20_top': sorted(g['concepts'], key=lambda c: -g['r20s'][g['concepts'].index(c)])[:6],
                    'concepts': g['concepts'][:12],
                    'wolf_support': sorted(g['wolf']),
                    'strength': '强' if round(sum(g['r20s']) / len(g['r20s']), 1) > 4 else ('中' if round(sum(g['r20s']) / len(g['r20s']), 1) > 1 else '弱')})
    out.sort(key=lambda x: (-x['r20_avg'], -x['concept_n']))
    json.dump({'date': '20260908', 'scanned_uncov': scanned, 'directions': out},
              open(os.path.join(DATA, 'theme_gap_scan.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('域外未覆盖产业概念扫描数:', scanned)
    for x in out[:22]:
        print(' %-22s n=%-3d r20=%+.1f%% %s wolf=%s' % (x['direction'], x['concept_n'], x['r20_avg'],
              str(x['r20_top'][:3]), x['wolf_support'][:3]))
    print('WROTE /app/data/theme_gap_scan.json')

if __name__ == '__main__':
    main()
