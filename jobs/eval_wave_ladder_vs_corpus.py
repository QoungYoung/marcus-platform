# -*- coding: utf-8 -*-
"""eval_wave_ladder_vs_corpus.py — 用他自己的语料给浪型链打分（golden set，v2）。

v1 教训（2026-10-15 首跑）：25131 行里只有 28 条命中「无歧义结构词」⇒ 打分样本 16 条，且大量
「调整浪」其实在说**个股/别的级别**（例：「潍柴…你前面的横盘你看成大2浪的调整浪就行」✗）。
所以 v2 两条硬门：
  · **指数语境**：命中词前后 ±40 字内必须出现 大盘/指数/上证/科创/双创/市场；
  · **度标记**：结构档只认 4-x / 失败5 / 下跌1浪 这种带级别的说法（裸「调整浪」不再单独计分）。

两套记分卡：
  A. 结构档（他的浪型口径 → 我们的 rung）：4浪调整 / 成功4-5 / 失败4-5 / 下跌1浪
  B. **操作档**（他说怎么做 → 我们的 operation）：只做T→t_only；不加仓/不新开/减仓→defense；
     建仓/加仓/按4接5做→side；止损/自救/清仓→exit
  两套都给「严格一致」与「同组一致」（A：相邻 1 档；B：build/side 同组、defense/exit 同组）。

用法::  python jobs/eval_wave_ladder_vs_corpus.py [--min-year 2024]
"""
import argparse
import bisect
import collections
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "apps", "main_line")):
    if p not in sys.path:
        sys.path.insert(0, p)
import wave_ladder as W  # noqa: E402

XLSX = "狼大回复汇总 20260814-1457&往期.xlsx"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
QUOTE = re.compile(r"\[quote\].*?\[/quote\]", re.S | re.I)
DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
SCENARIO = re.compile(r"如果|一旦|假如|要是|万一")
INDEX_CTX = re.compile(r"大盘|指数|上证|科创|双创|市场|全面")
RUNG_NAME = {0: "4浪调整", 1: "成功4-5", 2: "失败4-5", 3: "下跌1浪"}
STRUCT = [("down1", 3, re.compile(r"下跌1浪|主跌1浪|转为主跌|转为下跌")),
          ("fail5", 2, re.compile(r"失败5|失败浪|失败五")),
          ("ok45", 1, re.compile(r"成功5|开启4-5|走4-5|4-5(?:就)?(?:走完|结束)|第5浪|五浪")),
          ("adj4", 0, re.compile(r"4浪调整|现4-[1-4]|处于4-[1-4]|还在4-[1-4]|4-4(?:就)?结束不了"))]
OPMAP = [("exit", re.compile(r"止损|自救|清仓|空仓|割")),
         ("defense", re.compile(r"不加仓|不新开|不新建|减仓|降仓|降回|控仓|仓位内")),
         ("t_only", re.compile(r"只做\s*T|做\s*T|T出去|吃反弹|做反抽|反抽")),
         ("side", re.compile(r"建仓|加仓|低吸|买入|按4接5|按\s*4\s*接\s*5"))]
OP_GROUP = {"build": "多", "side": "多", "t_only": "T", "defense": "空", "exit": "空"}


def _col(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    n = 0
    for ch in (m.group(1) if m else ""):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_corpus():
    z = zipfile.ZipFile(XLSX)
    ss = []
    for _, el in ET.iterparse(z.open("xl/sharedStrings.xml"), events=("end",)):
        if el.tag == NS + "si":
            ss.append("".join(t.text or "" for t in el.iter(NS + "t")))
            el.clear()
    out = []
    for sh in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
        for _, el in ET.iterparse(z.open(sh), events=("end",)):
            if el.tag != NS + "row":
                continue
            cells = {}
            for c in el.findall(NS + "c"):
                v, t = c.find(NS + "v"), c.get("t")
                if t == "inlineStr":
                    txt = "".join(x.text or "" for x in c.iter(NS + "t"))
                elif v is None:
                    txt = ""
                elif t == "s":
                    txt = ss[int(v.text)]
                else:
                    txt = v.text or ""
                cells[_col(c.get("r"))] = txt
            el.clear()
            d = (cells.get(0) or "").strip()
            body = " ".join(x for x in ((cells.get(1) or ""), (cells.get(2) or "")) if x)
            m = DATE.match(d)
            if not m or not body:
                continue
            mine = re.sub(r"\[[^\]]{0,24}\]", " ", QUOTE.sub(" ", body))
            mine = re.sub(r"\s+", " ", mine).strip()
            if len(mine) >= 6:
                out.append((m.group(1) + m.group(2) + m.group(3), d[:16], mine))
    return out


def ctx_ok(text, rx):
    """命中词 ±40 字内有指数语境 ✓（v1 教训：裸「调整浪」多半在说个股/别的级别 ✗）。"""
    for m in rx.finditer(text):
        seg = text[max(0, m.start() - 40):m.end() + 40]
        if INDEX_CTX.search(seg):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-year", default="2024")
    args = ap.parse_args()
    rows = [r for r in read_corpus() if r[0][:4] >= args.min_year]
    evs = W.walk(W.load_rows())
    by = [e["date"] for e in evs]

    def at(d8):
        i = bisect.bisect_right(by, d8) - 1
        return evs[i] if i >= 0 else None

    funnel = collections.Counter()
    struct_scored, op_scored = [], []
    for d8, dts, mine in rows:
        funnel["语料行"] += 1
        if SCENARIO.search(mine):
            funnel["条件句 skip"] += 1
            continue
        ev = at(d8)
        if not ev:
            continue
        # A. 结构
        for _name, rung, rx in STRUCT:
            if rx.search(mine):
                if not ctx_ok(mine, rx):
                    funnel["结构-无指数语境 skip"] += 1
                    break
                struct_scored.append({"dt": dts, "his_rung": rung, "our_rung": ev["rung_index"],
                                      "text": mine[:150], "ours": (ev.get("reasons") or "")[:70]})
                break
        # B. 操作
        for op, rx in OPMAP:
            if rx.search(mine):
                if not ctx_ok(mine, rx):
                    funnel["操作-无指数语境 skip"] += 1
                    break
                op_scored.append({"dt": dts, "his_op": op, "our_op": ev["operation"],
                                  "text": mine[:150]})
                break

    def score(name, items, key_h, key_o, same, group=None):
        n = len(items)
        if not n:
            print("%s：无样本" % name)
            return
        ex = sum(1 for x in items if key_h(x) == key_o(x))
        gr = sum(1 for x in items if group(x)) if group else ex
        print("%s：n=%d ｜ 严格一致 %d (%.1f%%) ｜ %s %d (%.1f%%)" % (
            name, n, ex, 100 * ex / n, "同组一致" if group else "相邻一致", gr, 100 * gr / n))

    print("语料行（>= %s）：%d ｜ funnel: %s" % (args.min_year, len(rows), dict(funnel)))
    print()
    score("A 结构档", struct_scored, lambda x: x["his_rung"], lambda x: x["our_rung"], None,
          group=lambda x: abs(x["his_rung"] - x["our_rung"]) <= 1)
    score("B 操作档", op_scored, lambda x: x["his_op"], lambda x: x["our_op"], None,
          group=lambda x: OP_GROUP.get(x["his_op"]) == OP_GROUP.get(x["our_op"], "?"))
    print("\nA 混淆（他 → 我们）：")
    for (a, b), k in sorted(collections.Counter(
            (RUNG_NAME[x["his_rung"]], RUNG_NAME[x["our_rung"]]) for x in struct_scored).items(), key=lambda kv: -kv[1])[:10]:
        print("  %-8s → %-8s %3d%s" % (a, b, k, "" if a == b else "  ← 分歧"))
    print("\nB 混淆（他 → 我们）：")
    for (a, b), k in sorted(collections.Counter(
            (x["his_op"], x["our_op"]) for x in op_scored).items(), key=lambda kv: -kv[1])[:10]:
        print("  %-8s → %-8s %3d%s" % (a, b, k, "" if a == b else "  ← 分歧"))
    print("\nB 分歧样例（最近 8 条）：")
    for x in [y for y in op_scored if y["his_op"] != y["our_op"]][-8:]:
        print("  %s 他=%-8s 我们=%-8s | %s" % (x["dt"], x["his_op"], x["our_op"], x["text"][:60]))
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    json.dump({"struct": struct_scored, "op": op_scored},
              open(os.path.join(ROOT, "data", "_corpus_wave_golden.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n落盘 data/_corpus_wave_golden.json（struct %d / op %d）" % (len(struct_scored), len(op_scored)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
