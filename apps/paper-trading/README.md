# VN.PY 模拟交易环境配置 Skill

> 一键配置 VN.PY 量化交易框架的模拟交易环境

## 📦 安装

### 快速安装

```bash
# 进入技能目录
cd /root/.openclaw/workspace/skills/vnpy-paper-trading

# 运行安装脚本
bash install.sh
```

### 手动安装

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

## 🚀 使用

### 1. 运行模拟交易演示

```bash
# 无界面模式（推荐服务器环境）
python3 paper_demo.py
```

### 2. 启动图形界面

```bash
# 需要 X11/桌面环境
python3 start_gui.py
```

### 3. 运行策略回测

```bash
# 运行回测
python3 backtest.py

# 运行参数优化
python3 backtest.py optimize
```

### 4. 查看示例策略

```bash
# 布林带策略
cat strategies/boll_strategy.py

# 双均线策略
cat strategies/dual_ma_strategy.py
```

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `SKILL.md` | 技能文档 |
| `install.sh` | 安装脚本 |
| `requirements.txt` | Python 依赖 |
| `config.json` | 配置文件 |
| `paper_demo.py` | 模拟交易演示 |
| `start_gui.py` | GUI 启动脚本 |
| `backtest.py` | 回测脚本 |
| `strategies/` | 策略示例 |
| `data/` | 数据目录 |

## 📊 功能特性

- ✅ 模拟资金账户（初始资金可配置）
- ✅ 模拟订单撮合
- ✅ 实时盈亏计算
- ✅ 持仓管理
- ✅ CTA 策略框架
- ✅ 历史数据回测
- ✅ 参数优化

## 🔧 配置

编辑 `config.json` 配置：

```json
{
  "paper_account": {
    "initial_capital": 1000000,
    "commission_rate": 0.0003,
    "slippage": 0.01
  }
}
```

## 📝 策略开发

创建新策略：

```python
from vnpy_ctastrategy import CtaTemplate

class MyStrategy(CtaTemplate):
    author = "你的名字"
    
    parameters = []  # 策略参数
    variables = []   # 策略变量
    
    def on_init(self):
        self.load_bar(10)
    
    def on_bar(self, bar):
        # 交易逻辑
        pass
```

## ⚠️ 注意事项

1. **GUI 环境**: 图形界面需要 X11 支持
2. **数据源**: 回测需要历史数据，可使用 AKShare 获取
3. **模拟限制**: 模拟交易不考虑真实市场冲击

## 📚 相关资源

- [VN.PY 官网](https://www.vnpy.com/)
- [VN.PY 文档](https://www.vnpy.com/docs/)
- [SimNow 期货模拟](http://www.simnow.com.cn/)
- [XTP 股票接口](https://xtp.xsec.com.cn/)

## 📄 许可证

MIT License
