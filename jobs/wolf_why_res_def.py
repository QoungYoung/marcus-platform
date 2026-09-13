# -*- coding: utf-8 -*-
"""wolf_why_res_def.py — 定向取证：他为什么选**军工/航天**与**资源类**方向？如何定位它们？

背景（用户 2026-09-13）：2026 他的 doing 里 资源/周期 39 天、军工/航天 19 天，但我们的"量能占比"池天然装不下这两类
（成交占比低）。要把它们复刻进来，必须先搞清他在语料里给的**理由与定位**（是主线？支线？超短？避险？）。

方法：按月切片逐日整理稿 → dsh 语义精读（非关键词提取），输出带日期与逐字原话的理由。
输出 /app/data/wolf_why_res_def.json。
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wolf_d12_evidence import call_dsh, _extract_json, slices_for, DATA      # noqa: E402

OUT = os.path.join(DATA, 'wolf_why_res_def.json')

PROMPT = """你是交易语料判据抽取器。下面是同一位交易者（"狼大"）在 **{period}** 的逐日行为记录（`>` 引用为原话）。

**唯一任务**：抽取他关于两类方向的**理由与定位**：
  A. **军工 / 航天 / 商业航天 / 卫星 / 航空 / 低空 / 国防**
  B. **资源类**：有色 / 黄金 / 贵金属 / 稀土 / 铜 / 铝 / 小金属 / 石油 / 煤炭 / 化工 / 战争金属
对每一类，逐条给出：
  · `role`：他把这个方向**定位成什么**——`主线` / `支线` / `超短或打短` / `低位埋伏` / `避险` / `不参与` / `其他`
  · `why`：**为什么**做或不做的**理由**（宏观/放水、涨价、供需、地缘、资金面、筹码结构、确定性排序、消息叙事…）
  · `quote`：**逐字原话**（不得改写/拼接）
  · `date`：YYYY-MM-DD
  · `exit`：若有退出/不做的条件，写一句；没有留空

严格要求：①**找不到就不输出**，绝不编造或用自己的话冒充；②不要输出与这两类方向无关的内容；
③只输出 JSON：{{"items":[{{"topic":"军工|资源","role":"...","why":"...","quote":"...","date":"...","exit":"..."}}]}}

===== 语料开始 =====
{body}
===== 语料结束 ====="""


def main():
    files = [os.path.join(DATA, 'corpus', 'wolf-daily-log-xls2026.md'),
             os.path.join(DATA, 'corpus', 'wolf-daily-log-nga.md')]
    state = {"items": [], "slices": {}, "failed": []}
    if os.path.exists(OUT) and '--force' not in sys.argv:
        try:
            state = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            pass
    jobs = []
    for f in files:
        if not os.path.exists(f):
            continue
        tag = os.path.basename(f).replace('wolf-daily-log-', '').replace('.md', '')
        for period, body in sorted(slices_for(f).items()):
            if not period.startswith('2026'):
                continue
            jobs.append((tag + '_' + period, period, body))
    print('[rd] 切片 %d：%s' % (len(jobs), ', '.join(j[0] for j in jobs)), flush=True)
    for sid, period, body in jobs:
        if state['slices'].get(sid, {}).get('n_items') is not None:
            print('[rd] 跳过 %s' % sid, flush=True)
            continue
        t0 = time.time()
        try:
            reply = call_dsh('wolfrd_' + sid, PROMPT.format(period=period, body=body))
            obj = _extract_json(reply)
            items = (obj or {}).get('items') or []
            for it in items:
                it['slice'] = sid
            state['items'] = [x for x in state['items'] if x.get('slice') != sid] + items
            state['slices'][sid] = {'n_items': len(items), 'secs': round(time.time() - t0, 1)}
            print('[rd] %s → %d 条 (%.0fs)' % (sid, len(items), time.time() - t0), flush=True)
        except Exception as e:
            state['failed'].append({'slice': sid, 'err': str(e)[:80]})
            print('[rd] %s 失败 %s' % (sid, type(e).__name__), flush=True)
        json.dump(state, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        time.sleep(5)
    print('\n[rd] 共 %d 条 → %s' % (len(state['items']), OUT), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
