# -*- coding: utf-8 -*-
"""sync_wave_state_from_prod.py — 把**生产**的浪型产物同步到本地 `data/`（只读用 ✓）。

为什么需要：`data/` 在 `.gitignore:40` 里 ⇒ **每个环境各一份、从不互相同步** ✗
（生产 = 云端 `/opt/marcus-platform`；本地/工作区各有一份快照，日期可能落后很多 ✗）。
排查口径不一致时，先跑这个把三份浪型产物对齐 ✓。

只同步这三类，**别的一律不碰** ✓：
  ① data/wave_state.json                     ← live（当前档）
  ② data/wave_state_<YYYY-MM-DD>.json        ← 带日期回填件（daily_decision 回测下优先读它）
  ③ data/wave_state_history/<YYYYMMDD>.json  ← 回测取档（wave_gate.py:101 按模拟日读）

用法::
    python jobs/sync_wave_state_from_prod.py                    # dry-run：只看差异，不写盘
    python jobs/sync_wave_state_from_prod.py --apply            # 备份 + 写入
    python jobs/sync_wave_state_from_prod.py --apply --host marcusrv --remote /opt/marcus-platform

⚠️ 同步后本地 `wave_state.json` 的 date 可能**晚于**你本地的行情（例：生产 10-19 / 本地 10-15）
   ⇒ 那份档只适合"看" ✓；若要在本地跑 regen / 回测，请用本地数据重新生成 ✓
   （`python jobs/regen_wave_state_ladder.py --apply --history-days 90 --dated-days 90` 会把档改回与你数据一致 ✓）。
"""
import argparse
import glob
import io
import json
import os
import subprocess
import sys
import tarfile
import time

GLOBS = ["data/wave_state.json", "data/wave_state_[0-9]*.json", "data/wave_state_history"]


def _local_summary():
    out = {}
    for name in ("history", "dated"):
        if name == "history":
            fs = glob.glob(os.path.join("data", "wave_state_history", "*.json"))
        else:
            fs = glob.glob(os.path.join("data", "wave_state_[0-9]*.json"))
        out[name] = len(fs)
    try:
        d = json.load(open(os.path.join("data", "wave_state.json"), encoding="utf-8"))
        out["live"] = {k: d.get(k) for k in ("date", "session_date", "level", "sub_level", "operation")}
    except Exception:
        out["live"] = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写盘（默认 dry-run ✓）")
    ap.add_argument("--host", default="marcusrv", help="ssh 目标（默认 marcusrv ✓）")
    ap.add_argument("--remote", default="/opt/marcus-platform", help="远端仓库根（默认 /opt/marcus-platform ✓）")
    ap.add_argument("--prune-legacy", action="store_true",
                    help="同步后清掉 data/wave_state_history/ 里**旧 schema**（无 session_date）的残留 ✓（已备份）")
    args = ap.parse_args()

    before = _local_summary()
    print("本地（同步前）：", json.dumps(before, ensure_ascii=False))
    print("拉取远端快照：%s:%s …" % (args.host, args.remote))
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.host,
           "cd %s && tar czf - " % args.remote + " ".join(GLOBS)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        print("✗ 拉取失败（rc=%s）：%s" % (r.returncode, (r.stderr or b"")[:300].decode("utf-8", "ignore")))
        return 1
    buf = io.BytesIO(r.stdout)
    tf = tarfile.open(fileobj=buf, mode="r:gz")
    names = [n for n in tf.getnames() if n.endswith(".json")]
    print("远端快照：%d 个文件（%.1f MB）" % (len(names), len(r.stdout) / 1e6))

    # 远端 live 的关键字段（从 tar 里直接读 ✓，不落盘 ✓）
    remote_live = None
    try:
        m = tf.extractfile("data/wave_state.json")
        if m is not None:
            d = json.loads(m.read().decode("utf-8"))
            remote_live = {k: d.get(k) for k in ("date", "session_date", "level", "sub_level", "operation")}
    except Exception as e:
        print("（读远端 live 失败：%s）" % str(e)[:80])
    print("远端 live：", json.dumps(remote_live, ensure_ascii=False))

    if not args.apply:
        print("\n（dry-run ✓ 未写盘；加 --apply 才写）")
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join("data", "_waveagent_backup")
    os.makedirs(bdir, exist_ok=True)
    tarp = os.path.join(bdir, "wave_state_sync_%s.tar.gz" % ts)
    with tarfile.open(tarp, "w:gz") as out:
        for g in ("data/wave_state.json", "data/wave_state_[0-9]*.json", "data/wave_state_history/*.json"):
            for f in glob.glob(g):
                out.add(f, arcname=f)
    print("本地备份：%s" % tarp)

    n_w = 0
    for n in names:
        if not n.startswith("data/") or ".." in n:
            continue
        m = tf.extractfile(n)
        if m is None:
            continue
        os.makedirs(os.path.dirname(n) or ".", exist_ok=True)
        with open(n + ".tmp", "wb") as f:
            f.write(m.read())
        os.replace(n + ".tmp", n)
        n_w += 1
    print("写入完成：%d 个文件 ✓" % n_w)

    # ★ 可选：清掉 history/ 里**旧 schema** 的残留（没有 session_date 的就是旧 agent 时代写的 ✗）。
    #   为什么清：回测按模拟日取档（wave_gate.py:101）会读到它们 ✗ ⇒ 混用两代口径 ✗。
    #   安全：已在上面打进本地备份 tar.gz ✓；只动 data/wave_state_history/ ✓，不碰 dated ✓（
    #   生产上 dated 里也有 8 份窗口外老档，保持与生产一致 ✓）。
    if args.prune_legacy:
        killed = []
        for f in sorted(glob.glob(os.path.join("data", "wave_state_history", "*.json"))):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            if not d.get("session_date"):
                os.remove(f)
                killed.append(os.path.basename(f))
        print("清理旧 schema history：%d 份 %s" % (len(killed), killed[:6]))
    print("本地（同步后）：", json.dumps(_local_summary(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
