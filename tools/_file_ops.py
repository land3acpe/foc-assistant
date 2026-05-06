"""文件操作工具：read_file, read_many_files, write_file, edit_file"""

import json
import os
from pathlib import Path
from config import PROJECT_ROOT, DESKTOP
from tools._common import _resolve_path, _decode_text_file, TEXT_SUFFIXES

def _read_file(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        content = _decode_text_file(path)

        lines_spec = args.get("lines", "")
        if lines_spec:
            if "-" in lines_spec:
                start, end = lines_spec.split("-")
                lines = content.split("\n")
                start_idx = max(0, int(start) - 1)
                end_idx = min(len(lines), int(end))
                content = "\n".join(lines[start_idx:end_idx])
            else:
                n = int(lines_spec)
                lines = content.split("\n")
                content = "\n".join(lines[:n])

        # 如果内容太长则截断
        if len(content) > 8000:
            content = content[:8000] + "\n\n... (内容过长已截断，使用 lines 参数读取特定范围)"

        return content
    except Exception as e:
        return f"读取失败: {e}"


def _read_many_files(args: dict) -> str:
    paths = args.get("paths", [])
    if not isinstance(paths, list) or not paths:
        return "错误: paths 必须是非空列表"

    per_file_limit = int(args.get("per_file_limit", 4000))
    total_limit = int(args.get("total_limit", 20000))
    outputs = []
    total = 0

    for path_str in paths:
        path = _resolve_path(str(path_str))
        header = f"\n===== {path} =====\n"
        if not path.exists():
            chunk = header + "错误: 文件不存在"
        elif not path.is_file():
            chunk = header + "错误: 不是文件"
        else:
            try:
                content = _decode_text_file(path)
                if len(content) > per_file_limit:
                    content = content[:per_file_limit] + "\n... (单文件内容过长已截断)"
                chunk = header + content
            except Exception as e:
                chunk = header + f"读取失败: {e}"

        if total + len(chunk) > total_limit:
            remaining = max(total_limit - total, 0)
            if remaining > 0:
                outputs.append(chunk[:remaining])
            outputs.append("\n... (总输出过长已截断)")
            break

        outputs.append(chunk)
        total += len(chunk)

    return "\n".join(outputs).strip()


def _write_file(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    content = args["content"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"文件已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"写入失败: {e}"


def _edit_file(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    old = args["old_text"]
    new = args["new_text"]

    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        content = path.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            return f"错误: 在文件中未找到要替换的文本。请检查 old_text 是否完全匹配。"
        if count > 1:
            return f"错误: old_text 在文件中出现了 {count} 次，请提供更多上下文使其唯一。"
        content = content.replace(old, new)
        path.write_text(content, encoding="utf-8")
        return f"编辑成功: {path}"
    except Exception as e:
        return f"编辑失败: {e}"
