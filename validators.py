"""Validation helpers for FOC-Assistant graph workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from config import PROJECT_ROOT


@dataclass
class ValidationResult:
    ok: bool
    message: str
    files: list[str] = field(default_factory=list)


def requested_output_paths(task: str) -> list[Path]:
    paths: list[Path] = []
    compact = re.sub(r"\s+", "", task.lower())

    if any(name in compact for name in ("focexamplecode", "focexamplcode", "focexamplecodes", "focexamplcodes")):
        paths.append(Path(r"C:\Users\macree\Desktop\focexamplecode"))

    if "foc_example_codes" in compact or "focexamplecodes" in compact:
        paths.append(Path(r"C:\Users\macree\Desktop\FOC_Example_Codes"))

    for match in re.finditer(r"[A-Za-z]:\\[^\s，。；;]+", task):
        paths.append(Path(match.group(0).rstrip("。,.，")))

    seen = set()
    unique = []
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def validate_execution_outputs(task: str, final_text: str = "") -> ValidationResult:
    paths = requested_output_paths(task)
    if not paths:
        if _looks_plan_only(final_text):
            return ValidationResult(False, "回复像计划而不是完成结果，且未识别到可校验输出路径。")
        return ValidationResult(True, "未识别到明确输出路径，跳过文件系统校验。")

    files: list[str] = []
    problems: list[str] = []

    for path in paths:
        if not path.exists():
            problems.append(f"目标路径不存在: {path}")
            continue

        if path.is_file():
            files.append(str(path))
            continue

        found = [p for p in path.rglob("*") if p.is_file()]
        files.extend(str(p) for p in found[:40])
        if not found:
            problems.append(f"目标目录为空: {path}")

        suffixes = {p.suffix.lower() for p in found}
        task_compact = re.sub(r"\s+", "", task.lower())
        if "代码" in task_compact or "examplecode" in task_compact or "示例" in task_compact:
            if not ({".c", ".h", ".py", ".m", ".md"} & suffixes):
                problems.append(f"目标目录缺少代码/说明文件: {path}")

    if _looks_plan_only(final_text):
        problems.append("最终回复像计划或开场白，没有明确说明已完成。")

    if problems:
        return ValidationResult(False, "\n".join(problems), files)

    return ValidationResult(True, f"校验通过，发现 {len(files)} 个文件。", files)


def _looks_plan_only(text: str) -> bool:
    compact = re.sub(r"\s+", "", text.lower())
    plan_markers = (
        "我将", "我会", "开始编写", "准备编写", "接下来", "现在开始",
        "将生成", "will create", "will generate",
    )
    done_markers = (
        "已生成", "已创建", "已写入", "完成", "生成了", "文件路径",
        "created", "generated", "written",
    )
    return any(m in compact for m in plan_markers) and not any(m in compact for m in done_markers)
