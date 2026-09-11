# -*- coding: utf-8 -*-
"""wolf_discipline.py — 狼大纪律规则 (2026-09-03 新增)
① 周末降仓: 周五尾盘/午后若仓位占比>=阈值 → 降低 T 仓约一半(保留底仓)
② 板上减半止盈: 持仓当日触及/接近涨停(10%板→>=9.5%, 20%板→>=19.5%) 且本轮浮盈>=阈值 → 板上减半锁定
纯函数模块, 供 trade_graph 注入 context + 合规检查; 不直接下单。
"""
import os, json

_CFG_CACHE = {"at": 0.0, "cfg": None, "src": ""}
_CFG_TTL = float(os.getenv("WOLF_DISCIPLINE_CFG_TTL", "60"))   # 秒；配置查询频繁(每轮调用), 不能每次打 DB
# DB 熔断：连续失败后在窗口内不再尝试（本地无 PG / 生产 DB 抖动时，绝不能把监控线程拖住）
_CFG_DB_BREAK = {"until": 0.0, "fails": 0, "warned": False}
_CFG_DB_BREAK_SEC = float(os.getenv("WOLF_DISCIPLINE_CFG_DB_BREAK", "300"))


def deep_merge(base, over):
    """**递归**合并配置（dict 套 dict 也要逐层合）。

    为什么必须递归：`position_cap.tier_targets` 是嵌套 dict，浅合并
    (`{**base, **over}`) 会把整块 `tier_targets` 用局部值**整体替换** ——
    例如只传 `{"tier_targets": {"build": 88}}` 会让 defense/t_only/side/exit **全部消失**
    （单测 test_section_merge_keeps_other_keys 抓到的真实缺陷，正是"改一处丢一片"的同款）。
    """
    out = dict(base or {})
    for k, v in (over or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _cfg_file():
    """文件兜底：先 DATA_DIR/wolf_discipline.json（运行时那份），再 config/wolf_discipline.json。"""
    out = {}
    for p in (os.path.join(os.environ.get("DATA_DIR", "data"), "wolf_discipline.json"),
              os.path.join(_ws_root(), "config", "wolf_discipline.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d:
                out = d
                break
        except Exception:
            continue
    return out


def _ws_root():
    try:
        from app.config import get_settings
        return str(get_settings().workspace_path)
    except Exception:
        return os.environ.get("MARCUS_WORKSPACE", "/app")


def _cfg_db_read():
    """从 Postgres 读 wolf_discipline_config(id=1) → dict；失败/空/熔断中返回 None。

    开关 `WOLF_DISCIPLINE_CFG_DB=0` → 完全跳过 DB（回退"文件/内置默认"的旧行为，也是单测前提）。
    """
    import time as _t
    if os.getenv("WOLF_DISCIPLINE_CFG_DB", "1").strip() in ("0", "false", "no"):
        return None
    if _t.time() < _CFG_DB_BREAK["until"]:
        return None
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT cfg_json FROM wolf_discipline_config WHERE id = 1"
            )).mappings().first()
            if not row:
                return None
            v = row.get("cfg_json")
            d = json.loads(v) if isinstance(v, str) else dict(v or {})
            _CFG_DB_BREAK["fails"] = 0
            _CFG_DB_BREAK["warned"] = False
            return d if isinstance(d, dict) and d else None
        finally:
            db.close()
    except Exception as e:
        _CFG_DB_BREAK["fails"] += 1
        _CFG_DB_BREAK["until"] = _t.time() + _CFG_DB_BREAK_SEC
        if not _CFG_DB_BREAK["warned"]:
            _CFG_DB_BREAK["warned"] = True
            print(f"[wolf_discipline] 配置读库失败({_CFG_DB_BREAK['fails']}次): {str(e)[:90]} "
                  f"→ {_CFG_DB_BREAK_SEC:.0f}s 内改用文件/默认(熔断)")
        return None


def _cfg_db_write(cfg, updated_by=""):
    """整份写回（UPSERT）。返回 bool。熔断中/开关关闭 → 直接跳过（只落文件）。"""
    import time as _t
    if os.getenv("WOLF_DISCIPLINE_CFG_DB", "1").strip() in ("0", "false", "no"):
        return False
    if _t.time() < _CFG_DB_BREAK["until"]:
        return False
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO wolf_discipline_config (id, cfg_json, updated_at, updated_by) "
                "VALUES (1, :cfg, now(), :by) "
                "ON CONFLICT (id) DO UPDATE SET cfg_json = EXCLUDED.cfg_json, "
                "updated_at = now(), updated_by = EXCLUDED.updated_by"
            ), {"cfg": json.dumps(cfg, ensure_ascii=False), "by": str(updated_by or "")[:64]})
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[wolf_discipline] 配置落库失败: {str(e)[:120]}")
        return False


def save_cfg(cfg, updated_by="api"):
    """写回配置（DB 为唯一事实来源）并刷新缓存。文件**同时**落一份作为离线兜底。"""
    global _CFG_CACHE
    ok = _cfg_db_write(cfg, updated_by)
    try:
        p = os.path.join(os.environ.get("DATA_DIR", "data"), "wolf_discipline.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    _CFG_CACHE = {"at": 0.0, "cfg": None, "src": ""}
    return ok


def config_source():
    """当前生效配置来自哪里：db / file / default（诊断用）。"""
    _cfg()
    return _CFG_CACHE.get("src") or "?"


def _cfg():
    """狼大纪律配置：**DB(wolf_discipline_config) → 文件 → 内置默认**，带 TTL 缓存。

    2026-09-11 落库：此前 config/ 与 DATA_DIR/ 两份 json 容易改漏一份（当天真实踩到），
    DB 成为唯一事实来源；空表时**自动用文件（再退默认）播种**，保证首次上线行为不变。
    DB 不可用 → 用缓存/文件（fail-open，不抛、不改变配置语义）。
    """
    default = {
        "weekend_de_risk": {"enabled": True, "th_ratio": 0.5, "reduce_to": 0.5,
                            "windows": ["late_morning", "afternoon", "closing"]},
        "board_half": {"enabled": True, "min_float_pct": 3.0,
                       "board_threshold_pct": 9.5, "board_threshold_20": 19.5},
        # ③ 小赚兑现(2026-09-10 P0-3): 浮盈>=min_float_pct → 减 reduce_ratio 锁定。
        # 依据: 回测 T+5 收盘胜率 47% → 加 +3% 止盈 53%; rs>0 ∧ +3% 止盈 → 胜率 56%/中位 +1.01%。
        # 默认 enabled=False: 该规则会新增一条高频卖腿, 线上影响面大于选择层闸(rs),
        # 需先 dry-run 观察触发频次/与 board_half·defensive·roundtrip 的叠加再开启。
        "profit_take": {"enabled": False, "min_float_pct": 3.0, "reduce_ratio": 0.5},
        # ④ 常态仓位纪律(2026-09-10 P2-5)
        # 狼大**有原话**的: 2026-03-06「现在就是70%仓位」→ 总仓位上限 70%。
        # 狼大**无原话**的: 单票上限 / 集中度(前3大) / 底仓与T仓结构上限 → 属系统自设,
        #   默认一律 0(=不启用), 必须显式配置才生效, 以免冒充狼大规则(与 §5.2 清理同一条纪律)。
        # 2026-04-14「50%的底仓 30%左右做日内…剩下20%」是**他本人的持仓结构描述**, 非规则, 故默认关。
        # ④ 常态仓位纪律 + 分档(2026-09-11 校正)
        # **分档有原话**: 2026-01-17「主升趋势就75%以上 然后盘中满仓滚动啊 调整就50%
        #   有风险就30% 下跌趋势就不做」→ tier_targets（按本仓 operation 语义映射, 属外推, 见 _mapping）
        # **下限有原话**: 2025-08-11「这个位置 仓位低于55% 日内分时低于80%都是不太合适」
        #   → tier_floor（原话带"这个位置"限定 → 只在结构完好档 build/side/t_only 生效）
        # **原 total_max_pct=70 的依据被复核推翻**: 2026-03-06「现在就是70%仓位」是**他当时的状态描述、
        #   不是上限规则**, 且与"主升趋势就75%以上…盘中满仓滚动"直接冲突 → 置 0=不再额外限制, 由分档取代。
        # 系统自设项(单票/集中度/底仓T仓结构上限)默认一律 0=不启用, 不冒充狼大规则。
        "position_cap": {"enabled": True, "tier_enabled": True,
                         "tier_targets": {"build": 75.0, "t_only": 50.0, "side": 50.0,
                                          "defense": 30.0, "exit": 50.0},
                         # 下限**只挂 build 档**：原话带"这个位置"限定且他当时明确看多；
                         # 若也挂 t_only/side 会与"调整就50%"的目标(50%)数学互斥(55>50) → 自相矛盾。
                         "tier_floor": {"build": 55.0, "t_only": 0.0, "side": 0.0,
                                        "defense": 0.0, "exit": 0.0},
                         "total_max_pct": 0.0,
                         "single_max_pct": 0.0, "top3_max_pct": 0.0,
                         "base_max_pct": 0.0, "t_max_pct": 0.0},
    }
    import time as _time
    now = _time.time()
    if _CFG_CACHE["cfg"] is not None and (now - _CFG_CACHE["at"]) < _CFG_TTL:
        return _CFG_CACHE["cfg"]

    db = _cfg_db_read()
    if db:
        merged = deep_merge(default, db)
        src = "db"
    else:
        # DB 没有/不可用 → 用文件；若文件有内容则**回种**DB（首次上线/迁移自动完成，行为不变）
        f = _cfg_file()
        merged = deep_merge(default, f)
        src = "file" if f else "default"
        if f and _cfg_db_write(merged, updated_by="autoseed"):
            src = "db(seeded)"
    _CFG_CACHE.update({"at": now, "cfg": merged, "src": src})
    return merged

def _portfolio(portfolio):
    if portfolio is None:
        return None
    if isinstance(portfolio, str):
        try:
            return json.loads(portfolio)
        except Exception:
            return None
    return portfolio

def weekend_de_risk(portfolio, now, window=None, cfg=None):
    """周五 + 指定窗口 + 仓位占比>=th_ratio → active。
    返回 {active, ratio, threshold, reduce_to, reason, directive}。"""
    cfg = cfg or _cfg(); w = cfg.get("weekend_de_risk", {})
    if not w.get("enabled"):
        return {"active": False, "reason": "rule_disabled"}
    if now.weekday() != 4:
        return {"active": False, "reason": "not_friday"}
    if window and window not in (w.get("windows") or []):
        return {"active": False, "reason": "window_not_match"}
    pos = _portfolio(portfolio)
    if not pos:
        return {"active": False, "reason": "no_portfolio"}
    cash = float(pos.get("cash") or 0)
    total = float(pos.get("total_asset_market") or pos.get("total_asset") or 0)
    if total <= 0:
        return {"active": False, "reason": "no_asset"}
    ratio = (total - cash) / total
    th = float(w.get("th_ratio") or 0.5)
    active = ratio >= th
    return {"active": active, "ratio": round(ratio, 3), "threshold": th,
            "reduce_to": w.get("reduce_to", 0.5),
            "reason": "周五+仓位%.1f%%>=%.1f%%" % (ratio * 100, th * 100) if active else "仓位未达阈值",
            "directive": ("⚠️ 周末降仓：今日周五，仓位%.1f%%≥阈值%.1f%%。请于尾盘前将 T 仓(持仓-100的T部分)降低约一半，"
                          "保留底仓不卖，目标总仓位≤%.0f%%。不得新开仓/加仓。" % (ratio * 100, th * 100, w.get("reduce_to", 0.5) * 100)) if active else ""}

def board_half(portfolio, now, cfg=None, quotes=None):
    """板上减半: 对每个持仓判断是否接近涨停且浮盈达标。
    quotes: {symbol: {'current':..,'pre_close':..}} 可选; 无则返回含所有持仓的指令(由agent用实时行情判断)。
    返回 {active_sells:[{symbol,reason}], directive, enabled}。"""
    cfg = cfg or _cfg(); b = cfg.get("board_half", {})
    if not b.get("enabled"):
        return {"active_sells": [], "directive": "", "enabled": False}
    pos = _portfolio(portfolio)
    pos_list = (pos or {}).get("positions") or []
    min_float = float(b.get("min_float_pct", 3.0))
    t10 = float(b.get("board_threshold_pct", 9.5))
    t20 = float(b.get("board_threshold_20", 19.5))
    q = quotes or {}
    sells = []
    for p in pos_list:
        sym = str(p.get("symbol", ""))
        cost = float(p.get("avg_cost") or 0)
        vol = float(p.get("volume") or 0)
        if cost <= 0 or vol <= 0:
            continue
        cur = None
        if sym in q:
            cur = float(q[sym].get("current") or 0)
        if cur is None:
            continue  # 无实时价则不硬判, 由 agent 用实时数据
        pre = float(q[sym].get("pre_close") or 0)
        gain_pct = (cur / pre - 1) * 100 if pre else 0
        float_pct = (cur / cost - 1) * 100 if cost else 0
        # 判别板: 代码 30/68 开头 → 20% 板, 其余 10% 板
        is20 = any(sym.startswith(p) for p in ("30", "68", "SZ30", "SZ68", "SH68"))
        thr = t20 if is20 else t10
        if gain_pct >= thr and float_pct >= min_float:
            sells.append({"symbol": sym, "gain_pct": round(gain_pct, 2), "float_pct": round(float_pct, 2),
                          "action": "board_half_sell", "reason": "今涨%.2f%%(≥%.0f%%板)且浮盈%.2f%%≥%.0f%% → 板上减半锁定" % (gain_pct, thr, float_pct, min_float)})
    directive = "⚠️ 板上减半：对持仓中『今日触及/接近涨停(10%%板≥%.1f%%, 20%%板≥%.1f%%) 且 本轮浮盈≥%.1f%%』的标的 → 减半锁定(卖出持仓的一半, 底仓/芯片类按 T 仓处理)。" % (t10, t20, min_float) if b.get("enabled") else ""
    return {"active_sells": sells, "directive": directive, "enabled": b.get("enabled", True)}

def profit_take(portfolio, now=None, cfg=None, quotes=None):
    """小赚兑现(P0-3, 2026-09-10): 持仓浮盈 >= min_float_pct → 减 reduce_ratio 锁定。

    狼大原话依据: 「吃一口减一半 安全第一」(2026-09-01)、「我最喜欢的就是这种小赚就走的」
    「兌现风格」回测: 固定持有到 T+5 收盘胜率 47%; 改 +3% 小止盈 → 53%;
    rs>0(强于主题) ∧ +3% 止盈 → 56%(均值 +0.56%/中位 +1.01%)。
    作用域: 与 board_half 同口径按持仓 avg_cost 判定; "减 reduce_ratio" 而非清仓, 底仓保护由卖出管道负责。
    返回 {active_sells:[{symbol,reason,reduce_ratio}], directive, enabled}。
    """
    cfg = cfg or _cfg(); pt = cfg.get("profit_take", {})
    if not pt.get("enabled"):
        return {"active_sells": [], "directive": "", "enabled": False}
    pos = _portfolio(portfolio)
    pos_list = (pos or {}).get("positions") or []
    min_float = float(pt.get("min_float_pct", 3.0))
    ratio = float(pt.get("reduce_ratio", 0.5))
    q = quotes or {}
    sells = []
    for p in pos_list:
        sym = str(p.get("symbol", ""))
        cost = float(p.get("avg_cost") or 0)
        vol = float(p.get("volume") or 0)
        if cost <= 0 or vol <= 0 or sym not in q:
            continue          # 无实时价则不硬判(与 board_half 同口径)
        cur = float(q[sym].get("current") or 0)
        if cur <= 0:
            continue
        float_pct = (cur / cost - 1) * 100
        if float_pct >= min_float:
            sells.append({"symbol": sym, "float_pct": round(float_pct, 2), "reduce_ratio": ratio,
                          "action": "profit_take_sell",
                          "reason": "浮盈%.2f%%≥%.1f%% → 小赚兑现减%.0f%%(保留底仓)" % (float_pct, min_float, ratio * 100)})
    directive = ("⚠️ 小赚兑现：持仓浮盈≥%.1f%% 的标的 → 减%.0f%%锁定(保留底仓)。"
                 % (min_float, ratio * 100)) if pt.get("enabled") else ""
    return {"active_sells": sells, "directive": directive, "enabled": pt.get("enabled", False)}


def current_operation():
    """当前浪型主基调(wave_state.operation) —— 分档口径的轴。取不到返回 None。

    本仓 operation 语义(wave_agent.py): build=建仓追 / t_only=只做T不新开 /
    side=观望调仓换股 / defense=防御不建仓 / exit=兑现降仓。
    """
    try:
        p = os.path.join(os.environ.get("DATA_DIR", "data"), "wave_state.json")
        with open(p, encoding="utf-8") as f:
            op = str((json.load(f) or {}).get("operation") or "").strip().lower()
        return op or None
    except Exception:
        return None


def tier_target_pct(operation=None, cfg=None):
    """该 operation 下的**总仓位目标**(%) —— 狼大 2026-01-17 分档。取不到返回 None。

    这是全仓唯一的"按市况分档给仓位"口径来源: `position_cap`(建议层)与
    `position_tier`(P3 硬拦的现金底线) 都读它, 避免两套并行口径。
    """
    c = (cfg or _cfg()).get("position_cap", {}) or {}
    if not c.get("tier_enabled", True):
        return None
    op = (operation or os.getenv("WOLF_POSITION_TIER_OP") or current_operation() or "").strip().lower()
    if not op:
        return None
    t = (c.get("tier_targets") or {}).get(op)
    try:
        return float(t) if t is not None else None
    except Exception:
        return None


def tier_floor_pct(operation=None, cfg=None):
    """该 operation 下的**总仓位下限**(%) —— 狼大 2025-08-11。0/None = 该档无下限。"""
    c = (cfg or _cfg()).get("position_cap", {}) or {}
    if not c.get("tier_enabled", True):
        return None
    op = (operation or os.getenv("WOLF_POSITION_TIER_OP") or current_operation() or "").strip().lower()
    if not op:
        return None
    f = (c.get("tier_floor") or {}).get(op)
    try:
        v = float(f) if f is not None else 0.0
    except Exception:
        v = 0.0
    return v if v > 0 else None


def position_cap(portfolio, cfg=None, operation=None):
    """常态仓位纪律 + **按市况分档**（P2-5, 2026-09-10 立；2026-09-11 改为分档）
    → {"allowed", "ratio", "target_pct", "floor_pct", "below_floor", "exposure", "reason", "directive"}。

    **狼大有原话的部分（分档）**:
      2026-01-17「**主升趋势就75%以上** 然后盘中满仓滚动啊 **调整就50%** **有风险就30%**
                **下跌趋势就不做**」→ `tier_targets`（按本仓 operation 映射：build=主升 /
                side·t_only=调整 / defense=有风险 / exit=下跌趋势就不做。**映射是外推**，
                他的轴是"行情状态"、我们的是"浪型主基调"，故可 `tier_enabled=false` 关掉）。
      2025-08-11「这个位置 **仓位低于55%** 日内分时低于80%都是不太合适」→ `tier_floor`
                （**只挂 build 档**：原话带"这个位置"限定且他当时明确看多；若挂到调整档会与
                  "调整就50%"的目标数学互斥 → 自相矛盾。default 0 = 该档无下限）。
    **原 total_max_pct=70 的依据已被复核推翻**：2026-03-06「现在就是70%仓位」是**他当时的状态描述、
      不是上限规则**，且与"主升趋势就75%以上…盘中满仓滚动"直接冲突 → 默认 0=不再额外限制，由分档取代。
    **狼大无原话、属系统自设**（默认 0 = 不启用）：`single_max_pct` / `top3_max_pct` / `base_max_pct` / `t_max_pct`
      （2026-04-14「50%的底仓 30%左右做日内…剩下20%」是他**本人的持仓结构描述**, 不是规则 → 默认关）。

    口径与 weekend_de_risk 一致: 持仓占比 = (总资产 − 现金) / 总资产。
    本函数只**判定并给出建议**, 不直接下单、不做硬拦（是否拦截由调用方决定）；
    硬拦侧的同源口径在 `position_tier`（P3 现金底线 = 100 − 本表目标）。
    """
    cfg = cfg or _cfg(); c = cfg.get("position_cap", {})
    if not c.get("enabled"):
        return {"allowed": True, "ratio": None, "exposure": None, "reason": "rule_disabled", "directive": ""}
    pos = _portfolio(portfolio)
    if not pos:
        return {"allowed": True, "ratio": None, "exposure": None, "reason": "no_portfolio", "directive": ""}
    cash = float(pos.get("cash") or 0)
    total = float(pos.get("total_asset_market") or pos.get("total_asset") or 0)
    if total <= 0:
        return {"allowed": True, "ratio": None, "exposure": None, "reason": "no_asset", "directive": ""}
    ratio = (total - cash) / total * 100.0
    # 分档目标(=上限) 优先；未启用/取不到 operation 时退回 total_max_pct
    _tgt = tier_target_pct(operation, cfg)
    _flr = tier_floor_pct(operation, cfg)
    _op = (operation or os.getenv("WOLF_POSITION_TIER_OP") or current_operation() or "")
    if _tgt is not None:
        tot_max = _tgt
        _src = "分档(%s档)" % (_op or "?")
    else:
        tot_max = float(c.get("total_max_pct") or 0)
        _src = "total_max_pct"
    reasons = []
    if tot_max > 0 and ratio > tot_max:
        reasons.append("总仓位%.1f%% > %s目标%.0f%%" % (ratio, _src, tot_max))

    # 系统自设项: 仅当配置 >0 才计算(默认不启用, 避免冒充狼大规则)
    exp = {"single_max": None, "top3": None}
    single_max = float(c.get("single_max_pct") or 0)
    top3_max = float(c.get("top3_max_pct") or 0)
    if (single_max > 0 or top3_max > 0):
        vals = []
        for p in (pos.get("positions") or []):
            mv = float(p.get("market_value") or 0)
            if mv <= 0:
                mv = float(p.get("volume") or 0) * float(p.get("price") or p.get("current") or 0)
            if mv > 0:
                vals.append(mv)
        vals.sort(reverse=True)
        if vals:
            top1_pct = vals[0] / total * 100.0
            top3_pct = sum(vals[:3]) / total * 100.0
            exp["single_max"] = round(top1_pct, 1)
            exp["top3"] = round(top3_pct, 1)
            if single_max > 0 and top1_pct > single_max:
                reasons.append("单票%.1f%% > 上限%.0f%%" % (top1_pct, single_max))
            if top3_max > 0 and top3_pct > top3_max:
                reasons.append("前三集中度%.1f%% > 上限%.0f%%" % (top3_pct, top3_max))

    below_floor = bool(_flr and ratio < _flr)
    allowed = not reasons
    d = ""
    if not allowed:
        d = ("⚠️ 仓位纪律：%s。狼大 2026-01-17「主升趋势就75%%以上 然后盘中满仓滚动啊 "
             "调整就50%% 有风险就30%% 下跌趋势就不做」→ 该档不新开/不加仓, 优先降 T 仓至目标内。"
             % "；".join(reasons))
    elif below_floor:
        d = ("ℹ️ 仓位偏低：总仓位%.1f%% < 下限%.0f%%。狼大 2025-08-11「这个位置 仓位低于55%% "
             "日内分时低于80%%都是不太合适」→ 结构完好时不要因为怕回撤而过度减仓；"
             "该补则补（按 253/254 低吸腿，不追高）。" % (ratio, _flr))
    return {"allowed": allowed, "ratio": round(ratio, 1),
            "target_pct": (round(tot_max, 1) if tot_max > 0 else None),
            "floor_pct": (_flr if _flr else None), "below_floor": below_floor,
            "exposure": exp,
            "reason": "；".join(reasons) if reasons else "仓位在阈值内",
            "directive": d}


def discipline_context(portfolio=None, now=None, window=None, quotes=None):
    """返回注入 prompt 的纪律规则上下文块(周末降仓 + 板上减半 + 小赚兑现 + 仓位纪律)。"""
    now = now or __import__("datetime").datetime.now()
    wd = weekend_de_risk(portfolio, now, window=window)
    bh = board_half(portfolio, now, quotes=quotes)
    pt = profit_take(portfolio, now, quotes=quotes)
    pc = position_cap(portfolio)
    parts = []
    if pc.get("directive"):
        parts.append(pc["directive"])
    if wd.get("active"):
        parts.append(wd["directive"])
    if bh.get("enabled") and bh.get("directive"):
        parts.append(bh["directive"])
    if pt.get("enabled") and pt.get("directive"):
        parts.append(pt["directive"])
    for s in bh.get("active_sells", []) + pt.get("active_sells", []):
        parts.append("  - " + s["reason"])
    return ("\n## 狼大纪律规则\n" + "\n".join(parts) + "\n") if parts else ""
