#!/bin/bash
#
# VN.PY 模拟交易环境安装脚本
# 适用于 Ubuntu/Debian/CentOS 等 Linux 系统
#

set -e

echo "=============================================="
echo "  VN.PY 模拟交易环境安装脚本"
echo "=============================================="
echo ""

# 检测 Python 版本
echo "[1/6] 检测 Python 环境..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo "✓ 已安装 $PYTHON_VERSION"
else
    echo "✗ 错误：未找到 Python3，请先安装 Python 3.8+"
    exit 1
fi

# 检测 pip
echo ""
echo "[2/6] 检测 pip..."
if command -v pip3 &> /dev/null; then
    PIP_VERSION=$(pip3 --version)
    echo "✓ 已安装 pip3"
else
    echo "⚠ 未找到 pip3，尝试安装..."
    python3 -m ensurepip --default-pip || {
        echo "✗ 错误：无法安装 pip"
        exit 1
    }
fi

# 创建虚拟环境（可选，推荐）
echo ""
echo "[3/6] 创建 Python 虚拟环境..."
VENV_DIR="$(dirname "$0")/venv"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "✓ 虚拟环境已创建：$VENV_DIR"
else
    echo "✓ 虚拟环境已存在"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# 升级 pip
echo ""
echo "[4/6] 升级 pip..."
pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 VN.PY 核心
echo ""
echo "[5/6] 安装 VN.PY 核心组件..."
pip install vnpy -i https://pypi.tuna.tsinghua.edu.cn/simple
echo "✓ VN.PY 核心已安装"

# 安装插件
echo ""
echo "[6/6] 安装 VN.PY 插件..."

# 模拟账户（必需）
echo "  - 安装 vnpy-paperaccount..."
pip install vnpy-paperaccount -i https://pypi.tuna.tsinghua.edu.cn/simple || echo "  ⚠ vnpy-paperaccount 安装失败"

# CTA 策略（必需）
echo "  - 安装 vnpy-ctastrategy..."
pip install vnpy-ctastrategy -i https://pypi.tuna.tsinghua.edu.cn/simple || echo "  ⚠ vnpy-ctastrategy 安装失败"

# 回测引擎（必需）
echo "  - 安装 vnpy-backtester..."
pip install vnpy-backtester -i https://pypi.tuna.tsinghua.edu.cn/simple || echo "  ⚠ vnpy-backtester 安装失败"

# 可选：CTP 期货接口
echo "  - 安装 vnpy-ctp (期货接口，可选)..."
pip install vnpy-ctp -i https://pypi.tuna.tsinghua.edu.cn/simple || echo "  ⚠ vnpy-ctp 安装失败（如不需要期货可忽略）"

# 可选：XTP 股票接口
echo "  - 安装 vnpy-xtp (股票接口，可选)..."
pip install vnpy-xtp -i https://pypi.tuna.tsinghua.edu.cn/simple || echo "  ⚠ vnpy-xtp 安装失败（如不需要 A 股可忽略）"

# 可选：数据源
echo "  - 安装 akshare (免费数据源)..."
pip install akshare -i https://pypi.tuna.tsinghua.edu.cn/simple || echo "  ⚠ akshare 安装失败"

echo ""
echo "=============================================="
echo "  安装完成！"
echo "=============================================="
echo ""

# 验证安装
echo "验证安装..."
python3 -c "
import vnpy
print(f'✓ VN.PY 版本：{vnpy.__version__}')

try:
    from vnpy_paperaccount import PaperAccountApp
    print('✓ 模拟账户插件：OK')
except:
    print('⚠ 模拟账户插件：未安装')

try:
    from vnpy_ctastrategy import CtaTemplate
    print('✓ CTA 策略插件：OK')
except:
    print('⚠ CTA 策略插件：未安装')

try:
    from vnpy_ctp import CtpGateway
    print('✓ CTP 期货接口：OK')
except:
    print('⚠ CTP 期货接口：未安装')

try:
    from vnpy_xtp import XtpGateway
    print('✓ XTP 股票接口：OK')
except:
    print('⚠ XTP 股票接口：未安装')

try:
    import akshare
    print('✓ AKShare 数据源：OK')
except:
    print('⚠ AKShare 数据源：未安装')
"

echo ""
echo "=============================================="
echo "  下一步："
echo "=============================================="
echo ""
echo "1. 运行模拟交易演示:"
echo "   source $(dirname "$0")/venv/bin/activate"
echo "   python3 $(dirname "$0")/paper_demo.py"
echo ""
echo "2. 启动图形界面（需要 X11 环境）:"
echo "   python3 $(dirname "$0")/start_gui.py"
echo ""
echo "3. 运行策略回测:"
echo "   python3 $(dirname "$0")/backtest.py"
echo ""
echo "4. 查看策略示例:"
echo "   cat $(dirname "$0")/strategies/boll_strategy.py"
echo ""
echo "=============================================="
