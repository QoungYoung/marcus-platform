#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从**生产容器实际运行环境**生成回测环境文件（保证"与生产一致"，2026-09-17 用户拍板）。

为什么取容器 env 而不是生产 .env：compose 的 `environment:` 会覆盖 `env_file`，
例如 DEEPSEEK_MODEL 在 .env 是 deepseek-flash，容器里实际是 deepseek-chat。
容器 env 才是**生产真正跑的值**。

过滤规则（宁可少镜像，也不把密钥/内网地址带进回测）：
  ✗ 凭据类：KEY/TOKEN/SECRET/PASSWORD/COOKIE/SESSION/CREDENTIAL/AUTH/SIGN
  ✗ 地址类：*_URL/_HOST/_PORT/DSN/URI/DATABASE/POSTGRES/REDIS/PROXY
  ✗ 容器/进程类：PATH/HOME/HOSTNAME/PWD/TERM/LANG/PYTHON*/NODE*/GUNICORN*/SUPERVISOR*/_=
  ✓ 其余全部（开关、模式、数值、路径模板）
末尾追加**本地覆盖块**（数据库→本地 5433、LLM 出口→本地隔离 dsh、模型名对齐生产容器）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.dsh-tmp', 'wolfbt'))
import prod  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.dsh-tmp', 'wolfbt', 'bt_env_prod.env')
SECRET = re.compile(r'(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD$|COOKIE|SESSION|CREDENTIAL|AUTH|SIGN)', re.I)
ADDR = re.compile(r'(_URL|_HOST|_PORT|DSN|URI|DATABASE|POSTGRES|REDIS|PROXY|ENDPOINT)', re.I)
PROC = re.compile(r'^(PATH|HOME|HOSTNAME|PWD|OLDPWD|TERM|LANG|LC_|SHLVL|_|PYTHON|NODE|GUNICORN|SUPERVISOR|TZ|HOST_|container|MARCUS_WORKSPACE|DATA_DIR|PI_SERVER_URL)', re.I)

LOCAL_OVERRIDES = [
    ("DATABASE_URL", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"),
    ("BT_AGENT_CHAT_URL", "http://127.0.0.1:13101/chat"),   # 本地硬隔离 dsh
    ("MARCUS_API_URL", "http://127.0.0.1:13200/api/v1"),    # 本地 as-of API（dsh 工具通道）
    ("NO_PROXY", "127.0.0.1,localhost"), ("no_proxy", "127.0.0.1,localhost"),
    ("TZ", "Asia/Shanghai"),
    # ── 纯性能开关：**显式**在回测侧打开（代码默认一律关 → 生产永不受影响）──
    # 为什么写在这里而不是靠代码默认：2026-09-17 事故——为省 DB 往返加的缓存默认写成 ON，
    # 又被整体部署带进了生产（`WOLF_PRESCREEN_CACHE` 实测已在生产容器内），违背"只影响回测"。
    # 口径：生产 = 关（或不认识）；回测 = 这里显式开，开关值随运行记录可审计。
    ("WOLF_STOP_COND_CACHE", "1"),      # 止损扫描条件列表按 (标的,账户,bar) 缓存（0112 实测 150.5s → ~0）
    ("WOLF_PRESCREEN_CACHE", "1"),      # 预筛账本/笔数按 bar 缓存
    # 止损扫描"每标的每 bar 只算一次"：20260202 实测 stop_loss 492.4s / _round 675.4s（1589 次调用×310ms）
    ("WOLF_STOP_SCAN_ONCE_PER_BAR", "1"),
    # ── as-of 外部取数网关：**必须显式开**（代码默认 0 = 不接入，会静默退回被断网挡住的旧路径）──
    # 代价实测（2026-09-17 事故②）：漏掉这一项 → `_fetch_daily_tencent_dated` 兜底打在断网守卫上
    #   （一天 300+ 条"腾讯日线兜底失败"），**持仓标的拿不到日线** ⇒ `_check_fib_target` /
    #   `_check_passive_stop` 等日线依赖的判定整段变哑（耗时掉出 top10 即"没跑"）。
    # 为什么写死在生成器里而不是靠手工 export：年跑 gen=4 靠外层 shell 的 export 才带上，
    #   重启时 shell 被重置 → gen=5 静默退回 BT_ASOF_FETCH=0，白跑 5 天。口径开关**必须随环境文件固化**。
    ("BT_ASOF_FETCH", "1"),
]


def main() -> int:
    rc, out, err = prod.run("docker exec marcus-worker env", quiet=True)
    if rc != 0:
        print("取生产容器 env 失败：%s" % (err or '')[:200], file=sys.stderr)
        return 2
    keep, drop_secret, drop_addr, drop_proc = [], [], [], []
    for line in (out or '').splitlines():
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip()
        if SECRET.search(k):
            drop_secret.append(k)
        elif ADDR.search(k):
            drop_addr.append(k)
        elif PROC.match(k):
            drop_proc.append(k)
        else:
            keep.append((k, v))
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("# 由 jobs/bt_env_from_prod.py 生成：镜像生产容器(marcus-worker)实际运行环境\n")
        f.write("# 生成时间：%s\n" % __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        for k, v in sorted(keep):
            f.write("export %s=%s\n" % (k, "'" + v.replace("'", "'\\''") + "'"))
        f.write("\n# ── 本地覆盖（必须覆盖生产的容器内地址；模型名对齐生产容器实际值）──\n")
        for k, v in LOCAL_OVERRIDES:
            f.write("export %s=%s\n" % (k, "'" + v + "'"))
    print("镜像条目 %d 条 → %s" % (len(keep), os.path.relpath(OUT)))
    print("丢弃：密钥类 %d（%s）/ 地址类 %d（%s）/ 进程类 %d" % (
        len(drop_secret), ",".join(sorted(drop_secret)[:4]),
        len(drop_addr), ",".join(sorted(drop_addr)[:6]), len(drop_proc)))
    keys = dict(keep)
    for probe in ("T_BUY_TIER_LIMIT_ENABLED", "WOLF_GAP_CAUTION", "WOLF_TIER_STATE_FIX",
                  "WOLF_TRADE_WINDOW", "ENTRY_L2_DISABLED", "P3_TIER_MODE", "STOP_LOSS_DYNAMIC_ONLY",
                  "SWITCH_EXEC_ENABLED", "WOLF_WEEKEND_HEDGE", "WOLF_PICK_RANK_V3",
                  "DEEPSEEK_MODEL", "DEEPSEEK_TRADE_MODEL", "T_MONITOR_ACCOUNT"):
        print("  %-28s %s" % (probe, keys.get(probe, '(未在容器 env 中)')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
