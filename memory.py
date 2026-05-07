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
        if summary_keep >= maxlen:
            raise ValueError("summary_keep must be < maxlen")
        self.session_key = session_key
        self.maxlen = maxlen
        self.summary_keep = summary_keep
        self.storage_dir = storage_dir or DEFAULT_STORAGE_DIR
        self.llm_client = llm_client
        self._summary: str = ""
        self._turns: deque = deque()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def add_turn(self, role: str, content: str) -> None:
        self._turns.append({
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        if len(self._turns) > self.maxlen:
            to_compress = []
            while len(self._turns) > self.summary_keep:
                to_compress.append(self._turns.popleft())
            try:
                self._summary = self._summarize(self._summary, to_compress)
            except Exception as e:
                logger.warning(f"summary failed, drop oldest as FIFO: {e}")
        self._save()

    def get_context(self) -> list[dict]:
        msgs: list[dict] = []
        if self._summary:
            msgs.append({"role": "system", "content": self._summary})
        for t in self._turns:
            msgs.append({"role": t["role"], "content": t["content"]})
        return msgs

    def stats(self) -> dict:
        return {
            "turns": len(self._turns),
            "has_summary": bool(self._summary),
            "summary_chars": len(self._summary),
        }

    def _summarize(self, old_summary: str, new_turns: list[dict]) -> str:
        if not self.llm_client:
            raise RuntimeError("llm_client is None")
        formatted = "\n".join(
            f"[{t['role']}] {t['content']}" for t in new_turns
        )
        old_block = old_summary or "（无）"
        prompt = (
            "你是对话摘要助手。请把以下信息压缩为不超过 300 字的中文摘要，保留：\n"
            "- 用户的核心诉求和已确定的参数\n"
            "- 已尝试的方案和结果\n"
            "- 待解决的问题\n\n"
            f"【已有摘要（如有）】\n{old_block}\n\n"
            f"【新增对话】\n{formatted}\n\n"
            "输出新的统一摘要（替代已有摘要，不要追加）："
        )
        result = self.llm_client([{"role": "user", "content": prompt}])
        return result.strip() if isinstance(result, str) else str(result)

    def clear(self) -> None:
        self._turns.clear()
        self._summary = ""
        path = self._summary_path()
        if path.exists():
            path.unlink()

    def _summary_path(self) -> Path:
        safe = re.sub(r"[^\w\-]", "_", self.session_key)
        return self.storage_dir / f"{safe}.json"

    def _load(self) -> None:
        path = self._summary_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != SCHEMA_VERSION:
                logger.warning(f"incompatible memory schema, ignoring {path}")
                return
            self._summary = data.get("summary", "")
            for t in data.get("turns", []):
                self._turns.append(t)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"failed to load {path}: {e}")

    def _save(self) -> None:
        path = self._summary_path()
        data = {
            "session_key": self.session_key,
            "version": SCHEMA_VERSION,
            "summary": self._summary,
            "turns": list(self._turns),
        }
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error(f"failed to save {path}: {e}")


def get_chat_memory(session_key: str = "default") -> ChatMemory:
    raise NotImplementedError("骨架，由后续任务实现")
