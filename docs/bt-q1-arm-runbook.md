# Q1 回测臂 运行手册（Runbook）

> 面向「接手这条回测臂的另一个会话」。读完这一份即可起停、复现、排障、继续改。
> 最后更新：2026-10-18（当晚 23:00 前后）

---

## 0. 一句话

用 `jobs/bt_days.py` 驱动逐日重放，`jobs/bt_prod_run.py` 在每个交易日按 5 分钟 bar 复现
"信号 → 布腿/挂条件 → 监控判定 → 网关风控 → 撮合落库"，账户是 PG 里的模拟盘 `drabq1`，
看板是 `jobs/bt_dashboard.py`（固定端口 8800）。

---

## 1. 起 / 停 / 续跑

```bash
cd /home/fengx/marcus-platform

# 起（从头：脚本内 START=20260105，会先清沙箱）
nohup setsid bash .dsh-tmp/wolfbt/_run_q1_exec.sh \
  > .dsh-tmp/wolfbt/logs/size_run_q1.log 2>&1 < /dev/null &

# 续跑（从上次完成日之后继续）
LAST=$(ls data/_bt_q1/_summary/prod_*.json | sed 's/.*prod_//;s/\.json//' | sort | tail -1)
RESUME_FROM=$LAST nohup setsid bash .dsh-tmp/wolfbt/_run_q1_exec.sh \
  > .dsh-tmp/wolfbt/logs/size_run_q1_cont.log 2>&1 < /dev/null &

# 看板（独立 setsid，避免被"重启臂"的 killpg 带走）
nohup setsid .venv/bin/python jobs/bt_dashboard.py --port 8800 \
  --root data/_bt_q1 --log .dsh-tmp/wolfbt/logs \
  > .dsh-tmp/wolfbt/logs/dash_8800.log 2>&1 < /dev/null &

# 停（注意：**不要**用 pkill -f "bt_days.py"，会匹配到自己的命令行把自己杀掉）
python3 -c "
import os, signal
p1='jobs/bt_'+'days.py'; p2='bt_'+'prod_run.py'
me, pp = os.getpid(), os.getppid(); n=0
for d in os.listdir('/proc'):
    if not d.isdigit(): continue
    p=int(d)
    if p in (me,pp): continue
    try: c=open('/proc/%s/cmdline'%d,'rb').read().decode('utf-8','replace').replace(chr(0),' ')
    except Exception: continue
    if p1 in c or (p2 in c and 'bt_dashboard' not in c):
        try: os.killpg(int(open('/proc/%s/stat'%d).read().rsplit(')',1)[1].split()[2]), signal.SIGTERM)
        except Exception:
            try: os.kill(p, signal.SIGTERM)
            except Exception: pass
        n+=1
print('stopped', n)"
```

**端口**：`13507` as-of API（臂内起）、`8800` 看板（固定，按 `/api/arm` 判断当前是哪个臂）、
`13506` 内部数据口、PG `5433`。

---

## 2. 关键路径

| 名称 | 路径 |
|---|---|
| 臂脚本（**含所有覆盖式 env**） | `.dsh-tmp/wolfbt/_run_q1_exec.sh` |
| 口径开关库（pins） | `jobs/bt_env_pins.sh` |
| 沙箱 | `data/_bt_q1/<YYYYMMDD>/`（分钟档 `stock_5min_<code6>.json`） |
| 每日产出 | `data/_bt_q1/_summary/prod_<day>.{log,json}`、`legs_switch.jsonl` |
| 驱动日志 | `.dsh-tmp/wolfbt/logs/size_run_q1*.log` |
| 看板日志 | `.dsh-tmp/wolfbt/logs/dash_8800.log` |
| T 仓状态 | `data/t_sleeve_state.json`（**跨日持久**，按账户+代码） |
| 数据库 | `postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading` |

**重要**：`_run_q1_exec.sh` 里 `export` 的变量会**覆盖** pins。改 pins 前先
`grep <开关名> .dsh-tmp/wolfbt/_run_q1_exec.sh`，历史上已两次被它覆盖
（`WOLF_AMBUSH_VIA_AI`、`STOCK_CONFIRM_MAX`）。

---

## 3. 执行架构（自上而下）

```
_run_q1_exec.sh
  ├─ 起 as-of API (13507) / 看板 (8800)
  └─ jobs/bt_days.py --start/--end --root --account drabq1
        └─ 每个交易日一个子进程（**环境变量在此刻继承 ⇒ 改 env 需重启或以新日生效**）
             ├─ bt_run_pinned.py       把 tushare_relay 钉到本地 SQLite（as-of 只读 ≤ 当日）
             ├─ bt_day_legs_switch.py  08:18 阶段：跑选股/判级/布腿
             │     ├─ apps/main_line/stock_confirm_judge.py   ← **埋伏候选在这里生成**
             │     └─ apps/main_line/switch_builder.py       ← 埋伏候选并入买入列表
             ├─ bt_day_legs.py         09:20 阶段
             └─ bt_prod_run.py         盘中 5 分钟 bar 重放（`checks=(...)` 白名单逐个调）
                   └─ t_monitor.TMonitor
                         ├─ _check_plan_triggers / _round     条件腿
                         ├─ _check_wolf_t_rules               狼大做T腿
                         ├─ _check_t_sleeve                   ★ T 仓轮动（本手册 §4.2）
                         └─ …（settle_/defensive/board_half/profit_take …）
                               └─ t_gateway.gateway_execute   唯一放行者（30+ 道闸）
                                     └─ paper_engine → PG（paper_trades/t_triggers）
             └─ 触发消费：`--agent on` ⇒ bt_agent_loop 唤醒 agent 决策；
                机械腿（`BT_AGENT_AUTO_KINDS`）走 `_exec_mechanical` **直接调网关**
```

**关键：`bt_prod_run.py` 里有一份 `checks=(...)` 白名单**，新增的 `_check_*` 必须加进去，
否则在臂里永远不会被调用（这个坑踩过一次：`_check_t_sleeve` 曾因此 0 行日志）。

---

## 4. 当前交易口径（全部有语料依据）

### 4.1 埋伏腿（wolf_ambush_buy）

| 环节 | 现状 | 开关 / 位置 |
|---|---|---|
| 候选池 | 判级池 `STOCK_CONFIRM_MAX=24`；埋伏池 = **跳过前排 3 名之后的全部** | `stock_confirm_judge.py` `_pool0 = _ordered[_K:]` |
| 选股 | `WOLF_AMBUSH_PICK='r60'`（回退值；`lowpos` 因"低位与高弹性冲突"已验证不可用） | pins |
| 弹性筛 | `max(amp20≥1.2, **amp120≥2.5**)`（"波动基因"） | `WOLF_AMBUSH_MIN_AMP20/AMP120` |
| 主题排除 | 三大运营商代码 + 主题关键词「运营商/通信服务/公用事业」 | `WOLF_AMBUSH_THEME_EXCLUDE_CODES` / `..._DENY` |
| 每日上限 | 3 只，纯市值排序 | `WOLF_AMBUSH_DAY_MAX=3` / `RANK_KEY=mv` |
| 单笔规模 | **8%**（≈2 万 ⇒ 高价股 200 股，T 才有空间） | `WOLF_AMBUSH_SIZE_PCT=8` |
| 桶上限 | **40%**（未转正埋伏仓位成本值上限；20% 时实测最多 2~3 只） | `WOLF_AMBUSH_BUCKET_PCT=40` |
| 加仓放大 | **关**（NGA：「越跌越买？怎么可能」） | `WOLF_AMBUSH_SIZE_TIER_STEP=0` |
| 时点闸 | 埋伏**豁免**「不在下跌中买」，且 monitor 与 gateway **两处都放** | `WOLF_AMBUSH_EXEMPT_CONDTIMING=1` + `WOLF_FALLING_GATE_EXEMPT_LEGS='wolf_ambush_buy'` |
| 去重 | 同票不重复埋伏 / 同票同日一仓 | `WOLF_AMBUSH_HOLD_DEDUPE=1` / `AMBUSH_ONE_PER_DAY` |
| 持有与离场 | 50 日 / 宽止损 −20% | `WOLF_AMBUSH_HOLD_DAYS=50` / `STOP_PCT=20` |

### 4.2 做 T（T 仓轮动，wolf_t_sleeve_sell / _buy）

| 环节 | 现状 | 开关 |
|---|---|---|
| T 仓额度 | 持仓 × 20%，**至少 1 手** | `WOLF_T_SLEEVE_BAND=1` / `PCT=20` / `T_SLEEVE_MIN_LOT=1` |
| 方向 | **正T（先买后卖）+ 倒T（先卖后买）双向** | `t_sleeve.decide(..., plan=(buy,sell))` |
| 定价 | 盘前/当天首次生成**计划带**；数值带 = 昨收 ×(1±k)，k=clamp(振幅×0.45, 0.8%, 2.5%)；T入价取 `max(计划带, MA5)` | `WOLF_T_PLAN=1` |
| agent 定价 | **已关**（LLM 空闲后首调 ~59 秒，超时刷屏） | `WOLF_T_PLAN_AGENT=0` |
| 时间窗 | **全天**（语料按小时统计：10 点 23 条 / 11 点 12 条 / 13 点 8 条 ⇒ 他全天做 T） | `WOLF_T_SLEEVE_WINDOWS=''` |
| 主升浪 | **不做 T**（昨收 > MA20×1.03 且 MA5>MA10>MA20） | `WOLF_T_SKIP_UPTREND=1` |
| 买前看图形 | 分时"缩转放 / 缩量止跌"才买（agent 判定；失败则 **wait**） | `WOLF_T_BUYBACK_AI=1`（≤2 次/票/天） |
| 未回补 | **14:25 仍"已卖未回补" ⇒ 按现价接回**（语料 2016-01-29） | `WOLF_T_BUYBACK_FORCE=1` / `_HHMM=1425` |
| 止盈= T 出 | 当天任一卖出（止盈/兑现/高抛）即视为 T 出；**下单前再查一次**防同秒双卖 | `t_monitor._check_t_sleeve` + `bt_agent_loop._exec_mechanical` |
| 正T 卖出量 | 卖"本轮买入全量"（不是每天 100） | `bought_qty` |
| 配额豁免 | T 回补**豁免「加仓口径闸」**（回补 ≠ 加仓） | `WOLF_T_SLEEVE_BUY_EXEMPT_ADDON=1` |
| 状态持久化 | `data/t_sleeve_state.json`（跨日保留 `sold/last_sell_px/sold_day`，≤5 天） | `t_sleeve.save_state` |
| 每日期望 | 数值带实测（dec1 231 票日）：+2% 目标 ⇒ 91% 的日子有 T | — |

### 4.3 底仓与容量

`WOLF_BASE_FLOOR_PCT=80`（底仓目标）、`WOLF_FLOOR_RECOMPUTE=1`（旧口径"整仓锁死"自动按 80% 重算）、
`WOLF_T_EXCLUDE_FROM_FLOOR=1`（T 买回不计入底仓 floor）、`WOLF_T_SLEEVE_MIN_LOT=1`。

### 4.4 时段窗

`WOLF_TW_DECISION_ONLY=1` —— 时段窗只约束"决策/挂单"，**被动单可在窗口外成交**
（语料：「挂被动单…不盯分时」）。

---

## 5. 今天已修的关键 bug（别再踩）

1. **埋伏候选恒为 0**：`_ordered[MAX_STOCKS-_K:]`（第 8 名之后）⇒ 判级池只有 5~10 只时切片为空。
   现改为 `_ordered[_K:]` + 判级池 24。
2. **同一道闸拦两次**：monitor 按 `WOLF_AMBUSH_EXEMPT_CONDTIMING` 放行，但 gateway 的 BUY_TIMING
   只认 `WOLF_FALLING_GATE_EXEMPT_LEGS` ⇒ 必须两处都配。
3. **T 卖出状态写 `/app/data` 失败且静默**（`DATA_DIR` 在臂里根本没设）⇒ 每天重卖、永不回补。
   现写 `<repo>/data/t_sleeve_state.json` 且失败会打印。
4. **T 回补被"加仓口径闸"拦**（"创新高买入已达 2 次"）⇒ 已加豁免。
5. **同一秒双卖**（止盈 + T 仓轮动同价）⇒ 状态改为"下单前再查当天已卖"。
6. **`_check_t_sleeve` 不在 `checks` 白名单** ⇒ 臂里从不执行。
7. **`_run_q1_exec.sh` 覆盖 pins**（`WOLF_AMBUSH_VIA_AI`、`STOCK_CONFIRM_MAX`）⇒ 改配置必先 grep。
8. **宽 except 吞掉自己的 bug**（NameError/语法错都被 `except: pass` 掩盖）⇒ 新增闸必须打印失败原因。
9. **分钟档目录盲扫** `data/_bt_*` 会撞 `data/_bt_pg`（只读）和 `.csv.gz`（非目录）。
   现只遍历"含当天子目录"的根，并逐目录吞错。
10. **`pkill -f "bt_days.py"` 会杀掉自己**（命令行自匹配）⇒ 用上面的 python 片段。

---

## 6. 已知未解决 / 下一步

- **T 买（回补）验证**：`wolf_t_sleeve_buy` 历史 executed 仅 1、blocked 6（全是加仓闸，已修）。
  需在新的一天确认第一笔回补落地。
- **埋伏成交放大验证**：`STOCK_CONFIRM_MAX=24` 需下个交易日生效后观察候选/建仓笔数。
- **`lowpos` 选股路**：与高弹性天然冲突（低位股波动率已塌），当前回退 `r60`；
  若要做，正确方向是"按**主题/方向**的弹性筛"（需主题成分股全量数据）。
- **速度**：`--agent on` 约 10~20 分钟/天；日均 LLM 调用量大。若要快，可 `--agent off`（建仓类即不过 AI）。
- **`WOLF_AMBUSH_VIA_AI` 是死开关**（外层 `WOLF_BUILD_VIA_AI` 未开时无效），要么删要么改判据。

---

## 7. 验证手册

```bash
# 臂存活 / 进度
pgrep -af "jobs/bt_days.py"; ls data/_bt_q1/_summary/prod_*.json | wc -l
tail -3 .dsh-tmp/wolfbt/logs/size_run_q1.log

# 看板
curl -s http://127.0.0.1:8800/api/arm

# 关键日志
grep -hoE "\[TSleeve\][^|]{0,60}"      data/_bt_q1/_summary/prod_2026*.log | tail
grep -hoE "\[TSleeve计划\][^|]{0,60}"  data/_bt_q1/_summary/prod_2026*.log | tail
grep -hoE "(埋伏池|弹性筛|池诊断)[^|]{0,60}" data/_bt_q1/_summary/prod_2026*.log | tail
grep -c "timed out" data/_bt_q1/_summary/prod_2026*.log

# 成交与触发
export DATABASE_URL='postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading'
.venv/bin/python -c "
import psycopg2
cn=psycopg2.connect('$DATABASE_URL'); cur=cn.cursor()
cur.execute(\"select trade_date,direction,symbol,volume,round(price::numeric,2),left(coalesce(reason,''),44) from paper_trades where account_id='drabq1' order by id\")
for r in cur.fetchall(): print(r)
cur.execute(\"select event_type,status,count(*) from t_triggers where account_id='drabq1' group by 1,2 order by 3 desc limit 12\")
print(cur.fetchall())"
```

**清库重跑**（谨慎，会丢当日之后的全部结果）：

```bash
.venv/bin/python -c "
import psycopg2
cn=psycopg2.connect('postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading'); cur=cn.cursor()
for tb in ('paper_trades','t_conditions','t_triggers','t_daily_state','paper_daily_snapshot'):
    try: cur.execute('delete from %s where account_id=%%s' % tb, ('drabq1',)); cn.commit()
    except Exception: cn.rollback()"
rm -rf data/_bt_q1/2025* data/_bt_q1/2026* data/_bt_q1/ambush_promoted.json \
       data/_bt_q1/t_plan_*.json data/t_sleeve_state.json data/ambush_candidates.json
```

---

## 8. 语料依据（改口径前先读）

- `docs/wolf-daily-log-*.md`：做 T 701 处 / 正T 158 / 反T 91 / T仓 32 / 回补 59。
  代表性原话：
  - 「**分不清做T仓位和底仓的区别就是『贪』**」
  - 「当日只做 **上午 9.45-10.00 下午 2.00-2.30** 这两个时间段的交易」（**某一天的计划**，非通则；
    按小时统计做 T 条目：10 点 23 / **11 点 12** / **13 点 8** / 14 点 13 ⇒ 他全天做 T）
  - 「**主升浪中明确说"不做T"**」
  - 「**转折点就是缩转放的那一瞬间**」「缩量太快想做反T但**忍住**；**没量绝对不能买**」
  - 「早盘 20.34 出了…看见**跌不下去**…赶紧 20.2 **接回来了**」
  - 「**把减掉的标在均线附近补回**」「**反弹幅度超过 2 个点就开始 T 出去**」
  - 「**低位方向：找辨识度最高的老龙头埋伏**」「**日均波动 2 个点的品种没兴趣**」
  - 「越跌越买？怎么可能，肯定是根据指数趋势做高抛低吸」「**我没有兴趣增加仓位**」
- NGA 一手帖（优先级高于 docs 二次整理）：`tid=47288722`（1263 层，用 `nga_read_post` 读）。
