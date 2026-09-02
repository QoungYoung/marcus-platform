
import pandas as pd
xlsx = "/home/fengx/marcus-platform/狼大回复汇总 20260814-1457&往期.xlsx"
print("===== 所有含『缩转放』的原话 =====")
for sheet in pd.ExcelFile(xlsx).sheet_names:
    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
    except Exception:
        continue
    for _, row in df.iterrows():
        t = str(row.get("发帖时间", ""))
        c = str(row.get("回复内容", ""))
        if "缩转放" in c:
            print(f"[{sheet}|{t}] {c[:300]}")
            print("---")
