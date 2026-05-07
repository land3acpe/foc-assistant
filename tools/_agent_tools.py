"""Agent/模型工具：reflect, scheduler_status,
   spawn_agent, list_agents, handoff_to_agent, switch_model, list_models, trace_summary"""

import json

def _reflect_tool(args: dict) -> str:
    """触发自我反思（简化版，不调用 LLM，基于规则检查）"""
    task_summary = args.get("task_summary", "")

    checks = [
        "检查: 回答是否完整覆盖了用户需求",
        "检查: 工具调用是否有失败被忽略",
        "检查: 代码输出是否可编译运行（如适用）",
        "检查: 是否给出了明确的结论/结果（不只是计划）",
        "检查: 文件操作是否已验证文件存在（如适用）",
    ]

    return (
        f"自我反思检查清单:\n" +
        "\n".join(f"  {c}" for c in checks) +
        f"\n\n任务摘要: {task_summary}\n"
        f"提示: 如需深度反思，请使用 graph_agent 的 reflect 节点（自动触发）。"
    )


def _scheduler_status(args: dict) -> str:
    """查看调度器状态"""
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        return sched.get_status()
    except Exception as e:
        return f"调度器状态获取失败: {e}"


def _spawn_agent(args: dict) -> str:
    """启动专业子 Agent"""
    agent_id = args.get("agent_id", "")
    task = args.get("task", "")
    if not agent_id or not task:
        return "错误: 缺少 agent_id 或 task 参数"
    from agents import spawn_agent
    return spawn_agent(agent_id, task)


def _list_agents(args: dict) -> str:
    """列出可用的专业 Agent"""
    from agents import list_agents
    return list_agents()


def _handoff_to_agent(args: dict) -> str:
    """声明式 Handoff"""
    task = args.get("task", "")
    prefer_agent = args.get("prefer_agent", "")
    if not task:
        return "错误: 缺少 task 参数"
    from agents import handoff_to_agent
    return handoff_to_agent(task, prefer_agent)


def _switch_model(args: dict) -> str:
    """切换模型"""
    model_id = args.get("model_id", "")
    if not model_id:
        return "错误: 缺少 model_id 参数"
    from config import get_model_manager
    return get_model_manager().switch_model(model_id)


def _list_models(args: dict) -> str:
    """列出可用模型"""
    from config import get_model_manager
    return get_model_manager().list_models()


def _trace_summary(args: dict) -> str:
    """查看 trace 摘要"""
    from tracing import get_tracer
    return get_tracer().get_summary()
