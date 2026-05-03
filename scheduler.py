"""FOC-Assistant 自主触发模块 —— 后台定时任务和文件监控

预设任务：
1. 知识库健康检查（每小时）：检测新文件未索引
2. 项目文件变更监控（每5分钟）：记录 .c/.h 文件变化
3. 日志轮转（每天）：清理过大的日志文件
4. 记忆整理（每30分钟）：检查记忆目录状态
"""

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from config import (
    KB_AUTO_REBUILD,
    LOG_MAX_SIZE_MB,
    MEMORY_DIR,
    PROJECT_ROOT,
    PROJECT_WATCH_ENABLED,
    SCHEDULER_ENABLED,
)


@dataclass
class ScheduledTask:
    name: str
    func: Callable[[], str]
    interval_seconds: int
    last_run: Optional[datetime] = None
    last_result: str = ""
    run_count: int = 0
    error_count: int = 0
    enabled: bool = True


@dataclass
class FileWatchState:
    path: Path
    pattern: str
    file_hashes: dict = field(default_factory=dict)  # path -> md5


class AgentScheduler:
    """后台调度器：定时执行维护任务"""

    def __init__(self):
        self.tasks: list[ScheduledTask] = []
        self.file_watches: list[FileWatchState] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._start_time: Optional[datetime] = None
        self._lock = threading.Lock()
        self._project_mtime_cache: dict = {}

        # 注册预设任务
        self._register_preset_tasks()

    def _register_preset_tasks(self):
        """注册预设的定时任务"""
        if KB_AUTO_REBUILD:
            self.add_task(
                "知识库健康检查",
                self._task_kb_health_check,
                interval_seconds=3600,  # 每小时
            )

        if PROJECT_WATCH_ENABLED:
            self.add_task(
                "项目文件变更监控",
                self._task_project_watch,
                interval_seconds=300,  # 每5分钟
            )

        self.add_task(
            "日志轮转",
            self._task_log_rotation,
            interval_seconds=86400,  # 每天
        )

        self.add_task(
            "记忆目录检查",
            self._task_memory_check,
            interval_seconds=1800,  # 每30分钟
        )

    def add_task(self, name: str, func: Callable[[], str], interval_seconds: int):
        """添加定时任务"""
        with self._lock:
            self.tasks.append(ScheduledTask(
                name=name,
                func=func,
                interval_seconds=interval_seconds,
            ))

    def add_file_watch(self, path: Path, pattern: str = "*.c"):
        """添加文件变更监控"""
        with self._lock:
            self.file_watches.append(FileWatchState(path=path, pattern=pattern))

    def start(self):
        """启动后台调度器"""
        if self._started or not SCHEDULER_ENABLED:
            return
        self._started = True
        self._start_time = datetime.now()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="AgentScheduler")
        self._thread.start()
        print(f"  [SCHEDULER] 已启动，{len(self.tasks)} 个定时任务")

    def stop(self):
        """停止调度器"""
        if not self._started:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._started = False
        print("  [SCHEDULER] 已停止")

    def run_task_now(self, name: str) -> str:
        """手动触发指定任务"""
        with self._lock:
            for task in self.tasks:
                if task.name == name:
                    try:
                        result = task.func()
                        task.last_run = datetime.now()
                        task.last_result = result
                        task.run_count += 1
                        return result
                    except Exception as e:
                        task.error_count += 1
                        task.last_result = f"执行失败: {e}"
                        return f"任务 '{name}' 执行失败: {e}"
            return f"未找到任务: {name}"

    def get_status(self) -> str:
        """获取调度器状态"""
        now = datetime.now()
        uptime = ""
        if self._start_time:
            delta = now - self._start_time
            hours = delta.total_seconds() / 3600
            uptime = f"{hours:.1f} 小时"

        lines = [
            f"调度器状态: {'运行中' if self._started else '已停止'}",
            f"运行时间: {uptime or '未启动'}",
            f"定时任务: {len(self.tasks)} 个",
            f"文件监控: {len(self.file_watches)} 个",
            "",
            "任务列表:",
        ]

        with self._lock:
            for task in self.tasks:
                status = "启用" if task.enabled else "禁用"
                last = task.last_run.strftime("%H:%M:%S") if task.last_run else "从未"
                next_run = ""
                if task.last_run:
                    remaining = task.interval_seconds - (now - task.last_run).total_seconds()
                    if remaining > 0:
                        next_run = f"，下次: {int(remaining/60)}分钟后"
                    else:
                        next_run = "，即将执行"
                result_preview = task.last_result[:60] if task.last_result else ""
                lines.append(
                    f"  [{status}] {task.name}\n"
                    f"    间隔: {task.interval_seconds}s | 上次: {last}{next_run}\n"
                    f"    运行: {task.run_count}次 | 错误: {task.error_count}次"
                    + (f"\n    最近结果: {result_preview}" if result_preview else "")
                )

        return "\n".join(lines)

    # ================================================================
    # 后台循环
    # ================================================================

    def _run_loop(self):
        """调度器主循环"""
        while not self._stop_event.is_set():
            now = datetime.now()

            with self._lock:
                tasks_to_run = []
                for task in self.tasks:
                    if not task.enabled:
                        continue
                    if task.last_run is None:
                        tasks_to_run.append(task)
                    elif (now - task.last_run).total_seconds() >= task.interval_seconds:
                        tasks_to_run.append(task)

            for task in tasks_to_run:
                if self._stop_event.is_set():
                    break
                try:
                    result = task.func()
                    with self._lock:
                        task.last_run = datetime.now()
                        task.last_result = result
                        task.run_count += 1
                    print(f"  [SCHEDULED] {task.name}: {result[:200]}")
                except Exception as e:
                    with self._lock:
                        task.error_count += 1
                        task.last_result = f"错误: {e}"
                    print(f"  [SCHEDULED] {task.name} 异常: {e}")

            # 每30秒检查一次
            self._stop_event.wait(30)

    # ================================================================
    # 预设任务实现
    # ================================================================

    def _task_kb_health_check(self) -> str:
        """知识库健康检查：检测新文件是否已索引"""
        try:
            from knowledge import get_kb

            kb = get_kb()
            if not kb.loaded:
                kb._load_index()

            # 检查知识库目录是否有未索引的文件
            kb_dir = Path(__file__).parent / "knowledge_base"
            if not kb_dir.exists():
                return "知识库目录不存在"

            indexed_paths = {doc.get("path", "") for doc in kb.documents}
            new_files = []

            for subdir in ("papers", "data", "notes", "codes"):
                dir_path = kb_dir / subdir
                if not dir_path.exists():
                    continue
                for f in dir_path.rglob("*"):
                    if f.is_file() and str(f) not in indexed_paths:
                        new_files.append(f.name)

            if new_files:
                kb.build_index()
                return f"检测到 {len(new_files)} 个新文件，已重建索引: {', '.join(new_files[:5])}"
            return "知识库状态正常，无新文件"
        except Exception as e:
            return f"知识库检查失败: {e}"

    def _task_project_watch(self) -> str:
        """项目文件变更监控"""
        if not PROJECT_ROOT.exists():
            return f"项目目录不存在: {PROJECT_ROOT}"

        changed = []
        for ext in ("*.c", "*.h"):
            for f in PROJECT_ROOT.rglob(ext):
                if any(skip in f.parts for skip in (".git", "__pycache__", "Debug", "Release")):
                    continue
                try:
                    mtime = f.stat().st_mtime
                    key = str(f)
                    old_mtime = self._project_mtime_cache.get(key, 0)
                    if mtime > old_mtime and old_mtime > 0:
                        changed.append(f.name)
                    self._project_mtime_cache[key] = mtime
                except Exception:
                    continue

        if changed:
            return f"检测到 {len(changed)} 个文件变更: {', '.join(changed[:5])}"
        return "项目文件无变更"

    _project_mtime_cache: dict = field(default_factory=dict)

    def _task_log_rotation(self) -> str:
        """日志轮转：清理过大的日志文件"""
        rotated = []
        log_files = [
            Path(__file__).parent / "botpy.log",
            Path(__file__).parent / "qq_bot_runtime.err.log",
            Path(__file__).parent / "agent_runs.log",
        ]
        max_bytes = LOG_MAX_SIZE_MB * 1024 * 1024

        for log_path in log_files:
            if not log_path.exists():
                continue
            try:
                size = log_path.stat().st_size
                if size > max_bytes:
                    # 保留最后 10% 的内容
                    keep = int(size * 0.1)
                    content = log_path.read_bytes()
                    log_path.write_bytes(content[-keep:])
                    rotated.append(f"{log_path.name} ({size//1024//1024}MB → {keep//1024//1024}MB)")
            except Exception:
                continue

        if rotated:
            return f"日志轮转完成: {', '.join(rotated)}"
        return "日志文件大小正常，无需轮转"

    def _task_memory_check(self) -> str:
        """记忆目录检查"""
        if not MEMORY_DIR.exists():
            return "记忆目录不存在"

        files = list(MEMORY_DIR.glob("*.md"))
        total_size = sum(f.stat().st_size for f in files if f.exists())

        if len(files) > 500:
            return f"记忆条目较多 ({len(files)} 条, {total_size//1024}KB)，建议清理旧条目"
        return f"记忆目录正常: {len(files)} 条, {total_size//1024}KB"

    # 需要作为实例属性
# 全局单例
_scheduler_instance: Optional[AgentScheduler] = None


def get_scheduler() -> AgentScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = AgentScheduler()
    return _scheduler_instance
