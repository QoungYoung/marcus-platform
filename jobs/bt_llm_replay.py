# -*- coding: utf-8 -*-
"""bt_llm_replay.py — 回测用的 LLM「录制/回放」层（**wave agent / 交易腿 agent 共用**）。

## 为什么需要
`apps/main_line/wave_agent.py`（波浪判定）与 `jobs/rotation_switch_agent.py`（主线内切换·交易腿 agent）
的决策都来自 dsh `/chat`（LLM）→ **非确定**。全拟真回测要可复现，就必须：
  ① **record**（第一次跑）：真实外呼，同时把 `(agent, as_of, prompt, reply, ts)` 落盘；
  ② **replay**（之后所有跑）：只读缓存，命中即用；**未命中直接报错**（绝不静默降级成"这天没有 agent"，
     否则回测会静默少一层决策、结论不可比）。
另外缓存里记 `prompt_sha1`：replay 时若调用方给的 prompt 与录制时不一致（= 输入数据/PIT 口径漂移），
按 `BT_LLM_STRICT=1`（默认）**报错**，置 0 只警告 —— 这是"回测与生产同输入"的自检。

## 用法
```python
from bt_llm_replay import LLMReplay
llm = LLMReplay(agent="wave", as_of="20260910", cache_dir="/app/data/_bt_llm")   # 模式读 env
llm.install(wave_agent)                    # 把该模块里的 requests.post 换成本层
... 正常调用 wave_agent 的 main / call_agent ...
llm.summary()                              # 本日命中/新录制/报错计数
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
from typing import Any, Dict, Optional


class LLMReplayError(RuntimeError):
    pass


class _FakeResponse:
    """最小 HTTP 响应替身：只实现 agent 用到的 `.json()` / `.raise_for_status()` / `.text`。"""

    def __init__(self, payload: Dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise LLMReplayError("replay 缓存里的 HTTP 状态 %s" % self.status_code)


class _RequestsProxy:
    """把目标模块的 `requests` 换成代理：`post` 走本层，其余属性透传真实 requests。"""

    def __init__(self, real, handler):
        self._real = real
        self._handler = handler

    def post(self, url, **kw):
        return self._handler(url, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


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
        self.n_hit = self.n_record = self.n_miss = 0
        self.warnings = []

    # ── 缓存路径 ──
    @property
    def cache_file(self) -> str:
        return os.path.join(self.cache_dir, self.agent, "%s.json" % self.as_of)

    def load(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save(self, rec: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.cache_file)
        except Exception as e:
            print("[bt_llm] 缓存写失败 %s: %s" % (self.cache_file, str(e)[:80]))

    # ── 安装到目标模块 ──
    def install(self, module) -> "LLMReplay":
        real = module.requests
        module.requests = _RequestsProxy(real, self.post)
        self._module_name = getattr(module, "__name__", "?")
        return self

    # ── 核心：一次 chat 调用 ──
    def post(self, url: str, **kw) -> _FakeResponse:
        body = kw.get("json") or {}
        prompt = str(body.get("message") or "")
        psha = _sha1(prompt)
        rec = self.load()

        if rec is None and self.mode == "replay":
            self.n_miss += 1
            raise LLMReplayError(
                "replay 缺少缓存：agent=%s as_of=%s（%s）—— 先跑一次 record 再回放"
                % (self.agent, self.as_of, self.cache_file))
        if rec is not None:
            if rec.get("prompt_sha1") and rec["prompt_sha1"] != psha:
                msg = ("prompt 与录制时不一致：agent=%s as_of=%s（录制 %s / 现在 %s）"
                       % (self.agent, self.as_of, rec["prompt_sha1"][:8], psha[:8]))
                if self.strict:
                    raise LLMReplayError(msg + " —— 输入数据/PIT 口径已漂移，回放不可比")
                self.warnings.append(msg)
                print("[bt_llm] WARN " + msg)
            if rec.get("reply") is not None:
                self.n_hit += 1
                return _FakeResponse({"reply": rec["reply"]}, status=int(rec.get("http_status") or 200))

        # record：真实外呼（这里用真 requests），成功后落盘
        resp = self.real_post(url, **kw)
        try:
            payload = resp.json()
        except Exception:
            payload = {"reply": getattr(resp, "text", "")}
        self.save({"agent": self.agent, "as_of": self.as_of, "url": url,
                   "prompt": prompt, "prompt_sha1": psha,
                   "reply": payload.get("reply"),
                   "payload_keys": sorted(list(payload.keys()))[:10],
                   "http_status": getattr(resp, "status_code", 200),
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        self.n_record += 1
        return resp

    # 真实 requests（install 前的那个），record 模式下用它外呼
    real_post = staticmethod(lambda url, **kw: __import__("requests").post(url, **kw))

    def summary(self) -> Dict[str, Any]:
        return {"agent": self.agent, "as_of": self.as_of, "mode": self.mode,
                "hit": self.n_hit, "recorded": self.n_record, "miss": self.n_miss,
                "warnings": self.warnings, "cache_file": self.cache_file}
