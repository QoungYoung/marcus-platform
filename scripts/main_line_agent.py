# -*- coding: utf-8 -*-
# 主线判断 agent 工具: 事件窗口研报+主题上下文 -> 已部署dsh镜像(/chat) -> 催化分+主线判断
import requests, json, argparse, sys, re
HEADERS={'Content-Type':'application/json','User-Agent':'Mozilla/5.0'}
BT=chr(96)

def build_prompt(theme, date, reports):
    rs=chr(10).join('- '+r for r in reports[:30])
    return ('你是主线判断 agent，根据研报文本做产业催化分 + 主线判断（AI 分析，不用机械关键词）。'
            + chr(10) + '事件：'+date+' 狼大确认主线方向，主题：'+theme+'。'
            + chr(10) + '请基于以下研报标题（点内，只用当时可得信息）回答：'
            + chr(10) + '(a) 是否存在大厂资本开支/发布会/技术突破/政策/订单/量产/涨价等产业级催化，逐条列出；'
            + chr(10) + '(b) 给出 0~1 产业催化分（是否构成主升级产业逻辑）；'
            + chr(10) + '(c) 一句话主线判断（该主题当前处于 早候选/确认/已发酵 哪一阶段，是否具备主线逻辑）。'
            + chr(10) + '研报标题：'+rs
            + chr(10) + '仅输出一个 JSON 对象（不要多余文字、不要 markdown 代码块）：'
            + chr(10) + '{"catalyst_score": 0.0, "catalysts": ["..."], "main_line_judgment": "..."}')

def call_agent(url, prompt, session='ml_agent'):
    r=requests.post(url, json={'message': prompt, 'session_id': session}, headers=HEADERS, timeout=180, verify=False)
    r.raise_for_status()
    return r.json().get('reply','')

def parse_reply(reply):
    t=reply.strip()
    if t.startswith(BT*3):
        t=t.split(chr(10),1)[-1] if chr(10) in t else t
        t=t.rstrip(BT).strip()
    try:
        obj=json.loads(t)
    except Exception:
        m=re.search(chr(123)+'.*'+chr(125), t, re.S)
        if not m: return {'raw': reply, 'parse_failed': True}
        try: obj=json.loads(m.group(0))
        except Exception: return {'raw': reply, 'parse_failed': True}
    obj.setdefault('catalyst_score', None)
    obj.setdefault('catalysts', [])
    obj.setdefault('main_line_judgment', '')
    return obj

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:3001/chat')
    ap.add_argument('--theme', default=None)
    ap.add_argument('--date', default=None)
    ap.add_argument('--reports', nargs='*', default=None)
    ap.add_argument('--context-file', default=None)
    args=ap.parse_args()
    if args.context_file:
        ctx=json.load(open(args.context_file, encoding='utf-8'))
        theme=ctx.get('theme', args.theme); date=ctx.get('date', args.date); reports=ctx.get('reports', []) or []
    else:
        theme=args.theme; date=args.date; reports=args.reports or []
    if not reports:
        print('no reports'); return
    prompt=build_prompt(theme, date, reports)
    print('[*] 调用 dsh agent /chat ...', file=sys.stderr)
    reply=call_agent(args.url, prompt)
    result=parse_reply(reply)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__=='__main__': main()
