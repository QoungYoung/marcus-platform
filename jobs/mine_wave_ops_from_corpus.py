# -*- coding: utf-8 -*-
"""v3：档位 × **阶段**（进行中 / 末段 / 转折确认）分桶 —— 解决 v2 里"同档极性自相矛盾"的问题"""
import collections, json, re, sys
sys.path.insert(0, "jobs")
from eval_wave_ladder_vs_corpus import read_corpus

ROWS = [r for r in read_corpus() if r[0] >= "2024"]
STAGES = {
    "d1/反转1浪": ["反转大1浪", "大1浪", "1浪起"],
    "d2/大2浪": ["大2浪", "大二浪"],
    "3-1/起步": ["3-1", "三浪1", "3浪1"],
    "3-2/回调": ["3-2", "三浪2", "3浪2"],
    "3-3/最强主升": ["3-3", "三浪3", "3浪3", "主升3浪"],
    "3-4/中继": ["3-4", "三浪4", "3浪4"],
    "3-5/末段": ["3-5", "三浪5", "3浪5", "5浪末", "五浪末"],
    "d5/衰竭": ["衰竭浪", "末段浪", "衰竭"],
    "双头/M顶": ["双头", "M顶", "m顶", "顶部结构"],
    "W底/双底": ["W底", "w底", "双底", "筑底"],
    "B反": ["B反", "b反", "B浪"],
    "C杀": ["C杀", "C浪", "最后一跌"],
    "ABC": ["ABC", "abc"],
    "下跌浪/熊市": ["下跌浪", "主跌", "熊市", "下跌段"],
}
ACTS = [
    ("build",  re.compile(r"建仓|加仓|追|重仓|满仓|买入|低吸|接回|上仓位|进场|布局|抄底")),
    ("t_only", re.compile(r"只做\s*T|做\s*T|T出去|T掉|不追|反抽")),
    ("side",   re.compile(r"观望|调仓|换股|轻仓|躺平|分批|不参与|看着|等企稳|坐等")),
    ("defense",re.compile(r"防御|不建仓|不新开|不加仓|不做|空仓|规避|降仓|减仓|收手|少动")),
    ("exit",   re.compile(r"兑现|止盈|出货|先卖|卖掉|走人|清仓|离场|撤")),
]
CTX = re.compile(r"大盘|指数|上证|科创|双创|市场|行情|熊市|牛市|板块")
LATE = re.compile(r"末段|最后一|快结束|即将|加速|尾声|衰竭|透支|到顶|见顶|快完|尾段")
TRANS = re.compile(r"确认|站稳|突破|化解|开启|结束|转为|切换|跳到|进入下")


def pol(ops):
    s = set(ops)
    if "build" in s and not (s & {"defense", "exit"}):
        return "偏多"
    if s & {"defense", "exit"} and "build" not in s:
        return "偏空"
    if "t_only" in s and not (s & {"build", "defense", "exit"}):
        return "只T"
    return "混合"


res = collections.defaultdict(lambda: collections.defaultdict(list))
for name, kws in STAGES.items():
    rx = re.compile("|".join(re.escape(k) for k in kws))
    for d8, dt, txt in ROWS:
        m = rx.search(txt)
        if not m:
            continue
        seg = txt[max(0, m.start() - 120):m.end() + 120]
        if not CTX.search(seg):
            continue
        ops = [o for o, arx in ACTS if arx.search(seg)]
        if not ops:
            continue
        phase = "末段" if LATE.search(seg) else ("转折确认" if TRANS.search(seg) else "进行中")
        res[name][phase].append({"d8": d8, "dt": dt, "ops": ops, "pol": pol(ops),
                                 "seg": re.sub(r"\s+", " ", seg)})
print("%-13s %-6s %4s  %s" % ("档位", "阶段", "n", "极性分布"))
for name in STAGES:
    for phase in ("进行中", "末段", "转折确认"):
        rows = res[name].get(phase, [])
        if rows:
            print("  %-13s %-6s %4d  %s" % (name, phase, len(rows),
                  dict(collections.Counter(x["pol"] for x in rows))))
out = {n: {ph: rows for ph, rows in d.items()} for n, d in res.items()}
json.dump(out, open("data/_corpus_wave_ops.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n=== 「进行中」且样本≥2 的档位：代表原话（每档 2 条）===")
for name in ("3-2/回调", "3-3/最强主升", "B反", "C杀", "双头/M顶", "下跌浪/熊市"):
    rows = res[name].get("进行中", [])
    if len(rows) < 2:
        continue
    print("\n[%s 进行中 n=%d]" % (name, len(rows)))
    for x in rows[-2:]:
        print("  %s (%s) %s" % (x["dt"][:16], "+".join(x["ops"]), x["seg"][:170]))
