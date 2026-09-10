# -*- coding: utf-8 -*-
"""wolf_context.py — 狼大"分语境"闸门（P1-3, 2026-09-10; 2026-09-10 晚 **重大更正**）

## 用途
判定 253（指数 5min 急杀 → 低吸）当前**是否允许**。

狼大语义（分语境，非时钟窗）:
    筑底 / 上行  → 急杀可以接
    未筑底（下跌中 / 诱多反弹 / 最后一跌）→ 不该在急杀那一刻接

狼大原话（检索脚本 .dsh-tmp/wolfbt/gitwork/wolf_evidence*.py）:
  · 2025-06-05「要么继续震荡，但是**主线板块筑底行情**…**急杀可以买，缓跌不买**」
  · 2025-07-28「现在不是大盘跳水要不要出，而是**跳水了你高位减仓的钱敢不敢买**才对」
  · 2026-05-15「只要磨出底部结构 就是抄底迹象…**肯定不是急杀的时候买啊**」
  · 2026-01-13「说的是**主线题材 题材 题材**」

## ⚠️ 更正说明（本文件第一版做错了参照系）
第一版（commit 16a68aa）**只用大盘浪**（`wave_state.json` = 上证指数 000001.SH）判语境。
这是错的 —— 本项目对此已有明确原则, 见 `backend/app/services/trade_graph.py:675`:

    ⚠️ **主线自身浪型（重要: 主题浪, 不同于上方大盘 wave_context）**：
    → 该主线为主升 3 浪运行中(已确认), **不因大盘 t_only(4-2) 一刀切禁建**；
      按回调低吸模式(254 dip_prev_low/253)在其回调位建底仓, 不追高。

而 2026-09-10 当时大盘恰为 `d4/4-2/t_only` → 第一版会把**所有 253 一刀切禁掉**, 正是该注释警告之事。
且狼大 2025-06-05 说的也是"**主线板块**筑底行情", 参照系是板块而非大盘。

## 正确参照系：两个浪，分工不同
  1. **主题浪（主判据）** —— 决定"这个方向的急杀能不能接"。
     数据源：`trend_confirm_<date>_long.json` 的 `themes[*].track_a.stage`（机读, 优于解析自由文本），
             辅以 `mainline_gate_<date>.json` 的 `verdict`。
  2. **大盘浪（仅系统性护栏）** —— 只拦"真正系统性下跌", **不参与方向判定**。
     明确**不拦**：`4-2`（B反）/`t_only`/`4-4` —— 依上述项目原则。
     仅拦：level=down, 或 sub_level ∈ {C杀, 衰竭浪, 双头/M顶, 4-5, 失败5}（狼大: 最后一跌/防御等企稳）。

## 环境变量
  WOLF_253_CONTEXT=1            启用本闸门（**默认 1 = 启用**; 置 0 关闭）
  WOLF_253_CONTEXT_UNKNOWN=allow  主题浪数据缺失时放行（默认 allow —— 见下方 fail 策略）
  WOLF_253_CONTEXT_LOG=1        打印放行原因

## fail 策略（与第一版相反，此处有意为之）
  · 主题浪数据**缺失/不可读** → **放行**（allow）。理由：数据缺失是运维问题，不应把整条 253 买路封死；
    且 253 另有 regime GATE / 网关硬闸门等多层把关。
  · 大盘处于系统性下跌 → 拦（有明确狼大依据：最后一跌/防御等企稳）。
"""
import json
import os

DATA = os.environ.get("DATA_DIR", "/app/data")

# ── 主题浪：trend_confirm 的 track_a.stage 语义（见 trend_confirm.py TREND_CFG / 分层）──
# confirmed     = 主升/筑底结构成立（低点抬高+2浪不破前低+再创新高）→ 允许
# suspect       = 创新高但2浪形态不全（疑似V反/平台），等结构确认      → 不允许
# not_confirmed = 结构未确认                                        → 不允许
# window_limited= 历史不足，不硬判                                   → 不允许（宁可不接）
THEME_STAGE_ALLOW = {"confirmed"}

# ── 大盘浪：仅"真正系统性下跌"才拦（不参与方向判定）──
SYSTEMIC_BLOCK_SUB = {"C杀", "衰竭浪", "双头/M顶", "4-5", "失败5"}
SYSTEMIC_BLOCK_LEVEL = {"down"}


def _load(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _latest(prefix):
    """取 DATA 下 <prefix><YYYYMMDD>[*].json 中日期最大者。

    注意命名不统一：`mainline_gate_<date>.json` 与 `trend_confirm_<date>_long.json`
    （后者带 `_long` 后缀）→ 不能按"固定切片长度"取日期，须正则抓前 8 位数字。
    """
    import glob
    import re
    best, bd = None, ""
    for f in glob.glob(os.path.join(DATA, prefix + "*.json")):
        m = re.match(r"(\d{8})", os.path.basename(f)[len(prefix):])
        if m and m.group(1) > bd:
            bd, best = m.group(1), f
    return best, bd


def load_wave():
    """大盘浪（上证指数）。"""
    return _load("wave_state.json")


def theme_of_symbol(symbol):
    """symbol(如 SH600001/SZ000001/600001.SH) → 主题名；取不到返回 None。

    优先级：stock_confirm_result.json（生产含 theme 字段）
          → stock_pool.db 的 stock_concept_map × THEME_CONCEPTS 反查（离线可用）。
    """
    if not symbol:
        return None
    s = str(symbol).strip().upper()
    ts = s[2:] + ("." + s[:2] if s[:2] in ("SH", "SZ", "BJ") else "") if len(s) >= 8 else s
    # 1) 生产 stock_confirm_result（子概念 → theme + stocks[].code）
    sc = _load("stock_confirm_result.json")
    for cname, v in (sc or {}).items():
        if not isinstance(v, dict):
            continue
        th = v.get("theme")
        if not th:
            continue
        for st in (v.get("stocks") or []):
            if str(st.get("code") or "").upper() in (ts, s):
                return th
    # 2) 离线反查：stock_pool.db + THEME_CONCEPTS
    try:
        import sys as _s
        import sqlite3
        for p in (os.getenv("STOCK_POOL_DB"),
                  os.path.join(DATA, "stock_pool.db"),
                  os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "apps", "paper-trading", "data", "stock_pool.db")):
            if not p or not os.path.exists(p):
                continue
            con = sqlite3.connect(p)
            rows = [r[0] for r in con.execute(
                "SELECT concept_name FROM stock_concept_map WHERE ts_code=?", (ts,))]
            con.close()
            if not rows:
                continue
            am = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "apps", "main_line")
            if am not in _s.path:
                _s.path.insert(0, am)
            from fusion_mainline import THEME_CONCEPTS
            cons = set(rows)
            for th, names in THEME_CONCEPTS.items():
                if cons & set(names):
                    return th
    except Exception:
        pass
    return None


def theme_structure(theme):
    """主题浪状态 → dict(verdict, gate, stage, wave_text)。数据缺失返回 {}。"""
    if not theme:
        return {}
    out = {}
    gf, gd = _latest("mainline_gate_")
    if gf:
        g = _load(os.path.basename(gf))
        for r in (g.get("rows") or []):
            if str(r.get("theme")) == theme:
                out.update({"verdict": r.get("verdict"), "gate": r.get("gate"), "gate_date": gd})
                break
    tf, td = _latest("trend_confirm_")
    if tf:
        t = _load(os.path.basename(tf))
        for r in (t.get("themes") or []):
            if str(r.get("theme")) == theme:
                ta = r.get("track_a") or {}
                out.update({"stage": ta.get("stage"), "new_high": ta.get("new_high"),
                            "track_date": td, "track_b_ratio": (r.get("track_b") or {}).get("ratio")})
                break
    return out


def systemic_block(wave=None):
    """大盘是否处于"真正系统性下跌"（仅此情形拦 253）。返回 (blocked, reason)。"""
    w = wave if wave is not None else load_wave()
    lv = str(w.get("level") or "").strip()
    sl = str(w.get("sub_level") or "").strip()
    if lv in SYSTEMIC_BLOCK_LEVEL:
        return True, "大盘 level=%s（下跌段）" % lv
    if sl in SYSTEMIC_BLOCK_SUB:
        return True, "大盘 sub_level=%s（狼大: 最后一跌/防御等企稳）" % sl
    return False, ""


def m5dump_allowed(symbol=None, wave=None):
    """253 是否允许 → (bool, reason)。

    判定：①大盘系统性护栏（仅真系统性下跌才拦）→ ②主题浪（主判据）→ ③数据缺失放行。
    """
    if os.getenv("WOLF_253_CONTEXT", "1").strip() in ("0", "false", "no"):
        return True, "闸门关闭(WOLF_253_CONTEXT=0)"

    sb, sreason = systemic_block(wave)
    if sb:
        return False, "语境禁止（系统性风险）: %s" % sreason

    theme = theme_of_symbol(symbol)
    st = theme_structure(theme)
    if not st or not st.get("stage"):
        if os.getenv("WOLF_253_CONTEXT_UNKNOWN", "allow").strip().lower() == "allow":
            return True, "主题浪数据缺失(theme=%s) → 放行" % theme
        return False, "主题浪数据缺失(theme=%s) → fail-closed" % theme

    stage = st.get("stage")
    verdict = st.get("verdict")
    if stage in THEME_STAGE_ALLOW:
        return True, "语境允许: 主题[%s] 结构 stage=%s verdict=%s（筑底/上行）" % (theme, stage, verdict)
    # 结构未确认 → 不接（狼大: 肯定不是急杀的时候买 / B反典型诱多别抄底）
    return False, "语境禁止: 主题[%s] 结构 stage=%s verdict=%s（未筑底, 不在急杀那一刻接）" % (
        theme, stage, verdict)


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else None
    th = theme_of_symbol(sym)
    print(json.dumps({"symbol": sym, "theme": th, "theme_structure": theme_structure(th),
                      "wave": {k: load_wave().get(k) for k in ("level", "sub_level", "operation")},
                      "m5dump_allowed": m5dump_allowed(sym)}, ensure_ascii=False, indent=1))
