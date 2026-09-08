# -*- coding: utf-8 -*-
"""chain_map 生成器 v2 (2026-09-08): 环节龙头排序 = 成分池 -> fina主营核实剔伪 -> 核实集内 total_mv 排序 top3

相对 v0(demo: 概念序前2) 的修正(经 2026-09-08 滚动截面回测验证):
  1. 纯 mv 排序不能当"领涨龙头"代理(0701-0805 窗口市值TOP5 7段中5段20日跑输全成分, 大票领跌/小票弹性);
     但作为"环节代表"结构规则, mv 提供确定性+可复核排序。
  2. 概念污染(南航/招商蛇口/卧龙新能/陕西金叶 等异业大票)只有主营核实能剔; mv 反而放大。
  3. fina 词表有盲区(如中免主营词不含"免税") -> 核实空段回退概念序 top2 并置 needs_verify=True,
     交 AI/人工 refine 词典或补白名单, 不静默丢弃。

输出: data/chain_map_{date}.json (正式产物, 覆盖 demo) + data/chain_map_{date}_v2_cmp.json (新旧对比)
用法: python3 chain_map.py [YYYYMMDD]   # 缺省=最近交易日(≤today), mv 回退到最近有 daily_basic 的交易日
"""
import sys, os, json, sqlite3, time
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/apps/main_line')
DATA = os.environ.get('DATA_DIR', '/app/data')

# 环节 seed 词典(初稿 3主题7环节; 待 AI/人工 refine 扩展至 fusion 15 主题)
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
LEAD_TOP_N = 3          # 每环节核实集内 mv 排序取前 N
VERIFY_POOL_MV_N = 12   # 核实候选: 全量成分池内 mv top N(真实龙头)
VERIFY_POOL_CONCEPT_N = 6  # + 概念序前 N (覆盖新票/小票/词典盲区)

def concept_stocks(db, cname, limit=100):
    """全量成分(上限100/概念), 仅用于逐概念 mv 龙头定位"""
    try:
        cur = db.cursor()
        cur.execute("SELECT ts_code FROM stock_concept_map WHERE concept_name=? LIMIT ?", (cname, limit))
        return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print('concept err', cname, e); return []

def fetch_mv(pro, date8, dbg):
    """全市场 daily_basic(total_mv, 万元). trade_date 参数只认 ts_code,total_mv 两字段;
    当日盘后未入库时逐日回退(≤date8). 返回 (mv_map, mv_date)"""
    d = date8
    for _ in range(6):
        try:
            df = pro.daily_basic(trade_date=d, fields='ts_code,total_mv')
            if df is not None and not df.empty and len(df) > 1000:
                print('mv_date', d, 'n', len(df), flush=True)
                return {str(r['ts_code']): float(r['total_mv']) for _, r in df.iterrows()}, d
        except Exception as e:
            print('mv', d, 'err', str(e)[:80], flush=True)
        d = str(int(d) - 1)
    dbg.append('mv_unavailable_fallback_concept_order')
    return {}, None

def fina_verdict(pro, ts, seg, names):
    """主营核实(与 v0 demo 同词表口径): 最新期(全期按 bz_sales desc, 排除 行业/产品/地区 汇总行) top6 文本
    任一词命中 seg.kw -> ok. 返回 verdict dict"""
    v = {"ts": ts, "name": names.get(ts, ""), "mainbz": [], "hit": [], "ok": False}
    try:
        df = pro.fina_mainbz(ts_code=ts)
        if df is not None and not df.empty:
            df = df.sort_values('bz_sales', ascending=False)
            items = df[~df['bz_item'].isin(['行业', '产品', '地区'])].head(6).to_dict('records')
            tops = [str(r.get('bz_item') or '')[:40] for r in items]
            v["mainbz"] = tops
            hits = [t for t in tops if any(k.lower() in t.lower() for k in seg["kw"])]
            v["hit"] = hits[:3]
            v["ok"] = len(hits) > 0
    except Exception as e:
        v["err"] = str(e)[:80]
    return v

def main():
    date8 = sys.argv[1] if len(sys.argv) > 1 else None
    from app.api.market import _get_tushare_pro
    pro = _get_tushare_pro()
    names = {}
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
        if sb is not None and not sb.empty:
            names = dict(zip(sb['ts_code'], sb['name']))
    except Exception as e:
        print('stock_basic fail', str(e)[:80])
    db = sqlite3.connect(os.path.join(DATA, 'stock_pool.db'))
    t0 = time.time()
    # 目标交易日 = 最近交易日(取 trade_cal <=today 最后一个); 不给 date 时用 today
    import datetime
    today = datetime.date.today().strftime('%Y%m%d')
    date8 = date8 or today
    # trade_cal: 用本地接口获取最近交易日(≤date8)
    try:
        from app.api.market import _get_tushare_pro as _p
        cal = _p().trade_cal(exchange='SSE', start_date='20260801', end_date=date8, is_open='1')
        if cal is not None and not cal.empty:
            date8 = str(cal['cal_date'].astype(str).max()).replace('-', '')
    except Exception as e:
        print('trade_cal fail, use date=', date8, str(e)[:80], flush=True)

    mv, mv_date = fetch_mv(pro, date8, [])
    mv_lookup = mv if mv else {}
    out = {"date": date8, "mv_date": mv_date,
           "generated_by": "chain_map_v2_mv_verified",
           "method": "comps -> fina_mainbz主营核实剔伪 -> 核实集 total_mv 排序取top%d; 核实空段回退概念序top2+needs_verify" % LEAD_TOP_N,
           "themes": []}
    cmp_rows = []  # 新旧对比
    for theme, segs in SEED.items():
        t_seg = []
        seen = set()
        for seg in segs:
            codes = []          # 小池: 每概念前12(可信纯度, 概念序兜底用)
            big = []            # 大池: 每概念全量(仅用于逐概念 mv 龙头定位)
            seen_c, seen_b = set(), set()
            for c in seg["concepts"]:
                for ts in concept_stocks(db, c, 12):
                    if ts not in seen_c:
                        seen_c.add(ts); codes.append(ts)
                for ts in concept_stocks(db, c):
                    if ts not in seen_b:
                        seen_b.add(ts); big.append(ts)
            # 大池内 mv 排序(真实龙头进入候选), 核实池 = mv top N + 小池概念序前 N(对比/兜底)
            mv_sorted = sorted(big, key=lambda c: -mv_lookup.get(c, 0)) if mv_lookup else []
            pool = list(dict.fromkeys([c for c in mv_sorted[:VERIFY_POOL_MV_N] if mv_lookup.get(c)] + codes[:VERIFY_POOL_CONCEPT_N]))
            verdicts = {}
            for ts in pool:
                if ts not in verdicts:
                    verdicts[ts] = fina_verdict(pro, ts, seg, names); time.sleep(0.12)
            verified = [verdicts[ts] for ts in pool if verdicts[ts]["ok"]]
            # 核实集内 mv 排序
            verified.sort(key=lambda v: -mv_lookup.get(v["ts"], 0))
            for i, v in enumerate(verified):
                v["mv"] = round(mv_lookup.get(v["ts"], 0), 0)
                v["mv_rank_in_pool"] = i + 1
            leading = verified[:LEAD_TOP_N]
            # 旧逻辑参照(概念序前2 同口径核实) -> 对比
            old = [verdicts.get(ts) for ts in codes[:2] if ts in verdicts]
            rejected = sorted([verdicts[ts] for ts in pool if not verdicts[ts]["ok"]],
                               key=lambda v: -mv_lookup.get(v["ts"], 0))
            for v in rejected:
                v["reason"] = "mainbz_not_matched_by_kw"

            top3_rejected = [verdicts[ts] for ts in mv_sorted[:3] if ts in verdicts and not verdicts[ts]["ok"]]
            needs_verify = (len(verified) < LEAD_TOP_N) or bool(top3_rejected)
            seg_node = {"role": seg["role"], "label": seg["label"],
                        "concepts": seg["concepts"],
                        "pool_n": len(big),
                        "verified_n": len(verified),
                        "mv_date": mv_date,
                        "candidates": codes[:6],
                        "leading_verified": leading,
                        "rejected": rejected,
                        "needs_verify": needs_verify,
                        "method": "mv_verified" if leading else "concept_order_fallback"}
            t_seg.append(seg_node)
            old_names = [o["name"] + ("✓" if o["ok"] else "✗") for o in old if o]
            new_names = [l["name"] for l in leading]
            rej_names = [r["name"] + ("(mvTop?)" if r["ts"] in [x["ts"] for x in top3_rejected] else "") for r in rejected][:6]
            cmp_rows.append({"theme": theme, "seg": seg["label"], "old_top2": old_names,
                             "new_leading": new_names, "rejected": rej_names,
                             "needs_verify": needs_verify,
                             "pool_n": len(codes), "verified_n": len(verified)})
            print(f"[{theme}][{seg['label']}] pool={len(codes)} verified={len(verified)} needs_verify={needs_verify} | OLD {old_names} -> NEW {new_names} | rejected {rej_names}", flush=True)
        out["themes"].append({"theme": theme, "segments": t_seg,
                              "completeness": {"segments": len(t_seg),
                                               "has_leader": any(len(s["leading_verified"]) for s in t_seg)}})
        print(f'[{theme}] 环节{len(t_seg)} 用时{time.time()-t0:.0f}s', flush=True)
    path = os.path.join(DATA, f'chain_map_{date8}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    cmp_path = os.path.join(DATA, f'chain_map_{date8}_v2_cmp.json')
    with open(cmp_path, 'w', encoding='utf-8') as f:
        json.dump({"date": date8, "mv_date": mv_date, "rows": cmp_rows}, f, ensure_ascii=False, indent=1)
    print('WROTE', path); print('WROTE', cmp_path)

if __name__ == '__main__':
    main()
