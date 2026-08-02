import json

data = json.load(open('F:/pythonProject/AITrade/marcus-platform/prompts_dict.json', encoding='utf-8'))
prompts = data.get('prompts', data)

for name, content in prompts.items():
    if 'TRADE' in name or 'CHAT' in name:
        c = str(content)
        print(f'{name}:')
        print(f'  连续性检查 = {"连续性检查" in c}')
        print(f'  重叠 = {"重叠" in c}')
        print(f'  混乱期 = {"混乱期" in c}')
        print(f'  子判定 = {"子判定" in c}')
        print(f'  大类归类 = {"价值防御" in c}')
        print(f'  大小 = {len(c)} chars')
        print()
