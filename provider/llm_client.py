"""llm_client.py — 统一 LLM 调用客户端

从 OpenHanako 的 core/llm-client.js 移植。
封装 OpenAI / Anthropic / DeepSeek 多 provider 调用，
集成 deepseek_compat 适配层，支持同步/异步/流式。
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Iterator, Optional

import httpx
from openai import AsyncOpenAI, OpenAI

from provider import deepseek_compat


# ── 常量 ──

DEFAULT_TIMEOUT_S = 60
DEFAULT_MAX_TOKENS = 512
SLOW_THRESHOLD_S = 15


# ── 数据类 ──

@dataclass
class LLMUsage:
    """Token 用量"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class LLMResult:
    """LLM 调用结果"""
    text: str
    usage: Optional[LLMUsage] = None
    reasoning_content: str = ""
    raw_response: dict = field(default_factory=dict)


@dataclass
class LLMConfig:
    """LLM 调用配置"""
    base_url: str
    api_key: str
    model_id: str
    api: str = "openai-completions"  # openai-completions | anthropic-messages
    provider: str = "custom"
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: Optional[float] = None
    timeout: int = DEFAULT_TIMEOUT_S
    reasoning: bool = False
    quirks: list = field(default_factory=list)
    max_output: Optional[int] = None


# ── 内容转换 ──

def _normalize_text(content) -> str:
    """从各种 content 格式提取纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _strip_think_tags(text: str) -> str:
    """清除 <think> 标签"""
    return re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()


# ── Usage 解析 ──

def _parse_usage(data: dict) -> Optional[LLMUsage]:
    """解析 usage 字段"""
    raw = data.get("usage")
    if not raw or not isinstance(raw, dict):
        return None
    usage = LLMUsage(
        prompt_tokens=raw.get("prompt_tokens", 0),
        completion_tokens=raw.get("completion_tokens", 0),
        total_tokens=raw.get("total_tokens", 0),
    )
    # DeepSeek reasoning tokens
    details = raw.get("completion_tokens_details") or {}
    usage.reasoning_tokens = details.get("reasoning_tokens", 0)
    return usage


# ── 模型配置转换 ──

def _config_to_model_dict(cfg: LLMConfig) -> dict:
    """LLMConfig → deepseek_compat 所需的 model dict"""
    return {
        "id": cfg.model_id,
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "api": cfg.api,
        "reasoning": cfg.reasoning,
        "quirks": cfg.quirks,
        "maxTokens": cfg.max_output,
    }


# ── 同步客户端 ──

class LLMClient:
    """统一 LLM 调用客户端（同步）"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
        )
        self._model_dict = _config_to_model_dict(config)

    def call(
        self,
        system_prompt: str = "",
        messages: list[dict] = None,
        tools: list[dict] = None,
        max_tokens: int = None,
        temperature: float = None,
        reasoning_level: str = "",
        mode: str = "chat",
        stream: bool = False,
    ) -> LLMResult:
        """同步调用 LLM"""
        messages = messages or []
        max_tokens = max_tokens or self.config.max_tokens
        temperature = temperature if temperature is not None else self.config.temperature

        # 构建 payload
        payload = self._build_payload(system_prompt, messages, tools, max_tokens, temperature)

        # DeepSeek 适配
        if deepseek_compat.matches(self._model_dict):
            payload = deepseek_compat.apply(payload, self._model_dict, {
                "mode": mode,
                "reasoning_level": reasoning_level,
            })
            payload = deepseek_compat.resolve_output_budget(payload, self._model_dict)

        # 注入输出契约
        patches = deepseek_compat.get_provider_prompt_patches(self._model_dict, {
            "reasoning_level": reasoning_level,
        })
        if patches:
            for msg in payload["messages"]:
                if msg.get("role") == "system":
                    msg["content"] += "\n\n" + "\n\n".join(patches)
                    break

        # 发送请求
        if stream:
            return self._call_stream(payload)
        return self._call_sync(payload)

    def _build_payload(
        self, system_prompt: str, messages: list[dict],
        tools: list[dict], max_tokens: int, temperature: float,
    ) -> dict:
        """构建请求 payload"""
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        payload = {
            "model": self.config.model_id,
            "messages": all_messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
        return payload

    def _call_sync(self, payload: dict) -> LLMResult:
        """同步非流式调用"""
        start = time.time()
        try:
            resp = self._client.chat.completions.create(**payload)
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}") from e

        elapsed = time.time() - start
        if elapsed > SLOW_THRESHOLD_S:
            print(f"  [LLM] Slow response: {elapsed:.1f}s, model={self.config.model_id}")

        choice = resp.choices[0] if resp.choices else None
        message = choice.message if choice else None

        text = ""
        reasoning_content = ""
        if message:
            text = message.content or ""
            # DeepSeek reasoning_content
            if hasattr(message, "reasoning_content") and message.reasoning_content:
                reasoning_content = message.reasoning_content
            text = _strip_think_tags(text)

        usage = None
        if resp.usage:
            usage = LLMUsage(
                prompt_tokens=resp.usage.prompt_tokens or 0,
                completion_tokens=resp.usage.completion_tokens or 0,
                total_tokens=resp.usage.total_tokens or 0,
            )
            details = getattr(resp.usage, "completion_tokens_details", None)
            if details:
                usage.reasoning_tokens = getattr(details, "reasoning_tokens", 0)

        return LLMResult(
            text=text,
            usage=usage,
            reasoning_content=reasoning_content,
            raw_response=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )

    def _call_stream(self, payload: dict) -> Iterator[str]:
        """同步流式调用，yield token"""
        payload["stream"] = True
        with self._client.chat.completions.create(**payload) as stream:
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content

    def close(self):
        """关闭客户端"""
        self._client.close()


# ── 异步客户端 ──

class AsyncLLMClient:
    """统一 LLM 调用客户端（异步，供 subagent 使用）"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout,
        )
        self._model_dict = _config_to_model_dict(config)

    async def call(
        self,
        system_prompt: str = "",
        messages: list[dict] = None,
        tools: list[dict] = None,
        max_tokens: int = None,
        temperature: float = None,
        reasoning_level: str = "",
        mode: str = "chat",
    ) -> LLMResult:
        """异步调用 LLM"""
        messages = messages or []
        max_tokens = max_tokens or self.config.max_tokens
        temperature = temperature if temperature is not None else self.config.temperature

        payload = self._build_payload(system_prompt, messages, tools, max_tokens, temperature)

        if deepseek_compat.matches(self._model_dict):
            payload = deepseek_compat.apply(payload, self._model_dict, {
                "mode": mode,
                "reasoning_level": reasoning_level,
            })
            payload = deepseek_compat.resolve_output_budget(payload, self._model_dict)

        patches = deepseek_compat.get_provider_prompt_patches(self._model_dict, {
            "reasoning_level": reasoning_level,
        })
        if patches:
            for msg in payload["messages"]:
                if msg.get("role") == "system":
                    msg["content"] += "\n\n" + "\n\n".join(patches)
                    break

        return await self._call_async(payload)

    def _build_payload(
        self, system_prompt: str, messages: list[dict],
        tools: list[dict], max_tokens: int, temperature: float,
    ) -> dict:
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        payload = {
            "model": self.config.model_id,
            "messages": all_messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
        return payload

    async def _call_async(self, payload: dict) -> LLMResult:
        """异步非流式调用"""
        start = time.time()
        try:
            resp = await self._client.chat.completions.create(**payload)
        except Exception as e:
            raise RuntimeError(f"Async LLM call failed: {e}") from e

        elapsed = time.time() - start
        if elapsed > SLOW_THRESHOLD_S:
            print(f"  [LLM] Slow async response: {elapsed:.1f}s, model={self.config.model_id}")

        choice = resp.choices[0] if resp.choices else None
        message = choice.message if choice else None

        text = ""
        reasoning_content = ""
        if message:
            text = message.content or ""
            if hasattr(message, "reasoning_content") and message.reasoning_content:
                reasoning_content = message.reasoning_content
            text = _strip_think_tags(text)

        usage = None
        if resp.usage:
            usage = LLMUsage(
                prompt_tokens=resp.usage.prompt_tokens or 0,
                completion_tokens=resp.usage.completion_tokens or 0,
                total_tokens=resp.usage.total_tokens or 0,
            )
            details = getattr(resp.usage, "completion_tokens_details", None)
            if details:
                usage.reasoning_tokens = getattr(details, "reasoning_tokens", 0)

        return LLMResult(
            text=text,
            usage=usage,
            reasoning_content=reasoning_content,
            raw_response=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )

    async def call_text(
        self, system_prompt: str, user_prompt: str, tools: list[str] = None,
    ) -> str:
        """简化的文本调用接口（供 subagent executor 使用）"""
        messages = [{"role": "user", "content": user_prompt}]
        tool_defs = None
        if tools:
            tool_defs = [{"type": "function", "function": {"name": t}} for t in tools]
        result = await self.call(
            system_prompt=system_prompt,
            messages=messages,
            tools=tool_defs,
            mode="utility",
        )
        return result.text

    async def close(self):
        """关闭客户端"""
        await self._client.close()


# ── 工厂函数 ──

def create_llm_client(config: LLMConfig) -> LLMClient:
    """创建同步 LLM 客户端"""
    return LLMClient(config)


def create_async_llm_client(config: LLMConfig) -> AsyncLLMClient:
    """创建异步 LLM 客户端"""
    return AsyncLLMClient(config)


def config_from_model_registry(model_id: str, model_cfg: dict, api_key: str) -> LLMConfig:
    """从 MODEL_REGISTRY 配置创建 LLMConfig"""
    return LLMConfig(
        base_url=model_cfg["base_url"],
        api_key=api_key,
        model_id=model_cfg["model_id"],
        api="openai-completions",
        provider="deepseek" if "deepseek" in model_id else "custom",
        max_tokens=model_cfg.get("default_params", {}).get("max_tokens", DEFAULT_MAX_TOKENS),
        temperature=model_cfg.get("default_params", {}).get("temperature"),
        reasoning=model_cfg.get("supports_thinking", False),
        quirks=["enable_thinking"] if model_cfg.get("thinking_mode") == "thinking" else [],
        max_output=model_cfg.get("default_params", {}).get("max_tokens"),
    )
