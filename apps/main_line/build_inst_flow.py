# -*- coding: utf-8 -*-
"""build_inst_flow.py — 机构通道高门槛信号(step2 ②, 2026-09-09)
龙虎榜机构席位(top_inst net_buy, exalter含'机构'=机构专用; 其余=游资/营业部) 与 北向持股(hk_hold vol)
  按主题成分聚合 5/20 日净买/持股变化。产物 /app/data/theme_inst_flow.json
用法: python3 build_inst_flow.py [--date 20260908]
"""
import sys, os, json, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

def main():
    date8 = None
    for i, a in enumerate(sys.argv[1:]):
        if a == '--date': date8 = sys.argv[i + 2]
    from trend_confirm import load_by_name
    from fusion_mainline import THEME_CONCEPTS, MAIN_THEMES
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    date8 = date8 or time.strftime('%Y%m%d')
    by = load_by_name(os.path.join(DATA, 'concept_long.json'))
    days = sorted({d for v in by.values() for d in v.get('dates', []) if d <= date8})
    if len(days) < 22: print('天数不足'); return
    w5 = days[-5:]; w20 = days[-21:]
    import sqlite3
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    th_members = {}
    for th, cons in THEME_CONCEPTS.items():
        ts = []
        for c in cons:
            for r in db.execute('SELECT ts_code FROM stock_concept_map WHERE concept_name=?', (c,)):
                ts.append(r[0])
        th_members[th] = set(dict.fromkeys(ts))
    themes = [th for th in MAIN_THEMES if th in THEME_CONCEPTS]
    # 龙虎榜机构/营业部席位净买: 拉 w5/w20 区间逐日 top_inst
    def fetch_top_inst(days_list):
        rows = []
        for d in days_list:
            try:
                df = pro.top_inst(trade_date=d)
                if df is not None and not df.empty:
                    for _, r in df.iterrows():
                        rows.append({'d': str(r['trade_date']).replace('-', ''), 'ts': str(r['ts_code']),
                                     'exalter': str(r.get('exalter') or ''), 'net': float(r.get('net_buy') or 0)})
            except Exception as e:
                print('top_inst err', d, str(e)[:50])
            time.sleep(0.1)
        return rows
    inst_rows = fetch_top_inst(w20)   # 全 20 日窗口(修: 原只拉6天致 20 日值失真)
    print('top_inst rows', len(inst_rows), flush=True)
    # 北向: hk_hold 该网关返回港股(00001.HK), hsgt_top10 net_amount 空 -> 不可用(注明, 待净额源)
    hk = {}
    north_note = 'north_unavailable: hk_hold=港股, hsgt_top10 net空'
    # 聚合
    out = {'date': date8, 'note': north_note, 'window': {'d5': w5[0], 'd20': w20[0], 'now': days[-1]}, 'themes': []}
    for th in themes:
        mem = th_members.get(th) or set()
        inst5 = sum(r['net'] for r in inst_rows if r['d'] >= w5[0] and r['ts'] in mem and '机构' in r['exalter'])
        inst20 = sum(r['net'] for r in inst_rows if r['d'] >= w20[0] and r['ts'] in mem and '机构' in r['exalter'])
        yz5 = sum(r['net'] for r in inst_rows if r['d'] >= w5[0] and r['ts'] in mem and '机构' not in r['exalter'])
        north = None
        out['themes'].append({'theme': th, 'lhb_inst_net5': round(inst5, 0), 'lhb_inst_net20': round(inst20, 0),
                              'lhb_yz_net5': round(yz5, 0), 'north_hold_chg20_pct': None,
                              'north_covered': 0})
        print('%-12s 机构净买5/20日: %+.0f/%+.0f万 游资5日%+.0f万' % (th, inst5 / 1e4, inst20 / 1e4, yz5 / 1e4), flush=True)
    json.dump(out, open(os.path.join(DATA, 'theme_inst_flow.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('WROTE /app/data/theme_inst_flow.json')

if __name__ == '__main__':
    main()
