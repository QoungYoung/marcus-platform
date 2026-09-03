# -*- coding: utf-8 -*-
"""backfill_minute_windows.py — brze 按事件窗口补拉个股/ETF 5min（可选 1min）

背景：E01-E04(2025-08~10) 与 E15(2026-08-12) 此前无分钟文件，并非源没有——
brze(stk_mins 兼容源) 实测个股/ETF 1min(241根/日)、5min(48根/日) 均可回溯到 2024+；
本脚本只按需补事件相关窗口，断点续跑（已有且≥40根5min/≥200根1min 的日期跳过）。

输出: data/stock_5m_bt/{code6}.json   {YYYYMMDD: [bars]}   与既有 28 只同 schema
      （--1min 时写 data/stock_1m_bt/{code6}.json，实验用途）
用法:
  python -u apps/main_line/backfill_minute_windows.py                        # 默认全部事件窗口 5min
  python -u apps/main_line/backfill_minute_windows.py --events E01,E04,E15
  python -u apps/main_line/backfill_minute_windows.py --1min --events E12,E13
"""
import os, sys, json, time
from datetime import date, timedelta

DATA = os.environ.get('DATA_DIR', 'data')
for _p in ("/app", "/app/core", "/app/app", os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from app.services.t_data_sources import fetch_brze_stk_mins  # noqa: E402

# 事件窗口: 交易日范围(前后放宽到~15交易日, 覆盖 ±10 扫描 + T+5)
EVENT_WINDOWS = {
    'E01': ('20250803', '20250910'),   # 2025-08-21 开盘买 存储+AI终端
    'E02': ('20250825', '20251001'),   # 2025-09-10 CPO probe
    'E03': ('20251008', '20251110'),   # 2025-10-28 指数回补科技
    'E04': ('20251008', '20251112'),   # 2025-10-30 半导体设备ETF换入(含10-29低点)
    'E15': ('20260727', '20260820'),   # 2026-08-12 买回半导体
}
EVENT_CODES = {
    # 510300.SH = 上证/大盘急杀宽基代理(brze idx_mins tenant key 过期前暂用ETF近似253时点; E15 直接用 index_5min_dh 上证 08-12 窗口)
    'E01': ['603296.SH', '603986.SH', '688008.SH', '300475.SZ', '001309.SZ', '510300.SH'],
    'E02': ['300502.SZ', '300308.SZ', '300394.SZ', '510300.SH'],
    'E03': ['002371.SZ', '688012.SH', '688072.SH', '603986.SH', '300502.SZ', '300308.SZ', '510300.SH'],
    'E04': ['512480.SH', '159995.SZ', '588200.SH', '510300.SH'],
    'E15': ['688981.SH', '688041.SH', '002371.SZ', '603501.SH', '688012.SH', '600584.SH', '512480.SH'],
}
# E12/E13 关键事件日 1min(割肉盘口/254同日特征实验预留)
ONEMIN_WINDOWS = {'E12': ('20260525', '20260610'), 'E13': ('20260630', '20260715')}
ONEMIN_CODES = {
    'E12': ['688012.SH', '688072.SH', '002371.SZ', '300054.SZ', '300236.SZ', '600584.SH'],
    'E13': ['601138.SH', '000977.SZ', '000938.SZ', '603019.SH', '000034.SZ', '688041.SH'],
}


def _weekdays(d0s, d1s):
    d0 = date(int(d0s[:4]), int(d0s[4:6]), int(d0s[6:8]))
    d1 = date(int(d1s[:4]), int(d1s[4:6]), int(d1s[6:8]))
    out = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d.strftime('%Y%m%d'))
        d += timedelta(days=1)
    return out


def _load(path):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return {}


def _ok(bars, one_min):
    need = 200 if one_min else 40
    return bars is not None and len(bars) >= need


def pull(code, dates, one_min, outdir, logf):
    code6 = code.split('.')[0]
    freq = '1min' if one_min else '5min'
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, code6 + '.json')
    data = _load(path)
    todo = [td for td in dates if not _ok(data.get(td), one_min)]
    print('START', code, freq, 'pending', len(todo), flush=True)
    if not todo:
        print('SKIP(已覆盖)', code, flush=True)
        return
    for td in todo:
        try:
            bars = fetch_brze_stk_mins(code, freq=freq, trade_date=td)
        except Exception as e:
            print('ERR', code, td, str(e)[:100], flush=True)
            time.sleep(2)
            continue
        if bars:  # 只存有K线的交易日, 避免污染 trading-day 序列
            data[td] = bars
        json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
        print(code, td, freq, len(bars or []), 'total', len(data), flush=True)
        time.sleep(0.4)
    print('DONE', code, freq, 'days', len(data), flush=True)


def main():
    argv = sys.argv[1:]
    one_min = '--1min' in argv
    ev_arg = None
    if '--events' in argv:
        ev_arg = [x.strip() for x in argv[argv.index('--events') + 1].split(',') if x.strip()]
    if one_min:
        wins, codes = ONEMIN_WINDOWS, ONEMIN_CODES
        outdir = os.path.join(DATA, 'stock_1m_bt')
    else:
        wins, codes = EVENT_WINDOWS, EVENT_CODES
        outdir = os.path.join(DATA, 'stock_5m_bt')
    evs = [e for e in (ev_arg or sorted(wins.keys())) if e in wins]
    code_dates = {}
    for e in evs:
        d0, d1 = wins[e]
        tds = _weekdays(d0, d1)
        for c in codes[e]:
            code_dates.setdefault(c, [])
            for td in tds:
                if td not in code_dates[c]:
                    code_dates[c].append(td)
    print('events', evs, 'codes', len(code_dates), 'one_min', one_min, flush=True)
    for c, tds in sorted(code_dates.items()):
        tds.sort()
        pull(c, tds, one_min, outdir, None)
        time.sleep(0.3)
    print('ALL DONE', flush=True)


if __name__ == '__main__':
    main()
