"""FOC-Assistant 多 Agent 协作系统

主 Agent 可以通过 spawn_agent 工具将子任务委派给专业子 Agent。
子 Agent 独立运行（独立的 LLM 调用、工具上下文），完成后将结果返回给主 Agent。

架构:
  主 Agent (FOC-Assistant)
    ├── spawn_agent("code_analyzer", "分析 main.c 中断逻辑")
    │     └── 独立 agent_loop，专用 prompt + 工具子集
    ├── spawn_agent("waveform_analyzer", "分析 eso.csv")
    │     └── 独立 agent_loop，CSV 分析专精
    └── spawn_agent("controller_designer", "计算 PI 参数")
          └── 独立 agent_loop，控制设计专精

扩展: 在 profiles.py 的 AGENT_PROFILES 中新增条目即可。
"""

from agents.profiles import AGENT_PROFILES, get_agent_profile, list_agents


def spawn_agent(agent_id: str, task: str) -> str:
    """启动一个专业子 Agent 执行子任务。

    Args:
        agent_id: Agent 标识，对应 AGENT_PROFILES 中的 key
        task: 子任务描述

    Returns:
        str: 子 Agent 的执行结果
    """
    profile = get_agent_profile(agent_id)
    if not profile:
        return (
            f"未知 Agent: {agent_id}\n"
            f"可用 Agent: {', '.join(AGENT_PROFILES.keys())}\n"
            f"使用 list_agents 查看详情。"
        )

    # 延迟导入避免循环依赖
    from agent import AgentCallbacks, agent_loop
    from config import SYSTEM_PROMPT

    # 构建专业 System Prompt
    specialized_prompt = SYSTEM_PROMPT + "\n\n" + profile["system_prompt"]

    # 获取工具子集
    allowed_tools = profile.get("allowed_tools", [])
    if allowed_tools:
        from tools import TOOLS
        filtered_tools = [t for t in TOOLS if t["function"]["name"] in allowed_tools]
    else:
        filtered_tools = None  # 全部工具

    # 收集输出
    outputs: list[str] = []

    def on_token(t: str):
        outputs.append(t)

    def on_tool_call(name: str, args: dict):
        print(f"  [SUB-AGENT:{agent_id}] {name}({str(args)[:100]})")

    def on_tool_result(r: str):
        pass  # 子 Agent 的工具结果不逐条推送

    callbacks = AgentCallbacks(
        on_token=on_token,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )

    try:
        # 使用自定义 agent_loop 调用，注入专业 prompt 和工具子集
        result = _run_sub_agent(
            task=task,
            system_prompt=specialized_prompt,
            tools=filtered_tools,
            thinking_mode=profile.get("thinking_mode"),
            max_iterations=profile.get("max_iterations", 15),
            callbacks=callbacks,
        )
        return result or "".join(outputs) or f"[{agent_id}] 子 Agent 未产生输出"
    except Exception as e:
        return f"[{agent_id}] 子 Agent 执行异常: {e}"


def _run_sub_agent(
    task: str,
    system_prompt: str,
    tools: list | None,
    thinking_mode: str | None,
    max_iterations: int,
    callbacks,
) -> str:
    """运行子 Agent 的核心循环（从 agent_loop 简化而来）"""
    import json
    from datetime import datetime
    from openai import OpenAI
    from config import API_KEY, BASE_URL, MODEL, V4_PARAMS, STREAM_OUTPUT
    from tools import execute_tool

    if not API_KEY:
        return "[ERROR] DEEPSEEK_API_KEY not set"

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    collected: list[str] = []

    for iteration in range(1, max_iterations + 1):
        kwargs = dict(
            model=MODEL,
            messages=messages,
            stream=STREAM_OUTPUT,
            extra_body={"thinking_mode": thinking_mode} if thinking_mode else {},
            **V4_PARAMS,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)

        if STREAM_OUTPUT:
            content = ""
            tool_calls_buf = {}
            for chunk in response:
                delta = chunk.choices[0].delta
                if delta.content:
                    callbacks.on_token(delta.content)
                    content += delta.content
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buf:
                            tool_calls_buf[idx] = {"id": tc.id or "", "function": {"name": "", "arguments": ""}}
                        if tc.id:
                            tool_calls_buf[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_buf[idx]["function"]["name"] = tc.function.name
                            tool_calls_buf[idx]["function"]["arguments"] += tc.function.arguments or ""

            msg_tool_calls = [
                {"id": b["id"], "type": "function", "function": b["function"]}
                for b in tool_calls_buf.values()
            ] if tool_calls_buf else None
            msg_content = content or None
        else:
            msg = response.choices[0].message
            msg_content = msg.content
            msg_tool_calls = msg.tool_calls

        if not msg_tool_calls:
            if msg_content:
                collected.append(msg_content)
            return "".join(collected)

        # 执行工具
        assistant_msg = {"role": "assistant", "content": msg_content, "tool_calls": msg_tool_calls}
        messages.append(assistant_msg)

        for tc in msg_tool_calls:
            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                tool_args = {}

            callbacks.on_tool_call(tool_name, tool_args)
            result = execute_tool(tool_name, tool_args)
            if len(result) > 4000:
                result = result[:4000] + "\n... (truncated)"

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

            if tool_name == "task_complete":
                return "".join(collected).strip() or str(tool_args.get("summary", ""))

    return "".join(collected)
