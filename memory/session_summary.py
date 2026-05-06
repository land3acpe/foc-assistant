"""session_summary.py — Session 摘要管理器

从 OpenHanako 的 lib/memory/session-summary.js 移植。
管理 session 摘要的读写、脏标记、日期范围查询。
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class SummaryData:
    """Session 摘要数据"""
    session_id: str
    created_at: str = ""
    updated_at: str = ""
    summary: str = ""
    message_count: int = 0
    snapshot: str = ""
    snapshot_at: Optional[str] = None


def normalize_since(value: str) -> Optional[str]:
    """标准化时间字符串"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def latest_since(*values: str) -> Optional[str]:
    """取最新的时间值"""
    latest = None
    for value in values:
        normalized = normalize_since(value)
        if not normalized:
            continue
        if not latest or normalized > latest:
            latest = normalized
    return latest


def is_after(value: str, since: str) -> bool:
    """判断 value 是否在 since 之后"""
    if not value:
        return False
    try:
        return value > since
    except (TypeError, ValueError):
        return False


def are_messages_after(messages: list[dict], since: str) -> bool:
    """判断所有消息是否都在 since 之后"""
    if not since:
        return True
    for msg in messages:
        ts = msg.get("timestamp")
        if ts and not is_after(ts, since):
            return False
    return True


class SessionSummaryManager:
    """Session 摘要管理器"""

    def __init__(self, summaries_dir: str):
        self.summaries_dir = summaries_dir
        os.makedirs(summaries_dir, exist_ok=True)
        self._cache: dict[str, SummaryData] = {}
        self._cache_populated = False

    def _file_path(self, session_id: str) -> str:
        """获取摘要文件路径"""
        clean_id = session_id.replace(".jsonl", "")
        return os.path.join(self.summaries_dir, f"{clean_id}.json")

    def get_summary(self, session_id: str) -> Optional[SummaryData]:
        """读取指定 session 的摘要"""
        if session_id in self._cache:
            return self._cache[session_id]

        fp = self._file_path(session_id)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            summary = SummaryData(
                session_id=data.get("session_id", session_id),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                summary=data.get("summary", ""),
                message_count=data.get("message_count", 0),
                snapshot=data.get("snapshot", ""),
                snapshot_at=data.get("snapshot_at"),
            )
            self._cache[session_id] = summary
            return summary
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def save_summary(self, session_id: str, data: SummaryData):
        """写入摘要（原子写入）"""
        fp = self._file_path(session_id)
        os.makedirs(os.path.dirname(fp), exist_ok=True)

        data_dict = {
            "session_id": data.session_id,
            "created_at": data.created_at,
            "updated_at": data.updated_at,
            "summary": data.summary,
            "message_count": data.message_count,
            "snapshot": data.snapshot,
            "snapshot_at": data.snapshot_at,
        }

        # 原子写入
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(fp), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_path, fp)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        self._cache[session_id] = data

    def get_dirty_sessions(self, since: str = None) -> list[SummaryData]:
        """获取所有"脏" session（summary !== snapshot）"""
        self._ensure_cache_populated()
        normalized_since = normalize_since(since)
        dirty = []
        for data in self._cache.values():
            if not data.summary:
                continue
            if normalized_since and not is_after(data.updated_at or data.created_at, normalized_since):
                continue
            if data.summary != (data.snapshot or ""):
                dirty.append(data)
        return dirty

    def mark_processed(self, session_id: str):
        """标记 session 已被深度记忆处理"""
        data = self.get_summary(session_id)
        if not data:
            return
        data.snapshot = data.summary
        data.snapshot_at = datetime.now().isoformat()
        self.save_summary(session_id, data)

    def get_all_summaries(self) -> list[SummaryData]:
        """获取所有摘要（按 updated_at 降序）"""
        self._ensure_cache_populated()
        summaries = [d for d in self._cache.values() if d.summary]
        summaries.sort(key=lambda s: s.updated_at or "", reverse=True)
        return summaries

    def _ensure_cache_populated(self):
        """首次调用时做一次全量扫描填充缓存"""
        if self._cache_populated:
            return
        for fp in self._list_files():
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sid = data.get("session_id")
                if sid:
                    self._cache[sid] = SummaryData(
                        session_id=sid,
                        created_at=data.get("created_at", ""),
                        updated_at=data.get("updated_at", ""),
                        summary=data.get("summary", ""),
                        message_count=data.get("message_count", 0),
                        snapshot=data.get("snapshot", ""),
                        snapshot_at=data.get("snapshot_at"),
                    )
            except (json.JSONDecodeError, OSError):
                pass
        self._cache_populated = True

    def get_summaries_in_range(self, start_date: datetime, end_date: datetime,
                               since: str = None) -> list[SummaryData]:
        """获取指定日期范围内的摘要"""
        start_iso = start_date.isoformat()
        end_iso = end_date.isoformat()
        normalized_since = normalize_since(since)

        return [
            s for s in self.get_all_summaries()
            if start_iso <= (s.updated_at or s.created_at or "") <= end_iso
            and (not normalized_since or is_after(s.updated_at or s.created_at, normalized_since))
        ]

    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()
        self._cache_populated = False

    def clear_all(self):
        """清空所有摘要"""
        os.makedirs(self.summaries_dir, exist_ok=True)
        for fp in self._list_files():
            try:
                os.unlink(fp)
            except FileNotFoundError:
                pass
        self.clear_cache()

    def _list_files(self) -> list[str]:
        """列出所有摘要文件"""
        try:
            return [
                os.path.join(self.summaries_dir, f)
                for f in os.listdir(self.summaries_dir)
                if f.endswith(".json")
            ]
        except OSError:
            return []

    def build_conversation_text(self, messages: list[dict], is_zh: bool = True) -> str:
        """从消息列表构建带时间戳的对话文本"""
        parts = []
        for msg in messages:
            segments = self._extract_summary_segments(msg, is_zh)
            if not segments:
                continue

            # 时间标注
            time_prefix = ""
            if msg.get("timestamp"):
                try:
                    dt = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                    time_prefix = f"[{dt.strftime('%H:%M')}] "
                except (ValueError, TypeError):
                    pass

            speaker = "用户" if msg.get("role") == "user" else "助手" if is_zh else ("User" if msg.get("role") == "user" else "Assistant")
            for segment in segments:
                parts.append(f"{time_prefix}【{speaker}】{segment}")

        return "\n\n".join(parts)

    def _extract_summary_segments(self, msg: dict, is_zh: bool) -> list[str]:
        """提取消息中的摘要片段"""
        content = msg.get("content")
        if not content:
            return []

        if isinstance(content, str):
            text = content.strip()
            return [text] if text else []

        if not isinstance(content, list):
            return []

        segments = []
        text_buffer = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                text_buffer += block["text"]
                continue

            if msg.get("role") == "assistant" and self._is_tool_call_block(block):
                if text_buffer.strip():
                    segments.append(text_buffer.strip())
                    text_buffer = ""
                title = self._summarize_tool_call(block, is_zh)
                if title:
                    segments.append(title)

        if text_buffer.strip():
            segments.append(text_buffer.strip())
        return segments

    def _is_tool_call_block(self, block) -> bool:
        """判断是否为工具调用块"""
        return isinstance(block, dict) and block.get("type") in ("tool_use", "toolCall", "function_call")

    def _summarize_tool_call(self, block: dict, is_zh: bool) -> str:
        """汇总工具调用"""
        name = (block.get("name") or "").strip()
        if not name:
            return ""
        args = block.get("input") or block.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        def pick(*keys: str) -> str:
            for key in keys:
                val = args.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            return ""

        def shorten(text: str, limit: int = 120) -> str:
            return text[:limit - 1] + "…" if len(text) > limit else text

        tool_map = {
            "read": lambda: f"读取了 {pick('file_path', 'path')}" if is_zh else f"Read {pick('file_path', 'path')}",
            "write": lambda: f"写入了 {pick('file_path', 'path')}" if is_zh else f"Wrote {pick('file_path', 'path')}",
            "edit": lambda: f"修改了 {pick('file_path', 'path')}" if is_zh else f"Edited {pick('file_path', 'path')}",
            "bash": lambda: f"执行了命令 {shorten(pick('command'), 80)}" if is_zh else f"Ran command {shorten(pick('command'), 80)}",
            "web_search": lambda: f"搜索了 {shorten(pick('query'), 80)}" if is_zh else f"Searched {shorten(pick('query'), 80)}",
            "web_fetch": lambda: f"读取了网页 {pick('url')}" if is_zh else f"Fetched {pick('url')}",
        }

        handler = tool_map.get(name)
        if handler:
            return handler()

        detail = shorten(pick("file_path", "path", "query", "url", "command", "pattern", "prompt", "label", "title"), 80)
        return f"调用了 {name}{'：' + detail if detail else ''}" if is_zh else f"Called {name}{': ' + detail if detail else ''}"
