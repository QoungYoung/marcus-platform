---
name: vnpy-paper-trading
description: VN.PY 量化交易框架模拟交易环境配置，支持 A 股/期货模拟交易、策略回测、交易记录查询
read_when:
  - 配置 VN.PY 模拟交易环境
  - 安装量化交易框架
  - 查询交易记录或持仓
  - 运行策略回测
  - 设置 CTA 策略
metadata: {"clawdbot":{"emoji":"📉","requires":{"bins":["python3","bash"]}}}
allowed-tools: Bash(vnpy:*)
---

# VN.PY 模拟交易环境配置技能

## 描述

一键配置 VN.PY 量化交易框架的模拟交易环境，支持 A 股、期货模拟交易，包含完整的安装脚本、配置文件和示例策略。

## 功能

- ✅ 自动安装 VN.PY 核心及必要插件
- ✅ 配置模拟交易账户（paperaccount）
- ✅ 提供 CTA 策略框架
- ✅ 包含可运行的演示脚本
- ✅ 支持 A 股/期货模拟交易
- ✅ 提供策略开发模板
- ✅ **数据持久化**（SQLite + JSON）
- ✅ **查询历史交易记录**
- ✅ **查询当前持仓**
- ✅ **盈亏统计汇总**

## 使用方法

### 1. 快速配置环境

```bash
# 运行安装脚本
bash /root/.openclaw/workspace/skills/vnpy-paper-trading/install.sh
```

### 2. 验证安装

```bash
# 验证 VN.PY 安装
python3 -c "import vnpy; print('VN.PY 版本:', vnpy.__version__)"

# 验证模拟账户
python3 -c "from vnpy_paperaccount import PaperAccountApp; print('模拟账户插件 OK')"
```

### 3. 运行模拟交易演示

```bash
# 运行简易模拟引擎（无需 GUI）
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/paper_demo.py

# 运行持久化版本（推荐）
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/paper_engine.py

# 启动图形界面（需要 X11 环境）
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/start_gui.py
```

### 4. 查询交易数据（持久化版本）

```bash
# 查询账户信息
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/query.py account

# 查询当前持仓
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/query.py positions

# 查询订单记录
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/query.py orders -l 20

# 查询成交记录
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/query.py trades -l 20

# 查询盈亏汇总
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/query.py profit

# 买入操作
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/query.py buy SH600519 1700 100

# 卖出操作
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/query.py sell SH600519 1720 50
```

### 5. 加载示例策略

```bash
# 查看示例策略
cat /root/.openclaw/workspace/skills/vnpy-paper-trading/strategies/boll_strategy.py

# 运行策略回测
python3 /root/.openclaw/workspace/skills/vnpy-paper-trading/backtest.py
```

## 文件结构

```
vnpy-paper-trading/
├── SKILL.md              # 技能说明文档
├── install.sh            # 安装脚本
├── requirements.txt      # Python 依赖
├── config.json           # 配置文件模板
├── paper_demo.py         # 模拟交易演示
├── start_gui.py          # GUI 启动脚本
├── backtest.py           # 回测脚本
├── strategies/           # 策略目录
│   ├── __init__.py
│   ├── boll_strategy.py  # 布林带策略示例
│   └── dual_ma_strategy.py # 双均线策略示例
└── data/                 # 数据目录（可选）
    └── README.md
```

## 配置说明

### 模拟账户配置

编辑 `config.json`:

```json
{
  "paper_account": {
    "initial_capital": 1000000,
    "commission_rate": 0.0003,
    "slippage": 0.01
  },
  "ctp_simnow": {
    "enabled": false,
    "username": "你的 SimNow 账号",
    "password": "你的密码",
    "broker_id": "9999",
    "trade_server": "180.168.146.187:10201",
    "md_server": "180.168.146.187:10211"
  },
  "xtp": {
    "enabled": false,
    "user_id": "你的 XTP 账号",
    "password": "你的密码",
    "server": "127.0.0.1:7708"
  }
}
```

## 依赖项

- Python 3.8+
- VN.PY 4.0+
- vnpy-paperaccount
- vnpy-ctastrategy
- PyQt5 (GUI 需要)

## 注意事项

1. **GUI 环境**: 图形界面需要 X11 或桌面环境
2. **数据源**: 模拟交易需要行情数据，可使用：
   - Tushare (A 股)
   - AKShare (免费数据)
   - 券商提供的数据接口
3. **SimNow 账号**: 期货交易需先在 http://www.simnow.com.cn/ 注册
4. **XTP 接口**: A 股模拟需向中泰证券申请

## 常见问题

### Q: 安装失败怎么办？

```bash
# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: GUI 无法启动？

服务器环境可能不支持图形界面，使用无 GUI 模式：
```bash
python3 paper_demo.py
```

### Q: 如何获取实时行情？

```python
# 使用 AKShare 获取免费行情
import akshare as ak
df = ak.stock_zh_a_spot_em()
print(df.head())
```

### Q: 策略如何开发？

参考 `strategies/` 目录下的示例策略，继承 `CtaTemplate` 类即可。

## 策略开发模板

```python
from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager
)

class MyStrategy(CtaTemplate):
    """自定义策略"""
    
    author = "你的名字"
    
    # 策略参数
    boll_window = 20
    boll_dev = 2
    fixed_size = 1
    
    # 策略变量
    boll_up = 0
    boll_down = 0
    boll_mid = 0
    
    parameters = ["boll_window", "boll_dev", "fixed_size"]
    variables = ["boll_up", "boll_down", "boll_mid"]
    
    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager()
    
    def on_init(self):
        """初始化"""
        self.write_log("策略初始化")
        self.load_bar(10)
    
    def on_start(self):
        """启动"""
        self.write_log("策略启动")
    
    def on_stop(self):
        """停止"""
        self.write_log("策略停止")
    
    def on_tick(self, tick: TickData):
        """Tick 推送"""
        self.bg.update_tick(tick)
    
    def on_bar(self, bar: BarData):
        """K 线推送"""
        self.cancel_all()
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        
        # 计算布林带
        self.boll_up, self.boll_down, self.boll_mid = self.am.boll(
            self.boll_window, self.boll_dev
        )
        
        # 交易逻辑
        if not self.pos:
            if bar.close_price > self.boll_up:
                self.buy(bar.close_price, self.fixed_size)
        elif self.pos > 0:
            if bar.close_price < self.boll_mid:
                self.sell(bar.close_price, abs(self.pos))
        
        # 更新图形
        self.put_event()
    
    def on_order(self, order: OrderData):
        """订单推送"""
        pass
    
    def on_trade(self, trade: TradeData):
        """成交推送"""
        self.write_log(f"成交：{trade.direction} {trade.volume}手 @ {trade.price}")
        self.put_event()
    
    def on_stop_order(self, stop_order: StopOrder):
        """停止单推送"""
        pass
```

## 相关资源

- VN.PY 官网：https://www.vnpy.com/
- GitHub: https://github.com/vnpy/vnpy
- 文档：https://www.vnpy.com/docs/
- SimNow 模拟：http://www.simnow.com.cn/
- XTP 接口：https://xtp.xsec.com.cn/

## 更新日志

- 2026-03-04: 初始版本，包含完整的模拟交易环境配置
