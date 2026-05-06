"""memory_ticker.py — 记忆调度器（v3）

从 OpenHanako 的 lib/memory/memory-ticker.js 移植。
触发机制改为 turn-based：
- 每 6 轮：滚动摘要 + compileToday + assemble
- session 结束：final 滚动摘要 + compileToday + assemble
- 每天一次（日期变化时触发）：compileWeek + compileLongterm + compileFacts + assemble + deep-memory
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from memory.session_summary import SessionSummaryManager
from memory.fact_store import FactStore
from memory.compile import (
    compile_today, compile_week, compile_longterm, compile_facts,
    assemble, get_logical_day, safe_read_file,
)
from memory.deep_memory import process_dirty_sessions

TURNS_PER_SUMMARY = 6
DAILY_CHECK_INTERVAL = 3600  # 1 小时


def read_session_messages(file_path: str, since: str = None) -> tuple[list[dict], Optional[str]]:
    """从 session JSONL 文件提取消息列表（带时间戳）"""
    messages = []
    last_timestamp = None

    try:
        file_size = os.path.getsize(file_path)
        tail_threshold = 256 * 1024  # 256KB

        with open(file_path, "r", encoding="utf-8") as f:
            if file_size > tail_threshold:
                f.seek(file_size - tail_threshold)
                f.readline()  # 跳过首个不完整行
            raw = f.read()
    except (FileNotFoundError, OSError):
        return [], None

    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "message" and entry.get("message"):
                msg = entry["message"]
                role = msg.get("role")
                if role in ("user", "assistant"):
                    ts = entry.get("timestamp")
                    if since and ts and ts <= since:
                        continue
                    messages.append({
                        "role": role,
                        "content": msg.get("content"),
                        "timestamp": ts,
                    })
                    if ts:
                        last_timestamp = ts
        except (json.JSONDecodeError, KeyError):
            pass

    return messages, last_timestamp


def list_all_sessions(session_dir: str) -> list[dict]:
    """列出所有 session JSONL 文件"""
    results = []
    for f in os.listdir(session_dir):
        if not f.endswith(".jsonl"):
            continue
        fp = os.path.join(session_dir, f)
        try:
            stat = os.stat(fp)
            results.append({"filename": f, "file_path": fp, "mtime": stat.st_mtime})
        except OSError:
            pass
    return results


def session_id_from_filename(filename: str) -> str:
    """从文件名提取 session ID"""
    return filename.replace(".jsonl", "")


class MemoryTicker:
    """记忆调度器"""

    def __init__(
        self,
        summary_manager: SessionSummaryManager,
        fact_store: FactStore,
        compact_llm: Callable,
        session_dir: str,
        memory_dir: str,
        is_zh: bool = True,
    ):
        self.summary_manager = summary_manager
        self.fact_store = fact_store
        self.compact_llm = compact_llm
        self.session_dir = session_dir
        self.memory_dir = memory_dir
        self.is_zh = is_zh

        # 路径
        self.today_md_path = os.path.join(memory_dir, "today.md")
        self.week_md_path = os.path.join(memory_dir, "week.md")
        self.longterm_md_path = os.path.join(memory_dir, "longterm.md")
        self.facts_md_path = os.path.join(memory_dir, "facts.md")
        self.memory_md_path = os.path.join(memory_dir, "memory.md")

        # 状态
        self._turn_counts: dict[str, int] = {}
        self._summary_in_progress: set[str] = set()
        self._last_daily_job_date: Optional[str] = None
        self._daily_steps_completed: set[str] = set()
        self._daily_steps_date: Optional[str] = None
        self._daily_running = False
        self._timer: Optional[threading.Timer] = None
        self._running = False

    def notify_turn(self, session_path: str):
        """每轮对话结束后调用"""
        count = self._turn_counts.get(session_path, 0) + 1
        self._turn_counts[session_path] = count

        if count % TURNS_PER_SUMMARY == 0:
            asyncio.create_task(self._do_rolling_and_compile(session_path))

        self._check_daily_job()

    async def _do_rolling_and_compile(self, session_path: str):
        """滚动摘要 + 编译今天 + 组装"""
        await self._do_rolling_summary(session_path)
        await self._do_compile_today_and_assemble()

    async def _do_rolling_summary(self, session_path: str):
        """执行滚动摘要"""
        if session_path in self._summary_in_progress:
            return
        self._summary_in_progress.add(session_path)
        try:
            messages, _ = read_session_messages(session_path)
            if not messages:
                return
            session_id = session_id_from_filename(os.path.basename(session_path))
            await self.summary_manager.rolling_summary(
                session_id, messages, self.compact_llm, is_zh=self.is_zh,
            )
        except Exception as e:
            print(f"[memory-ticker] 滚动摘要失败: {e}")
        finally:
            self._summary_in_progress.discard(session_path)

    async def _do_compile_today_and_assemble(self):
        """编译今天 + 组装"""
        try:
            await compile_today(
                self.summary_manager, self.today_md_path,
                self.compact_llm, is_zh=self.is_zh,
            )
            assemble(
                self.facts_md_path, self.today_md_path,
                self.week_md_path, self.longterm_md_path,
                self.memory_md_path, is_zh=self.is_zh,
            )
        except Exception as e:
            print(f"[memory-ticker] compileToday 失败: {e}")

    async def _do_daily(self):
        """每日任务"""
        if self._daily_running:
            return
        self._daily_running = True
        try:
            range_start, _ = get_logical_day()
            today_str = range_start.strftime("%Y-%m-%d")

            if self._daily_steps_date != today_str:
                self._daily_steps_completed.clear()
                self._daily_steps_date = today_str

            print(f"[memory-ticker] 每日任务开始 ({today_str})")

            # compileWeek
            if "compileWeek" not in self._daily_steps_completed:
                try:
                    await compile_week(
                        self.summary_manager, self.week_md_path,
                        self.compact_llm, is_zh=self.is_zh,
                    )
                    self._daily_steps_completed.add("compileWeek")
                except Exception as e:
                    print(f"[memory-ticker] compileWeek 失败: {e}")

            # compileLongterm
            if "compileLongterm" not in self._daily_steps_completed and "compileWeek" in self._daily_steps_completed:
                try:
                    await compile_longterm(
                        self.week_md_path, self.longterm_md_path,
                        self.compact_llm, is_zh=self.is_zh,
                    )
                    self._daily_steps_completed.add("compileLongterm")
                except Exception as e:
                    print(f"[memory-ticker] compileLongterm 失败: {e}")

            # compileFacts
            if "compileFacts" not in self._daily_steps_completed:
                try:
                    await compile_facts(
                        self.summary_manager, self.facts_md_path,
                        self.compact_llm, is_zh=self.is_zh,
                    )
                    self._daily_steps_completed.add("compileFacts")
                except Exception as e:
                    print(f"[memory-ticker] compileFacts 失败: {e}")

            # assemble
            try:
                assemble(
                    self.facts_md_path, self.today_md_path,
                    self.week_md_path, self.longterm_md_path,
                    self.memory_md_path, is_zh=self.is_zh,
                )
            except Exception as e:
                print(f"[memory-ticker] assemble 失败: {e}")

            # deep-memory
            if "deepMemory" not in self._daily_steps_completed:
                try:
                    result = await process_dirty_sessions(
                        self.summary_manager, self.fact_store,
                        self.compact_llm, is_zh=self.is_zh,
                    )
                    self._daily_steps_completed.add("deepMemory")
                    if result["processed"] > 0:
                        print(f"[memory-ticker] deep-memory: {result['processed']} session, {result['facts_added']} 条新事实")
                except Exception as e:
                    print(f"[memory-ticker] deep-memory 失败: {e}")

            self._last_daily_job_date = today_str
            print("[memory-ticker] 每日任务完成")
        finally:
            self._daily_running = False

    def _check_daily_job(self):
        """检查是否需要执行每日任务"""
        range_start, _ = get_logical_day()
        today_str = range_start.strftime("%Y-%m-%d")
        if self._last_daily_job_date != today_str:
            asyncio.create_task(self._do_daily())

    def start(self):
        """启动定时器"""
        if self._running:
            return
        self._running = True

        def _timer_loop():
            while self._running:
                time.sleep(DAILY_CHECK_INTERVAL)
                if self._running:
                    self._check_daily_job()

        self._timer = threading.Thread(target=_timer_loop, daemon=True)
        self._timer.start()
        print("[memory-ticker] v3 已启动（turn-based，每日任务备用 timer 1h）")

    def stop(self):
        """停止定时器"""
        self._running = False

    async def tick(self):
        """手动触发一次完整编译"""
        await self._do_daily()
        await self._do_compile_today_and_assemble()

    async def notify_session_end(self, session_path: str):
        """Session 结束时调用"""
        count = self._turn_counts.pop(session_path, 0)
        if count == 0:
            return
        await self._do_rolling_summary(session_path)
        await self._do_compile_today_and_assemble()

    def get_memory_md(self) -> str:
        """读取当前 memory.md"""
        return safe_read_file(self.memory_md_path, "（暂无记忆）" if self.is_zh else "(No memory yet)")
