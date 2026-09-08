# -*- coding: utf-8 -*-
"""chain_map demo(2026-09-08): 对 fusion TOP3 主题拆产业链(环节词典初稿待审)
概念层=THEME_CONCEPTS+stock_pool.db成分; 个股层=每环节代表股 get_fina_mainbz 主营纯度验证
输出 data/chain_map_{date}.json —— schema 供评审, 后续可换 agent(LLM) 拆链"""
import sys, os, json, sqlite3, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')
THEME_CONCEPTS = {
 "消费/内需": ["白酒","免税概念","新消费","新零售","零售概念","旅游概念","消费电子概念","文娱消费"],
 "传媒/游戏": ["网络游戏","短剧互动游戏","影视概念","在线教育","职业教育","体育产业"],
 "AI/算力/科技": ["人工智能","算力概念","AIGC概念","AI应用","AI智能体","AI语料","DeepSeek概念","ChatGPT概念","Kimi概念","智谱AI","多模态AI","AI眼镜","光通信模块","CPO概念","液冷概念","PCB","东数西算","数据中心","云计算","边缘计算","存储芯片","AI芯片"],
}
# 环节 seed 词典（初稿, 待 AI/人工 refine —— 演示用）
SEED = {
 "AI/算力/科技": [
   {"role": "upstream", "label": "上游·芯片/光器件/材料设备",
    "concepts": ["AI芯片", "存储芯片", "光通信模块", "CPO概念", "PCB", "液冷概念"],
    "kw": ["芯片", "半导体", "光模块", "光器件", "PCB", "液冷", "制冷", "散热", "存储", "处理器", "CPU", "面板", "显示", "晶圆", "设备"]},
   {"role": "mid", "label": "中游·算力基建/云",
    "concepts": ["算力概念", "数据中心", "云计算", "边缘计算", "东数西算"],
    "kw": ["算力", "服务器", "数据中心", "IDC", "云计算", "算网"]},
   {"role": "downstream", "label": "下游·模型/应用/终端",
    "concepts": ["AI应用", "AIGC概念", "多模态AI", "AI智能体", "AI语料", "DeepSeek概念", "ChatGPT概念", "Kimi概念", "智谱AI", "AI眼镜", "人工智能"],
    "kw": ["AI", "人工智能", "大模型", "应用", "软件", "语料", "智能体", "眼镜", "数字人", "信息服务", "互联网"]},
 ],
 "传媒/游戏": [
   {"role": "upstream", "label": "内容/研发制作",
    "concepts": ["网络游戏", "影视概念", "短剧互动游戏"],
    "kw": ["游戏", "影视", "剧", "内容", "研发", "IP"]},
   {"role": "downstream", "label": "平台/分发/服务",
    "concepts": ["在线教育", "职业教育", "体育产业"],
    "kw": ["平台", "教育", "培训", "体育", "赛事", "发行"]},
 ],
 "消费/内需": [
   {"role": "upstream", "label": "品牌/制造",
    "concepts": ["白酒", "新消费", "消费电子概念"],
    "kw": ["白酒", "酒", "消费", "电子", "品牌", "制造"]},
   {"role": "downstream", "label": "渠道/场景",
    "concepts": ["免税概念", "新零售", "零售概念", "旅游概念", "文娱消费"],
    "kw": ["免税", "零售", "商超", "旅游", "景区", "餐饮", "文娱"]},
 ],
}

def concept_stocks(db, cname, limit=8):
    try:
        cur = db.cursor()
        cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?", (cname, limit))
        return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print('concept err', cname, e); return []

def main():
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    # 名称映射
    names = {}
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        if sb is not None and not sb.empty:
            names = dict(zip(sb['ts_code'], sb['name']))
    except Exception as e:
        print('stock_basic fail', str(e)[:80])
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    t0 = time.time()
    out = {"date": "20260908", "generated_by": "chain_map_demo_v0", "themes": []}
    for theme, segs in SEED.items():
        t_seg = []
        seen = set()
        for seg in segs:
            codes = []
            for c in seg["concepts"]:
                for ts in concept_stocks(db, c):
                    if ts not in seen:
                        seen.add(ts); codes.append(ts)
            leading = []
            # fina_mainbz 纯度验证: 每环节取前2只(概念顺序近似无市值, 用名单前2)
            for ts in codes[:2]:
                verdict = {"ts": ts, "name": names.get(ts, ""), "mainbz": [], "hit": [], "ok": False}
                try:
                    df = pro.fina_mainbz(ts_code=ts, period='20261231') if False else None
                    df = pro.fina_mainbz(ts_code=ts)
                    if df is not None and not df.empty:
                        df = df.sort_values('bz_sales', ascending=False)
                        items = df[~df['bz_item'].isin(['行业', '产品', '地区'])].head(6).to_dict('records')
                        tops = [str(r.get('bz_item') or '')[:40] for r in items]
                        verdict["mainbz"] = tops
                        hits = [t for t in tops if any(k.lower() in str(t).lower() for k in seg["kw"])]
                        verdict["hit"] = hits[:3]
                        verdict["ok"] = len(hits) > 0
                except Exception as e:
                    verdict["err"] = str(e)[:80]
                leading.append(verdict)
            t_seg.append({"role": seg["role"], "label": seg["label"],
                          "concepts": seg["concepts"],
                          "candidates": codes[:6], "leading_verified": leading})
        out["themes"].append({"theme": theme,
                              "segments": t_seg,
                              "completeness": {"segments": len(t_seg),
                                               "has_leader": any(len(s["leading_verified"]) for s in t_seg)}})
        print(f'[{theme}] 环节{len(t_seg)} 用时{time.time()-t0:.0f}s', flush=True)
    path = os.path.join(DATA, 'chain_map_20260908.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('WROTE', path)
    # summary
    for th in out["themes"]:
        print('\n==', th["theme"], th["completeness"])
        for seg in th["segments"]:
            for lv in seg["leading_verified"]:
                print('  ', lv["ts"], lv.get("name"), '| ok', lv.get("ok"),
                      '| top', (lv.get("mainbz") or [])[:2], '| hit', lv.get("hit"))

if __name__ == '__main__':
    main()
