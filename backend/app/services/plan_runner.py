# -*- coding: utf-8 -*-
"""plan_runner.py — 计划库触发检查(PostgreSQL 版): 读写 plan_library 表, 命中→fired+注入指令。
供 trade_graph._read_plan_context() 与 TMonitor._check_plan_triggers() 调用。
"""
import os, json, datetime
from app.database import SessionLocal
from app.models.plan import Plan

def _j(s):
    try: return json.loads(s) if s else {}
    except: return {}

def _d(p):
    return {'id': p.id, 'type': p.type, 'subject': p.subject, 'horizon': p.horizon,
            'created_at': p.created_at, 'status': p.status, 'thesis': p.thesis,
            'trigger': _j(p.trigger), 'action': p.action, 'gate': p.gate,
            'pit_score': _j(p.pit_score), 'today_context': _j(p.today_context),
            'fired_at': p.fired_at, 'fire_reason': p.fire_reason}

def upsert_plan(plan: dict) -> None:
    db = SessionLocal()
    try:
        row = db.get(Plan, plan['id']) or Plan(id=plan['id'])
        row.type = plan.get('type','theme_plan')
        row.subject = plan.get('subject','')
        row.horizon = plan.get('horizon','')
        row.created_at = plan.get('created_at', datetime.datetime.now().strftime('%Y-%m-%d'))
        row.status = plan.get('status','armed')
        row.thesis = plan.get('thesis','')
        row.trigger = json.dumps(plan.get('trigger',{}), ensure_ascii=False)
        row.action = plan.get('action','')
        row.gate = plan.get('gate','')
        row.pit_score = json.dumps(plan.get('pit_score',{}), ensure_ascii=False)
        row.today_context = json.dumps(plan.get('today_context',{}), ensure_ascii=False)
        db.add(row); db.commit()
    finally:
        db.close()

def _index_last_close():
    import json as _j
    p=os.path.join(os.environ.get('DATA_DIR','/app/data'),'index_daily_000001.json')
    try: d=_j.load(open(p,encoding='utf-8'))
    except: return None
    rows=sorted(d,key=lambda x:int(str(x['trade_date']).replace('-','')))
    return float(rows[-1]['close']) if rows else None

def _daily_close(code):
    import json as _j
    out={}
    for root in ['stock_5m_bt','recent_sync']:
        p=os.path.join(os.environ.get('DATA_DIR','/app/data'),root,code+'.json')
        try: d=_j.load(open(p,encoding='utf-8'))
        except: d={}
        for k,v in d.items():
            bs=sorted(v,key=lambda x:str(x.get('time') or x.get('trade_time')))
            if bs: out.setdefault(k,float(bs[-1]['close']))
    return out

def _pos_pct(dc, win=90):
    if not dc: return None
    ks=sorted(dc); vals=[dc[k] for k in ks[-win:]]
    lo=min(vals); hi=max(vals); cur=dc[ks[-1]]
    return round((cur-lo)/(hi-lo)*100,1) if hi>lo else 50.0

def evaluate_plans():
    """读 plan_library 表 armed 计划, 判触发, 命中→fired. 返回 (context_block, fired_list)."""
    db = SessionLocal()
    ctx=[]; fired=[]
    try:
        idx_close=_index_last_close()
        rows=db.query(Plan).filter(Plan.status=='armed').all()
        now=datetime.datetime.now().isoformat()
        for p in rows:
            trig=_j(p.trigger); hit=False; why=''
            if p.type=='refill_plan':
                val=trig.get('value')
                if idx_close is not None and val and idx_close <= val*1.01:
                    hit=True; why='指数收盘%.1f<=关键位%.1f×1.01' % (idx_close, val)
            elif p.type=='theme_plan':
                sym=trig.get('symbol') or ''
                dc=_daily_close(sym); pp=_pos_pct(dc)
                if pp is not None and 'pos_pct_max' in trig and pp <= trig['pos_pct_max']:
                    hit=True; why='%s位置分位%.1f%%<=%.0f%%' % (sym, pp, trig['pos_pct_max'])
            if hit:
                p.status='fired'; p.fired_at=now; p.fire_reason=why
                fired.append(_d(p)); db.add(p)
                ctx.append('🔔 计划命中(%s) [%s]: %s → 动作:%s' % (p.id, p.subject, why, p.action))
            else:
                ctx.append('⏳ 计划待触发 [%s]: %s → 触发:%s 动作:%s' % (p.id, p.subject, json.dumps(trig,ensure_ascii=False), p.action))
        db.commit()
    finally:
        db.close()
    block=("\n## 计划库触发检查(plan_runner)\n" + "\n".join(ctx) + "\n") if ctx else ""
    return block, fired

def plan_context():
    try:
        block,_ = evaluate_plans()
        return block
    except Exception:
        return ""
