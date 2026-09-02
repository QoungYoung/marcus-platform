# -*- coding: utf-8 -*-
"""主线融合样本外验证 (2026-09-02)
样本内(IS): 22 重放日期(2025-10-21~2026-08-13), 27 条可评估标注 —— v1 权重搜索集
样本外(OOS): 8 日期(2025-09-04~2025-10-17), 7 条可评估标注(2 条机器人无主题映射) —— 严格未参与权重选择
评估:
  1) 固定 v1 最优权重 (0.0,0.3,0.2,0.5) 在 OOS 命中率 (跨样本检验, 对比 v2 旧代理 60%)
  2) OOS 单独网格搜索 -> OOS 最优权重, 与 v1 对比稳定性
  3) 全量(IS+OOS)网格搜索 -> 最优权重是否仍落 v1 区域
用法: python apps/main_line/eval_mainline_outsample.py
"""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_mainline as fm

DATA = os.environ.get("DATA_DIR", "data")
OOS_DATES = ['2025-09-04', '2025-09-10', '2025-09-19', '2025-10-15', '2025-10-16', '2025-10-17']
V1_W = (0.0, 0.3, 0.2, 0.5)   # 生产/IS 最优 (2026-09-01 网格搜索)

def load_rows():
    hist = fm.load("concept_hist.json")
    rows = fm.load_labels()
    for r in rows:
        r["oos"] = r["date"] in OOS_DATES
    return hist, rows

def grid(rows, hist):
    best = None
    for w1 in np.arange(0, 1.01, 0.1):
        for w2 in np.arange(0, 1.01 - w1 + 0.001, 0.1):
            for w3 in np.arange(0, 1.01 - w1 - w2 + 0.001, 0.1):
                w4 = round(1 - w1 - w2 - w3, 1)
                if w4 < -0.001:
                    continue
                results = fm.score_and_match(rows, hist, (round(w1, 1), round(w2, 1), round(w3, 1), w4))
                ok = sum(1 for x in results if x["match"])
                if best is None or ok > best[0]:
                    best = (ok, (round(w1, 1), round(w2, 1), round(w3, 1), w4), results)
    return best

def rate(results):
    n = len(results)
    return (sum(1 for x in results if x["match"]), n) if n else (0, 0)

def main():
    hist, rows = load_rows()
    is_rows = [r for r in rows if not r["oos"]]
    oos_rows = [r for r in rows if r["oos"]]
    print(f"总可评估: {len(rows)} (IS {len(is_rows)} + OOS {len(oos_rows)})")
    print(f"OOS 标注: {[(r['date'], r['label_theme']) for r in oos_rows]}")

    # 1) 固定 v1 最优权重
    oos_detail = None
    for name, rr in [("IS(样本内 22日)", is_rows), ("OOS(样本外 7条)", oos_rows)]:
        res = fm.score_and_match(rr, hist, V1_W)
        ok, n = rate(res)
        print(f"[固定 v1 权重 {V1_W}] {name}: {ok}/{n} = {100*ok/n if n else 0:.0f}%")
        if name.startswith("OOS"):
            oos_detail = res
            for x in sorted(res, key=lambda z: z["date"]):
                m = "✓" if x["match"] else "✗"
                print(f"   [{x['date']}] {x['label_theme']:4} expect={x['expect']} {m} rank={x['rank']} score={x['score']} top1={x['top1']}")

    # 2) OOS 单独网格
    ok, w, _ = grid(oos_rows, hist)
    print(f"\n[OOS 单独网格] 最优 w={w} -> {ok}/{len(oos_rows)} = {100*ok/len(oos_rows):.0f}%  (v1 最优={V1_W})")

    # 3) 全量网格
    ok2, w2, _ = grid(rows, hist)
    print(f"[全量网格]    最优 w={w2} -> {ok2}/{len(rows)} = {100*ok2/len(rows):.0f}%  (v1 最优={V1_W})")

    # 4) OOS 单信号
    print("\n=== OOS 单信号对比 ===")
    for name, ww in [("纯研报", (1, 0, 0, 0)), ("纯资金", (0, 1, 0, 0)), ("纯强度", (0, 0, 1, 0)),
                     ("纯集中度", (0, 0, 0, 1)), ("等权", (0.25, 0.25, 0.25, 0.25))]:
        rr = fm.score_and_match(oos_rows, hist, ww)
        okk, nn = rate(rr)
        print(f"  {name}: {okk}/{nn} = {100*okk/nn if nn else 0:.0f}%")

    # 保存明细
    out = {
        "v1_w": list(V1_W),
        "oos_dates": OOS_DATES,
        "oos_detail": [
            {"date": x["date"], "theme": x["label_theme"], "expect": x["expect"],
             "rank": x["rank"], "score": x["score"], "top1": x["top1"], "match": x["match"]}
            for x in (oos_detail or [])
        ],
    }
    with open(os.path.join(DATA, "mainline_oos_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nWROTE data/mainline_oos_result.json")

if __name__ == "__main__":
    main()
