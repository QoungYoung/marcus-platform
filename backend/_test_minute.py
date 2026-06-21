from datetime import date
from app.services.local_data_provider import local_data

# 加载数据
local_data.load(date(2026, 2, 2)
# 测试获取分钟数据
result = local_data.get_minute_quote('300102.SZ', date(2026, 2, 2)
print('RESULT:', result)
