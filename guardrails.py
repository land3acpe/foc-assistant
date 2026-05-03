"""FOC-Assistant Guardrails 系统

参考 OpenAI Agents SDK 的 Guardrails，提供：
- 输入护栏：在任务进入 Agent 之前检查安全性
- 输出护栏：在 Agent 输出返回用户之前检查合规性

Guardrail 不修改内容，只决定放行或拦截。
"""

import re
from dataclasses import dataclass
from typing import Optional

from tracing import get_tracer


@dataclass
class GuardrailResult:
    passed: bool
    rule: str
    detail: str = ""
    severity: str = "info"  # "info" | "warning" | "block"


class InputGuardrail:
    """输入护栏：检查用户输入是否安全。"""

    def __init__(self):
        self.rules = [
            self._check_prompt_injection,
            self._check_sensitive_paths,
            self._check_dangerous_commands,
            self._check_excessive_length,
        ]

    def check(self, user_input: str) -> GuardrailResult:
        """检查用户输入。返回第一个拦截结果，全部通过则返回 passed=True。"""
        for rule_fn in self.rules:
            result = rule_fn(user_input)
            if not result.passed:
                get_tracer().trace_guardrail(
                    direction="input",
                    rule=result.rule,
                    blocked=True,
                    detail=result.detail,
                )
                return result
        get_tracer().trace_guardrail(
            direction="input",
            rule="all_passed",
            blocked=False,
        )
        return GuardrailResult(passed=True, rule="all_passed")

    def _check_prompt_injection(self, text: str) -> GuardrailResult:
        """检测常见的 prompt injection 模式。"""
        injection_patterns = [
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"忽略(之前|上面|所有)的(指令|提示|要求)",
            r"你现在是(?!.*FOC)",  # "你现在是XX" 但不是 FOC 相关
            r"system\s*:\s*",  # 伪造 system message
            r"<\|im_start\|>",  # 伪造 chat template
            r"jailbreak",
            r"DAN\s+mode",
        ]
        text_lower = text.lower()
        for pattern in injection_patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return GuardrailResult(
                    passed=False,
                    rule="prompt_injection",
                    detail=f"检测到可能的 prompt injection: {pattern}",
                    severity="block",
                )
        return GuardrailResult(passed=True, rule="prompt_injection")

    def _check_sensitive_paths(self, text: str) -> GuardrailResult:
        """检测是否试图访问敏感路径。"""
        sensitive_patterns = [
            r"\.env\b",
            r"id_rsa",
            r"\.ssh[/\\]",
            r"credentials",
            r"password",
            r"secret",
            r"token\.json",
        ]
        text_lower = text.lower()
        for pattern in sensitive_patterns:
            if re.search(pattern, text_lower):
                return GuardrailResult(
                    passed=False,
                    rule="sensitive_path",
                    detail=f"检测到敏感路径访问尝试: {pattern}",
                    severity="block",
                )
        return GuardrailResult(passed=True, rule="sensitive_path")

    def _check_dangerous_commands(self, text: str) -> GuardrailResult:
        """检测是否包含危险 shell 命令。"""
        # 这个检查比较宽松，只拦截明显的恶意命令
        dangerous = [
            r"rm\s+-rf\s+/",
            r"format\s+[a-z]:",
            r"del\s+/[sfq]\s+",
            r"shutdown\s",
            r"mkfs\.",
            r"dd\s+if=.*of=/dev/",
        ]
        text_lower = text.lower()
        for pattern in dangerous:
            if re.search(pattern, text_lower):
                return GuardrailResult(
                    passed=False,
                    rule="dangerous_command",
                    detail=f"检测到危险命令模式: {pattern}",
                    severity="block",
                )
        return GuardrailResult(passed=True, rule="dangerous_command")

    def _check_excessive_length(self, text: str) -> GuardrailResult:
        """检查输入是否过长。"""
        if len(text) > 50000:
            return GuardrailResult(
                passed=False,
                rule="excessive_length",
                detail=f"输入过长: {len(text)} 字符 (上限 50000)",
                severity="warning",
            )
        return GuardrailResult(passed=True, rule="excessive_length")


class OutputGuardrail:
    """输出护栏：检查 Agent 输出是否合规。"""

    def __init__(self):
        self.rules = [
            self._check_api_key_leak,
            self._check_private_path_leak,
            self._check_output_length,
        ]

    def check(self, output: str, task: str = "") -> GuardrailResult:
        """检查 Agent 输出。"""
        for rule_fn in self.rules:
            result = rule_fn(output, task)
            if not result.passed:
                get_tracer().trace_guardrail(
                    direction="output",
                    rule=result.rule,
                    blocked=True,
                    detail=result.detail,
                )
                return result
        get_tracer().trace_guardrail(
            direction="output",
            rule="all_passed",
            blocked=False,
        )
        return GuardrailResult(passed=True, rule="all_passed")

    def _check_api_key_leak(self, output: str, task: str) -> GuardrailResult:
        """检测输出中是否泄露了 API key。"""
        key_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",
            r"api[_-]?key[=:]\s*['\"]?[a-zA-Z0-9]{20,}",
            r"Bearer\s+[a-zA-Z0-9._-]{20,}",
        ]
        for pattern in key_patterns:
            matches = re.findall(pattern, output)
            if matches:
                return GuardrailResult(
                    passed=False,
                    rule="api_key_leak",
                    detail=f"输出中可能包含 API key (模式: {pattern})",
                    severity="block",
                )
        return GuardrailResult(passed=True, rule="api_key_leak")

    def _check_private_path_leak(self, output: str, task: str) -> GuardrailResult:
        """检测输出中是否泄露了私有路径（仅在非文件操作任务中检查）。"""
        # 如果任务本身涉及文件操作，允许出现路径
        file_task_keywords = ["读取", "写入", "文件", "路径", "read", "write", "file", "path", "目录"]
        if any(kw in task.lower() for kw in file_task_keywords):
            return GuardrailResult(passed=True, rule="private_path_leak")

        # 检查是否泄露了 .env 内容
        if re.search(r"(API_KEY|SECRET|PASSWORD)\s*=\s*\S+", output):
            return GuardrailResult(
                passed=False,
                rule="private_path_leak",
                detail="输出中可能泄露了环境变量/密钥",
                severity="block",
            )
        return GuardrailResult(passed=True, rule="private_path_leak")

    def _check_output_length(self, output: str, task: str) -> GuardrailResult:
        """检查输出是否过长。"""
        if len(output) > 100000:
            return GuardrailResult(
                passed=False,
                rule="excessive_output",
                detail=f"输出过长: {len(output)} 字符 (上限 100000)",
                severity="warning",
            )
        return GuardrailResult(passed=True, rule="excessive_output")


# 全局单例
_input_guardrail = InputGuardrail()
_output_guardrail = OutputGuardrail()

def get_input_guardrail() -> InputGuardrail:
    return _input_guardrail

def get_output_guardrail() -> OutputGuardrail:
    return _output_guardrail
