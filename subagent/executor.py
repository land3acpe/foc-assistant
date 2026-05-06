"""executor.py — 非阻塞 Subagent 执行器

从 OpenHanako 的 lib/tools/subagent-tool.js 移植。
将独立子任务派给隔离的 agent session 执行，支持异步结果回收。
"""

import asyncio
import uuid
from datetime import datetime
from typing import Callable, Awaitable, Optional

from subagent.deferred_store import DeferredResultStore, SubagentTask, TaskStatus, DeferredResult

# 工具白名单：只给调研 + 工程所需的最小集
CUSTOM_TOOLS = ["web_search", "web_fetch", "todo_write"]
BUILTIN_TOOLS = ["read", "write", "edit", "bash", "grep", "find", "ls"]
ALL_TOOLS = CUSTOM_TOOLS + BUILTIN_TOOLS

TIMEOUT_S = 15 * 60  # 15 分钟
MAX_CONCURRENT = 3


class SubagentExecutor:
    """非阻塞 subagent 执行器"""

    def __init__(
        self,
        deferred_store: DeferredResultStore,
        llm_call: Callable[[str, str, list[str]], Awaitable[str]],
    ):
        """
        Args:
            deferred_store: 异步结果存储
            llm_call: LLM 调用函数 (system_prompt, user_prompt, tools) -> response
        """
        self.deferred_store = deferred_store
        self.llm_call = llm_call
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def dispatch(
        self,
        agent_id: str,
        prompt: str,
        parent_context: dict = None,
        tools: list[str] = None,
    ) -> str:
        """派发 subagent 任务（非阻塞，立即返回 task_id）"""
        task_id = str(uuid.uuid4())
        task = SubagentTask(
            task_id=task_id,
            agent_id=agent_id,
            prompt=prompt,
            metadata={"parent_context": parent_context or {}},
        )

        # 保存初始状态
        self.deferred_store.save(task)

        # 后台执行
        async_task = asyncio.create_task(self._execute(task, tools))
        self._running_tasks[task_id] = async_task

        return task_id

    async def _execute(self, task: SubagentTask, tools: list[str] = None):
        """后台执行 subagent"""
        async with self._semaphore:
            try:
                task.status = TaskStatus.RUNNING
                self.deferred_store.save(task)

                # 构建隔离的 system prompt（无记忆、无团队协作）
                system_prompt = self._build_subagent_prompt(task.agent_id)

                # 限制工具集
                available_tools = tools or ALL_TOOLS

                # 执行（带超时）
                result = await asyncio.wait_for(
                    self._run_agent(system_prompt, task.prompt, available_tools),
                    timeout=TIMEOUT_S,
                )

                task.status = TaskStatus.COMPLETED
                task.result = result
                task.completed_at = datetime.now().isoformat()

            except asyncio.TimeoutError:
                task.status = TaskStatus.TIMEOUT
                task.error = "Task timed out after 15 minutes"
                task.completed_at = datetime.now().isoformat()
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.completed_at = datetime.now().isoformat()
            finally:
                self.deferred_store.save(task)
                self._running_tasks.pop(task.task_id, None)

    def _build_subagent_prompt(self, agent_id: str) -> str:
        """构建 subagent 的轻量 prompt"""
        return f"""你是一个独立子任务执行器（agent: {agent_id}）。

你的职责是完成分配给你的任务，然后返回结果。
- 专注执行，不需要闲聊
- 使用提供的工具完成任务
- 给出清晰、结构化的结果
- 如果遇到无法解决的问题，明确说明"""

    async def _run_agent(self, system_prompt: str, user_prompt: str, tools: list[str]) -> str:
        """实际调用 LLM 执行任务"""
        return await self.llm_call(system_prompt, user_prompt, tools)

    async def check_result(self, task_id: str) -> Optional[DeferredResult]:
        """检查任务结果"""
        return self.deferred_store.get_by_id(task_id)

    async def wait_result(self, task_id: str, timeout: float = 900) -> Optional[DeferredResult]:
        """等待任务完成"""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            result = self.deferred_store.get_by_id(task_id)
            if result and result.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT):
                return result
            await asyncio.sleep(1)
        return None

    def cancel(self, task_id: str) -> bool:
        """取消任务"""
        task = self._running_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def get_running_count(self) -> int:
        """获取正在运行的任务数"""
        return len(self._running_tasks)

    def dispatch_sync(
        self,
        agent_id: str,
        prompt: str,
        parent_context: dict = None,
        tools: list[str] = None,
    ) -> str:
        """同步派发并等待结果（阻塞，适合从同步代码调用）"""
        import concurrent.futures

        async def _run():
            task_id = await self.dispatch(agent_id, prompt, parent_context, tools)
            result = await self.wait_result(task_id, timeout=TIMEOUT_S)
            if result and result.result:
                return result.result
            if result and result.error:
                return f"[ERROR] {result.error}"
            return "[ERROR] Task did not complete"

        try:
            asyncio.get_running_loop()
            # 已有事件循环，用线程池避免嵌套
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _run())
                return future.result(timeout=TIMEOUT_S + 10)
        except RuntimeError:
            # 没有运行中的事件循环
            return asyncio.run(_run())
