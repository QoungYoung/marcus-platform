# -*- coding: utf-8 -*-
"""bt_code_revs.py — 为全拟真回测准备**按日的代码版本树**（"与生产一模一样"的另一半）。

为什么必须做：生产代码在 2026 年一路在改（实测 09-11、09-13、09-14、09-15 都有影响买入链的提交，
例如 09-13 `gate 整块退场 / mainline_top_themes 改读方向层池`、09-15 `两道建仓门 + 均线挂单 + v3`）。
用"今天的代码"回放 1 月的日子，等于把后来才上线的机制提前装上去 → 结论必然错。
所以：**每个交易日用它当天在跑的代码版本**（= 该日之前最后一次提交），把版本树落盘，
回放时把该树的 `apps/main_line`、`jobs`、`backend`、`core`、`config` 放到 `sys.path` 最前。

产物：`<out>/rev_<rev>/…`（git archive 只含 tracked 文件 = 纯代码，不含 data/）+ `<out>/rev_map.json`。

用法（本地/服务器 git 仓库内）：
  python jobs/bt_code_revs.py --start 20260901 --end 20260915 --out data/_bt_code
  python jobs/bt_code_revs.py --start 20260105 --end 20260915 --out data/_bt_code --tar data/_bt_code.tgz
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import time
from datetime import date, timedelta

PATHS = ["apps", "jobs", "backend", "core", "config", "scripts"]


def rev_for(day: str, paths=PATHS) -> str:
    """该日结束时在跑的版本 = `day 23:59:59` 之前最后一次**触及这些目录**的提交。"""
    r = subprocess.run(["git", "rev-list", "-1", "--before=%sT23:59:59" % _dash(day), "HEAD", "--"] + paths,
                       capture_output=True, text=True)
    rev = (r.stdout or "").strip()
    if rev:
        return rev
    r = subprocess.run(["git", "rev-list", "-1", "--before=%sT23:59:59" % _dash(day), "HEAD"],
                       capture_output=True, text=True)
    return (r.stdout or "").strip()


def _dash(d8: str) -> str:
    return "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])


def weekdays(start: str, end: str):
    d = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    e = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    while d <= e:
        if d.weekday() < 5:
            yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


def materialize(rev: str, out: str, paths=PATHS) -> bool:
    dst = os.path.join(out, "rev_%s" % rev)
    if os.path.isdir(os.path.join(dst, "apps")):
        return True
    os.makedirs(dst, exist_ok=True)
    p1 = subprocess.Popen(["git", "archive", rev] + paths, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["tar", "-x", "-C", dst], stdin=p1.stdout)
    p1.stdout.close()
    rc = p2.wait()
    p1.wait()
    return rc == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="data/_bt_code")
    ap.add_argument("--tar", default="", help="把这些版本树打成一个 tar（便于一次性上传服务器）")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    mapping, t0, n_new = {}, time.time(), 0
    for d8 in weekdays(a.start, a.end):
        rev = rev_for(d8)
        if not rev:
            print("[revs] %s 无版本（早于首个提交？）" % d8); continue
        if not os.path.isdir(os.path.join(a.out, "rev_%s" % rev, "apps")):
            ok = materialize(rev, a.out)
            n_new += 1 if ok else 0
            if not ok:
                print("[revs] %s 展开失败 rev=%s" % (d8, rev)); continue
        try:
            subj = subprocess.run(["git", "log", "-1", "--format=%h %ad %s", "--date=short", rev],
                                  capture_output=True, text=True).stdout.strip()
        except Exception:
            subj = rev
        mapping[d8] = {"rev": rev, "subject": subj}
        print("[revs] %s → %s | %s" % (d8, rev, subj[:90]))
    with open(os.path.join(a.out, "rev_map.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=1)
    print("[revs] 共 %d 天 → %d 个不同版本（新建 %d 棵树）%.1fs"
          % (len(mapping), len({v["rev"] for v in mapping.values()}), n_new, time.time() - t0))
    if a.tar:
        with tarfile.open(a.tar, "w:gz") as tf:
            tf.add(a.out, arcname=os.path.basename(a.out))
        print("[revs] 打包 %s（%.1f MB）" % (a.tar, os.path.getsize(a.tar) / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
