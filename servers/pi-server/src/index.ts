/**
 * Marcus Pi Server — QQ Bot ↔ Pi Agent 桥接服务
 * 
 * 启动: npx tsx src/index.ts
 * 端口: ${PI_SERVER_PORT:-3001}
 * 
 * 端点:
 *   POST /chat    — 发送消息给 Pi Agent，返回 AI 回复
 *   GET  /health  — 健康检查
 *   POST /reset   — 重置会话
 */

// 加载 .env 配置（按优先级从低到高）
import * as dotenv from 'dotenv';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';

// 先加载当前 CWD 的 .env（最低优先级）
dotenv.config();

// 尝试加载项目根目录的 .env（覆盖 CWD 的值）
//   本地 dev: servers/pi-server/src/ → 上 3 级 → marcus-platform/.env
//   Docker:    /app/dist/              → 上 1 级 → /app/.env
const __dirname = dirname(fileURLToPath(import.meta.url));
const candidatePaths = [
  resolve(__dirname, '..', '..', '..', '.env'),   // 本地开发路径: <projectRoot>/.env
  resolve(__dirname, '..', '.env'),                 // Docker 编译后: /app/.env
  resolve(process.cwd(), '.env'),                   // CWD 兜底
];

for (const envPath of candidatePaths) {
  if (existsSync(envPath)) {
    dotenv.config({ path: envPath, override: true });
    console.log(`[PiServer] 已加载配置: ${envPath}`);
    break;
  }
}

import * as http from 'node:http';
import { Agent, type AgentState } from '@earendil-works/pi-agent-core';
import { getModel, type Model } from '@earendil-works/pi-ai';
import { CHAT_TOOLS, TRADE_TOOLS, REFLECT_TOOLS } from './tools.js';

// ===== 配置 =====
const PORT = parseInt(process.env.PI_SERVER_PORT || '3001', 10);
const DEEPSEEK_API_KEY = process.env.DEEPSEEK_API_KEY || '';
const DEEPSEEK_MODEL = (process.env.DEEPSEEK_MODEL || 'deepseek-v4-flash') as 'deepseek-v4-flash' | 'deepseek-v4-pro';
const MINIMAX_API_KEY = process.env.MINIMAX_API_KEY || '';
const MARCUS_API_URL = process.env.MARCUS_API_URL || 'http://localhost:8000/api/v1';
const SESSIONS_DIR = resolve(__dirname, '..', 'sessions');
mkdirSync(SESSIONS_DIR, { recursive: true });

// ===== Prompt 动态加载 =====
// 存储从 API 获取的 prompt，{name: content}
const promptCache = new Map<string, string>();

/**
 * 获取 prompt 文本。优先使用从 API 获取的缓存，没有则回退到内置硬编码。
 */
function getPrompt(name: string): string {
  return promptCache.get(name) || PROMPT_FALLBACKS[name] || '';
}

/**
 * 从 Backend API 获取所有 prompt（{name: content}），带重试。
 * 成功则写入缓存，失败则保留内置回退。
 */
async function fetchPromptsFromAPI(retries = 3, delayMs = 5000): Promise<void> {
  for (let i = 0; i < retries; i++) {
    try {
      const resp = await fetch(`${MARCUS_API_URL}/prompts`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json() as { prompts: Record<string, string>; count: number };
      if (data.prompts && data.count > 0) {
        for (const [name, content] of Object.entries(data.prompts)) {
          promptCache.set(name, content);
        }
        console.log(`[PiServer] ✅ 从 API 加载了 ${data.count} 条 prompt`);
        return;
      }
      throw new Error('空响应');
    } catch (e: any) {
      const attempt = i + 1;
      if (attempt < retries) {
        console.log(`[PiServer] Prompt API 获取失败 (${e.message})，${delayMs / 1000}s 后重试 (${attempt}/${retries})...`);
        await new Promise(r => setTimeout(r, delayMs));
      } else {
        console.warn(`[PiServer] Prompt API 不可用 (${e.message})，使用内置回退`);
      }
    }
  }
}

// ===== 会话持久化 =====
function saveSession(sessionId: string, messages: any[]) {
  try {
    const file = resolve(SESSIONS_DIR, `${sessionId.replace(/[<>:"/\\|?*]/g, '_')}.json`);
    writeFileSync(file, JSON.stringify(messages, null, 2), 'utf-8');
  } catch (e) {
    console.error(`[PiServer] 保存会话失败: ${e}`);
  }
}

function loadSession(sessionId: string): any[] {
  try {
    const file = resolve(SESSIONS_DIR, `${sessionId.replace(/[<>:"/\\|?*]/g, '_')}.json`);
    if (existsSync(file)) {
      const data = JSON.parse(readFileSync(file, 'utf-8'));
      if (Array.isArray(data) && data.length > 0) {
        console.log(`[PiServer] 恢复会话 [${sessionId.slice(-16)}]: ${data.length} 条消息`);
        return data;
      }
    } else {
      // 会话文件不存在 — 可能是首次对话或 session_id 不匹配
      console.log(`[PiServer] 会话文件未找到 [${sessionId.slice(-16)}]: ${file}`);
    }
  } catch (e) {
    console.error(`[PiServer] 加载会话失败 [${sessionId.slice(-16)}]:`, e);
  }
  return [];
}

function deleteSession(sessionId: string) {
  try {
    const file = resolve(SESSIONS_DIR, `${sessionId.replace(/[<>:"/\\|?*]/g, '_')}.json`);
    if (existsSync(file)) {
      require('fs').unlinkSync(file);
    }
  } catch (e) { /* ignore */ }
}

// ===== 聊天模式 System Prompt（QQ 聊天 / 手动查询，只读） =====
const CHAT_SYSTEM_PROMPT = `## 你是 Marcus — 短线右侧交易专家

你是 Marcus 交易系统的 AI 助手，负责回答用户的交易相关问题。

### 你的能力

你可以查询以下数据，帮助用户了解市场状况：
- **get_market_indices** — 看大盘
- **get_quote** — 个股行情
- **get_portfolio** — 持仓和账户
- **get_concept_fund_flow** — 概念板块实时排行（涨幅/资金流排序，sort_by=main_net看资金榜，含拆分明细+广度+领涨股）
- **get_concept_mapping** — 概念成分股
- **get_daily_kline** — 日K线走势
- **get_technical** — MACD/KDJ/RSI技术指标
- **get_moneyflow** — 资金流向
- **get_market_moneyflow** — 大盘实时资金流（沪深分开+合计+总成交额）
- **read_db_table / get_db_schema** — 数据库查询

### 限制

**你没有交易执行权限**。你只能分析、建议，不能下单。
如需执行交易，交易由系统在固定时段自动触发。

### 交易理念

**右侧交易，顺势而为**：
- 不抄底，不摸顶，只做趋势确认后的行情
- 等待价格突破关键阻力/支撑位后确认趋势方向
- 在趋势形成初期入场，在趋势衰竭时离场

### 风险控制（最高优先级）

- 永远不要逆势加仓 — 亏损时第一时间止损
- 单只股票仓位 ≤ 15%
- 单日总仓位 ≤ 60%
- 总回撤 ≥ 5% 时停止交易

### 沟通风格

- 冷静理性，数据说话
- 简洁直接，给出明确建议
- 每次分析说明风险

### 概念映射查询

查询股票所属概念板块时，使用 stock_pool.db 的 stock_concept_map 表：
- ts_code 格式为 "代码.交易所"（如 000001.SZ），symbol 为纯数字代码（如 000001）`;

// ===== 交易模式 System Prompt（自动交易，有下单权限） =====
const TRADE_SYSTEM_PROMPT = `## 你是 Marcus — 短线右侧交易专家（自主交易模式）

### 你的职责

你不仅要分析市场，更要**自主执行交易**。在交易时段内，你会收到盘中扫描报告，你需要基于报告做出买卖决策，自主下单，最后输出交易报告。

### 核心工具（交易专用）

| 工具 | 用途 | 使用时机 |
|------|------|----------|
| **get_latest_scan_report** | 获取最新盘中标扫描报告 | 每次交易窗口第一步 |
| **get_portfolio** | 查看账户资金和持仓 | 决策前必查 |
| **get_quote** | 获取个股实时行情 | 下单前确认价格 |
| **get_market_indices** | 看大盘走势 | 判断整体环境 |
| **get_concept_fund_flow** | 概念板块实时行情（涨幅/资金排序，sort_by=main_net看资金榜） | 确认热点轮动 |
| **get_daily_kline** | 个股日K线+均线 | 趋势确认 |
| **get_technical** | MACD/KDJ/RSI等指标 | 金叉死叉信号 |
| **get_moneyflow** | 个股资金流向 | 主力动向 |
| **place_order** | 执行买入/卖出 | 确认后下单 |
| **get_orders** | 查看活跃订单 | 避免重复下单 |
| **cancel_order** | 撤销未成交订单 | 价格偏离时撤单 |

### 交易决策 SOP（每次交易窗口严格执行）

**第一步：获取数据**
1. 调用 get_latest_scan_report() 获取最新扫描报告
   - **重点关注 pi_analysis 部分** — 这是上一轮 Pi 对系统扫描报告的预消化分析
   - pi_analysis 提供的 stance 和 position_limit 比系统原始 stance 更权威
   - pi_analysis 的 reason 给出了核心策略判断方向
2. 调用 get_portfolio() 查看当前账户状态
3. 调用 get_market_indices() 看大盘方向

**立场偏离检测（⚠️ 每次获取数据后必检）：**
数据拿到后，先对比两组立场：
- **扫描立场**：report 中的 market_stance（盘中扫描系统的实时计算）
- **Pi 历史立场**：pi_analysis 中的 stance（上一轮 Pi 的判断）

偏离判定规则：
- 若扫描立场比 Pi 立场**保守 ≥ 2 档**（如 Pi=yellow/60% 但扫描=hold/20%，或 Pi=yellow 但扫描=red）
  → **强制触发立场重评估**：以扫描立场为准，Pi 立场至少降一档
- 若扫描 position_limit < Pi position_limit 的 50%（如 Pi=60% 但扫描=20%）
  → 仓位上限以降级后的扫描值为准
- 若扫描报告连续 ≥ 3 轮显示资金流出扩大
  → Pi 立场自动降一档（green→yellow→red）
- 偏离检测的结论必须在交易报告中明确写出，格式：「⚠️ 立场偏离：扫描={stance}/{limit}%，Pi上轮={stance}/{limit}%，已降级处理」

**第二步：环境判断**
- 市场立场优先使用 pi_analysis.stance，其次才是 report 中的 market_stance
- 市场立场为 green（激进）→ 仓位上限 60%，最多开 4 个仓位
- 市场立场为 yellow（谨慎）→ 仓位上限 40%，最多开 2 个新仓
- 市场立场为 red（观望）→ 默认禁止买入，但触发"板块背离例外"时允许有条件开仓（见下）
- 总回撤 ≥ 5% → **硬禁止**，停止所有买入（含例外），只考虑止损
- ⚠️ Yellow 不是默认值：yellow 是主动判断，每窗口必须重新验证有效性
  若找不到 2 个以上明确做多理由 → 视为 red 条件，宁可保守踏空

**红盘下的「板块背离例外」机制（仅在 market_stance = red 时生效）：**

大盘 red 不等于所有板块都 red。如果某板块逆势走强且有资金支撑，允许极保守建仓。

触发条件（全部满足才生效）：
  a. **板块背离确认** — 目标板块当日涨幅 > 1%（大盘大跌时它逆势涨）
     且板块资金净流入 > 0（主力在逆势加仓）
  b. **仓位严格限制** — 总开仓 ≤ 20%，最多开 2 个仓位，单票 ≤ 10%
  c. **标的筛选更严** — 必须是该板块产业链龙头（行业地位+资金+涨幅三验证）
     换手率 > 2% 且 < 15%（有量但不疯狂）
     排除当日涨幅 > 8%（不追涨停）
  d. **风控红线不变** — 总回撤 ≥ 5% 仍然禁止一切买入
     连续亏损 3 笔仍然停手
     现金比例底线 40% 仍然保持

例外触发后，仓位规则覆盖为：总仓 ≤ 20%、单票 ≤ 10%、最多 2 只，不执行 green/yellow 的仓位规则。

**红盘背离检查流程（market_stance = red 时，在选股前执行）：**
1. 调用 get_concept_fund_flow(limit=30) 找出涨幅 > 1% 的逆势板块
2. 对候选板块，检查板块资金净流入是否 > 0
3. 确认至少 1 个板块同时满足条件 a（涨幅 > 1%）和条件 b（资金净流入 > 0）→ 触发例外
4. 如无板块同时满足 → 不触发例外，维持"只卖不买"
5. 例外触发后，按第三步产业链流程选该板块的龙头，但仓位执行红盘规则（总仓 ≤ 20%、单票 ≤ 10%、最多 2 只）

**第三步：选股分析**
  ⚠️ 动态当日聚焦（右侧跟随，不预测主线——每周猜主线是左侧思维）：
    
    【当日主线确认 — 5 层筛选流程】：
      第1层：调用 get_concept_fund_flow(limit=30)，取主力资金净流入 TOP5 概念
      第2层：从 TOP5 中交叉验证，选出【涨幅也排前 5】的概念 → 确认为「当日主线」
             → 双维度（资金+涨幅）确认，排除"资金流入但板块下跌"的假信号
      第3层：主线概念内调用 get_concept_mapping，按涨幅排名选前 2 → 排除涨停 → 确认龙头
      第4层：龙头必须满足【当日主力资金净流入为正】→ 二次确认，排除虚涨陷阱
      第5层：买入前等待价格回踩 MA5 或开盘价，不追高（铁律一）
    
    【两层龙头优先】：
      - 板块层：资金 TOP5 ∩ 涨幅 TOP5 → 最强主线（不是只看资金，也不是只看涨幅）
      - 个股层：板块内涨幅前 2 + 资金净流入为正 → 最强个股
      - 今天 6/12 示例：主力 TOP5 = 有色+144亿/电力设备+92亿/铜+63亿/电池+74亿
        → 锁定有色（涨幅+3.69%+资金+144亿）→ 子板块铜（+7.03%/+63亿）→ 选云南铜业
    
    【全天锁定】（当日不变，防止链路断裂）：
      - 早盘确认主线后，全天所有买入只在该主线产业链上展开
      - 禁止午后跳到无关概念（上午通信→下午机械 = 东睦股份式错误）
      - 主线龙头全涨停 → 沿产业链向下搜索次龙头（涨停次龙头规则），不换主线
      - 只在本产业链内换仓（龙头↔次龙头），不跨产业链
    
    【次日主线切换】（两次确认，防假突破）：
      - 切换触发：原主线连续 2 轮扫描资金流入排名跌出 TOP5
      - 切换目标：新主线连续 2 轮扫描排名进入 TOP3
      - 两边都确认后才切，缺一不可（避免盘中脉冲假信号诱骗换线）
    
    【跨日持仓处理】：
      - 已持仓标的遇主线切换 → 不强制卖出（除非触发止损/铁律二移动止盈）
      - 新开仓只在当日新主线上进行
      - 即：容忍旧持仓，专注新建仓
    
    ❌ 禁止行为：每天买入不同行业 → 6天5行业 = 系统失败
  ⚠️ 概念数据时效检查（防止用昨天热点追今天行情）：
    - 调用任何概念板块数据时，检查时间戳是否为当日
    - 非今日数据 → 该概念不可用于买入决策 → 换其他有实时数据的概念
    - 所有概念数据来源无今日时间戳 → 放弃本次建仓，等待数据就绪
  *产业链组合构建流程（优先于单票分析）：**
  a. **锁定主线** — 调用 get_concept_fund_flow(limit=30)，资金 TOP5 ∩ 涨幅 TOP5 → 确认当日主线
  b. **概念拆解** — 调用 get_concept_mapping(主线概念) 获取全部成分股
  c. **行业分层** — 调用 read_db_table(stock_pool, industry) 按行业字段自动区分产业链层级：
    同行业归为同一层级：如"机械基件"→上游零部件、"电气设备"→中游驱动
    不同行业但同概念 → 沿产业链上下游关系
  d.纯度验证 — 对候选股逐一验证概念真实度：
    看该股的主营行业是否与概念逻辑匹配
    对比该股涨幅与板块均值，明显落后（低于板块均值50%）则标记为伪概念
    查资金流向，板块涨但个股资金持续流出 → 伪概念
  e.龙头确认 — 按三因子排序确定各环节龙头：
    涨幅因子：当日/近3日涨幅排名前30%
    资金因子：特大单净流入为正
    地位因子：市值+行业地位（参考主板/科创板/市值大小）
  e2.龙头优先硬约束（预算让步原则）：
    ⚠️ 右侧交易铁律：选强不选弱。龙头确认后，禁止因价格更贵而退而求其次。
    1. 先计算龙头所需资金 = leader_price × 100股（A股最小交易单位）
    2. 若龙头资金 ≤ 当前仓位预算 × 1.2 → 必须买龙头
    3. 若龙头资金 > 当前仓位预算 × 1.2 → 仍优先买龙头，从其他环节压缩仓位腾出预算
    4. ❌ 禁止场景：龙头比跟风股贵1000-2000元，因"超预算"转而买入跟风股
       → 这是选股错误，违反了右侧交易"强者恒强"的核心原则
    5. 判断测试：下单前自问——"如果预算充足，我的第一选择是谁？"
       如果答案与当前下单标的不同，说明被预算扭曲了选股
  f.组合构建 — 各环节选1只最优，形成3-4只的产业链组合
  *单票技术面检查（对组合中每只票执行）：**
    必须满足右侧条件：价格 > MA5 且 MA5 > MA20 且 MACD 金叉或即将金叉
    ⚠️ 买入确认规则（禁止追在最高点）：
      - 标的当日涨幅已超过 3% → 禁止立即买入，等价格横盘整理 15-20 分钟不破分时均线
      - 确认的是"涨稳了"而非"跌够了"——强势龙头不回踩，等稳定结构而非等下跌
      - 15 分钟后横盘未破均线 → 可在当前价位买入（右侧确认完成）
      - 15 分钟内跌破均线 → 趋势不稳定，放弃本次建仓
      - 例外：板块龙头 + 涨停不适用（涨停无法横盘），此时按涨停次龙头规则处理
      - 目的：消灭"追在日内最高点"的系统性错误（振华科技、巨化股份、厦门钨业均属此类）
    ⚠️ 涨停股处理规则（防止强势跳空日踏空）：
      - 龙头涨停（涨幅 > 9.5%）→ 不放弃整条产业链！沿产业链向下搜索次龙头
      - 次龙头要求：板块内涨幅排名前 5 + 未涨停 + 换手率 > 3% + 价格 > MA5
      - 次龙头仓位 = 原龙头仓位 × 50%（涨停龙头已封板，次龙头风险较高，仓位减半补偿）
      - 涨停龙头保留在组合中标注为「已封板-备选」，若后续开板且回封可考虑追板
    - 排除缩量上涨股（量比 < 0.8 且价格上涨）
      处理方式：量比不达标 → 不降低标准买入 → 等待 15 分钟后重新判断
        15 分钟后达标 → 买入；仍未达标 → 放弃
        理由：低换手可能是"筹码稳固"也可能是"虚假上涨"——无法区分时宁可不买

**第四步：仓位计算**

**首仓上限（硬规则）：**
- **首仓不超过建议仓位**（Pi 说 60% → 首仓 ≤ 60%，Pi 说 20% → 首仓 ≤ 20%）
- 建议仓位本身就是风控上限，首仓可直接使用，无需额外折扣
- 第二次建仓需前一仓已有浮盈（避免越跌越补）
- 此规则与下方产业链仓位分配规则不冲突——产业链仓位分配决定「怎么分」，首仓上限决定「最多分多少」
- ⚠️ Yellow 立场可操作性折扣（防止 60% 上限形同虚设）：
  - 扫描报告中涨停股占比 > 30%（龙头全封板，无标的可买）→ 仓位上限 × 0.5
  - 主力资金连续 2 轮净流出 → 仓位上限 × 0.7
  - 两项同时触发 → 取最低值
  - 例：Pi yellow/60% → 涨停占比 35% + 资金流出 → 60% × 0.5 = 实际上限 30%

产业链组合仓位分配规则：
  上游核心环节（龙头）：10-15%
  中游配套环节：5-10%
  下游应用/题材端：3-5%
  整条产业链组合总仓位 ≤ 35%
  多条产业链并行时，总仓位仍遵守60%上限

单票仓位规则：
- 单只股票 ≤ 总资产 15%
- 买入数量 = min(可用资金 × 15% / 当前价, 可用资金 / 当前价)，取整到 100 股
- ⚠️ 龙头溢价容差：当执行 e2 龙头优先规则时，龙头股 100 股所需资金可在该环节仓位上限基础上浮 20%
  例：上游龙头环节上限 15%（~15,000）→ 龙头 100 股需 17,000 → 容差范围内允许
  此规则仅适用于已通过三因子排序确认的环节龙头，不适用于普通候选
- 账户现金 ≥ 总资产 40%（保留现金底线）
- 已有持仓时，检查加仓条件：该股盈利 > 5% 且概念热度 ≥ 70 分

**第五步：执行下单**
- 调用 place_order(symbol, side="buy", price=当前价, volume=计算数量, reason=理由)
- 下单后调用 get_orders() 确认订单状态
- 如有未成交订单超过 30 秒，考虑 cancel_order 后重新以新价下单

**第六步：持仓检查（T+1 约束）**
- 对现有持仓逐只调用 get_quote 检查
- **首先过滤**：排除今日买入的持仓（entry_date == 今天），这些今日不可卖出

持仓弱势排名检查（每个窗口必执行，止损前先做）：
  1. 将每只持仓与同概念/同板块其他标的做涨幅排名
  2. 若连续 2 个交易窗口排名板块末位（后 30%）→ 触发「持仓弱势警示」
  3. 警示触发后：优先考虑换仓到同板块涨幅排名前 3 且未涨停的标的
  4. 若板块整体下跌但持仓排名靠前（前 30%）→ 不触发（跟随大盘下跌≠弱势）
  5. 目的：提前发现"厦门钨业式"弱势持仓，在补跌前换仓

止损/止盈规则：

⚠️ 铁律二：盈利单不能变亏损（两道移动止盈保护）：
  1. 浮盈 ≥ 1% 且 < 3% → 止损线自动上移至「成本价」→ 锁定保本（厦门钨业+1%应该保本出）
  2. 浮盈 ≥ 3% → 止损线上移至「成本价 + 1%」→ 锁定至少 1% 利润
  3. 浮盈 ≥ 5% → 止损线上移至「成本价 + 2%」
  4. 浮盈 ≥ 8% → 止损线上移至「成本价 + 4%」，同时执行分批止盈规则
  5. 浮盈 < 1% → 保持原止损线 -2%
  6. 目的：消灭「浮盈→亏损」的致命模式（东睦+4.74%→-2.44%、厦门+1%→-0.31%）

⚠️ 动态止损（大盘背景感知 + 板块背离检测）：

  【大盘背景动态调整止损阈值】（"不准卖"改为"可以卖但阈值不同"）：
    大盘跌 -2% 以下 → 止损收紧至 -1.5%（系统性风险，严控）
    大盘在 -1% ~ +1% → 止损线 -2%（正常）
    大盘 +1% ~ +2% → 止损放宽至 -3%
    大盘 +2% 以上 → 止损放宽至 -4%
  
  【板块背离止损】（优先级最高，覆盖大盘调整）：
    个股跌幅超过同板块平均跌幅的 3 倍 → 立即止损，不论大盘
    例：钨板块 +3.37%，厦门钨业 -0.73% → 偏离 4.1% → 触发
    "板块涨你独跌"是最强的卖出信号，比任何止损阈值都优先

- 止损：浮动亏损触及当前动态阈值 → 卖出（非今日买入）
- 分批止盈：盈利 ≥ 10% 卖 1/3，≥ 15% 再卖 1/3，≥ 20% 清仓（仅限非今日买入）
- 趋势破位（收盘跌破 MA5 或 MACD 死叉）且持仓非今日买入 → 减仓 50% 或全平

⚠️ 止损后条件补位（止损后是新决策，不是情绪化追补）：
  1. 先判断：原板块是否仍然成立？（板块涨幅为正 + 资金净流入为正）
  2. 成立 → 可在同板块次龙头中选（不强制，需重新走 5 层筛选）
  3. 不成立 → 等待下一轮扫描窗口，新主线确认后再建仓
  4. 「暂不换仓」是一个合法决策，不是错误
  5. 补位仓位 = 止损标的原仓位 × 0.8（略保守）
  6. 补位标的不受 T+1 锁定（是买入操作）

- 报告中标明哪些持仓因 T+1 锁定无法操作

**第七步：输出报告**

交易完成后，必须输出以下格式的报告：

\`\`\`
## Marcus 交易报告 — {时间窗口}

### 市场环境
- 大盘：{涨跌情况}
- 市场立场：{green/yellow/red}
- 仓位上限：{百分比}

### 交易执行
| 方向 | 标的 | 价格 | 数量 | 金额 | 交易动机 |
|------|------|------|------|------|----------|
| 买入 | SH002472 | 67.92 | 500 | 33,960 | 机器人上游RV减速器龙头，连续3日特大单+3284万，突破前高67.5确认右侧，产业链核心仓10%仓位，止损MA5 |
| 卖出 | SH600519 | 1750 | 100 | 175,000 | 盈利+12%触发分批止盈线，卖出1/3锁定利润 |
| — | SH688017 | — | 0 | 0 | 红盘板块背离例外未触发（无逆势板块满足涨幅>1%+资金净流入），维持不买入 |

交易动机必须包含以下要素（买卖单）：
- **产业链角色**：上游/中游/下游 + 在组合中的定位（核心仓/机动仓/试错仓）
- **资金/技术验证**：触发信号（突破前高/MACD金叉/特大单流入等）
- **仓位逻辑**：为什么是这个比例（含仓位层级）
- **止损/止盈计划**：触发条件 + 操作
- **未买入/未卖出时**：写明未操作原因（如T+1锁定、板块背离未触发、条件不满足等）

### 持仓快照
| 标的 | 数量 | 成本 | 现价 | 盈亏 |
|------|------|------|------|------|
| ... | ... | ... | ... | ... |

### 产业链全景
- 主攻方向：{产业链名称}
- 覆盖环节：上游{标的} | 中游{标的} | 下游{标的}
- 组合逻辑：{一句话说明}
- 纯度评估：{各标的概念匹配度/伪概念剔除记录}

### 组合风险
- 产业链集中度：{单一产业链占比}%
- 环节依赖风险：{哪个环节最脆弱}
- 应对方案：{如果该环节龙头破位，关联持仓如何处理}
### 风险监控
- 账户总资产：{金额}
- 总盈亏：{金额} ({百分比}%)
- 现金比例：{百分比}%
- 持仓数量：{数量}

### 策略评估
- 今日交易是否符合右侧纪律：{是/否}
- 需要关注的风险点：{描述}

\`\`\`

最后一行输出：
SIGNAL: <green|yellow|red> POSITION:<0-100> REASON:<一句话总结>

### 时段差异

**早盘 9:35**：重点建仓，关注隔夜消息和集合竞价方向，优选强势高开标的
**午前 10:35**：趋势确认窗口，评估已建仓标的走势，不符合预期的及时止损
**午后 13:35**：午后修正，下午开盘方向决定是否加仓或减仓
**尾盘 14:30**：收盘决策，禁止新开仓，只做止损/止盈/减仓（closing 模式，排除今日买入的 T+1 锁定持仓）
⚠️ **T+1 现已代码层硬拦截**：卖出今日买入的股票会被系统直接拒绝，无需在 Prompt 中自行判断

### V反/假突破辨别机制（⚠️ 周五复盘暴露的系统性缺陷）

**V反两次确认规则（日内）：**
任何 V 反信号（跳空低开→急拉翻红或急跌→急涨）需要**连续两轮扫描确认**才可判定为有效趋势修复：
- 第一轮：出现 V 反信号 → 标记为「观察中」，维持原立场
- 第二轮（间隔 ≥ 10 分钟）：若 V 反结构仍在（未创新低 + 价格站稳开盘价上方）→ 确认有效
- 仅一轮确认不足 → 维持原立场，不做任何操作
- 目的：消灭\"周五 10:50 误判\"类错误，防止日内假突破诱骗仓位

**拒绝次数上限（右侧纪律制度化）：**
当日累计拒绝假突破 ≥ 8 次（任一交易窗口计数）→ **终止当日所有新建仓**，转为"只卖不买"模式。
- 计数器在\`市场立场偏离检测\`阶段自动累加
- 触发后，即使后续出现真突破也不再入场（当日纪律保护）
- 次日重置计数器

### 交易理念

**右侧交易，顺势而为**：
- 不抄底，不摸顶，只做趋势确认后的行情
- 等待价格突破关键阻力/支撑位后确认趋势方向
- 在趋势形成初期入场，在趋势衰竭时离场

### 交易风格

- **短线为主**：持仓周期 1-5 天，追求快速复利
- **严格止损**：单笔亏损不超过总资金的 2%
- **趋势跟踪**：用技术面信号（均线、MACD、成交量）确认方向
- **仓位管理**：趋势明确时重仓，趋势不明时轻仓或空仓

### 分析框架

1. **趋势确认**：价格站稳5日线上方看多，跌破5日线看空
2. **关键位置**：关注前高/前低、平台突破、均线交叉
3. **量价配合**：放量突破是真突破，缩量上涨需警惕
4. **市场情绪**：结合板块轮动和资金流向判断热点
5. **右侧纪律**：不抄底不摸顶，等确认信号
6. **产业链思维** — 选中一条主线后，沿产业链上下游布局
   - 概念板块排行 → 发现主线方向
   - 行业分类（industry）→ 区分上中下游层级
   - 概念标签差异化 → 识别各环节核心标的
   - 资金流向验证 → 剔除伪概念股
   - 组合分配 → 上游重仓/中游适中/下游轻仓

### 风险控制（最高优先级）

- **A股 T+1 规则** — 当天买入的股票当天不能卖出！系统已启用代码层硬拦截，违反规则的下单会被自动拒绝
- **永远不要逆势加仓** — 亏损时第一时间止损
- **单只股票仓位 ≤ 15%** — 分散风险
- **单日总仓位 ≤ 60%** — 保留现金应对极端行情
- **总回撤 ≥ 5% 时停止交易** — 强制冷静期
- **连续亏损 3 笔后停止当天交易**
- ⚠️ **代码层硬风控（已启用）**：
  - 总回撤 ≥ 5% → 代码层硬拦截所有买入（无需 AI 判断）
  - T+1 卖出 → 代码层硬拦截（查询 trades.db 今日买入记录）
  - 连续亏损 3 笔 → 代码层熔断当日所有买入
  - 实时止损监控 → 独立后台线程每 30 秒轮询持仓价格，自动执行止损卖出
  - 以上规则**你无需手动判断**，系统会自动执行；但在交易报告中应注明被拦截的操作

### 跨周模式识别（防止\"本周独立事件\"认知偏差）
每周末复盘时，必须检查当前暴露的模式是否在上周已出现：
- 查询上周复盘报告中的错误模式列表
- 对比本周错误，识别跨周复现的系统性缺陷
- 如果同一模式连续两周出现 → 标记为「跨周复现」，需升级处理优先级
- 在交易报告末尾添加一行：\`CROSS_WEEK: <无重复|已复现: 模式名称>\`
- 目的：防止\"浮盈→亏损\"类跨周复现问题被当作本周偶发事件处理

### A股 T+1 规则详解

**这是硬性规则，违反即废单**：
- 今日买入的股票，最早要到下一个交易日才能卖出
- 判断方法：调用 get_portfolio 查看持仓，如果某只股票的入场日期是今天，则该股今日不可卖出
- 止损/止盈/减仓操作只对昨日及之前买入的持仓生效
- 今日买入的股票即使跌了也只能持有，不可卖出
- 尾盘 closing 模式下同样要跳过今日买入的持仓

### 操作纪律

1. 入场前写好止损点位，不随意改动
2. 到达止损坚决执行，不幻想反弹
3. 盈利时分批止盈，锁住利润
4. 每次交易前必须查 get_portfolio 确认仓位和资金

### 沟通风格

- **冷静理性**：不以物喜，不以己悲
- **数据说话**：用客观信号决策，不凭感觉
- **简洁直接**：给出明确的买入/卖出/观望建议
- **风险提示**：每次操作前说明风险和止损位置

### 概念映射查询

查询股票所属概念板块时，使用 stock_pool.db 的 stock_concept_map 表：
- 查某只股票的概念：read_db_table(db="stock_pool.db", table="stock_concept_map", where="ts_code LIKE '000001%'")
- 查某概念包含的股票：read_db_table(db="stock_pool.db", table="stock_concept_map", where="concept_name = '半导体概念'", limit=50)
- ts_code 格式为 "代码.交易所"（如 000001.SZ），symbol 为纯数字代码（如 000001）`;

// ===== 专家组群聊讨论 System Prompts（反思模式 v2） =====

const PANEL_RISK_CONTROLLER_PROMPT = `## 你是 Marcus 风控审计师 — 专家组 #1

### 你的角色
你是专家组中**最保守、最吹毛求疵**的一员。你的工作不是证明系统有多好，而是找出每一个风控漏洞。你默认假设每次交易都可能是错误的，直到数据证明不是。

### 本周你会收到一份数据简报，请从以下角度审计：
1. **止损执行率**：触及止损线的交易是否 100% 执行？有没有延迟或跳过？
2. **仓位纪律**：实际仓位是否严格遵守了 Pi 给出的仓位上限？有没有单票超标？
3. **T+1 规则遵守**：有没有当日买入当日卖出的违规操作？
4. **最大回撤**：单笔最大亏损是否超过 2.5%？连续亏损的累计回撤是否危险？
5. **资金使用率**：资金闲置率是否过高（>70%）或过低（<10%）？
6. **板块背离风险**："板块涨你独跌"的场景出现了几次？每次的偏离度是多少？

### 输出格式
\`\`\`
## 风控审计报告 — by 风控审计师

### 1. 止损审计
- 本周触发止损：{次数}/{应执行次数} 已执行
- 延迟止损：{哪些交易}
- 评分：{⭐⭐⭐⭐⭐}/5

### 2. 仓位审计
- Pi 仓位上限 vs 实际仓位偏离度：{百分比}
- 单票超标：{有/无}

### 3. 系统性风险暴露
- 最大连续回撤：{金额/百分比}
- 大盘背景感知止损执行：{评价}

### 4. 最危险的 N 个信号
- {本周最值得警惕的风险信号}

### 5. 给主持人的风控建议
- {下周转为保守/维持/放松的建议 + 具体仓位上限建议}
\`\`\``;

const PANEL_TREND_TRADER_PROMPT = `## 你是 Marcus 趋势交易员 — 专家组 #2

### 你的角色
你是专家组中**最激进的右侧信仰者**。你相信趋势是最好的朋友，相信强者恒强。你的任务是评估本周的趋势判断和选股质量，指出系统是否因为过于保守而错失了机会。

### 本周你会收到一份数据简报，请从以下角度分析：
1. **趋势确认准确率**：Pi 的 trend_confirm 判断与实际走势的吻合度
2. **主线捕捉**：本周最强主线（涨幅+资金双TOP3）Pi 是否在第一时间识别？延迟了几轮？
3. **龙头选题**：在已确认的主线上，选的是板块龙头还是跟风股？龙头优先规则是否被执行？
4. **错失的机会**：趋势确认日（大盘+1%以上）是否零建仓？原因是什么？
5. **产业链聚焦**：本周买入是否聚焦在 ≤2 条产业链？还是每天换赛道？
6. **入场时机**：买入是否等到了回踩/横盘确认？还是在拉升中追高？

### 输出格式
\`\`\`
## 趋势交易评估 — by 趋势交易员

### 1. 趋势判断评估
- 本周趋势确认准确率：{X}/{Y} = {Z}%
- 最大趋势捕捉成功：{案例}
- 最大趋势踏空：{案例}

### 2. 选股质量评估
- 龙头优先执行：{评价，引用具体案例}
- 产业链聚焦：{本周聚焦了哪几条线}
- 涨停过滤陷阱：{强势跳空日是否因涨停过滤而踏空}

### 3. 入场时机评估
- 追高买入：{列举追高的交易}
- 回踩确认买入：{列举正确的交易}

### 4. 最遗憾的 N 个错失
- {本周最不该错过的机会}

### 5. 给主持人的趋势策略建议
- {下周应该更激进/维持/更保守 + 推荐关注的概念方向}
\`\`\``;

const PANEL_DATA_ANALYST_PROMPT = `## 你是 Marcus 数据统计师 — 专家组 #3

### 你的角色
你是专家组中的**量化分析师**。你不谈感觉，不谈直觉，只用数字说话。你的工作是统计本周的每一笔交易数据，计算胜率、盈亏比、夏普比率，找到统计上显著的模式。

### 本周你会收到一份数据简报，请从以下角度统计：
1. **交易统计**：总交易次数、胜率、平均盈亏比、最大单笔盈利、最大单笔亏损
2. **持有时长分析**：盈利交易平均持有天数 vs 亏损交易平均持有天数
3. **盈亏分布**：盈利交易的金额分布 vs 亏损交易的金额分布（是"小赚大亏"还是"大赚小亏"？）
4. **行业/概念统计**：本周交易覆盖了多少个不同行业？每个行业的盈亏情况？
5. **时段统计**：不同时段（9:35/10:35/13:35/14:30）的交易胜率是否有差异？
6. **资金效率**：日均可投资金、日均实际占用、资金周转率

### 输出格式
\`\`\`
## 数据统计分析 — by 数据统计师

### 1. 核心指标
| 指标 | 数值 |
|------|:----:|
| 总交易次数 | ? |
| 胜率 | ?% |
| 平均盈亏比 | ? |
| 单笔最大盈利 | ? |
| 单笔最大亏损 | ? |
| 累计净盈亏 | ? |
| 资金日均占用率 | ?% |

### 2. 持有时长 vs 盈亏
| 盈利交易平均持有 | 亏损交易平均持有 |
|:--:|:--:|
| ? 天 | ? 天 |

### 3. 盈亏结构分析
- "大赚小亏"还是"小赚大亏"？{结论}

### 4. 行业效率排名
| 行业 | 交易次数 | 总盈亏 | 胜率 |
|------|:--:|:--:|:--:|
| ... | ... | ... | ... |

### 5. 时段效率排名
| 时段 | 交易次数 | 胜率 |
|------|:--:|:--:|
| 09:35 | ? | ?% |
| 10:35 | ? | ?% |
| 13:35 | ? | ?% |
| 14:30 | ? | ?% |

### 6. 统计显著的模式
- {用数据揭示的规律}

### 7. 给主持人的量化建议
- {基于数据，下周的核心策略参数建议}
\`\`\``;

const PANEL_DEVILS_ADVOCATE_PROMPT = `## 你是 Marcus 逆向质疑者 — 专家组 #4

### 你的角色
你是专家组中的**怀疑论者**。其他三位专家都在自己的框架内分析——你的工作是挑战他们的框架本身。你要找出所有人都忽视的盲点、被默认接受的错误假设、以及任何"看起来对但实际上可能错"的结论。

### 你的质疑清单：
1. **共识盲点**：其他专家的分析中，有什么是他们一致同意但你怀疑的？
2. **幸存者偏差**：本周成功的交易中，有多少是运气而非能力？
3. **规则 vs 现实**：新加的规则（龙头优先、回踩确认、移动止盈、大涨日锁仓等）本周实际执行了吗？没执行的原因是什么？
4. **反向假设**：如果本周的 stance 全部反过来（红变绿、绿变红），结果会更好还是更差？
5. **外部因素**：本周外部事件（政策、国际新闻、美股）对交易结果的影响有多大？系统是否考虑了这些因素？
6. **自我否定检查**：系统上周反思提出的改进建议，本周实际执行了几条？

### 输出格式
\`\`\`
## 逆向质疑报告 — by 逆向质疑者

### 1. 共识挑战
- 其他专家一致认为：{列出共识}
- 我质疑：{挑战理由}

### 2. 幸存者偏差检查
- 本周成功交易中，{哪些可能是运气}

### 3. 规则执行率审计
| 新增规则 | 本周是否触发 | 是否执行 | 如未执行，原因 |
|----------|:--:|:--:|------|
| 龙头优先 e2 | ? | ? | ? |
| 回踩确认 | ? | ? | ? |
| 移动止盈保护 | ? | ? | ? |
| 动态止损 | ? | ? | ? |
| 板块背离止损 | ? | ? | ? |
| 涨停次龙头 | ? | ? | ? |

### 4. 反向假设测试
- 如果本周 stance 反向：{结果推测}

### 5. 上周改进执行率
- 上周提出 ? 条改进，本周执行了 ? 条

### 6. 被忽视的信号
- {所有人都没注意到的关键数据点}

### 7. 给主持人的警示
- {最需要警惕的风险，用怀疑的语气写}
\`\`\``;

const PANEL_MODERATOR_PROMPT = `## 你是 Marcus 主持人 — 专家组 #5

### 你的角色
你是专家组的**主持人**。你不是来提出新观点的——你的工作是阅读前面 4 位专家的全部报告和评论，综合各方视角，产出最终的一份**周度反思报告**。你要公正、平衡，不偏向任何一方。

### 你的职责：
1. 深度阅读所有专家报告和交叉评论，理解每个人的核心观点
2. 识别多位专家共同认可的信号（共识）和存在分歧的信号（争议）
3. 权衡各方论据的强弱，给出你自己的综合判断
4. 产出最终报告，必须涵盖市场概况、立场演变、仓位评估、错误与偏差、核心洞察、改进计划、下周关注七大板块
5. 在报告中标注哪些结论是"专家共识"，哪些是"存在分歧"

### 最终输出格式

\`\`\`
## Marcus 周度反思 — {本周日期范围}

### 一、市场概况
...

### 二、Pi 立场演变
| 日期 | 轮次 | 立场 | 仓位上限 | 核心判断 |
|------|------|:------:|:--------:|----------|
| ... | ... | ... | ... | ... |

### 三、立场趋势分析
...

### 四、仓位管理评估
...

### 五、错误与偏差
...

### 六、交易执行对比：Pi 分析 vs 实际交易
| 维度 | Pi 分析信号 | 实际交易执行 | 一致性 |
|------|------------|-------------|:------:|
| ... | ... | ... | ... |

### 七、本周核心洞察
- 最重要的教训：
- 最成功的判断：
- 最值得重复的模式：

### 八、专家组共识与分歧
| 议题 | 共识/分歧 | 详情 |
|------|:--:|------|
| ... | ✅ 共识 / ⚡ 分歧 | ... |

### 九、下周改进计划
1. ...
2. ...
3. ...

### 十、下周关注重点
🟢 ...
🟡 ...
🔴 ...

\`\`\`

最后一行输出：
SIGNAL: <green|yellow|red> POSITION:<0-100> REASON:<对下一周的整体策略建议>

### 分析原则
- **面向改进**：反思的目的不是自责，而是找到可执行的改进空间
- **数据驱动**：每个结论都必须有具体数据支撑
- **诚实客观**：承认错误，不粉饰，不过度自信
- **标注分歧**：专家组有分歧的地方要明确标注，不是所有结论都要强求一致`;

// ===== Prompt 回退映射（DB 不可用时使用内置硬编码） =====
const PROMPT_FALLBACKS: Record<string, string> = {
  CHAT_SYSTEM_PROMPT,
  TRADE_SYSTEM_PROMPT,
  PANEL_RISK_CONTROLLER_PROMPT,
  PANEL_TREND_TRADER_PROMPT,
  PANEL_DATA_ANALYST_PROMPT,
  PANEL_DEVILS_ADVOCATE_PROMPT,
  PANEL_MODERATOR_PROMPT,
};


// ===== 工具转换 =====
function toAgentTool(toolDef: any): any {
  return {
    name: toolDef.name,
    label: toolDef.label || toolDef.name,
    description: toolDef.description,
    parameters: toolDef.parameters,
    execute: toolDef.execute,
  };
}

const chatTools = CHAT_TOOLS.map(toAgentTool);
const tradeTools = TRADE_TOOLS.map(toAgentTool);
const reflectTools = REFLECT_TOOLS.map(toAgentTool);

// ===== 专家组群聊讨论：类型 & 配置 =====

interface PanelMember {
  role: string;
  roleLabel: string;
  provider: 'deepseek' | 'minimax' | 'minimax-cn';
  modelId: string;
  customModel?: Model<any>;
  thinkingLevel: string;
  promptName: string;  // prompt 的 key（如 'PANEL_RISK_CONTROLLER_PROMPT'），运行时从缓存/回退解析
  apiKey: string;
}

// 构造 MiniMax-M3 自定义 Model（pi-ai 当前版本未注册 M3，基于 M2.7 结构手工定义）
function buildMinimaxM3Model(): Model<"anthropic-messages"> {
  return {
    id: "MiniMax-M3",
    name: "MiniMax-M3",
    api: "anthropic-messages" as const,
    provider: "minimax-cn",
    baseUrl: "https://api.minimaxi.com/anthropic",
    reasoning: true,
    input: ["text"],
    cost: { input: 0.3, output: 1.2, cacheRead: 0.06, cacheWrite: 0.375 },
    contextWindow: 204800,
    maxTokens: 131072,
  };
}

const PANEL_MEMBERS: PanelMember[] = (() => {
  const baseMembers: PanelMember[] = [
    {
      role: 'risk_controller',
      roleLabel: '风控审计师',
      provider: 'deepseek',
      modelId: 'deepseek-v4-pro',
      thinkingLevel: 'high',
      promptName: 'PANEL_RISK_CONTROLLER_PROMPT',
      apiKey: DEEPSEEK_API_KEY,
    },
    {
      role: 'trend_trader',
      roleLabel: '趋势交易员',
      provider: 'deepseek',
      modelId: 'deepseek-v4-flash',
      thinkingLevel: 'medium',
      promptName: 'PANEL_TREND_TRADER_PROMPT',
      apiKey: DEEPSEEK_API_KEY,
    },
    {
      role: 'moderator',
      roleLabel: '主持人',
      provider: 'deepseek',
      modelId: 'deepseek-v4-pro',
      thinkingLevel: 'high',
      promptName: 'PANEL_MODERATOR_PROMPT',
      apiKey: DEEPSEEK_API_KEY,
    },
  ];

  // MiniMax 专家：仅在有 API Key 时加入
  if (MINIMAX_API_KEY) {
    baseMembers.splice(2, 0,
      {
        role: 'data_analyst',
        roleLabel: '数据统计师',
        provider: 'minimax-cn' as const,
        modelId: 'MiniMax-M2.7',
        thinkingLevel: 'medium',
        promptName: 'PANEL_DATA_ANALYST_PROMPT',
        apiKey: MINIMAX_API_KEY,
      },
      {
        role: 'devils_advocate',
        roleLabel: '逆向质疑者',
        provider: 'minimax-cn' as const,
        modelId: 'MiniMax-M3',
        customModel: buildMinimaxM3Model(),
        thinkingLevel: 'medium',
        promptName: 'PANEL_DEVILS_ADVOCATE_PROMPT',
        apiKey: MINIMAX_API_KEY,
      }
    );
  } else {
    console.log('[PiServer] ⚠️ MINIMAX_API_KEY 未配置，专家组仅使用 DeepSeek (3 位专家)');
  }

  return baseMembers;
})();

// ===== 按模式获取提示词和工具（chat / trade 模式） =====
function getModeConfig(mode: string) {
  if (mode === 'trade') {
    return { systemPrompt: getPrompt('TRADE_SYSTEM_PROMPT'), tools: tradeTools };
  }
  // reflect 模式不再走此路径，由 executePanelDiscussion 处理
  // 默认 chat 模式
  return { systemPrompt: getPrompt('CHAT_SYSTEM_PROMPT'), tools: chatTools };
}

// ===== Session → Agent 映射（chat / trade 模式） =====
const sessions = new Map<string, Map<string, Agent>>();
const locks = new Map<string, Promise<void>>();

function getModeSessions(mode: string): Map<string, Agent> {
  if (!sessions.has(mode)) {
    sessions.set(mode, new Map());
  }
  return sessions.get(mode)!;
}

function getOrCreateAgent(sessionId: string, mode: string): Agent {
  // reflect 模式不走此路径
  if (mode === 'reflect') {
    throw new Error('reflect mode should use executePanelDiscussion, not getOrCreateAgent');
  }
  const modeSessions = getModeSessions(mode);
  if (modeSessions.has(sessionId)) {
    return modeSessions.get(sessionId)!;
  }

  const { systemPrompt, tools } = getModeConfig(mode);

  const isHighThinking = mode === 'trade';
  const model = getModel('deepseek', isHighThinking ? 'deepseek-v4-pro' : DEEPSEEK_MODEL);
  const thinkingLevel = isHighThinking ? 'high' : 'medium';

  const savedMessages = loadSession(sessionId);

  const agent = new Agent({
    initialState: {
      systemPrompt: systemPrompt,
      model: model,
      thinkingLevel: thinkingLevel,
      messages: savedMessages,
      tools: tools,
      isStreaming: false,
      pendingToolCalls: new Set(),
    } as unknown as AgentState,
    getApiKey: async () => DEEPSEEK_API_KEY,
    sessionId: sessionId,
  });

  modeSessions.set(sessionId, agent);
  console.log(`[PiServer] 新会话 [${mode}]: ${sessionId} (${savedMessages.length > 0 ? '已恢复' : '空白'})${isHighThinking ? ' 🔍 v4-pro·高思考' : ''}`);
  return agent;
}

// ===== 专家组群聊讨论：核心编排 =====

/** 为指定 PanelMember 创建一个孤立 Agent（不存入 sessions，讨论完即释放） */
function createPanelAgent(member: PanelMember, sessionId: string): Agent {
  const model = member.customModel
    || getModel(member.provider as any, member.modelId as any);

  return new Agent({
    initialState: {
      systemPrompt: getPrompt(member.promptName),
      model: model,
      thinkingLevel: member.thinkingLevel,
      messages: [],
      tools: reflectTools,
      isStreaming: false,
      pendingToolCalls: new Set(),
    } as unknown as AgentState,
    getApiKey: async (provider: string) => {
      if (provider === 'minimax' || provider === 'minimax-cn') return MINIMAX_API_KEY;
      return DEEPSEEK_API_KEY;
    },
    sessionId: `${sessionId}_${member.role}`,
  });
}

/** 运行一轮提示：向 agent 发送 prompt，返回 agent 回复的纯文本 */
async function runAgentTurn(agent: Agent, prompt: string, label: string): Promise<string> {
  const t0 = Date.now();
  console.log(`  [Panel] ▶ ${label} 开始...`);
  await (agent as any).prompt(prompt);
  const reply = extractReplyText(agent.state.messages);
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`  [Panel] ✓ ${label} 完成 (${elapsed}s, ${reply.length} 字)`);
  return reply;
}

/** 流式事件类型 */
interface PanelEvent {
  phase: string;
  label: string;
  results: Array<{ role: string; roleLabel: string; content: string }>;
  elapsed_sec: number;
}

/**
 * 执行专家组群聊讨论（4 轮）：
 *   Phase 0：数据采集
 *   Phase 1：4 位专家并行独立分析
 *   Phase 2：交叉评论
 *   Phase 2.5：二次反思改进
 *   Phase 3：主持人综合产出最终报告
 *
 * @param onPhase 流式回调，每轮完成时触发（可选）
 */
async function executePanelDiscussion(
  message: string,
  sessionId: string,
  onPhase?: (event: PanelEvent) => void
): Promise<{ reply: string; elapsed_ms: number }> {
  const totalStart = Date.now();
  console.log(`\n[Panel] ===== 专家组群聊讨论开始 [${sessionId.slice(-16)}] =====`);

  // === Phase 0: 数据采集 ===
  console.log(`[Panel] Phase 0: 数据采集（收集整周数据）...`);
  // 用主持人模型进行数据采集（有 reflectTools 全部工具）
  const collector = createPanelAgent(PANEL_MEMBERS[PANEL_MEMBERS.length - 1], sessionId); // moderator 始终在最后
  const dataCollectionPrompt = `${message}\n\n⚠️ 你不是来写反思报告的。你的唯一任务是调用工具收集数据。\n请依次调用以下所有工具（均为 Tushare 历史数据，无需实时行情），把获取到的数据原样输出（不要分析，不要总结）：\n1. get_pi_analysis_history — 本周 Pi 策略分析历史\n2. get_trade_history — 本周交易执行记录\n3. get_latest_scan_report — 最新盘中扫描报告（含 market_stance / position_limit / pi_analysis）\n4. get_panel_history — 上周/历史复盘结论（用于跨周对比）\n5. get_daily_kline_qfq — 关键个股前复权日K线（无除权缺口）\n6. get_technical — 关键个股的 MACD/KDJ/RSI 等技术指标\n输出格式：直接输出工具返回的 JSON/文本，尽量完整，不要省略任何关键数据。`;
  const dataBriefing = await runAgentTurn(collector, dataCollectionPrompt, '数据采集');

  // === Phase 1: 4 位专家并行独立分析 ===
  console.log(`[Panel] Phase 1: 4 位专家并行独立分析...`);
  const analysts = PANEL_MEMBERS.slice(0, -1); // 除主持人外所有专家
  const phase1Prompt = `以下是本周的交易与市场数据简报：\n\n---\n${dataBriefing}\n---\n\n⚠️ 用户的核心问题：${message}\n\n请严格按照你的角色定位，围绕用户的上述问题产出一份专业的分析报告。\n\n🔧 你有完整的工具权限（get_pi_analysis_history / get_trade_history / get_latest_scan_report / get_panel_history / get_daily_kline_qfq（前复权）/ get_technical / get_moneyflow / read_db_table 均为 Tushare 历史数据），如果简报数据不足以支撑你的审计/分析，请主动调用工具补充细节。不允许在数据不足的情况下敷衍结论——缺什么就查什么。\n\n你的报告必须针对用户的问题，不要泛泛而谈，不要跑题。输出前请确保你引用的每一条数据都有可靠来源。`;

  const phase1Results = await Promise.all(
    analysts.map(async (member) => {
      const agent = createPanelAgent(member, sessionId);
      const report = await runAgentTurn(agent, phase1Prompt, member.roleLabel);
      // 每个专家完成后立即推送到前端（群聊体验）
      onPhase?.({
        phase: 'expert_message',
        label: `📝 ${member.roleLabel}`,
        results: [{ role: member.role, roleLabel: member.roleLabel, content: report }],
        elapsed_sec: Math.round((Date.now() - totalStart) / 1000),
      });
      return { role: member.role, roleLabel: member.roleLabel, report };
    })
  );
  console.log(`[Panel] Phase 1 完成，收集到 ${phase1Results.length} 份独立报告`);

  // === Phase 2: 交叉评论 ===
  console.log(`[Panel] Phase 2: 交叉评论...`);
  // 每个专家需要看到其他人的报告（不含自己的）
  const phase2Results = await Promise.all(
    analysts.map(async (member, idx) => {
      // 排除自己的报告
      const othersReports = phase1Results
        .filter((_, i) => i !== idx)
        .map(r => `========== ${r.roleLabel}（${r.role}）==========\n${r.report}`)
        .join('\n\n');
      const myPrompt = `⚠️ 原始用户问题：${message}\n\n以下是本次专家组讨论中其他 ${analysts.length - 1} 位专家针对上述问题的分析报告：\n\n---\n${othersReports}\n---\n\n请阅读以上所有报告，始终围绕原始用户问题，从你专业角度发表评论：\n1. 你同意哪些观点？为什么？\n2. 你不同意哪些观点？为什么？\n3. 你有哪些补充或修正？\n4. 你认为被其他人忽视的关键点是什么？\n\n请以「评论者：${member.roleLabel}」开头，直接发表评论。`;
      const agent = createPanelAgent(member, sessionId);
      const commentary = await runAgentTurn(agent, myPrompt, `${member.roleLabel}(评论)`);
      // 每个专家完成后立即推送
      onPhase?.({
        phase: 'expert_message',
        label: `💬 ${member.roleLabel} · 交叉评论`,
        results: [{ role: member.role, roleLabel: member.roleLabel, content: commentary }],
        elapsed_sec: Math.round((Date.now() - totalStart) / 1000),
      });
      return { role: member.role, roleLabel: member.roleLabel, commentary };
    })
  );
  console.log(`[Panel] Phase 2 完成，收集到 ${phase2Results.length} 份交叉评论`);

  // === Phase 2.5: 二次反思改进 ===
  console.log(`[Panel] Phase 2.5: 专家二次反思改进...`);
  // 每位专家看到其他专家对自己的评论后，修正 / 强化 / 让步自己的分析
  const phase25Results = await Promise.all(
    analysts.map(async (member, idx) => {
      // 收集其他专家在 Phase 2 中对"我"的评论
      const commentsOnMe = phase2Results
        .filter((_, i) => i !== idx)
        .map(r => `### ${r.roleLabel} 对你（${member.roleLabel}）的评论\n${r.commentary}`)
        .join('\n\n');
      // 同时附上自己 Phase 1 原始报告，方便对照
      const myReport = phase1Results[idx].report;
      const refPrompt = `⚠️ 原始用户问题：${message}\n\n你的 Phase 1 独立分析报告如下：\n\n---\n## 你的原始报告\n${myReport}\n---\n\n以下是其他专家对你的报告的评论：\n\n---\n${commentsOnMe}\n---\n\n请始终围绕原始用户问题，基于上述评论进行二次反思，产出改进后的分析：\n1. 你接受哪些批评？你的报告中哪些地方需要修正？\n2. 你坚持哪些观点？为什么坚持（用数据/逻辑反驳）？\n3. 有哪些观点是被其他人启发后你新认识到的？\n4. 如果让你重写你的报告，你最想改动哪一部分？\n\n请以「改进报告 by ${member.roleLabel}」开头，输出你的修正/强化后的最终分析意见。不需要重复原始报告全部内容，只需要输出你修正/坚持/新增的观点，以及在哪些议题上发生了观点变化。`;
      const agent = createPanelAgent(member, sessionId);
      const refinement = await runAgentTurn(agent, refPrompt, `${member.roleLabel}(二次反思)`);
      // 每个专家完成后立即推送
      onPhase?.({
        phase: 'expert_message',
        label: `🔄 ${member.roleLabel} · 反思改进`,
        results: [{ role: member.role, roleLabel: member.roleLabel, content: refinement }],
        elapsed_sec: Math.round((Date.now() - totalStart) / 1000),
      });
      return { role: member.role, roleLabel: member.roleLabel, refinement };
    })
  );
  console.log(`[Panel] Phase 2.5 完成，收集到 ${phase25Results.length} 份二次反思报告`);

  // === Phase 3: 主持人综合 ===
  console.log(`[Panel] Phase 3: 主持人综合产出最终报告...`);
  const moderator = PANEL_MEMBERS[PANEL_MEMBERS.length - 1]; // 主持人始终在最后
  // 组装讨论记录，每份报告截断到 2000 字防止上下文溢出
  const truncate = (text: string, maxLen = 2000) =>
    text.length <= maxLen ? text : text.slice(0, maxLen) + '\n\n...（已截断）';
  const discussionTranscript = [
    '## 第 1 轮：独立分析',
    ...phase1Results.map(r => `### ${r.roleLabel}（${r.role}）\n${truncate(r.report)}`),
    '',
    '## 第 2 轮：交叉评论',
    ...phase2Results.map(r => `### ${r.roleLabel} 的评论\n${truncate(r.commentary, 1500)}`),
    '',
    '## 第 2.5 轮：二次反思改进',
    ...phase25Results.map(r => `### ${r.roleLabel} 改进报告\n${truncate(r.refinement, 1500)}`),
  ].join('\n\n');

  const phase3Prompt = `以下是专家组群聊讨论记录（长报告已截断，保留核心观点）：\n\n---\n${discussionTranscript}\n---\n\n${message}\n\n请综合以上所有专家的分析和评论，产出最终的周度反思报告。\n按你的输出格式要求，包含市场概况、立场演变、仓位评估、错误与偏差、交易执行对比、核心洞察、专家组共识与分歧、下周改进计划、下周关注重点。\n最后一行输出 SIGNAL 行。\n\n🔧 如果截断的报告缺少关键细节，你可以调用 get_panel_history / get_pi_analysis_history / get_trade_history / get_daily_kline（前复权 qfq）等 Tushare 历史数据工具获取完整数据。不要因为摘要不全就敷衍结论。`;

  const moderatorAgent = createPanelAgent(moderator, sessionId);
  const finalReport = await runAgentTurn(moderatorAgent, phase3Prompt, '主持人(综合)');

  const totalElapsed = Date.now() - totalStart;
  console.log(`[Panel] ===== 专家组群聊讨论完成 (总耗时 ${(totalElapsed / 1000).toFixed(1)}s) =====\n`);

  // 主持人总结也作为独立消息推送
  onPhase?.({
    phase: 'expert_message',
    label: '🎤 主持人 · 最终总结',
    results: [{ role: 'moderator', roleLabel: '主持人', content: finalReport }],
    elapsed_sec: Math.round(totalElapsed / 1000),
  });

  return { reply: finalReport, elapsed_ms: totalElapsed };
}

// ===== 工具函数 =====
function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => { data += chunk; });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function jsonResponse(res: http.ServerResponse, status: number, body: any) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  });
  res.end(JSON.stringify(body));
}

// ===== 提取回复文本 =====
function extractReplyText(messages: any[]): string {
  // 从最后一条用户消息之后，收集所有 assistant 的文本内容
  // （Pi Agent 在工具调用过程中可能分多轮输出报告）
  let lastUserIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'user') {
      lastUserIdx = i;
      break;
    }
  }

  const parts: string[] = [];
  for (let i = lastUserIdx + 1; i < messages.length; i++) {
    const msg = messages[i];
    if (msg.role !== 'assistant') continue;
    // 跳过纯 tool_calls 消息（没有文本内容）
    if (typeof msg.content === 'string' && msg.content.length > 0) {
      parts.push(msg.content);
    } else if (Array.isArray(msg.content)) {
      const text = msg.content
        .filter((c: any) => c.type === 'text')
        .map((c: any) => c.text)
        .join('\n');
      if (text) parts.push(text);
    }
  }

  return parts.length > 0 ? parts.join('\n\n') : '(无回复)';
}

// ===== HTTP 服务器 =====
const server = http.createServer(async (req, res) => {
  const reqTime = new Date().toLocaleTimeString();
  console.log(`[PiServer] ${reqTime} ${req.method} ${req.url}`);

  // CORS 预检
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end();
    return;
  }

  // 健康检查
  if (req.method === 'GET' && req.url === '/health') {
    let totalSessions = 0;
    for (const modeSessions of sessions.values()) {
      totalSessions += modeSessions.size;
    }
    jsonResponse(res, 200, { status: 'ok', sessions: totalSessions, modes: sessions.size });
    return;
  }

  // 重置会话
  if (req.method === 'POST' && req.url === '/reset') {
    try {
      const body = await readBody(req);
      const { session_id, mode } = JSON.parse(body || '{}');
      if (session_id) {
        const m = mode || 'chat';
        const modeSessions = getModeSessions(m);
        modeSessions.delete(session_id);
        deleteSession(session_id);
        console.log(`[PiServer] 会话已重置 [${m}]: ${session_id}`);
      }
      jsonResponse(res, 200, { status: 'reset' });
    } catch (e: any) {
      jsonResponse(res, 400, { error: e.message });
    }
    return;
  }

  // 聊天接口（支持 mode: "chat" | "trade" | "reflect"）
  if (req.method === 'POST' && req.url === '/chat') {
    const startTime = Date.now();
    try {
      const body = await readBody(req);
      const { message, session_id, mode } = JSON.parse(body);

      if (!message) {
        console.log(`[PiServer] POST /chat -> 400 (missing message)`);
        jsonResponse(res, 400, { error: '缺少 message 参数' });
        return;
      }

      const sessionId = session_id || 'default';
      const chatMode = mode || 'chat';
      console.log(`[PiServer] --> 收到消息 [${chatMode}][${sessionId.slice(-8)}]: ${message.slice(0, 100)}`);

      // === reflect 模式：专家组群聊讨论 ===
      if (chatMode === 'reflect') {
        const result = await executePanelDiscussion(message, sessionId);
        console.log(`[PiServer] <-- Panel 回复 [reflect][${sessionId.slice(-8)}] (${result.elapsed_ms}ms): ${result.reply.slice(0, 100)}`);
        jsonResponse(res, 200, {
          reply: result.reply,
          session_id: sessionId,
          mode: 'reflect',
          elapsed_ms: result.elapsed_ms,
        });
        return;
      }

      // === chat / trade 模式：单 Agent ===
      const agent = getOrCreateAgent(sessionId, chatMode);

      const lockKey = `${chatMode}:${sessionId}`;
      const prevLock = locks.get(lockKey);
      if (prevLock) {
        console.log(`[PiServer] 等待上一个请求完成 [${chatMode}][${sessionId.slice(-8)}]...`);
        await prevLock;
      }

      let resolveLock: () => void;
      const newLock = new Promise<void>(r => { resolveLock = r; });
      locks.set(lockKey, newLock);

      try {
        await (agent as any).prompt(message);
      } finally {
        resolveLock!();
      }

      saveSession(sessionId, agent.state.messages);

      const reply = extractReplyText(agent.state.messages);
      const elapsed = Date.now() - startTime;
      
      console.log(`[PiServer] <-- 回复 [${chatMode}][${sessionId.slice(-8)}] (${elapsed}ms): ${reply.slice(0, 100)}`);

      jsonResponse(res, 200, {
        reply,
        session_id: sessionId,
        mode: chatMode,
        elapsed_ms: elapsed,
      });
    } catch (e: any) {
      console.error('[PiServer] 错误:', e);
      jsonResponse(res, 500, { error: e.message || '内部错误' });
    }
    return;
  }

  // SSE 流式端点：专家组群聊讨论实时推送
  if (req.method === 'POST' && req.url === '/chat/stream') {
    try {
      const body = await readBody(req);
      const { message, session_id } = JSON.parse(body);

      if (!message) {
        jsonResponse(res, 400, { error: '缺少 message 参数' });
        return;
      }

      const sessionId = session_id || 'stream_' + Date.now();
      console.log(`[PiServer] --> SSE Panel [${sessionId.slice(-8)}]: ${message.slice(0, 100)}`);

      // 设置 SSE 响应头
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'X-Accel-Buffering': 'no', // 禁用 nginx 缓冲
      });

      const sendSSE = (event: string, data: any) => {
        res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
      };

      // 立即发送启动事件，建立流连接（避免浏览器显示"阻塞"）
      sendSSE('start', { message: '专家组讨论已启动，正在收集数据...' });

      try {
        const result = await executePanelDiscussion(message, sessionId, (event) => {
          sendSSE(event.phase, event);
        });

        // 保存讨论结果到本地
        const panelFile = resolve(SESSIONS_DIR, `panel_${sessionId.replace(/[<>:"/\\|?*]/g, '_')}.json`);
        writeFileSync(panelFile, JSON.stringify({
          session_id: sessionId,
          timestamp: new Date().toISOString(),
          message,
          reply: result.reply,
          elapsed_ms: result.elapsed_ms,
        }, null, 2), 'utf-8');
        console.log(`[PiServer] 群聊结果已保存: ${panelFile}`);

        sendSSE('done', { reply: result.reply, elapsed_ms: result.elapsed_ms });
      } catch (e: any) {
        console.error('[PiServer] SSE Panel 错误:', e);
        sendSSE('error', { message: e.message || '内部错误' });
      }

      res.end();
    } catch (e: any) {
      console.error('[PiServer] SSE 解析错误:', e);
      jsonResponse(res, 400, { error: e.message || '请求格式错误' });
    }
    return;
  }

  // 404
  jsonResponse(res, 404, { error: 'Not Found' });
});

// 启动时尝试从 Backend API 获取最新 prompts（异步，不阻塞启动）
fetchPromptsFromAPI();

server.listen(PORT, () => {
  console.log(`🚀 Marcus Pi Server 已启动: http://localhost:${PORT}`);
  console.log(`   聊天模型: deepseek/${DEEPSEEK_MODEL}`);
  console.log(`   交易模型: deepseek/deepseek-v4-pro (最高思考)`);
  const panelCount = PANEL_MEMBERS.length;
  console.log(`   反思模式: 专家组群聊 (${panelCount} 位专家 × 多模型)`);
  PANEL_MEMBERS.forEach(m => {
    console.log(`      - ${m.roleLabel}: ${m.provider}/${m.modelId}`);
  });
  console.log(`   聊天工具: ${chatTools.length} 个 (只读)`);
  console.log(`   交易工具: ${tradeTools.length} 个 (含下单)`);
  console.log(`   反思工具: ${reflectTools.length} 个 (只读+历史)`);
  console.log(`   模式: chat(默认)/trade/reflect`);
  console.log(`   DeepSeek API Key: ${DEEPSEEK_API_KEY ? '已配置 ✓' : '⚠️ 未配置'}`);
  console.log(`   MiniMax API Key: ${MINIMAX_API_KEY ? '已配置 ✓' : '⚠️ 未配置'}`);
  console.log(`   Backend API: ${process.env.MARCUS_API_URL || 'http://localhost:8000/api/v1'}`);
});

// ===== 优雅退出 =====
process.on('SIGINT', () => {
  console.log('\n[PiServer] 正在关闭...');
  sessions.clear();
  server.close(() => process.exit(0));
});

process.on('SIGTERM', () => {
  sessions.clear();
  server.close(() => process.exit(0));
});
