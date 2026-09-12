# -*- coding: utf-8 -*-
"""catalyst_ic_test.py — 研报标题分的**可检验性**测试（2026-09-12）。

**要回答的问题**：用研报标题（还只是标题）当催化/方向输入，到底有没有预测力？
在把它接进 L1 方向之前，先用可证伪的方式测：标题密度分 vs 主题未来超额收益。

**口径（严格 PIT）**
  · 打分日 D：只用 `trade_date <= D-1` 的研报（研报常在盘中/盘后发布，用当日即前视）
  · 前瞻窗口：D+1 … D+h（h=5 / 10）个交易日
  · 主题收益：成分股（`wolf_confirm_pick.confirm_universe`，与布腿链同一套）等权日收益复利
  · 基准：全市场等权（`mkt_bars_daily` 全票 `pct_chg` 等权复利）→ **超额 = 主题 − 基准**
  · 分数：主题关键词命中数（原始 count）+ 横截面 z（分开看）
  · IC：逐日横截面 Spearman(score, 超额)，再汇总 mean / ICIR / t / 正比例
  · **对照**：另算一版"用当日(≤D)标题"的 IC —— 差值就是前视带来的虚高

主题↔关键词映射是我们定的（不是他给的），脚本会把它打印出来以便审计。
用法：python jobs/catalyst_ic_test.py [--h 5,10] [--window 30]
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/apps/main_line")

# 主题 → 标题关键词（我们的构造，需审计；与 MAIN_THEMES 名称对齐）
THEME_KW: Dict[str, List[str]] = {
    "半导体/芯片": ["半导体", "芯片", "晶圆", "存储", "光刻", "封测", "IC设计", "先进封装", "碳化硅", "第三代半导体"],
    "AI/算力/科技": ["算力", "AI", "人工智能", "光模块", "CPO", "服务器", "液冷", "数据中心", "大模型", "国产算力"],
    "医药": ["医药", "创新药", "CXO", "疫苗", "医疗器械", "生物", "中药", "GLP", "临床"],
    "新能源/电池": ["锂电", "电池", "光伏", "储能", "风电", "新能源", "钠电", "固态电池", "组件", "硅料"],
    "机器人/智能制造": ["机器人", "人形", "减速器", "丝杠", "机床", "自动化", "工业母机", "智能制造"],
    "汽车/智驾": ["汽车", "智驾", "智能驾驶", "整车", "零部件", "座舱", "车路云", "激光雷达"],
    "消费/内需": ["消费", "白酒", "食品", "饮料", "零售", "家电", "纺织", "免税", "餐饮", "啤酒"],
    "农业": ["农业", "生猪", "养殖", "种业", "饲料", "禽", "转基因", "粮食"],
    "电力/公用": ["电力", "电网", "核电", "水电", "公用事业", "燃气", "火电", "绿电"],
    "稳增长/基建": ["基建", "建筑", "地产", "水泥", "钢铁", "工程机械", "城投", "水利"],
    "资源/周期": ["有色", "煤炭", "黄金", "铜", "铝", "稀土", "石油", "化工", "锂矿", "小金属"],
    "金融": ["券商", "银行", "保险", "金融", "多元金融", "证券"],
    "军工/航天": ["军工", "航天", "航空", "导弹", "卫星", "国防", "无人机", "低空"],
}


# 主题 → **概念关键词**（用于从 stock_pool.db::stock_concept_map 取成分股；与标题关键词分开）
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


def load_universe_from_concepts(min_codes: int = 20) -> Dict[str, List[str]]:
    """从 stock_pool.db 的 stock_concept_map 取各主题成分股（排除 ST）。

    为什么不用 `confirm_universe`：实测它只覆盖 3 个主题（依赖各主题的 confirm_hist 文件），
    而 IC 检验需要横截面 ≥ 4 个主题。这里改用概念库（同样的生产数据源，覆盖面稳定）。
    """
    import sqlite3
    db = os.path.join(os.environ.get("DATA_DIR", "/app/data"), "stock_pool.db")
    uni: Dict[str, List[str]] = {}
    try:
        c = sqlite3.connect(db)
        st = {r[0] for r in c.execute("SELECT ts_code FROM stock_pool WHERE is_st=1")}
        for th, kws in THEME_CONCEPT_KW.items():
            codes = set()
            for kw in kws:
                for (ts,) in c.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name LIKE ?",
                                       ("%" + kw + "%",)):
                    if ts and ts not in st:
                        codes.add(ts)
            if len(codes) >= min_codes:
                uni[th] = sorted(codes)
        c.close()
    except Exception as e:
        print("[ic] 概念库取成分失败: %s: %s" % (type(e).__name__, str(e)[:80]), flush=True)
    return uni


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman 秩相关（纯 python，避免 scipy 依赖）。"""
    n = len(xs)
    if n < 4:
        return None
    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def load_data() -> Tuple[Dict[str, Dict[str, float]], Dict[str, List[str]], List[Tuple[str, str]]]:
    """返回 (行情 {date: {ts_code: pct_chg}}, 主题成分 {theme: [ts_code]}, 研报 [(date, title)])。"""
    from app.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        px: Dict[str, Dict[str, float]] = {}
        for d, ts, pc in db.execute(text(
                "SELECT trade_date, ts_code, pct_chg FROM mkt_bars_daily WHERE pct_chg IS NOT NULL")).all():
            px.setdefault(d, {})[ts] = float(pc)
        rows = db.execute(text("SELECT DISTINCT trade_date, title FROM research_reports "
                               "WHERE title IS NOT NULL")).all()
        reports = [(r[0], r[1]) for r in rows]
    finally:
        db.close()
    uni = load_universe_from_concepts()
    # 对照：生产布腿链用的 confirm_universe 覆盖多少主题（仅供审计）
    try:
        from wolf_confirm_pick import confirm_universe
        import fusion_mainline as fm
        cov = []
        for th in [t for t in getattr(fm, "MAIN_THEMES", []) if t in THEME_KW]:
            u = confirm_universe(th) or {}
            n = sum(len(v) for v in u.values())
            if n:
                cov.append("%s=%d" % (th, n))
        print("[ic] confirm_universe 覆盖: %s" % (", ".join(cov) or "无"), flush=True)
    except Exception:
        pass
    return px, uni, reports


def _score_titles(titles: Sequence[str]) -> Dict[str, int]:
    sc = {th: 0 for th in THEME_KW}
    for t in titles:
        for th, kws in THEME_KW.items():
            if any(k in t for k in kws):
                sc[th] += 1
    return sc


def _z(d: Dict[str, float]) -> Dict[str, float]:
    vals = [v for v in d.values()]
    if not vals:
        return d
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / max(1, len(vals))
    sd = var ** 0.5
    return {k: ((v - m) / sd if sd else 0.0) for k, v in d.items()}


def main() -> int:
    hs = [5, 10]
    window = 30
    argv = sys.argv
    if "--h" in argv:
        try:
            hs = [int(x) for x in argv[argv.index("--h") + 1].split(",")]
        except Exception:
            pass
    if "--window" in argv:
        try:
            window = int(argv[argv.index("--window") + 1])
        except Exception:
            pass

    px, uni, reports = load_data()
    days = sorted(px.keys())
    print("[ic] 行情 %d 天(%s→%s) | 主题成分 %d 个 | 研报 %d 条" %
          (len(days), days[0], days[-1], len(uni), len(reports)), flush=True)
    print("[ic] 主题关键词映射（我们的构造）:", json.dumps({k: v[:3] for k, v in THEME_KW.items()}, ensure_ascii=False)[:300], flush=True)
    themes = [th for th in THEME_KW if th in uni]
    if not themes:
        print("[ic] 没有可用主题成分，退出"); return 1

    # 主题日收益（等权）
    theme_ret: Dict[str, Dict[str, float]] = {th: {} for th in themes}
    for th in themes:
        codes = set(uni[th])
        for d in days:
            vals = [px[d][c] for c in codes if c in px[d]]
            if vals:
                theme_ret[th][d] = sum(vals) / len(vals) / 100.0
    # 基准（全市场等权）
    mkt_ret = {d: (sum(px[d].values()) / len(px[d]) / 100.0) for d in days if px[d]}

    def cum(d0: str, i0: int, h: int) -> Tuple[Optional[Dict[str, float]], Optional[float]]:
        """从 days[i0+1] 起连续 h 日的主题/基准复利收益。"""
        seg = days[i0 + 1: i0 + 1 + h]
        if len(seg) < h:
            return None, None
        th_cum, mk = {}, 1.0
        for d in seg:
            mk *= (1 + mkt_ret.get(d, 0.0))
        for th in themes:
            c = 1.0
            for d in seg:
                c *= (1 + theme_ret[th].get(d, 0.0))
            th_cum[th] = c - 1
        return th_cum, mk - 1

    rep_by_day: Dict[str, List[str]] = {}
    for d, t in reports:
        rep_by_day.setdefault(d, []).append(t)

    out: Dict[str, Any] = {"generated_at": None, "themes": themes, "n_days": len(days),
                           "results": {}, "pit": "score uses titles with trade_date <= D-1"}
    import datetime as _dt
    out["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")

    for h in hs:
        for mode in ("pit", "sametag"):
            ics_count, ics_z, valid_days = [], [], 0
            for i, d in enumerate(days):
                if i + h >= len(days):
                    break
                # 打分窗口
                lo = days[max(0, i - window)]
                hi = days[i - 1] if mode == "pit" else days[i]
                titles = []
                for dd in days[max(0, i - window): i + (0 if mode == "pit" else 1)]:
                    if dd <= hi:
                        titles.extend(rep_by_day.get(dd, []))
                if not titles:
                    continue
                sc = _score_titles(titles)
                fwd, mk = cum(d, i, h)
                if not fwd:
                    continue
                xs = [float(sc[th]) for th in themes]
                zs = _z({th: float(sc[th]) for th in themes})
                ex = [fwd[th] - mk for th in themes]
                r1 = _spearman(xs, ex)
                r2 = _spearman([zs[th] for th in themes], ex)
                if r1 is not None:
                    ics_count.append(r1); valid_days += 1
                if r2 is not None:
                    ics_z.append(r2)
            def summ(v):
                n = len(v)
                if n == 0:
                    return {"n_days": 0}
                m = sum(v) / n
                sd = (sum((x - m) ** 2 for x in v) / max(1, n - 1)) ** 0.5
                return {"n_days": n, "mean_ic": round(m, 4),
                        "icir": round(m / sd, 3) if sd else None,
                        "t": round(m / (sd / n ** 0.5), 2) if sd else None,
                        "pos_ratio": round(sum(1 for x in v if x > 0) / n, 3)}
            out["results"]["h%d_%s_count" % (h, mode)] = summ(ics_count)
            out["results"]["h%d_%s_z" % (h, mode)] = summ(ics_z)
            print("[ic] h=%d %-7s count-IC: %s | z-IC: %s" %
                  (h, mode, json.dumps(summ(ics_count), ensure_ascii=False),
                   json.dumps(summ(ics_z), ensure_ascii=False)), flush=True)

    try:
        with open("/app/data/catalyst_ic_report.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("[ic] 已写 /app/data/catalyst_ic_report.json", flush=True)
    except Exception as e:
        print("[ic] 落盘失败: %s" % str(e)[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
