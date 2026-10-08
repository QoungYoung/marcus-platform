# -*- coding: utf-8 -*-
"""quality_judge.py —— 「**agent 判质地**」（账本 §9.763 ✓，用户拍板接生产 ✓）

做什么 ✓：对**买腿首次建仓**问一次 agent（财报 ＋ 技术/K线 ＋ 它自己联网查的新闻）
  ⇒ 严格 JSON 判 `avoid/caution/ok` ⇒ ★ **只有 `avoid`（且理由含数字）才硬拦** ✗
  ⇒ `caution`/`ok` **不改变任何行为** ✗（只留痕 ✓ —— 验证实测：下跌趋势里 `caution` 几乎对所有票都给 ✗，
     拿它"少买"会把买腿全砍 ✗，所以 v1 不做降档 ✓）

★ 三条铁律 ✓：
  ① **失败一律 fail-open ＋ 醒目告警** ✓（任何取数/桥/解析失败 ⇒ 放行 ✓ 但**绝不静默** ✗）
  ② **后置校验**：`verdict=avoid` 但 `red_flags` **不含数字** ⇒ **降级为 caution** ⇒ 放行 ✓
     （防它"凭印象说 avoid" ✗ —— 这是验证阶段我们提的建议 ✓）
  ③ **生产用 `get_settings().PI_SERVER_URL`** ✓（= `http://dsh:3001/chat` ✓）
     ✗ **禁止**用 `127.0.0.1:13001`（那是**回测**的转发器 ✗）

开关 ✓：`WOLF_QUALITY_JUDGE`（★★ 默认 `'0'`＝关 ⇒ 未配 env 的部署**逐位不变** ✓）
      ＋ `WOLF_QUALITY_JUDGE_TIMEOUT`（默认 120 秒 ✓）
      ＋ `WOLF_QUALITY_JUDGE_CACHE_TTL_H`（默认 24 小时 ✓）
      ＋ `WOLF_QUALITY_JUDGE_CACHE_DIR`（默认 `<repo>/.cache/quality_judge` ✓）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_ENV = "WOLF_QUALITY_JUDGE"


def enabled() -> bool:
    return str(os.getenv(_ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def timeout_s() -> int:
    try:
        return int(float(os.getenv("WOLF_QUALITY_JUDGE_TIMEOUT", "120") or 120))
    except Exception:
        return 120


def ttl_h() -> float:
    try:
        return float(os.getenv("WOLF_QUALITY_JUDGE_CACHE_TTL_H", "24") or 24)
    except Exception:
        return 24.0


def _repo() -> str:
    ws = str(os.getenv("MARCUS_WORKSPACE") or "").strip()
    if ws and os.path.isdir(ws):
        return ws
    if os.path.isdir("/app/data"):
        return "/app"
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _cache_dir() -> str:
    d = str(os.getenv("WOLF_QUALITY_JUDGE_CACHE_DIR") or "").strip() or os.path.join(_repo(), ".cache", "quality_judge")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _warn(msg: str) -> None:
    line = "[QUALITY] " + str(msg)[:300]
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    for mod, fn in (("app.services.alert_hub", "note_silent"), ("app.services.gate_alarm", "note")):
        try:
            m = __import__(mod, fromlist=[fn])
            getattr(m, fn)("quality_judge", RuntimeError(line))
            return
        except Exception:
            continue


def _prompt_text() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quality_judge_prompt.md")
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read()
    except Exception as _e:
        _warn("提示词读取失败（用内置精简版）: %s" % str(_e)[:80])
        return ("你是质地审查员，只判「值不值得买」。只用我给出的数据，不许凭记忆补充事实。"
                "判据优先级：退市/ST > 连续两年归母净利为负 > 未分配利润大额为负 > 负债率异常高 > 近期暴涨 > 技术破位。"
                "只输出严格 JSON：{\"symbol\":\"\",\"verdict\":\"avoid|caution|ok\",\"confidence\":0,"
                "\"reasons\":[],\"red_flags\":[],\"data_used\":[]}")


def _df_rows(d: Any) -> List[Dict[str, Any]]:
    """★ 判空必须按 DataFrame 语义 ✓（不许 `if not d` ✗ —— 真值歧义 ✗）"""
    try:
        if d is None or getattr(d, "empty", True):
            return []
        return [dict(r) for _, r in d.iterrows()]
    except Exception:
        return []


def _fetch_financials(symbol: str) -> Dict[str, Any]:
    """财报（生产＝实时 ✓ ⇒ 直接取最近已披露 ✓ 无需 as-of ✓）"""
    out: Dict[str, Any] = {}
    try:
        sys.path.insert(0, _repo())
        from core.tushare_relay import relay_query  # type: ignore
    except Exception as _e:
        _warn("relay 不可用: %s" % str(_e)[:80])
        return {"ok": False, "why": "relay 不可用"}
    ts = symbol if "." in symbol else (symbol[2:] + "." + symbol[:2])
    for api, keys in (("fina_indicator", ("end_date", "netprofit_yoy", "debt_to_assets", "profit_dedt", "q_netprofit_yoy")),
                      ("income", ("end_date", "n_income_attr_p", "revenue", "total_revenue", "n_income"))):
        try:
            rows = _df_rows(relay_query(api, ts_code=ts))
            if not rows:
                continue
            rows.sort(key=lambda r: str(r.get("end_date") or ""), reverse=True)
            out[api] = [{k: r.get(k) for k in keys if k in r} for r in rows[:4]]
        except Exception as _e2:
            _warn("%s 取数失败 %s: %s" % (api, ts, str(_e2)[:70]))
    out["ok"] = bool(out.get("income") or out.get("fina_indicator"))
    if not out["ok"]:
        out["why"] = "财报未取到"
    return out


def _fetch_tech(symbol: str) -> Dict[str, Any]:
    """技术/K线（近 60 个交易日 ⇒ 均线/20 日涨跌/20 日新低/量比 ✓）"""
    try:
        sys.path.insert(0, _repo())
        from core.tushare_relay import relay_query  # type: ignore
    except Exception:
        return {"ok": False, "why": "relay 不可用"}
    ts = symbol if "." in symbol else (symbol[2:] + "." + symbol[:2])
    try:
        rows = _df_rows(relay_query("daily", ts_code=ts))
        if not rows:
            return {"ok": False, "why": "日线未取到"}
        rows.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)
        cl = [float(r.get("close")) for r in rows[:60] if r.get("close") is not None]
        vo = [float(r.get("vol")) for r in rows[:60] if r.get("vol") is not None]
        if len(cl) < 21:
            return {"ok": False, "why": "日线不足 21 根"}
        ma = lambda n: round(sum(cl[:n]) / float(n), 3) if len(cl) >= n else None
        low20 = min(cl[:20])
        return {"ok": True, "close": cl[0], "ma5": ma(5), "ma20": ma(20), "ma60": ma(60),
                "chg20_pct": round((cl[0] / cl[20] - 1.0) * 100.0, 2) if len(cl) > 20 else None,
                "low20": round(low20, 3), "is_new_low20": bool(cl[0] <= low20 + 1e-9),
                "vol_ratio5": round(vo[0] / (sum(vo[1:6]) / 5.0), 2) if len(vo) >= 6 and sum(vo[1:6]) > 0 else None,
                "bars": len(cl)}
    except Exception as _e:
        _warn("日线取数失败 %s: %s" % (ts, str(_e)[:70]))
        return {"ok": False, "why": "日线取数异常"}


def _post_validate(j: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """★ 后置校验 ✓：avoid 必须至少一条 red_flag **含数字** ✗ 否则降级 ✓"""
    try:
        v = str(j.get("verdict") or "").strip().lower()
        if v != "avoid":
            return j, ""
        rf = j.get("red_flags") or []
        if isinstance(rf, str):
            rf = [rf]
        has_num = any(re.search(r"\d", str(x)) for x in rf)
        if has_num:
            return j, ""
        j["verdict"] = "caution"
        j["_downgraded"] = "avoid→caution（red_flags 无数字 ✗）"
        return j, "avoid 但 red_flags 无数字 ⇒ 降级为 caution"
    except Exception as _e:
        return j, "后置校验异常(按原样) %s" % str(_e)[:60]


def _parse_json(reply: str) -> Optional[Dict[str, Any]]:
    s = str(reply or "")
    i, k = s.find("{"), s.rfind("}")
    if i < 0 or k <= i:
        return None
    try:
        j = json.loads(s[i:k + 1])
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def _call_bridge(msg: str) -> Optional[str]:
    """★ 用 get_settings().PI_SERVER_URL ✓（生产= http://dsh:3001/chat ✓；✗ 不用 13001 ✗）"""
    url = ""
    # ★ 账本 §9.763 修正 ✓：settings 在 **`app.config`** ✗（先前误写成 `app.core.config` ✗
    #   ⇒ 导入失败 ⇒ URL 为空 ⇒ **桥根本没被调用** ✗ ⇒ 断言"假通过" ✗）
    #   取法照项目既有 ✓：`from app.config import get_settings` ✓（同 `database.py:10` ✓、
    #   `qqbot_service.py:31` ✓）；兜底 `127.0.0.1:3001/chat` ✓（本机/容器都通 ✓）
    try:
        sys.path.insert(0, _repo())
        sys.path.insert(0, os.path.join(_repo(), 'backend'))
        from app.config import get_settings  # type: ignore
        url = str(getattr(get_settings(), "PI_SERVER_URL", "") or "")
    except Exception as _e:
        _warn("取 PI_SERVER_URL 失败(用兜底 3001): %s" % str(_e)[:80])
        url = "http://127.0.0.1:3001/chat"
    if not url:
        _warn("PI_SERVER_URL 为空 ⇒ 放行（fail-open ✓）")
        return None
    try:
        import urllib.request
        body = json.dumps({"message": msg, "session_id": "quality-judge", "mode": "trade"}).encode("utf-8")
        req = urllib.request.Request(url if url.endswith("/chat") else url.rstrip("/") + "/chat",
                                     data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_s()) as resp:
            return json.loads(resp.read().decode("utf-8")).get("reply")
    except Exception as _e:
        _warn("桥调用失败: %s" % str(_e)[:100])
        return None


def _cache_path(symbol: str, day: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]", "_", str(symbol))
    return os.path.join(_cache_dir(), "%s_%s.json" % (safe, day))


def _cache_get(symbol: str, day: str) -> Optional[Dict[str, Any]]:
    p = _cache_path(symbol, day)
    try:
        if not os.path.exists(p):
            return None
        if time.time() - os.path.getmtime(p) > ttl_h() * 3600.0:
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or None
    except Exception:
        return None


def _cache_put(symbol: str, day: str, val: Dict[str, Any]) -> None:
    try:
        p = _cache_path(symbol, day)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(val, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def _today() -> str:
    try:
        return time.strftime("%Y%m%d")
    except Exception:
        return "00000000"


def judge(symbol: str, day: Optional[str] = None) -> Tuple[bool, str]:
    """★ 返回 `(blocked, why)` ✓：blocked=True **仅当** verdict=avoid 且 red_flags 含数字 ✓

    · 开关关 ✓ ⇒ `(False, '')` ✓
    · 任何失败 ✓ ⇒ `(False, why)` ＋ 醒目告警 ✓（fail-open ✓）
    """
    if not enabled():
        return False, ""
    sym = str(symbol or "").strip()
    if not sym:
        return False, ""
    d8 = str(day or _today()).replace("-", "")[:8]
    hit = _cache_get(sym, d8)
    if hit is not None:
        v = str(hit.get("verdict") or "")
        return (v == "avoid"), ("[cache] " + str(hit.get("why") or v))
    fin = _fetch_financials(sym)
    tech = _fetch_tech(sym)
    if not fin.get("ok") and not tech.get("ok"):
        why = "数据未取到（财报:%s／技术:%s）⇒ 放行" % (fin.get("why"), tech.get("why"))
        _warn("%s %s" % (sym, why))
        return False, why
    payload = {"symbol": sym, "今日": d8, "财报": fin, "技术": tech,
               "要求": "只用以上数据＋你可联网查到的新闻；缺的写「未提供/未取到」；只输出严格 JSON"}
    reply = _call_bridge(_prompt_text() + "\n\n【待审数据】\n" + json.dumps(payload, ensure_ascii=False, default=str))
    j = _parse_json(reply or "")
    if not j:
        _warn("%s 判质地解析失败 ⇒ 放行（fail-open ✓）reply=%s" % (sym, str(reply)[:80]))
        return False, "解析失败 ⇒ 放行"
    j, note = _post_validate(j)
    if note:
        _warn("%s %s" % (sym, note))
    v = str(j.get("verdict") or "").strip().lower()
    why = "%s(conf=%s) %s" % (v, j.get("confidence"), "；".join([str(x) for x in (j.get("red_flags") or [])][:3]))
    _cache_put(sym, d8, {"verdict": v, "why": why[:300], "raw": j})
    if v == "avoid":
        return True, why
    return False, ""
