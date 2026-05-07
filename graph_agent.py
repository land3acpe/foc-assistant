"""LangGraph orchestration layer for FOC-Assistant."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypedDict

from langgraph.graph import END, StateGraph

from config import get_base_url, get_model, MAX_ITERATIONS, REFLECTION_ENABLED, REFLECTION_MAX_RETRIES, REFLECTION_QUALITY_THRESHOLD, MEMORY_ENABLED
from router import Intent, route_intent
from tools import execute_tool
from validators import validate_execution_outputs


ProgressCallback = Callable[[str], Awaitable[None] | None]
AgentRunner = Callable[..., Awaitable[str]]


class GraphState(TypedDict, total=False):
    task: str
    intent: str
    route_reason: str
    final: str
    kb_result: str
    web_result: str
    validation_ok: bool
    validation_message: str
    validation_files: list[str]
    retry_count: int
    reflection_quality: float
    reflection_summary: str
    reflection_should_retry: bool
    memory_stored: list[str]
    tool_calls_log: list[dict]
    tool_results_log: list[str]


@dataclass
class GraphRunResult:
    intent: str
    final: str
    validation_ok: bool = True
    validation_message: str = ""
    validation_files: list[str] | None = None
    reflection_quality: float = 0.0
    reflection_summary: str = ""
    memory_stored: list[str] | None = None


class FOCGraphAgent:
    """Small, explicit LangGraph workflow around the existing agent loop."""

    def __init__(
        self,
        agent_runner: AgentRunner,
        progress: Optional[ProgressCallback] = None,
    ):
        self.agent_runner = agent_runner
        self.progress = progress
        self.graph = self._build_graph()

    async def run(self, task: str) -> GraphRunResult:
        state = await self.graph.ainvoke({"task": task, "retry_count": 0})
        return GraphRunResult(
            intent=state.get("intent", ""),
            final=state.get("final", ""),
            validation_ok=bool(state.get("validation_ok", True)),
            validation_message=state.get("validation_message", ""),
            validation_files=state.get("validation_files", []),
            reflection_quality=state.get("reflection_quality", 0.0),
            reflection_summary=state.get("reflection_summary", ""),
            memory_stored=state.get("memory_stored", []),
        )

    def _build_graph(self):
        graph = StateGraph(GraphState)

        graph.add_node("route", self._route_node)
        graph.add_node("chat", self._chat_node)
        graph.add_node("status", self._status_node)
        graph.add_node("meta", self._meta_node)
        graph.add_node("qa", self._qa_node)
        graph.add_node("research", self._research_node)
        graph.add_node("execution", self._execution_node)
        graph.add_node("validate", self._validate_node)
        graph.add_node("reflect", self._reflect_node)
        graph.add_node("memorize", self._memorize_node)
        graph.add_node("final", self._final_node)

        graph.set_entry_point("route")
        graph.add_conditional_edges(
            "route",
            self._route_edge,
            {
                "chat": "chat",
                "status": "status",
                "meta": "meta",
                "qa": "qa",
                "research": "research",
                "execution": "execution",
            },
        )
        graph.add_edge("chat", END)
        graph.add_edge("status", END)
        graph.add_edge("meta", END)
        graph.add_edge("qa", END)
        graph.add_edge("research", END)
        graph.add_edge("execution", "validate")
        graph.add_conditional_edges("validate", self._validation_edge, {"retry": "execution", "reflect": "reflect"})
        graph.add_conditional_edges("reflect", self._reflect_edge, {"retry": "execution", "memorize": "memorize"})
        graph.add_edge("memorize", "final")
        graph.add_edge("final", END)
        return graph.compile()

    async def _emit(self, text: str):
        if not self.progress:
            return
        result = self.progress(text)
        if inspect.isawaitable(result):
            await result

    async def _route_node(self, state: GraphState) -> GraphState:
        decision = route_intent(state["task"])
        return {
            "intent": decision.intent.value,
            "route_reason": decision.reason,
        }

    def _route_edge(self, state: GraphState) -> str:
        intent = state.get("intent", Intent.QA.value)
        if intent in {i.value for i in Intent}:
            return intent
        return Intent.QA.value

    async def _chat_node(self, state: GraphState) -> GraphState:
        task = state["task"]
        prompt = (
            "【纯聊天模式】\n"
            "你现在只是一个自然、简洁的聊天机器人。不要查知识库，不要调用工具，"
            "不要主动进入工程分析。只有用户明确提出代码、电机、资料、生成文件等任务时，"
            "外层路由才会切换到代码助理模式。\n\n"
            f"用户消息:\n{task}"
        )
        final = await self.agent_runner(
            prompt,
            thinking_mode="non-thinking",
            max_iterations=1,
            label="聊天",
            skill_task=task,
            enable_tools=False,
            enable_skills=False,
        )
        return {"final": final}

    async def _status_node(self, state: GraphState) -> GraphState:
        return {"final": "我现在在线。如果上一条任务仍在运行，QQ Bot 会先回复“正在处理上一条任务”；否则我就在等你的下一条任务。"}

    async def _meta_node(self, state: GraphState) -> GraphState:
        base_url = get_base_url()
        provider = "DeepSeek API" if "deepseek" in base_url.lower() else base_url
        return {
            "final": (
                f"我是 FOC-Assistant，一个运行在你本地电脑上的 FOC/PMSM 开发助手。\n"
                f"当前配置的大模型是 `{get_model()}`，接口来源是 {provider}。\n"
                "我通过 Python + LangGraph + qq-botpy 编排请求，并带有本地知识库、代码搜索、"
                "CSV 分析、CCS/Simulink/控制器参数计算等工具。"
            )
        }

    async def _qa_node(self, state: GraphState) -> GraphState:
        task = state["task"]
        await self._emit("已切换到代码助理模式：我先查本地知识库，然后直接回答。")
        kb_result = await asyncio.to_thread(execute_tool, "knowledge_search", {"query": task, "top_k": 5})
        prompt = (
            "【直接问答】\n"
            "请基于本地知识库结果和你的专业知识直接回答用户问题。"
            "如果知识库结果明显不相关，请忽略它；不要要求用户选择方向。\n\n"
            f"用户问题:\n{task}\n\n"
            f"本地知识库检索结果:\n{kb_result[:4000]}"
        )
        final = await self.agent_runner(
            prompt,
            thinking_mode="thinking",
            max_iterations=8,
            label="回答",
            skill_task=task,
        )
        return {"kb_result": kb_result, "final": final}

    async def _research_node(self, state: GraphState) -> GraphState:
        task = state["task"]
        await self._emit("已切换到代码助理模式：这是资料/研究类任务，我会先本地检索，再联网补充并给结论。")
        kb_result = await asyncio.to_thread(execute_tool, "knowledge_search", {"query": task, "top_k": 5})
        prompt = (
            "【研究任务】\n"
            "先用给定的本地知识库结果判断是否足够；不足时使用 web_search/web_fetch 补充。"
            "最后直接给出方向总结和建议，不要为了选择方向而停止。\n\n"
            f"用户任务:\n{task}\n\n"
            f"本地知识库检索结果:\n{kb_result[:4000]}"
        )
        final = await self.agent_runner(
            prompt,
            thinking_mode="thinking",
            max_iterations=12,
            label="研究",
            skill_task=task,
        )
        return {"kb_result": kb_result, "final": final}

    async def _execution_node(self, state: GraphState) -> GraphState:
        task = state["task"]
        retry_count = int(state.get("retry_count", 0))
        await self._emit("已切换到代码助理模式：我会直接落地文件/代码，并在结束前校验。")

        retry_note = ""
        if retry_count:
            retry_note = (
                "\n\n【上次执行未通过校验】\n"
                f"{state.get('validation_message', '')}\n"
                "请继续调用工具补齐缺失产物，然后再次校验。"
            )

        prompt = (
            "【执行任务 - 必须落地】\n"
            "不要只给计划，不要说“我将开始/准备编写”后就结束。\n"
            "要求:\n"
            "1. 需要资料时先用 knowledge_search / web_search / web_fetch；芯片手册优先官方资料。\n"
            "2. 需要生成文件时必须调用 write_file 创建到用户指定目录。\n"
            "3. 写完后必须用 list_directory 或 read_many_files 校验关键文件。\n"
            "4. 最终回复必须列出生成/修改的文件路径、核心功能、未验证项。\n"
            "5. 如果无法完成，明确说明阻塞原因。\n\n"
            f"用户原始任务:\n{task}"
            f"{retry_note}"
        )
        final = await self.agent_runner(
            prompt,
            thinking_mode="thinking_max",
            max_iterations=MAX_ITERATIONS,
            label="执行",
            skill_task=task,
        )
        return {"final": final}

    async def _validate_node(self, state: GraphState) -> GraphState:
        result = validate_execution_outputs(state["task"], state.get("final", ""))
        return {
            "validation_ok": result.ok,
            "validation_message": result.message,
            "validation_files": result.files,
            "retry_count": int(state.get("retry_count", 0)) + (0 if result.ok else 1),
        }

    def _validation_edge(self, state: GraphState) -> str:
        if state.get("validation_ok", True):
            return "reflect"
        if int(state.get("retry_count", 0)) <= 1:
            return "retry"
        return "reflect"

    async def _final_node(self, state: GraphState) -> GraphState:
        validation = state.get("validation_message", "")
        files = state.get("validation_files", []) or []
        reflection_quality = state.get("reflection_quality", 0.0)
        reflection_summary = state.get("reflection_summary", "")
        memory_stored = state.get("memory_stored", []) or []

        parts = [state.get("final", "")]

        if validation:
            parts.append(f"\n产物校验: {validation}")
            if files:
                shown = "\n".join(f"  - {p}" for p in files[:12])
                suffix = f"\n... 另有 {len(files) - 12} 个文件" if len(files) > 12 else ""
                parts.append(f"\n{shown}{suffix}")

        if REFLECTION_ENABLED and reflection_summary:
            quality_bar = "■" * int(reflection_quality * 10) + "□" * (10 - int(reflection_quality * 10))
            parts.append(f"\n[自评] {quality_bar} ({reflection_quality:.0%}) {reflection_summary}")

        if MEMORY_ENABLED and memory_stored:
            parts.append(f"\n[记忆] 已自动记录 {len(memory_stored)} 条洞察")

        return {"final": "".join(parts)}

    # ================================================================
    # 反思节点
    # ================================================================

    async def _reflect_node(self, state: GraphState) -> GraphState:
        if not REFLECTION_ENABLED:
            return {"reflection_quality": 1.0, "reflection_should_retry": False}

        task = state.get("task", "")
        final = state.get("final", "")
        intent = state.get("intent", "")
        tool_calls = state.get("tool_calls_log", [])
        tool_results = state.get("tool_results_log", [])

        await self._emit("正在评估执行质量...")

        try:
            from reflection import reflect_on_execution
            result = await asyncio.to_thread(
                reflect_on_execution, task, final, tool_calls, tool_results, intent
            )
            await self._emit(f"自评完成: {result.quality:.0%} — {result.summary}")
            return {
                "reflection_quality": result.quality,
                "reflection_summary": result.summary,
                "reflection_should_retry": result.should_retry,
            }
        except Exception as e:
            return {
                "reflection_quality": 0.5,
                "reflection_summary": f"反思异常: {e}",
                "reflection_should_retry": False,
            }

    def _reflect_edge(self, state: GraphState) -> str:
        should_retry = state.get("reflection_should_retry", False)
        quality = state.get("reflection_quality", 1.0)
        retry_count = int(state.get("retry_count", 0))

        if should_retry and quality < REFLECTION_QUALITY_THRESHOLD and retry_count <= REFLECTION_MAX_RETRIES:
            return "retry"
        return "memorize"

    # ================================================================
    # 记忆节点
    # ================================================================

    async def _memorize_node(self, state: GraphState) -> GraphState:
        if not MEMORY_ENABLED:
            return {"memory_stored": []}

        task = state.get("task", "")
        final = state.get("final", "")
        intent = state.get("intent", "")
        tool_calls = state.get("tool_calls_log", [])
        tool_results = state.get("tool_results_log", [])

        # 洞察提取已废（Issue #1：记忆系统简化为三层架构）
        # ChatMemory 集成将在 Issue #2 中接入 LangGraph chat/qa 节点
        return {"memory_stored": []}
