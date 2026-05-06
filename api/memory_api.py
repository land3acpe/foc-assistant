"""memory_api.py — 记忆系统 API 层

从 OpenHanako 的 API 结构移植。
当前以内嵌方式直接调用各模块，预留 HTTP API 壳（FastAPI），
未来可拆分为独立服务。
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from memory.fact_store import FactStore
from memory.session_summary import SessionSummaryManager, SummaryData
from memory.compile import assemble, compile_today, compile_week, compile_longterm, compile_facts
from memory.deep_memory import process_dirty_sessions
from experience.experience_store import ExperienceStore
from experience.experience_tools import ExperienceToolExecutor


class MemoryAPI:
    """记忆系统统一 API

    封装记忆系统和经验库的所有操作，提供内嵌调用接口。
    未来可替换为 HTTP 客户端，连接独立的记忆服务。
    """

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        # 初始化各子系统
        self.fact_store = FactStore(os.path.join(data_dir, "facts.db"))
        self.session_summary = SessionSummaryManager(os.path.join(data_dir, "sessions"))
        self.experience_store = ExperienceStore(os.path.join(data_dir, "experience.db"))
        self.experience_tools = ExperienceToolExecutor(self.experience_store)

    # ── 事实操作 ──

    def add_fact(self, fact: str, tags: list[str] = None, session_id: str = "") -> int:
        """添加一条元事实，返回 ID"""
        return self.fact_store.add(fact, tags=tags or [], session_id=session_id or None)

    def search_facts(self, query: str, limit: int = 10) -> list:
        """搜索元事实"""
        return self.fact_store.search_full_text(query, limit=limit)

    # ── 会话摘要 ──

    def save_session_summary(self, session_id: str, summary: str, date: str = None):
        """保存会话摘要"""
        now = datetime.now().isoformat()
        data = SummaryData(
            session_id=session_id,
            summary=summary,
            created_at=now,
            updated_at=now,
        )
        self.session_summary.save_summary(session_id, data)

    def get_session_summary(self, session_id: str) -> Optional[str]:
        """获取会话摘要"""
        data = self.session_summary.get_summary(session_id)
        return data.summary if data else None

    # ── 记忆编译 ──

    def compile_memory(self, llm_func=None, sessions_dir: str = None) -> str:
        """编译完整记忆文本"""
        mem_dir = os.path.join(self.data_dir, "compiled")
        os.makedirs(mem_dir, exist_ok=True)

        today_path = os.path.join(mem_dir, "today.md")
        week_path = os.path.join(mem_dir, "week.md")
        longterm_path = os.path.join(mem_dir, "longterm.md")
        facts_path = os.path.join(mem_dir, "facts.md")
        memory_md_path = os.path.join(mem_dir, "memory.md")

        if sessions_dir:
            compile_today(sessions_dir, self.session_summary, today_path, llm_func=llm_func)
            compile_week(sessions_dir, self.session_summary, week_path, llm_func=llm_func)

        compile_longterm(longterm_path, longterm_path, llm_func=llm_func)
        compile_facts(facts_path, facts_path, llm_func=llm_func)
        assemble(facts_path, today_path, week_path, longterm_path, memory_md_path)

        try:
            with open(memory_md_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    # ── 深度记忆 ──

    async def process_deep_memory(self, sessions_dir: str, llm_call=None, dirty_sessions: list[str] = None):
        """处理深度记忆提取"""
        await process_dirty_sessions(
            sessions_dir=sessions_dir,
            fact_store=self.fact_store,
            llm_call=llm_call,
            dirty_sessions=dirty_sessions,
        )

    # ── 经验库 ──

    def recall_experience(self, category: str = "", query: str = "") -> str:
        """回忆经验"""
        return self.experience_tools.execute("recall_experience", {
            "category": category,
            "query": query,
        })

    def record_experience(self, category: str, content: str, tags: list[str] = None, source: str = "") -> str:
        """记录经验"""
        return self.experience_tools.execute("record_experience", {
            "category": category,
            "content": content,
            "tags": tags or [],
            "source": source,
        })

    def get_experience_prompt(self) -> str:
        """获取经验库 prompt 注入文本"""
        from experience.experience_tools import get_experience_prompt_section
        return get_experience_prompt_section(self.experience_store)

    # ── 生命周期 ──

    def cleanup(self, days: int = 7):
        """清理旧数据"""
        self.experience_store.cleanup_old(days * 13)  # 经验保留更久

    def close(self):
        """关闭所有连接"""
        self.fact_store.close()


# ── 默认实例 ──

_default_api: Optional[MemoryAPI] = None


def get_memory_api(data_dir: str = None) -> MemoryAPI:
    """获取记忆 API 单例"""
    global _default_api
    if _default_api is None:
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "memory")
        _default_api = MemoryAPI(data_dir)
    return _default_api
