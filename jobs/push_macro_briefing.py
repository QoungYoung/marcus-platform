# -*- coding: utf-8 -*-
"""push_macro_briefing.py — 把 DSH agent 写好的「宏观事件晨报」推送到 QQ。

分工（刻意分开 ✓）：
  · **分析**由 DSH agent 任务做（它会调 marcus-panel-tools 的只读数据工具 + 读持仓 ✓）⇒ 落盘
    data/macro_briefing_<YYYYMMDD>.md；
  · **投递**由本脚本做（平台侧，用现成的 QQ 通道 ✓）—— 因为 DSH 侧没有推送端点 ✗。
  · 本脚本**只读文件**：没有当日晨报 ⇒ 直接退出 0（fail-open ✓，节假日/agent 未跑都走这条路 ✓）。

去重：data/_macro_briefing_pushed.json 记已推日期 ⇒ 同一天重复跑不会重复推 ✓。
推送：走 core.qq_notifier.send_c2c_message（HTTP + access_token，不依赖 WS ✓；先例 jobs/gate_failure_report.py:110 ✓）。
用法：python jobs/push_macro_briefing.py [--date YYYYMMDD] [--dry]
"""
import argparse, json, os, time

DATA = os.environ.get("DATA_DIR", "data")
STATE = os.path.join(DATA, "_macro_briefing_pushed.json")
MAX_LEN = 1500          # QQ 单条上限保守值；超长截断并在末尾提示（全文仍在文件里 ✓）


def _read(path):
    try:
        return open(path, encoding="utf-8").read().strip()
    except Exception:
        return ""


def _push_qq(text: str) -> bool:
    try:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core.qq_notifier import send_c2c_message   # 导入即加载 .env ✓
        openid = (os.environ.get("QQ_BOT_RECIPIENT") or "").strip()
        if not openid:
            print("[macro-brief] 推送跳过：未配置 QQ_BOT_RECIPIENT")
            return False
        ok = bool(send_c2c_message(openid, text))
        print("[macro-brief] 推送%s" % ("成功 ✓" if ok else "失败 ✗"))
        return ok
    except Exception as e:
        print("[macro-brief] 推送异常: %s" % str(e)[:120])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--dry", action="store_true", help="只打印不推送 ✓")
    a = ap.parse_args()
    day = a.date or time.strftime("%Y%m%d")
    p = os.path.join(DATA, "macro_briefing_%s.md" % day)
    txt = _read(p)
    if not txt:
        print("[macro-brief] 无当日晨报 %s ⇒ 跳过（fail-open ✓；节假日或 agent 未跑都走这里）" % p)
        return 0
    st = {}
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    if st.get("last_pushed") == day and not a.dry:
        print("[macro-brief] %s 已推送过 ⇒ 跳过（去重 ✓）" % day)
        return 0
    body = txt if len(txt) <= MAX_LEN else txt[:MAX_LEN] + "\n…（已截断，全文见 %s）" % p
    if a.dry:
        print("[macro-brief][dry] 将推送 %d 字符：\n%s" % (len(body), body[:400]))
        return 0
    ok = _push_qq(body)
    if ok:
        st["last_pushed"] = day
        st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
