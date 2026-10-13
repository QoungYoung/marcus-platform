# -*- coding: utf-8 -*-
"""113 条长尾待核：把每条 reason 里的**引用**拿去语料里核对（§9.818）

方法（承接 §9.817 的教训 ✓：**必须读 reason 自带的引用**，不能靠名称猜 ✗）：
 ① 从两臂 prod_*.json 取出"未归类的拦截"（长尾 ✓）
 ② 从 reason 里抽取引用：`狼大 YYYY-MM-DD「…」` / `YYYY-MM-DD「…」` / `语料…`
 ③ 把引用的**最长独特片段**（去标点后取前 12 字 ✓）到 `docs/*.md` 里 grep ⇒ **命中/未命中** ✓
 ④ 无引用的 ⇒ 抽关键词做弱检索 ✓（标注"弱证据 ✗"）
输出：docs/longtail-provenance.json（逐条 ✓）+ 控制台摘要 ✓
"""
import glob
import json
import os
import re
from collections import Counter, defaultdict

DOCS = glob.glob("docs/*.md")
KNOWN = ("无弹药", "无底仓", "无券", "无可卖", "需可卖底仓", "可为 0", "可卖 0", "裸空", "as-of",
         "浪型", "COND-TIMING", "做T时段窗", "G8", "涨停", "埋伏腿同日去重", "埋伏同时持仓",
         "本轮已", "买入1笔", "买入 1 笔", "加仓口径", "底仓floor", "floor", "收敛", "桥不可达")
CITE = re.compile(r"(狼大\s*)?(\d{4}-\d{2}-\d{2})\s*[「『]([^」』]{4,80})[」』]")
CITE2 = re.compile(r"[「『]([^」』]{6,80})[」』]")


def _docs_text():
    txt = []
    for p in DOCS:
        try:
            txt.append((p, open(p, encoding="utf-8", errors="replace").read()))
        except Exception:
            pass
    return txt


def _hit(frag, texts):
    key = re.sub(r"[\s，。、；：（）(),.·…—\-]", "", frag)[:12]
    if len(key) < 5:
        return None
    for p, t in texts:
        if key in re.sub(r"[\s，。、；：（）(),.·…—\-]", "", t):
            return p
    return None


def _code_cite(fragment):
    """在代码里找发出该 reason 的行，并检查 ±40 行内是否有语料引用 ✓（承接 §9.817 教训 ✓）"""
    import subprocess as _sp
    key = fragment.strip()[:14]
    if len(key) < 5:
        return None, None
    try:
        out = _sp.run(["grep", "-rn", "--include=*.py", key, "backend", "apps"],
                      capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None, None
    for ln in out.split("\n")[:3]:
        if ":" not in ln:
            continue
        fp = ln.split(":")[0]
        try:
            num = int(ln.split(":")[1])
        except Exception:
            continue
        try:
            lines = open(fp, encoding="utf-8").read().split("\n")
        except Exception:
            continue
        win = "\n".join(lines[max(0, num - 40): num + 40])
        m = re.search(r"(狼大[^\n]{0,80}|20\d\d-\d\d-\d\d[^\n]{0,60}|wolf-[a-z-]+\.md:\d+|playbook[^\n]{0,60}|blueprint[^\n]{0,60})", win)
        return fp, (m.group(0)[:70] if m else None)
    return None, None


def main():
    reasons = {}
    for root in ("data/_bt_t41", "data/_bt_c2610122052"):
        for fp in glob.glob(root + "/_summary/prod_*.json"):
            try:
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            for t in (d.get("triggers") or []):
                if str(t.get("status")) not in ("blocked", "cancelled"):
                    continue
                r = str(t.get("reason") or "")
                if any(k in r for k in KNOWN):
                    continue
                fam = re.sub(r"[A-Z]{2}\d{6}|\d+\.?\d*", "X", r)[:120]
                reasons.setdefault(fam, {"n": 0, "raw": r})
                reasons[fam]["n"] += 1
    texts = _docs_text()
    rows = []
    for fam, v in sorted(reasons.items(), key=lambda x: -x[1]["n"]):
        raw = v["raw"]
        cites = CITE.findall(raw) or [(None, None, m.group(1)) for m in CITE2.finditer(raw)]
        verified, missed = [], []
        for c in cites[:3]:
            frag = c[2]
            p = _hit(frag, texts)
            (verified if p else missed).append({"quote": frag[:60], "doc": p})
        verdict = ("有据 ✓（引用已核对命中）" if verified and not missed else
                   "引用未在语料命中 ✗（可疑）" if missed and not verified else
                   "部分命中 🔶" if verified and missed else
                   "无引用 ✗（待人工）")
        code_file, code_cite = (None, None)
        if verdict.startswith("无引用"):
            # 用 reason 里最独特的片段去代码里找发出点 ✓
            # ★ 必须用**未脱敏**的片段去 grep（首版传的是带 X 的脱敏串 ⇒ 全找不到 ✗）
            _cands = [x.strip() for x in raw.split("|")]
            frag = ""
            for c in _cands:
                c2 = re.sub(r"[:：]\s*[\d.]+.*$", "", c).strip()
                if len(c2) >= 6:
                    frag = c2
                    break
            code_file, code_cite = _code_cite(frag or raw[:16])
            if code_cite:
                verdict = "有据 ✓（代码处引用：%s）" % code_cite[:34]
            elif code_file:
                verdict = "无引用 ·代码在 %s ✗（待人工）" % os.path.basename(code_file)
        rows.append({"fam": fam, "n": v["n"], "raw": raw[:220], "cites": len(cites),
                     "verified": verified, "missed": missed, "verdict": verdict,
                     "code_file": code_file, "code_cite": code_cite})
    json.dump(rows, open("docs/longtail-provenance.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    tot = sum(r["n"] for r in rows)
    c = Counter(r["verdict"] for r in rows)
    print("长尾条目 = %d 条（%d 种说法 ✓）" % (tot, len(rows)))
    print("判定分布:", dict(c))
    print("\n%-30s %4s %-26s %s" % ("说法（截断）", "条数", "判定", "引用核对"))
    for r in rows[:34]:
        ev = ("命中: " + os.path.basename(r["verified"][0]["doc"])) if r["verified"] else \
             ("未命中: " + r["missed"][0]["quote"][:24]) if r["missed"] else "-"
        print("%-30s %4d %-26s %s" % (r["fam"][:30], r["n"], r["verdict"], ev[:60]))
    print("\n明细 ⇒ docs/longtail-provenance.json")


if __name__ == "__main__":
    main()
