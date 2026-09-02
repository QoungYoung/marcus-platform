# -*- coding: utf-8 -*-
"""
validate_rotation_cases.py — P2 轮动 Case 验证（docs/p2-rotation-cases.md 的 22 例）
用法:
  python apps/main_line/validate_rotation_cases.py --mode wave     # wave_agent 逐日期重放(需 dsh/promax, 跑 worker 容器)
  python apps/main_line/validate_rotation_cases.py --mode position # position_class 时点回放(纯本地CPU)
  python apps/main_line/validate_rotation_cases.py --mode both
输出: data/rotation_wave_replay.json / data/rotation_position_replay.json
"""
import os, sys, json, re, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", "data")
HIST = os.path.join(DATA, "concept_hist.json")
import pandas as pd

# ── 22 case (与 docs/p2-rotation-cases.md 对齐) ─────────────────────────────
# expect_op_family: build=可主升内轮动 / tside=防御性切低合法 / no=禁止轮动防守 / na=个股纪律不限
# targets: [(概念关键词, 期望位置)] — 用关键词在 concept_hist 名称里找(取首个>=80日且有数据的)
CASES = [
 # 组A
 dict(id="A1", date="2025-02-06", expect_wave="side", group="A",
      note="三浪初期(2-06 仍是2浪W底side, 2-20才转3-1 build)去弱留强/筛票换票"),
 dict(id="A2", date="2025-03-19", expect_wave="build", group="A",
      note="机器人/算力主线内细分轮动走业绩向"),
 dict(id="A3", date="2026-01-05", expect_wave="build", group="A",
      note="大级别龙头先上平台→二线细分超额(液冷/电源/PCB)"),
 dict(id="A4", date="2026-05-25", expect_wave="exit", group="A", targets=[("存储", None)],
      note="4000以上明牌主升禁板块级高低切"),
 dict(id="A5", date="2026-08-05", expect_wave="mixed", group="A", targets=[("半导体", None)],
      note="设备/材料不同浪级(设备3/材料1)"),
 dict(id="A6", date="2026-08-12", expect_wave="tside", group="A", targets=[("创新药", "LOW"), ("医药", "LOW")],
      note="科技自救非主升/药反弹转反转竞争主线"),
 # 组B
 dict(id="B1", date="2026-01-12", expect_wave="build", group="B", targets=[("卫星", None), ("航天", None)],
      note="卫星龙头死→跑不高切低; 未死→拿补涨"),
 dict(id="B2", date="2026-01-12", expect_wave="build", group="B", targets=[("商业航天", None)],
      note="商业航天加速吃AI硬的钱, 接力去向=AI软/AIDC"),
 dict(id="B3", date="2026-03-19", expect_wave="defense", group="B",
      note="指数4-1/defense期主线内调仓: AI硬海外链(麦米/CPO/电源)→国算电源链(未出货), 03-19 10:27/11:06语料证实个人操作——gate应允许defense期主线内部高低切(禁切出主线/禁新开)"),
 dict(id="B4", date="2026-05-08", expect_wave="build", group="B", targets=[("半导体", "HIGH"), ("PCB", "HIGH")],
      note="半导体顶分型破位→撤高位切低位"),
 dict(id="B5", date="2026-05-08", expect_wave="build", group="B",
      note="诱多=内资高切低/外资追高(证伪)"),
 # 组C
 dict(id="C1", date="2026-04-15", expect_wave="tside", group="C", targets=[("创新药", "LOW"), ("减肥药", "LOW"), ("中药", "LOW"), ("医药", "LOW"), ("航天", "LOW"), ("存储", "HIGH")],
      note="低位拿来切换(药/航天 LOW vs 存储高位误配)"),
 dict(id="C2", date="2026-04-16", expect_wave="tside", group="C",
      note="压力线防御性高切低(不减仓换方向)"),
 dict(id="C3", date="2026-05-11", expect_wave="na", group="C",
      note="高切低成功底线=执行纪律(无2孕线/未放量破前日低), 与wave op弱相关→不参与gate判定"),
 dict(id="C4", date="2026-07-23", expect_wave="tside", group="C",
      note="4-4机构按业绩指引调仓, ETF为主"),
 dict(id="C5", date="2026-07-27", expect_wave="tside", group="C",
      note="切换混乱期指数比小票安全"),
 dict(id="C6", date="2026-07-28", expect_wave="tside", group="C",
      note="减少品类, 不随意调仓"),
 # 组D
 dict(id="D1", date="2026-01-26", expect_wave="na", group="D",
      note="下跌日内乱切换=亏损最大化"),
 dict(id="D2", date="2026-04-14", expect_wave="tside", group="D",
      note="机构/量化高切低, A股拉非科技=弱市避险"),
 dict(id="D3", date="2026-04-15", expect_wave="tside", group="D",
      note="跌破4034高切低被识破=踩踏; 时点 gate"),
 dict(id="D4", date="2026-05-25", expect_wave="exit", group="D",
      note="寒武纪/中芯/华虹/拓荆齐动无法轮动=抽血不健康"),
 dict(id="D5", date="2026-05-26", expect_wave="t_only", group="D",
      note="主升明牌(3-4/t_only)仍禁板块级高低切——规律①: 判据是主线吸金/明牌而非wave op"),
 dict(id="D6", date="2026-08-03", expect_wave="tside", group="D",
      note="GJD稳3800无主线快速轮动→机构收割散户"),
 # 组E(个股执行, 只查 wave 参考)
 dict(id="E1", date="2025-07-11", expect_wave="na", group="E", note="换票3%割弱"),
 dict(id="E2", date="2026-04-23", expect_wave="na", group="E", note="反弹卖弱留强"),
 dict(id="E3", date="2016-06-24", expect_wave="na", group="E", note="突破回落高点止盈法"),
 dict(id="E4", date="2022-10-31", expect_wave="na", group="E", note="去弱留强该损就损"),
]

def load_json(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

def close_upto(a, upto, field="close"):
    t = pd.Timestamp(upto)
    s = pd.Series([float(x) for x in a[field]], index=pd.to_datetime(a["dates"]))
    return s[s.index <= t]

def pick_concepts(hist, kws):
    """返回与关键词匹配的 (code,name) 列表, 优先名字最短/概念更纯; kws 支持 str 或 list"""
    if isinstance(kws, str): kws = [kws]
    kws = [k for k in kws if k]
    out = []
    for code, a in hist.items():
        nm = str(a.get("name") or "")
        if any(k in nm for k in kws):
            out.append((code, nm))
    return out

def position_at(hist, date, kws, rel_map=None, max_hits=3):
    """按日期切数据回放 position_features+classify+structure+fund; 返回概念快照列表"""
    cand = pick_concepts(hist, kws)
    res = []
    for code, nm in cand:
        try:
            ser = close_upto(hist[code], date)
            if len(ser) < 80: continue
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import position_class as pc
            f = pc.position_features(ser)
            st = pc.structure_of(ser)
            f["structure"] = st
            rel = (rel_map or {}).get(code)
            if rel: f["rel_mainline"] = rel["level"]
            cls = pc.classify(f)
            # 资金: 截至 date 的最近10日符号
            net = close_upto(hist[code], date, field="net_amount").dropna().astype(float).values
            fund = {"dir": "flat", "conv": 0}
            if len(net):
                last = float(net[-1]); conv = 0
                for x in reversed(net[-10:]):
                    if (x > 0) == (last > 0) and x != 0: conv += 1
                    else: break
                fund = {"dir": "in" if last > 0 else ("out" if last < 0 else "flat"),
                        "conv": conv, "strength": round(last / 1e8, 2)}
            res.append({"code": code, "name": nm, "position": cls["position"], "trend": cls["trend"],
                        "op": cls["op"], "fund": fund, "structure": st.get("desc", "flat"),
                        "rel": (rel or {}).get("level"), "vs1y": f.get("vs_1y_high_pct"),
                        "box": f.get("box_pos_pct")})
        except Exception as e:
            res.append({"code": code, "name": nm, "err": str(e)[:80]})
    # 按名字长度排序, 保留前 max_hits
    res = [r for r in res if "err" not in r]
    res.sort(key=lambda r: len(r["name"]))
    return res[:max_hits]

def run_position():
    import position_class as pc
    hist = load_json(HIST)
    print("concepts:", len(hist), file=sys.stderr)
    out = {}
    # rel_map 全局用 upto 逐 case 太慢→只对需要概念的 case 用关键词概念计算相对位置
    for c in CASES:
        row = {"id": c["id"], "date": c["date"], "targets": []}
        for kws, _exp in (c.get("targets") or []):
            hits = pick_concepts(hist, kws)
            rel_map = pc.build_rel_map(hist, upto=c["date"])
            row["targets"].append({"kws": kws, "hits": position_at(hist, c["date"], kws, rel_map)})
        out[c["id"]] = row
        print(c["id"], c["date"], json.dumps(row, ensure_ascii=False)[:200], file=sys.stderr)
    json.dump(out, open(os.path.join(DATA, "rotation_position_replay.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", os.path.join(DATA, "rotation_position_replay.json"), "cases=", len(out))

def run_wave():
    import os as _os
    # 离线模式: 历史重放不需要实时两融/北向/GJD(多次实测 tushare 端会挂起数分钟)；量价结构+锚点+当前主线仍注入
    _os.environ['TUSHARE_TOKEN'] = ''
    _os.environ.pop('TUSHARE_API_URL', None)
    import wave_agent as wa
    import time
    dates = sorted({c["date"] for c in CASES})
    by_date = {}
    print("unique dates:", len(dates), file=sys.stderr)
    for date in dates:
        cache_p = os.path.join(DATA, "wave_state_%s.json" % date)
        cached = load_json(cache_p)
        if cached and cached.get("level"):
            by_date[date] = {k: cached.get(k) for k in ("date","level","sub_level","operation","gate","confidence","reasons")}
            print("CACHE", date, cached.get("level"), cached.get("operation"), file=sys.stderr)
            continue
        rec = {"date": date}
        try:
            f = wa.index_features(date)
            if f is None:
                rec["err"] = "no_data"
                print(date, "NO_DATA", file=sys.stderr)
                by_date[date] = rec
                continue
            prompt = wa.build_prompt(f)
            reply = wa.call_agent(prompt, session="rv_")
            res = wa.parse(reply)
            res["date"] = date
            rec.update({k: res.get(k) for k in ("level", "sub_level", "operation", "gate", "confidence", "reasons")})
            try:
                json.dump({k: res.get(k) for k in ("date","level","sub_level","operation","gate","confidence","reasons")},
                          open(cache_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            except Exception:
                pass
            print(date, json.dumps(rec, ensure_ascii=False)[:250], file=sys.stderr)
        except Exception as e:
            rec["err"] = str(e)[:160]
            print(date, "ERR", str(e)[:160], file=sys.stderr)
        by_date[date] = rec
        time.sleep(2)
    out = {}
    for c in CASES:
        rec = dict(by_date.get(c["date"]) or {})
        rec["id"] = c["id"]; rec["expect_wave"] = c.get("expect_wave")
        out[c["id"]] = rec
    json.dump(out, open(os.path.join(DATA, "rotation_wave_replay.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", os.path.join(DATA, "rotation_wave_replay.json"), "cases=", len(out))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="both", choices=["wave", "position", "both"])
    args = ap.parse_args()
    if args.mode in ("position", "both"): run_position()
    if args.mode in ("wave", "both"): run_wave()
