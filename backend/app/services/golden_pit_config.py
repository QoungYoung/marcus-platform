# -*- coding: utf-8 -*-
"""黄金坑配置与参数 — 指数分级、阈值、DCA 参数与展示配置。

板块拆分(guide_only): 588000/159915 仅作择时指导，坑内资金由 SECTOR_ETF_POOL
按 combo 信号动态选筹配置；信号模式由 GOLDEN_PIT_SECTOR_SIGNAL_MODE 控制：
greed（默认，超跌+板块贪婪 arkvol funds-greed）或 moneyflow（超跌+中信二级5日资金流，旧逻辑可回滚）。
GOLDEN_PIT_SECTOR_SPLIT_ENABLED 灰度开关控制 dry-run 展示 / 实际执行；
开关与参数均从仓库根目录 .env 读取（经 app.config.Settings）。"""
import logging
from typing import Any, Dict, Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

CHINA_INDICES: Dict[str, Dict[str, Any]] = {
    # ═══ 核心 (必做) — 高胜率+高收益+高稳定性 ═══
    # 科创50: adjCAGR +15.9%, Win 73%, 52 trades, Stability 0.74
    # 科创50: 宽基仅作黄金坑择时指导(guide_only)，不直接买入，坑内资金拆分至板块 ETF 组合
    "588000": {"name": "科创50",   "priority": 4, "guide_only": True, "data_source": "arkvol",  "tier": "core",
               "signal_quality": "strong", "exp_15d": 5.5, "exp_20d": 8.5, "position_weight": 0.20,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.348, "entry_greed": 0.400, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.2, "pre_turn_cap": 0.20,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 80, "exit_half_pct": 40, "exit_fallback_days": 20,
               "buy_time": "14:44", "buy_time_pit": "09:37"},
    # 中证500: adjCAGR +16.2%, Win 72%, 29 trades, Stability 0.74 (satellite→core)
    "510500": {"name": "中证500",  "priority": 5, "data_source": "arkvol",  "tier": "core",
               "signal_quality": "strong", "exp_15d": 4.0, "exp_20d": 6.0, "position_weight": 0.20,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.345, "entry_greed": 0.395, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.2, "pre_turn_cap": 0.20,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 40, "exit_half_pct": 40, "exit_fallback_days": 20,
               "buy_time": "09:36"},
    # ═══ 卫星 (选做) — 高收益但波动大或历史短 ═══
    # 中证1000: adjCAGR +44.5%, Win 47%, 53 trades, Stability 0.52
    "159845": {"name": "中证1000", "priority": 3, "data_source": "arkvol",  "tier": "satellite",
               "signal_quality": "good",   "exp_15d": 3.5, "exp_20d": 5.0, "position_weight": 0.15,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.391, "entry_greed": 0.440, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.0, "pre_turn_cap": 0.12,
               "dca_strategy": "uniform_3", "dca_fallback": 15,
               "trend_factors": {"declining": 0.15, "full": 1.3},
               "exit_full_pct": 80, "exit_half_pct": 30, "exit_fallback_days": 20,
               "buy_time": "09:36", "buy_time_pit": "14:44"},
    # 创业板指: adjCAGR +22.6%, Win 58%, 43 trades, Stability 0.52
    # 创业板指: 宽基仅作黄金坑择时指导(guide_only)，不直接买入，坑内资金拆分至板块 ETF 组合
    "159915": {"name": "创业板指", "priority": 2, "guide_only": True, "data_source": "arkvol",  "tier": "satellite",
               "signal_quality": "good",   "exp_15d": 3.0, "exp_20d": 4.5, "position_weight": 0.15,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.328, "entry_greed": 0.380, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.0, "pre_turn_cap": 0.12,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 70, "exit_half_pct": 70, "exit_fallback_days": 25,
               "buy_time": "09:36"},
    # 道琼斯指数: adjCAGR +11.8%, Win 79%, 14 trades, Stability 0.91, 仅575天数据 (core→satellite)
    "513400": {"name": "道琼斯指数", "priority": 8, "data_source": "arkvol", "tier": "satellite",
               "signal_quality": "strong", "exp_15d": 2.5, "exp_20d": 3.5, "position_weight": 0.10,
               "use_fixed_greed": False, "entry_pct": 10, "pit_pct": 3,
               "pit_greed": 0.380, "entry_greed": 0.494, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 1.0, "pre_turn_cap": 0.15,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 99, "exit_half_pct": 99, "exit_fallback_days": 10,
               "buy_time": "09:36"},
    # ═══ 防御 (可选) — 稳定但收益偏低或参数敏感 ═══
    # 沪深300: adjCAGR +11.0%, Win 63%, 35 trades, Stability 0.63
    "510300": {"name": "沪深300",  "priority": 6, "data_source": "arkvol",  "tier": "defense",
               "signal_quality": "good",   "exp_15d": 1.5, "exp_20d": 2.5, "position_weight": 0.08,
               "use_fixed_greed": True, "entry_pct": 5, "pit_pct": 3,
               "pit_greed": 0.357, "entry_greed": 0.410, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.8, "pre_turn_cap": 0.12,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 40, "exit_half_pct": 40, "exit_fallback_days": 20,
               "buy_time": "09:36"},
    # 纳斯达克: adjCAGR +11.9%, Win 89%, 36 trades, Stability 0.30 ⚠️参数极度敏感 (core→defense)
    "159632": {"name": "纳斯达克", "priority": 10, "data_source": "arkvol", "tier": "defense",
               "signal_quality": "strong", "exp_15d": 2.0, "exp_20d": 3.0, "position_weight": 0.06,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 4,
               "pit_greed": 0.512, "entry_greed": 0.560, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.8, "pre_turn_cap": 0.08,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 99, "exit_half_pct": 99, "exit_fallback_days": 60,
               "buy_time": "09:37", "buy_time_pit": "14:15"},
    # 恒生指数: adjCAGR +10.5%, Win 67%, 30 trades, Stability 0.80
    "513600": {"name": "恒生指数", "priority": 9, "data_source": "arkvol", "tier": "defense",
               "signal_quality": "good", "exp_15d": 1.5, "exp_20d": 2.5, "position_weight": 0.06,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 5,
               "pit_greed": 0.368, "entry_greed": 0.420, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.8, "pre_turn_cap": 0.12,
               "dca_strategy": "uniform_3", "dca_fallback": 15,
               "trend_factors": {"declining": 0.10, "full": 1.3},
               "exit_full_pct": 60, "exit_half_pct": 30, "exit_fallback_days": 60,
               "buy_time": "09:36"},
    # ═══ 放弃 (回测确认: 年化过低) ═══
    # 上证50: adjCAGR +5.1%, Win 52%, 54 trades, Stability 0.97 — 收益不如货基
    "510050": {"name": "上证50",   "priority": 7, "data_source": "arkvol",  "tier": "drop",
               "signal_quality": "weak",   "exp_15d": 0.5, "exp_20d": 1.0, "position_weight": 0.0,
               "use_fixed_greed": True, "entry_pct": 5, "pit_pct": 3,
               "pit_greed": 0.403, "entry_greed": 0.450, "entry_offset": 0,
               "turning_days": 1, "position_multiplier": 0.0, "pre_turn_cap": 0.0,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 99, "exit_half_pct": 99, "exit_fallback_days": 10,
               "buy_time": "09:36"},
    # ═══ 卫星 (选做) — 海外指数 ═══
    # 韩国KOSPI: ArkVol 贪婪值来自 019455, ETF 交易代码 513310
    # adjCAGR +21%, Win 92%, 12 trades (pit=0.42), 仅660天数据 → satellite
    "513310": {"name": "韩国KOSPI", "priority": 11, "data_source": "arkvol", "tier": "satellite",
               "arkvol_code": "019455",
               "signal_quality": "good", "exp_15d": 8.0, "exp_20d": 12.0, "position_weight": 0.10,
               "use_fixed_greed": True, "entry_pct": 8, "pit_pct": 3,
               "pit_greed": 0.380, "entry_greed": 0.440, "entry_offset": 0,
               "turning_days": 0, "position_multiplier": 1.0, "pre_turn_cap": 0.08,
               "dca_strategy": "lump_entry", "dca_fallback": 15,
               "exit_full_pct": 60, "exit_half_pct": 99, "exit_fallback_days": 40,
               "buy_time": "09:36"},
    # ═══ 观察 (仅预警) ═══
    "562660": {"name": "中证2000", "priority": 1, "data_source": "arkvol", "tier": "watch",
               "signal_quality": "inferred", "exp_15d": None, "exp_20d": None, "position_weight": 0.0,
               "use_fixed_greed": False, "entry_pct": 10, "pit_pct": 5, "turning_days": 2,
               "position_multiplier": 0.0, "pre_turn_cap": 0.0,
               "exit_full_pct": 50, "exit_half_pct": 50, "exit_fallback_days": 60,
               "buy_time": "09:36"},
}

# ═══ 防御组合（撤场资金承接，永久持有模式）═══
# 信号源: 250日滚动价格分位（回测校准 2017-2026）；银行/黄金/国债/有色 ArkVol 贪婪仅作展示与快照；中证红利以价格分位为准
DEFENSE_INDICES: Dict[str, Dict[str, Any]] = {
    # 中证红利: 随宽基撤场等权轮入，持有至宽基重新入场时卖出（永久持有，无独立波段止盈）
    "515080": {"name": "中证红利", "priority": 21, "data_source": "defense_price", "tier": "defense_rotation",
               "etf_code": "SH515080",
               "signal_quality": "strong", "exp_15d": 1.5, "exp_20d": 2.5, "position_weight": 0.20,
               "use_fixed_greed": False, "entry_pct": 25, "pit_pct": 20,
               "entry_enabled": True,
               "turning_days": 1, "position_multiplier": 0.8,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 40, "exit_half_pct": 40, "exit_fallback_days": 60,
               "buy_time": "09:36"},
    # 银行: P10 入坑 / P40 撤场
    "512800": {"name": "银行", "priority": 22, "data_source": "arkvol", "tier": "defense_rotation",
               "arkvol_code": "014028", "etf_code": "SH512800",
               "signal_quality": "good", "exp_15d": 1.0, "exp_20d": 1.8, "position_weight": 0.25,
               "use_fixed_greed": False, "entry_pct": 15, "pit_pct": 10,
               "entry_enabled": True,
               "turning_days": 1, "position_multiplier": 0.8,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 40, "exit_half_pct": 40, "exit_fallback_days": 60,
               "buy_time": "09:36"},
    # 黄金: P15 入坑 / P50 撤场
    "518880": {"name": "黄金", "priority": 23, "data_source": "arkvol", "tier": "defense_rotation",
               "arkvol_code": "020412", "etf_code": "SH518880",
               "signal_quality": "weak", "exp_15d": 0.8, "exp_20d": 1.5, "position_weight": 0.25,
               "use_fixed_greed": False, "entry_pct": 20, "pit_pct": 15,
               "entry_enabled": True,
               "turning_days": 1, "position_multiplier": 0.8,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 50, "exit_half_pct": 50, "exit_fallback_days": 60,
               "buy_time": "09:36"},
    # 国债: P10 入坑 / P50 撤场
    "511010": {"name": "国债", "priority": 24, "data_source": "arkvol", "tier": "defense_rotation",
               "arkvol_code": "020741", "etf_code": "SH511010",
               "signal_quality": "weak", "exp_15d": 0.5, "exp_20d": 1.0, "position_weight": 0.25,
               "use_fixed_greed": False, "entry_pct": 15, "pit_pct": 10,
               "entry_enabled": True,
               "turning_days": 1, "position_multiplier": 0.8,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 50, "exit_half_pct": 50, "exit_fallback_days": 60,
               "buy_time": "09:36"},
    # 有色: 不触发入坑信号（入坑后继续下跌，仅作组合成分）
    "512400": {"name": "有色", "priority": 25, "data_source": "arkvol", "tier": "defense_rotation",
               "arkvol_code": "017193", "etf_code": "SH512400",
               "signal_quality": "weak", "exp_15d": 0.5, "exp_20d": 1.0, "position_weight": 0.0,
               "use_fixed_greed": False, "entry_pct": 1, "pit_pct": 1,
               "entry_enabled": False,
               "turning_days": 1, "position_multiplier": 0.5,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 99, "exit_half_pct": 99, "exit_fallback_days": 60,
               "buy_time": "09:36"},
}

# ═══ 半导体增强（坑内 10% 增强仓位，ArkVol tech-hardware-greed 信号）═══
# 注意: 该机制仅对非 guide_only 宽基生效；588000/159915 拆分后由 SECTOR_ETF_POOL 选筹替代
SEMI_BOOST_INDICES: Dict[str, Dict[str, Any]] = {
    # 科创芯片: P30 预警 / P5 入坑；二次拐点向下离场（连续3天回落清仓，兜底60天）
    "588200": {"name": "科创芯片", "priority": 31, "data_source": "arkvol_tech", "tier": "semi_boost",
               "arkvol_code": "588200", "etf_code": "SH588200",
               "signal_quality": "good", "exp_15d": 4.0, "exp_20d": 6.0, "position_weight": 0.10,
               "use_fixed_greed": False, "entry_pct": 30, "pit_pct": 5,
               "exit_mode": "down_turn", "exit_down_days": 3,
               "turning_days": 1, "position_multiplier": 1.0,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 50, "exit_half_pct": 50, "exit_fallback_days": 60,
               "buy_time": "09:36"},
    # 半导体: P30 预警 / P5 入坑；二次拐点向下离场（连续3天回落清仓，兜底60天）
    "512480": {"name": "半导体", "priority": 32, "data_source": "arkvol_tech", "tier": "semi_boost",
               "arkvol_code": "512480", "etf_code": "SH512480",
               "signal_quality": "good", "exp_15d": 4.0, "exp_20d": 6.0, "position_weight": 0.10,
               "use_fixed_greed": False, "entry_pct": 30, "pit_pct": 5,
               "exit_mode": "down_turn", "exit_down_days": 3,
               "turning_days": 1, "position_multiplier": 1.0,
               "dca_strategy": "lump_entry", "dca_fallback": 5,
               "exit_full_pct": 50, "exit_half_pct": 50, "exit_fallback_days": 60,
               "buy_time": "09:36"},
}

# 坑内仓位分配: 指数自身 90% + 科创芯片 5% + 半导体 5%（512480 实测最优：+117%/Calmar 0.88，80/10/10 降至 0.73）
# 注意: 仅对非 guide_only 宽基生效（588000/159915 由 SECTOR_ETF_POOL 动态配置替代）
PIT_POSITION_SPLIT: Dict[str, float] = {"index": 0.90, "588200": 0.05, "512480": 0.05}

# ═══ 黄金坑板块拆分（guide_only 宽基 → 板块 ETF 组合）═══
# 588000/159915 仅作择时指导，坑内资金按 combo 信号动态选筹(信号模式见 SECTOR_SIGNAL_MODE: greed=超跌+板块贪婪 / moneyflow=超跌+资金流)
# 板块池: 中信板块名 → 代表 ETF（flow_name 为 moneyflow_ind_dc 中的中信二级行业名）
# greed_code 为 arkvol funds-greed/fund 代表基金代码（场内 ETF 直接用 6 位代码；
# 场外代表基金 018301/015528/018396/026130/022243 对应消费电子/新能源车/医药/机械/军工）
SECTOR_ETF_POOL: Dict[str, Dict[str, Any]] = {
    "半导体":       {"name": "半导体ETF国联安", "etf_code": "SH512480", "flow_name": "半导体",     "greed_code": "512480"},
    "科创芯片":     {"name": "科创芯片ETF",     "etf_code": "SH588200", "flow_name": "半导体",     "greed_code": "588200"},
    "通信设备":     {"name": "通信设备ETF国泰", "etf_code": "SH515880", "flow_name": "通信设备",   "greed_code": "515880"},
    "计算机":       {"name": "计算机ETF国泰",   "etf_code": "SH512720", "flow_name": "计算机",     "greed_code": "512720"},
    "软件":         {"name": "软件ETF",          "etf_code": "SZ159852", "flow_name": "软件开发",   "greed_code": "159852"},
    "消费电子":     {"name": "消费电子ETF华夏", "etf_code": "SZ159732", "flow_name": "消费电子",   "greed_code": "018301"},
    "新能源动力系统": {"name": "新能源车ETF华夏", "etf_code": "SH515030", "flow_name": "电池",     "greed_code": "015528"},
    "生物医药":     {"name": "医药卫生ETF汇添富", "etf_code": "SZ159929", "flow_name": "医药生物", "greed_code": "018396"},
    "机械":         {"name": "机械设备ETF富国", "etf_code": "SZ159886", "flow_name": "专用设备",   "greed_code": "026130"},
    "军工":         {"name": "军工ETF国泰",     "etf_code": "SH512660", "flow_name": "国防军工",   "greed_code": "022243"},
}

# tech7 板块池（生产默认, pool_source=tech7）: arkvol tech-hardware-greed/series 覆盖的 7 只场内科技 ETF
# 回测（生产500天分位口径, 2025起5个板块窗口）: 超额 +5.86% 5/5 胜率, 优于 SECTOR_ETF_POOL(+3.21% 3/5)
# 已剔除: 159227 航天航空(军工属性错配/贪婪2025-06起), 588080 科创50ETF(与宽基588000同源)
TECH_SECTOR_POOL: Dict[str, Dict[str, Any]] = {
    "创业板50": {"name": "创业板50ETF华安",   "etf_code": "SZ159949"},
    "半导体":   {"name": "半导体ETF国联安",   "etf_code": "SH512480"},
    "人工智能": {"name": "人工智能ETF平安",   "etf_code": "SH512930"},
    "5G通信":   {"name": "5G通信ETF华夏",     "etf_code": "SH515050"},
    "大数据":   {"name": "大数据ETF富国",     "etf_code": "SH515400"},
    "通信设备": {"name": "通信设备ETF国泰",   "etf_code": "SH515880"},
    "科创芯片": {"name": "科创芯片ETF嘉实",   "etf_code": "SH588200"},
}

# 板块拆分选筹参数（来自 .env，经 app.config.Settings 加载；改 .env 后需重启 backend 生效）
_settings = get_settings()
GOLDEN_PIT_SECTOR_SPLIT_ENABLED: bool = _settings.GOLDEN_PIT_SECTOR_SPLIT_ENABLED  # 灰度开关: false=dry-run 展示选筹; true=板块 ETF 下单（588000/159915 不再直接买入）
SECTOR_TOP_N: int = _settings.GOLDEN_PIT_SECTOR_TOP_N                            # 坑内选筹 TOP N 板块
SECTOR_MAX_WEIGHT: float = _settings.GOLDEN_PIT_SECTOR_MAX_WEIGHT                # 单板块权重上限（归一化后截断）
COMBO_W_OVS: float = _settings.GOLDEN_PIT_SECTOR_COMBO_W_OVS                     # combo 超跌分权重
COMBO_W_MF: float = _settings.GOLDEN_PIT_SECTOR_COMBO_W_MF                       # combo 资金流分权重
SECTOR_OVS_DAYS: int = _settings.GOLDEN_PIT_SECTOR_OVS_DAYS                      # 超跌窗口（距N日高点回撤）
SECTOR_MF_DAYS: int = _settings.GOLDEN_PIT_SECTOR_MF_DAYS                        # 资金流累计窗口（日）
SECTOR_MF_MA_DAYS: int = _settings.GOLDEN_PIT_SECTOR_MF_MA_DAYS                  # 资金流均值窗口（日）
SECTOR_MIN_VALID: int = _settings.GOLDEN_PIT_SECTOR_MIN_VALID                    # 有效信号板块数下限（不足则空仓等待）
SECTOR_EXIT_DOWN_DAYS: int = _settings.GOLDEN_PIT_SECTOR_EXIT_DOWN_DAYS          # 板块ETF二次拐点退出: 连续回落天数
SECTOR_SIGNAL_MODE: str = _settings.GOLDEN_PIT_SECTOR_SIGNAL_MODE                # 选筹信号模式: greed=超跌+板块贪婪 / moneyflow=超跌+资金流
SECTOR_POOL_SOURCE: str = _settings.GOLDEN_PIT_SECTOR_POOL_SOURCE                  # 板块选筹池来源: tech7=场内科技7只(默认) / prod10=原10板块(回滚)

# ═══ DCA 执行载体（guide_only 宽基: 坑内资金买入对象, 灰度开关 dca_carrier_enabled）═══
# 回测（生产500天分位出入场 + 生产DCA形态, data/backtest/_dca_elastic_hist.py）:
#   科创50 信号 8 窗口: 588200 科创芯片 +19.23% / 512480 半导体 +18.08% / tech7等权 +14.15% / 宽基 +11.84%
#   创业板 信号 6 窗口: 159949 创业板50 +16.41% / 宽基 +14.85%（硬科技ETF普遍跑输, 因创业板坑由权重板块驱动）
# mode: sector_selection=板块选筹(现状) / fixed_combo=固定高弹性ETF组合 / broad=宽基本身(对照/回滚)
# 灰度已开启(2026-08-11): 588000 → fixed_combo 588200+512480 等权; 159915 → fixed_combo 159949
# 回滚: golden_pit_sector_config 置 dca_carrier_enabled=false 即恢复板块选筹
DCA_CARRIER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "588000": {"mode": "fixed_combo", "codes": [{"code": "588200", "weight": 0.5}, {"code": "512480", "weight": 0.5}]},
    "159915": {"mode": "fixed_combo", "codes": [{"code": "159949", "weight": 1.0}]},
}

# 撤场后防御承接组合（等权五标的：中证红利/银行/黄金/国债/有色）——永久持有模式
# 轮动回测 2020-11~2026-07（515080 替换 510880 + 持有至宽基重新入场）: 组合 CAGR 12.7% vs 波段 7.85%，回撤 -23.9% vs -21.0%
DEFENSE_TAKEOVER_WEIGHTS: Dict[str, float] = {
    "515080": 0.20, "512800": 0.20, "518880": 0.20, "511010": 0.20, "512400": 0.20,
}

# 全部指数配置索引（成长 + 防御 + 半导体增强），供统一查询
ALL_INDEX_CONFIGS: Dict[str, Dict[str, Any]] = {}
ALL_INDEX_CONFIGS.update(CHINA_INDICES)
ALL_INDEX_CONFIGS.update(DEFENSE_INDICES)
ALL_INDEX_CONFIGS.update(SEMI_BOOST_INDICES)


def get_index_config(fund_code: str) -> Dict[str, Any]:
    """按 fund_code 返回任意指数/标的最新的配置。"""
    return ALL_INDEX_CONFIGS.get(fund_code, {})


# 出入场参数覆盖键: pgsql golden_pit_sector_config.entry_exit_<fund_code> JSON 可覆盖（集中管理）
ENTRY_EXIT_OVERRIDE_KEYS = (
    "use_fixed_greed", "pit_greed", "entry_greed", "entry_offset",
    "pit_pct", "entry_pct", "entry_enabled",
    "exit_full_pct", "exit_half_pct", "exit_fallback_days",
    "turning_days", "exit_mode", "exit_down_days",
)


def get_effective_index_config(fund_code: str) -> Dict[str, Any]:
    """返回指数配置：CHINA_INDICES 硬编码为基线，叠加 pgsql golden_pit_sector_config
    表 entry_exit_<fund_code> JSON 的出入场参数覆盖（与板块拆分配置同表集中管理）。"""
    cfg = dict(ALL_INDEX_CONFIGS.get(fund_code, {}))
    try:
        from app.services import golden_pit_sector_service as _sector
        over = _sector.get_index_entry_exit(fund_code)
        for k in ENTRY_EXIT_OVERRIDE_KEYS:
            if k in over:
                cfg[k] = over[k]
    except Exception as e:  # noqa: BLE001 - 配置读取失败不阻断主流程
        logger.warning("读取 %s 出入场配置覆盖失败: %s", fund_code, e)
    return cfg


def build_entry_exit_defaults(overrides: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """提取全部指数的出入场参数字段（供 pgsql golden_pit_sector_config 集中管理 seed）。

    overrides: {fund_code: {field: value}} 已优化指数用生产确认参数覆盖默认。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for code, cfg in ALL_INDEX_CONFIGS.items():
        d = {k: cfg[k] for k in ENTRY_EXIT_OVERRIDE_KEYS if k in cfg}
        if d:
            out[code] = d
    for code, ov in (overrides or {}).items():
        if code in out:
            out[code].update(ov)
        else:
            out[code] = dict(ov)
    return out


# 仓位分级: 拐点确认度 → 仓位比例 (单次定投占 max_total 的比例)
POSITION_TIERS = {
    "pre_turn":   0.03,   # 拐点前: 单次≤3%, 累计≤15%
    "turning":    0.50,   # 拐点确认 (连续2天回升): 50%
    "accelerate": 0.75,   # 加速 (连续3天回升): 75%
    "full":       1.00,   # 满仓 (连续4+天回升): 100%
}
PRE_TURN_CUMULATIVE_CAP = 0.15  # 拐点前累计上限

# ── DCA 趋势调节因子 (全局默认) ──
# 趋势状态 → 仓位乘数, 与 DCA 基准权重相乘
# 分指数可在 CHINA_INDICES 中通过 trend_factors 字段覆盖
DEFAULT_TREND_FACTORS: Dict[str, float] = {
    "declining":    0.10,   # 贪婪仍在下降 → 飞刀减速
    "bottoming":    0.50,   # 首次回升 1 天 → 初步试探
    "turning":      1.00,   # 连续回升 2 天 → 标准节奏
    "accelerating": 1.20,   # 连续回升 3 天 → 加快速度
    "full":         1.50,   # 连续回升 4+ 天 → 快速满仓
}

# 加速阈值保护: greed 回升到此比例以上时, 趋势因子上限=1.0 (防止追高)
TREND_ACCELERATION_CAP_RATIO = 1.0  # greed / entry_greed >= 1.0 时禁止加速

# 拐点检测: 连续 N 天贪婪值回升→拐点确认
TURNING_CONSECUTIVE_DAYS = 2   # 连续回升天数 → 拐点确认

# 假信号过滤参数
FAKE_SIGNAL_REBOUND_DAYS = 2   # N天内反弹回P10以上 → 假信号
SIGNAL_CLUSTER_DAYS = 5         # N天内多个信号 → 合并，取最低greed日

# 保留绝对阈值作为参考值（仅科创50/上证50曾触发）
GREED_ABSOLUTE_PIT = 0.35

# 核心信号阈值: 用 expanding-window percentile (每个指数自己的历史分位)
PERCENTILE_GOLDEN_PIT = 5    # <= P5  → 黄金坑确认（信号最强）
PERCENTILE_WARNING = 10      # <= P10 → 预警（信号有效）

# 百分位计算窗口: 只取最近 N 天，避免 expanding-window 导致 Px 贪婪阈值漂移
PERCENTILE_WINDOW_DAYS = 500

PIT_WINDOW_DAYS = 15

SIGNAL_QUALITY_LABEL = {
    "strong": "信号强 (Win% >= 80%, Avg 15d > 5%)",
    "good":   "信号有效 (Win% >= 60%, Avg 15d > 3%)",
    "weak":   "信号弱 (不建议单独使用)",
}

STATUS_MAP = {
    "normal":     {"label": "正常",    "color": "#22c55e"},
    "warning":    {"label": "预警",    "color": "#f97316"},
    "golden_pit": {"label": "黄金坑",  "color": "#ef4444"},
}


def _trend_label(trend: str, factor: float) -> str:
    """生成通俗中文趋势标签。"""
    name_map = {
        "declining": "下跌中",
        "bottoming": "初步企稳",
        "turning": "拐点确认",
        "accelerating": "趋势加速",
        "full": "强势上涨",
    }
    name = name_map.get(trend, trend)
    if factor <= 0.15:
        return f"{name} · 仅投{int(factor * 100)}%试探"
    elif factor < 1.0:
        return f"{name} · 半速建仓({int(factor * 100)}%)"
    elif factor >= 1.5:
        return f"{name} · 全速建仓"
    elif factor > 1.0:
        return f"{name} · 加速建仓({int(factor * 100)}%)"
    return f"{name} · 标准节奏"


STRATEGY_LABELS: Dict[str, str] = {
    "uniform_3": "3日等权", "uniform_5": "5日等权", "uniform_7": "7日等权",
    "uniform_10": "10日等权", "uniform_15": "15日等权",
    "front_loaded": "前重后轻", "back_loaded": "前轻后重",
    "triangle": "三角加权", "lump_entry": "一次性建仓",
}


def _strategy_label(strategy: str) -> str:
    """DCA 策略代码 → 中文标签。"""
    return STRATEGY_LABELS.get(strategy, strategy)


def _compute_resonance(pit_count: int) -> float:
    """根据黄金坑指数数量计算共振乘数。"""
    if pit_count >= 4:
        return 1.3
    elif pit_count >= 3:
        return 1.2
    elif pit_count >= 2:
        return 1.0
    return 0.6


def _display_config() -> Dict[str, Any]:
    """返回前端展示所需的统一配置元数据。"""
    return {
        "status_colors": {
            "normal": STATUS_MAP["normal"]["color"],
            "warning": STATUS_MAP["warning"]["color"],
            "golden_pit": STATUS_MAP["golden_pit"]["color"],
        },
        "status_labels": {
            "normal": STATUS_MAP["normal"]["label"],
            "warning": STATUS_MAP["warning"]["label"],
            "golden_pit": STATUS_MAP["golden_pit"]["label"],
        },
        "strategy_labels": dict(STRATEGY_LABELS),
        "tier_labels": {
            "core": "核心", "satellite": "卫星", "defense": "防御",
            "defense_rotation": "防御轮动", "semi_boost": "半导体增强",
            "watch": "观察", "drop": "放弃",
        },
        "exit_labels": {
            "half_exit": "减持 50%",
            "full_exit": "清仓",
            "stop_profit": "止盈",
            "fallback_exit": "兜底退出",
        },
        "trend_icons": {
            "declining": "↓",
            "bottoming": "→",
            "recovering": "↑",
        },
        "trend_colors": {
            "declining": "#e5484d",
            "bottoming": "#c98a12",
            "recovering": "#27a06b",
        },
    }


def get_trend_factor(trend: str, days_rising: int, fund_code: str = "",
                     current_greed: float = 0.0, entry_greed: float = 999.0) -> float:
    """根据趋势状态返回仓位调节因子, 支持分指数覆盖。

    趋势状态映射 (全局默认):
      declining    (days_rising=0) → 0.10x
      bottoming    (days_rising=1) → 0.50x
      turning      (days_rising=2) → 1.00x
      accelerating (days_rising=3) → 1.20x
      full         (days_rising≥4) → 1.50x

    加速阈值保护: 当 current_greed >= entry_greed 时, 因子上限=1.0
    """
    # 确定趋势状态键
    if days_rising >= 4:
        state_key = "full"
    elif days_rising >= 3:
        state_key = "accelerating"
    elif days_rising >= 2:
        state_key = "turning"
    elif days_rising >= 1:
        state_key = "bottoming"
    else:
        state_key = "declining"

    # 读取分指数覆盖或全局默认（成长 + 防御 + 半导体增强）
    idx_cfg = ALL_INDEX_CONFIGS.get(fund_code, {})
    if fund_code and idx_cfg:
        idx_trend_factors = idx_cfg.get("trend_factors", {})
        factor = idx_trend_factors.get(state_key, DEFAULT_TREND_FACTORS.get(state_key, 1.0))
    else:
        factor = DEFAULT_TREND_FACTORS.get(state_key, 1.0)

    # 加速阈值保护: greed 已回到 entry_greed 以上 → 禁止加速
    if current_greed > 0 and entry_greed is not None and entry_greed > 0 and current_greed >= entry_greed:
        factor = min(factor, 1.0)

    return factor


def _trading_days_between(start_date: str, end_date: str) -> int:
    """估算两个日期之间的交易日数（简化为自然日 * 5/7）。"""
    try:
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(end_date, "%Y-%m-%d")
        days = (d2 - d1).days
        # 粗略估算交易日：自然日 * 5/7
        return max(0, round(days * 5 / 7))
    except (ValueError, TypeError):
        return 0


def _add_trading_days(date_str: str, trading_days: int) -> str:
    """给定起始日期和交易日数，估算目标日期。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        cal_days = round(trading_days * 7 / 5)
        result = d + timedelta(days=cal_days)
        return result.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str


def _describe_entry_strategy(cfg: Dict[str, Any]) -> str:
    """生成入场策略的人类可读描述。"""
    # DCA 分配方式
    if cfg.get("tier") == "defense_rotation":
        return "随宽基撤场等权轮入（不单独判断入坑）"
    dca = cfg.get("dca_strategy", "lump_entry")
    dca_label = {
        "lump_entry": "一次性打入",
        "uniform_3": "前3天分批",
        "uniform_5": "前5天分批",
        "uniform_7": "前7天分批",
        "uniform_10": "前10天分批",
        "uniform_15": "前15天分批",
        "front_loaded": "递减加权",
        "back_loaded": "递增加权",
        "triangle": "三角加权",
    }.get(dca, dca)

    if cfg.get("use_fixed_greed"):
        pit = cfg.get("pit_greed")
        entry = cfg.get("entry_greed")
        offset = cfg.get("entry_offset", 0)
        desc = f"固定阈值 greed≤{pit}"
        if entry is not None and entry != pit:
            desc += f" (预警≤{entry})"
        if offset:
            desc += f" 滞后{offset}天入场"
        else:
            desc += " 当天入场"
        desc += f" · {dca_label}"
        return desc
    else:
        pit_pct = cfg.get("pit_pct", 5)
        entry_pct = cfg.get("entry_pct", 10)
        return f"滚动百分位 P{pit_pct}入坑 P{entry_pct}预警 · {dca_label}"


def _describe_exit_strategy(cfg: Dict[str, Any]) -> str:
    """生成出场策略的人类可读描述。"""
    if cfg.get("tier") == "defense_rotation":
        return "持有至宽基重新入场时卖出（无独立止盈）"
    fallback = cfg.get("exit_fallback_days", 60)

    # 二次拐点向下离场（半导体增强）
    if cfg.get("exit_mode") == "down_turn":
        down_days = cfg.get("exit_down_days", 3)
        return f"二次拐点离场: 连续{down_days}天回落清仓 兜底{fallback}天"

    exit_full = cfg.get("exit_full_pct", 50)
    exit_half = cfg.get("exit_half_pct", 50)

    if exit_full == 99 and exit_half == 99:
        return f"固定持有 {fallback}天"
    elif exit_full == exit_half:
        return f"全仓止盈 P{exit_full} 兜底{fallback}天"
    else:
        return f"分批止盈 P{exit_half}/P{exit_full} 兜底{fallback}天"


