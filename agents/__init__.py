"""FOC-Assistant 多 Agent 协作系统

支持两种模式:
1. 显式 Handoff：主 Agent 调用 spawn_agent("code_analyzer", "任务") 明确指定子 Agent
2. 声明式 Handoff：主 Agent 调用 handoff_to_agent("任务")，由系统自动选择最合适的子 Agent

路由使用模糊语义匹配（字符 n-gram + 拼音），不依赖精确关键词。

扩展: 在 profiles.py 的 AGENT_PROFILES 中新增条目即可。
"""

import re
from difflib import SequenceMatcher
from agents.profiles import AGENT_PROFILES, get_agent_profile, list_agents


# ============================================================
# 模糊匹配工具函数
# ============================================================

def _char_ngrams(text: str, n: int = 2) -> set:
    """提取字符级 n-gram 集合。"""
    text = re.sub(r"\s+", "", text.lower())
    return {text[i:i + n] for i in range(max(len(text) - n + 1, 1))}


def _fuzzy_score(query: str, target: str) -> float:
    """计算两个字符串的模糊相似度 (0~1)。

    综合三种策略:
    1. SequenceMatcher（编辑距离）
    2. 字符 2-gram Jaccard 相似度
    3. 子串包含加分
    """
    q = query.lower().strip()
    t = target.lower().strip()

    if not q or not t:
        return 0.0

    # 策略 1: SequenceMatcher
    seq_score = SequenceMatcher(None, q, t).ratio()

    # 策略 2: 字符 2-gram Jaccard
    q_ngrams = _char_ngrams(q, 2)
    t_ngrams = _char_ngrams(t, 2)
    if q_ngrams and t_ngrams:
        intersection = q_ngrams & t_ngrams
        union = q_ngrams | t_ngrams
        jaccard_score = len(intersection) / len(union) if union else 0.0
    else:
        jaccard_score = 0.0

    # 策略 3: 子串包含加分
    contain_bonus = 0.0
    if t in q or q in t:
        contain_bonus = 0.3
    else:
        # 检查是否有 3 字以上的公共子串
        for length in range(min(len(q), len(t)), 2, -1):
            for i in range(len(q) - length + 1):
                sub = q[i:i + length]
                if sub in t:
                    contain_bonus = 0.2
                    break
            if contain_bonus > 0:
                break

    # 加权综合
    return 0.4 * seq_score + 0.3 * jaccard_score + 0.3 * contain_bonus


def _pinyin_fuzzy_score(query: str, target_pinyins: list[str]) -> float:
    """检查查询文本是否模糊匹配拼音列表（处理中文同音字）。"""
    # 简易拼音匹配：检查声母/韵母片段
    q_lower = query.lower().strip()
    best = 0.0
    for py in target_pinyins:
        # 直接匹配
        score = _fuzzy_score(q_lower, py)
        # 声母匹配（取前 2 个字符）
        if len(q_lower) >= 2 and len(py) >= 2:
            initial_score = 1.0 if q_lower[:2] == py[:2] else 0.0
            score = max(score, initial_score * 0.5)
        best = max(best, score)
    return best


# ============================================================
# Agent 语义画像（比关键词更丰富的描述）
# ============================================================

AGENT_SEMANTIC_PROFILES = {
    "code_analyzer": {
        "core_concepts": [
            "代码分析", "代码结构", "调用链", "函数逻辑", "中断分析", "数据流",
            "函数定义", "头文件", "源文件", "变量", "宏定义", "结构体",
            "main", "ISR", "interrupt", "function", "variable", "struct",
            "调用关系", "模块", "接口", "API", "寄存器", "底层驱动",
        ],
        "trigger_phrases": [
            "分析代码", "看代码", "代码怎么写的", "这个函数做什么",
            "调用关系", "数据流", "中断优先级", "代码结构",
            "帮我看看这个文件", "读一下源码", "找一下函数定义",
        ],
        "pinyin_hints": ["daima", "fenxi", "hanshu", "zhongduan", "diaoyong"],
        "weight": 1.0,
    },
    "waveform_analyzer": {
        "core_concepts": [
            "波形", "CSV", "阶跃响应", "纹波", "超调", "稳态误差",
            "观测器", "ESO", "扰动估计", "收敛", "精度", "示波器",
            "数据", "采样", "时间序列", "电流波形", "转速波形",
            "rise time", "overshoot", "ripple", "steady state",
        ],
        "trigger_phrases": [
            "分析波形", "看看这个数据", "CSV 分析", "阶跃响应怎么样",
            "纹波多大", "超调多少", "观测器精度", "波形分析",
            "eso 的输出", "看看实验数据",
        ],
        "pinyin_hints": ["boxing", "boshu", "csv", "guanceqi", "wenbo"],
        "weight": 1.0,
    },
    "controller_designer": {
        "core_concepts": [
            "控制器", "PI", "PID", "参数", "增益", "带宽", "极点", "零点",
            "Kp", "Ki", "Kd", "SMC", "滑模", "ADRC", "自抗扰", "ESO参数",
            "SVPWM", "MTPA", "弱磁", "电流环", "速度环", "转矩",
            "整定", "调参", "设计", "计算", "离散化",
            "controller", "gain", "bandwidth", "pole", "tuning",
        ],
        "trigger_phrases": [
            "计算参数", "帮我调参", "PI 参数怎么设", "控制器设计",
            "带宽多少合适", "Kp Ki 怎么算", "ESO 参数整定",
            "速度环怎么设计", "电流环带宽", "帮我算一下增益",
            "参数整定", "控制策略",
        ],
        "pinyin_hints": ["kongzhiqi", "canshu", "zengyi", "daikuan", "tiaocan"],
        "weight": 1.0,
    },
    "research_agent": {
        "core_concepts": [
            "论文", "文献", "搜索", "联网", "调研", "资料", "参考",
            "数据手册", "芯片手册", "datasheet", "manual", "手册",
            "GitHub", "开源", "最新", "研究", "学术", "期刊", "会议",
            "paper", "research", "survey", "reference",
        ],
        "trigger_phrases": [
            "帮我查一下", "搜索一下", "找找论文", "有没有相关资料",
            "联网搜搜", "最新研究", "看看别人怎么做的", "调研一下",
            "有没有参考", "帮我找资料", "查手册", "芯片手册",
            "帮我搜", "找一下资料",
        ],
        "pinyin_hints": ["lunwen", "wenxian", "sousuo", "lianwang", "ziliao"],
        "weight": 1.0,
    },
    "debug_helper": {
        "core_concepts": [
            "编译", "报错", "错误", "异常", "调试", "debug", "失败",
            "warning", "error", "排查", "修复", "bug", "崩溃",
            "链接错误", "未定义", "内存泄漏", "死锁", "溢出",
            "跑不了", "跑不起来", "不工作", "挂了", "死机",
            "compile", "build", "crash", "fault", "exception",
        ],
        "trigger_phrases": [
            "编译报错了", "有错误", "运行不了", "帮我调试",
            "这个 warning 是什么意思", "链接失败", "怎么修复",
            "程序崩溃了", "排查一下问题", "跑不起来", "不工作了",
            "程序挂了", "出问题了",
        ],
        "pinyin_hints": ["bianyi", "baocuo", "yichang", "tiaoshi", "xiufu"],
        "weight": 1.0,
    },
}


def _resolve_task_type(task: str) -> str:
    """根据任务描述推断最合适的子 Agent（模糊语义匹配）。

    匹配策略（按优先级）:
    1. 精确子串包含（最快、最准）
    2. 触发短语模糊匹配（SequenceMatcher + n-gram）
    3. 核心概念模糊匹配
    4. 拼音模糊匹配（兜底）
    """
    task_lower = task.lower().strip()

    if not task_lower:
        return ""

    scores = {}

    for agent_id, profile in AGENT_SEMANTIC_PROFILES.items():
        score = 0.0
        weight = profile.get("weight", 1.0)

        # --- 策略 1: 精确子串包含（高权重） ---
        for phrase in profile["trigger_phrases"]:
            if phrase.lower() in task_lower:
                score += 3.0
                break

        for concept in profile["core_concepts"]:
            if concept.lower() in task_lower:
                score += 1.5
                break  # 只计一次，避免重复加分

        # --- 策略 2: 触发短语模糊匹配 ---
        best_phrase_score = 0.0
        for phrase in profile["trigger_phrases"]:
            fs = _fuzzy_score(task_lower, phrase)
            best_phrase_score = max(best_phrase_score, fs)
        score += best_phrase_score * 2.0

        # --- 策略 3: 核心概念模糊匹配 ---
        concept_scores = []
        for concept in profile["core_concepts"]:
            fs = _fuzzy_score(task_lower, concept)
            concept_scores.append(fs)
        if concept_scores:
            # 取 top-3 平均
            concept_scores.sort(reverse=True)
            top_n = concept_scores[:3]
            score += (sum(top_n) / len(top_n)) * 1.5

        # --- 策略 4: 拼音模糊匹配 ---
        pinyin_hints = profile.get("pinyin_hints", [])
        if pinyin_hints:
            py_score = _pinyin_fuzzy_score(task_lower, pinyin_hints)
            score += py_score * 0.5

        scores[agent_id] = score * weight

    if not scores:
        return ""

    best_agent = max(scores, key=scores.get)
    best_score = scores[best_agent]

    # 设置最低阈值，避免误匹配
    if best_score < 0.8:
        return ""

    return best_agent


def handoff_to_agent(task: str, prefer_agent: str = "") -> str:
    """声明式 Handoff：自动选择最合适的子 Agent 执行任务。

    Args:
        task: 子任务描述
        prefer_agent: 可选，优先使用的 Agent ID（如果指定则跳过自动路由）

    Returns:
        str: 子 Agent 的执行结果
    """
    # 延迟导入避免循环依赖
    from tracing import get_tracer

    # 确定目标 Agent
    agent_id = prefer_agent or _resolve_task_type(task)
    if not agent_id:
        # 无法自动确定，列出所有 Agent 让主 Agent 的 LLM 选择
        return (
            "无法自动确定最适合的 Agent。请使用 spawn_agent 显式指定:\n"
            + list_agents()
        )

    profile = get_agent_profile(agent_id)
    if not profile:
        return f"未知 Agent: {agent_id}\n可用: {', '.join(AGENT_PROFILES.keys())}"

    # 记录 Handoff trace
    get_tracer().trace_handoff(
        from_agent="FOC-Assistant",
        to_agent=agent_id,
        task=task,
    )

    return _execute_sub_agent(agent_id, profile, task)


def spawn_agent(agent_id: str, task: str) -> str:
    """显式 Handoff：指定 Agent ID 执行子任务。

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

    # 记录 Handoff trace
    from tracing import get_tracer
    get_tracer().trace_handoff(
        from_agent="FOC-Assistant",
        to_agent=agent_id,
        task=task,
    )

    return _execute_sub_agent(agent_id, profile, task)


def _execute_sub_agent(agent_id: str, profile: dict, task: str) -> str:
    """执行子 Agent 的核心逻辑（spawn_agent 和 handoff_to_agent 共用）。"""
    # 延迟导入避免循环依赖
    from agent import AgentCallbacks
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
        pass

    callbacks = AgentCallbacks(
        on_token=on_token,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )

    try:
        result = _run_sub_agent(
            task=task,
            system_prompt=specialized_prompt,
            tools=filtered_tools,
            thinking_mode=profile.get("thinking_mode"),
            max_iterations=profile.get("max_iterations", 15),
            callbacks=callbacks,
            agent_id=agent_id,
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
    agent_id: str = "sub",
) -> str:
    """运行子 Agent 的核心循环，集成 Tracing。"""
    import json
    from datetime import datetime
    from openai import OpenAI
    from config import API_KEY, BASE_URL, MODEL, V4_PARAMS, STREAM_OUTPUT, get_model_manager
    from tools import execute_tool
    from tracing import get_tracer

    # 使用混合策略选择模型（子 Agent 通常需要工具调用能力）
    mm = get_model_manager()
    model_cfg = mm.get_model_config(mm.get_model_for_task("tool"))
    api_key = os.environ.get(model_cfg.get("api_key_env", ""), "") or model_cfg.get("api_key_default", "")
    base_url = model_cfg["base_url"]
    model_id = model_cfg["model_id"]

    if not api_key:
        return "[ERROR] API key not set"

    client = OpenAI(api_key=api_key, base_url=base_url)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    collected: list[str] = []

    for iteration in range(1, max_iterations + 1):
        kwargs = dict(
            model=model_id,
            messages=messages,
            stream=STREAM_OUTPUT,
            extra_body={"thinking_mode": thinking_mode} if thinking_mode else {},
            **(model_cfg.get("default_params", V4_PARAMS)),
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        with get_tracer().trace_llm_call(
            model=model_id,
            messages_count=len(messages),
            tools_count=len(tools) if tools else 0,
            thinking_mode=thinking_mode or "",
            task_type="sub_agent",
        ):
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

            with get_tracer().trace_tool_call(tool_name, tool_args):
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


# 需要在 _run_sub_agent 中导入 os
import os
