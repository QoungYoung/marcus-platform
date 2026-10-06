# -*- coding: utf-8 -*-
"""bt_llm_replay.py — 回测用的 LLM「录制/回放」层（**wave agent / 交易腿 agent 共用**）。

## 为什么需要
`apps/main_line/wave_agent.py`（波浪判定）、`jobs/rotation_switch_agent.py`（主线内切换·布腿 agent）
与生产做T链的**交易腿 agent**（`app.services.t_bridge.wake_agent` → `/chat`）决策都来自
dsh `/chat`（LLM）→ **非确定**。全拟真回测要可复现，就必须：
  ① **record**（第一次跑）：真实外呼，同时把 `(agent, as_of, prompt, reply, ts)` 落盘；
  ② **replay**（之后所有跑）：只读缓存，命中即用；**未命中直接报错**（绝不静默降级成"这天没有 agent"，
     否则回测会静默少一层决策、结论不可比）。
另外缓存里记 `prompt_sha1`：replay 时若调用方给的 prompt 与录制时不一致（= 输入数据/PIT 口径漂移），
按 `BT_LLM_STRICT=1`（默认）**报错**，置 0 只警告 —— 这是"回测与生产同输入"的自检。

## 一次/多次调用（2026-09-17 扩展：交易腿 agent）
wave agent 每交易日**只调 1 次** `/chat`，而交易腿 agent（`t_bridge.wake_agent`）是**每触发一次调一次**，
一天几十次。原实现"一个 (agent, as_of) 文件只存一条记录"会让第二次调用就被当成 prompt 漂移 → 本层扩展为
**按 `prompt_sha1` 索引的多条记录**（缓存文件从 `{...单条...}` 变为 `{"calls":[...]}`；
**单条时仍写旧的单条格式**，波浪 agent 的既有缓存/工具零影响）：
  · 命中规则：sha1 精确匹配 → 取一条**未被本次运行用过**的记录（同 prompt 重复触发也能各自命中）；
  · replay 未命中（缓存里没有这个 prompt）→ 报错（strict）或警告并按旧语义退化为"第一条未用记录"（strict=0）；
  · record 未命中 → 真实外呼并**追加**记录（旧记录永不覆盖 —— 旧录制是当时口径的证据）；
  · `summary()` 额外给 `unused`（本次未用到的旧记录数 = 触发序列与录制时不同，漂移信号）。

## 拦截面（install）
   · 模块用 `requests.post` → 换 `requests` 为代理（wave agent / rotation_switch_agent / low_logic_agent）；
   · 模块用 `urllib.request.urlopen` → 换 `urllib` 为代理（**生产 `t_bridge.wake_agent` 走的是 urllib，
     不是 requests**；`/chat` 与 `/conditions/generate` 两个端点都在这里被录/放）。

## 用法
```python
from bt_llm_replay import LLMReplay
llm = LLMReplay(agent="t_leg", as_of="20260107", cache_dir="/app/data/_bt_llm_year2")  # 模式读 env
llm.install(t_bridge); llm.install(t_ai_agent)     # requests / urllib 两种调用面都换掉
... 正常跑生产链 ...
llm.summary()                                      # 本日命中/新录制/报错计数
```

env：
  `BT_LLM_MODE`  = `record`（默认，外呼+落盘） / `replay`（只读缓存）
  `BT_LLM_CACHE` = 缓存根目录（默认 `<DATA_DIR>/_bt_llm`）
  `BT_LLM_STRICT`= 1（默认）prompt 不一致即报错；0 只警告
"""
from __future__ import annotations

import hashlib
import json
import os
import time

# ★ 账本 §9.684 ✓：已焐热的 session_id 集合（模块级 ✓；**必须在 from __future__ 之后** ✗）
_PREWARMED = set()
_rr = [0]   # ★ §9.691：会话槽轮转计数器（配合 WOLF_AGENT_SESSION_SLOTS ✓）
from typing import Any, Dict, List, Optional


class LLMReplayError(RuntimeError):
    pass


class _FakeResponse:
    """最小 HTTP 响应替身：实现 agent 用到的 `.json()` / `.raise_for_status()` / `.text`
    以及 **urllib 风格**的 `.read()` + 上下文管理器（`with urlopen(...) as resp`）。"""

    def __init__(self, payload: Dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status
        self.status = status
        self.text = json.dumps(payload, ensure_ascii=False)
        self.headers: Dict[str, str] = {"Content-Type": "application/json"}

    def json(self) -> Dict[str, Any]:
        return self._payload

    def read(self) -> bytes:
        return self.text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise LLMReplayError("replay 缓存里的 HTTP 状态 %s" % self.status_code)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _RequestsProxy:
    """把目标模块的 `requests` 换成代理：`post` 走本层，其余属性透传真实 requests。"""

    def __init__(self, real, handler):
        self._real = real
        self._handler = handler

    def post(self, url, **kw):
        return self._handler(url, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _UrlopenProxy:
    """把 `urllib.request.urlopen` 换成代理（`Request` 等其它入口透传给真 urllib）。"""

    def __init__(self, real_request, handler):
        self._real = real_request
        self._handler = handler

    def urlopen(self, req, *a, **kw):
        return self._handler(req, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _UrllibProxy:
    """把目标模块的 `urllib` 换成代理：只改 `urllib.request.urlopen`，其余透传。"""

    def __init__(self, real_urllib, handler):
        self._real = real_urllib
        self.request = _UrlopenProxy(getattr(real_urllib, "request", None), handler)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _prompt_of(body: Any) -> str:
    """一次 LLM 调用的"prompt 指纹源"。

    · `/chat`：body.message（生产把全部上下文拼进这一条 message）；
    · 其它端点（如 `/conditions/generate`）：整个 body 的规范化 JSON（没有 message 字段）。
    """
    if isinstance(body, dict):
        msg = body.get("message")
        if isinstance(msg, str) and msg:
            return msg
        return json.dumps(body, ensure_ascii=False, sort_keys=True)
    return json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)


class LLMReplay:
    def __init__(self, agent: str, as_of: str, cache_dir: Optional[str] = None,
                 mode: Optional[str] = None):
        self.agent = str(agent)
        self.as_of = str(as_of)
        self.mode = str(mode or os.getenv("BT_LLM_MODE", "record")).strip().lower()
        if self.mode not in ("record", "replay"):
            raise LLMReplayError("BT_LLM_MODE 只能是 record / replay，得到 %r" % self.mode)
        self.cache_dir = cache_dir or os.getenv(
            "BT_LLM_CACHE", os.path.join(os.environ.get("DATA_DIR", "/app/data"), "_bt_llm"))
        self.strict = str(os.getenv("BT_LLM_STRICT", "1")).strip() not in ("0", "false", "no")
        # `BT_LLM_FRESH=1`：**不读缓存**，每次都真问 LLM（仍然把答复追加进缓存文件）。
        # 为什么需要（2026-09-17 用户拍板"多跑取均值"）：record 模式下同一 prompt 的第二跑会**命中第一跑的录制**
        # ⇒ 多跑得到的是**同一条回复**、差异被人为抹平，"均值"是假的（方差失真）。
        # 要拿"独立重复"的收益分布，必须让每一跑都重新采样；采样结果仍留在同一个缓存文件里
        # （同一 `prompt_sha1` 会有多条记录），后续既能看到分布，也能用 `replay` 冻结其中一套。
        self.fresh = str(os.getenv("BT_LLM_FRESH", "0")).strip().lower() in ("1", "true", "yes", "on")
        self.n_hit = self.n_record = self.n_miss = 0
        # ── 跨臂共享缓存（2026-09-19 用户拍板）────────────────────────────────────
        # 背景：runner 里 `export BT_LLM_FRESH=1`（"多跑取均值"口径）+ 缓存目录 per-run
        #   （data/_bt_llm_year_<tag>）⇒ **同一 as-of 同一 prompt 在各臂被重复外呼**。
        # 做法：再挂一层共享目录（默认缓存目录的兄弟目录 data/_bt_llm_shared，布局同构），
        #   命中即复用（即便 fresh=1 —— 同一 as-of 同 prompt 的答案复用不会改变"独立重复"的语义，
        #   因为独立重复要的是**不同 as-of 或不同 prompt** 的分布）；新录的答复也会追加进共享目录。
        # 开关 `WOLF_LLM_SHARED_CACHE`（库内默认关、回测开）；`BT_LLM_SHARED` 可指定目录。
        self.shared_on = str(os.getenv("WOLF_LLM_SHARED_CACHE",
                                       "1" if os.getenv("BT_ASOF_FETCH") else "0")).strip().lower() in ("1", "true", "yes", "on")
        self.shared_dir = os.getenv("BT_LLM_SHARED") or os.path.join(
            os.path.dirname(os.path.abspath(self.cache_dir)), "_bt_llm_shared")
        self.n_shared_hit = 0
        self._shared_recs: Optional[List[Dict[str, Any]]] = None
        self._shared_by_sha: Dict[str, List[int]] = {}
        self._shared_used: set = set()
        self._shared_logged: set = set()
        self.warnings: List[str] = []
        self.errors: List[str] = []          # 本层主动抛出的错误（strict 漂移 / replay 缺缓存）
        self.installed: List[Dict[str, Any]] = []
        self._records: Optional[List[Dict[str, Any]]] = None
        self._file_is_multi = False
        self._by_sha: Dict[str, List[int]] = {}
        self._used: set = set()
        self._module_name = "?"
        # 提示词改写钩子（回测 as-of「工具通道」修复）：`fn(url, body) -> body`
        # 只有回测驱动（`--agent on`）会装；不装时本层行为与以前逐字节一致。
        self.prompt_guard = None
        self.guard_applied: List[Dict[str, Any]] = []
        self._guard_last: Optional[Dict[str, Any]] = None

    # ── 缓存路径 ──
    @property
    def cache_file(self) -> str:
        return os.path.join(self.cache_dir, self.agent, "%s.json" % self.as_of)

    @property
    def shared_file(self) -> str:
        return os.path.join(self.shared_dir, self.agent, "%s.json" % self.as_of)

    def _load_shared(self) -> List[Dict[str, Any]]:
        if self._shared_recs is not None:
            return self._shared_recs
        recs: List[Dict[str, Any]] = []
        try:
            with open(self.shared_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("calls"), list):
                recs = [r for r in data["calls"] if isinstance(r, dict)]
        except Exception:
            recs = []
        self._shared_recs = recs
        self._shared_by_sha = {}
        for i, r in enumerate(recs):
            self._shared_by_sha.setdefault(str(r.get("prompt_sha1") or ""), []).append(i)
        return recs

    def _take_shared(self, psha: str) -> Optional[int]:
        self._load_shared()
        for i in self._shared_by_sha.get(psha, []):
            if i not in self._shared_used:
                self._shared_used.add(i)
                return i
        return None

    def _append_shared(self, rec: Dict[str, Any]) -> None:
        """把新录的答复追加进共享目录（best-effort，失败不影响主流程）。"""
        if not self.shared_on:
            return
        try:
            old = []
            try:
                with open(self.shared_file, encoding="utf-8") as f:
                    d = json.load(f)
                old = [r for r in (d.get("calls") or []) if isinstance(r, dict)]
            except Exception:
                old = list(self._shared_recs or [])
            if any(r.get("prompt_sha1") == rec.get("prompt_sha1")
                   and r.get("reply") == rec.get("reply") for r in old):
                return
            old.append(rec)
            os.makedirs(os.path.dirname(self.shared_file), exist_ok=True)
            tmp = self.shared_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"calls": old}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.shared_file)
            self._shared_recs = old
            self._shared_by_sha.setdefault(str(rec.get("prompt_sha1") or ""), []).append(len(old) - 1)
        except Exception as e:
            print("[bt_llm] 共享缓存写失败: %s" % str(e)[:80])

    def load(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save(self, rec: Dict[str, Any]) -> None:
        """兼容旧入口：保存"单条记录"文件（wave agent 的老格式）。多条时请走 `_save_all`。"""
        self._write_file(rec if isinstance(rec, dict) else {"calls": rec})

    def _write_file(self, data: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cache_file)
        except Exception as e:
            print("[bt_llm] 缓存写失败 %s: %s" % (self.cache_file, str(e)[:80]))

    # ── 记录装载（兼容：单条 dict 老格式 / {"calls":[...]} 多调用格式）──
    def _load_records(self) -> List[Dict[str, Any]]:
        if self._records is not None:
            return self._records
        data = self.load()
        recs: List[Dict[str, Any]] = []
        if isinstance(data, dict) and isinstance(data.get("calls"), list):
            recs = [r for r in data["calls"] if isinstance(r, dict)]
            self._file_is_multi = True
        elif isinstance(data, dict) and ("reply" in data or "prompt_sha1" in data
                                         or "response" in data):
            recs = [data]
            self._file_is_multi = False
        self._records = recs
        self._by_sha = {}
        for i, r in enumerate(recs):
            self._by_sha.setdefault(str(r.get("prompt_sha1") or ""), []).append(i)
        return recs

    def _save_all(self) -> None:
        recs = self._records or []
        if len(recs) == 1 and not self._file_is_multi:
            self._write_file(recs[0])                      # 保持波浪 agent 的老文件形状
        else:
            self._write_file({"agent": self.agent, "as_of": self.as_of,
                              "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "n_calls": len(recs), "calls": recs})

    def _take(self, psha: str) -> Optional[int]:
        for i in self._by_sha.get(psha, []):
            if i not in self._used:
                self._used.add(i)
                return i
        return None

    def _first_unused(self) -> Optional[int]:
        for i in range(len(self._records or [])):
            if i not in self._used:
                self._used.add(i)
                return i
        return 0 if (self._records or []) else None

    def _resp_from(self, rec: Dict[str, Any], status: Optional[int] = None) -> _FakeResponse:
        st = int(status or rec.get("http_status") or 200)
        if isinstance(rec.get("response"), dict):
            return _FakeResponse(rec["response"], status=st)
        return _FakeResponse({"reply": rec.get("reply")}, status=st)

    # ── 安装到目标模块 ──
    def install(self, module) -> "LLMReplay":
        """把目标模块的 LLM 出口换成本层：`requests`（有则换）与 `urllib`（有则换）。

        生产做T链的 `t_bridge.wake_agent` 用 `urllib.request.urlopen`；波浪/切换 agent 用 `requests.post`。
        """
        patched = []
        real_req = getattr(module, "requests", None)
        if real_req is not None:
            module.requests = _RequestsProxy(real_req, self.post)
            patched.append("requests")
        real_url = getattr(module, "urllib", None)
        if real_url is not None and hasattr(real_url, "request"):
            module.urllib = _UrllibProxy(real_url, self.urlopen)
            patched.append("urllib")
        self._module_name = getattr(module, "__name__", "?")
        self.installed.append({"module": self._module_name, "patched": patched})
        if not patched:
            print("[bt_llm] ⚠️ %s 里既没有 requests 也没有 urllib —— 本层未拦截任何调用"
                  % self._module_name)
        return self

    # ── 提示词改写钩子（回测 as-of 工具口径；见 bt_agent_tools.BacktestToolGuard）──
    def set_prompt_guard(self, fn) -> "LLMReplay":
        """装一个 `fn(url, body) -> body` 的改写器：在**计算 prompt 指纹之前**改写请求体。

        为什么放在这里：prompt 指纹 = 请求体里的 message；口径声明必须进入指纹，
        否则「同一个 sha1 对应两种口径」→ 回放会把泄漏口径的回复当成 as-of 口径的回复。
        """
        self.prompt_guard = fn
        return self

    def _apply_guard(self, url: str, body: Any):
        if self.prompt_guard is None or not isinstance(body, dict):
            return body
        new_body = self.prompt_guard(url, body)
        if isinstance(new_body, dict) and new_body is not body:
            # 钩子可能是绑定方法（`guard.decorate`）→ 元数据挂在实例上，先解析出宿主对象
            owner = getattr(self.prompt_guard, "__self__", self.prompt_guard)
            meta = {"guard": getattr(owner, "guard_name", "?"),
                    "version": getattr(owner, "guard_version", "?")}
            self._guard_last = meta
            self.guard_applied.append(meta)
            return new_body
        self._guard_last = None
        return body

    # ── 核心：一次 chat 调用（requests 风格）──
    def post(self, url: str, **kw) -> _FakeResponse:
        body = kw.get("json") or {}
        body = self._apply_guard(url, body)
        if isinstance(body, dict) and kw.get("json") is not body:
            kw = dict(kw)
            kw["json"] = body
        prompt = _prompt_of(body)
        psha = _sha1(prompt)
        recs = self._load_records()
        idx = None if self.fresh else self._take(psha)

        if idx is not None:
            self.n_hit += 1
            return self._resp_from(recs[idx])

        # 跑缓存未命中（或 fresh=1）→ 试跨臂共享缓存（2026-09-19）
        if self.shared_on:
            _si = self._take_shared(psha)
            if _si is not None:
                self.n_shared_hit += 1
                if psha not in self._shared_logged:
                    self._shared_logged.add(psha)
                    print("[bt_llm] 跨臂复用 sha1=%s agent=%s as_of=%s ← %s"
                          % (psha[:8], self.agent, self.as_of, self.shared_file), flush=True)
                return self._resp_from(self._load_shared()[_si])

        # —— 未命中 ——
        self._dump_miss(prompt, psha, recs)
        if not recs:
            if self.mode == "replay":
                self.n_miss += 1
                msg = ("replay 缺少缓存：agent=%s as_of=%s（%s）—— 先跑一次 record 再回放"
                       % (self.agent, self.as_of, self.cache_file))
                self.errors.append(msg)
                raise LLMReplayError(msg)
            return self._record(url, kw, prompt, psha)

        if self.mode == "replay":
            self.n_miss += 1
            msg = ("prompt 与录制时不一致：agent=%s as_of=%s（缓存 %d 条，无 sha1=%s）"
                   % (self.agent, self.as_of, len(recs), psha[:8]))
            self.errors.append(msg)
            if self.strict:
                raise LLMReplayError(msg + " —— 输入数据/PIT 口径已漂移，回放不可比")
            self.warnings.append(msg)
            print("[bt_llm] WARN " + msg + "（strict=0 → 退化为未使用的旧记录）")
            fb = self._first_unused()
            if fb is None:
                raise LLMReplayError(msg)
            return self._resp_from(recs[fb])

        # record 模式：这是"新的一次调用"（新触发 / 口径确实变了）→ 外呼并追加
        return self._record(url, kw, prompt, psha)

    def _dump_miss(self, prompt: str, psha: str, recs: List[Dict[str, Any]]) -> None:
        """未命中时把**调用方实际给的 prompt** 落盘（`BT_LLM_DUMP_MISS=1` 才写）。

        为什么需要：`prompt 与录制时不一致` 只说 sha1 不同，不说是哪一段变了；排漂移要能 diff。
        写到 `<cache_dir>/<agent>/<as_of>.miss.txt`（追加，含 sha1 头）。
        """
        if str(os.getenv("BT_LLM_DUMP_MISS", "0")).strip() in ("", "0", "false", "no"):
            return
        try:
            d = os.path.join(self.cache_dir, self.agent)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "%s.miss.txt" % self.as_of), "a", encoding="utf-8") as f:
                f.write("=== %s mode=%s sha1=%s（缓存 %d 条）===\n%s\n"
                        % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.mode, psha, len(recs), prompt))
        except Exception as e:
            print("[bt_llm] miss prompt 落盘失败: %s" % str(e)[:80])

    def _with_session(self, url: str, kw: Dict[str, Any]) -> Dict[str, Any]:
        """★ 给 `/chat` 注入**按天**的 `session_id`（账本 §9.596 ✓，开关默认关 ✓）

        为什么 ✓：桥的会话默认**共用 `default`** ✗、chat 模式 TTL **30 天** ✗，
        再叠加 DSH rc.6 的 resume bug（中断 ⇒ 空回复 ⇒ 桥 fork 复制全部历史 ✗）
        ⇒ 实测会话涨到 **4.5 万事件** ⇒ node 堆爆 ⇒ OOM 重启 66+ 次 ✗
        做法 ✓：每天一个会话（`bt:<agent>:<as_of>` ✓）⇒ 历史**不跨天累积** ✓
        位置 ✓：放在**真正外呼之前** ✓ ⇒ **不影响缓存键**（prompt/sha 已算好 ✓）
        """
        try:
            if str(os.getenv("WOLF_AGENT_SESSION_PER_DAY", "0")).strip().lower() not in ("1", "true", "yes", "on"):
                return kw
            if "/chat" not in str(url):
                return kw
            body = kw.get("json")
            if not isinstance(body, dict):
                return kw
            # ★ 账本 §9.633 ✓（用户问「fork 迁移该怎么办」✓）：**按"窗口"轮换会话** ✗
            #   实测 ✓：按天(`bt:t_leg:<日>` ✓)一天涨到 **seed 28355 事件** ✗
            #     ⇒ 桥每次 **fork 迁移都要整份复制** ✗（30 分钟 18 次 fork ✓）
            #   现在 ✓：同一 **窗口**（默认 600 秒 ✓）内复用同一会话 ⇒ 既"热"又不膨胀 ✓
            #     ⇒ fork 即使发生 ✓，代价也从"复制 2.8 万事件"降到"复制几百事件" ✓
            #   `WOLF_AGENT_SESSION_WINDOW` 可调 ✓
            _win = max(60, int(os.getenv("WOLF_AGENT_SESSION_WINDOW", "600") or 600))
            _slots = max(1, int(os.getenv("WOLF_AGENT_SESSION_SLOTS", "6") or 6))   # ★ §9.691：并发会话槽 ✓
            _rr[0] = (_rr[0] + 1) % _slots
            sid = "bt:%s:%s:w%d:c%d" % (self.agent, self.as_of, int(time.time() // _win), _rr[0])
            if body.get("session_id") == sid:
                return kw
            # ★ 账本 §9.684 ✓（用户：「加上」✓）：发现**新会话**先发一个极小请求把它焐热 ✓
            #   （实测冷 ~95 秒 ✗、热 ~6 秒 ✓ ⇒ 重启后第一批调用的 240 秒成批超时由此而来 ✓）
            #   开关 WOLF_SESSION_PREWARM（库内默认 0 ⇒ 生产零影响 ✓；pins 打开 ✓）
            try:
                _pw_on = str(os.getenv("WOLF_SESSION_PREWARM", "0")).strip().lower() in ("1", "true", "yes", "on")
                if _pw_on and sid not in _PREWARMED:
                    _PREWARMED.add(sid)
                    import json as _j_pw
                    import urllib.request as _u_pw
                    _rq = _u_pw.Request(str(url),
                                       data=_j_pw.dumps({"message": "只回两字: 就绪", "session_id": sid}).encode(),
                                       headers={"Content-Type": "application/json"})
                    _tt = time.time()
                    with _u_pw.urlopen(_rq, timeout=float(os.getenv("WOLF_SESSION_PREWARM_TIMEOUT", "200") or 200)) as _rs:
                        _rs.read()
                    print("[bt_llm] 会话焐热完成 ✓ %s（%.1f 秒）" % (str(sid)[-26:], time.time() - _tt), flush=True)
            except Exception as _e_pw:
                print("[bt_llm] 会话焐热失败（继续 ✓）: %s" % str(_e_pw)[:70], flush=True)
            kw2 = dict(kw)
            nb = dict(body)
            nb["session_id"] = sid
            # ★ 账本 §9.694 ✓（用户：「把回测唤醒的 thinking/effort 降到 low 试一天」✓）
            #   实测 ✓：0107 单次唤醒 4~88 秒（均值 36.8 ✓），prompt 规模相同 ⇒ 抖动来自推理长度 ✗
            #   模型 deepseek-flash 的 effort 默认 high ✗ ⇒ 回测唤醒降到 low ✓（只影响回测 ✓）
            #   开关 ✓：WOLF_BT_THINKING_LEVEL（库内默认空 ⇒ 不注入 ⇒ 生产零影响 ✓）
            _lvl9 = (os.getenv('WOLF_BT_THINKING_LEVEL') or '').strip()
            if _lvl9:
                nb['thinking_level'] = _lvl9
            kw2["json"] = nb
            return kw2
        except Exception as _e_ws:
            print("[bt_llm] session_id 注入失败: %s" % str(_e_ws)[:60], flush=True)
            return kw

    def _record(self, url: str, kw: Dict[str, Any], prompt: str, psha: str) -> Any:
        kw = self._with_session(url, kw)          # ★ 按天会话（账本 §9.596 ✓）
        # ★ 账本 §9.692 ✓（用户：「我要看一次唤醒的耗时分配」✓）：**每次唤醒计时** ✓
        try:
            import time as _t9
            _t0 = _t9.time()
            resp = self.real_post(url, **kw)
            _dt = _t9.time() - _t0
            try:
                _nb = kw.get("json") or {}
                _plen = len(_nb.get("message") or "")
                _rlen = len(str(getattr(resp, "text", "") or ""))
                print("[bt_llm] %s 一次唤醒 ⇒ %6.2f 秒｜prompt %5d 字符｜reply %5d 字符｜会话 %s"
                      % (_t9.strftime("%H:%M:%S"), _dt, _plen, _rlen, str(_nb.get("session_id"))[-16:]), flush=True)
            except Exception as _e_p2:
                # ★ §9.699 ✓：留痕（防回潮规则 ✓）
                print("[bt_llm] 唤醒计时打印失败（不影响主流程 ✓）: %s"
                      % str(_e_p2)[:70], flush=True)
        except Exception:
            raise
        try:
            payload = resp.json()
        except Exception:
            payload = {"reply": getattr(resp, "text", "")}
        if not isinstance(payload, dict):
            payload = {"reply": json.dumps(payload, ensure_ascii=False)}
        rec: Dict[str, Any] = {
            "agent": self.agent, "as_of": self.as_of, "url": url,
            "prompt": prompt, "prompt_sha1": psha,
            "reply": payload.get("reply"),
            "payload_keys": sorted(list(payload.keys()))[:10],
            "http_status": getattr(resp, "status_code", 200),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if self._guard_last:          # 这次录制的 prompt 被哪个 as-of 口径钩子改写过
            rec["prompt_guard"] = self._guard_last
            self._guard_last = None
        if not isinstance(payload.get("reply"), str):
            # 非 /chat 端点（如 /conditions/generate）：整包留档，回放时原样返回
            rec["response"] = payload
        self._records = (self._load_records() + [rec])
        self._by_sha.setdefault(psha, []).append(len(self._records) - 1)
        self._used.add(len(self._records) - 1)
        self.n_record += 1
        self._save_all()
        self._append_shared(rec)
        return resp

    # ── 核心：一次 LLM 调用（urllib.request.urlopen 风格）──
    def urlopen(self, req, timeout: Optional[int] = None, **kw):
        """`urllib.request.urlopen(Request(...))` 的替身：抽 body → 走 post() → 返回 urllib 风格响应。"""
        url = getattr(req, "full_url", None) or str(req)
        data = getattr(req, "data", None)
        body: Any = {}
        if data:
            try:
                text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
                body = json.loads(text)
            except Exception:
                body = {"_raw": str(data)[:2000]}
        call_kw: Dict[str, Any] = {"json": body}
        if timeout is not None:
            call_kw["timeout"] = timeout
        call_kw.update(kw)
        resp = self.post(url, **call_kw)
        try:
            payload = resp.json()
        except Exception:
            payload = {"reply": getattr(resp, "text", "")}
        if not isinstance(payload, dict):
            payload = {"reply": json.dumps(payload, ensure_ascii=False)}
        return _FakeResponse(payload, status=int(getattr(resp, "status_code", 200) or 200))

    # 真实 requests（install 前的那个），record 模式下用它外呼
    real_post = staticmethod(lambda url, **kw: __import__("requests").post(url, **kw))

    def summary(self) -> Dict[str, Any]:
        recs = self._records or []
        unused = max(len(recs) - len(self._used), 0)
        return {"agent": self.agent, "as_of": self.as_of, "mode": self.mode,
                "hit": self.n_hit, "recorded": self.n_record, "miss": self.n_miss,
                "shared_hit": getattr(self, "n_shared_hit", 0),
                "shared_on": bool(getattr(self, "shared_on", False)),
                "shared_file": getattr(self, "shared_file", ""),
                "cache_records": len(recs), "unused": unused,
                "strict": self.strict, "fresh": bool(self.fresh),
                "warnings": self.warnings, "errors": self.errors[:10],
                "installed": self.installed,
                "prompt_guard": (getattr(getattr(self.prompt_guard, "__self__", self.prompt_guard),
                                         "guard_version", None)
                                 if self.prompt_guard is not None else None),
                "guard_applied": len(self.guard_applied),
                "cache_file": self.cache_file}
