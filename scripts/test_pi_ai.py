"""
诊断脚本：测试 Pi Server 的 AI 交易决策能力

用法：
  在 Pi Server 所在机器上运行：
  python scripts/test_pi_ai.py

  或指定 Pi Server 地址：
  python scripts/test_pi_ai.py --url http://81.70.44.68:3001/chat
"""

import argparse
import io
import json
import sys
import time
import traceback
import urllib.request
import ssl

# Fix Windows GBK encoding issues
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def test_chat(pi_url: str, message: str, session_id: str, mode: str = "trade", timeout: int = 120):
    """测试 Pi Server /chat 端点"""
    payload = json.dumps({
        "message": message,
        "session_id": session_id,
        "mode": mode,
    }).encode("utf-8")

    print(f"[→] 发送请求到 {pi_url}")
    print(f"    Mode: {mode} | Session: {session_id}")
    print(f"    Message 长度: {len(message)} 字符")
    print(f"    Message 前 200 字:\n{message[:200]}\n")

    req = urllib.request.Request(
        pi_url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()

    start = time.time()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            elapsed = time.time() - start
            http_status = resp.status
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        print(f"[✗] HTTP {e.code}: {e.reason}")
        body = e.read().decode("utf-8", errors="replace")
        print(f"    Body: {body[:500]}")
        return None
    except Exception as e:
        elapsed = time.time() - start
        print(f"[✗] 连接失败 ({elapsed:.0f}s): {e}")
        traceback.print_exc()
        return None

    print(f"[✓] HTTP {http_status} | 耗时 {elapsed:.0f}s")
    print(f"    Reply 字段长度: {len(data.get('reply', ''))} 字符")
    print(f"    Session ID: {data.get('session_id', 'N/A')}")
    print(f"    Mode: {data.get('mode', 'N/A')}")
    print(f"    Elapsed (服务端): {data.get('elapsed_ms', 'N/A')}ms")

    reply = data.get("reply", "")
    if reply:
        print(f"\n{'='*60}")
        print("回复内容:")
        print(f"{'='*60}")
        print(reply[:3000])
        if len(reply) > 3000:
            print(f"\n... (截断，共 {len(reply)} 字符)")
        print(f"{'='*60}")
    else:
        print("\n[!!!] 空回复！Pi 未返回任何内容")
        print(f"完整响应: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")

    if data.get("error"):
        print(f"\n[!!!] 服务端错误: {data['error']}")

    return data


SIMPLE_TRADE_PROMPT = """你是 Marcus 右侧交易专家。请简单分析当前市场并输出你的判断。

⚠️ 重要：请在回复末尾包含以下格式的 SIGNAL 行：
SIGNAL: green POSITION: 80 REASON: 市场情绪积极，技术面支持

即使你没有真实数据，也请模拟一个判断输出。这只是一个连通性测试。"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="测试 Pi Server AI")
    parser.add_argument("--url", default="http://localhost:3001/chat", help="Pi Server /chat 端点")
    parser.add_argument("--mode", default="trade", choices=["chat", "trade", "reflect"])
    parser.add_argument("--message", default=None, help="自定义消息（默认使用测试提示词）")
    args = parser.parse_args()

    session_id = f"diag_test_{int(time.time())}"

    print(f"{'='*60}")
    print(f"Pi Server AI 诊断测试")
    print(f"{'='*60}")
    print(f"目标: {args.url}")
    print(f"模式: {args.mode}")
    print()

    message = args.message or SIMPLE_TRADE_PROMPT

    result = test_chat(args.url, message, session_id, args.mode)

    if result is None:
        print("\n[✗] 诊断失败：无法连接到 Pi Server")
        sys.exit(1)
    elif not result.get("reply") or result["reply"] == "(无回复)":
        print("\n[✗] 诊断失败：Pi 返回空回复")
        sys.exit(2)
    else:
        print(f"\n[✓] 诊断通过：Pi 成功返回 {len(result['reply'])} 字符回复")
