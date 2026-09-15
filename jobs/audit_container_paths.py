# -*- coding: utf-8 -*-
r"""audit_container_paths.py — 找出「按仓库布局推导、但在容器里解析错」的路径（2026-09-15 round 28/29）。

背景（round 27 的教训）：`position_tier_monitor.TIER_STATE_FILE = Path(__file__).parent.parent.parent.parent/"data"/…`
在仓库里上四级 = `<repo>` ✓，但**容器里 `backend/app` 挂在 `/app/app`** → 上四级 = `/` →
写成 `/data/…`（容器无此目录），异常又被 debug 日志吞掉 → **静默不持久化**。
本脚本在**容器内**跑（本地跑没意义：仓库布局本来就对），把所有 `Path(__file__)` 往上爬的表达式解析出来，
与实际落点对照，列出**解析结果在生产不存在**的那些（=同类隐患）。

识别方式（够用为准，不做完整 AST 求值）：
  ⚠️ 正则里 `\.parents\[\d+\]` **必须排在 `(?:\.parent)+` 前面**：否则 `.parents[3]` 的
     `.parent` 前缀会先被吃掉，整条表达式退化成"上溯 1 级"而被跳过（round 29 实测漏报
     `direction_prediction.py:813`）。
  · 正则找出 `Path(__file__)` 后跟 `k` 个 `.parent`（或 `parents[k]`）再跟若干 `"字面量"` 链；
  · 用 `Path(该文件).parents[k] / 字面量...` 复算（容器内的真实路径就是模块位置，故直接可算）；
  · 报告三类：① 解析结果不存在；② **上溯越界**（模块层级不足，`parents[k]` 越界 → 落点必为 `/`）；
    ③ 落点本身就是 `/`（即便存在也算可疑：说明按仓库布局算出来的是根目录）。
    ②③ 是 round 29 补的：旧版遇到越界会 `IndexError → continue` **静默漏报**，
    且「落点 = `/`」因为 `/` 存在而被当作正常 —— 实测漏掉了 4 处（trade_graph / api.scan / market / backtest_stop_loss）。

用法（**容器内**）::

    docker exec marcus-worker python -u /app/jobs/audit_container_paths.py
"""
import argparse
import os
import re
import sys
from pathlib import Path

ROOT = os.environ.get("AUDIT_ROOT", "/app")
EXPR = re.compile(r"Path\(__file__\)(?:\.(?:resolve|absolute)\(\))?(\.parents\[\d+\]|(?:\.parent)+)((?:\s*/\s*[\"'][^\"']+[\"'])*)")
SUFFIX = re.compile(r"[\"']([^\"']+)[\"']")
SKIP_DIRS = ("__pycache__", ".git", "node_modules", "site-packages")


def parents_count(chain: str) -> int:
    """把两种写法统一成"上溯级数 n"，使 `p.parents[n-1]` 恒等于表达式落点。

    · `.parent` × k  → n = k      （`.parent.parent` = parents[1]）
    · `.parents[k]`  → n = k + 1  （`parents[0]` 是直接父目录，不是"上溯 0 级"）
    ⚠️ round 29 修：旧版对 `parents[k]` 也返回 k → **少算一级**，实测漏报
    `direction_prediction.py:813`（`.resolve().parents[3] / "data" / "backtest"`：
    真相是 `/data/backtest`，旧版算成 `/app/data/backtest`）。
    """
    if chain.startswith(".parents["):
        return int(re.search(r"\[(\d+)\]", chain).group(1)) + 1
    return chain.count(".parent")


def resolve(p: Path, n: int, sufs):
    """复算 `Path(__file__)` 上溯 n 级再拼字面量的落点。

    返回 (base, target, overflow)：
      · overflow=True 表示 `parents[n-1]` 越界（容器里层级不够）→ base 取根目录；
      · 越界不是"算不出来"，而是**必然落到 `/`**，正是最危险的一类，必须报出来。
    """
    parents = list(p.parents)
    if n - 1 < len(parents):
        base, overflow = parents[n - 1], False
    else:
        base, overflow = Path(p.anchor or "/"), True
    target = base
    for s in sufs:
        target = target / s
    return base, target, overflow


def scan(root):
    """扫 root 下所有 .py，返回 (检查条数, 问题行列表)。纯函数，便于单测。"""
    rows, checked = [], 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            p = Path(dirpath) / f
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in EXPR.finditer(src):
                n = parents_count(m.group(1))
                sufs = SUFFIX.findall(m.group(2) or "")
                if not sufs and n < 3:
                    continue
                base, target, overflow = resolve(p, n, sufs)
                checked += 1
                exists = target.exists()
                base_is_root = str(base) == "/"
                if exists and not base_is_root and not overflow:
                    continue
                if overflow:
                    why = "上溯越界（层级不足）→ 落点 /"
                elif base_is_root:
                    why = "落点 = / （按仓库布局应上溯到仓库根）"
                else:
                    why = "解析结果不存在"
                near = sorted(x.name for x in base.iterdir())[:12] if base.exists() else []
                rows.append({
                    "file": str(p.relative_to(root)), "line": src[:m.start()].count("\n") + 1,
                    "expr": (m.group(1) + m.group(2)).strip()[:90],
                    "target": str(target), "base": str(base), "near_base": near,
                    "why": why, "overflow": overflow, "exists": exists,
                })
    return checked, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    checked, rows = scan(args.root)
    missing = [r for r in rows if not r["exists"]]
    print("[paths] 扫描 %s：检查了 %d 个 Path(__file__) 派生路径，**问题 %d 个**（其中解析结果不存在 %d 个）"
          % (args.root, checked, len(rows), len(missing)))
    print("\n%-52s %-5s %-22s %s" % ("文件", "行", "问题", "解析出的路径"))
    for r in rows[:args.limit]:
        print("%-52s %-5d %-22s %s" % (r["file"], r["line"], r["why"], r["target"]))
        if r["near_base"]:
            print("      ↳ 上级 %s 下实际有: %s" % (r["base"], ", ".join(r["near_base"][:8])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
