
import pandas as pd


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

xlsx = "/home/fengx/marcus-platform/狼大回复汇总 20260814-1457&往期.xlsx"
KWS = ["缩转放", "缩量转放", "做T", "正T", "反T", "T出", "分时", "黄线", "15分钟", "冲高", "无量", "放量反弹", "轨道", "大盘带"]
seen = set()
for sheet in pd.ExcelFile(xlsx).sheet_names:
    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
    except Exception as _e_sil1:
        _silent_alert("_corpus_search.py:10", _e_sil1)
        continue
    for _, row in df.iterrows():
        t = str(row.get("发帖时间", ""))
        c = str(row.get("回复内容", ""))
        if len(c) < 8: continue
        for kw in KWS:
            if kw in c:
                key = c[:60]
                if key in seen: break
                seen.add(key)
                print(f"[{sheet}|{t}] {c[:220]}")
                print("---")
                break
