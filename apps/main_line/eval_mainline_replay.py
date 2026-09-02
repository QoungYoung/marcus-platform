# -*- coding: utf-8 -*-
"""主线历史重放评估: data/main_line_state_<date>.json vs wolf_labels_v2 mainline 标注
用法: python apps/main_line/eval_mainline_replay.py [标注文件]
"""
import json, os, sys

DATA = os.environ.get("DATA_DIR", "data")
# 标注主题 → 主线判定主题(THEMES 键); None=主线判定不覆盖该主题
MAIN_THEME_OF = {
  "AI": "AI/算力/科技", "AI硬": "AI/算力/科技", "AI应用": "AI/算力/科技",
  "科技": "AI/算力/科技", "光": "AI/算力/科技", "算力": "AI/算力/科技",
  "半导体": "半导体/芯片", "电池": "新能源/电池", "金融": "金融",
  "消费": "消费/内需", "医药": "医药", "航天": "军工/航天",
  "游戏": None, "软件": None, "数据": None, "PCB": None, "液冷": None, "稀土": None, "有色": None,
}

def load(p):
    return json.load(open(os.path.join(DATA, p), encoding="utf-8"))

def main():
    labels_file = sys.argv[1] if len(sys.argv) > 1 else "wolf_labels_v2.json"
    labels = load(labels_file)
    results = []
    for l in labels.get("mainline", []):
        mt = MAIN_THEME_OF.get(l["theme"])
        if mt is None:
            results.append({**l, "match": None, "reason": "no_mainline_theme", "state": None})
            continue
        state_file = os.path.join(DATA, f"main_line_state_{l['date']}.json")
        if not os.path.exists(state_file):
            results.append({**l, "match": None, "reason": "no_state", "state": None})
            continue
        state = load(f"main_line_state_{l['date']}.json")
        ml = state.get("main_line"); cands = state.get("candidates") or []
        in_set = mt == ml or mt in cands
        catalyst = (state.get("catalyst") or {}).get(mt)
        expect = bool(l.get("expect"))
        # 判定: expect=true → 该主题应被识别为主线/候选; expect=false → 不应
        match = in_set if expect else (not in_set)
        results.append({**l, "match": match, "reason": None,
                        "state": {"main_line": ml, "candidates": cands, "in_set": in_set,
                                  "catalyst": catalyst}})
    # 汇总
    judged = [r for r in results if r["match"] is not None]
    ok = sum(1 for r in judged if r["match"])
    no_theme = [r for r in results if r.get("reason") == "no_mainline_theme"]
    no_state = [r for r in results if r.get("reason") == "no_state"]
    print(f"主线标注: {len(results)} | 可评估: {len(judged)} | 命中: {ok}/{len(judged)} = {100*ok/len(judged) if judged else 0:.0f}%")
    print(f"无法映射主题(游戏/软件等): {len(no_theme)} | 缺state: {len(no_state)}")
    for r in judged:
        m = "✓" if r["match"] else "✗"
        st = r["state"]
        print(f"  [{r['date']}] {r['theme']:6} expect={r['expect']} {m}  main_line={st['main_line']} cands={st['candidates']} catalyst({r['theme']})={st['catalyst']} | {r.get('note','')[:40]}")
    # 保存明细
    json.dump(results, open(os.path.join(DATA, "mainline_replay_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE data/mainline_replay_result.json")

if __name__ == "__main__":
    main()
