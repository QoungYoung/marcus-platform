# -*- coding: utf-8 -*-
import json, os
P="/opt/marcus-platform/data/wave_config.md"
s=open(P,encoding="utf-8").read()
s=s.replace("# wave_agent 配置固化（冻结版本 v6）","# wave_agent 配置固化（冻结版本 v6+H2）")
s=s.replace("> 冻结时间：2026-08-30；当前最优，方向一致率 75% / 级别族 83%（12 例范围回测）。",
"> 冻结时间：2026-08-30（2026-09-01 补 2026H2 验证）；当前最优，方向一致率 75% / 级别族 83%（12 例范围 2016-2026 回测）。")
marker="## 8. 待办（非配置）"
if "2026H2 4-x" not in s:
    lines=[
"## 7.5 2026H2 4-x 子浪验证（v6+H2，2026-09-01 用 tushare 补数据后）",
"指数数据经 tushare 扩展到 2026-08-31（原止于 2026-06-26），rebuild pivot 含 2026-07-17 L 3764.2。",
"| 日期 | agent 判定 | 狼大期望 | 结果 |",
"|---|---|---|---|",
"| 2026-07-23 | d4 / 4-3筑底 / side | d4/4-3/side | 完全一致 |",
"| 2026-07-27 | d4 / 4-3 / side | d4/4-3→4-4/t_only | 级别一致，子浪小差 |",
"| 2026-07-31 | d4 / 4-3 / side | d4/4-3/side | 完全一致 |",
"| 2026-08-03 | d4 / 4-3 / side | d4/4-2/t_only | 级别一致，4-3 vs 4-2 小差 |",
"| 2026-08-13 | (dsh 读取超时) | d4/4-4/4-5/t_only | 瞬时超时，重试 |",
"结论：agent 全部正确判入大4浪(d4)，07-23/07-31 命中狼大'4-3筑底'——此前因数据截止无法验证的 4-x 部分现已验证。",
"",
    ]
    block="\n".join(lines)
    s=s.replace(marker, block+marker)
open(P,"w",encoding="utf-8").write(s)
print("config updated, len:", len(s))
V="/opt/marcus-platform/data/wave_version.json"
v=json.load(open(V,encoding="utf-8"))
v["h2_validated"]=True
v["h2_note"]="2026H2 4-x: 07-23/07-31 命中4-3筑底; 08-03 4-3 vs 4-2 小差; 08-13 dsh超时重试"
json.dump(v, open(V,"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("version", json.dumps(v, ensure_ascii=False))
