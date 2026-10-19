# -*- coding: utf-8 -*-
'''event_briefing_agent.py — 生产侧的「宏观事件晨报/提醒」：定时由 Marcus 调度器驱动 ✓，分析交给 DSH agent ✓

用户 2026-10-19 要求：**定时必须在生产 Marcus**（不靠 DSH 自己的调度 ✗），且内容要 agent 分析 ✓。
实现路径（与仓库既有做法一致 ✓）：
  · 调度：config/tasks.yaml 的 script 任务（08:05 工作日 ✓）
  · agent：POST {CHAT_URL}  {"message": ..., "session_id": ...} ⇒ {"reply": ...}
    CHAT_URL 取 EVENT_BRIEFING_CHAT_URL > MAIN_LINE_CHAT_URL > PI_SERVER_URL > http://localhost:3001/chat ✓
    （先例：jobs/wolf_selection_reasons.py:123 ✓；生产容器里 PI_SERVER_URL=http://dsh:3001/chat ✓）
  · 推送：core.qq_notifier.send_c2c_message ✓（按日期去重 ✓）
  · 失败兜底：DSH 不可用 ⇒ 推**确定性事实版**（数据照样全 ✓）并在文末注明「agent 不可用」✓

用法：python jobs/event_briefing_agent.py [--date YYYYMMDD] [--force] [--dry] [--no-push]
  --force  非 T-5/T-3/T-1 也生成（演练 ✓）
  --dry    不推送、不写盘，只打印 ✓
'''
import argparse, datetime as dt, json, os, sys, time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'core'))
sys.path.insert(0, os.path.join(ROOT, 'apps', 'main_line'))
DATA = os.environ.get('DATA_DIR', os.path.join(ROOT, 'data'))
STATE = os.path.join(DATA, '_event_briefing_pushed.json')
ALERT_OFFSETS = (5, 3, 1)

BETA_LADDER = [('科创50', 1.69, 11.8), ('半导体ETF', 1.62, 14.5), ('创业板指', 1.72, 9.9)]
BETA_SRC = '本仓 799 个交易日实测（2026-10-16）：科创50 β1.69/P(≤-2%)11.8%｜半导体 1.62/14.5%｜创业板 1.72/9.9%｜沪指 2.4%'


def chat_url():
    return (os.environ.get('EVENT_BRIEFING_CHAT_URL') or os.environ.get('MAIN_LINE_CHAT_URL')
            or os.environ.get('PI_SERVER_URL') or 'http://localhost:3001/chat')


def http_json(url, payload=None, timeout=180):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


def panel(path, timeout=25):
    base = os.environ.get('PANEL_BASE', 'http://81.70.44.68/api/v1')
    try:
        return http_json(base + path, timeout=timeout)
    except Exception as e:
        return {'_error': '%s: %s' % (type(e).__name__, str(e)[:80])}


def trading_days(start8, end8):
    try:
        from tushare_relay import relay_query
        df = relay_query('trade_cal', exchange='SSE', start_date=start8, end_date=end8)
        ds = []
        for _, r in df.iterrows():
            d = str(r.get('cal_date') or '').replace('-', '')
            if len(d) == 8 and int(r.get('is_open') or 0) == 1:
                ds.append(d)
        return sorted(ds)
    except Exception as e:
        print('[brief] trade_cal 失败: %s' % str(e)[:80])
        return []


def load_impact(rel):
    try:
        import yaml
        ev = yaml.safe_load(open(os.path.join(ROOT, 'config', 'macro_event_impact.yaml'), encoding='utf-8'))['events']
        if rel in ev:
            return ev[rel]
        for k in ev:
            for tok in str(k).replace('/', ' ').replace('_', ' ').split():
                if tok and tok in rel.replace('_', ' '):
                    return ev[k]
    except Exception as e:
        print('[brief] 知识库读取失败: %s' % str(e)[:80])
    return None


def build_facts(today):
    cal = json.load(open(os.path.join(DATA, 'macro_calendar.json'), encoding='utf-8'))['events']
    fut = sorted([e for e in cal if e.get('tier', 0) >= 3 and e['local'][:10].replace('-', '') > today], key=lambda e: e['ts'])
    facts = {'date': today}
    if not fut:
        facts['event'] = None
        return facts
    ev = fut[0]
    evd = ev['local'][:10].replace('-', '')
    ds = trading_days(today, evd)
    off = len([d for d in ds if d < evd]) - (1 if today in ds else 0)
    facts.update({'offset': off, 'trading_days_before_event': [d for d in ds if d < evd],
                  'event': {'name': ev.get('name_cn') or ev['release'], 'release': ev['release'], 'tier': ev['tier'],
                            'beijing': ev['local'], 'src': ev.get('src', 'fxmacrodata')},
                  'next7': [{'date': e['local'][:10], 'name': e.get('name_cn') or e['release'], 'tier': e.get('tier')}
                            for e in sorted(cal, key=lambda x: x['ts']) if today < e['local'][:10].replace('-', '') <= (dt.date(int(today[:4]), int(today[4:6]), int(today[6:])) + dt.timedelta(days=7)).strftime('%Y%m%d')][:12],
                  'impact': load_impact(ev['release'])})
    return facts


def build_state():
    st = {}
    try:
        ws = json.load(open(os.path.join(DATA, 'wave_state.json'), encoding='utf-8'))
        st['wave_state'] = {k: ws.get(k) for k in ('date', 'level', 'sub_level', 'operation', 'mode')}
    except Exception:
        st['wave_state'] = None
    st['position'] = panel('/indicator/calc-position')
    # ★ 2026-10-19 修正：个股浮盈/浮亏**唯一权威来源**是用这个接口 ✓
    #   （此前 agent 把「定投/DCA」快照的浮亏 12.5% 张冠李戴到 SH600299 ✗ —— 用户当场纠正 ✓；
    #    真实为 500 股 / 成本 7.693 / 现价 8.10 / **浮盈 +5.29%** ✓）
    st['positions_truth'] = panel('/portfolio/positions')
    st['scan'] = panel('/scan/latest')
    st['index_tech'] = panel('/market/technical/SH000001?limit=5')
    return st


def fallback_text(facts, st):
    ev = facts.get('event') or {}
    imp = facts.get('impact') or {}
    ws = (st.get('wave_state') or {})
    base = 30
    caps = ' ｜ '.join('%s ≤%.1f%%' % (n, base / b) for n, b, _ in BETA_LADDER)
    lines = ['【宏观事件晨报 %s】（确定性事实版 · agent 不可用）' % facts['date'],
             '⏳ 距下一个事件：**T-%s 个交易日** ｜ %s ｜ %s ｜ tier %s（北京时 %s）' % (
                 facts.get('offset'), ev.get('name', '-'), ev.get('release', '-'), ev.get('tier'), (ev.get('beijing') or '')[:16])]
    if imp:
        lines += ['', '📌 事件关联：%s' % (imp.get('what') or ''),
                  '· 传导：' + '；'.join(imp.get('transmit') or []),
                  '· 关联板块：' + json.dumps(imp.get('sectors_typical') or {}, ensure_ascii=False)]
    pos = st.get('positions_truth') or []
    pos_line = '；'.join('%s %s %s股 成本%.3f 现价%.2f 浮盈%+.2f%%' % (
        p.get('symbol'), p.get('name'), p.get('volume'), float(p.get('avg_price') or 0), float(p.get('current_price') or 0),
        float(p.get('floating_pnl_pct') or 0)) for p in pos if isinstance(p, dict)) if isinstance(pos, list) else ''
    lines += ['', '⚡ 当前状态：浪型 %s/%s/%s%s' % (ws.get('level'), ws.get('sub_level'), ws.get('operation'),
                                              ('（mode=%s）' % ws.get('mode')) if ws.get('mode') else ''),
              '· 仓位上限（档位 invest 30%% ÷ β）：%s' % caps,
              '· β 标尺来源：%s' % BETA_SRC,
              '· 持仓（/portfolio/positions 权威 ✓）：%s' % (pos_line or '缺'),
              '', '💡 建议：事件窗口内不新开高 β、推迟加仓；解除须链回到 rung≤1 且沪指站回 MA20；存量按个股纪律，卖出侧不受浪型闸门限制 ✓',
              '', '⚠️ 只读分析，非交易指令；agent（DSH）本次不可用，以上为确定性事实版 ✓']
    return '\n'.join(lines)


def agent_text(facts, st):
    prompt = [
        '你是 A 股交易系统的宏观事件分析师。请输出一份**可直接推到手机**的「宏观事件晨报」，≤900 字中文。',
        '严格按下面五段（不要加别的标题层级）：',
        '【宏观事件晨报 <日期>】',
        '⏳ 距下一个事件：T-N 交易日 ｜ 事件名 ｜ 类别 ｜ tier（写清事件北京时与 A 股冲击日 ✓）',
        '📅 未来 7 天事件表（美国事件按北京时当晚发布 ⇒ A 股冲击在次日 ✓）',
        '📌 事件关联：这件事是什么 + 主要关联哪些 A 股板块/资产 + 传导路径（从下面 facts.impact 取，别自创 ✓）',
        '⚡ 影响评估：结合下方持仓与浪型档位；若没有已验证的历史统计，就写「历史影响未验证」✓',
        '💡 建议：1~3 条**具体**动作，必须带数字或条件（仓位上限按 β 折算：科创50 ÷1.69、半导体 ÷1.62、创业板 ÷1.72，基准取档位 invest）✓',
        '',
        '硬纪律：① **禁止预测涨跌方向**（只写状态/约束/上限/解除条件）✗；② **禁止编造数字**，每个数字必须来自下面 JSON ✓；',
        '⑤ **盈亏口径（2026-10-19 用户纠错）**：个股「浮盈/浮亏」**只能**用 STATE.positions_truth 里的 floating_pnl_pct ✓，',
        '   并写出标的名 ✓；**禁止**引用「定投/DCA/黄金坑」类快照的盈亏数字 ✗（已停用且滞后，例如 as_of=09-15 的浮亏 12.5% 属于定投计划、',
        '   不是 SH600299 的持仓 ✓）；任何数据滞后 >5 个交易日 ⇒ 只能标「滞后·不用于结论」✗；',
        '③ 缺失数据就写「缺」✗ 不许猜；④ 结尾一句「⚠️ 只读分析，非交易指令；执行由既有 gate/仓位纪律决定」✓。',
        '',
        '=== FACTS（事件/交易日/知识库）===',
        json.dumps(facts, ensure_ascii=False)[:3500],
        '',
        '=== STATE（浪型/持仓/扫描/指数技术；_error 表示取数失败 ⇒ 写「缺」✓）===',
        json.dumps(st, ensure_ascii=False)[:3000],
    ]
    payload = {'message': '\n'.join(prompt), 'session_id': 'eventbrief_%s' % facts['date']}
    # 2026-10-19 实测：本地 DSH 偶发 240s 超时 ✗ ⇒ 加时到 300s + 重试 1 次 ✓（仍失败就兜底 ✓ 绝不静默 ✗）
    last = None
    for attempt in (1, 2):
        try:
            out = http_json(chat_url(), payload, timeout=300)
            txt = str((out or {}).get('reply') or '').strip()
            if txt:
                return txt
            last = RuntimeError('empty reply')
        except Exception as e:
            last = e
            print('[brief] agent 第 %d 次失败: %s' % (attempt, str(e)[:90]))
    raise last


def push_qq(text):
    try:
        from core.qq_notifier import send_c2c_message
        openid = (os.environ.get('QQ_BOT_RECIPIENT') or '').strip()
        if not openid:
            print('[brief] 推送跳过：未配置 QQ_BOT_RECIPIENT')
            return False
        ok = bool(send_c2c_message(openid, text[:1500]))
        print('[brief] 推送%s' % ('成功 ✓' if ok else '失败 ✗'))
        return ok
    except Exception as e:
        print('[brief] 推送异常: %s' % str(e)[:120])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().strftime('%Y%m%d'))
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--no-push', action='store_true')
    a = ap.parse_args()
    today = a.date
    ds = trading_days(today, today)
    if today not in ds:
        print('[brief] %s 非交易日 ⇒ 退出（不写文件 ✓）' % today)
        return 0
    facts = build_facts(today)
    if not facts.get('event'):
        print('[brief] 无未来 tier>=3 事件 ⇒ 退出')
        return 0
    off = facts.get('offset')
    print('[brief] %s ｜ 下一事件 %s ｜ T-%s ｜ chat=%s' % (today, facts['event']['name'], off, chat_url()))
    is_alert = off in ALERT_OFFSETS
    if not (is_alert or a.force):
        print('[brief] 不在 T-5/T-3/T-1 且未 --force ⇒ 只生成晨报（不改提醒）✓')
    st = build_state()
    txt = ''
    try:
        txt = agent_text(facts, st)
        print('[brief] agent 返回 %d 字 ✓' % len(txt))
    except Exception as e:
        print('[brief] agent 调用失败 ⇒ 兜底事实版: %s' % str(e)[:120])
    if not txt or len(txt) < 80:
        txt = fallback_text(facts, st)
    if is_alert:
        txt = txt.replace('【宏观事件晨报', '【⚠️ 事件临近 T-%d 提醒 · %s】\n【宏观事件晨报' % (off, today), 1)
    if a.dry:
        print('===== DRY =====\n' + txt)
        return 0
    p = os.path.join(DATA, 'macro_briefing_%s.md' % today)
    open(p, 'w', encoding='utf-8').write(txt)
    print('[brief] 已写 %s（%d 字）' % (p, len(txt)))
    if a.no_push:
        return 0
    try:
        stt = json.load(open(STATE, encoding='utf-8'))
    except Exception:
        stt = {}
    if today in (stt.get('history') or []):
        print('[brief] %s 已推送过 ⇒ 跳过（去重 ✓）' % today)
        return 0
    if push_qq(txt):
        stt.setdefault('history', []).append(today)
        stt['last'] = today
        stt['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        json.dump(stt, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
