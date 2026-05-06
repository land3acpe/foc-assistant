"""test_persona_builder.py — PersonaBuilder 单元测试"""

import os
import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persona.persona_builder import PersonaBuilder


@pytest.fixture
def builder():
    template_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "persona", "templates")
    return PersonaBuilder(template_dir=template_dir)


class TestPersonality:
    def test_base_personality(self, builder):
        text = builder.get_personality()
        assert "理性" in text or "冷静" in text or "工程师" in text

    def test_hardware_variant(self, builder):
        text = builder.get_personality("hardware")
        assert "硬件" in text or "功率" in text or "PCB" in text

    def test_algorithm_variant(self, builder):
        text = builder.get_personality("algorithm")
        assert "算法" in text or "FOC" in text or "控制" in text

    def test_test_variant(self, builder):
        text = builder.get_personality("test")
        assert "测试" in text or "验证" in text

    def test_variable_substitution(self, builder):
        # build_system_prompt 包含用户姓名注入
        prompt = builder.build_system_prompt(user_name="张三", agent_name="TestBot")
        assert "张三" in prompt


class TestSystemPrompt:
    def test_full_prompt(self, builder):
        prompt = builder.build_system_prompt(
            persona_type="base",
            user_name="用户",
            context={"project": "FOC", "mcu": "28377"},
            memory="测试记忆",
        )
        assert "用户" in prompt
        assert "FOC" in prompt
        assert "28377" in prompt
        assert "测试记忆" in prompt

    def test_english(self, builder):
        prompt = builder.build_system_prompt(is_zh=False)
        assert "User" in prompt or "collaborating" in prompt
