"""FOC-Assistant QQ 网关 —— 基于腾讯 QQ 官方 Bot API (qq-botpy)

支持智能工作流:
1. 本地知识库搜索 (non-thinking, 快速)
2. 知识库命中 → 深度推理 (thinking_max)
3. 知识库未命中 → 联网搜索+方向总结 (non-thinking) → 询问用户 → 深度推理 (thinking_max)

使用前需在 https://q.qq.com/ 注册机器人，获取 AppID 和 AppSecret。
"""

import asyncio
import ctypes
import json
import os
import re
import threading
import traceback
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import botpy
from botpy import Intents
from botpy.message import Message

from agent import AgentCallbacks, agent_loop
from config import (
    BASE_URL,
    DESKTOP,
    MAX_ITERATIONS,
    MODEL,
    QQ_APP_ID,
    QQ_APP_SECRET,
    QQ_DANGER_ALLOW,
    QQ_MAX_RESPONSE_LEN,
)
from graph_agent import FOCGraphAgent
from tools import execute_tool
from scheduler import get_scheduler
from config import MEMORY_ENABLED, SCHEDULER_ENABLED

_MUTEX_HANDLE = None
AGENT_RUN_LOG = Path(__file__).with_name("agent_runs.log")


def _append_agent_log(event: str, detail: str = ""):
    """Append lightweight Agent workflow logs for later diagnosis."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        AGENT_RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        AGENT_RUN_LOG.open("a", encoding="utf-8").write(f"{ts}\t{event}\t{detail}\n")
    except Exception:
        pass


def _redact_tool_args(args: dict) -> str:
    """Keep logs useful without writing full generated file contents."""
    safe = dict(args or {})
    for key in ("content", "old_text", "new_text"):
        if key in safe:
            safe[key] = f"<{len(str(safe[key]))} chars>"
    return json.dumps(safe, ensure_ascii=False)[:1000]

# ---------------------------------------------------------------------------
# 工作流状态机
# ---------------------------------------------------------------------------

class WFState(Enum):
    IDLE = "idle"
    WAIT_DIRECTION = "wait_direction"     # 等待用户选择方向
    SEARCHING = "searching"              # 正在搜索/推理（防重入）


class WorkflowSession:
    """每个用户的工作流会话"""
    def __init__(self, uid: str):
        self.uid = uid
        self.state = WFState.IDLE
        self.original_task = ""
        self.web_directions: list[str] = []   # 搜索结果方向列表
        self.direction_map: dict[str, str] = {}  # 编号→方向名
        self.web_results_text = ""             # 搜索结果原始文本
        self.stage_log: list[str] = []         # 各阶段摘要，用于跨阶段上下文传递
        self.last_active = datetime.now()
        self.cancel_event = threading.Event()

    def reset(self):
        self.state = WFState.IDLE
        self.original_task = ""
        self.web_directions = []
        self.direction_map = {}
        self.web_results_text = ""
        self.stage_log = []
        self.cancel_event.clear()


# ---------------------------------------------------------------------------
# QQ Bot Client
# ---------------------------------------------------------------------------

class FOCQQBot(botpy.Client):
    """FOC-Assistant QQ 机器人（支持智能工作流）"""

    def __init__(self, intents: Intents):
        super().__init__(intents=intents)
        self.sessions: dict[str, WorkflowSession] = {}  # uid → session
        self._processed_msgs: dict[str, datetime] = {}  # 消息去重缓存

    # ---- 消息入口 ----

    async def on_c2c_message_create(self, message: Message):
        await self._dispatch(message)

    async def on_at_message_create(self, message: Message):
        await self._dispatch(message)

    async def _dispatch(self, message: Message):
        content = (message.content or "").strip()
        if not content:
            return

        # 消息去重：防止 botpy 重复投递或竞态导致双重处理
        msg_id = getattr(message, 'id', None) or f"{self._get_uid(message)}:{hash(content)}"
        if msg_id in self._processed_msgs:
            return
        self._processed_msgs[msg_id] = datetime.now()
        if len(self._processed_msgs) > 200:
            self._processed_msgs.clear()  # 简单策略：满了就清

        uid = self._get_uid(message)
        session = self._get_session(uid)
        session.last_active = datetime.now()

        if self._is_cancel_command(content):
            if session.state == WFState.SEARCHING:
                session.cancel_event.set()
                await self._reply(message, "已收到终止指令，正在打断当前任务。")
            elif session.state == WFState.WAIT_DIRECTION:
                session.reset()
                await self._reply(message, "已取消当前等待中的任务。")
            else:
                await self._reply(message, "当前没有正在运行的任务。")
            return

        # ---- 状态路由 ----
        if session.state == WFState.WAIT_DIRECTION:
            await self._handle_direction_pick(session, message, content)
            return

        if session.state == WFState.SEARCHING:
            await self._reply(message, "(正在处理上一条任务，请稍候...)")
            return

        # ---- 新任务 ----
        print(f"\n[QQ] 新任务 [{uid[:12]}]: {content[:80]}")
        _append_agent_log("task.start", f"uid={uid[:12]} task={content[:500]}")
        session.reset()
        session.original_task = content
        session.state = WFState.SEARCHING

        try:
            await self._run_graph_workflow(session, message, content)
        except Exception as e:
            traceback.print_exc()
            await self._reply(message, f"[错误] {e}")
        finally:
            if session.state != WFState.WAIT_DIRECTION:
                session.reset()

    # ---- LangGraph 工作流核心 ----

    async def _run_graph_workflow(self, session: WorkflowSession, message: Message, task: str):
        async def progress(text: str):
            await self._reply(message, text)

        async def runner(prompt: str, **kwargs) -> str:
            return await self._run_agent(prompt, message, cancel_event=session.cancel_event, **kwargs)

        graph_agent = FOCGraphAgent(agent_runner=runner, progress=progress)
        result = await graph_agent.run(task)
        print(f"[QQ] Graph intent={result.intent}, validation={result.validation_ok}: {task[:80]}")
        _append_agent_log(
            "task.end",
            f"intent={result.intent} validation={result.validation_ok} "
            f"reflection={result.reflection_quality:.2f} "
            f"memory={len(result.memory_stored or [])} "
            f"message={result.validation_message[:300]}",
        )

        # 洞察提取已废（Issue #1：记忆系统简化为三层架构）
        # ChatMemory 集成留给 Issue #2

        for chunk in self._split_text(result.final):
            await self._reply(message, chunk)

    # ---- 旧工作流（保留作回退/参考） ----

    async def _run_execution_workflow(self, session: WorkflowSession, message: Message, task: str):
        """执行型任务：直接完成文件/代码/工程修改，不进入方向选择。"""
        await self._reply(message, "收到，按执行任务处理：我会直接查资料/写文件，并在结束前校验产物。")
        exec_task = (
            "【执行任务 - 必须落地】\n"
            "用户要求你完成一个实际工程任务。不要只给计划，不要说“我将开始/准备编写”后就结束。\n"
            "工作要求:\n"
            "1. 需要资料时先用 knowledge_search / web_search / web_fetch 查找；芯片手册优先查官方资料。\n"
            "2. 需要生成文件时必须调用 write_file 创建到用户指定目录；如果目录不存在，write_file 会自动创建父目录。\n"
            "3. 写完后必须用 list_directory 或 read_many_files 校验文件确实存在，并检查关键内容。\n"
            "4. 最终回复必须列出已生成/修改的文件路径、核心功能、未验证项。\n"
            "5. 如果受限无法完成，明确说明阻塞原因；不要假装已经写入。\n\n"
            f"用户原始任务:\n{task}"
        )
        result = await self._run_agent(
            exec_task,
            message,
            thinking_mode="thinking_max",
            max_iterations=MAX_ITERATIONS,
            label="执行",
            skill_task=task,
        )
        for chunk in self._split_text(result):
            await self._reply(message, chunk)

        verification = self._verify_requested_outputs(task)
        if verification:
            for chunk in self._split_text("产物校验:\n" + verification):
                await self._reply(message, chunk)

    async def _run_answer_workflow(self, session: WorkflowSession, message: Message, task: str):
        """明确问答任务：查知识库后直接回答，不要求用户选择方向。"""
        await self._reply(message, "我先快速查一下本地知识库，然后直接回答。")
        loop = asyncio.get_running_loop()
        kb_result = await loop.run_in_executor(
            None, execute_tool, "knowledge_search", {"query": task, "top_k": 5}
        )
        answer_task = (
            "【直接问答】\n"
            "请基于本地知识库结果和你的通用知识直接回答用户问题。"
            "如果知识库结果明显不相关，请忽略它，不要声称“找到相关材料”。"
            "不要要求用户选择方向，除非问题本身确实缺少必要条件。\n\n"
            f"用户问题:\n{task}\n\n"
            f"本地知识库检索结果:\n{kb_result[:4000]}"
        )
        result = await self._run_agent(
            answer_task,
            message,
            thinking_mode="thinking",
            max_iterations=8,
            label="回答",
            skill_task=task,
        )
        for chunk in self._split_text(result):
            await self._reply(message, chunk)

    async def _run_smart_workflow(self, session: WorkflowSession, message: Message, task: str):
        """三阶段智能工作流"""

        # ====== 阶段1: 本地知识库搜索 (non-thinking, 快速检索) ======
        await self._reply(message, " 查找本地知识库...")
        loop = asyncio.get_running_loop()
        kb_result = await loop.run_in_executor(
            None, execute_tool, "knowledge_search", {"query": task, "top_k": 5}
        )
        session.stage_log.append(f"[阶段1·知识库检索] 搜索主题:「{task[:100]}」\n结果摘要: {kb_result[:400]}")

        kb_has_info = self._kb_has_useful_info(kb_result)

        if kb_has_info:
            # 本地知识库有料 → 先呈现给用户，询问是否深度推理
            session.state = WFState.WAIT_DIRECTION
            session.web_results_text = kb_result
            session.original_task = task
            prompt = (
                f" 知识库找到相关材料:\n\n{kb_result[:2500]}\n\n"
                f"---\n"
                f"回复 **'深度分析'** — 开启 thinking_max 深入推理\n"
                f"回复 **'联网补充'** — 先联网搜索再分析\n"
                f"回复具体问题 — 针对性回答"
            )
            for chunk in self._split_text(prompt):
                await self._reply(message, chunk)
            return

        # ====== 阶段2: 联网搜索+方向总结 (non-thinking) ======
        await self._reply(message, " 知识库无匹配，联网搜索中...")
        history = "\n".join(session.stage_log) if session.stage_log else ""
        web_result = await self._run_agent(
            f"用 web_search 搜索以下主题（中英文各搜一次），"
            f"然后将结果分成 3-5 个技术方向，每个方向一句话概述。"
            f"最后一行写 '请回复数字选择方向'。"
            f"不要展开技术细节！\n\n主题: {task}",
            message,
            thinking_mode=None, max_iterations=6, label="搜索", pct_offset=30,
            history_context=history,
            skill_task=task,
        )
        session.stage_log.append(f"[阶段2·联网搜索] 搜索主题:「{task[:100]}」\n结果概述: {web_result[:400]}")

        session.web_results_text = web_result
        session.web_directions = self._parse_directions(web_result, session)

        if len(session.web_directions) >= 2:
            session.state = WFState.WAIT_DIRECTION
            prompt = f" 网络搜索完成，回复数字选择方向:\n\n{web_result}\n\n---\n回复数字 (如 '1' 或 '1,3') 开始深入分析，或回复 '取消'"
            for chunk in self._split_text(prompt):
                await self._reply(message, chunk)
        else:
            await self._reply(message, " 资料较少，直接深度分析...")
            history = "\n".join(session.stage_log) if session.stage_log else ""
            deep_result = await self._run_agent(
                f"基于搜索结果深入回答。如果需要写代码，请写出**完整可编译运行的代码**，"
                f"包含必要的 #include、函数实现、初始化配置，不要只写伪代码或注释占位符。\n\n"
                f"问题: {task}\n\n搜索结果: {web_result[:4000]}",
                message, thinking_mode="thinking_max", label="推理", pct_offset=60,
                history_context=history,
                skill_task=task,
            )
            for chunk in self._split_text(deep_result):
                await self._reply(message, chunk)

    async def _handle_direction_pick(self, session: WorkflowSession, message: Message, content: str):
        """用户在等待选择状态下的回复"""
        content_lower = content.strip().lower()

        if content_lower in ("取消", "不用了", "算了", "quit"):
            await self._reply(message, "已取消。")
            session.reset()
            return

        # ---- KB 命中后的交互 ----
        if "深度分析" in content_lower or "深入" in content_lower:
            session.state = WFState.SEARCHING
            await self._reply(message, " 深度推理中 (thinking_max)...")
            session.stage_log.append(f"[阶段3·用户选择] 用户要求基于知识库深度分析")
            history = "\n".join(session.stage_log)
            deep_result = await self._run_agent(
                f"基于本地知识库的检索结果，深入回答以下问题。请调用 read_file 查看相关文档细节，"
                f"结合项目代码给出实现建议。如果需要写代码，请写出**完整可编译运行的代码**，"
                f"包含必要的 #include、函数实现、初始化配置，不要只写伪代码或注释占位符。\n\n"
                f"{session.original_task}\n\n"
                f"知识库检索结果:\n{session.web_results_text[:3000]}",
                message, thinking_mode="thinking_max", label="推理", pct_offset=60,
                history_context=history,
                skill_task=session.original_task,
            )
            for chunk in self._split_text(deep_result):
                await self._reply(message, chunk)
            session.reset()
            return

        if "联网" in content_lower or "搜索" in content_lower:
            session.state = WFState.SEARCHING
            await self._reply(message, " 联网补充搜索...")
            session.stage_log.append(f"[阶段3·用户选择] 用户要求联网补充搜索")
            history = "\n".join(session.stage_log)
            web_result = await self._run_agent(
                f"用 web_search 搜索以下主题（中英文各搜一次），将结果分成 3-5 个方向概述。"
                f"不要展开技术细节！\n\n主题: {session.original_task}",
                message, thinking_mode=None, max_iterations=6, pct_offset=30,
                history_context=history,
                skill_task=session.original_task,
            )
            session.web_results_text = web_result
            session.web_directions = self._parse_directions(web_result, session)
            if len(session.web_directions) >= 2:
                session.state = WFState.WAIT_DIRECTION
                prompt = f" 回复数字选择方向:\n\n{web_result}\n\n---\n回复数字 (如 '1' 或 '1,3')，或回复 '取消'"
                for chunk in self._split_text(prompt):
                    await self._reply(message, chunk)
            else:
                session.reset()
                history = "\n".join(session.stage_log)
                deep_result = await self._run_agent(
                    f"基于搜索结果深入回答。如果需要写代码，请写出**完整可编译运行的代码**，"
                    f"包含必要的 #include、函数实现、初始化配置，不要只写伪代码或注释占位符。\n\n"
                    f"{session.original_task}\n\n{web_result[:4000]}",
                    message, thinking_mode="thinking_max", label="推理", pct_offset=60,
                    history_context=history,
                    skill_task=session.original_task,
                )
                for chunk in self._split_text(deep_result):
                    await self._reply(message, chunk)
            return

        # ---- 联网搜索后的方向选择 ----
        selected = self._parse_user_choice(content, session.direction_map)
        if not selected:
            await self._reply(message, "请回复 '深度分析' / '联网补充' / 数字编号 / '取消'")
            return

        session.state = WFState.SEARCHING
        await self._reply(message, f" 深入分析: {', '.join(selected)}...")
        session.stage_log.append(f"[阶段3·方向选择] 用户选择了: {', '.join(selected)}")
        history = "\n".join(session.stage_log)
        task = (
            f"【深度分析】用户选择以下方向深入研究:\n{', '.join(selected)}\n\n"
            f"原始问题: {session.original_task}\n\n"
            f"搜索结果: {session.web_results_text[:3000]}\n\n"
            f"要求: 1) web_fetch 打开相关链接 2) 需要时继续 web_search "
            f"3) 结合项目代码给实现建议 4) 关键知识用 knowledge_add 存库 5) 输出完整技术方案 "
            f"6) 如果需要写代码，写出**完整可编译运行的代码**，包含必要的 #include、函数实现、"
            f"初始化配置，不要只写伪代码或注释占位符"
        )
        deep_result = await self._run_agent(task, message, thinking_mode="thinking_max", label="推理",
                                             pct_offset=60, history_context=history,
                                             skill_task=session.original_task)
        for chunk in self._split_text(deep_result):
            await self._reply(message, chunk)
        session.reset()

    # ---- Agent 调用 ----

    async def _run_agent(self, task: str, message: Message, thinking_mode: Optional[str] = None,
                         max_iterations: int = MAX_ITERATIONS, label: str = "思考",
                         history_context: str = "", pct_offset: int = 0,
                         skill_task: Optional[str] = None,
                         enable_tools: bool = True,
                         enable_skills: bool = True,
                         cancel_event: Optional[threading.Event] = None) -> str:
        """运行 agent_loop，用百分比推送进展。

        Args:
            pct_offset: 进度条起始偏移（0/30/60），避免多阶段百分比重置
            history_context: 前几个阶段的对话摘要
        """
        parts: list[str] = []
        current_round = [0]
        tool_count = [0]
        est_max = min(max_iterations, 12 if thinking_mode == "thinking_max" else 5)
        main_loop = asyncio.get_running_loop()
        openid = message.author.user_openid
        api = self.api
        last_pct = [-1]

        # 每个阶段的百分比区间宽度
        range_width = 28 if pct_offset < 60 else 35
        live_step_count = [0]

        def emit_live(text: str):
            if live_step_count[0] >= 18:
                return
            clean = re.sub(r"\s+", " ", text).strip()
            if not clean:
                return
            live_step_count[0] += 1
            asyncio.run_coroutine_threadsafe(
                self._reply(message, clean[:QQ_MAX_RESPONSE_LEN]),
                main_loop,
            )
            _append_agent_log("progress", clean[:1000])

        def describe_tool_step(name: str, args: dict) -> str:
            labels = {
                "knowledge_search": "查本地知识库",
                "knowledge_import": "导入知识库",
                "knowledge_add": "写入知识库笔记",
                "web_search": "联网搜索资料",
                "web_fetch": "打开网页资料",
                "download_file": "下载资料文件",
                "read_file": "读取文件",
                "read_many_files": "批量读取文件",
                "search_code": "搜索代码",
                "find_files": "查找文件",
                "project_overview": "扫描项目结构",
                "write_file": "写入文件",
                "edit_file": "修改文件",
                "list_directory": "校验目录",
                "compile_ccs": "尝试编译 CCS 工程",
                "run_command": "执行本地命令",
            }
            target = (
                args.get("path")
                or args.get("filepath")
                or args.get("directory")
                or args.get("project_path")
                or args.get("query")
                or args.get("url")
                or ""
            )
            target = str(target).replace("\n", " ").strip()
            if len(target) > 120:
                target = target[:117] + "..."
            verb = labels.get(name, f"调用工具 {name}")
            return f"{label}步骤 {tool_count[0]}：{verb}" + (f" - {target}" if target else "")

        def push_pct():
            raw_pct = min(int(current_round[0] / est_max * 100), 100)
            pct = pct_offset + int(raw_pct * range_width / 100)
            pct = min(pct, 95)
            # 只在跨越 25% 节点时推送，减少消息轰炸
            milestone = (pct // 25) * 25
            if milestone > last_pct[0] and milestone >= 25:
                last_pct[0] = milestone
                asyncio.run_coroutine_threadsafe(
                    api.post_c2c_message(openid=openid, content=f"{label}中... {milestone}%"),
                    main_loop,
                )

        def on_token(t: str):
            parts.append(t)

        def on_tool_call(name: str, args: dict):
            tool_count[0] += 1
            print(f"  [TOOL] {name}({str(args)[:300]})")
            _append_agent_log("tool.call", f"{name} {_redact_tool_args(args)}")
            emit_live(describe_tool_step(name, args))

        def on_tool_result(result: str):
            one_line = result.replace("\n", " ") if isinstance(result, str) else str(result)
            print(f"  [RESULT] {one_line[:500]}{'...' if len(one_line) > 500 else ''}")
            _append_agent_log("tool.result", one_line[:1000])
            important_prefixes = (
                "文件已写入:",
                "编辑成功:",
                "文件已下载:",
                "权限拒绝:",
                "错误:",
                "写入失败:",
                "下载失败:",
            )
            if one_line.startswith(important_prefixes):
                emit_live(f"{label}结果：{one_line[:500]}")

        def on_danger_confirm(command: str) -> bool:
            return False

        def on_status(msg: str):
            print(msg)
            if msg.startswith("--- Round"):
                current_round[0] += 1
                push_pct()

        def on_complete(summary: str, elapsed: float, calls: int):
            print(f"  [DONE] {elapsed:.1f}s, {calls} tool calls, output {len(summary)} chars")
            _append_agent_log("agent.complete", f"label={label} elapsed={elapsed:.1f}s calls={calls} output={len(summary)}")

        def on_cancel_check() -> bool:
            return bool(cancel_event and cancel_event.is_set())

        callbacks = AgentCallbacks(
            on_token=on_token, on_tool_call=on_tool_call,
            on_tool_result=on_tool_result, on_danger_confirm=on_danger_confirm,
            on_status=on_status, on_complete=on_complete,
            on_cancel_check=on_cancel_check,
        )

        try:
            result = await main_loop.run_in_executor(
                None, agent_loop, task, max_iterations, callbacks, thinking_mode,
                history_context, skill_task or task, enable_tools, enable_skills,
            )
            return result or "".join(parts) or "(Agent 未产生输出)"
        except Exception as e:
            traceback.print_exc()
            return f"[ERROR] Agent 执行异常: {e}"

    # ---- 帮助方法 ----

    def _is_cancel_command(self, content: str) -> bool:
        compact = re.sub(r"\s+", "", content.strip().lower())
        return compact in {
            "终止",
            "终止任务",
            "停止",
            "停止任务",
            "取消任务",
            "打断",
            "中断",
            "abort",
            "cancel",
            "stop",
        }

    def _get_uid(self, message: Message) -> str:
        author = getattr(message, 'author', None)
        uid = (getattr(author, 'user_openid', None) or getattr(author, 'id', None) or str(id(message)))
        return str(uid)[:32]

    def _get_session(self, uid: str) -> WorkflowSession:
        if uid not in self.sessions:
            self.sessions[uid] = WorkflowSession(uid)
        return self.sessions[uid]

    async def _reply(self, message: Message, text: str):
        msg_type = type(message).__name__
        try:
            if msg_type == "C2CMessage":
                await self.api.post_c2c_message(
                    openid=message.author.user_openid, content=text,
                )
            else:
                await message.reply(content=text)
        except Exception as e:
            print(f"[QQ] 回复失败: {e}")

    def _kb_has_useful_info(self, kb_result: str) -> bool:
        """判断知识库搜索结果是否有实质内容"""
        no_info_markers = [
            "No results in knowledge base",
            "未找到相关结果",
            "未在",
            "没有找到",
            "无匹配",
            "不相关",
            "均不相关",
            "Knowledge base is empty",
            "Search query too short",
        ]
        result_lower = kb_result.lower()
        if any(m.lower() in result_lower for m in no_info_markers):
            return False
        # 结果太短也视为无信息
        if len(kb_result) < 80:
            return False
        return True

    def _direct_reply(self, content: str, session: WorkflowSession) -> str:
        """不需要 Agent/知识库的低延迟直接回复。"""
        text = content.strip().lower()
        compact = re.sub(r"\s+", "", text)

        status_patterns = (
            "/status", "状态", "进度", "在干啥", "在干嘛", "忙吗", "还在吗",
            "处理完了吗", "还在处理吗", "what are you doing",
        )
        if any(p in compact or p in text for p in status_patterns):
            if session.state == WFState.SEARCHING:
                return f"我正在处理上一条任务：{session.original_task[:120]}"
            if session.state == WFState.WAIT_DIRECTION:
                return "我在等你选择方向。你可以回复数字编号、`深度分析`、`联网补充` 或 `取消`。"
            return "我现在空闲在线，正在等你的下一条任务。"

        meta_patterns = (
            "什么大模型", "哪个大模型", "大模型驱动", "你用的模型", "你是什么模型",
            "你是谁", "who are you", "what model", "which model",
        )
        if any(p in compact or p in text for p in meta_patterns):
            provider = "DeepSeek API" if "deepseek" in BASE_URL.lower() else BASE_URL
            return (
                f"我是 FOC-Assistant，一个运行在你本地电脑上的 FOC/PMSM 开发助手。\n"
                f"当前配置的大模型是 `{MODEL}`，接口来源是 {provider}。\n"
                f"我通过 Python + qq-botpy 接入 QQ，并带有本地知识库、代码搜索、CSV 分析、"
                f"CCS/Simulink/控制器参数计算等工具。"
            )

        greetings = {"你好", "你好啊", "hi", "hello", "在吗", "哈喽"}
        if compact in greetings:
            return "你好，我是 FOC-Assistant。可以帮你查 FOC/PMSM 资料、读代码、分析波形，也可以直接回答工程问题。"

        return ""

    def _is_execution_task(self, content: str) -> bool:
        """需要落地到文件/工程/命令的任务，直接执行，不走方向选择。"""
        compact = re.sub(r"\s+", "", content.lower())
        action_words = (
            "生成", "写出", "帮我写", "创建", "新建", "保存", "输出到", "放到",
            "修改", "改一下", "实现", "编写", "生成示例代码", "examplecode",
            "写入", "导出", "整理成文件",
        )
        target_words = (
            "代码", ".c", ".h", ".py", ".m", ".md", "文件", "目录", "工程",
            "桌面", "desktop", "focexamplecode", "保存到",
        )
        return any(w in compact for w in action_words) and any(w in compact for w in target_words)

    def _is_simple_qa_task(self, content: str) -> bool:
        """明确问答/解释类任务，直接回答，不强迫用户选方向。"""
        compact = re.sub(r"\s+", "", content.lower())
        if len(compact) <= 12:
            return False
        qa_words = (
            "是什么", "为什么", "怎么", "如何", "解释", "说明", "总结", "对比",
            "区别", "原理", "公式", "参数", "讲一下", "介绍", "分析一下",
        )
        execution_hints = ("生成", "写", "创建", "保存", "修改", "放到", "输出到")
        return any(w in compact for w in qa_words) and not any(w in compact for w in execution_hints)

    def _verify_requested_outputs(self, content: str) -> str:
        """对常见输出目录做真实文件系统校验。"""
        compact = re.sub(r"\s+", "", content.lower())
        paths = []
        if "focexamplecode" in compact:
            paths.append(str(DESKTOP / "focexamplecode"))

        # 捕获显式 Windows 路径，避免只支持 focexamplecode。
        for m in re.finditer(r"[A-Za-z]:\\[^\s，。；;]+", content):
            paths.append(m.group(0).rstrip("。,.，"))

        seen = set()
        reports = []
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            result = execute_tool("list_directory", {"path": path, "recursive": True})
            reports.append(f"{path}\n{result}")
        return "\n\n".join(reports)

    def _parse_directions(self, text: str, session: WorkflowSession) -> list[str]:
        """从 Agent 回复中解析方向列表"""
        import re
        directions = []
        session.direction_map = {}
        # 匹配编号标题: "1. **方向名**" 或 "1. 方向名"
        pattern = r'(?:^|\n)\s*(\d+)[\.\、\)]\s*(?:\*\*)?([^*\n]+?)(?:\*\*)?(?:\s*—|：|:|\n|$)'
        matches = re.findall(pattern, text)
        for num, direction in matches:
            name = direction.strip()[:60]
            if len(name) > 3:
                directions.append(name)
                session.direction_map[num] = name
        return directions

    def _parse_user_choice(self, content: str, direction_map: dict[str, str]) -> list[str]:
        """解析用户选择的数字"""
        import re
        nums = re.findall(r'\d+', content)
        selected = []
        for n in nums:
            if n in direction_map:
                selected.append(direction_map[n])
        return selected

    def _split_text(self, text: str) -> list[str]:
        if len(text) <= QQ_MAX_RESPONSE_LEN:
            return [text]
        chunks = []
        remaining = text
        while len(remaining) > QQ_MAX_RESPONSE_LEN:
            split_at = remaining.rfind("\n", 0, QQ_MAX_RESPONSE_LEN)
            if split_at < QQ_MAX_RESPONSE_LEN // 2:
                split_at = remaining.rfind(" ", 0, QQ_MAX_RESPONSE_LEN)
            if split_at < QQ_MAX_RESPONSE_LEN // 2:
                split_at = QQ_MAX_RESPONSE_LEN
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def acquire_single_instance_lock() -> bool:
    """Windows 进程级单实例锁，避免同一个 QQ Bot 被启动两次。"""
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True

    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Global\\FOC_Assistant_QQ_Bot")
    already_exists = kernel32.GetLastError() == 183
    if already_exists:
        print("[QQ] 已有一个 FOC-Assistant QQ Bot 实例在运行，本进程退出。")
        return False
    return True


def main():
    app_id = QQ_APP_ID
    app_secret = QQ_APP_SECRET

    if not app_id or not app_secret:
        print("=" * 55)
        print("  QQ Bot 配置缺失")
        print("  请设置环境变量 QQ_APP_ID / QQ_APP_SECRET")
        print("  注册地址: https://q.qq.com/")
        print("=" * 55)
        return

    # 启动后台调度器
    if SCHEDULER_ENABLED:
        scheduler = get_scheduler()
        scheduler.start()

    print("=" * 55)
    print("  FOC-Assistant QQ Bot (LangGraph Workflow)")
    print(f"  启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  AppID: {app_id[:8]}...")
    print(f"  反思系统: 启用 | 记忆系统: {'启用' if MEMORY_ENABLED else '禁用'}")
    print(f"  调度器: {'启用' if SCHEDULER_ENABLED else '禁用'}")
    print("=" * 55)

    intents = Intents(public_guild_messages=True, direct_message=True, public_messages=True)
    client = FOCQQBot(intents=intents)

    try:
        client.run(appid=app_id, secret=app_secret)
    finally:
        # 停止调度器
        if SCHEDULER_ENABLED:
            get_scheduler().stop()


def daemon_main():
    retry_delay = 5
    failures = 0
    while True:
        try:
            failures = 0
            main()
        except KeyboardInterrupt:
            print("[QQ] 退出")
            break
        except Exception as e:
            failures += 1
            traceback.print_exc()
            if failures >= 10:
                break
            print(f"[QQ] 崩溃 #{failures}, {retry_delay}s 后重启...")
            import time
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)


if __name__ == "__main__":
    if acquire_single_instance_lock():
        daemon_main()
