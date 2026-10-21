# -*- coding: utf-8 -*-
"""狼大风格选股决策卡（P0 skeleton）。

设计原则：
  · `blind` 默认：运行时只读行情/主线/波浪，不读当日帖子；
  · 他的帖子只做标签（`data/wolf_labels/*.jsonl`），用于 oracle/评测；
  · 行为补丁默认关，先影子，再 A/B。
"""

__version__ = "0.1.0"
