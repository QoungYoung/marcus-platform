
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
print("===== 所有含『缩转放』的原话 =====")
for sheet in pd.ExcelFile(xlsx).sheet_names:
    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
    except Exception as _e_sil1:
        _silent_alert("_corpus_shrink.py:9", _e_sil1)
        continue
    for _, row in df.iterrows():
        t = str(row.get("发帖时间", ""))
        c = str(row.get("回复内容", ""))
        if "缩转放" in c:
            print(f"[{sheet}|{t}] {c[:300]}")
            print("---")
