# -*- coding: utf-8 -*-
"""做T系统 · Worker 主动唤醒 + Agent 决策桥接。

依据 final-t-plan.md §④/§⑥ 与 spec t-monitor-trigger / t-execution-risk / t-ai-agentic：
- Worker 命中后主动 POST bridge /chat 唤醒做T Agent（附触发上下文），Agent 不轮询
- AI 主导模式：AI 是唯一决策主体，唤醒后自主看盘决策（exec/wait/abandon/update_condition）
- 桥不可达降级低频轮询兜底（只标记事件待处理，不自动下单）；Worker 永不直接下单
"""
from .t_leg_kinds import BUY_LEG_KINDS, is_buy_leg, LOWDIP_KINDS, is_lowdip, PREVLOW_M5_KINDS, is_prevlow_m5  # noqa: F401  §9.496 单一来源

_BUY_LEG_KINDS = BUY_LEG_KINDS   # §9.496 单一来源：加新腿类型只改 t_leg_kinds.py
import json
import time
import urllib.request
import os
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services import t_db
from app.services.t_gateway import classify_escalation
from app.services.wave_gate import gate_regime as compute_regime   # ★ §9.709：波浪口径 ✓

# 唤醒降级轮询（桥不可达兜底）
FALLBACK_POLL_INTERVAL = 30.0
# AI 主导唤醒（wake_agent POST /chat 等 LLM 决策）：超时+重试（LLM turn 可能>30s，避免瞬时失败直接丢）
# ★ 账本 §9.654 ✓（用户 QQ：`t_bridge.py:288  Read timed out. (read timeout=90)` ✗）：
#   实测 ✓：LLM 一次回合可达 **~193 秒** ✗（§9.646 的 25 只批量 ✓）
#     ⇒ 90 秒 ⇒ **必超时** ✗ ⇒ 触发 `WAKE_RETRY=2` 再等一轮 ✗ ⇒ **净亏**（该条唤醒仍失败 ✓）
#   ⇒ 改为**环境可调** ✓：`WOLF_WAKE_TIMEOUT`（**库内默认仍 90** ✓ ⇒ 生产零影响 ✓；
#     回测 pins 置 **240** ✓ 与批量超时对齐 ✓）
WAKE_TIMEOUT = int(os.getenv("WOLF_WAKE_TIMEOUT", "90") or 90)   # 单次 HTTP 等待秒数
WAKE_RETRY = int(os.getenv("WOLF_WAKE_RETRY", "2") or 2)         # 失败重试次数
# 本地条件单（卖出端秒级）在网关内通过 t_conditions 价位判断承载（见 t_monitor）
# AI 主导模式下连续命中未实质改善的阈值（≥N 次提示 AI 调整/冷却条件）
AI_CONSECUTIVE_HIT_ALERT = 3

_agent_session: Dict[str, str] = {}


def _bridge_url() -> str:
    """bridge /chat 地址（PI_SERVER_URL 已含 /chat 路径段，直接使用，勿重复拼接）。"""
    try:
        settings = get_settings()
        return getattr(settings, "PI_SERVER_URL", "http://127.0.0.1:3001/chat").rstrip("/")
    except Exception:
        return "http://127.0.0.1:3001/chat"


def _position_summary(symbol: str) -> Dict[str, Any]:
    """持仓摘要（唤醒上下文用）：可卖底仓/持仓量/成本/浮动盈亏。"""
    try:
        from app.services.t_gateway import get_sellable_ledger
        ledger = get_sellable_ledger()
        item = ledger.get(symbol) or {}
        return {
            "symbol": symbol,
            "sellable": item.get("sellable", 0),
            "volume": item.get("volume", 0),
            "avg_price": item.get("avg_price"),
            "pnl_pct": item.get("pnl_pct"),
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)[:100]}


def _recent_decisions(symbol: str, limit: int = 3) -> List[Dict[str, Any]]:
    """最近 N 次 AI 决策（t_ai_actions 倒序，含 outcome 结果摘要），唤醒上下文供 AI 参考历史判断。

    ★ 账本 §9.635（用户：agent loop 好慢，精简唤醒上下文 ✓）：
      实测单条 ~1.2 KB ⇒ 5 条 **9345 字符** ✗，其中最大一块是 **`input_snapshot`（683 字符/条 ✗）**
      —— 它装的是**我们这次消息里已经写过去的同一个触发** ✗ ⇒ **纯冗余** ✓
      ⇒ 默认**去掉** `input_snapshot` ✓（保留 action/output/gateway_result 等**决策与结果** ✓）
      ⇒ 5 条上下文约 **9.3 KB ⇒ ~6 KB** ✓（省 ~27% payload ✓）
      开关 `WOLF_AGENT_TRIM_RECENT`（默认 **1** ✓；设 0 恢复旧行为 ✓）
    """
    try:
        rows = t_db.list_ai_actions(symbol=symbol, limit=limit)
    except Exception:
        return []
    if str(os.getenv("WOLF_AGENT_TRIM_RECENT", "1")).strip().lower() in ("0", "false", "no", ""):
        return rows
    # ★ 账本 §9.635 ✓（用户：「省的都是什么提示词」⇒ **连问两次才发现正确边界** ✗）：
    #   实测记录结构 ✓：`input_snapshot = {"context": …, "trigger": {…, "snapshot": {…}}}`
    #     ① `trigger` 的**外层字段**（id/mode/status/symbol/quote_price/suggest_* ✓）
    #        ⇒ **与消息里【做T触发】那段重复** ✗ ⇒ **可删** ✓
    #     ② ★ **`trigger.snapshot`** ⇒ **规则证据**（`wolf_rule` 原话 ✓、`vol_ratio` ✓、
    #        `day_quantile` ✓、`day_rise_pct` ✓、`prev_low`/`today_low` ✓、`quote_time` ✓）
    #        ⇒ **AI 决策理由直接引用** ✗（实测原话：「现价+3.73% 且日内分位76.4 非恐慌放量创新低」✓）
    #        ⇒ ⇒ **必须保留** ✓✓
    #   ⇒ 最终：**只留 `trigger.snapshot`** ✓，删 `trigger` 的其余外层字段 ✓
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            out.append(r); continue
        sn = r.get("input_snapshot")
        if not isinstance(sn, dict):
            out.append(r); continue
        tr = sn.get("trigger")
        if not isinstance(tr, dict):
            out.append(r); continue
        r2 = dict(r)
        sn2 = dict(sn)
        sn2["trigger"] = {"snapshot": tr.get("snapshot")}     # ★ 只留规则证据 ✓
        r2["input_snapshot"] = sn2
        out.append(r2)
    # ★★ 账本 §9.716 ✓（修 ④ 时抓到的**真 bug** ✗）：
    #   原第 106 行的 `return out` **缩进在 for 循环里** ✗ ⇒
    #     ① 最多只返回**第一条**历史决策 ✗ —— 而本函数签名/prompt 都写着「最近 5 次」✗（名不副实 ✓）
    #     ② 若第一条**没有 `input_snapshot`**（= 老记录/测试夹具 ✓），上面会 `continue` ✗
    #        ⇒ 循环自然结束 ⇒ **函数隐式返回 None** ✗ ⇒ 调用方拿到 None ⇒ 历史块**整段消失** ✗
    #   ⇒ 归位到**函数级** ✓：所有条目都处理 ✓，且**永不返回 None** ✓
    return out


def _symbol_t_stats(symbol: str) -> Dict[str, Any]:
    """该标的做T历史统计（供 AI 决策参考）：低吸触发后走向、exec 胜率、abandon 正确率。"""
    try:
        from app.services.t_ai_agent import decision_quality
        q = decision_quality(symbol=symbol)
        return {
            "total_decisions": q.get("total", 0),
            "exec_count": q.get("exec", {}).get("count", 0),
            "exec_win_rate_pct": q.get("exec_win_rate_pct"),
            "exec_avg_pct": q.get("exec", {}).get("avg_pct"),
            "abandon_count": q.get("abandon", {}).get("count", 0),
            "abandon_correct_rate_pct": q.get("abandon_correct_rate_pct"),
            "wait_count": q.get("wait", {}).get("count", 0),
            "wait_to_exec_rate_pct": q.get("wait_to_exec_rate_pct"),
        }
    except Exception as e:
        return {"error": str(e)[:100]}


# ── 同票历史快照（确定性；`WOLF_AGENT_HISTORY_SNAPSHOT`，库内默认 0）──────────────────────
# 为什么要有它（2026-09-22 实测的"采样固化"链）：
#   SH600183 在 0105 10:20 有一条低吸腿，被**确定性**的做T时段窗拦掉（一笔没成交）；但它的
#   **AI 裁决文本**进了 prompt 的【历史决策参考（最近 3 次）】——T6 那次抽到 `ai_wait`（"现价 72.18
#   相对本腿低吸基准仍偏高、非深度回踩"），于是 0106 同票 73.88 的腿被 agent 判"高出前次低吸价 +2.35%"⇒ cancelled；
#   而 T8 抽到 `ai_exec`，历史块里就没有否定锚 ⇒ 同一条腿 executed。**一次采样差异被 prompt 历史固化并放大**。
#   根因：历史来自 `t_ai_actions` 的**最近 N 条窗口**（条数随各臂决策量变化，锚会被挤出窗口），
#   且全是自由文本 ⇒ "能不能发现高出低吸"变成运气。
# 本函数把历史做成**确定性快照**：直接读 `t_triggers`（腿历史，含被拦/取消）+ `paper_trades`（真实成交），
#   给出**锚点**（前次同票低吸腿价 / 前次买入成交价 / 前次卖出价）与现价相对它们的偏离、
#   以及本轮（自上次清仓）的买卖笔数。开关关 ⇒ 不注入（与既有 prompt 逐字一致）。
_HIST_SNAP = str(os.getenv("WOLF_AGENT_HISTORY_SNAPSHOT", "0")).strip().lower() in ("1", "true", "yes", "on")


def history_snapshot_on() -> bool:
    return _HIST_SNAP


def symbol_history_snapshot(symbol: str, account_id: Optional[str] = None,
                            current: Optional[float] = None, limit_legs: int = 6) -> Dict[str, Any]:
    """该票的**确定性**历史快照（腿历史 + 成交锚点 + 本轮笔数）。取数失败 ⇒ 空 dict（fail-open）。"""
    out: Dict[str, Any] = {}
    sym = str(symbol or "").upper()
    try:
        from sqlalchemy import text as _text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            # ⚠️ `t_triggers` 没有 trade_date 列（日期只在 created_at 里）—— 2026-09-22 踩过：
            #    写成 trade_date 会抛错并被本函数的 except 吞掉 ⇒ 快照恒空（静默失效）。
            rows = db.execute(_text(
                "SELECT created_at, direction, event_type, quote_price, status, reason "
                "FROM t_triggers WHERE upper(symbol)=:s AND (:a IS NULL OR account_id=:a) "
                "ORDER BY created_at DESC, id DESC LIMIT 40"), {"s": sym, "a": account_id}).mappings().all()
            legs = []
            for r in rows:
                legs.append({"day": str(r["created_at"] or "")[:10].replace("-", ""),
                             "t": str(r["created_at"] or "")[11:16],
                             "dir": "买" if str(r["direction"]) == "buy" else "卖",
                             "kind": str(r["event_type"] or ""), "px": float(r["quote_price"] or 0) or None,
                             "status": str(r["status"] or "")})
            out["legs"] = legs[:limit_legs]
            # 锚点①：最近一次**买腿**（含被拦的）价
            lb = next((x for x in legs if x["dir"] == "买" and x["px"]
                       and (x["kind"] in _BUY_LEG_KINDS or x["kind"].startswith("buy_"))), None)
            if lb:
                out["last_buy_leg"] = lb
            # 锚点②③：最近一次买入成交价 / 最近一次卖出价 + 本轮笔数
            trows = db.execute(_text(
                "SELECT trade_date, direction, price, volume FROM paper_trades "
                "WHERE upper(symbol)=:s AND (:a IS NULL OR account_id=:a) AND COALESCE(voided,0)=0 "
                "ORDER BY created_at DESC, id DESC LIMIT 60"), {"s": sym, "a": account_id}).mappings().all()
            n_buy = n_sell = 0
            for t in trows:
                d = str(t["direction"])
                if d in ("买入", "buy") and "last_buy_fill" not in out:
                    out["last_buy_fill"] = {"day": str(t["trade_date"] or "")[:10].replace("-", ""),
                                            "px": float(t["price"] or 0), "vol": int(t["volume"] or 0)}
                if d in ("卖出", "sell") and "last_sell" not in out:
                    out["last_sell"] = {"day": str(t["trade_date"] or "")[:10].replace("-", ""),
                                        "px": float(t["price"] or 0), "vol": int(t["volume"] or 0)}
            # 本轮（自上次清仓）笔数：按时间倒序累加，遇到"持仓归零"停
            pos = 0
            for t in trows:
                v = int(t["volume"] or 0)
                if str(t["direction"]) in ("卖出", "sell"):
                    pos += v; n_sell += 1
                else:
                    pos -= v; n_buy += 1
                    if pos <= 0:
                        break
            out["round"] = {"buys": n_buy, "sells": n_sell}
        finally:
            db.close()
    except Exception:
        return {}
    try:
        px = float(current or 0)
    except Exception:
        px = 0.0
    if px > 0:
        for k, label in (("last_buy_leg", "dev_vs_buy_leg_pct"), ("last_buy_fill", "dev_vs_buy_fill_pct"),
                         ("last_sell", "dev_vs_last_sell_pct")):
            a = out.get(k) or {}
            if a.get("px"):
                out[label] = round((px / float(a["px"]) - 1.0) * 100.0, 2)
    return out


def render_history_snapshot(snap: Dict[str, Any], symbol: str = "") -> str:
    """把快照渲染成 prompt 段落（确定性、可核对；无数据 ⇒ 返回空串）。"""
    if not snap:
        return ""
    L = ["【同票历史快照（确定性口径，系统直接给出，不必自己回忆）】"]
    a = snap.get("last_buy_leg")
    if a:
        L.append("- 前次**买腿**（含被拦）：%s %s %s @%s（%s）" % (
            a.get("day"), a.get("t"), a.get("kind"), a.get("px"),
            {"blocked": "被拦", "cancelled": "AI取消", "executed": "已成交"}.get(a.get("status"), a.get("status"))))
    b = snap.get("last_buy_fill")
    if b:
        L.append("- 前次**买入成交**：%s @%.2f ×%d 股" % (b.get("day"), b.get("px"), b.get("vol")))
    s_ = snap.get("last_sell")
    if s_:
        L.append("- 前次**卖出**：%s @%.2f ×%d 股" % (s_.get("day"), s_.get("px"), s_.get("vol")))
    dev = []
    for k, lbl in (("dev_vs_buy_leg_pct", "距前次买腿价"), ("dev_vs_buy_fill_pct", "距前次买入成交价"),
                   ("dev_vs_last_sell_pct", "距前次卖出价")):
        if snap.get(k) is not None:
            dev.append("%s %+.2f%%" % (lbl, float(snap[k])))
    if dev:
        L.append("- 现价相对锚点：" + "；".join(dev) + "（正=已高于该锚，属「追高」证据；负=回踩到该锚下方）")
    r = snap.get("round") or {}
    if r.get("buys") or r.get("sells"):
        L.append("- 本轮（自上次清仓）：买入 %d 笔、卖出 %d 笔（G6 上限 2/2）" % (r.get("buys", 0), r.get("sells", 0)))
    if snap.get("legs"):
        L.append("- 最近腿序（新→旧）：" + "，".join(
            "%s %s %s@%s(%s)" % (x.get("day")[4:], x.get("dir"), x.get("kind"), x.get("px"),
                                 {"blocked": "拦", "cancelled": "取消", "executed": "成交"}.get(x.get("status"), x.get("status")))
            for x in snap["legs"][:5]))
    return "\n".join(L) + "\n"


def _outcome_summary(oc: Dict[str, Any]) -> str:
    """outcome 摘要（供唤醒上下文展示）：✅+0.85% / ⛔-1.5%。"""
    try:
        pct = float(oc.get("pct_change") or 0)
        direction = "✅" if pct > 0 else "⛔"
        return f"{direction}{pct:+.2f}%"
    except (TypeError, ValueError):
        return ""


def _consecutive_hits(condition_id: Optional[int], symbol: str) -> int:
    """同条件当日连续命中计数（t_triggers 最近事件，按条件+标的统计）。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            # ⚠️ 2026-09-19：与 created_at 的写入钟同源（Python 钉钟）—— 原用 CURRENT_DATE（DB 真钟）
            #   在回放里恒真 ⇒ 连续命中跨日累加。
            from datetime import datetime as _dt
            _today = _dt.now().strftime("%Y-%m-%d")
            rows = db.execute(text(
                "SELECT status, created_at FROM t_triggers "
                "WHERE condition_id = :cid AND symbol = :sym "
                "AND to_char(created_at, 'YYYY-MM-DD') = :today "
                "ORDER BY id DESC LIMIT 10"
            ), {"cid": condition_id, "sym": symbol, "today": _today}).mappings().all()
            n = 0
            for r in rows:
                # 连续：从最新往前数，遇到 executed/blocked/cancelled 则中断计数
                st = r.get("status")
                if st in ("await_retry", "ai_decided", "pending"):
                    n += 1
                else:
                    break
            return n
        finally:
            db.close()
    except Exception:
        return 0


def wake_agent(trigger: Dict[str, Any], context: Optional[dict] = None) -> Optional[str]:
    """Worker 命中后主动唤醒做T Agent（POST /chat，附触发上下文）。

    AI 主导模式：唤醒语义为"决策"而非"复核"——AI 自主看盘后决定
    exec（执行）/ wait（等待）/ abandon（放弃）/ update_condition（调整条件）。
    返回 AI 回复文本（/chat 响应 reply 字段）；失败返回 None（调用方降级）。
    """
    symbol = trigger.get("symbol", "")
    ctx = dict(context or {})
    # 增强上下文：持仓摘要 + 最近决策（含结果） + 标的做T历史统计 + 连续命中计数
    ctx.setdefault("position", _position_summary(symbol))
    ctx.setdefault("recent_decisions", _recent_decisions(symbol))
    ctx.setdefault("symbol_t_stats", _symbol_t_stats(symbol))
    ctx.setdefault("history_snapshot", symbol_history_snapshot(
        symbol, account_id=trigger.get("account_id"),
        current=trigger.get("quote_price"))) if _HIST_SNAP else None
    consec = _consecutive_hits(trigger.get("condition_id"), symbol)
    ctx["consecutive_hits"] = consec
    hit_alert = consec >= AI_CONSECUTIVE_HIT_ALERT
    ctx["consecutive_hit_alert"] = hit_alert

    msg = (
        f"【做T触发】{symbol} {trigger.get('event_type', 'low_buy')} "
        f"触发价={trigger.get('trigger_price')} 现价={trigger.get('quote_price')} "
        f"建议买价={trigger.get('suggest_bid_price')} 建议卖价={trigger.get('suggest_ask_price')} "
        f"事件#{trigger.get('id')} 条件#{trigger.get('condition_id')}。"
    )
    # ★★ 账本 §9.730 ✓（用真实消息核对后实测 ✗）：**规则证据根本没进消息** ——
    #   消息里只有「触发价/现价/建议买价/建议卖价」✗，而决策理由却在大批量引用
    #   「量比 X」「日内分位 Y」「规则原文」✗（引用率：量比 52% ✓、止损 46% ✓、分位 40% ✓）
    #   ⇒ 这些值**在 t_triggers.snapshot 里全都有** ✓（200 条抽样已验证字段全集 ✓）
    #     但从未渲染进唤醒消息 ✗（§9.635 又把历史记录里的 `input_snapshot` 裁掉了 ✓）
    #   ⇒ ⇒ **等于让 AI 引用它没收到的数字** ✗ ⇒ 这里把**最高频的 5 项**补渲染 ✓
    #   （按用户「够用即可」口径 ✓：只补被真正引用的，不补 MA/RSI/分钟/资金流 ✗）
    _sn = trigger.get("snapshot")
    if isinstance(_sn, str):
        try:
            import json as _js_sn
            _sn = _js_sn.loads(_sn)
        except Exception:
            _sn = None
    if isinstance(_sn, dict) and _sn:
        _parts = []
        for _k, _label, _fmt in (
            ("vol_ratio", "量比", "%.2f"),
            ("day_quantile", "日内分位", "%.1f"),
            ("day_rise_pct", "当日涨幅", "%+.2f%%"),
            ("prev_low", "前低", "%.3f"),
            ("today_low", "今日最低", "%.3f"),
            ("amplitude", "振幅", "%.2f%%"),
            ("turnover_rate", "换手", "%.2f%%"),
            ("slippage_budget", "滑点预算", "%.3f"),
        ):
            _v = _sn.get(_k)
            if _v is None:
                continue
            try:
                _parts.append("%s=" % _label + (_fmt % float(_v)))
            except Exception:
                _parts.append("%s=%s" % (_label, str(_v)[:12]))
        if _parts:
            msg += "【快照证据】" + " ".join(_parts) + "\n"
        _rule = _sn.get("wolf_rule")
        if _rule:
            msg += "【规则原文】%s\n" % str(_rule)[:220]
    # 高抛卖腿是兑现利润的正向动作：连续命中告警不适用于高抛（高抛越多越好）
    if hit_alert and trigger.get("event_type") != "high_sell_then_buy_back":
        msg += (f"⚠️ 该条件已连续命中 {consec} 次且未见实质改善——你必须二选一："
                f"① 输出 update_condition 附新的合理条件；② 输出 wait 注明『连续命中，等待冷却』。"
                f"严禁只把 target_price 往现价方向微调制造下一轮触发。")
    msg += (
        f"你是做T决策者：本次触发已命中你的监控条件并通过系统规则预筛——**默认动作=exec（执行）**。"
        f"仅当存在客观证据时才 wait/abandon，且 reason 必须写明具体证据："
        f"① 现价与目标价/建议价脱节（差>1%）；② 已跌破止损；③ regime 禁自动；④ 恐慌放量追跌（量比骤升+创新低）。"
        # ★ 账本 §9.728 ✓（用户 2026-10-07「全屏蔽，改成直接置入数据」）：
        #   工具已**全屏蔽**（桥只注册 0 个 ✓）⇒ 原句「请先调用查询工具补数」成了**死指令** ✗
        #   ⇒ 改为：**决策所需数据已随本消息置入** ✓；数据确实不足时按 wait/abandon 处理 ✓
        f"信息不足不等于 wait——但**决策所需的现价/量比/日内分位/持仓/规则证据已随本消息给出** ✓，"
        f"**无需也无法调用查询工具**（回测内已全屏蔽 ✓）；若本消息确实缺关键字段，请按 wait/abandon 处理并写明缺什么。"
        f"输出决策 JSON："
        f'{{"action": "exec|wait|abandon|update_condition", "reason": "一句话理由", '
        f'"condition": {{...}}}}（condition 仅在 update_condition 时提供，含 symbol/trigger_kind/target_price 等）。'
        # ★★ 账本 §9.729 ✓（本轮实测 ✗）：**AI 在编指标** ——
        #   抽样 200 条触发快照：**没有任何 RSI/KDJ/MACD 字段** ✗
        #   但 164 条决策里 **38 条（23%）** 的理由引用了它们 ✗；而工具全程只被调过 3 次 ✓
        #   ⇒ **绝大多数是编出来的** ✗（编造依据 ≠ 对齐狼大 ✓）⇒ 明确禁止 ✓
        f"★ 判定只能用**本消息已给出**的字段（现价/建议价/止损线/量比/日内分位/涨幅/前低/"
        f"规则证据/持仓/regime/历史决策）✓；**本消息未提供的指标一律不得引用** ✗"
        f"（RSI/KDJ/MACD/均线/资金流/分钟走势等 **均不在数据内** ✗，写了就是编造 ✓）。"
        f"exec 将按触发快照的『建议价』执行（低吸用建议买价、高抛用建议卖价）。"
        f"数量可选：① 不输出 volume/amount → 系统按可卖底仓档位自动推导；"
        f"② 在 JSON 输出建议量 volume(股,100整数倍) 或 amount(金额元) → 系统按 min(建议量, 档位上限) 执行，"
        f"超出上限自动收敛、不会整单拒绝；不要输出超档位上限的巨额建议（会被收敛）。"
        # ★ 账本 §9.728 补 ✓（全屏蔽的连带影响 ✗）：工具已全屏蔽 ⇒ `create_t_condition` **调用不了** ✗
        #   ⇒ 原文给的"两条路"里有一条是**死路** ✗ ⇒ 只保留 **update_condition**（它是 JSON 动作、非工具 ✓）
        f"【消费式条件】本次触发后该条件已销毁（consumed）——如需继续做T，请在决策里用 "
        f"update_condition 附新 condition 重建（**唯一可用方式** ✓；回测内工具已全屏蔽 ✓，"
        f"create_t_condition 不可调用 ✗）；"
        f"不重建则本标的今日不再有触发条件。"
        f"（本消息已含决策所需全部字段 ✓；回测内**工具已全屏蔽** ✗，"
        f"任何「调用查询工具」的路径都不存在 ✓）"
    )
    # 历史模式段：最近决策结果 + 标的做T统计（决策 checklist 依据）
    # ★ 账本 §9.728 ✓：**持仓摘要之前算完就丢** ✗ —— `ctx["position"]` 从未渲染进消息 ✓
    #   ⇒ 而「当前持仓（可卖/成本/盈亏）」正是工具被屏蔽后最需要的数据 ✓ ⇒ 这里补渲染 ✓
    _pos = ctx.get("position") or {}
    if isinstance(_pos, dict) and _pos and not _pos.get("error"):
        try:
            _pv = int(_pos.get("volume") or 0)
            _sv = int(_pos.get("sellable") or 0)
            _cost = float(_pos.get("avg_price") or 0)
            _pnl = _pos.get("pnl_pct")
            msg += ("【当前持仓】%s 持仓 %d 股（可卖 %d）｜成本 %.3f｜浮动盈亏 %s｜"
                    "（底仓不可卖 ✓ 卖腿只动 T 仓）\n"
                    % (symbol, _pv, _sv, _cost,
                       ("%.2f%%" % float(_pnl)) if _pnl is not None else "—"))
        except Exception as _e_pos:
            print("[t-bridge] 持仓渲染失败(跳过): %s" % str(_e_pos)[:70], flush=True)
    recents = ctx.get("recent_decisions") or []
    if recents:
        lines = ["【历史决策参考（最近 " + str(len(recents)) + " 次，含结果）】"]
        for r in recents[:3]:
            at = r.get("action_type", "")
            oc = r.get("outcome") or {}
            oc_sum = _outcome_summary(oc) if oc else "（无结果）"
            rs = ((r.get("output") or {}).get("reason") or "")[:60]
            lines.append(f"- {at} {oc_sum} {rs}")
        msg += "\n".join(lines) + "\n"
    if _HIST_SNAP:
        _snap_txt = render_history_snapshot(ctx.get("history_snapshot") or {}, symbol)
        if _snap_txt:
            msg += _snap_txt
    st = ctx.get("symbol_t_stats") or {}
    if st and st.get("total_decisions"):
        win_rate = st.get("exec_win_rate_pct")
        msg += (
            f"【{symbol} 做T历史统计】决策 {st.get('total_decisions')} 次 | "
            f"exec {st.get('exec_count')} 次 胜率 {win_rate}% "
            f"均幅 {st.get('exec_avg_pct')}% | "
            f"abandon {st.get('abandon_count')} 次 正确率 {st.get('abandon_correct_rate_pct')}% | "
            f"wait {st.get('wait_count')} 次 转exec {st.get('wait_to_exec_rate_pct')}%\n"
        )
        # 高胜率标的重触发放开（P3-3）：>55% 放开冷却；<40% 提示减仓
        if win_rate is not None and win_rate > 55:
            msg += f"【提示】该标的 exec 胜率 {win_rate}% > 55%，属于高胜率标的——允许连续命中继续触发（不强制冷却）。\n"
        elif win_rate is not None and win_rate < 40:
            msg += f"【警告】该标的 exec 胜率 {win_rate}% < 40%，历史表现差——建议减仓或收紧触发。\n"
    msg += (
        "【决策 checklist】① 价差/盈亏比（参考，非决定项）：现价距建议价应有 ≥0.2% 价差（网关建议层阈值），"
        "滑点+手续费不应吃光价差——网关仍会做最终风控（裸空/跌停/熔断/可卖底仓/单笔5%/回转额），"
        "你不需要比网关更严，系统一旦命中条件默认价差已够做；"
        "② 高抛卖腿（high_sell_then_buy_back）是兑现利润的正向动作——触及时应倾向 exec 卖出兑现，"
        "而非担心卖飞继续等待；③ 弹药：可卖底仓与浮盈浮亏（低吸触及时若亏损接近止损线才保守）；"
        "④ 历史模式：该标的低吸后历史走向/exec 胜率（仅作趋势参考，不作为否决依据——"
        "不要因为之前 wait 过就继续 wait）；"
        "⑤ 连续命中：低吸条件已达告警阈值可调整或等待冷却，高抛不适用冷却。"
    )
    # ★★★★ 账本 §9.736 ✓（用户 2026-10-07：「**还是有震荡市不是浪型**」✗）：
    #   实测 ✗：消息里**完全没有浪型闸结论** ⇒ AI 只好编「震荡市」填空 ✗
    #   真值就在快照 `fields` 里 ✓（抽 40 条样本，三个子块齐全 ✓）：
    #     · regime ⇒ state=HALT|ACTIVE ＋ gate_low_buy/gate_high_sell=ALLOWED|BLOCKED|MANUAL_ONLY
    #     · index  ⇒ 大盘 sh_drop / 黄白线 huang_bai_side / intraday_dd …
    #     · tech   ⇒ MA5/10/20/60 ＋ KDJ ＋ RSI6/12/24 ＋ MACD
    #   ★ 更正此前误判 ✗：RSI 等**一直在数据里** ✓（fields.tech ✓），并非"AI 在编" ✗，
    #     只是**没渲染进消息** ✗ ⇒ 本次一并注入 ✓
    _f = trigger.get("snapshot") if isinstance(trigger, dict) else None
    if isinstance(_f, str):
        try:
            import json as _js_f2
            _f = _js_f2.loads(_f) or {}
        except Exception:
            _f = {}
    _f = (_f or {}).get("fields") if isinstance(_f, dict) else None
    if isinstance(_f, str):
        try:
            import json as _js_f3
            _f = _js_f3.loads(_f)
        except Exception:
            _f = None
    if isinstance(_f, dict):
        _rg = _f.get("regime") or {}
        if isinstance(_rg, dict) and _rg:
            _gt = " ".join("%s=%s" % (str(k).replace("gate_", ""), v)
                           for k, v in _rg.items() if str(k).startswith("gate_"))
            _sign = _rg.get("interpret_sign")
            msg += ("【浪型闸】state=%s %s%s\n"
                    % (_rg.get("state"), _gt,
                       (" sign=%s" % _sign) if _sign is not None else ""))
        _ix = _f.get("index") or {}
        if isinstance(_ix, dict) and _ix:
            _pix = []
            for _k in ("sh_drop", "sz_drop", "hs300_drop", "intraday_dd",
                       "huang_bai_side", "huang_bai_index", "huang_bai_equal",
                       "bai_on_top", "m5_dump"):
                _v = _ix.get(_k)
                if _v is None:
                    continue
                if isinstance(_v, bool):
                    _pix.append("%s=%s" % (_k, "T" if _v else "F"))
                else:
                    try:
                        _pix.append("%s=%.3f" % (_k, float(_v)))
                    except Exception:
                        _pix.append("%s=%s" % (_k, str(_v)[:10]))
            if _pix:
                msg += "【大盘/黄白线】" + " ".join(_pix) + "\n"
        _tc = _f.get("tech") or {}
        if isinstance(_tc, dict) and _tc:
            _ptc = []
            for _k in ("ma5", "ma10", "ma20", "ma60", "kdj_k", "kdj_d", "kdj_j",
                       "rsi_6", "rsi_12", "rsi_24", "macd_bar", "macd_dea", "macd_dif"):
                _v = _tc.get(_k)
                if _v is None:
                    continue
                try:
                    _ptc.append("%s=%.2f" % (_k, float(_v)))
                except Exception:
                    _ptc.append("%s=%s" % (_k, str(_v)[:10]))
            if _ptc:
                msg += "【技术指标】" + " ".join(_ptc) + "\n"
    payload = {
        "message": msg,
        "session_id": _agent_session.setdefault(symbol, f"t-agent-{symbol}"),
        "mode": "trade",
        "decision_mode": "ai_led",
    }

    reply = None
    for attempt in range(1, WAKE_RETRY + 1):
        try:
            req = urllib.request.Request(
                _bridge_url(),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=WAKE_TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
            try:
                reply = str(json.loads(body).get("reply") or "") if body else ""
            except (ValueError, TypeError):
                reply = body or ""
            print(f"[t-bridge] 唤醒 Agent 成功: {symbol} ({len(body)} bytes) decision_mode=ai_led "
                  f"reply_len={len(reply)}")
            return reply or None
        except Exception as e:
            print(f"[t-bridge] 唤醒 Agent 失败(第{attempt}/{WAKE_RETRY}次): {e}", flush=True)
            if attempt < WAKE_RETRY:
                time.sleep(1.5)
    return None


def wake_and_decide(trigger: Dict[str, Any], context: Optional[dict] = None,
                    session_id: Optional[str] = None) -> Dict[str, Any]:
    """AI 主导闭环：唤醒 AI → 取回复 → handle_ai_decision 路由（exec/wait/abandon/update_condition）→ 审计。

    唤醒失败（桥不可达）返回 {"status": "wake_failed"}，由调用方走降级（agent_review_and_execute 标记）。
    """
    reply = wake_agent(trigger, context=context)
    if not reply:
        return {"status": "wake_failed", "reason": "AI 唤醒失败（桥不可达）"}
    from app.services.t_ai_agent import handle_ai_decision
    sid = session_id or f"t-agent-{trigger.get('symbol', '')}"
    return handle_ai_decision(trigger, context, reply, session_id=sid)


def agent_review_and_execute(trigger: Dict[str, Any]) -> Dict[str, Any]:
    """AI 决策降级通道（桥不可达兜底调用）。

    AI 主导模式下此路径仅做合理性标记：异常升级分类命中则置 human_confirm；
    否则标记 ai_decided 等待 AI 下次唤醒处理，绝不自动下单（与 AI 主导语义一致）。
    """
    trigger_id = int(trigger.get("id") or 0)
    symbol = trigger.get("symbol", "")
    side = t_db.trigger_side(trigger)  # 方向以落库 direction 为准（2026-09-08）
    regime = compute_regime().get("regime", "ACTIVE")

    # 1) 异常升级分类（与 AI 决策共享的硬性升级）
    escalation, why = classify_escalation(symbol, side, trigger=trigger, regime=regime)
    if escalation == "human":
        t_db.update_trigger_status(trigger_id, "human_confirm", reason=why)
        return {"status": "human_confirm", "trigger_id": trigger_id, "reason": why}
    if escalation == "agent" and side == "buy":
        # 2026-09-21：新开仓口径改 AI 审核 ⇒ **在这里真给 AI 一次机会**（本函数只会在
        # "上一次唤醒失败/异常"后被调用，所以这是一次带新上下文的补审）。
        # 仍失败 ⇒ 标 await_retry（保留活性，条件再触发时重来），**不再进 human_confirm 黑洞**。
        try:
            retry = wake_and_decide(trigger)
            if retry and retry.get("status") != "wake_failed":
                return retry
        except Exception as _e:
            why = "%s；AI 审核唤醒异常 %s" % (why, str(_e)[:60])
        t_db.update_trigger_status(trigger_id, "await_retry",
                                   reason="%s（AI 审核未完成 → 保留活性，等待条件再触发）" % why)
        return {"status": "await_retry", "trigger_id": trigger_id, "reason": why}

    # 2) AI 主导：桥不可达 → 标记待 AI 下次唤醒，不自动下单
    t_db.update_trigger_status(trigger_id, "ai_decided",
                               reason="桥不可达降级：标记待 AI 唤醒决策（不自动下单）")
    return {"status": "ai_decided", "trigger_id": trigger_id,
            "reason": "AI 主导降级：仅标记不自动下单"}


def fallback_poll_loop(stop_event):
    """桥不可达时的低频轮询兜底：消费 pending 事件 → 先 AI 主导唤醒决策（wake_and_decide，真 LLM），
    唤醒失败/异常再降级 agent_review_and_execute（标记 human_confirm / ai_decided）。"""
    while not stop_event.is_set():
        try:
            trig = t_db.claim_pending_trigger("t-fallback", timeout_seconds=300)
            if trig:
                result = wake_and_decide(trig)
                if result and result.get("status") != "wake_failed":
                    print(f"[t-bridge] AI 决策完成 #{trig.get('id')}: {result.get('status')} {result.get('action')}")
                else:
                    result = agent_review_and_execute(trig)
                    print(f"[t-bridge] 唤醒失败，降级标记 #{trig.get('id')}: {result.get('status')}")
        except Exception as e:
            print(f"[t-bridge] 兜底轮询异常: {e}")
        time.sleep(FALLBACK_POLL_INTERVAL)


# ────────────────────────────────────────────────────────────────
# AI 条件生成（AI 自主设定触发条件，系统条件命中后唤醒 AI 决策）
# ────────────────────────────────────────────────────────────────

# 条件生成结果缓存：key = (symbol, round(cost, 2), amp_med) → (conditions, source)
# 同一建仓参数不重复唤醒 LLM（滚动建仓每日多标的时显著降低 LLM 调用量）
_cond_gen_cache: Dict[tuple, tuple] = {}
# 允许 AI 生成条件关闭（灾难回退开关）
AI_CONDITIONS_ENABLED = True


def _bridge_base_url() -> str:
    """bridge 服务基址（去掉 /chat 路径段）——与 t_backtest_runner.bridge_base_url 同源。"""
    try:
        settings = get_settings()
        raw = getattr(settings, "PI_SERVER_URL", "http://127.0.0.1:3001/chat").rstrip("/")
    except Exception:
        raw = "http://127.0.0.1:3001/chat"
    scheme_sep = raw.find("://")
    if scheme_sep >= 0:
        rest = raw[scheme_sep + 3:]
        host = rest.split("/", 1)[0]
        return raw[:scheme_sep + 3] + host
    return raw.rsplit("/", 1)[0] if "/" in raw else raw


def generate_conditions(symbol: str, cost: float, amp_med: Optional[float] = None,
                        trend: Optional[dict] = None, regime: Optional[dict] = None,
                        context: Optional[dict] = None, session_id: Optional[str] = None,
                        use_cache: bool = True,
                        quote_price: Optional[float] = None,
                        rebuild_ctx: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """AI 自主设定做T双条件（低吸 + 高抛回补）→ POST bridge /conditions/generate。

    返回 {"conditions": [...], "source": "ai"|"fallback", "reason": ...}；
    桥不可达 / AI 解析失败 / 开关关闭 → None（调用方回退规则公式 build_t_conditions）。
    quote_price：现价（消费式重建传——防止 AI 把现价误当成本基准设止损，迭代#56c）。
    rebuild_ctx：重建上下文（上次触发 kind/价，迭代#57c——让 AI 设移动条件，
    避免重复设同价触发循环）。
    """
    if not AI_CONDITIONS_ENABLED:
        return None
    cache_key = (symbol, round(float(cost), 2), round(float(amp_med), 3) if amp_med else None)
    if use_cache and cache_key in _cond_gen_cache:
        return dict(_cond_gen_cache[cache_key])  # 浅拷贝（conditions 列表引用可读）
    payload = {
        "symbol": symbol,
        "cost": float(cost),
        "amp_med": amp_med,
        "trend": trend,
        "regime": regime,
        "context": context,
        "session_id": session_id,
        "quote_price": quote_price,
        "rebuild_ctx": rebuild_ctx,
    }
    # 重试一次（迭代#55b：#57/#58 中 2/5 条件生成 120s timed out 回退规则——
    # LLM 推理时快时慢（新会话首建+推理可达 3min+），超时后重试通常命中已建会话更快）
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                _bridge_base_url() + "/conditions/generate",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            conditions = body.get("conditions") or []
            source = body.get("source") or "ai"
            if not conditions:
                return None
            result = {"conditions": conditions, "source": source,
                      "reason": body.get("reason") or "AI 生成"}
            if use_cache:
                _cond_gen_cache[cache_key] = result
            print(f"[t-bridge] AI 条件生成 {symbol} → {len(conditions)} 条 (source={source}, "
                  f"cost={cost}, attempt={attempt + 1})")
            return result
        except Exception as e:
            last_err = e
            print(f"[t-bridge] AI 条件生成 {symbol} 第{attempt + 1}次失败: {e}"
                  f"（重试{'中' if attempt == 0 else '后放弃，回退规则公式'}）")
    return None
