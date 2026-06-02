

def _empty_result() -> dict:
    """返回空结果"""
    return {
        'score': 50.0,
        'positive': 0,
        'negative': 0,
        'neutral': 0,
        'total': 0,
        'hot_concepts': [],
        'catalysts': [],
        'risks': [],
        'analysis': 'API 调用失败',
        'confidence': 0.0
    }


if __name__ == '__main__':
    # 测试
    test_news = [
        {'title': 'AI 芯片需求爆发，英伟达财报超预期', 'category': '半导体', 'content': ''},
        {'title': '新能源车销量增长放缓，特斯拉裁员 10%', 'category': '新能源', 'content': ''},
    ]
    
    print("\n=== DeepSeek Chat AI 新闻分析测试 ===\n")
    result = analyze_news_with_deepseek(test_news)
    
    print(f"\n情绪分数：{result['score']}/100")
    print(f"正面：{result['positive']} | 负面：{result['negative']} | 中性：{result['neutral']}")
