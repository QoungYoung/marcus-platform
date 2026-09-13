# -*- coding: utf-8 -*-
import yaml, os, tempfile
p="/opt/marcus-platform/config/tasks.yaml"
lines=[
"",
"- depends_on: []",
"  description: 每周一 8:20 东财概念高低位分类(position_class)+低位看逻辑AI判定(low_logic_agent)，供主线内轮动/风控过滤",
"  enabled: true",
"  id: position_judge",
"  name: 概念高低位判定",
"  notifications:",
"    channels:",
"    - qqbot",
"    on_failure: true",
"    on_success: false",
"    pi_analysis: false",
"  output:",
"    append: true",
"    log_file: logs/position_judge_{date}.log",
"  schedule:",
"    expr: 20 8 * * mon",
"    timezone: Asia/Shanghai",
"    type: cron",
"  script:",
"    args: []",
"    path: apps/main_line/position_judge.py",
]
s=open(p,encoding="utf-8").read()
if not s.endswith("\n"): s+="\n"
s2=s+"\n".join(lines)+"\n"
d=yaml.safe_load(s2)
assert len(d["tasks"])==23 and d["tasks"][-1]["id"]=="position_judge", str(len(d["tasks"]))
fd,tmp=tempfile.mkstemp(dir=os.path.dirname(p),suffix=".yaml"); os.close(fd)
open(tmp,"w",encoding="utf-8").write(s2); os.replace(tmp,p)
print("OK tasks:", len(yaml.safe_load(open(p,encoding="utf-8"))["tasks"]), "| last:", yaml.safe_load(open(p,encoding="utf-8"))["tasks"][-1]["id"])
