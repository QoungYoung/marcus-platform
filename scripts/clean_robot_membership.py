#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理 robot_actuator / humanoid_robot 的明显非核心成员。

只删“明显错误”的宽泛概念污染；汽车配件/轴承等配套段也先移除，
后续如需保留配套证据，放 support 表，不再混进 source membership。
"""
from __future__ import annotations
import argparse, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(ROOT,'backend'))
REMOVE={
 '000026.SZ':'飞亚达/服饰',
 '002708.SZ':'光洋股份/汽车配件轴承',
 '300825.SZ':'阿尔特/汽车服务',
 '000967.SZ':'盈峰环境/环保',
 '002236.SZ':'大华股份/IT设备',
 '002230.SZ':'科大讯飞/软件服务',
 '002380.SZ':'科远智慧/软件服务',
 '002139.SZ':'拓邦股份/元器件',
 '002031.SZ':'巨轮智能/汽车配件',
}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');a=ap.parse_args()
 import psycopg2
 c=psycopg2.connect(os.environ['DATABASE_URL'],connect_timeout=5);cur=c.cursor()
 cur.execute("select theme_id,symbol,as_of_date from theme_membership where theme_id in ('robot_actuator','humanoid_robot') and symbol=any(%s)",(list(REMOVE),))
 rows=cur.fetchall();print('matched',len(rows))
 for r in rows: print(r,REMOVE.get(r[1],''))
 if a.apply and rows:
  cur.execute("delete from theme_membership where theme_id in ('robot_actuator','humanoid_robot') and symbol=any(%s)",(list(REMOVE),));c.commit();print('deleted',cur.rowcount)
 else: print('dry-run; use --apply')
 c.close()
if __name__=='__main__': main()
