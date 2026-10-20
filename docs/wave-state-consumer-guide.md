# 浪型状态消费指南（其他功能要读浪型，照这个来）

> 一句话：读 `data/wave_state.json` → 看 `operation`（+ `mode`）→ **门禁一律走 `wave_gate.check_gate`** → **缺数据一律放行（fail-open）**。
> 写 `2026-10-20`；维护人：接管浪型链的会话。改代码时请同步本文件。

## 1. 三份产物与命名

| 文件 | 谁写 | 语义 |
|---|---|---|
| `data/wave_state.json` | 08:10 调度任务 `wave_ladder_state` | 当前档。`date`=**数据日**（上一交易日收盘）；`session_date`=**服务交易日** |
| `data/wave_state_<YYYY-MM-DD>.json` | 同一任务（`--dated`） | 按**数据日**命名的快照 ⇒ **服务于下一个交易日**（例：`wave_state_2026-10-19.json` 就是 10-20 那份判定 ✓） |
| `data/wave_state_history/<sim8>.json` | 回填/`regen` | 按**模拟日**读的归档（`wave_gate.py:101`）。**回测必须用这个** ✗ 不许用 live 文件回测 |

## 2. 字段（只列可依赖的；生产实测 schema）

| 字段 | 取值 | 能否依赖 |
|---|---|---|
| `date` | `YYYYMMDD` 数据日 | ✓ |
| `session_date` | `YYYYMMDD` 服务交易日 | ✓（2026-10-20 新增；只跳周末，节假日偏一天 ✓）|
| `level` | `d1..d5` / `down` | ⚠️ **只作粗粒度**。**不要**用 `level` 判「大5浪衰竭」✗ —— 4-5 失败也写成 `level=d5 + sub_level=失败5` ✓ |
| `sub_level` | `4-1/4-2/4-3/4-4/4-5/失败5/末段/双头/W底/下跌1浪/C杀/B反…` | ✓ 展示用 |
| **`operation`** | `build / t_only / side / defense / exit` | ✓ **主判据**（是**状态**，不是买信号 ✓ 见铁律 3）|
| `mode` | `自救` / 空 | ✓ 存量处置语义（禁新建 (+) 借反弹减 ✓）|
| `family` / `stage` / `status` | `1/2/3/4/5/down` ｜ `4-3 / down-1 / 5-末段 …` ｜ `confirmed / forming` | ✓ 判断「哪一族、哪一步」用它们（**别**用 `level` 推 ✗）|
| `confidence` | 0~1 | ✓ 弱参考（不建议单独触发动作）|
| `reasons` | 人类可读串 | 展示/日志用 |
| `source` | `wave_ladder` | ✓ 判断「这份是不是新链写的」（回滚期很有用 ✓）|
| `features` / `levels` / `sub_legs` / `rung` | 明细 | 复盘用；**不要**写进业务判据 ✗（内部结构会变）|

## 3. 四条铁律（都是踩过的坑）

1. **fail-open**：文件缺失 / 解析失败 / `source` 不是 `wave_ladder` ⇒ **放行**（不阻断业务 ✓）。
2. **卖类永不拦** ✓：止损、减仓、止盈**不许**被浪型挡住 ✗（`wave_gate.py:229-231` 的语义表已写死 ✓）。
3. **`operation` 是状态、不是买信号** ✓：
   `side`/`t_only` 都**允许卖** ✓；买侧语义走 `wave_gate` 表：
   `build`=放行 ｜ `t_only`=买类拦 ｜ `side`=买类**需人工确认** ｜ `defense`·`exit`=买类拦 ✓。
   （实证：E1b 里 `side` 期间他照样减仓，把 `side` 当买信号命中仅 29% ✗）
4. **长驻进程别永久缓存** ✗（2026-10-20 事故：接口缓存无 TTL ⇒ 卡片色带冻在 10-15 ✗）⇒ ≤60s TTL 或按 mtime ✓。

## 4. 推荐写法（贴这两段就够）

### 4.1 门禁（买类 / 卖类都走它，别自己写 if）
```python
from app.services.wave_gate import ensure_wave, check_gate

w = ensure_wave(as_of=None)              # 读 live；缺失/过期(>10天)会先找归档、再现场补 ✓
g = check_gate(kind="low_buy", wave=w)   # kind: low_buy/high_sell* 等
# → {"allowed": bool, "gate": "ALLOWED|BLOCKED|CONFIRM", "why": str, "text": str, ...}
# 读不到/导入失败 ⇒ 自动 allowed=True（fail-open ✓）
```

### 4.2 档位 → 仓位目标 / 新建意图
```python
from app.services.wolf_discipline import tier_target_pct      # 总仓位目标 %（build 75 / t_only·side 50 / defense 30 ✓）
from app.services.wolf_253_build import choose_intent          # build·side→new_base ｜ t_only→probe(≤3%) ｜ defense·exit→None

op = (w or {}).get("operation")
tgt = tier_target_pct(op)        # 可能 None（未配置 ⇒ 不约束）
intent = choose_intent(op)       # None ⇒ 不新建
```

### 4.3 前端 / 其它语言服务
```
GET /api/v1/wave/index-history?days=250&code=000001.SH
→ { asof, bars[{date,open,high,low,close,amount}],
    waves[{date, session_date, rung, name, level, sub_level, operation, mode, expect, invest, reasons, marks}],
    display_patterns[], last{}, ohlc_source, wave_source, note }
```
· K线优先东财、退本地缓存/CSV（`ohlc_source` 会写明 ✓，`local_csv(csv_close_only)` 时开高低是合成值 ⛔）；
· `waves` 是**实时回放**（带单向锁 ✓），与 live 文件同源 ✓（2026-10-20 起 ✓）。

## 5. 自测清单（新对接必跑）

- 造一个假 `wave_state.json`（`operation=defense`）⇒ 买类应 `BLOCKED`、卖类应 `ALLOWED` ✓；
- `.venv/bin/python -m pytest backend/tests/ -k wave -q` ✓（35 例）；
- 接口：`curl ...` 检查 `asof` == 数据源最后一根 ✓、`waves` 末条 == live 文件 ✓（这条今天抓出了缓存 bug ✓）；
- 故意删掉 wave_state ⇒ 业务应照常放行 ✓（fail-open）。

## 6. 已知缺口（别依赖）

- `ensure_wave` 的「现场补」仍走**旧** `wave_level.judge_wave` ✗（等换成浪型链 ✓）；
- 生产上 `data/wave_state_history/` **目录不存在** ✗（`regen` 只更新已存在的文件 ✓）⇒ 回测走 `_find_archived` 的其它落点；
- `session_date` 只跳周末 ✓，遇节假日偏一天（下一根 K 线出现后自然对齐 ✓）；
- 卡片/接口的 5 分钟进程内缓存 ✓（数据更新后最多 5 分钟可见 ✓）。