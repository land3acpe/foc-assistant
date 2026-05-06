"""deferred_store.py — 异步结果持久化存储

从 OpenHanako 的 subagent-tool.js 移植。
使用 SQLite 存储 subagent 的异步执行结果。
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class SubagentTask:
    """Subagent 任务"""
    task_id: str
    agent_id: str
    prompt: str
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class DeferredResult:
    """异步结果"""
    task_id: str
    agent_id: str
    result: str
    status: TaskStatus
    completed_at: str


class DeferredResultStore:
    """异步结果持久化存储"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deferred_results (
                task_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                prompt TEXT,
                result TEXT,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                metadata TEXT
            )
        """)
        conn.commit()
        conn.close()

    def save(self, task: SubagentTask):
        """保存任务状态"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO deferred_results
            (task_id, agent_id, prompt, result, status, error, created_at, completed_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.task_id, task.agent_id, task.prompt, task.result,
            task.status.value, task.error, task.created_at,
            task.completed_at, json.dumps(task.metadata),
        ))
        conn.commit()
        conn.close()

    def get_by_id(self, task_id: str) -> Optional[DeferredResult]:
        """按 task_id 获取结果"""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT task_id, agent_id, result, status, completed_at FROM deferred_results WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        conn.close()
        if row:
            return DeferredResult(
                task_id=row[0], agent_id=row[1], result=row[2] or "",
                status=TaskStatus(row[3]), completed_at=row[4] or "",
            )
        return None

    def get_pending(self, agent_id: str = None) -> list[DeferredResult]:
        """获取待处理的结果"""
        conn = sqlite3.connect(self.db_path)
        if agent_id:
            rows = conn.execute(
                """SELECT task_id, agent_id, result, status, completed_at
                   FROM deferred_results
                   WHERE agent_id = ? AND status IN ('completed', 'failed', 'timeout')
                   ORDER BY completed_at DESC""",
                (agent_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT task_id, agent_id, result, status, completed_at
                   FROM deferred_results
                   WHERE status IN ('completed', 'failed', 'timeout')
                   ORDER BY completed_at DESC"""
            ).fetchall()
        conn.close()
        return [
            DeferredResult(
                task_id=row[0], agent_id=row[1], result=row[2] or "",
                status=TaskStatus(row[3]), completed_at=row[4] or "",
            )
            for row in rows
        ]

    def cleanup_old(self, days: int = 7):
        """清理旧记录"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"DELETE FROM deferred_results WHERE created_at < datetime('now', '-{days} days')"
        )
        conn.commit()
        conn.close()
