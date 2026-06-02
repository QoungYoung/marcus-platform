# -*- coding: utf-8 -*-
"""
Context compactor for long conversations.
"""
import json
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime


# Simple token estimation (Chinese chars count as 2, English words as 1.5)
def estimate_tokens(text: str) -> int:
    """Estimate token count for text."""
    if not text:
        return 0

    # Rough estimation: Chinese chars ~2 tokens, English words ~1.5 tokens
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other_chars = len(text) - chinese_chars
    english_words = len(text.split()) - chinese_chars

    return int(chinese_chars * 2 + other_chars * 0.5 + english_words * 1.5)


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens for messages."""
    total = 0
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        total += estimate_tokens(f"{role}: {content}")
    return total


class ContextCompactor:
    """Compacts context when it exceeds threshold."""

    def __init__(
        self,
        max_tokens: int = 6000,
        reserve_tokens: int = 2000,
        keep_recent_tokens: int = 1500,
    ):
        self.max_tokens = max_tokens
        self.reserve_tokens = reserve_tokens
        self.keep_recent_tokens = keep_recent_tokens

    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        """Check if messages need compaction."""
        total = estimate_messages_tokens(messages)
        return total > self.max_tokens

    def prepare_compaction(
        self,
        messages: List[Dict[str, Any]],
        previous_summary: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
        """
        Prepare messages for compaction.
        Returns: (messages_to_summarize, summary, tokens_before)
        """
        if not messages:
            return [], previous_summary, 0

        # Calculate total tokens
        total_tokens = estimate_messages_tokens(messages)

        if total_tokens <= self.max_tokens:
            return messages, previous_summary, total_tokens

        # Find cut point - keep recent messages
        kept_messages = []
        dropped_messages = []

        accumulated_tokens = 0
        for msg in reversed(messages):
            msg_tokens = estimate_tokens(f"{msg.get('role', '')}: {msg.get('content', '')}")
            if accumulated_tokens + msg_tokens > self.keep_recent_tokens:
                dropped_messages.insert(0, msg)
            else:
                kept_messages.insert(0, msg)
                accumulated_tokens += msg_tokens

        return kept_messages, previous_summary, total_tokens

    def generate_summary_prompt(
        self,
        messages: List[Dict[str, Any]],
        custom_instructions: Optional[str] = None,
    ) -> str:
        """Generate a prompt for summarizing messages."""
        # Format messages for LLM
        formatted = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            formatted.append(f"{role}: {content}")

        messages_text = "\n\n".join(formatted)

        prompt = f"""请总结以下对话的要点，保留关键信息供后续参考。

对话内容：
{messages_text}

"""
        if custom_instructions:
            prompt += f"额外要求：{custom_instructions}\n"

        prompt += """
请用简洁的中文总结：
1. 对话的主要话题和结论
2. 重要的数据和决策
3. 用户的关键偏好或要求

摘要格式：
- 主要话题：[...]
- 关键结论：[...]
- 重要数据：[...]
- 用户偏好：[...]
"""
        return prompt

    def compact(
        self,
        messages: List[Dict[str, Any]],
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
    ) -> List[Dict[str, Any]]:
        """
        Apply compaction - returns the compacted message list.
        In practice, this creates a compaction entry rather than modifying messages.
        """
        # After compaction, the context should include:
        # 1. The summary as a system message
        # 2. Recent messages after the cut point

        compacted = [
            {
                "role": "system",
                "content": f"[Earlier conversation summarized: {summary}]",
                "timestamp": datetime.now().isoformat(),
            }
        ]

        # Add messages after first_kept_entry_id (not implemented here as we need entry IDs)

        return compacted


class BranchSummarizer:
    """Generates summaries for session branches."""

    def __init__(self):
        self.compactor = ContextCompactor()

    async def generate_branch_summary(
        self,
        entries: List[Dict[str, Any]],
        custom_instructions: Optional[str] = None,
    ) -> str:
        """Generate a summary for a branch of entries."""
        # Extract messages from entries
        messages = []
        for entry in entries:
            if entry.get("type") == "message":
                messages.append({
                    "role": entry.get("role"),
                    "content": entry.get("content"),
                })

        if not messages:
            return ""

        # Use the compactor's summary generation
        prompt = self.compactor.generate_summary_prompt(messages, custom_instructions)

        # This would be called by the LLM to generate the summary
        # In practice, it returns the prompt and the caller uses an LLM to summarize

        return prompt