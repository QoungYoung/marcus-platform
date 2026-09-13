# -*- coding: utf-8 -*-
import yaml, os, tempfile
p="/opt/marcus-platform/config/tasks.yaml"
lines=[
"",
"- depends_on: []",
"  description: 每周一 8:10 上证指数结构+量能/资金/GJD/历史锚点/主线 -> 狼大两级浪型判定 -> 写入 wave_state.json;交易gate按(level,sub_level)->operation->gate防御",
"  enabled: true",
"  id: wave_judge",
"  name: 波浪判定",
"  notifications:",
"    channels:",
"    - qqbot",
"    on_failure: true",
"    on_success: true",
"    pi_analysis: false",
"  output:",
"    append: false",
"    log_file: logs/wave_judge_{date}.log",
"  schedule:",
"    expr: 10 8 * * mon",
"    timezone: Asia/Shanghai",
"    type: cron",
"  script:",
"    args: []",
"    path: apps/main_line/wave_agent.py",
]
s=open(p,encoding="utf-8").read()
if not s.endswith("\n"): s+="\n"
s2=s+"\n".join(lines)+"\n"
d=yaml.safe_load(s2)
assert len(d["tasks"])==22 and d["tasks"][-1]["id"]=="wave_judge", "bad append: "+str(len(d["tasks"]))
fd,tmp=tempfile.mkstemp(dir=os.path.dirname(p),suffix=".yaml"); os.close(fd)
open(tmp,"w",encoding="utf-8").write(s2)
os.replace(tmp,p)
print("OK tasks:", len(yaml.safe_load(open(p,encoding="utf-8"))["tasks"]), "| last:", yaml.safe_load(open(p,encoding="utf-8"))["tasks"][-1]["id"])
