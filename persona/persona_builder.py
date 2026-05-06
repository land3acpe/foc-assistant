"""persona_builder.py — 人格模板渲染器

从 OpenHanako 的 core/agent.js 的 personality/buildSystemPrompt 部分移植。
支持模板变量替换和多模板组合。
"""

import os
from typing import Optional


class PersonaBuilder:
    """人格模板渲染器"""

    def __init__(self, template_dir: str = None):
        self.template_dir = template_dir or os.path.join(os.path.dirname(__file__), "templates")
        self._templates: dict[str, str] = {}

    def _load_template(self, name: str) -> str:
        """加载模板文件"""
        if name not in self._templates:
            path = os.path.join(self.template_dir, name)
            with open(path, "r", encoding="utf-8") as f:
                self._templates[name] = f.read()
        return self._templates[name]

    def _fill(self, text: str, user_name: str, agent_name: str, agent_id: str = "") -> str:
        """模板变量替换"""
        return (
            text
            .replace("{{user_name}}", user_name)
            .replace("{{agent_name}}", agent_name)
            .replace("{{agent_id}}", agent_id)
        )

    def get_personality(
        self,
        persona_type: str = "base",
        user_name: str = "用户",
        agent_name: str = "FOC-Engineer",
        agent_id: str = "",
    ) -> str:
        """获取纯人格 prompt"""
        # 加载基础人格
        base = self._load_template("engineer_base.md")

        # 加载变体人格（如果指定）
        variant_file = f"engineer_{persona_type}.md"
        variant_path = os.path.join(self.template_dir, variant_file)
        variant = ""
        if os.path.exists(variant_path) and persona_type != "base":
            variant = self._load_template(variant_file)

        # 组合
        parts = [base]
        if variant:
            parts.append(f"\n\n{variant}")

        return self._fill("\n".join(parts), user_name, agent_name, agent_id)

    def build_system_prompt(
        self,
        persona_type: str = "base",
        user_name: str = "用户",
        agent_name: str = "FOC-Engineer",
        agent_id: str = "",
        context: dict = None,
        memory: str = None,
        experience: str = None,
        tools_desc: str = None,
        is_zh: bool = True,
    ) -> str:
        """构建完整的 system prompt"""
        parts = []

        # 1. 身份
        parts.append(self.get_personality(persona_type, user_name, agent_name, agent_id))

        # 2. 用户档案
        if user_name:
            if is_zh:
                parts.append(f"\n\n## 用户\n\n你正在与 {user_name} 协作。")
            else:
                parts.append(f"\n\n## User\n\nYou are collaborating with {user_name}.")

        # 3. 工作上下文
        if context:
            parts.append(self._render_context(context, is_zh))

        # 4. 经验库
        if experience:
            header = "## 历史经验" if is_zh else "## Historical Experience"
            parts.append(f"\n\n{header}\n\n{experience}")

        # 5. 记忆
        if memory:
            header = "## 记忆" if is_zh else "## Memory"
            parts.append(f"\n\n{header}\n\n{memory}")

        # 6. 工具描述
        if tools_desc:
            parts.append(f"\n\n{tools_desc}")

        return "\n".join(parts)

    def _render_context(self, context: dict, is_zh: bool) -> str:
        """渲染动态上下文"""
        lines = ["\n\n## 当前工作上下文" if is_zh else "\n\n## Current Work Context"]
        if context.get("project"):
            lines.append(f"- {'项目' if is_zh else 'Project'}：{context['project']}")
        if context.get("mcu"):
            lines.append(f"- {'MCU' if is_zh else 'MCU'}：{context['mcu']}")
        if context.get("motor"):
            lines.append(f"- {'电机' if is_zh else 'Motor'}：{context['motor']}")
        if context.get("task"):
            lines.append(f"- {'当前任务' if is_zh else 'Current Task'}：{context['task']}")
        return "\n".join(lines)


# 默认实例
_default_builder = None


def get_persona_builder() -> PersonaBuilder:
    """获取默认人格构建器"""
    global _default_builder
    if _default_builder is None:
        _default_builder = PersonaBuilder()
    return _default_builder
