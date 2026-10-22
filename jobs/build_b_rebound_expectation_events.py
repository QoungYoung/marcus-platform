# -*- coding: utf-8 -*-
"""从根目录 XLS 抽取 B反 预期日（按提及聚类，取首日）。"""
from __future__ import annotations
import os,re,json
import openpyxl,pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLS=os.path.join(ROOT,'狼大回复汇总 20260814-1457&往期.xlsx')
OUT=os.path.join(ROOT,'data/core_pit')

def clean(s):
 s=re.sub(r'\[/?[^\]]+\]','',str(s))
 s=re.sub(r'\s+',' ',s)
 return s.strip()

def main():
 wb=openpyxl.load_workbook(XLS,read_only=True,data_only=True)
 rows=[]
 for ws in wb.worksheets:
  if ws.title not in ('2025','2026','小时代狼大楼（历史存档）'): continue
  for r in ws.iter_rows(min_row=2,values_only=True):
   t=str(r[0] or '').strip(); body=str(r[1] or '')
   if 'B反' not in body and 'B 反' not in body: continue
   # 只保留狼大本人发言：以 -阿狼- 或包含操作/判断口吻；历史存档中混杂网友引用
   if ws.title=='小时代狼大楼（历史存档）' and not any(k in body for k in ['我的观点没变化','B反高点','B反是一次性','我不是给你们B反目标价']):
    continue
   rows.append({'time':t,'date':t[:10],'sheet':ws.title,'text':clean(body)[:500]})
 df=pd.DataFrame(rows).sort_values('time').reset_index(drop=True)
 # cluster gap > 20 days -> 预期日取 cluster 首条
 out=[]; last=None
 for _,r in df.iterrows():
  d=pd.to_datetime(r.date,errors='coerce')
  if pd.isna(d): continue
  if last is None or (d-last).days>20:
   out.append({'expect_date':r.date,'first_time':r.time,'sheet':r.sheet,'text':r.text,'n_mentions':1})
   last=d
  else:
   out[-1]['n_mentions']+=1
 x=pd.DataFrame(out)
 x.to_csv(os.path.join(OUT,'b_rebound_expectation_events.csv'),index=False,encoding='utf-8-sig')
 print(x[['expect_date','first_time','sheet','n_mentions','text']].to_string(index=False))
 print('events',len(x))
if __name__=='__main__': main()
