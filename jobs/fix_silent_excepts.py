# -*- coding: utf-8 -*-
"""fix_silent_excepts.py —— 把 `except …: pass` 改成**至少留痕**（账本 §9.543，用户「避免吞异常」）

**两级**：
  · **关键文件**（监控/网关/止损/仓位）⇒ `alert_hub.note_silent(...)`（落盘 alerts.jsonl ＋ 去重限流的 QQ）
  · **其余** ⇒ `print("[silent:<file>:<line>] …")`（无依赖、绝不抛）
**保守**：只改 `except …:` 后**直接是 `pass`** 的（不含 `continue`／不含已有语句的）✓；每文件改完 `compile()` ✓
用法：`.venv/bin/python jobs/fix_silent_excepts.py [--apply] [--files a.py,b.py]`
"""
from __future__ import annotations
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRITICAL = ("t_monitor.py", "t_gateway.py", "stop_loss_monitor.py", "position_tier_monitor.py",
            "position_tier.py", "wolf_discipline.py", "t_base_floor.py", "tranche_ladder.py")
# 匹配：except <...>:\n<indent>pass   （pass 后允许行尾注释）
PAT = re.compile(r"^(?P<ind>[ \t]+)except(?P<sig>[^\n:]*):[ \t]*(?P<cmt>#[^\n]*)?\n(?P=ind)[ \t]+pass[ \t]*(?P<cmt2>#[^\n]*)?$", re.M)


def convert(path: str, apply: bool, counter: list) -> int:
    s = open(path, encoding="utf-8").read()
    n = len(PAT.findall(s))
    if not n:
        return 0
    name = os.path.basename(path)
    critical = name in CRITICAL
    i = [0]

    def repl(m):
        i[0] += 1
        ind = m.group("ind")
        var = "_e_sil%d" % i[0]
        sig = m.group("sig") or ""
        tag = "%s:%d" % (name, len(s[:m.start()].split("\n")))
        if critical:
            body = ('%s    try:\n%s        from app.services import alert_hub as _ah_sil\n'
                    '%s        _ah_sil.note_silent("%s", %s)\n'
                    '%s    except Exception:\n'
                    '%s        print("[silent:%s] %%s: %%s" %% (type(%s).__name__, str(%s)[:110]), flush=True)'
                    % (ind, ind, ind, tag, var, ind, ind, tag, var, var))
        else:
            body = ('%s    print("[silent:%s] %%s: %%s" %% (type(%s).__name__, str(%s)[:110]), flush=True)'
                    % (ind, tag, var, var))
        return "%sexcept%s as %s:%s\n%s" % (ind, sig, var, m.group("cmt") or "", body)

    s2 = PAT.sub(repl, s)
    if apply:
        open(path, "w", encoding="utf-8").write(s2)
        compile(s2, path, "exec")
    counter[0] += i[0]
    return i[0]


def main() -> int:
    apply = "--apply" in sys.argv
    files = []
    for i, a in enumerate(sys.argv):
        if a == "--files" and i + 1 < len(sys.argv):
            files = [f.strip() for f in sys.argv[i + 1].split(",")]
    if not files:
        base = os.path.join(ROOT, "backend", "app", "services")
        files = [os.path.join(base, f) for f in CRITICAL if os.path.exists(os.path.join(base, f))]
        files.append(os.path.join(ROOT, "jobs", "bt_prod_run.py"))
    tot = [0]
    print("  ── 静默点改造（%s）──" % ("**落盘/留痕（apply ✓）**" if apply else "演练（dry-run）"))
    for p in files:
        if not os.path.exists(p):
            print("    %-46s 不存在 ✗" % os.path.basename(p)); continue
        try:
            n = convert(p, apply, tot)
        except Exception as e:
            print("    %-46s 失败: %s" % (os.path.basename(p), str(e)[:60])); continue
        if n:
            print("    %-46s 改 %3d 处 ✓（%s）" % (os.path.basename(p), n,
                  "note_silent" if os.path.basename(p) in CRITICAL else "print"))
    print("  ⇒ 共 %d 处 ✓" % tot[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
