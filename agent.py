"""FOC-Assistant —— 永磁同步电机 FOC 开发 AI 助手"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from openai import OpenAI

from config import MAX_ITERATIONS, STREAM_OUTPUT, SYSTEM_PROMPT, get_v4_params, get_thinking_mode, SKILLS, SKILL_AUTO_DETECT, get_model_manager
from knowledge import get_kb
from tools import TOOLS, execute_tool
from tracing import get_tracer
from guardrails import get_input_guardrail, get_output_guardrail


@dataclass
class AgentCallbacks:
    """Agent 输出回调——允许外部调用者（如微信 Bot）接收 Agent 的实时输出。"""
    on_token: Callable[[str], None] = lambda t: print(t, end="", flush=True)
    on_tool_call: Callable[[str, dict], None] = lambda n, a: print(f"  [TOOL] {n}({json.dumps(a, ensure_ascii=False)[:120]})")
    on_tool_result: Callable[[str], None] = lambda r: print(f"  [RESULT] {r[:200].replace(chr(10), ' ')}{'...' if len(r) > 200 else ''}")
    on_danger_confirm: Callable[[str], bool] = lambda c: False  # 默认拒绝
    on_status: Callable[[str], None] = lambda s: print(s)
    on_complete: Callable[[str, float, int], None] = lambda s, e, c: print(f"\n{'='*55}\n  [DONE]  |  {e:.1f}s  |  {c} tool calls\n{'='*55}")
    on_cancel_check: Callable[[], bool] = lambda: False


class AgentCancelled(Exception):
    """Raised when an external caller asks the current agent loop to stop."""


def detect_and_inject_skill(task: str) -> str:
    """根据用户任务自动检测匹配的 Skill，返回增强后的 System Prompt"""
    if not SKILL_AUTO_DETECT:
        return SYSTEM_PROMPT

    # 统计每个 skill 的触发关键词匹配数
    scores = {}
    task_lower = task.lower()
    for skill_id, skill in SKILLS.items():
        score = sum(1 for kw in skill["trigger"] if kw.lower() in task_lower)
        if score > 0:
            scores[skill_id] = score

    if not scores:
        return SYSTEM_PROMPT  # 无匹配，用默认 prompt

    # 选匹配度最高的 skill
    best_skill_id = max(scores, key=scores.get)
    best_skill = SKILLS[best_skill_id]

    enhanced = SYSTEM_PROMPT + "\n\n" + best_skill["prompt_addon"]
    print(f"  [SKILL] Activated: {best_skill['name']}")
    return enhanced


def agent_loop(
    user_task: str,
    max_iterations: int = MAX_ITERATIONS,
    callbacks: Optional[AgentCallbacks] = None,
    thinking_mode: Optional[str] = None,
    history_context: str = "",
    skill_task: Optional[str] = None,
    enable_tools: bool = True,
    enable_skills: bool = True,
    task_type: str = "tool",
    system_prompt_override: Optional[str] = None,
) -> str:
    """Agent 主循环。返回累积的完整响应文本。

    Args:
        thinking_mode: 覆盖 config.THINKING_MODE，可选 "non-thinking" / "thinking" / "thinking_max"
        history_context: 前几个阶段的对话摘要，会拼接到 user_task 前面，帮助 LLM 保持上下文连续
        skill_task: 用于 Skill 自动检测的原始用户任务。为空时使用 user_task。
        enable_tools: False 时不暴露工具，适合纯聊天。
        enable_skills: False 时不自动注入 Skill，避免寒暄误触发专业模式。
        task_type: 任务类型，用于混合模型选择 ("tool" / "reasoning" / "chat" / "reflection")
        system_prompt_override: 如果提供，直接用作 system prompt，跳过 Skill 检测和经验注入。
                                用于子 Agent 调用，由调用者构建完整的专业 prompt。
    """
    # 构建完整的用户任务（含历史上下文）
    full_task = user_task
    if history_context:
        full_task = (
            f"【对话历史 —— 以下是本轮对话中已经完成的步骤，请保持上下文连贯，不要重复已完成的工作】\n"
            f"{history_context}\n\n"
            f"---\n"
            f"【当前任务】\n"
            f"{user_task}"
        )

    if callbacks is None:
        callbacks = AgentCallbacks()

    def check_cancelled():
        try:
            cancelled = callbacks.on_cancel_check()
        except Exception:
            cancelled = False
        if cancelled:
            raise AgentCancelled()

    # === 输入 Guardrail ===
    input_gr = get_input_guardrail().check(user_task)
    if not input_gr.passed:
        callbacks.on_status(f"[GUARDRAIL] 输入被拦截: {input_gr.rule} - {input_gr.detail}")
        return f"[GUARDRAIL BLOCKED] {input_gr.rule}: {input_gr.detail}"

    # === 模型选择（混合策略） ===
    mm = get_model_manager()
    actual_model_id = mm.get_model_for_task(task_type)
    model_cfg = mm.get_model_config(actual_model_id)
    import os
    api_key = os.environ.get(model_cfg.get("api_key_env", ""), "") or model_cfg.get("api_key_default", "")
    base_url = model_cfg["base_url"]
    model_name = model_cfg["model_id"]
    model_params = dict(model_cfg.get("default_params", get_v4_params()))

    if not api_key:
        callbacks.on_status("=" * 50)
        callbacks.on_status(f"[ERROR] API key not set for model '{actual_model_id}'")
        callbacks.on_status(f"  环境变量: {model_cfg.get('api_key_env', 'N/A')}")
        callbacks.on_status("=" * 50)
        return f"[ERROR] API key not set for model '{actual_model_id}'"

    client = OpenAI(api_key=api_key, base_url=base_url)

    # 构建 system prompt
    if system_prompt_override:
        system_prompt = system_prompt_override
    else:
        # 检测并注入 Skill
        system_prompt = detect_and_inject_skill(skill_task or user_task) if enable_skills else SYSTEM_PROMPT

        # 注入经验库 prompt（如果经验库有内容）
        try:
            from experience.experience_store import ExperienceStore
            from experience.experience_tools import get_experience_prompt_section
            exp_prompt = get_experience_prompt_section(ExperienceStore())
            if exp_prompt:
                system_prompt += "\n\n" + exp_prompt
        except Exception as e:
            print(f"  [WARN] 经验库初始化失败: {e}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": full_task},
    ]

    total_tool_calls = 0
    consecutive_failures = 0  # 连续工具调用失败计数
    collected_output: list[str] = []  # 累积所有用户可见输出
    session_start = datetime.now()

    # === 开始 Trace ===
    tracer = get_tracer()
    trace_id = tracer.start_trace(user_task[:200])

    callbacks.on_status("=" * 55)
    callbacks.on_status(f"  FOC-Assistant [Started]")
    callbacks.on_status(f"  Model: {model_name} ({model_cfg['display_name']})  |  Max iterations: {max_iterations}")
    callbacks.on_status(f"  Tools: {len(TOOLS)}  |  Skills: {len(SKILLS)}  |  Trace: {trace_id}")
    callbacks.on_status(f"  Time: {session_start.strftime('%Y-%m-%d %H:%M:%S')}")
    callbacks.on_status("=" * 55)
    callbacks.on_status(f"\n[Task]: {full_task[:300]}{'...' if len(full_task) > 300 else ''}\n")

    try:
        for iteration in range(1, max_iterations + 1):
            check_cancelled()
            callbacks.on_status(f"--- Round {iteration} ---")

            # 调用 LLM（使用动态模型配置 + Tracing）
            kwargs = dict(
                model=model_name,
                messages=messages,
                stream=STREAM_OUTPUT,
                extra_body={"thinking_mode": thinking_mode} if thinking_mode is not None else {},
                **model_params,
            )
            if enable_tools:
                kwargs["tools"] = TOOLS
                kwargs["tool_choice"] = "auto"

            with tracer.trace_llm_call(
                model=model_name,
                messages_count=len(messages),
                tools_count=len(TOOLS) if enable_tools else 0,
                thinking_mode=thinking_mode or get_thinking_mode(),
                task_type=task_type,
            ):
                response = client.chat.completions.create(**kwargs)
            check_cancelled()

            # --- 处理流式响应 ---
            if STREAM_OUTPUT:
                collected = {"content": "", "reasoning_content": "", "tool_calls": []}
                tool_call_buffer = {}

                for chunk in response:
                    check_cancelled()
                    delta = chunk.choices[0].delta

                    # V4 thinking 模式的推理过程（不打印，仅收集用于回传）
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        collected["reasoning_content"] += delta.reasoning_content

                    # 收集文本内容
                    if delta.content:
                        try:
                            callbacks.on_token(delta.content)
                        except Exception as e:
                            print(f"  [WARN] token 回调异常: {e}")
                        collected["content"] += delta.content

                    # 收集工具调用
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_call_buffer:
                                tool_call_buffer[idx] = {
                                    "id": tc_delta.id or "",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tc_buf = tool_call_buffer[idx]
                            if tc_delta.id:
                                tc_buf["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tc_buf["function"]["name"] = tc_delta.function.name
                                tc_buf["function"]["arguments"] += tc_delta.function.arguments or ""

                # 构建工具调用列表
                if tool_call_buffer:
                    collected["tool_calls"] = [
                        {
                            "id": buf["id"],
                            "type": "function",
                            "function": buf["function"],
                        }
                        for buf in tool_call_buffer.values()
                    ]

                msg_content = collected["content"] or None
                msg_reasoning = collected["reasoning_content"] or None
                msg_tool_calls = collected["tool_calls"] or None

                # 没有工具调用 → 任务结束
                if not msg_tool_calls:
                    if collected["content"]:
                        collected_output.append(collected["content"])
                        collected_output.append("\n")
                    elapsed = (datetime.now() - session_start).total_seconds()
                    full = "".join(collected_output)

                    # === 输出 Guardrail ===
                    output_gr = get_output_guardrail().check(full, user_task)
                    if not output_gr.passed:
                        full = f"[GUARDRAIL WARNING] {output_gr.rule}: {output_gr.detail}\n\n{full}"

                    tracer.end_trace(trace_id, output=full[:300], status="ok")
                    callbacks.on_complete(full, elapsed, total_tool_calls)
                    return full

            else:
                # --- 非流式响应 ---
                msg = response.choices[0].message
                msg_content = msg.content
                msg_tool_calls = msg.tool_calls
                msg_reasoning = getattr(msg, "reasoning_content", None) or None

                if msg_content:
                    try:
                        callbacks.on_token(msg_content)
                    except Exception:
                        pass

                if not msg_tool_calls:
                    if msg_content:
                        collected_output.append(msg_content)
                    elapsed = (datetime.now() - session_start).total_seconds()
                    full = "".join(collected_output)

                    # === 输出 Guardrail ===
                    output_gr = get_output_guardrail().check(full, user_task)
                    if not output_gr.passed:
                        full = f"[GUARDRAIL WARNING] {output_gr.rule}: {output_gr.detail}\n\n{full}"

                    tracer.end_trace(trace_id, output=full[:300], status="ok")
                    callbacks.on_complete(full, elapsed, total_tool_calls)
                    return full

            # --- 执行工具调用 ---
            assistant_msg = {
                "role": "assistant",
                "content": msg_content,
                "tool_calls": msg_tool_calls,
            }
            if msg_reasoning:
                assistant_msg["reasoning_content"] = msg_reasoning
            messages.append(assistant_msg)

            for tc in msg_tool_calls:
                check_cancelled()
                tool_name = tc["function"]["name"]
                try:
                    tool_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    tool_args = {}

                callbacks.on_tool_call(tool_name, tool_args)
                check_cancelled()

                with tracer.trace_tool_call(tool_name, tool_args):
                    result = execute_tool(tool_name, tool_args, danger_callback=callbacks.on_danger_confirm)
                total_tool_calls += 1

                # 错误恢复：跟踪连续失败
                is_failure = any(err in result.lower() for err in ["错误", "error", "失败", "failed", "拒绝", "未知工具"])
                if is_failure:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                # 连续失败降级：注入系统提示（最多触发 2 次，共 6 次失败后强制退出）
                if consecutive_failures >= 3 and consecutive_failures < 6:
                    messages.append({
                        "role": "system",
                        "content": (
                            "【错误恢复提示】你已经连续多次工具调用失败。请：\n"
                            "1. 停止重复调用同一个失败的工具\n"
                            "2. 尝试使用替代方案（如用 search_code 代替 find_files）\n"
                            "3. 如果确实无法完成，用 task_complete 报告已完成的部分和阻塞原因\n"
                            "4. 不要无限循环尝试"
                        ),
                    })
                    consecutive_failures = 0  # 重置，允许再试一轮

                # 硬限制：连续失败超过 6 次，强制终止
                if consecutive_failures >= 6:
                    callbacks.on_status("[ERROR] 连续工具调用失败过多，强制终止")
                    elapsed = (datetime.now() - session_start).total_seconds()
                    full = "".join(collected_output) or "[ERROR] 工具调用持续失败，任务无法完成。"
                    tracer.end_trace(trace_id, output=full[:300], status="error")
                    callbacks.on_complete(full, elapsed, total_tool_calls)
                    return full

                # 截断过长结果（保留摘要信息）
                if len(result) > 6000:
                    result = result[:6000] + "\n\n... (结果过长已截断)"

                # 显示结果摘要
                callbacks.on_tool_result(result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

                # task_complete early exit
                if tool_name == "task_complete":
                    elapsed = (datetime.now() - session_start).total_seconds()
                    full = "".join(collected_output).strip()
                    if not full:
                        full = str(tool_args.get("summary", "")).strip() or result

                    # === 输出 Guardrail ===
                    output_gr = get_output_guardrail().check(full, user_task)
                    if not output_gr.passed:
                        full = f"[GUARDRAIL WARNING] {output_gr.rule}: {output_gr.detail}\n\n{full}"

                    tracer.end_trace(trace_id, output=full[:300], status="ok")
                    callbacks.on_complete(full, elapsed, total_tool_calls)
                    return full
    except AgentCancelled:
        callbacks.on_status("[CANCELLED] 用户终止了当前任务")
        partial = "".join(collected_output).strip()
        tracer.end_trace(trace_id, output="cancelled", status="error")
        return "[CANCELLED] 当前任务已终止。" + (f"\n\n已产生的部分输出:\n{partial}" if partial else "")

    elapsed = (datetime.now() - session_start).total_seconds()
    callbacks.on_status(f"\n{'='*55}")
    callbacks.on_status(f"  [WARN] Max iterations reached ({max_iterations})  |  {elapsed:.1f}s")
    callbacks.on_status(f"{'='*55}")
    full = "".join(collected_output)
    tracer.end_trace(trace_id, output=full[:300], status="ok")
    return full


def main():
    """入口"""

    # 启动配置校验
    from config import validate_config
    config_warnings = validate_config()
    for w in config_warnings:
        print(f"  [CONFIG WARNING] {w}")

    # 启动时自动加载知识库索引
    kb = get_kb()
    if not kb.loaded:
        kb_stats = kb.build_index()
        print(f"  [KB] {kb_stats.split(chr(10))[0]}")
    else:
        print(f"  [KB] Knowledge base loaded: {len(kb.documents)} chunks")

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        print("FOC-Assistant —— PMSM 电机控制开发助手")
        print()
        print("可用命令:")
        print("  /skills  查看所有可用 Skill")
        print("  /help    查看帮助")
        print("  /kb      查看知识库状态")
        print()
        task = input("请输入任务: ").strip()
        if not task:
            print("未输入任务，退出。")
            return

    if task.lower() in ("/skills", "/s"):
        show_skills()
        return
    if task.lower() in ("/help", "/h", "/?"):
        show_help()
        return
    if task.lower() in ("/kb",):
        print(kb.list_documents())
        return

    agent_loop(task)


def show_skills():
    """展示所有可用的 Skill"""
    print("\n" + "=" * 55)
    print("  FOC-Assistant 可用 Skills")
    print("=" * 55)
    for sid, s in SKILLS.items():
        print(f"\n  [{s['name']}] ({sid})")
        print(f"  触发词: {', '.join(s['trigger'][:5])}")
        print(f"  说明: {s['prompt_addon'].strip().split(chr(10))[0].strip('# ')}")
    print()


def show_help():
    """帮助信息"""
    print("\n" + "=" * 55)
    print("  FOC-Assistant 帮助")
    print("=" * 55)
    print()
    print("  直接输入任务即可，Agent 会自动:")
    print("  1. 检测任务类型，激活对应的 Skill")
    print("  2. 调用工具完成分析/修改/编译等操作")
    print("  3. 任务完成后输出总结")
    print()
    print("  也可在命令行直接传参:")
    print("    python agent.py \"你的任务\"")
    print()
    mm = get_model_manager()
    print(f"  当前工具: {len(TOOLS)} 个")
    print(f"  当前 Skills: {len(SKILLS)} 个")
    print(f"  模型: {mm.active_model_id}")
    print()


if __name__ == "__main__":
    main()
