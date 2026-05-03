"""Intent routing for FOC-Assistant.

The router is deliberately deterministic: it keeps chat/status messages away
from the expensive research workflow, and sends file-producing requests to the
execution path where validators can enforce completion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    CHAT = "chat"
    STATUS = "status"
    META = "meta"
    QA = "qa"
    RESEARCH = "research"
    EXECUTION = "execution"


@dataclass(frozen=True)
class RouteDecision:
    intent: Intent
    confidence: float
    reason: str
    needs_semantic: bool = False


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def route_intent(text: str) -> RouteDecision:
    raw = text.strip()
    compact = _compact(raw)
    lower = raw.lower()

    if not raw:
        return RouteDecision(Intent.CHAT, 1.0, "empty message")

    code_assistant_terms = (
        "电机", "pmsm", "foc", "svpwm", "pwm", "互补", "死区", "控制器",
        "观测器", "eso", "smc", "adrc", "stm32", "c2000", "dsp", "ccs",
        "matlab", "simulink", "代码", "函数", "编译", "报错", "调参",
        "波形", "csv", "论文", "文献", "数据手册", "datasheet", "github",
        "工程", "寄存器", "定时器", "adc", "hall", "编码器",
    )
    has_code_context = any(term in compact for term in code_assistant_terms)

    status_terms = (
        "/status", "状态", "进度", "干啥", "干嘛", "忙啥", "忙吗", "处理到哪",
        "处理完", "还在处理", "在做什么", "在干什么", "在干啥", "在干嘛",
        "what are you doing", "status", "progress",
    )
    if any(term in compact or term in lower for term in status_terms):
        return RouteDecision(Intent.STATUS, 0.98, "status/progress wording")

    meta_terms = (
        "什么大模型", "哪个大模型", "大模型驱动", "你用的模型", "你是什么模型",
        "你是谁", "who are you", "what model", "which model",
    )
    if any(term in compact or term in lower for term in meta_terms):
        return RouteDecision(Intent.META, 0.98, "assistant identity/model question")

    chat_terms = (
        "你好", "下午好", "上午好", "晚上好", "早上好", "嗨", "哈喽", "在吗",
        "有空", "闲吗", "开工", "聊聊", "谢谢", "辛苦", "哈哈", "测试一下",
        "hi", "hello", "thanks",
    )
    if not has_code_context and any(term in compact or term in lower for term in chat_terms):
        return RouteDecision(Intent.CHAT, 0.95, "short greeting")

    execution_actions = (
        "生成", "写出", "帮我写", "创建", "新建", "保存", "输出到", "放到",
        "修改", "改一下", "实现", "编写", "写入", "导出", "整理成文件",
        "补全", "修复", "新增", "建立", "下载", "更新", "改写", "替换",
    )
    execution_targets = (
        "代码", ".c", ".h", ".py", ".m", ".md", "文件", "目录", "工程",
        "桌面", "desktop", "focexamplecode", "保存到", "写到", "放进",
    )
    if has_code_context and any(w in compact for w in execution_actions) and any(w in compact for w in execution_targets):
        return RouteDecision(Intent.EXECUTION, 0.92, "file/code producing task")

    research_terms = (
        "研究一下", "查一下", "搜索", "联网", "学习", "调研", "资料", "文献",
        "论文", "数据手册", "datasheet", "manual", "github", "最新",
    )
    # If the user asks to search and then write code/files, execution wins.
    if has_code_context and any(w in compact for w in research_terms):
        return RouteDecision(Intent.RESEARCH, 0.82, "research/search wording")

    qa_terms = (
        "是什么", "为什么", "怎么", "如何", "解释", "说明", "总结", "对比",
        "区别", "原理", "公式", "参数", "讲一下", "介绍", "分析一下",
        "能不能", "可以吗", "是否",
    )
    professional_request_terms = (
        "看看", "看一下", "检查", "梳理", "结构", "调用关系", "定位", "排查",
        "优化", "建议", "评估", "读一下", "分析",
    )
    if has_code_context and (any(w in compact for w in qa_terms) or raw.endswith("?") or raw.endswith("？")):
        return RouteDecision(Intent.QA, 0.78, "question/analysis wording")
    if has_code_context and any(w in compact for w in professional_request_terms):
        return RouteDecision(Intent.QA, 0.76, "professional assistant wording")

    return RouteDecision(Intent.CHAT, 0.35, "ambiguous; ask semantic router", needs_semantic=True)
