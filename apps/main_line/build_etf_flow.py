# -*- coding: utf-8 -*-
"""build_etf_flow.py — ETF 份额流(机构资金未跑第二信号, step2, 2026-09-08)
映射: 人审 curated 主题→主流宽基 ETF(代码/名称经 fund_basic market=E 核验, list>6个月)
信号: 主题 ETF 份额(合并多只) 近5/20交易日变化%; 环境: 全市场两融余额(沪+深 rzye) 20日变化%
产物: /app/data/etf_share_flow.json; 每日增量由调度(可挂 mainline_gate_daily 前)
"""
import sys, os, json, time
sys.path.insert(0, '/app')
DATA = os.environ.get('DATA_DIR', '/app/data')
ETFS = {
 'AI/算力/科技': [('159819.SZ', '易方达人工智能ETF'), ('512720.SH', '国泰计算机ETF'), ('515880.SH', '国泰通信设备ETF')],
 '半导体/芯片': [('512760.SH', '国泰CES半导体芯片ETF'), ('159995.SZ', '华夏半导体芯片ETF')],
 '传媒/游戏': [('512980.SH', '广发传媒ETF'), ('159869.SZ', '华夏动漫游戏ETF')],
 '农业': [('159825.SZ', '富国农业ETF'), ('516810.SH', '华夏农业ETF')],
 '消费/内需': [('159928.SZ', '汇添富主要消费ETF')],
 '医药': [('512010.SH', '易方达医药卫生ETF')],
 '新能源/电池': [('159755.SZ', '广发电池ETF'), ('515030.SH', '华夏新能源车ETF')],
 '军工/航天': [('512660.SH', '国泰军工ETF')],
 '机器人/智能制造': [('562500.SH', '华夏机器人ETF')],
 '汽车/智驾': [('516110.SH', '国泰汽车零部件ETF')],
 '电力/公用': [('159611.SZ', '广发电力公用ETF')],
 '金融': [('512880.SH', '国泰证券ETF'), ('512800.SH', '华宝银行ETF')],
 '资源/周期': [('512400.SH', '南方有色金属ETF')],
 '稳增长/基建': [('516950.SH', '银华基建ETF')],
}

def load_etf_map():
    """ETF 映射: Pi(服务器 dsh 镜像)审核结果优先(etf_theme_map_pi.json primary+optional 补足),
    缺失/文件不存在回退内嵌 curated ETFS(v1 人审)"""
    try:
        raw = json.load(open(os.path.join(DATA, 'etf_theme_map_pi.json'), encoding='utf-8'))
        out = {}
        for a in raw.get('themes', []):
            picks = [e['ts_code'] for e in a.get('primary', [])][:2]
            if len(picks) < 2:
                for e in a.get('optional', []):
                    if len(picks) >= 2: break
                    if e['ts_code'] not in picks: picks.append(e['ts_code'])
            if picks: out[a['theme']] = picks
        if out:
            return out, 'pi_ai_review_v1'
    except Exception:
        pass
    return {th: [t for t, _ in etfs] for th, etfs in ETFS.items()}, 'curated_fallback'

def main():
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    # 交易日历(近 45 交易日)
    cal = pro.trade_cal(exchange='SSE', start_date='20260601', end_date='20260908', is_open='1')
    days = [str(d).replace('-', '') for d in (cal['cal_date'].tolist() if cal is not None else [])]
    days = sorted(days)
    MAP, map_src = load_etf_map()
    print('ETF MAP source:', map_src, 'themes', len(MAP), flush=True)
    shares = {}
    for th, etfs in MAP.items():
        if etfs and isinstance(etfs[0], str):
            etfs = [(t, t) for t in etfs]
        per_etf = []
        for ts, nm in etfs:
            try:
                df = pro.fund_share(ts_code=ts, start_date='20260601', end_date='20260908')
                if df is not None and not df.empty:
                    ser = {}
                    for _, r in df.iterrows():
                        ser[str(r['trade_date']).replace('-', '')] = float(r['fd_share'])
                    if ser:
                        per_etf.append({'ts': ts, 'name': nm, 'series': ser})
            except Exception as e:
                print('fs err', ts, str(e)[:60])
            time.sleep(0.15)
        shares[th] = per_etf
    def etf_chg(ser, k):
        ds = sorted(ser)
        if len(ds) <= k: return None, None
        a, b = ser[ds[-1 - k]], ser[ds[-1]]
        return ((b / a - 1.0) * 100.0 if a else None), ds[-1]
    import statistics
    out = {'date': '20260908', 'themes': []}
    for th, per in shares.items():
        c5 = [v for v in (etf_chg(e['series'], 5)[0] for e in per) if v is not None]
        c20 = [v for v in (etf_chg(e['series'], 20)[0] for e in per) if v is not None]
        out['themes'].append({'theme': th, 'etfs': [e['ts'] for e in per],
                              'd5': round(statistics.median(c5), 2) if c5 else None,
                              'd20': round(statistics.median(c20), 2) if c20 else None,
                              'per_etf': [{'ts': e['ts'], 'd5': etf_chg(e['series'], 5)[0] and round(etf_chg(e['series'], 5)[0], 2),
                                           'last': etf_chg(e['series'], 5)[1]} for e in per]})
    # margin 两融环境
    try:
        def mg(d):
            df = pro.margin(trade_date=d)
            if df is None or df.empty: return None
            return float(df['rzye'].sum())
        def mg_recent(idx):
            for i in range(idx, max(idx - 5, 0), -1):
                v = mg(days[i])
                if v is not None: return v, days[i]
            return None, None
        m_now, dn = mg_recent(len(days) - 1)
        m_20, d20 = mg_recent(len(days) - 21) if len(days) > 21 else (None, None)
        out['margin'] = {'rzye_now': m_now, 'date_now': dn, 'rzye_20d_ago': m_20, 'date_20': d20,
                         'd20_pct': round((m_now / m_20 - 1.0) * 100.0, 2) if (m_now and m_20) else None}
    except Exception as e:
        out['margin'] = {'err': str(e)[:100]}
    json.dump(out, open(os.path.join(DATA, 'etf_share_flow.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('%-12s %8s %8s' % ('主题', '份额5d%', '份额20d%'), flush=True)
    for t in out['themes']:
        print('%-12s %8s %8s' % (t['theme'], t['d5'] and round(t['d5'], 2), t['d20'] and round(t['d20'], 2)), flush=True)
    print('map_src', map_src, flush=True)
    print('margin(两融) 20d%%:', out.get('margin'), flush=True)
    print('WROTE /app/data/etf_share_flow.json', flush=True)

if __name__ == '__main__':
    main()
