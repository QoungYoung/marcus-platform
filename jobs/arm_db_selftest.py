# -*- coding: utf-8 -*-
"""arm_db_selftest.py —— **臂隔离自检**（账本 §9.540 ✓，用户问：能确保不会访问到其他臂吗 ✓）

输出每条臂的：解析出的臂根 / 账户名 / 库文件路径，并断言：
  ① 各臂库路径**互不相同**（不串臂）
  ② 每条库路径**都在自己的臂根内**（不越界）
  ③ 账户名非空（不会是"默认 drabt35"✗）
用法：`.venv/bin/python jobs/arm_db_selftest.py`
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_db as adb  # noqa: E402

# (臂名, 臂根, 账户) —— 与各 launcher 的实际配置对应
ARMS = [
    ("t16", "/home/fengx/marcus-platform/data/_bt_t16", "drabt16"),
    ("t35", "/home/fengx/marcus-platform/data/_bt_t35", "drabt35"),
    ("t17", "/home/fengx/marcus-platform/data/_bt_t17", "drabt17"),
]


def main() -> int:
    rows, ok = [], True
    for name, root, acc in ARMS:
        try:
            p = adb.db_path(acc, root)
            got = adb.resolve_account(acc, root)
        except Exception as e:
            p, got = "", "ERR:%s" % str(e)[:40]
        inside = bool(p) and os.path.realpath(p).startswith(os.path.realpath(root) + os.sep)
        rows.append((name, root, got, p, inside))
    seen = {}
    for name, root, acc, p, inside in rows:
        dup = seen.get(p)
        mark = "OK" if (p and inside and not dup and "ERR" not in acc) else "FAIL"
        if mark == "FAIL":
            ok = False
        seen[p] = name
        print("  [%-4s] 臂根=%-46s 账户=%-9s 库=%s%s"
              % (mark, root, acc, os.path.basename(p) or "(空)", "  << 与 %s 重复" % dup if dup else ""))
    # 反向用例：未指定账户且臂根下无唯一库 ⇒ 必须**拒绝**（而不是默认 drabt35 ✗）
    import tempfile
    d = tempfile.mkdtemp(prefix="armiso.")
    try:
        adb.db_path("", d)
        print("  [FAIL] 空账户+空臂根竟然允许写入 ✗（会跨臂）")
        ok = False
    except Exception as e:
        print("  [OK  ] 空账户 ⇒ **正确拒绝** ✓（%s）" % str(e)[:60])
    print("  ⇒ 隔离自检: %s" % ("**全部通过 ✓**" if ok else "**有失败 ✗**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
