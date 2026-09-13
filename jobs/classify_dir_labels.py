# -*- coding: utf-8 -*-
"""classify_dir_labels.py — 把他 2026 的方向标签（232 个）分类，用于"股票方向层"对齐口径。

为什么要分类：`wolf_actual_mainline.doing` 里混着**期货商品仓**（"油（多油）""黄金期货"）、
**非方向词**（"整体仓位""ETF""低位方向"）与**股票方向**（"半导体""光""存储"）。
拿我们**股票方向层**的池去比一个含期货/模式词的全集，会结构性压低 recall（见 docs §九）。

方法：把标签清单交给 dsh 语义分类（非关键词匹配）。输出 data/wolf_dir_kind.json：
    {"<label>": {"kind": "stock_direction|futures_commodity|non_direction|mode", "note": "一句"}}
用法：python jobs/classify_dir_labels.py
"""
import collections
import json
import os
import sys

sys.path.insert(0, 'jobs')
sys.path.insert(0, '.dsh-tmp/wolfbt')
from wolf_d12_evidence import call_dsh, _extract_json     # noqa: E402

OUT = 'data/wolf_dir_kind.json'

PROMPT = """你在给一位 A 股交易者（网名"狼大"）的**方向标签**做分类。这些标签来自对他在论坛/社群发言的抽取，
表示他"当天在做的方向"。请把每个标签归入**四类之一**：

- `stock_direction`：**A 股股票方向**（如 半导体、光、存储、机器人、医药、航天、卫星、金融…）
- `futures_commodity`：**期货/商品仓**（如 多油/空油、黄金期货、原油多单、金油比…）
- `non_direction`：**不是方向**（仓位、ETF/指数标的、未指明、无意义词…）
- `mode`：**操作模式词**而非方向（如 低位方向、超短、打野、调仓、埋伏…）

判断依据：标签本身的措辞与括号里的说明（如"（多油）""（黄金期货多单）"= 期货）。
不确定时选最接近的；实在无法判断选 `non_direction` 并在 note 里写"不明"。

只输出 JSON（不要解释）：
{"items":[{"label":"标签原文","kind":"...","note":"不超过15字的理由"}]}

标签清单（共 {n} 个，含出现次数）：
{labels}
"""


def main():
    src = sys.argv[sys.argv.index('--labels') + 1] if '--labels' in sys.argv else None
    if src:
        cnt = collections.Counter(json.load(open(src, encoding='utf-8')))
    else:
        from local_pg import ensure_tunnel, DSN      # 仅本地读库时才需要
        ensure_tunnel()
        import psycopg2
        c = psycopg2.connect(**DSN)
        cur = c.cursor()
        cur.execute("SELECT trade_date, payload FROM wolf_actual_mainline WHERE status='ok'")
        cnt = collections.Counter()
        for d, p in cur.fetchall():
            o = p if isinstance(p, dict) else json.loads(p or '{}')
            for x in (o.get('doing') or []):
                dt = (x.get('dir') or '').strip()
                if dt:
                    cnt[dt] += 1
        c.close()
    out_path = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else OUT
    labels = sorted(cnt)
    print('[kind] 标签 %d 个（总条目 %d）' % (len(labels), sum(cnt.values())), flush=True)
    body = '\n'.join('%s\t%d' % (l, cnt[l]) for l in labels)
    prompt = PROMPT.replace("{n}", str(len(labels))).replace("{labels}", body)
    reply = call_dsh('wolfdirkind', prompt)
    obj = _extract_json(reply) or {}
    items = obj.get('items') or []
    out = {it['label']: {'kind': it.get('kind'), 'note': it.get('note', '')} for it in items if it.get('label')}
    miss = [l for l in labels if l not in out]
    if miss:
        print('[kind] 未分类 %d 个，按 non_direction 兜底：%s' % (len(miss), miss[:8]), flush=True)
        for l in miss:
            out[l] = {'kind': 'non_direction', 'note': '未返回，兜底'}
    kc = collections.Counter(v['kind'] for v in out.values())
    print('[kind] 分类结果：%s' % kc.most_common(), flush=True)
    for k in ('futures_commodity', 'mode', 'non_direction'):
        ex = [l for l, v in out.items() if v['kind'] == k][:10]
        print('   %-18s 例：%s' % (k, '、'.join(ex)), flush=True)
    json.dump(out, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[kind] → %s' % out_path, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
