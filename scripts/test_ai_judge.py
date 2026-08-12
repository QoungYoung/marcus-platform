"""
Test deepseek-v4-flash with thinking disabled
API: https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
"""
import requests
import json
import time

API_KEY = "sk-e75a9cf2b4e04dceb2291d2425db03e7"
API_HOST = "api.deepseek.com"

STOCK_CONCEPTS = [
    "5G概念", "CPO概念", "DeepSeek概念", "HS300_", "IPv6",
    "人工智能", "算力", "东数西算", "信创", "国产替代"
]
TOP_CHANGE = ["机器人概念", "低空经济", "新能源车", "光伏", "半导体", "军工", "创新药", "白酒", "银行", "煤炭"]
TOP_INFLOW = ["人工智能", "算力", "CPO", "5G", "芯片", "消费电子", "锂电池", "光伏", "券商", "地产"]

stock_concepts_str = '\n'.join(f'- {c}' for c in sorted(STOCK_CONCEPTS))
top_change_str = '\n'.join(f'- {c}' for c in sorted(TOP_CHANGE))
top_inflow_str = '\n'.join(f'- {c}' for c in sorted(TOP_INFLOW))

PROMPT = f"""判断「股票所属概念」列表中是否有任何一个概念，与「涨幅TOP10」或「主力TOP10」中的某个概念语义相同或指向同一板块。

语义匹配标准（宽松）：
1. 名称相似：如"机器人概念"≈"机器人"，"AI智能体"≈"人工智能"，"低空经济"≈"低空飞行器"
2. 产业链同向：如"锂电池"≈"新能源车"，"光伏"≈"太阳能"
3. 简称/全称：如"CPO"≈"光电共封装"，"PCB"≈"印制电路板"
4. 同一板块的不同命名方式：如"人形机器人"≈"人行机器人"，"半导体"≈"芯片"

股票所属概念：
{stock_concepts_str}

当日概念涨幅TOP10：
{top_change_str}

主力净流入TOP10：
{top_inflow_str}

请只回复一个JSON对象，不要任何其他内容：
{{"matched": true或false, "matched_concept": "匹配到的概念名（false时为空字符串）", "in_which": "change/inflow/both/none", "reason": "一句话说明匹配理由"}}"""


def test(label, payload):
    print(f"\n{'='*60}")
    print(f"  [{label}]")
    print(f"{'='*60}")
    try:
        resp = requests.post(
            f'https://{API_HOST}/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {API_KEY}',
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            },
            json=payload,
            timeout=15,
        )
        print(f"  HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"  Body: {resp.text[:300]}")
            return False

        data = json.loads(resp.text)
        if 'error' in data:
            print(f"  API Error: {data['error']}")
            return False

        msg = data['choices'][0]['message']
        content = msg.get('content', '') or ''
        reasoning = msg.get('reasoning_content', '') or ''
        print(f"  content_len: {len(content)}, reasoning_len: {len(reasoning)}")
        print(f"  finish_reason: {data['choices'][0].get('finish_reason')}")

        if not content:
            print(f"  FAIL: content empty!")
            print(f"  reasoning[:200]: {reasoning[:200]}")
            return False

        try:
            result = json.loads(content)
            print(f"  OK: {json.dumps(result, ensure_ascii=False)}")
            return True
        except json.JSONDecodeError as e:
            print(f"  FAIL: JSON parse error: {e}")
            print(f"  content: {repr(content[:200])}")
            return False
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


# Test 1: v4-flash + thinking disabled + max_tokens=200
test("v4-flash thinking=disabled max_tokens=200", {
    'model': 'deepseek-v4-flash',
    'messages': [
        {'role': 'system', 'content': '只输出JSON，不要解释。'},
        {'role': 'user', 'content': PROMPT},
    ],
    'temperature': 0.0,
    'max_tokens': 200,
    'thinking': {'type': 'disabled'},
})

# Test 2: v4-flash + thinking disabled + max_tokens=200 + response_format
test("v4-flash thinking=disabled + response_format", {
    'model': 'deepseek-v4-flash',
    'messages': [
        {'role': 'system', 'content': '只输出JSON，不要解释。'},
        {'role': 'user', 'content': PROMPT},
    ],
    'temperature': 0.0,
    'max_tokens': 200,
    'thinking': {'type': 'disabled'},
    'response_format': {'type': 'json_object'},
})

# Stress: 5 rounds
print(f"\n\n{'='*60}")
print(f"  Stress: 5 rounds v4-flash thinking=disabled")
print(f"{'='*60}")
ok = 0
for i in range(5):
    result = test(f"Round {i+1}", {
        'model': 'deepseek-v4-flash',
        'messages': [
            {'role': 'system', 'content': '只输出JSON，不要解释。'},
            {'role': 'user', 'content': PROMPT},
        ],
        'temperature': 0.0,
        'max_tokens': 200,
        'thinking': {'type': 'disabled'},
        'response_format': {'type': 'json_object'},
    })
    if result:
        ok += 1
    time.sleep(0.3)

print(f"\n  Stress result: {ok}/5 OK")
