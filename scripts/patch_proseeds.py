# -*- coding: utf-8 -*-
# 在服务器 prompt_seeds.py 的 TRADE_SYSTEM_PROMPT 插入『主线判定注入与确认层』SOP
import io
p='backend/app/db/prompt_seeds.py'
s=open(p,encoding='utf-8').read()
BT=chr(96)
marker='## 交易决策 SOP'
add = ('## 主线判定注入与确认层\n\n'
'**系统会在 prompt 开头注入 '+BT+'main_line_state'+BT+'**（主线判定agent每周生成：'+BT+'main_line'+BT+'=当前主线、'+BT+'candidates'+BT+'=候选集(catalyst≥0.7)、各主题'+BT+'catalyst_score'+BT+'）。\n\n'
'**主线两档判定（结合已有右侧/regime/做T框架）：**\n'
'- 候选(early)：主题在 '+BT+'candidates'+BT+'（catalyst≥0.7）——产业逻辑强。**观察，不建仓**；等待动量/资金确认。\n'
'- 确认(confirmed)：候选 且 当日动量/资金转强（满足其一即可）：RS转正 / 突破前高 / 板块主力净流入>0(fund_acc转正) / 均线多头。\n'
'- **决策**：确认 → 可对主线方向（重点关注）建仓/加仓（仍需过 check_entry_filters / calc_position / stance）；候选 → 加入观察（可入长期候选池），不主动建仓，若后续确认转强再介入；无 → 不作为主线重点，按明暗线/其他方向操作。\n'
'**限制**：'+BT+'main_line_state'+BT+' 是**大方向参考**，不替代当天 '+BT+'get_concept_fund_flow'+BT+'（明线/暗线）+' + BT+'check_entry_filters'+BT+'+'+BT+'calc_position'+BT+' 三层过滤；主线判断**不能**作为跳过右侧纪律/风控的理由。\n\n')
if '主线判定注入与确认层' in s:
    print('已存在,跳过')
else:
    s=s.replace(marker, add+marker, 1)
    open(p,'w',encoding='utf-8').write(s)
    print('已插入')
