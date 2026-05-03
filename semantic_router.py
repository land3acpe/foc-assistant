"""LLM-backed semantic router for ambiguous messages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openai import OpenAI

from config import API_KEY, BASE_URL, MODEL
from router import Intent, RouteDecision


@dataclass(frozen=True)
class SemanticRoute:
    intent: Intent
    confidence: float
    reason: str


ROUTER_SYSTEM_PROMPT = """你是 FOC-Assistant 的意图路由器，只输出 JSON，不要输出解释。

可选 intent:
- chat: 普通聊天、寒暄、玩笑、闲聊、情绪陪伴、和工程无关的问题
- status: 用户问机器人在干什么、进度、是否忙、是否处理完
- meta: 用户问你是谁、用什么模型、能力边界
- qa: 用户提出电机/FOC/代码/工程/论文/芯片等专业问题，希望解释、分析、判断、排查
- research: 用户要求查资料、联网搜索、找论文、找数据手册、学习/调研某主题
- execution: 用户要求写代码、生成文件、修改工程、保存到目录、编译、运行命令、完成具体落地任务

重要原则:
1. 默认普通聊天，不要因为出现“代码”二字就进入专业模式。
2. 只有用户真的在请求工程/代码/电机/资料任务时，才进入 qa/research/execution。
3. 如果用户要求产出文件、修改代码、生成工程，选择 execution。
4. 如果用户只是寒暄，比如“今天适合写代码吗”，选择 chat。

输出 JSON 格式:
{"intent":"chat|status|meta|qa|research|execution","confidence":0.0到1.0,"reason":"一句话原因"}
"""


def semantic_route(text: str) -> RouteDecision:
    """Classify ambiguous text with the configured model.

    The call intentionally exposes no tools and does not inject domain skills.
    If anything fails, fall back to chat-first behavior.
    """
    if not API_KEY:
        return RouteDecision(Intent.CHAT, 0.2, "semantic router unavailable: missing API key")

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": text[:1000]},
            ],
            temperature=0,
            top_p=1,
            max_tokens=200,
            stream=False,
        )
        content = response.choices[0].message.content or ""
        data = _parse_json(content)
        intent = Intent(data.get("intent", "chat"))
        confidence = float(data.get("confidence", 0.5))
        reason = str(data.get("reason", "semantic route"))
        return RouteDecision(intent, max(0.0, min(confidence, 1.0)), reason)
    except Exception as e:
        return RouteDecision(Intent.CHAT, 0.2, f"semantic router fallback: {e}")


def _parse_json(content: str) -> dict:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))

