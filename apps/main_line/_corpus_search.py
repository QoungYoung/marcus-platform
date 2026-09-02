
import pandas as pd
xlsx = "/home/fengx/marcus-platform/狼大回复汇总 20260814-1457&往期.xlsx"
KWS = ["缩转放", "缩量转放", "做T", "正T", "反T", "T出", "分时", "黄线", "15分钟", "冲高", "无量", "放量反弹", "轨道", "大盘带"]
seen = set()
for sheet in pd.ExcelFile(xlsx).sheet_names:
    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
    except Exception:
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
