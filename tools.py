"""FOC-Assistant 工具集"""

import csv
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

import chardet

from config import DANGEROUS_PATTERNS, DANGER_CONFIRM, DESKTOP, PROJECT_ROOT
from knowledge import get_kb

TOOL_ROOT = Path(__file__).resolve().parent
SAFE_ROOTS = tuple(
    root.resolve()
    for root in (TOOL_ROOT, PROJECT_ROOT, DESKTOP)
)
SENSITIVE_NAMES = {
    ".env",
    ".wechat_token.json",
    ".netrc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
RUN_COMMAND_ALLOWED = os.environ.get("FOC_ALLOW_RUN_COMMAND", "").lower() in {"1", "true", "yes", "on"}

SKIP_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".pytest_cache",
    "node_modules", ".venv", "venv", "dist", "build",
}
BUILD_SKIP_DIRS = SKIP_DIRS | {"Debug", "Release", "Flash", "RAM"}
TEXT_SUFFIXES = {
    ".c", ".h", ".cpp", ".hpp", ".cc", ".asm", ".cmd", ".m", ".py",
    ".md", ".txt", ".json", ".xml", ".mk", ".ps1", ".bat", ".tex", ".bib",
}


# ============================================================
# 工具注册表（给 OpenAI/DeepSeek API 用）
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件内容。支持源代码(.c/.h/.m)、文档(.md/.txt)、数据文件(.csv)等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，可以是绝对路径或相对于项目根目录的路径",
                    },
                    "lines": {
                        "type": "string",
                        "description": "可选，指定读取的行范围，如 '1-100' 或 '50'",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_many_files",
            "description": "一次读取多个文件，减少多轮工具调用。适合同时查看 .c/.h/.m/.md 等小片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "文件路径列表，可以是绝对路径或相对于项目根目录的路径",
                    },
                    "per_file_limit": {
                        "type": "integer",
                        "description": "每个文件最多返回字符数，默认 4000",
                    },
                    "total_limit": {
                        "type": "integer",
                        "description": "总返回字符数上限，默认 20000",
                    },
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建新文件或完全覆写已有文件。修改已有文件优先使用 edit_file 工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "按文件名快速查找文件。适合先定位 pmsm_controller.c、*.slx、*.csv 等，再读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "文件名关键词或通配符，如 'pmsm'、'*.slx'、'eso*.c'",
                    },
                    "directory": {
                        "type": "string",
                        "description": "搜索目录路径，默认项目根目录",
                    },
                    "extensions": {
                        "type": "string",
                        "description": "可选，扩展名过滤，逗号分隔，如 '.c,.h,.m'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回数量，默认 80",
                    },
                    "skip_build_dirs": {
                        "type": "boolean",
                        "description": "是否跳过 Flash/RAM/Debug/Release 等构建目录，默认 true",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_overview",
            "description": "快速概览目录结构、文件数量、主要扩展名和顶层目录，适合接手新工程时第一步使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "目录路径，默认项目根目录",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "展示目录树深度，默认 2",
                    },
                    "skip_build_dirs": {
                        "type": "boolean",
                        "description": "是否跳过 Flash/RAM/Debug/Release 等构建目录，默认 true",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_symbols",
            "description": "从 C/H/MATLAB/Python 文件中提取函数、宏、结构体等符号，便于快速建立调用和模块认知。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件或目录路径",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "目录模式下的文件过滤，如 '*.c'、'*.h'、'*.m'，默认常见源码文件",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回符号数，默认 200",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "在文件中做精确的字符串替换。old_text 必须在文件中唯一匹配。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "要被替换的原文本（必须精确匹配，且文件中只出现一次）",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "在项目代码中搜索关键词或正则表达式。支持 C/头文件/MATLAB/汇编等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "搜索关键词或正则表达式，如 'ESO_init' 或 'float.*current'",
                    },
                    "directory": {
                        "type": "string",
                        "description": "搜索目录路径，默认项目根目录",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "文件名过滤，如 '*.c'、'*.h'、'*.m'",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "列出目录内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，默认项目根目录",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出子目录，默认 false",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行 shell 命令。可用于编译(CCS)、运行 MATLAB 脚本、git 操作等。危险命令需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录，默认项目根目录",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 60",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_csv",
            "description": "分析 CSV 波形数据文件。适合分析 ESO 观测器输出的电流/转速/扰动估计等数据。自动输出统计摘要和波形特征。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "CSV 文件路径",
                    },
                    "columns": {
                        "type": "string",
                        "description": "要分析的列名（逗号分隔），默认分析全部数值列",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "搜索桌面上与电机控制相关的论文（PDF 文件名），根据关键词匹配。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词，如 'ESO'、'SMC'、'finite-time'、'MTPA'",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": "任务完成时调用此工具，向用户报告完成状态和结果摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "任务完成摘要，说明做了什么、结果如何",
                    },
                    "files_modified": {
                        "type": "string",
                        "description": "修改过的文件列表（逗号分隔），没有就留空",
                    },
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_slx_model",
            "description": "解析 Simulink .slx 模型文件，列出子系统层次结构和关键模块。.slx 文件本质是 ZIP 压缩包，内含 XML 描述。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ".slx 文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_pi_params",
            "description": "根据电机参数（电阻、电感、极对数、磁链等）计算 PI 控制器参数。使用带宽法，给出电流环和速度环的 Kp/Ki 推荐值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "Rs": {"type": "number", "description": "定子电阻 (Ohm)"},
                    "Ld": {"type": "number", "description": "d 轴电感 (H)"},
                    "Lq": {"type": "number", "description": "q 轴电感 (H)"},
                    "flux": {"type": "number", "description": "永磁磁链 (Wb)"},
                    "poles": {"type": "integer", "description": "极对数"},
                    "J": {"type": "number", "description": "转动惯量 (kg.m^2) [可选，算速度环时需要]"},
                    "Ts_current": {"type": "number", "description": "电流环采样时间 (s)，默认 1e-4"},
                    "Ts_speed": {"type": "number", "description": "速度环采样时间 (s)，默认 1e-3"},
                    "bandwidth_current": {"type": "number", "description": "电流环期望带宽 (Hz)，默认 500"},
                    "bandwidth_speed": {"type": "number", "description": "速度环期望带宽 (Hz)，默认 50"},
                },
                "required": ["Rs", "Ld", "Lq", "flux", "poles"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_svpwm_table",
            "description": "生成 SVPWM 扇区切换表和矢量作用时间计算公式。适用于三相/双三相 PMSM。",
            "parameters": {
                "type": "object",
                "properties": {
                    "phases": {
                        "type": "integer",
                        "description": "电机相数，3 或 6（双三相），默认 3",
                    },
                    "Ts": {"type": "number", "description": "PWM 周期 (s)，默认 1e-4"},
                    "Vdc": {"type": "number", "description": "直流母线电压 (V)，默认 300"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_matlab_script",
            "description": "读取 MATLAB .m 脚本文件，并以结构化方式展示：变量定义、函数、参数表、Simulink 调用等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ".m 文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compile_ccs",
            "description": "调用 Code Composer Studio 命令行编译项目。需要 CCS 已安装在工作目录下。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "CCS 工程目录路径（包含 .ccsproject 文件）",
                    },
                    "build_config": {
                        "type": "string",
                        "description": "编译配置: Debug 或 Release，默认 Debug",
                    },
                },
                "required": ["project_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_controller_params",
            "description": "基于电机类型和规格，建议适合的控制策略和参数范围。覆盖 PI、SMC、ADRC、ESO 等常用方案。",
            "parameters": {
                "type": "object",
                "properties": {
                    "motor_type": {
                        "type": "string",
                        "description": "电机类型: PMSM / IM / BLDC",
                    },
                    "rated_speed": {"type": "number", "description": "额定转速 (RPM)"},
                    "rated_current": {"type": "number", "description": "额定电流峰值 (A)"},
                    "rated_voltage": {"type": "number", "description": "额定电压 (V)"},
                    "control_target": {
                        "type": "string",
                        "description": "控制目标: torque / speed / position，默认 speed",
                    },
                },
                "required": ["motor_type", "rated_speed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "【优先使用】搜索本地知识库。知识库包含项目文档、技术笔记、论文摘要等。当需要查找技术概念、公式、参考信息时，先搜索本地知识库再考虑其他工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询，如 'ESO 参数整定'、'SVPWM 原理'、'MTPA 公式'",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认 8",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_add",
            "description": "向知识库添加一条笔记，方便后续复用。比如电机参数表、已验证的调参经验、常用公式等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "笔记标题"},
                    "content": {"type": "string", "description": "笔记内容"},
                    "tags": {"type": "string", "description": "标签（逗号分隔），如 'PI, 电流环, 调参'"},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_list",
            "description": "列出知识库中所有已索引的文档，了解知识库覆盖范围。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_import",
            "description": "导入单个文件到知识库。支持 PDF（提取全文）、CSV（索引列名）、MD/TXT（全文索引）。文件会被复制到 knowledge_base/ 目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "要导入的文件路径",
                    },
                    "tags": {
                        "type": "string",
                        "description": "标签（逗号分隔），如 '有限时间, ESO, SMC'",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_rebuild",
            "description": "重建知识库索引。当添加了大量新文件到 knowledge_base/ 目录后使用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索，返回相关网页的标题、URL 和摘要。适合查找最新技术资料、芯片手册、学术资源等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 'TMS320F28377D SVPWM example' 或 '有限时间ESO 最新论文'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数，默认 5，最大 10",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取并阅读网页内容。适合打开 web_search 返回的 URL 查看详情。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网页 URL",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": "从 URL 下载文件到本地安全目录。适合下载芯片数据手册 PDF、参考文档等；下载后可用 knowledge_import 导入知识库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "文件 URL",
                    },
                    "path": {
                        "type": "string",
                        "description": "保存路径，可以是绝对路径或相对项目路径；必须位于安全目录内",
                    },
                },
                "required": ["url", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflect",
            "description": "触发自我反思：评估上一次回答的质量，检查是否有遗漏或错误，给出改进建议。适合在任务完成后主动检查质量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_summary": {
                        "type": "string",
                        "description": "对本次任务的简短描述",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "搜索对话历史记忆。记忆系统自动从对话中提取技术洞察、调试经验、参数配置等。适合回顾之前讨论过的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 'PI调参'、'编译错误'、'ESO参数'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回条数，默认 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_stats",
            "description": "查看记忆系统统计：记忆条目数、用户关注主题、常用工具、对话次数等。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scheduler_status",
            "description": "查看后台调度器状态：定时任务列表、运行次数、下次执行时间、文件监控状态等。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": "启动一个专业子 Agent 来执行子任务。适合将复杂任务拆分给专业领域处理。例如：让代码分析专家深入分析某文件、让波形分析专家分析 CSV、让控制器设计专家计算参数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "专业 Agent 标识。可选: code_analyzer, waveform_analyzer, controller_designer, research_agent, debug_helper",
                        "enum": ["code_analyzer", "waveform_analyzer", "controller_designer", "research_agent", "debug_helper"],
                    },
                    "task": {
                        "type": "string",
                        "description": "要委派给子 Agent 的具体子任务描述",
                    },
                },
                "required": ["agent_id", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "列出所有可用的专业子 Agent 及其能力和适用场景。在不确定用哪个 Agent 时先调用此工具。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ============================================================
# 工具执行器
# ============================================================

def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_path(path: str) -> Path:
    """将相对路径转为绝对路径，并限制在安全目录内。"""
    p = Path(path).expanduser()
    resolved = p.resolve() if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    if not any(_is_relative_to(resolved, root) for root in SAFE_ROOTS):
        safe_text = ", ".join(str(root) for root in SAFE_ROOTS)
        raise PermissionError(f"路径不在允许范围内: {resolved}\n允许范围: {safe_text}")

    lowered_parts = {part.lower() for part in resolved.parts}
    if ".git" in lowered_parts or any(name in lowered_parts for name in SENSITIVE_NAMES):
        raise PermissionError(f"禁止访问敏感路径: {resolved}")

    return resolved


def _is_dangerous(command: str) -> bool:
    """检查命令是否包含危险操作"""
    cmd_lower = command.lower()
    return any(p in cmd_lower for p in DANGEROUS_PATTERNS)


def _should_skip_dir(dirname: str, skip_build_dirs: bool = False) -> bool:
    skip = BUILD_SKIP_DIRS if skip_build_dirs else SKIP_DIRS
    return dirname in skip or dirname.startswith(".")


def _decode_text_file(path: Path) -> str:
    raw = path.read_bytes()
    encoding = chardet.detect(raw)["encoding"] or "utf-8"
    return raw.decode(encoding, errors="replace")


def _iter_candidate_files(directory: Path, file_filter: str = "", skip_build_dirs: bool = False):
    import fnmatch

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d, skip_build_dirs)]
        for fname in files:
            if file_filter and not fnmatch.fnmatch(fname, file_filter):
                continue
            yield Path(root) / fname


def execute_tool(name: str, args: dict, danger_callback: Optional[Callable[[str], bool]] = None) -> str:
    """执行单个工具调用。danger_callback 用于远程确认危险命令，默认通过 input() 交互。"""

    try:
        if name == "read_file":
            return _read_file(args)

        elif name == "read_many_files":
            return _read_many_files(args)

        elif name == "write_file":
            return _write_file(args)

        elif name == "edit_file":
            return _edit_file(args)

        elif name == "search_code":
            return _search_code(args)

        elif name == "find_files":
            return _find_files(args)

        elif name == "project_overview":
            return _project_overview(args)

        elif name == "extract_symbols":
            return _extract_symbols(args)

        elif name == "list_directory":
            return _list_directory(args)

        elif name == "run_command":
            return _run_command(args, danger_callback)

        elif name == "analyze_csv":
            return _analyze_csv(args)

        elif name == "search_papers":
            return _search_papers(args)

        elif name == "task_complete":
            return json.dumps({"status": "complete", "summary": args.get("summary", "")})

        elif name == "parse_slx_model":
            return _parse_slx_model(args)

        elif name == "calculate_pi_params":
            return _calculate_pi_params(args)

        elif name == "generate_svpwm_table":
            return _generate_svpwm_table(args)

        elif name == "read_matlab_script":
            return _read_matlab_script(args)

        elif name == "compile_ccs":
            return _compile_ccs(args)

        elif name == "suggest_controller_params":
            return _suggest_controller_params(args)

        elif name == "knowledge_search":
            return _knowledge_search(args)

        elif name == "knowledge_add":
            return _knowledge_add(args)

        elif name == "knowledge_list":
            return _knowledge_list(args)

        elif name == "knowledge_import":
            return _knowledge_import(args)

        elif name == "knowledge_rebuild":
            return _knowledge_rebuild(args)

        elif name == "web_search":
            return _web_search(args)

        elif name == "web_fetch":
            return _web_fetch(args)

        elif name == "download_file":
            return _download_file(args)

        elif name == "reflect":
            return _reflect_tool(args)

        elif name == "memory_search":
            return _memory_search(args)

        elif name == "memory_stats":
            return _memory_stats(args)

        elif name == "scheduler_status":
            return _scheduler_status(args)

        elif name == "spawn_agent":
            return _spawn_agent(args)

        elif name == "list_agents":
            return _list_agents(args)

        else:
            return f"未知工具: {name}"
    except PermissionError as e:
        return f"权限拒绝: {e}"


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


def _search_code(args: dict) -> str:
    pattern = args["pattern"]
    directory = _resolve_path(args.get("directory", str(PROJECT_ROOT)))
    file_filter = args.get("file_pattern", "")

    try:
        cmd = ["rg", "-n", "--no-heading", pattern, str(directory)]
        if file_filter:
            cmd.extend(["-g", file_filter])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode not in (0, 1):
            fallback = _search_code_fallback(pattern, directory, file_filter)
            return fallback + f"\n\n[提示] rg 不可用，已自动切换 Python 搜索。原因: {(result.stderr or '').strip()[:200]}"

        output = result.stdout.strip()
        if not output:
            return f"未在 {directory} 中找到 '{pattern}'"
        if len(output) > 6000:
            lines = output.split("\n")
            output = "\n".join(lines[:80]) + f"\n\n... (共 {len(lines)} 条匹配，仅显示前 80 条)"
        return output
    except Exception as e:
        fallback = _search_code_fallback(pattern, directory, file_filter)
        return fallback + f"\n\n[提示] rg 不可用，已自动切换 Python 搜索。原因: {e}"


def _search_code_fallback(pattern: str, directory: Path, file_filter: str) -> str:
    """当 rg 不可用时的 Python 回退搜索"""
    import fnmatch
    results = []
    try:
        regex = re.compile(pattern)
    except re.error:
        # 不是正则，用普通字符串搜索
        regex = None

    for filepath in _iter_candidate_files(directory, file_filter):
        if filepath.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if filepath.stat().st_size > 2_000_000:
                continue
            for i, line in enumerate(filepath.read_text(encoding="utf-8", errors="ignore").split("\n"), 1):
                if regex:
                    if regex.search(line):
                        results.append(f"{filepath}:{i}: {line.strip()}")
                elif pattern.lower() in line.lower():
                    results.append(f"{filepath}:{i}: {line.strip()}")
                if len(results) >= 80:
                    break
        except Exception:
            continue
        if len(results) >= 80:
            break

    if not results:
        return f"未在 {directory} 中找到 '{pattern}'"
    return "\n".join(results)


def _find_files(args: dict) -> str:
    import fnmatch

    query = args["query"].strip()
    directory = _resolve_path(args.get("directory", str(PROJECT_ROOT)))
    limit = int(args.get("limit", 80))
    skip_build_dirs = bool(args.get("skip_build_dirs", True))
    extensions = {
        e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}"
        for e in args.get("extensions", "").split(",")
        if e.strip()
    }

    if not directory.exists():
        return f"错误: 目录不存在: {directory}"

    use_glob = any(ch in query for ch in "*?[]")
    q_lower = query.lower()
    results = []

    for filepath in _iter_candidate_files(directory, skip_build_dirs=skip_build_dirs):
        if extensions and filepath.suffix.lower() not in extensions:
            continue
        name = filepath.name
        matched = fnmatch.fnmatch(name.lower(), q_lower) if use_glob else q_lower in name.lower()
        if matched:
            results.append(str(filepath))
            if len(results) >= limit:
                break

    if not results:
        return f"未在 {directory} 中找到文件: {query}"
    suffix = f"\n\n... (仅显示前 {limit} 个)" if len(results) >= limit else ""
    return f"找到 {len(results)} 个文件:\n" + "\n".join(f"  {p}" for p in results) + suffix


def _project_overview(args: dict) -> str:
    directory = _resolve_path(args.get("directory", str(PROJECT_ROOT)))
    max_depth = int(args.get("max_depth", 2))
    skip_build_dirs = bool(args.get("skip_build_dirs", True))

    if not directory.exists():
        return f"错误: 目录不存在: {directory}"

    ext_counts: dict[str, int] = {}
    top_counts: dict[str, int] = {}
    total_files = 0
    total_dirs = 0
    tree_lines = [f"{directory.name}/"]

    base_parts = len(directory.parts)
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d, skip_build_dirs)]
        root_path = Path(root)
        depth = len(root_path.parts) - base_parts
        total_dirs += len(dirs)
        if depth <= max_depth:
            indent = "  " * depth
            if depth > 0:
                tree_lines.append(f"{indent}{root_path.name}/")
            if depth < max_depth:
                for fname in sorted(files)[:12]:
                    tree_lines.append(f"{indent}  {fname}")
                if len(files) > 12:
                    tree_lines.append(f"{indent}  ... ({len(files) - 12} more files)")

        top = root_path.parts[base_parts] if len(root_path.parts) > base_parts else "."
        for fname in files:
            total_files += 1
            suffix = Path(fname).suffix.lower() or "(no ext)"
            ext_counts[suffix] = ext_counts.get(suffix, 0) + 1
            top_counts[top] = top_counts.get(top, 0) + 1

    top_ext = sorted(ext_counts.items(), key=lambda x: -x[1])[:12]
    top_dirs = sorted(top_counts.items(), key=lambda x: -x[1])[:12]

    return (
        f"项目概览: {directory}\n"
        f"文件: {total_files}, 目录: {total_dirs}, 展示深度: {max_depth}\n\n"
        f"主要扩展名:\n" +
        "\n".join(f"  {ext}: {count}" for ext, count in top_ext) +
        f"\n\n主要目录文件数:\n" +
        "\n".join(f"  {name}: {count}" for name, count in top_dirs) +
        f"\n\n目录树:\n" +
        "\n".join(tree_lines[:120])
    )


def _extract_symbols(args: dict) -> str:
    path = _resolve_path(args["path"])
    file_pattern = args.get("file_pattern", "")
    limit = int(args.get("limit", 200))

    if not path.exists():
        return f"错误: 路径不存在: {path}"

    if path.is_file():
        files = [path]
    else:
        patterns = [file_pattern] if file_pattern else ["*.c", "*.h", "*.m", "*.py"]
        files = []
        for pattern in patterns:
            files.extend(_iter_candidate_files(path, pattern, skip_build_dirs=True))

    symbols = []
    for filepath in files:
        if filepath.suffix.lower() not in {".c", ".h", ".m", ".py"}:
            continue
        try:
            text = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        rel = str(filepath)
        suffix = filepath.suffix.lower()

        if suffix in (".c", ".h"):
            patterns = [
                ("macro", r"^\s*#\s*define\s+([A-Za-z_]\w+)"),
                ("struct", r"^\s*(?:typedef\s+)?struct\s+([A-Za-z_]\w*)?"),
                ("function", r"^\s*(?:static\s+|inline\s+|extern\s+)?[A-Za-z_][\w\s\*]*\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?"),
            ]
        elif suffix == ".m":
            patterns = [("function", r"^\s*function\s+(?:\[?.*?\]?\s*=\s*)?([A-Za-z_]\w*)")]
        else:
            patterns = [
                ("class", r"^\s*class\s+([A-Za-z_]\w*)"),
                ("function", r"^\s*def\s+([A-Za-z_]\w*)"),
            ]

        for kind, pattern in patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                name = match.group(1) or "(anonymous)"
                line = text.count("\n", 0, match.start()) + 1
                if suffix in (".c", ".h") and kind == "function" and name in {"if", "for", "while", "switch", "return"}:
                    continue
                symbols.append(f"{rel}:{line}: [{kind}] {name}")
                if len(symbols) >= limit:
                    break
            if len(symbols) >= limit:
                break
        if len(symbols) >= limit:
            break

    if not symbols:
        return f"未提取到符号: {path}"
    suffix = f"\n\n... (仅显示前 {limit} 个符号)" if len(symbols) >= limit else ""
    return f"符号提取: {path}\n" + "\n".join(symbols) + suffix


def _list_directory(args: dict) -> str:
    directory = _resolve_path(args.get("path", str(PROJECT_ROOT)))
    recursive = args.get("recursive", False)

    if not directory.exists():
        return f"错误: 目录不存在: {directory}"

    try:
        if recursive:
            output = []
            for root, dirs, files in os.walk(directory):
                # 跳过隐藏目录
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                level = root.replace(str(directory), "").count(os.sep)
                indent = "  " * level
                output.append(f"{indent}{Path(root).name}/")
                for f in sorted(files):
                    output.append(f"{indent}  {f}")
            return "\n".join(output)
        else:
            items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
            lines = []
            for item in items:
                prefix = "[F]" if item.is_file() else "[D]"
                lines.append(f"{prefix} {item.name}")
            return "\n".join(lines) if lines else "(空目录)"
    except Exception as e:
        return f"列出目录失败: {e}"


def _run_command(args: dict, danger_callback: Optional[Callable[[str], bool]] = None) -> str:
    command = args["command"]
    cwd = args.get("cwd", str(PROJECT_ROOT))

    if not RUN_COMMAND_ALLOWED:
        return (
            "run_command 默认已禁用，避免 QQ/微信远程入口执行系统命令。\n"
            "如果你确认需要开放，请在本机环境变量中设置 FOC_ALLOW_RUN_COMMAND=1 后重启 Bot。"
        )

    if DANGER_CONFIRM and _is_dangerous(command):
        if danger_callback is not None:
            allowed = danger_callback(command)
            if not allowed:
                return "用户拒绝了该命令(远程自动拒绝)"
        else:
            print(f"\n[DANGER] Dangerous command: {command}")
            confirm = input("Allow execution? (y/n): ")
            if confirm.lower() != "y":
                return "用户拒绝了该命令"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=args.get("timeout", 60),
            cwd=str(_resolve_path(cwd)),
        )
        output = result.stdout + result.stderr
        if not output.strip():
            return f"命令执行成功（无输出），退出码: {result.returncode}"
        if len(output) > 5000:
            output = output[:5000] + "\n\n... (输出过长已截断)"
        return output
    except subprocess.TimeoutExpired:
        return f"命令超时（{args.get('timeout', 60)} 秒）"
    except Exception as e:
        return f"命令执行失败: {e}"


def _analyze_csv(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        sample = path.read_bytes()[:65536]
        encoding = chardet.detect(sample)["encoding"] or "utf-8"

        target_cols = args.get("columns", "")
        target_names = [c.strip() for c in target_cols.split(",") if c.strip()]

        with path.open("r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            if not fieldnames:
                return "错误: CSV 文件无法解析表头"

            probe_rows = []
            total_rows = 0
            for row in reader:
                total_rows += 1
                if len(probe_rows) < 50:
                    probe_rows.append(row)

        if target_names:
            numeric_columns = [c for c in target_names if c in fieldnames]
        else:
            numeric_columns = []
            for col in fieldnames:
                numeric_hits = 0
                for row in probe_rows:
                    try:
                        float(row[col])
                        numeric_hits += 1
                    except (ValueError, KeyError, TypeError):
                        pass
                if numeric_hits > 0:
                    numeric_columns.append(col)

        if not numeric_columns:
            return f"错误: 未找到数值列。可用列: {', '.join(fieldnames)}"

        steady_start = int(total_rows * 0.7)
        stats = {
            col: {
                "n": 0, "sum": 0.0, "min": None, "max": None,
                "steady_n": 0, "steady_sum": 0.0, "steady_sum_sq": 0.0,
            }
            for col in numeric_columns
        }

        with path.open("r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader):
                for col in numeric_columns:
                    try:
                        val = float(row[col])
                    except (ValueError, KeyError, TypeError):
                        continue

                    st = stats[col]
                    st["n"] += 1
                    st["sum"] += val
                    st["min"] = val if st["min"] is None else min(st["min"], val)
                    st["max"] = val if st["max"] is None else max(st["max"], val)

                    if row_idx >= steady_start:
                        st["steady_n"] += 1
                        st["steady_sum"] += val
                        st["steady_sum_sq"] += val * val

        output_lines = [f"CSV 分析: {path.name}", f"共 {total_rows} 行, 数值列: {numeric_columns}", "=" * 50]

        for col in numeric_columns:
            st = stats[col]
            if st["n"] == 0:
                continue

            mean_val = st["sum"] / st["n"]
            steady_n = max(st["steady_n"], 1)
            steady_mean = st["steady_sum"] / steady_n
            variance = max(st["steady_sum_sq"] / steady_n - steady_mean * steady_mean, 0.0)
            ripple = variance ** 0.5

            output_lines.append(
                f"\n{col}:\n"
                f"  范围: [{st['min']:.4f}, {st['max']:.4f}]\n"
                f"  均值: {mean_val:.4f}\n"
                f"  稳态均值(后30%): {steady_mean:.4f}\n"
                f"  稳态纹波: {ripple:.4f} ({ripple / max(abs(steady_mean), 0.001) * 100:.2f}%)"
            )

        return "\n".join(output_lines)

    except Exception as e:
        return f"CSV 分析失败: {e}"


def _search_papers(args: dict) -> str:
    keyword = args["keyword"].lower()
    results = []

    try:
        for item in DESKTOP.iterdir():
            if item.is_file() and item.suffix.lower() == ".pdf":
                name = item.name.lower()
                if keyword in name:
                    results.append(item.name)

        if not results:
            return f"未在桌面找到包含 '{keyword}' 的论文文件"
        return "找到以下相关论文:\n" + "\n".join(f"  - {r}" for r in sorted(results))
    except Exception as e:
        return f"搜索论文失败: {e}"


# ============================================================
# 新增工具: Simulink / PI计算 / SVPWM / MATLAB / CCS / 控制器
# ============================================================

def _parse_slx_model(args: dict) -> str:
    """解析 Simulink .slx 模型结构"""
    import zipfile

    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()

            # 找到 XML 主文件
            xml_file = None
            for n in names:
                if n.endswith(".xml") and not n.startswith("_"):
                    xml_file = n
                    break

            if not xml_file:
                return f"SLX 解析: 共 {len(names)} 个内部文件，但未找到主 XML\n文件列表:\n" + "\n".join(f"  {n}" for n in sorted(names)[:30])

            xml_content = zf.read(xml_file).decode("utf-8", errors="ignore")

            # 提取 Block 名称
            blocks = re.findall(r'BlockType="([^"]+)"', xml_content)
            block_names = re.findall(r'Name="([^"]+)"', xml_content)

            block_counts = {}
            for b in blocks:
                block_counts[b] = block_counts.get(b, 0) + 1

            return (
                f"SLX 模型分析: {path.name}\n"
                f"  内部文件数: {len(names)}\n"
                f"  主 XML: {xml_file}\n"
                f"  模块总数: {len(blocks)}\n\n"
                f"模块类型统计:\n" +
                "\n".join(f"  {bt}: {cnt}" for bt, cnt in sorted(block_counts.items(), key=lambda x: -x[1])[:25]) +
                f"\n\n子系统/模块名称:\n" +
                "\n".join(f"  {n}" for n in block_names[:40] if len(n) > 2)
            )
    except Exception as e:
        return f"SLX 解析失败: {e}"


def _calculate_pi_params(args: dict) -> str:
    """根据电机参数计算 PI 控制器增益（带宽法）"""
    Rs = float(args["Rs"])
    Ld = float(args["Ld"])
    Lq = float(args["Lq"])
    flux = float(args["flux"])
    poles = int(args["poles"])
    J = float(args.get("J", 0))
    Ts_c = float(args.get("Ts_current", 1e-4))
    Ts_s = float(args.get("Ts_speed", 1e-3))
    bw_c = float(args.get("bandwidth_current", 500))   # Hz
    bw_s = float(args.get("bandwidth_speed", 50))        # Hz

    import math

    # 电流环 PI（零极点对消法）
    wc = 2 * math.pi * bw_c
    Kp_d = wc * Ld
    Ki_d = wc * Rs
    Kp_q = wc * Lq
    Ki_q = wc * Rs

    # 速度环 PI（对称最优法）
    Kt = 1.5 * poles * flux       # 转矩常数
    ws = 2 * math.pi * bw_s
    if J > 0:
        Kp_s = ws * J / Kt
        Ki_s = Kp_s * ws / 4       # 对称最优：Ti = 4/wc
    else:
        Kp_s, Ki_s = 0, 0

    # 数字实现时的积分限幅建议
    i_limit = 1.2 * 10  # 假设额定电流约 10A

    return (
        f"PI 参数计算结果 (带宽法)\n"
        f"{'='*50}\n"
        f"电机参数: Rs={Rs} Ohm, Ld={Ld} H, Lq={Lq} H, Flux={flux} Wb, Poles={poles}\n"
        f"{'='*50}\n\n"
        f"【电流环】 (fc = {bw_c} Hz, Ts = {Ts_c*1e6:.0f} us)\n"
        f"  Kp_d = {Kp_d:.4f}   Ki_d = {Ki_d:.4f}\n"
        f"  Kp_q = {Kp_q:.4f}   Ki_q = {Ki_q:.4f}\n"
        f"  零极点对消频率: {wc:.1f} rad/s\n"
        f"  积分限幅建议: +/- {i_limit:.1f}\n"
        f"  离散化: 后向欧拉, Ki_disc = Ki * Ts_c\n\n"
        f"【速度环】 (fs = {bw_s} Hz, Ts = {Ts_s*1e3:.1f} ms)\n"
        f"  Kp_s = {Kp_s:.6f}   Ki_s = {Ki_s:.6f}\n"
        f"  转矩常数 Kt = {Kt:.4f} Nm/A\n" +
        (f"  惯量 J = {J:.6f} kg.m^2\n" if J > 0 else "  (未提供惯量 J，无法完整计算速度环)\n") +
        f"\n【设计检查】\n"
        f"  带宽比 fc/fs = {bw_c/bw_s:.1f} (建议 > 10 以避免环间干扰)\n"
        f"  数字延迟裕度: {1/Ts_c/bw_c:.1f}x 采样频率\n"
    )


def _generate_svpwm_table(args: dict) -> str:
    """生成 SVPWM 扇区切换表"""
    phases = int(args.get("phases", 3))
    Ts = float(args.get("Ts", 1e-4))
    Vdc = float(args.get("Vdc", 300))

    if phases == 3:
        import math
        # 三相 SVPWM 基本矢量
        v_mag = Vdc * 2.0 / 3.0
        sectors = []
        for k in range(1, 7):
            theta_k = (k - 1) * math.pi / 3
            sectors.append(f"  扇区 {k}: 角度 [{math.degrees(theta_k):.0f}, {math.degrees(theta_k + math.pi/3):.0f}) deg")

        return (
            f"SVPWM 扇区表 ({phases}相)\n"
            f"{'='*50}\n"
            f"直流母线电压: {Vdc} V\n"
            f"PWM 周期: {Ts*1e6:.0f} us\n"
            f"基本矢量幅值: {v_mag:.1f} V\n"
            f"最大调制比 (线性): {Vdc/math.sqrt(3):.1f} V (相电压峰值)\n\n"
            f"【扇区判定 - 6 个扇区】\n" +
            "\n".join(sectors) +
            f"\n\n【矢量作用时间】 (Ts={Ts*1e6:.0f}us)\n"
            f"  T1 = sqrt(3)*|Uref|*Ts*sin(pi/3 - theta) / Vdc\n"
            f"  T2 = sqrt(3)*|Uref|*Ts*sin(theta) / Vdc\n"
            f"  T0 = Ts - T1 - T2\n\n"
            f"【各扇区开关序列】\n"
            f"  扇区1: 000-100-110-111-110-100-000\n"
            f"  扇区2: 000-110-010-111-010-110-000\n"
            f"  扇区3: 000-010-011-111-011-010-000\n"
            f"  扇区4: 000-011-001-111-001-011-000\n"
            f"  扇区5: 000-001-101-111-101-001-000\n"
            f"  扇区6: 000-101-100-111-100-101-000\n\n"
            f"【C2000 实现提示】\n"
            f"  - 使用 EPwm 模块的 TBPRD 设定周期\n"
            f"  - CMPA/CMPB 更新在 CTR=0 或 PRD 时触发\n"
            f"  - 七段式 SVPWM 在每个周期内采样两次，减少谐波\n"
        )
    else:
        return (
            f"双三相 SVPWM ({phases}相)\n"
            f"{'='*50}\n"
            f"【双三相解耦 SVPWM】\n"
            f"  将六相分解为两个三相子系统 (ABC + XYZ)\n"
            f"  各自独立进行三相 SVPWM\n"
            f"  载波相位差 30° 以减少 5/7 次谐波\n\n"
            f"【VSD 变换矩阵】\n"
            f"  alpha-beta 平面: 转矩产生分量\n"
            f"  xy 平面: 谐波分量（被 ESO 抑制）\n"
            f"  o1-o2 平面: 零序分量\n"
        )


def _read_matlab_script(args: dict) -> str:
    """读取 MATLAB .m 脚本并结构化展示"""
    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")

        # 提取变量赋值
        assignments = re.findall(r'^(\w+)\s*=\s*(.+?);?\s*$', content, re.MULTILINE)

        # 提取函数定义
        functions = re.findall(r'^function\s+(.+?)$', content, re.MULTILINE)

        # 提取 Simulink 调用
        sim_calls = re.findall(r"(sim|load_system|open_system)\(([^)]+)\)", content)

        # 提取注释行（以 % 开头）
        comments = [l.strip()[1:].strip() for l in content.split("\n") if l.strip().startswith("%")]

        output = [
            f"MATLAB 脚本分析: {path.name}",
            f"总行数: {len(content.splitlines())}",
            f"",
        ]

        if functions:
            output.append(f"[函数定义] ({len(functions)}个):")
            output.extend(f"  - {f}" for f in functions)
            output.append("")

        if assignments:
            output.append(f"[变量赋值] ({len(assignments)}个, 仅显示前20):")
            for var, val in assignments[:20]:
                output.append(f"  {var} = {val[:120]}")
            output.append("")

        if sim_calls:
            output.append(f"[Simulink 调用] ({len(sim_calls)}个):")
            for cmd, arg in sim_calls:
                output.append(f"  {cmd}({arg})")
            output.append("")

        if comments:
            output.append(f"[注释摘要]:")
            output.extend(f"  % {c[:150]}" for c in comments[:10])

        return "\n".join(output)
    except Exception as e:
        return f"MATLAB 脚本读取失败: {e}"


def _compile_ccs(args: dict) -> str:
    """调用 CCS 命令行编译"""
    pp = args.get("project_path", "")
    if not pp:
        return "错误: 缺少 project_path 参数"
    project_path = _resolve_path(pp)
    build_config = args.get("build_config", "Debug")

    if not (project_path / ".ccsproject").exists():
        return (
            f"错误: 在 {project_path} 未找到 .ccsproject 文件\n"
            f"请确认这是一个有效的 CCS 工程目录。\n\n"
            f"提示: 可用的 CCS 工程路径:\n"
            f"  {PROJECT_ROOT}"
        )

    # 尝试常见的 CCS 路径
    ccs_candidates = [
        r"d:\ccs12.8\ccs\eclipse\eclipsec.exe",
        r"d:\ccs\ccs\eclipse\eclipsec.exe",
        r"C:\ti\ccs1280\ccs\eclipse\eclipsec.exe",
        r"C:\ti\ccs1270\ccs\eclipse\eclipsec.exe",
    ]

    ccs_found = None
    for ccs_path in ccs_candidates:
        if Path(ccs_path).exists():
            ccs_found = ccs_path
            break

    if not ccs_found:
        # 尝试搜索
        result = subprocess.run(
            f'dir /s /b "d:\\*eclipsec.exe" 2>nul',
            shell=True, capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip():
            ccs_found = result.stdout.strip().split("\n")[0]

    if not ccs_found:
        return (
            f"CCS 命令行工具未找到，无法自动编译。\n"
            f"已搜索以下路径:\n" +
            "\n".join(f"  {p}" for p in ccs_candidates) +
            f"\n\n请手动通过 CCS GUI 编译项目:\n"
            f"  1. 打开 CCS\n"
            f"  2. Import Project -> {project_path}\n"
            f"  3. Project -> Build Config -> {build_config}\n"
            f"  4. Project -> Build All"
        )

    try:
        workspace = str(project_path.parent)
        cmd = (
            f'"{ccs_found}" -noSplash '
            f'-data "{workspace}" '
            f'-application com.ti.ccstudio.apps.projectBuild '
            f'-ccs.projects "{project_path.name}" '
            f'-ccs.configuration {build_config}'
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120, cwd=str(project_path))
        output = result.stdout + result.stderr
        return f"CCS 编译 ({build_config}):\n{'='*50}\n{output[-4000:]}" if output else f"编译完成，退出码: {result.returncode}"
    except Exception as e:
        return f"CCS 编译失败: {e}"


def _suggest_controller_params(args: dict) -> str:
    """根据电机类型和建议控制策略"""
    motor = args["motor_type"].upper()
    speed = float(args["rated_speed"])
    current = float(args.get("rated_current", 10))
    voltage = float(args.get("rated_voltage", 310))
    target = args.get("control_target", "speed")

    suggestions = []

    if motor == "PMSM":
        suggestions = [
            ("电流环", "PI (零极点对消)", "Kp = wc*L, Ki = wc*R", "带宽 300-800 Hz"),
            ("速度环", "PI (对称最优)", "Kp = w*J/Kt, Ki = Kp*w/4", "带宽 30-80 Hz"),
            ("电流环(增强)", "SMC (滑模)", "切换增益 > 扰动上界", "适合参数摄动大"),
            ("扰动补偿", "ESO (扩展状态观测器)", "L1=1000-2000, L2=10^5-10^6", "需整定带宽比 1:10"),
            ("速度环(增强)", "ADRC (自抗扰)", "wc=50-200, wo=3-5*wc, b0=Kt/J", "鲁棒性强"),
            ("MTPA", "查表/公式", f"Id = (flux/(Lq-Ld))*(1-sqrt(1+...))", "IPM 专用"),
            ("弱磁", "电压闭环", f"Udc > {voltage*0.577:.0f}V 时生效", "高速区"),
        ]
    elif motor == "IM":
        suggestions = [
            ("电流环", "PI (零极点对消)", "Kp = sigma*Ls*wc, Ki = Rs*wc", "带宽 300-500 Hz"),
            ("速度环", "PI (对称最优)", "Kp = w*J/Kt, Ki = Kp*w/4", "带宽 20-50 Hz"),
            ("磁链观测", "全阶观测器", "极点位于电机极点左侧 2-3 倍", "或 Gopinath 模型"),
        ]
    elif motor == "BLDC":
        suggestions = [
            ("电流环", "PI", "Kp=0.5*Vdc/Imax, Ki=Kp*100", "梯形波控制"),
            ("速度环", "PI", "Kp=0.01*Vdc/Ke, Ki=Kp*50", "Hall 传感器反馈"),
        ]

    return (
        f"控制策略建议: {motor} @ {speed:.0f} RPM\n"
        f"{'='*50}\n" +
        "\n".join(
            f"【{s[0]}】{s[1]}\n"
            f"  参数: {s[2]}\n"
            f"  备注: {s[3]}\n"
            for s in suggestions
        )
    )


# ============================================================
# 知识库工具
# ============================================================

def _knowledge_search(args: dict) -> str:
    """搜索本地知识库"""
    query = args["query"]
    top_k = int(args.get("top_k", 8))
    kb = get_kb()
    return kb.search(query, top_k)


def _knowledge_add(args: dict) -> str:
    """向知识库添加笔记"""
    title = args["title"]
    content = args["content"]
    tags = args.get("tags", "")
    kb = get_kb()
    return kb.add_note(title, content, tags)


def _knowledge_list(args: dict) -> str:
    """列出知识库文档"""
    kb = get_kb()
    return kb.list_documents()


def _knowledge_import(args: dict) -> str:
    """导入文件到知识库"""
    filepath = args["filepath"]
    tags = args.get("tags", "")
    kb = get_kb()
    return kb.import_file(filepath, tags)


def _knowledge_rebuild(args: dict) -> str:
    """重建知识库索引"""
    kb = get_kb()
    return kb.rebuild()


# ============================================================
# 联网搜索
# ============================================================

def _web_search(args: dict) -> str:
    """使用 DuckDuckGo 搜索网页"""
    query = args["query"]
    max_results = min(int(args.get("max_results", 5)), 10)

    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(r)

        if not results:
            return f"未找到相关结果: '{query}'\n提示: 请尝试不同的关键词"

        output = [f"搜索结果 ({len(results)} 条): '{query}'\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            href = r.get("href", "")
            body = r.get("body", "")[:200]
            output.append(f"{i}. {title}\n   URL: {href}\n   {body}\n")

        return "\n".join(output)
    except ImportError:
        return "错误: 请安装 ddgs 库: pip install ddgs"
    except Exception as e:
        return f"搜索失败: {e}"


def _web_fetch(args: dict) -> str:
    """抓取网页文本内容"""
    url = args["url"]

    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除脚本和样式
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # 清理空行
        lines = [line for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)

        if len(text) > 4000:
            text = text[:4000] + "\n\n... (内容过长已截断)"

        return f"网页内容: {url}\n{len(text)} 字符\n\n{text}"
    except ImportError as e:
        return f"错误: 缺少依赖库: {e}"
    except Exception as e:
        return f"抓取失败: {e}"


def _download_file(args: dict) -> str:
    """下载文件到安全目录。"""
    url = args.get("url", "")
    path_str = args.get("path", "")
    if not url or not path_str:
        return "错误: 缺少 url 或 path 参数"

    path = _resolve_path(path_str)
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        with requests.get(url, headers=headers, timeout=30, stream=True) as resp:
            resp.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 80 * 1024 * 1024:
                        return "下载失败: 文件超过 80MB 安全上限"
                    f.write(chunk)
        return f"文件已下载: {path} ({total} bytes)"
    except ImportError as e:
        return f"错误: 缺少依赖库: {e}"
    except Exception as e:
        return f"下载失败: {e}"


# ============================================================
# 新增工具: 反思 / 记忆 / 调度器
# ============================================================

def _reflect_tool(args: dict) -> str:
    """触发自我反思（简化版，不调用 LLM，基于规则检查）"""
    task_summary = args.get("task_summary", "")

    checks = [
        "检查: 回答是否完整覆盖了用户需求",
        "检查: 工具调用是否有失败被忽略",
        "检查: 代码输出是否可编译运行（如适用）",
        "检查: 是否给出了明确的结论/结果（不只是计划）",
        "检查: 文件操作是否已验证文件存在（如适用）",
    ]

    return (
        f"自我反思检查清单:\n" +
        "\n".join(f"  {c}" for c in checks) +
        f"\n\n任务摘要: {task_summary}\n"
        f"提示: 如需深度反思，请使用 graph_agent 的 reflect 节点（自动触发）。"
    )


def _memory_search(args: dict) -> str:
    """搜索对话记忆"""
    query = args.get("query", "")
    max_results = int(args.get("max_results", 5))
    if not query:
        return "错误: 缺少 query 参数"
    from memory import get_memory
    mem = get_memory()
    return mem.search_memory(query, max_results)


def _memory_stats(args: dict) -> str:
    """查看记忆统计"""
    from memory import get_memory
    mem = get_memory()
    return mem.get_stats()


def _scheduler_status(args: dict) -> str:
    """查看调度器状态"""
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        return sched.get_status()
    except Exception as e:
        return f"调度器状态获取失败: {e}"


def _spawn_agent(args: dict) -> str:
    """启动专业子 Agent"""
    agent_id = args.get("agent_id", "")
    task = args.get("task", "")
    if not agent_id or not task:
        return "错误: 缺少 agent_id 或 task 参数"
    from agents import spawn_agent
    return spawn_agent(agent_id, task)


def _list_agents(args: dict) -> str:
    """列出可用的专业 Agent"""
    from agents import list_agents
    return list_agents()
