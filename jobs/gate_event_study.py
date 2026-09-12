# -*- coding: utf-8 -*-
"""gate_event_study.py — 「gate 确认」到底准不准：事件研究（2026-09-12）。

**用户的观察**：农业 08-12 首次确认、09-03 二次确认后确实涨了一波 → 与"gate 无区分度/反向"的统计结论冲突。
**本脚本要回答**：gate 确认之后，主题的**未来超额收益**如何？并且：
  ① 全体确认 vs 未确认（t 检验）
  ② **按主题分组**（看是否只有农业/消费这类"低位方向"有效，而半导体/AI 无效）
  ③ **按确认时的位置分组**（过去 20 日主题涨幅低/中/高、heat 排名段）→ 检验假设
     「低位确认有效、高位确认无效」（这可能才是真相：gate 不是没用，而是**只在某些状态下有用**）
  ④ 把 08-12 / 09-03 农业、消费的具体事件打出来看

口径：主题成分来自 `stock_pool.db::stock_concept_map`；收益 = 主题等权复利 − 全市场等权复利（超额）；
前瞻 5 / 10 个交易日；**确认日 D 的收益从 D+1 开始算**（盘后产出，次日才可用 → PIT）。
用法：python jobs/gate_event_study.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")

THEME_CONCEPT_KW: Dict[str, List[str]] = {
    "半导体/芯片": ["半导体", "芯片", "光刻", "封测", "存储"],
    "AI/算力/科技": ["算力", "人工智能", "光通信", "光器件", "CPO", "数据中心", "服务器"],
    "医药": ["医药", "创新药", "CXO", "疫苗", "医疗器械", "中药"],
    "新能源/电池": ["锂电", "电池", "光伏", "储能", "风电", "新能源"],
    "机器人/智能制造": ["机器人", "机床", "工业母机", "自动化", "智能制造"],
    "汽车/智驾": ["汽车", "智能驾驶", "车路云", "智能网联", "汽车零部件", "激光雷达"],
    "消费/内需": ["白酒", "食品", "饮料", "零售", "家电", "免税", "餐饮"],
    "农业": ["农业", "生猪", "养殖", "种业", "饲料", "种植"],
    "电力/公用": ["电力", "电网", "核电", "水电", "燃气", "绿电"],
    "稳增长/基建": ["基建", "建筑", "地产", "水泥", "钢铁", "工程机械", "水利"],
    "资源/周期": ["有色", "煤炭", "黄金", "稀土", "石油", "化工", "小金属"],
    "金融": ["券商", "银行", "保险", "多元金融"],
    "军工/航天": ["军工", "航天", "航空", "卫星", "国防", "低空"],
}


def _stat(v: List[float]) -> Dict[str, Any]:
    n = len(v)
    if n == 0:
        return {"n": 0}
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
    return {"n": n, "mean": round(m * 100, 3), "t": round(m / (sd / n ** 0.5), 2) if sd else None,
            "pos": round(sum(1 for x in v if x > 0) / n, 3)}


def load_universe() -> Dict[str, List[str]]:
    import os
    db = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "stock_pool.db")
    c = sqlite3.connect(db)
    st = {r[0] for r in c.execute("SELECT ts_code FROM stock_pool WHERE is_st=1")}
    out = {}
    for th, kws in THEME_CONCEPT_KW.items():
        codes = set()
        for kw in kws:
            for (ts,) in c.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name LIKE ?",
                                   ("%" + kw + "%",)):
                if ts and ts not in st:
                    codes.add(ts)
        if codes:
            out[th] = sorted(codes)
    c.close()
    return out


def main() -> int:
    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        px: Dict[str, Dict[str, float]] = {}
        for d, ts, pc in db.execute(text("SELECT trade_date, ts_code, pct_chg FROM mkt_bars_daily "
                                        "WHERE pct_chg IS NOT NULL")).all():
            px.setdefault(d, {})[ts] = float(pc) / 100.0
        days = sorted(px.keys())
        uni = load_universe()
        gate_rows: List[Tuple[str, Dict[str, Any]]] = []
        for r in db.execute(text("SELECT trade_date, payload FROM daily_artifacts "
                                 "WHERE artifact_key='mainline_gate'")).mappings().all():
            p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
            for x in (p.get("rows") or []):
                gate_rows.append((r["trade_date"], x))
        print("[ev] 行情 %d 天 | 主题 %d 个 | gate 行 %d 条" % (len(days), len(uni), len(gate_rows)), flush=True)

        mkt = {d: (sum(px[d].values()) / len(px[d])) for d in days if px[d]}

        def fwd_excess(d: str, th: str, h: int) -> Optional[float]:
            if th not in uni or d not in days:
                return None
            i = days.index(d)
            seg = days[i + 1: i + 1 + h]
            if len(seg) < h:
                return None
            codes = uni[th]
            c = 1.0
            for dd in seg:
                vals = [px[dd][x] for x in codes if x in px[dd]]
                if vals:
                    c *= (1 + sum(vals) / len(vals))
            m = 1.0
            for dd in seg:
                m *= (1 + mkt.get(dd, 0.0))
            return c - m

        def prior_ret(d: str, th: str, back: int = 20) -> Optional[float]:
            if th not in uni or d not in days:
                return None
            i = days.index(d)
            seg = days[max(0, i - back): i]
            if len(seg) < 5:
                return None
            codes = uni[th]
            c = 1.0
            for dd in seg:
                vals = [px[dd][x] for x in codes if x in px[dd]]
                if vals:
                    c *= (1 + sum(vals) / len(vals))
            return c - 1

        conf5, not5, conf10, not10 = [], [], [], []
        by_theme: Dict[str, Dict[str, List[float]]] = {}
        by_heat: Dict[str, List[float]] = {}
        by_pos: Dict[str, List[float]] = {}
        events = []
        for d, x in gate_rows:
            th = x.get("theme")
            if th not in uni:
                continue
            e5, e10 = fwd_excess(d, th, 5), fwd_excess(d, th, 10)
            if e5 is None:
                continue
            if x.get("gate"):
                conf5.append(e5)
                if e10 is not None:
                    conf10.append(e10)
                by_theme.setdefault(th, []).append(e5)
                hr = x.get("heat_rank")
                if hr is not None:
                    band = "1-3" if hr <= 3 else ("4-7" if hr <= 7 else "8-14")
                    by_heat.setdefault(band, []).append(e5)
                pr = prior_ret(d, th, 20)
                if pr is not None:
                    band = "低位(过去20日<-3%)" if pr < -0.03 else ("中位(-3%~+5%)" if pr <= 0.05 else "高位(>+5%)")
                    by_pos.setdefault(band, []).append(e5)
                events.append({"date": d, "theme": th, "heat_rank": hr,
                               "prior20": None if pr is None else round(pr * 100, 2),
                               "ex5": round(e5 * 100, 2),
                               "ex10": None if e10 is None else round(e10 * 100, 2)})
            else:
                not5.append(e5)
                if e10 is not None:
                    not10.append(e10)

        print("[ev] ① 全体：确认 5日超额 %s | 未确认 %s" % (json.dumps(_stat(conf5), ensure_ascii=False),
                                                       json.dumps(_stat(not5), ensure_ascii=False)), flush=True)
        print("[ev]          确认 10日超额 %s | 未确认 %s" % (json.dumps(_stat(conf10), ensure_ascii=False),
                                                        json.dumps(_stat(not10), ensure_ascii=False)), flush=True)
        print("[ev] ② 按主题（确认后 5 日超额，按均值排序）:", flush=True)
        for th, v in sorted(by_theme.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            print("      %-14s %s" % (th, json.dumps(_stat(v), ensure_ascii=False)), flush=True)
        print("[ev] ③ 按确认时 heat 排名:", flush=True)
        for b in ("1-3", "4-7", "8-14"):
            if b in by_heat:
                print("      heat %-5s %s" % (b, json.dumps(_stat(by_heat[b]), ensure_ascii=False)), flush=True)
        print("[ev] ④ 按确认时位置（过去20日主题涨幅）:", flush=True)
        for b in ("低位(过去20日<-3%)", "中位(-3%~+5%)", "高位(>+5%)"):
            if b in by_pos:
                print("      %-18s %s" % (b, json.dumps(_stat(by_pos[b]), ensure_ascii=False)), flush=True)
        # 农业 / 消费 专项
        print("[ev] ⑤ 农业 / 消费相关确认事件（最近 12 条）:", flush=True)
        for ev in [e for e in events if e["theme"] in ("农业", "消费/内需")][-12:]:
            print("      %s %-10s heat=%-5s 前20日=%s%% → 5日超额=%s%% 10日=%s%%" %
                  (ev["date"], ev["theme"], ev["heat_rank"], ev["prior20"], ev["ex5"], ev["ex10"]), flush=True)
        out = {"confirmed_5d": _stat(conf5), "not_confirmed_5d": _stat(not5),
               "confirmed_10d": _stat(conf10), "not_confirmed_10d": _stat(not10),
               "by_theme_5d": {k: _stat(v) for k, v in by_theme.items()},
               "by_heat_5d": {k: _stat(v) for k, v in by_heat.items()},
               "by_position_5d": {k: _stat(v) for k, v in by_pos.items()},
               "events_tail": events[-60:],
               "note": "超额=主题等权复利−全市场等权复利；确认日 D 的收益自 D+1 起算（PIT）"}
        with open("/app/data/gate_event_study.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("[ev] 已写 /app/data/gate_event_study.json", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
