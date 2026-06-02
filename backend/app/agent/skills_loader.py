# -*- coding: utf-8 -*-
"""
Skills loader - loads SKILL.md files from directory.
"""
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str
    content: str
    file_path: str
    category: str = "analysis"
    disable_model_invocation: bool = False


class SkillsLoader:
    """Loads skills from SKILL.md files."""

    FRONTMATTER_RE = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL)
    NAME_RE = re.compile(r'^name:\s*(.+)$', re.MULTILINE)
    DESCRIPTION_RE = re.compile(r'^description:\s*(.+)$', re.MULTILINE)

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)

    def load_skill_from_file(self, file_path: Path) -> Optional[Skill]:
        """Load a single skill from SKILL.md file."""
        if not file_path.name == "SKILL.md":
            return None

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            return None

        # Parse frontmatter
        match = self.FRONTMATTER_RE.match(content)
        if not match:
            return None

        frontmatter_text = match.group(1)
        body = content[match.end():]

        # Extract name
        name_match = self.NAME_RE.search(frontmatter_text)
        name = name_match.group(1).strip() if name_match else file_path.parent.name

        # Extract description
        desc_match = self.DESCRIPTION_RE.search(frontmatter_text)
        description = desc_match.group(1).strip() if desc_match else ""

        if not description:
            return None

        # Determine category from name
        category = self._categorize_skill(name)

        return Skill(
            name=name,
            description=description,
            content=body.strip(),
            file_path=str(file_path),
            category=category,
        )

    def _categorize_skill(self, name: str) -> str:
        """Determine skill category from name."""
        name_lower = name.lower()
        if "trade" in name_lower or "execute" in name_lower:
            return "execution"
        elif "strategy" in name_lower or "backtest" in name_lower:
            return "strategy"
        return "analysis"

    def load_all_skills(self) -> List[Skill]:
        """Load all skills from skills directory."""
        skills = []

        if not self.skills_dir.exists():
            return skills

        # Walk through all subdirectories looking for SKILL.md
        for item in self.skills_dir.rglob("*"):
            if item.is_file() and item.name == "SKILL.md":
                skill = self.load_skill_from_file(item)
                if skill:
                    skills.append(skill)

        return skills

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a specific skill by name."""
        skills = self.load_all_skills()
        for skill in skills:
            if skill.name == name:
                return skill
        return None


def get_default_skills() -> List[Skill]:
    """Get built-in default skills (when no skill files available)."""
    return [
        Skill(
            name="market-analysis",
            description="分析市场走势、板块热点、指数表现",
            content="""你是股票市场分析师。当用户要求分析市场时：

1. 首先调用 get_market_indices 获取主要指数数据
2. 调用 get_sector_performance 获取板块表现
3. 调用 get_hot_stocks 获取热门股票
4. 综合以上数据，结合宏观消息面，给出市场分析

分析要点：
- 指数涨跌情况及趋势判断
- 板块轮动情况，热点板块分析
- 资金流向分析
- 短期技术面和中期趋势判断
- 风险提示""",
            file_path="built-in:market-analysis",
            category="analysis",
        ),
        Skill(
            name="stock-research",
            description="研究个股基本面和技术面，给出投资建议",
            content="""你是股票研究员。当用户要求研究股票时：

1. 调用 get_quote 获取股票实时行情
2. 获取公司基本面信息（可以通过 get_news 相关新闻）
3. 分析技术形态和趋势
4. 结合市场情绪给出投资建议

研究要点：
- 基本面：估值、业绩、行业地位
- 技术面：趋势、支撑阻力、形态
- 消息面：近期新闻、公告
- 风险因素：系统性风险、行业风险
- 操作建议：买入/持有/卖出区间""",
            file_path="built-in:stock-research",
            category="analysis",
        ),
        Skill(
            name="trading-execute",
            description="执行股票交易、管理订单",
            content="""你是交易执行专家。当用户要求下单时：

1. 确认交易意图：买入还是卖出
2. 检查持仓情况：可用数量是否足够
3. 检查账户资金是否充足
4. 执行交易 execute_trade
5. 回报交易结果

重要原则：
- 必须确认交易方向和数量
- 买入时检查可用资金
- 卖出时检查持仓可用数量
- Paper trading 不涉及真实资金
- 每次交易都要回报完整结果""",
            file_path="built-in:trading-execute",
            category="execution",
        ),
        Skill(
            name="portfolio-review",
            description="审视投资组合，评估持仓状况和风险",
            content="""你是投资组合管理专家。当用户要求审视组合时：

1. 调用 get_portfolio 获取当前持仓
2. 调用 get_account 获取账户总览
3. 调用 get_today_profit_loss 获取今日盈亏
4. 分析持仓结构和风险分布

分析要点：
- 仓位分布：是否过于集中
- 盈亏状况：总体和个股
- 风险暴露：行业集中度、风格暴露
- 调仓建议：是否需要优化
- 风险控制建议""",
            file_path="built-in:portfolio-review",
            category="strategy",
        ),
        Skill(
            name="news-sentiment",
            description="分析财经新闻和市场情绪",
            content="""你是财经新闻分析专家。当用户要求分析新闻时：

1. 调用 get_news 获取最新新闻
2. 调用 get_sentiment 获取市场情绪
3. 分析新闻对市场的潜在影响

分析要点：
- 重大新闻的事件性质（正面/负面/中性）
- 对相关板块和个股的影响
- 市场情绪变化
- 资金流向预判
- 投资决策参考""",
            file_path="built-in:news-sentiment",
            category="analysis",
        ),
    ]