# -*- coding: utf-8 -*-
"""dsh 无思考模式的主营方向校验（2026-09-18 用户指定：不要用关键词）。

关键词匹配会被概念表脏数据放行：实测 600658（电子城，主营产业地产/REITs）的概念里就带「半导体概念」；
而 dsh 按主营判定会正确回答「否」（本地实测：1 月买过的 15 只里 是 4 / 否 11，含两只重亏票 600658、603660）。
调用口径（已实测）：POST http://127.0.0.1:13101/chat，body {"message": ..., "thinking": false} → {"reply": "是"/"否"}。
结果按 (symbol, theme) 进程内缓存；任何异常 fail-open（保留该票）。
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Dict, List, Tuple

ENDPOINT = os.getenv("WOLF_MEMBER_LLM_URL", "http://127.0.0.1:13101/chat")
# 2026-09-19 提速：单次判定实测 ~2s（端点正常时），而 08:18 布腿器是**逐票串行**调用
#   ⇒ 一天几十票就是几分钟（0106 实测 switch 465s、0128 曾 958s）。三处改造：
#   ① 超时 25s → 8s（端点挂掉时最坏等待从 25s/票降到 8s/票）；
#   ② **落盘缓存**（跨臂/跨天复用：判定只依赖 票+主题+概念，与行情无关）——同一票同主题第二次起 0 网络；
#   ③ **负缓存**：失败落一条（默认 1800s 内不再重试同一票），避免整天反复等超时。
TIMEOUT = float(os.getenv("WOLF_MEMBER_LLM_TIMEOUT", "8"))
# 2026-09-20：超时重试（用户问"加上超时重试呢"）。实测超时是**排队**造成的（13101 串行：
#   纯串行 20 次延迟平坦 2.1–6.7s；2/3/6 并发 ⇒ 中位 5.5/9.6/9.1–13.0s ⇒ 延迟≈并发×单发），
#   队列几秒就排空 ⇒ 退避后重试几乎必然成功。但**必须带退避+抖动**：过载/405 时立刻重试会把
#   失败放大成 2–3 倍请求（11:16 那次 60×405 就是洪水）。默认重试 2 次、退避 2s→5s。
try:
    RETRY = max(0, int(os.getenv("WOLF_MEMBER_RETRY", "2")))
except Exception:
    RETRY = 2
try:
    RETRY_BACKOFF = max(0.0, float(os.getenv("WOLF_MEMBER_RETRY_BACKOFF", "2.0")))
except Exception:
    RETRY_BACKOFF = 2.0
try:
    NEG_TTL = float(os.getenv("WOLF_MEMBER_NEG_TTL", "1800"))
except Exception:
    NEG_TTL = 1800.0
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.getenv("WOLF_MEMBER_CACHE_DIR") or os.path.join(_REPO, ".dsh-tmp", "wolfbt", "member_cache")
_STATS: Dict[str, int] = {"calls": 0, "mem_hit": 0, "disk_hit": 0, "neg_hit": 0, "llm": 0, "err": 0,
                          "retry": 0, "batch": 0, "batch_miss": 0, "warn": 0}
import hashlib as _hashlib
import threading as _threading
import time as _time


def _silent_alert(where, exc=None):
    """静默点统一出口（账本 §9.545）：原来 `except …: pass/continue` 什么都不留 ⇒ 至少留痕。

    优先 `alert_hub.note_silent`（落盘 alerts.jsonl；QQ 受去重/限流约束）；不可用时退回 print。**绝不抛** ✓。
    """
    try:
        from app.services import alert_hub as _ah
        _ah.note_silent(where, exc)
    except Exception:
        try:
            print("[silent:%s] %s: %s" % (where, type(exc).__name__ if exc is not None else "",
                  str(exc)[:110] if exc is not None else ""), flush=True)
        except Exception:
            pass

_LOCK = _threading.Lock()


def stats() -> Dict[str, int]:
    return dict(_STATS)


# 2026-09-20 修：**缓存 key 与 prompt 必须用同一个概念切片**。
#   旧实现 key 取 `concepts[:20]`、prompt 只取 `concepts[:12]` ⇒ 两条调用路径（预热用 PG 概念表、
#   confirm 用当天沙箱库概念表）只要前 12 位相同、13–20 位不同，就会为**同一个 prompt** 付两次外呼。
#   实测：抽 40 只票有 24 只两边概念集合不同（27 vs 25、13 vs 14…）⇒ 重复判定不可避免。
#   ⇒ 现在两边都用 `_PROMPT_CONCEPTS`，key 由"prompt 真正可见的那部分"派生，同 prompt 必同 key。
_PROMPT_CONCEPTS = 12


def _prompt_concepts(concepts) -> str:
    """prompt 里实际会展示的概念串（key 必须由它派生，见上）。"""
    return "/".join([str(x) for x in (concepts or [])][:_PROMPT_CONCEPTS])


def batch_timeout() -> float:
    """批量判定的超时（秒）。默认沿用单票超时 ⇒ **与现状一致**；
    `WOLF_MEMBER_BATCH_TIMEOUT` 可单独放宽（实测 n=25 批量要 18.9s，卡在 20s 边缘 ⇒ 必然超时后退化成逐票兜底）。
    """
    try:
        return max(3.0, float(os.getenv("WOLF_MEMBER_BATCH_TIMEOUT", str(TIMEOUT))))
    except Exception:
        return TIMEOUT


def fail_fast() -> bool:
    """批量失败后**不再逐票兜底**（`WOLF_MEMBER_FAIL_FAST=1`，库内默认 0＝现状）。

    为什么：0212 实测预热 1215.66s，其中 **18 次逐票兜底 × ~42s ≈ 756s** 全是白等——
    批量已经因为排队超时，逐票问同一个串行服务几乎必然再超时，而且每票还要 ×2 次尝试 + 退避。
    语义不变（都 fail-open 放行），只是不再对同一个打不通的服务逐票重试。
    """
    return str(os.getenv("WOLF_MEMBER_FAIL_FAST", "0")).strip().lower() in ("1", "true", "yes", "on")


def batch_retry() -> int:
    """整批重试次数（`WOLF_MEMBER_BATCH_RETRY`，默认 0＝不重试）。整批重试比逐票重试划算得多：
    一次请求覆盖 N 只，队列几秒就排空。
    """
    try:
        return max(0, int(os.getenv("WOLF_MEMBER_BATCH_RETRY", "0")))
    except Exception:
        return 0


def neg_put(symbol: str, concepts, theme: str, why: str = "batch_fail") -> None:
    """把 (票, 主题) 写成**负缓存**（fail-open 放行 + 短 TTL），供批量失败后整批落盘。"""
    try:
        _disk_put(_ckey(symbol, theme, concepts), (True, "neg_cache"), err=why)
        with _LOCK:
            _CACHE[(str(symbol), str(theme))] = (True, "neg_cache")
    except Exception as _e_sil1:
        _silent_alert("theme_member_llm.py:103", _e_sil1)


def _ckey(symbol: str, theme: str, concepts) -> str:
    raw = "%s|%s|%s" % (str(symbol).upper(), str(theme), _prompt_concepts(concepts))
    return _hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _disk_get(k: str):
    try:
        with open(os.path.join(CACHE_DIR, "%s.json" % k), encoding="utf-8") as f:
            rec = json.load(f) or {}
    except Exception:
        return None
    if rec.get("neg"):
        _age = _time.time() - float(rec.get("at") or 0)
        # 2026-09-20 修：**回放把进程时钟钉在回放日** ⇒ 上次跑到 0119 写的 neg 条目（at=2026-01-19）
        #   在重跑 0105 时 age 是**负数**，旧逻辑判"新鲜" ⇒ 继续 fail-open 且永不重试（实测踩到）。
        #   ⇒ `age < 0`（at 在未来）一律视为过期，重试。
        if 0 <= _age <= NEG_TTL:
            _STATS["neg_hit"] += 1
            return (True, "neg_cache")          # 负缓存命中：直接 fail-open，不再等超时
        return None
    if isinstance(rec.get("reply"), str):
        return (bool(rec.get("keep", True)), str(rec.get("reply")))
    return None


def _disk_put(k: str, out, err: str = "") -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = os.path.join(CACHE_DIR, "%s.tmp" % k)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"keep": bool(out[0]), "reply": str(out[1]), "neg": bool(err),
                       "err": err[:120], "at": _time.time()}, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(CACHE_DIR, "%s.json" % k))
    except Exception:
        _STATS["err"] += 1


def _ask_llm(prompt: str, timeout: Optional[float] = None) -> str:
    """打一次 dsh（POST /chat），失败**退避重试** `WOLF_MEMBER_RETRY` 次后才抛异常。

    2026-09-20 实测坑：跑批的**子进程**里同一 URL、同一 prompt 会稳定收到 `405 Method Not Allowed`，
    而我在同机手测（env -i / 带代理环境）都是 200 ⇒ 因此失败必须带 URL/方法/响应体证据（见下）。

    为什么重试有效：超时是**排队**造成的（13101 串行 ⇒ 延迟≈并发×单发），队列几秒就排空。
    为什么必须退避+抖动：过载/405 时立刻重试会自我放大（1 次失败变 2–3 倍请求）。
    退避序列：backoff × 2**(i-1)，每次 ±40% 抖动（由 prompt+次数派生，确定性、便于复现）。
    """
    last = None
    for _i in range(RETRY + 1):
        if _i:
            _base = RETRY_BACKOFF * (2 ** (_i - 1))
            _r = _hashlib.sha1(("%s|%d" % (str(prompt)[:64], _i)).encode()).digest()[0] / 255.0
            try:
                _time.sleep(max(0.0, _base * (1.0 + 0.4 * (2.0 * _r - 1.0))))
            except Exception as _e_sil2:
                _silent_alert("theme_member_llm.py:161", _e_sil2)
            _STATS["retry"] += 1
        try:
            _STATS["llm"] += 1
            req = urllib.request.Request(ENDPOINT, data=json.dumps({"message": prompt, "thinking": False}).encode(),
                                         headers={"Content-Type": "application/json"})
            _to = float(timeout) if timeout else TIMEOUT
            with urllib.request.urlopen(req, timeout=_to) as fh:
                return str(json.load(fh).get("reply", "")).strip()
        except Exception as e:
            last = e
            # 2026-09-20 诊断（实测踩到"跑批进程 405、同 URL 手测 200"）：失败必须自带证据 ——
            #   URL/方法/状态码/响应体，否则只能靠猜。附在异常里由 _warn 打印。
            try:
                _st = getattr(e, "code", None)
                _body = ""
                if hasattr(e, "read"):
                    _body = str(e.read()[:100])
                e.dsh_diag = "url=%s method=%s status=%s body=%s" % (
                    getattr(req, "full_url", ENDPOINT), getattr(req, "method", "?"), _st, _body.replace("\n", " ")[:80])
            except Exception as _e_sil3:
                _silent_alert("theme_member_llm.py:182", _e_sil3)
    raise last if last else RuntimeError("member_llm: unknown failure")


def _warn(msg: str) -> None:
    """失败不再静默：fail-open 是设计，但必须留痕（否则闸门被关掉没人知道 —— 2026-09-20 踩的坑）。"""
    _STATS["warn"] += 1
    if _STATS["warn"] <= 20:
        print("[member_llm] ⚠️ %s" % str(msg)[:160], flush=True)
    elif _STATS["warn"] % 50 == 0:
        print("[member_llm] ⚠️ 累计 %d 次判定失败（fail-open 放行；详见 member_cache 的 neg 条目）"
              % _STATS["warn"], flush=True)


def member_prompt(symbol: str, concepts, theme: str) -> str:
    """单票判定的 prompt（与批量版同义；差别只在结尾"只回答两个字" vs "只输出 N 行"）。"""
    cs = _prompt_concepts(concepts) or "无"
    return ("判断这只A股的**主营业务**是否与【" + str(theme) + "】方向**完全无关**（只看主营/产业链归属；"
            "概念表可能有脏数据，仅概念沾边不算有关，例如主营为地产/REITs/医药/消费/计算机设备/工程等）。"
            "代码 " + str(symbol) + "；概念：" + cs + "。只回答两个字：无关 或 有关。")


def verdict_from_reply(reply: str) -> Tuple[bool, str]:
    """dsh 回复 → (保留?, 原文)：以"无关"开头 ⇒ 拒；否则保留（fail-open）。"""
    r = str(reply or "").strip()[:8]
    return (not r.startswith("无关"), r or "空回复")


def cached(symbol: str, concepts, theme: str):
    """只读缓存（内存+落盘），**不发网络**。命中返回 (keep, why)，否则 None。"""
    key = (str(symbol), str(theme))
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    _d = _disk_get(_ckey(key[0], key[1], concepts))
    if _d is not None:
        with _LOCK:
            _CACHE[key] = _d
    return _d


def needs_dsh(symbol: str, concepts, theme: str) -> bool:
    """该 (票, 主题) 是否**需要**问 dsh：白名单 / 主类 reject 都不需要（确定性定案）。

    批量校验用它筛掉不必问的票 —— 否则会把"主类已拒"的票也塞进 prompt 白花 token，
    还可能让 dsh 的结论覆盖确定性规则（口径不允许，见 is_member 的注释）。
    """
    # ★ 账本 §9.670 ✓（用户：「过滤里去掉退市、ST、北交所的票为什么没有对这里生效？」✗）
    #   查明 ✓：过滤**确实开着**（pins:152-156 WOLF_UNIVERSE_CLEAN* 全 =1 ✓），但它装在
    #     `leg_gate.approve()`（**腿批准闸** ✓）里，而 approve 是在**成员判定之后**才调用的 ✗
    #     ⇒ **LLM 判定在前、卫生过滤在后** ✗ ⇒ 脏票已经被判过了（白花调用 ✗）
    #       （过滤本身有效 ✓：实测回测腿里 **0 只北交所** ✓，没有脏腿 ✓）
    #   ⇒ 修法 ✓：把**同一口径**（universe_clean.judge = 退市/ST/北交所 ✓）搬到
    #     **LLM 路径的共同入口**（本函数 ✓，批量与单票都先问它 ✓）⇒ 判定与过滤同源 ✓
    #   ⇒ fail-open ✓：import/判定异常 ⇒ 不跳过（绝不因过滤故障而少判 ✓）
    try:
        import universe_clean as _uc_all
        if _uc_all.enabled():
            _ok_all, _why_all = _uc_all.judge(str(symbol))
            if not _ok_all:
                return False
    except Exception as _e_uc:
        _silent_alert("theme_member_llm.py:universe_clean", _e_uc)
    except Exception as _e_sil4:
        _silent_alert("theme_member_llm.py:233", _e_sil4)
    try:
        from theme_main_class import decide as _mc
        _v, _ = _mc(str(symbol), str(theme), concepts)
        return _v != "reject"
    except Exception:
        return True


def store_result(symbol: str, concepts, theme: str, keep: bool, reply: str) -> None:
    """把（批量得到的）结论写进**与单票同一个缓存**：内存 + 落盘、key 不变。

    这样 `stock_confirm_judge` 的逐票路径与 `leg_gate.approve` 一行不改也能吃到批量结果。
    """
    out = (bool(keep), str(reply)[:32] or "空回复")
    key = (str(symbol), str(theme))
    with _LOCK:
        _CACHE[key] = out
    _disk_put(_ckey(key[0], key[1], concepts), out)
# 开关（与 stock_confirm_judge 同一口径，2026-09-19）：库内默认 **关**（生产行为不变），
# 回测（BT_ASOF_FETCH 存在）默认开；本臂显式置 0 即"关掉主营校验、保留可买门"。
# 2026-09-19 用户澄清（重要）：本闸的目的是**拦"概念错配"的票**——首例就是电子城(SH600658)：
#   PG `stock_concept_map` 把它挂在「半导体概念」下（同时挂着 房地产/产业地产/REITs），
#   同类的还有 华泰股份(造纸→挂着 AI语料/人工智能)、科德教育(教育→挂着 AI芯片/国产芯片)、
#   日科化学(化工→挂着 东数西算)…… 狼大是按**板块内正宗度/龙头**选票的
#   （docs/wolf-buy-parameter-ledger.md:1050「龙头 核心标的要素构成：政策点火>强赚钱效应>题材联动性>板块内体量大>基本面」），
#   不会因为名字带"电子"就当成科技票 ⇒ 本闸是语料**明确要求**的正宗度校验，
#   只是语料要的「主营构成」数据我们缺（docs/wolf-buy-gap-audit.md:62 B4「⛔没落（数据缺口：主营构成）」），
#   故用 dsh 无思考模式判"主营与该主题是否完全无关"作**代理**。
# 开关口径：库内默认关（生产零影响），回测默认开；显式 WOLF_THEME_MEMBER_CHECK=0/1 可覆盖。
# ⚠️ 因它是"代理"判定，dsh=否 的标的会落成 `member_reject_<day>.jsonl` 复核清单（见 leg_gate），
#    避免把真主线误判后无人察觉。
# 人工白名单（默认空）：概念表(stock_concept_map)本身有大量错配（化工挂"液冷服务器"、造纸挂"AI语料"），
# 所以**不能用概念自动放行**（实测：用"先进封装/中芯概念/CPO/液冷/数据中心"等硬概念放行会把 66 只里的 32 只
# 放进来，其中包括日科化学(化工)、永和股份(氟化工)）⇒ 放行只能靠**人工点名**。
# 复核清单产出于 leg_gate._record_member_reject → `member_reject_<day>.jsonl`；
# 点名后写进 WOLF_MEMBER_WHITELIST（逗号分隔，SEP 支持 SH600658/600658.SH/600658 三种写法）。
WHITELIST = {x.strip().upper() for x in (os.getenv("WOLF_MEMBER_WHITELIST") or "").split(",") if x.strip()}


def _in_whitelist(symbol) -> bool:
    """白名单命中：接受 SH603203 / 603203.SH / 603203 三种写法。"""
    s = str(symbol or "").strip().upper().replace(" ", "")
    if not s:
        return False
    code = s
    if "." in s:
        _a, _dot, _b = s.partition(".")
        if len(_a) == 6 and _b in ("SH", "SZ", "BJ"):
            code = _b + _a                       # 603203.SH → SH603203
    s6 = code[2:] if (len(code) >= 8 and code[:2] in ("SH", "SZ", "BJ")) else code
    cands = {s, code, s6}
    for _c in list(cands):
        cands.add(_c + ".SH")
        cands.add(_c + ".SZ")
        cands.add(_c + ".BJ")
    return bool(cands & WHITELIST)


# ④ 2026-09-20 用户拍板「只做第四步，不要用代码规则收紧」：
#    主类规则 **allow 不再有绝对定案权** —— 仍过一次 dsh 主营校验，dsh 说"无关"就拒。
#    背景：主类词取自东财**概念标签**（不是行业字段），面板股 TCL科技(SZ000100) 因概念里含
#    "半导体/国产芯片" 被判 主类=半导体 ⇒ allow ⇒ dsh 被短路（实测 dsh 对 TCL 在两个主题下都判"无关"）。
#    库内默认 0 = 旧行为；回测 pins 打开 1。
DSH_VETO = str(os.getenv("WOLF_MEMBER_DSH_VETO", "0")).strip().lower() in ("1", "true", "yes", "on")

ON = str(os.getenv("WOLF_THEME_MEMBER_CHECK",
                   "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")

_CACHE: Dict[Tuple[str, str], Tuple[bool, str]] = {}


def is_member(symbol: str, concepts: List[str], theme: str) -> Tuple[bool, str]:
    """判定该票是否属于 theme 方向（按主营/产业链）。返回 (保留?, 判定原文)。"""
    _mc_allow = ""
    if _in_whitelist(symbol):
        return True, "whitelist"      # 人工点名放行（概念表不可信，见文件头注释）
    # ① 确定性主类规则（2026-09-19 D）：allow/reject 直接定案，只有 defer（无主类词）才落到 dsh。
    #    实测：真半导体(603893/603986/688981)主类=半导体 ⇒ allow；电子城=产业地产、华泰=造纸、
    #    科德=教育、日科=化工、快克智能/科瑞技术=机械设备·工控·工业母机 ⇒ reject（同一批票在
    #    「机器人/智能制造」主题下则是 allow —— 错的是主题标签，不是票）。
    try:
        from theme_main_class import decide as _mc
        _v, _mwhy = _mc(str(symbol), str(theme), concepts)
        if _v == "reject":
            return False, "主类:%s" % _mwhy
        if _v == "allow":
            # ── 账本 §9.500 ✓（用户「起新臂观察比亚迪低吸」现场定位 ✓）：**数据分放行不再被 dsh 否决** ✓
            #   实测（0302 in 臂 ✓）：`decide()` 判 allow（数据分 0.744 通过 ✓），
            #   但这里 `DSH_VETO=1` ⇒ **继续走 dsh** ✗ ⇒ dsh 否决 ⇒ `member_reject` 写「新能源/电池」✗
            #   ⇒ 与"数据驱动、不看名单"的口径矛盾 ✗（§9.496 同一思路：单一、确定的来源不该被二次否决 ✓）
            #   开关 `WOLF_SCORE_ALLOW_NO_VETO`（**默认 1** ✓，置 0 ＝ 恢复旧行为 ✓）
            if ("数据分通过" in str(_mwhy)
                    and str(os.getenv("WOLF_SCORE_ALLOW_NO_VETO", "1")).strip().lower() in ("1", "true", "yes", "on")):
                return True, "主类:%s" % _mwhy
            if not DSH_VETO:
                return True, "主类:%s" % _mwhy          # 旧行为（库内默认）
            _mc_allow = "主类allow→dsh:%s" % _mwhy      # ④：继续走 dsh，dsh 有权否决
    except Exception as _e_sil5:
        _silent_alert("theme_member_llm.py:332", _e_sil5)

    def _tag(res):                                       # dsh 否决时把"主类原判 allow"写进理由，便于审计
        try:
            keep, why = res
        except Exception:
            return res
        if _mc_allow and not keep:
            return False, "%s ｜ %s" % (why, _mc_allow)
        return res

    if not ON:
        return True, (_mc_allow or "off")      # 主营校验整体关闭（WOLF_THEME_MEMBER_CHECK=0）
    key = (str(symbol), str(theme))
    with _LOCK:
        if key in _CACHE:
            _STATS["mem_hit"] += 1
            return _tag(_CACHE[key])
    _STATS["calls"] += 1
    _ck = _ckey(key[0], key[1], concepts)
    _d = _disk_get(_ck)
    if _d is not None:
        _STATS["disk_hit"] += 1
        with _LOCK:
            _CACHE[key] = _d
        return _tag(_d)
    cs = _prompt_concepts(concepts) or "无"      # 仅用于日志；prompt 由 member_prompt 生成（同一 helper）
    # 2026-09-19 用户口径调整：**只拦"主营与主题完全无关"的**（如地产/REITs/医药/消费/计算机设备），
    # 允许"沾边概念"票（AI 芯片/CPO/OLED 这类）进入——它们此前是 1 月的盈利来源之一。
    prompt = member_prompt(str(symbol), concepts, str(theme))
    try:
        reply = _ask_llm(prompt)                      # 内部带退避重试（WOLF_MEMBER_RETRY）
        out = verdict_from_reply(reply)
        _disk_put(_ck, out)
    except Exception as e:
        out = (True, "ERR " + str(e)[:40])
        _disk_put(_ck, out, err="%s: %s" % (type(e).__name__, str(e)[:60]))   # 负缓存（重试已用尽）
        _STATS["err"] += 1
        _warn("判定失败(重试已用尽，fail-open 放行) %s × %s：%s ｜ %s"
              % (symbol, theme, type(e).__name__, getattr(e, "dsh_diag", "")))
    with _LOCK:
        _CACHE[key] = out
    return _tag(out)


def prefetch(items, workers: int = int(os.getenv("WOLF_MEMBER_PREFETCH_WORKERS", "6") or 6)) -> int:
    # ★ 账本 §9.657 ✓：**默认 6 路并发** ✗ ⇒ 单模型排队 ✗ ⇒ 延迟 ×N ✓
    #   ⇒ `WOLF_MEMBER_PREFETCH_WORKERS` 可调 ✓（库内默认仍 6 ⇒ 生产零影响 ✓）
    """并发预热判定缓存（items = [(symbol, concepts, theme), ...]）。返回预热条数。

    为什么需要：布腿器是逐票串行调用（每票 ~2s）⇒ 一天几十票就是几分钟（0106 switch 实测 465s）。
    这里用线程池并发打同一个 dsh 端点（默认 6 并发），配合落盘缓存 ⇒ 跨天/跨臂基本 0 成本。
    任何异常都吞掉（fail-open，不影响布腿）。
    """
    try:
        from concurrent.futures import ThreadPoolExecutor
        _seen, _jobs = set(), []
        for it in (items or []):
            try:
                sym, ccs, th = it[0], it[1], it[2]
            except Exception as _e_sil6:
                _silent_alert("theme_member_llm.py:391", _e_sil6)
                continue
            k = (str(sym), str(th))
            if k in _seen:
                continue
            _seen.add(k)
            _jobs.append((sym, ccs, th))
        if not _jobs:
            return 0
        # ★ 账本 §9.660 ✓（用户：「让我知道布腿进度、候选进度、总计多少腿、布了多少、还剩多少」）：
        #   这一段是布腿里**最久**的 ✗（每条候选都要问一次 dsh ✓）⇒ 之前**一句进度都不打** ✗
        #   ⇒ 现在**逐条报进度 ＋ 已用时长 ＋ 均值 ＋ 预计剩余** ✓（串行时可精确外推 ✓）
        import time as _t7
        import threading as _th7
        _t7_0 = _t7.time()
        _tot = len(_jobs)
        _cnt = {"n": 0}
        _lk7 = _th7.Lock()
        print("[member_llm] ★ 候选预热开始 %d 条（并发 %d）" % (_tot, max(1, int(workers))), flush=True)

        def _one7(a):
            try:
                _r7 = is_member(a[0], a[1], a[2])
            except Exception as _e_pf:
                _silent_alert("theme_member_llm.py:prefetch", _e_pf)
                _r7 = None
            with _lk7:
                _cnt["n"] += 1
                _n7 = _cnt["n"]
            if _n7 <= 30 or _n7 % 5 == 0 or _n7 == _tot:
                _el = _t7.time() - _t7_0
                _avg = _el / max(1, _n7)
                print("[member_llm] 预热 %d/%d %s × %s｜已 %.0fs｜均 %.1fs｜剩 ~%.0fs"
                      % (_n7, _tot, a[0], a[2], _el, _avg, _avg * (_tot - _n7)), flush=True)
            return _r7

        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
            list(ex.map(_one7, _jobs))
        print("[member_llm] ★ 候选预热完成 %d 条（%.0fs ✓）" % (_tot, _t7.time() - _t7_0), flush=True)
        return len(_jobs)
    except Exception:
        return 0
