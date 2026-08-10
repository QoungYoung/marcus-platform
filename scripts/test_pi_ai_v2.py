"""
深度诊断脚本 v2：模拟真实交易场景测试 Pi Server AI

用法：
  python scripts/test_pi_ai_v2.py
"""

import io
import json
import sys
import time
import traceback
import urllib.request
import ssl

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

PI_URL = "http://localhost:3001/chat"

# 模拟真实交易 prompt（类似 trade_graph.py 构建的 prompt）
REALISTIC_PROMPT = """## 市场结构
当前市场结构: 上升趋势 (Regime=tren)
策略倾向: 进攻型 (仓位上限 80%)
板块风格: 成长股占优

## 当前账户持仓
```json
[
  {"symbol": "159845.SZ", "name": "中证1000ETF", "shares": 10000, "cost": 2.350, "current": 2.420, "pnl_pct": 2.98},
  {"symbol": "588000.SH", "name": "科创50ETF", "shares": 5000, "cost": 1.080, "current": 1.125, "pnl_pct": 4.17}
]
```

## 候选池状态
- 待选标的: 15 只
- 已买入: 2 只 (159845, 588000)
- 剩余资金: ~50000 元

━━━ 系统已预获取的数据（无需重复调用工具）━━━

## 最新扫描报告
```json
{
  "scan_time": "2026-08-04 10:30:00",
  "top_sectors": ["半导体", "AI算力", "创新药"],
  "market_sentiment": "bullish",
  "recommendations": [
    {"code": "512480.SH", "name": "半导体ETF", "score": 92, "reason": "资金持续流入"},
    {"code": "159995.SH", "name": "芯片ETF", "score": 87, "reason": "突破平台"},
    {"code": "516160.SH", "name": "新能源ETF", "score": 75, "reason": "超跌反弹"}
  ]
}
```

请立即执行以下操作：
1. 分析上方已提供的扫描报告和持仓数据
2. 按当前市场结构对应的策略参数选股分析（可调用 check_entry_filters / calc_position / get_quote 等）
3. 执行交易（买入/卖出/调仓）
4. 输出完整交易报告（含 SIGNAL 行）

你是 Marcus 右侧交易专家。基础数据已就绪，请直接分析和决策。
当前时间：2026-08-04 14:30:00"""


def test_realistic():
    session_id = f"diag_realistic_{int(time.time())}"
    payload = json.dumps({
        "message": REALISTIC_PROMPT,
        "session_id": session_id,
        "mode": "trade",
    }).encode("utf-8")

    print(f"[→] 发送真实模拟交易请求...")
    print(f"    Session: {session_id}")
    print(f"    Prompt 长度: {len(REALISTIC_PROMPT)} 字符")

    req = urllib.request.Request(
        PI_URL, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()

    start = time.time()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
            elapsed = time.time() - start
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        print(f"[✗] HTTP {e.code}: {e.reason}")
        body = e.read().decode("utf-8", errors="replace")
        print(f"    Body: {body[:1000]}")
        return None
    except Exception as e:
        elapsed = time.time() - start
        print(f"[✗] 连接失败 ({elapsed:.0f}s): {e}")
        return None

    print(f"[✓] HTTP 200 | 耗时 {elapsed:.0f}s | 服务端耗时: {data.get('elapsed_ms', 'N/A')}ms")

    reply = data.get("reply", "")
    if reply and reply != "(无回复)":
        print(f"\n回复 ({len(reply)} 字符):")
        print("=" * 60)
        print(reply[:4000])
        if len(reply) > 4000:
            print(f"\n... (截断，共 {len(reply)} 字符)")
        print("=" * 60)

        # 检查 SIGNAL 行
        if "SIGNAL:" in reply:
            import re
            m = re.search(r'SIGNAL:\s*(green|yellow|red)\s+POSITION:\s*(\d+)', reply, re.IGNORECASE)
            if m:
                print(f"\n[✓] SIGNAL 解析成功: stance={m.group(1)}, limit={m.group(2)}%")
            else:
                print(f"\n[!] SIGNAL 行格式异常")
        else:
            print(f"\n[✗] 缺少 SIGNAL 行!")
    else:
        print(f"\n[✗✗✗] 空回复！Pi 返回: '{reply}'")
        print(f"完整响应: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")

    return data


if __name__ == "__main__":
    print("=" * 60)
    print("Pi Server AI 深度诊断 v2 — 真实交易场景模拟")
    print("=" * 60)
    print()

    result = test_realistic()

    if result is None:
        print("\n[✗] 诊断失败：无法连接")
        sys.exit(1)
    elif not result.get("reply") or result["reply"] == "(无回复)":
        print("\n[✗] 诊断失败：空回复 — 这复现了生产环境的 bug!")
        print("    建议检查 Pi Server 控制台日志，查看 agent 是否陷入工具调用循环")
        sys.exit(2)
    else:
        print(f"\n[✓] 诊断通过")
