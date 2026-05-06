"""deepseek_compat.py — DeepSeek v4 协议适配层

从 OpenHanako 的以下文件移植：
- core/provider-compat/deepseek.js
- core/provider-prompt-patches.js
- shared/model-capabilities.js
- core/provider-compat/output-budget.js

处理 DeepSeek 特殊协议：
1. 思考模式控制：thinking: {type: "enabled" | "disabled"}
2. reasoning_effort 归一化：low/medium → high；xhigh → max
3. max_tokens 抬升：思考模式下需 ≥ 32768
4. reasoning_content 回传：工具调用轮次必须携带真实思考链
5. 输出契约 prompt：reasoning_content 只用于内部推理
6. Anthropic 协议双模：V4 支持两种 API 格式
"""

from typing import Optional


# 常量
DEEPSEEK_HIGH_THINKING_BUDGET = 32768
DEEPSEEK_HIGH_SAFE_MAX_TOKENS = 65536
DEEPSEEK_MAX_SAFE_MAX_TOKENS = 131072


def _get_lower(d: dict, *keys: str) -> str:
    """从 dict 中取第一个非空 key 的值并 lower()，不存在返回 ''"""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v.lower()
    return ""


def _positive_integer(value) -> Optional[int]:
    try:
        n = int(value)
        return n if n > 0 else None
    except (ValueError, TypeError):
        return None


# ── 模型判断 ──

def matches(model: dict) -> bool:
    """判断是否为 DeepSeek 模型"""
    if not isinstance(model, dict):
        return False
    return (
        _get_lower(model, "provider") == "deepseek"
        or "api.deepseek.com" in _get_lower(model, "base_url", "baseUrl")
    )


def is_known_thinking_model_id(model_id: str) -> bool:
    """判断是否为已知的思考模型"""
    mid = (model_id or "").lower()
    return mid == "deepseek-reasoner" or mid.startswith("deepseek-v4-")


def is_v4_model(model_id: str) -> bool:
    """判断是否为 DeepSeek V4 模型"""
    mid = (model_id or "").lower()
    return mid == "deepseek-v4" or mid.startswith("deepseek-v4-") or mid.startswith("deepseek-v4.")


def is_anthropic_profile(model: dict) -> bool:
    """判断是否为 DeepSeek V4 + Anthropic Messages 协议"""
    if get_reasoning_profile(model) == "deepseek-v4-anthropic":
        return True
    return _get_lower(model, "api") == "anthropic-messages" and is_v4_model(model.get("id", ""))


def is_deepseek_family_model(model: dict) -> bool:
    """判断是否为 DeepSeek 系列模型"""
    if not isinstance(model, dict):
        return False
    provider = _get_lower(model, "provider")
    base_url = _get_lower(model, "base_url", "baseUrl")
    model_text = " ".join(
        (model.get(k) or "").lower()
        for k in ("id", "name", "model", "modelId")
    )
    return (
        provider == "deepseek"
        or "deepseek" in provider
        or "api.deepseek.com" in base_url
        or "deepseek-ai/" in model_text
        or "deepseek/" in model_text
        or "deepseek-" in model_text
    )


def is_deepseek_reasoning_model(model: dict) -> bool:
    """判断是否为 DeepSeek 推理模型"""
    if not is_deepseek_family_model(model):
        return False
    if model.get("reasoning") is True:
        return True
    if get_thinking_format(model) or get_reasoning_profile(model):
        return True
    model_text = " ".join(
        (model.get(k) or "").lower()
        for k in ("id", "name", "model", "modelId")
    )
    return "deepseek-reasoner" in model_text or "deepseek-r1" in model_text or "deepseek-v4" in model_text


# ── 模型能力 ──

def get_thinking_format(model: dict) -> Optional[str]:
    """解析请求侧思考控制格式"""
    if not isinstance(model, dict):
        return None

    explicit = (model.get("compat") or {}).get("thinkingFormat")
    if isinstance(explicit, str) and explicit:
        return explicit.lower()

    quirks = model.get("quirks") or []
    if "enable_thinking" in quirks:
        return "qwen"

    api = _get_lower(model, "api")
    provider = _get_lower(model, "provider")
    model_id = _get_lower(model, "id")

    if model.get("reasoning") is True and api == "anthropic-messages":
        return "anthropic"

    if provider == "anthropic" and model.get("reasoning") is not False:
        return "anthropic"

    if matches(model) and (model.get("reasoning") is True or is_known_thinking_model_id(model_id)):
        return "deepseek"

    return None


def get_reasoning_profile(model: dict) -> Optional[str]:
    """解析推理 profile"""
    if not isinstance(model, dict):
        return None

    compat = model.get("compat") or {}
    explicit_raw = compat.get("reasoningProfile") or compat.get("thinkingProfile")
    explicit = explicit_raw.lower() if isinstance(explicit_raw, str) else ""
    if explicit:
        return explicit

    if not matches(model):
        return None

    if not is_v4_model(model.get("id", "")):
        return None

    api = _get_lower(model, "api")
    if api == "anthropic-messages":
        return "deepseek-v4-anthropic"
    if api in ("openai-completions", "openai-responses", ""):
        return "deepseek-v4-openai"

    return None


# ── 核心逻辑 ──

def _is_thinking_off(level: str) -> bool:
    return level in ("off", "none", "disabled")


def _reasoning_effort_for_level(level: str) -> Optional[str]:
    if not level:
        return None
    if level in ("xhigh", "max"):
        return "max"
    if level in ("minimal", "low", "medium", "high"):
        return "high"
    return None


def _normalize_effort_value(effort: str) -> Optional[str]:
    if effort in ("low", "medium"):
        return "high"
    if effort == "xhigh":
        return "max"
    return effort


def _should_use_thinking(payload: dict, model: dict, reasoning_level: str) -> bool:
    if payload.get("thinking", {}).get("type") == "disabled":
        return False
    if _is_thinking_off(reasoning_level):
        return False
    known_thinking = model.get("reasoning") is True or is_known_thinking_model_id(model.get("id", ""))
    return bool(
        payload.get("reasoning_effort")
        or (known_thinking and _reasoning_effort_for_level(reasoning_level))
        or known_thinking
    )


def _enable_thinking(payload: dict):
    payload["thinking"] = {"type": "enabled"}


def _disable_thinking(payload: dict):
    payload.pop("reasoning_effort", None)
    payload["thinking"] = {"type": "disabled"}
    if "messages" in payload:
        payload["messages"] = _strip_reasoning_content(payload["messages"])


def _disable_anthropic_thinking(payload: dict):
    payload.pop("reasoning_effort", None)
    payload.pop("output_config", None)
    payload["thinking"] = {"type": "disabled"}


def _normalize_max_token_field(payload: dict):
    if "max_completion_tokens" in payload and "max_tokens" not in payload:
        payload["max_tokens"] = payload.pop("max_completion_tokens")


def _ensure_thinking_token_budget(payload: dict, model: dict):
    current = _positive_integer(payload.get("max_tokens"))
    if current and current > DEEPSEEK_HIGH_THINKING_BUDGET:
        return

    model_limit = _positive_integer(model.get("maxTokens") or model.get("maxOutput"))
    desired = DEEPSEEK_MAX_SAFE_MAX_TOKENS if payload.get("reasoning_effort") == "max" else DEEPSEEK_HIGH_SAFE_MAX_TOKENS
    target = min(model_limit, desired) if model_limit else desired

    if target <= DEEPSEEK_HIGH_THINKING_BUDGET:
        _disable_thinking(payload)
        return

    payload["max_tokens"] = target


def _strip_reasoning_content(messages: list) -> list:
    """剥离 messages 中的 reasoning_content"""
    changed = False
    result = []
    for msg in messages:
        if isinstance(msg, dict) and "reasoning_content" in msg:
            changed = True
            result.append({k: v for k, v in msg.items() if k != "reasoning_content"})
        else:
            result.append(msg)
    return result if changed else messages


def _normalize_anthropic_thinking(thinking: dict) -> dict:
    if not isinstance(thinking, dict):
        return {"type": "enabled"}
    result = {"type": "enabled"}
    budget = _positive_integer(thinking.get("budget_tokens"))
    if budget:
        result["budget_tokens"] = budget
    return result


# ── reasoning_content 校验/恢复 ──

def extract_reasoning_from_content(message: dict) -> str:
    """从 message.content 恢复思考链原文"""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    # 路径 1：同模型，content 里有 thinking block
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking" and isinstance(block.get("thinking"), str):
            return block["thinking"]

    # 路径 2：跨模型降级，第一个 text block 即原文
    if content and isinstance(content[0], dict) and content[0].get("type") == "text":
        return content[0].get("text", "")

    return ""


def ensure_reasoning_content_for_tool_calls(messages: list) -> list:
    """保证带 tool_calls 的 assistant message 都有 reasoning_content"""
    changed = False
    result = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            result.append(msg)
            continue
        if not msg.get("tool_calls"):
            result.append(msg)
            continue
        if "reasoning_content" in msg and isinstance(msg["reasoning_content"], str):
            result.append(msg)
            continue
        # 尝试从 content 恢复
        recovered = extract_reasoning_from_content(msg)
        if not recovered:
            raise ValueError(
                "DeepSeek thinking mode reasoning_content is missing for tool_calls history. "
                "Compact this session or start a new session."
            )
        changed = True
        result.append({**msg, "reasoning_content": recovered})
    return result if changed else messages


def normalize_context_messages(messages: list, model: dict, options: dict = None) -> list:
    """Anthropic 模式下的 thinking 校验"""
    if not is_anthropic_profile(model):
        return messages
    options = options or {}
    if options.get("mode") == "utility" or _is_thinking_off(options.get("reasoning_level", "")):
        return messages

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        has_tool_call = any(
            isinstance(b, dict) and b.get("type") in ("toolCall", "tool_use", "function_call")
            for b in content
        )
        has_thinking = any(
            isinstance(b, dict) and b.get("type") == "thinking" and b.get("thinking", "").strip()
            for b in content
        )
        if has_tool_call and not has_thinking:
            raise ValueError(
                "DeepSeek Anthropic thinking mode history is missing non-empty thinking content for a tool call."
            )
    return messages


# ── 主入口 ──

def apply(payload: dict, model: dict, options: dict = None) -> dict:
    """应用 DeepSeek 适配到请求 payload"""
    if not isinstance(payload.get("messages"), list):
        return payload

    options = options or {}

    # Anthropic 协议路径
    if is_anthropic_profile(model):
        return _apply_anthropic(payload, model, options)

    # OpenAI 协议路径
    mode = options.get("mode", "chat")
    reasoning_level = options.get("reasoning_level", "")

    # max_completion_tokens → max_tokens
    if "max_completion_tokens" in payload:
        payload = {**payload}
        _normalize_max_token_field(payload)

    # 关闭思考
    if _is_thinking_off(reasoning_level) or payload.get("thinking", {}).get("type") == "disabled":
        _disable_thinking(payload)
        return payload

    # 判断是否开启思考
    if not _should_use_thinking(payload, model, reasoning_level):
        return payload

    # utility 模式关闭思考
    if mode == "utility":
        _disable_thinking(payload)
        return payload

    # 开启思考
    p = {**payload}
    effort = _reasoning_effort_for_level(reasoning_level)
    if effort:
        p["reasoning_effort"] = effort
    p["reasoning_effort"] = _normalize_effort_value(p.get("reasoning_effort"))
    _enable_thinking(p)
    _ensure_thinking_token_budget(p, model)

    if p.get("thinking", {}).get("type") == "disabled":
        return payload

    # 校验 tool_calls 历史
    p["messages"] = ensure_reasoning_content_for_tool_calls(p["messages"])
    return p


def _apply_anthropic(payload: dict, model: dict, options: dict) -> dict:
    """Anthropic 协议路径"""
    mode = options.get("mode", "chat")
    reasoning_level = options.get("reasoning_level", "")

    if _is_thinking_off(reasoning_level) or payload.get("thinking", {}).get("type") == "disabled":
        _disable_anthropic_thinking({**payload})
        return payload

    if not _should_use_thinking(payload, model, reasoning_level):
        return payload

    if mode == "utility":
        _disable_anthropic_thinking({**payload})
        return payload

    p = {**payload}
    p.pop("reasoning_effort", None)
    p["thinking"] = _normalize_anthropic_thinking(payload.get("thinking"))

    effort = _reasoning_effort_for_level(reasoning_level)
    if effort:
        p["output_config"] = {"effort": effort}
    else:
        p.pop("output_config", None)

    return p


# ── Provider Prompt Patches ──

def get_provider_prompt_patches(model: dict, options: dict = None) -> list[str]:
    """为 DeepSeek 推理模型注入输出契约 prompt"""
    options = options or {}
    if _is_thinking_off(options.get("reasoning_level", "")):
        return []
    if not is_deepseek_reasoning_model(model):
        return []

    locale = options.get("locale", "zh")
    if locale.startswith("zh"):
        return [
            "如果你使用的是 DeepSeek 模型，请遵守以下 DeepSeek 输出契约：\n"
            "reasoning_content / thinking 只用于内部推理草稿。\n"
            "任何需要展示给用户的回答、建议、代码、列表、问题、摘要、结论，都必须在思考结束后写入最终 assistant content。\n"
            "不要只输出 reasoning_content / thinking 就结束本轮回复。\n"
            "如果使用 <think> 标签，必须先关闭思考标签，再输出最终回答。"
        ]
    return [
        "If you are using a DeepSeek model, follow this DeepSeek output contract:\n"
        "reasoning_content / thinking is only for private reasoning scratch work.\n"
        "Any user-facing answer must be written into the final assistant content after thinking.\n"
        "Do not end a response with only reasoning_content / thinking."
    ]


# ── Output Budget ──

def resolve_output_budget(payload: dict, model: dict) -> dict:
    """输出预算归一化"""
    provider = _get_lower(model, "provider")
    base_url = _get_lower(model, "base_url", "baseUrl")

    # 官方 DeepSeek 端点保留隐式 SDK 默认值
    if provider == "deepseek" or "api.deepseek.com" in base_url:
        return payload

    # 其他端点：移除隐式 SDK 默认值
    sdk_implicit_cap = 32000
    model_limit = _positive_integer(model.get("maxTokens") or model.get("maxOutput"))
    if not model_limit:
        return payload

    for field in ("max_completion_tokens", "max_tokens", "max_output_tokens", "maxOutputTokens"):
        value = _positive_integer(payload.get(field))
        if value and value == min(model_limit, sdk_implicit_cap):
            payload = {**payload}
            del payload[field]

    return payload
