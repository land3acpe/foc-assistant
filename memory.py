"""ChatMemory: 短期对话记忆
- 滑动窗口 deque(maxlen)
- 触发式 LLM 摘要（替代式更新，避免无限累积）
- JSON 持久化，按 session_key 隔离
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_DIR = Path.home() / ".foc-assistant" / "memory"
SCHEMA_VERSION = 1


class ChatMemory:
    """短期对话记忆：滑动窗口 + 触发式 LLM 摘要 + JSON 持久化"""

    def __init__(
        self,
        session_key: str,
        maxlen: int = 20,
        summary_keep: int = 10,
        storage_dir: Optional[Path] = None,
        llm_client: Optional[Callable[[list[dict]], str]] = None,
    ):
        raise NotImplementedError("骨架，由后续任务实现")


def get_chat_memory(session_key: str = "default") -> ChatMemory:
    raise NotImplementedError("骨架，由后续任务实现")
