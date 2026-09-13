# -*- coding: utf-8 -*-
# 批量跑狼大事件 agent 主线判断(研报->dsh /chat->催化分+主线判断)，结果写 json
import json, sys
sys.path.insert(0, '/tmp')
import main_line_agent as ml

def main():
    ctx_file=sys.argv[1] if len(sys.argv)>1 else '/tmp/wolf_events_ctx.json'
    url=sys.argv[2] if len(sys.argv)>2 else 'http://marcus-dsh:3001/chat'
    outfile=sys.argv[3] if len(sys.argv)>3 else '/tmp/wolf_events_agent_result.json'
    events=json.load(open(ctx_file, encoding='utf-8'))
    results=[]
    for ev in events:
        theme=ev['theme']; date=ev['date']; reports=ev.get('reports',[])
        print('[*]', date, theme, 'reports', len(reports), file=sys.stderr)
        if not reports:
            results.append({'theme':theme,'date':date,'catalyst_score':None,'main_line_judgment':'','catalysts':[],'error':'no_reports'})
            continue
        prompt=ml.build_prompt(theme, date, reports)
        try:
            reply=ml.call_agent(url, prompt, session='ev_'+date.replace('-',''))
            res=ml.parse_reply(reply)
        except Exception as e:
            res={'catalyst_score':None,'main_line_judgment':'','catalysts':[],'error':str(e)[:150]}
        res['theme']=theme; res['date']=date
        results.append(res)
    json.dump(results, open(outfile,'w',encoding='utf-8'), ensure_ascii=False, indent=2)
    print('done', len(results), '->', outfile, file=sys.stderr)

if __name__=='__main__': main()
