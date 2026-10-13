# -*- coding: utf-8 -*-
"""隧道看门狗（§9.810）：本地两条隧道（13001 LLM / 15432 PG）会**半死** —— 端口还在听 ✓
但转发必 reset ✗（今天实测 4 次 ✗）⇒ 回测被迫走超时/降级 ✗。
本脚本每 60s 探测一次：LLM 用真实 POST /chat（短报文 ✓）；PG 用 TCP connect ✓；
探测失败 ⇒ 杀掉旧转发进程（**按 /proc 扫描，排除自己/父进程**，绝不 pkill -f 自伤 ✗）后重启 ✓。
日志：.dsh-tmp/wolfbt/logs/tunnel_watchdog.log
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(REPO, ".dsh-tmp", "wolfbt")
LOGDIR = os.path.join(RUN, "logs")
PY = sys.executable
JOBS = [("llm", 13001, "local_llm.py"), ("pg", 15432, "local_pg_serve.py")]


def log(msg):
    os.makedirs(LOGDIR, exist_ok=True)
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(os.path.join(LOGDIR, "tunnel_watchdog.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def probe_llm(port=13001, timeout=25):
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/chat" % port,
                                     data=json.dumps({"message": "ping"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(r.read())
    except Exception as e:
        return "ERR:" + type(e).__name__


def probe_pg(port=15432, timeout=3):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception as e:
        return "ERR:" + type(e).__name__


def _kill_script(name):
    me, pp = os.getpid(), os.getppid()
    n = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        p = int(d)
        if p in (me, pp):
            continue
        try:
            c = open("/proc/%s/cmdline" % d, "rb").read().decode("utf-8", "replace").replace("\x00", " ")
        except Exception:
            continue
        if name in c and "watchdog" not in c:
            try:
                os.kill(p, signal.SIGTERM)
                n += 1
            except Exception:
                pass
    return n


def restart(name):
    _kill_script(name)
    time.sleep(2)
    path = os.path.join(RUN, name)
    if not os.path.exists(path):
        log("  ✗ 缺脚本 %s" % path)
        return False
    with open(os.path.join("/tmp", name + ".log"), "ab") as fh:
        subprocess.Popen([PY, path], cwd=REPO, stdout=fh, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    log("  ⇒ 已重启 %s" % name)
    return True


def main():
    log("隧道看门狗启动（每 60s 探测：LLM=真实 /chat ✓，PG=TCP ✓）")
    fails = 0
    while True:
        r = probe_llm()
        if r is not True:
            fails += 1
            log("✗ LLM 隧道探测失败（%s）⇒ 重启" % r)
            restart("local_llm.py")
            time.sleep(10)
            log("  复测：%s" % probe_llm())
        else:
            if fails:
                log("✓ LLM 隧道恢复")
            fails = 0
        if probe_pg() is not True:
            log("✗ PG 隧道探测失败 ⇒ 重启")
            restart("local_pg_serve.py")
        time.sleep(60)


if __name__ == "__main__":
    main()
