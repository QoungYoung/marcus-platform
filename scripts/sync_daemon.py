"""资金流本地同步守护进程 — 交易日 9-11,13-14 时的 17,28,33,48 分执行"""
import subprocess
import time
from datetime import datetime

SCRIPT = __file__.replace("sync_daemon.py", "sync_em_to_pg.py")
RUN_MINUTES = {17, 28, 33, 48}
last_run = ""

print(f"[daemon] 启动，脚本: {SCRIPT}")
print(f"[daemon] 执行时间: 交易日 {sorted(RUN_MINUTES)}分")

while True:
    now = datetime.now()
    wd = now.weekday()  # 0=Mon ... 4=Fri
    h, m = now.hour, now.minute

    should_run = (
        wd <= 4
        and m in RUN_MINUTES
        and ((9 <= h <= 11) or (13 <= h <= 14))
        and now.strftime("%H:%M") != last_run  # 防重复
    )

    if should_run:
        last_run = now.strftime("%H:%M")
        print(f"\n[daemon] {now.strftime('%H:%M:%S')} 执行中...")
        try:
            subprocess.run(["python", SCRIPT], check=True, timeout=120)
        except subprocess.TimeoutExpired:
            print("[daemon] 超时 (>120s)")
        except Exception as e:
            print(f"[daemon] 失败: {e}")

    time.sleep(30)  # 每 30 秒检查一次
