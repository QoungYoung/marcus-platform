# -*- coding: utf-8 -*-
# 每事件调用agent一次，输出9主题 catalyst_score + stage；结果写json
import json, sys, re
sys.path.insert(0, '/tmp')
import main_line_agent as ml
BT=chr(96)
THEMES=['AI/算力/科技','半导体/芯片','新能源/电池','军工/航天','资源/周期','金融','消费/内需','医药','稳增长/基建']

def build_prompt(date, themes):
    parts=['你是主线判断 agent，面对同一个事件日期 '+date+'，请对下面 9 个主题分别评估产业催化分(0~1)与主线阶段(早候选/确认/已发酵/无)。',
           '每个主题给出该主题在事件前研报标题(点内,只用当时信息)。请逐一判断是否存在大厂资本开支/发布会/技术突破/政策/订单/量产/涨价等产业级催化。',
           '仅输出一个 JSON 对象(不要markdown、不要多余文字)：']
    for th in THEMES:
        rs=themes.get(th) or []
        if rs:
            line=chr(10).join('- '+r for r in rs[:12])
            parts.append('【'+th+'】研报:'+chr(10)+line)
        else:
            parts.append('【'+th+'】研报:(无)')
    parts.append('输出JSON结构: {"themes": {"主题名": {"catalyst_score": 0.0, "main_line_stage": "确认"}}}')
    return chr(10).join(parts)

def parse_all(reply):
    t=reply.strip()
    if t.startswith(BT*3):
        t=t.split(chr(10),1)[-1] if chr(10) in t else t
        t=t.rstrip(BT).strip()
    try:
        obj=json.loads(t)
    except Exception:
        m=re.search(chr(123)+'.*'+chr(125), t, re.S)
        if not m: return None
        try: obj=json.loads(m.group(0))
        except Exception: return None
    return obj.get('themes')

def main():
    ctx_file=sys.argv[1]; url=sys.argv[2]; outfile=sys.argv[3]
    events=json.load(open(ctx_file,encoding='utf-8'))
    results=[]
    for ev in events:
        date=ev['date']; themes=ev['themes']
        print('[*]', date, flush=True)
        prompt=build_prompt(date, themes)
        try:
            reply=ml.call_agent(url, prompt, session='all_'+date.replace('-',''))
            parsed=parse_all(reply)
        except Exception as e:
            parsed=None
        if not parsed:
            print('   parse_failed, reply head:', reply[:120], flush=True)
            parsed={}
        results.append({'date':date, 'themes': {th: (parsed.get(th) or {}) for th in THEMES}})
    json.dump(results, open(outfile,'w',encoding='utf-8'), ensure_ascii=False, indent=2)
    print('done', file=sys.stderr)

if __name__=='__main__': main()
