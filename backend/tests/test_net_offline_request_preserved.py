# -*- coding: utf-8 -*-
"""回放断网闸不能改语义：`urlopen(Request)` 必须仍是 **POST**（2026-09-20 实测 bug）。

现象：pinned 回放子进程里 `theme_member_llm.is_member` 稳定收到 `HTTP 405 Method Not Allowed`，
而裸 socket / http.client / 新建 opener 全是 200 —— 因为 `install_net_offline` 的包装把 Request
对象降级成 URL 字符串（`getattr(url,'full_url',url)`）再转交真 urlopen ⇒ urllib 发 **GET**，
data/method 全丢 ⇒ 对只收 POST 的 `/chat` 就是 405。
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in (REPO_ROOT / "jobs", REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


@pytest.fixture()
def patched(monkeypatch):
    import bt_local_pro
    real = urllib.request.urlopen
    seen = []

    def _fake(url, *a, **kw):
        seen.append((type(url).__name__, getattr(url, "full_url", url), getattr(url, "data", None)))
        return "FAKE"

    monkeypatch.setattr(urllib.request, "urlopen", _fake, raising=False)
    bt_local_pro.install_net_offline()
    yield seen
    urllib.request.urlopen = real


def test_回环请求保留Request对象与POST体(patched):
    body = json.dumps({"message": "hi"}).encode()
    req = urllib.request.Request("http://127.0.0.1:13101/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(req, timeout=5) == "FAKE"
    kind, url, data = patched[-1]
    assert kind == "Request", "必须原样转交 Request（降级成 str 会让 urllib 发 GET）"
    assert url == "http://127.0.0.1:13101/chat"
    assert data == body, "POST 体不能丢"


def test_直传字符串URL也仍可用(patched):
    assert urllib.request.urlopen("http://localhost:1/x") == "FAKE"
    assert patched[-1][1] == "http://localhost:1/x"


def test_非回环仍然被切断(patched):
    with pytest.raises(Exception) as ei:
        urllib.request.urlopen("http://datahubco.com/app-api/x")
    assert "BT_NET_OFFLINE" in str(ei.value)
    assert not patched, "非回环不该到达真 urlopen"


def test_带端口的回环也算本地(patched):
    assert urllib.request.urlopen("http://127.0.0.1:13244/api/v1/health") == "FAKE"
