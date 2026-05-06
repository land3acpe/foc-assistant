"""test_deepseek_compat.py — DeepSeek 适配层单元测试"""

import os
import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from provider.deepseek_compat import (
    matches, is_v4_model, is_anthropic_profile,
    is_deepseek_family_model, is_deepseek_reasoning_model,
    get_thinking_format, get_reasoning_profile,
    apply, ensure_reasoning_content_for_tool_calls,
    normalize_context_messages, resolve_output_budget,
    get_provider_prompt_patches,
)


class TestModelDetection:
    def test_matches_deepseek_provider(self):
        assert matches({"provider": "deepseek"}) is True

    def test_matches_deepseek_url(self):
        assert matches({"base_url": "https://api.deepseek.com/v1"}) is True

    def test_not_matches(self):
        assert matches({"provider": "openai"}) is False

    def test_is_v4(self):
        assert is_v4_model("deepseek-v4-pro") is True
        assert is_v4_model("deepseek-v4-flash") is True
        assert is_v4_model("deepseek-reasoner") is False

    def test_is_anthropic_profile(self):
        model = {"id": "deepseek-v4-pro", "api": "anthropic-messages", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1"}
        assert is_anthropic_profile(model) is True

    def test_is_deepseek_family(self):
        assert is_deepseek_family_model({"id": "deepseek-v4-pro", "provider": "deepseek"}) is True
        assert is_deepseek_family_model({"id": "gpt-4o", "provider": "openai"}) is False

    def test_is_reasoning_model(self):
        model = {"id": "deepseek-v4-pro", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1"}
        assert is_deepseek_reasoning_model(model) is True


class TestThinkingFormat:
    def test_deepseek_format(self):
        model = {"id": "deepseek-v4-pro", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1", "reasoning": True}
        assert get_thinking_format(model) == "deepseek"

    def test_no_format(self):
        assert get_thinking_format({"id": "gpt-4o", "provider": "openai"}) is None


class TestApply:
    def _make_payload(self):
        return {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": "test"},
                {"role": "user", "content": "hello"},
            ],
            "max_tokens": 4096,
        }

    def _make_model(self):
        return {
            "id": "deepseek-v4-pro",
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "reasoning": True,
        }

    def test_apply_openai_thinking(self):
        payload = self._make_payload()
        model = self._make_model()
        result = apply(payload, model, {"mode": "chat", "reasoning_level": "high"})
        assert result.get("thinking", {}).get("type") == "enabled"
        assert result.get("reasoning_effort") == "high"

    def test_apply_disable_thinking(self):
        payload = self._make_payload()
        model = self._make_model()
        result = apply(payload, model, {"mode": "chat", "reasoning_level": "off"})
        assert result.get("thinking", {}).get("type") == "disabled"

    def test_apply_utility_disables_thinking(self):
        payload = self._make_payload()
        model = self._make_model()
        result = apply(payload, model, {"mode": "utility", "reasoning_level": "high"})
        assert result.get("thinking", {}).get("type") == "disabled"

    def test_apply_max_effort(self):
        payload = self._make_payload()
        model = self._make_model()
        result = apply(payload, model, {"mode": "chat", "reasoning_level": "xhigh"})
        assert result.get("reasoning_effort") == "max"


class TestReasoningContent:
    def test_ensure_reasoning_content(self):
        messages = [
            {"role": "assistant", "content": "thinking here", "tool_calls": [{"id": "1"}]},
        ]
        result = ensure_reasoning_content_for_tool_calls(messages)
        assert result[0]["reasoning_content"] == "thinking here"

    def test_ensure_reasoning_content_from_content_blocks(self):
        messages = [
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "deep thought"}], "tool_calls": [{"id": "1"}]},
        ]
        result = ensure_reasoning_content_for_tool_calls(messages)
        assert result[0]["reasoning_content"] == "deep thought"

    def test_missing_raises(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        ]
        with pytest.raises(ValueError, match="reasoning_content is missing"):
            ensure_reasoning_content_for_tool_calls(messages)


class TestProviderPromptPatches:
    def test_patches_for_reasoning(self):
        model = {"id": "deepseek-v4-pro", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1", "reasoning": True}
        patches = get_provider_prompt_patches(model)
        assert len(patches) == 1
        assert "reasoning_content" in patches[0]

    def test_no_patches_for_non_reasoning(self):
        patches = get_provider_prompt_patches({"id": "gpt-4o", "provider": "openai"})
        assert len(patches) == 0

    def test_no_patches_when_thinking_off(self):
        model = {"id": "deepseek-v4-pro", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1", "reasoning": True}
        patches = get_provider_prompt_patches(model, {"reasoning_level": "off"})
        assert len(patches) == 0


class TestOutputBudget:
    def test_deepseek_preserves(self):
        payload = {"max_tokens": 4096}
        model = {"provider": "deepseek", "base_url": "https://api.deepseek.com/v1"}
        result = resolve_output_budget(payload, model)
        assert result["max_tokens"] == 4096
