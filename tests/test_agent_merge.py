"""测试 agent_loop 合并后的子 Agent 行为"""

import pytest
from unittest.mock import patch, MagicMock


def test_agent_loop_accepts_system_prompt_override():
    """agent_loop 应接受 system_prompt_override 参数覆盖默认 SYSTEM_PROMPT"""
    from agent import agent_loop
    with patch("agent.get_model_manager") as mock_mm, \
         patch("agent.OpenAI") as mock_openai, \
         patch("agent.get_input_guardrail") as mock_ig, \
         patch("agent.get_output_guardrail") as mock_og, \
         patch("agent.STREAM_OUTPUT", False):
        mock_ig.return_value.check.return_value = MagicMock(passed=True)
        mock_og.return_value.check.return_value = MagicMock(passed=True)
        mock_mm.return_value.get_model_for_task.return_value = "test"
        mock_mm.return_value.get_model_config.return_value = {
            "display_name": "Test",
            "base_url": "http://test",
            "api_key_env": "TEST_KEY",
            "api_key_default": "test-key",
            "model_id": "test",
            "default_params": {},
        }
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "done"
        mock_response.choices[0].message.tool_calls = None
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            result = agent_loop(
                "test task",
                system_prompt_override="CUSTOM SYSTEM PROMPT",
                enable_skills=False,
                enable_tools=False,
            )
    assert "done" in result


def test_execute_sub_agent_calls_agent_loop():
    """_execute_sub_agent 应调用 agent_loop 而非 _run_sub_agent"""
    from agents import _execute_sub_agent
    from agents.profiles import AGENT_PROFILES

    profile = AGENT_PROFILES.get("code_analyzer")
    if profile is None:
        pytest.skip("code_analyzer profile not found")

    with patch("agent.agent_loop") as mock_loop:
        mock_loop.return_value = "sub agent result"
        result = _execute_sub_agent("code_analyzer", profile, "分析这个文件")

    assert result == "sub agent result"
    mock_loop.assert_called_once()
    call_kwargs = mock_loop.call_args
    # 验证 system_prompt_override 被使用
    kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
    assert "system_prompt_override" in kwargs
