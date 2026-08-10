import pandas as pd, io, os
p = r'F:\pythonProject\AITrade\marcus-platform\data\backtest\资金流向数据\moneyflow.parquet'
if not os.path.exists(p):
    # find it
    for root, dirs, files in os.walk(r'F:\pythonProject\AITrade\marcus-platform\data'):
        for f in files:
            if f == 'moneyflow.parquet':
                print('found:', os.path.join(root, f))
                p = os.path.join(root, f)
                break
df = pd.read_parquet(p)
print('shape:', df.shape)
print('columns:', list(df.columns))
print('head:')
print(df.head(3).to_string())
print('dtypes:')
print(df.dtypes)
