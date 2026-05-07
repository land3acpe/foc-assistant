"""工具注册表和分发器"""

import json
import inspect
from typing import Callable, Optional

# 经验库工具（延迟初始化）
_experience_executor = None

def _get_experience_executor():
    global _experience_executor
    if _experience_executor is None:
        import os
        from experience.experience_store import ExperienceStore
        from experience.experience_tools import ExperienceToolExecutor
        from pathlib import Path as _Path
        db_path = str(_Path(__file__).resolve().parent.parent / "data" / "experience.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        store = ExperienceStore(db_path)
        _experience_executor = ExperienceToolExecutor(store)
    return _experience_executor


# 工具定义列表（OpenAI function calling 格式）
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
    {
        "type": "function",
        "function": {
            "name": "handoff_to_agent",
            "description": "声明式 Handoff：将子任务交给最合适的专家 Agent 自动处理。不需要指定 Agent ID，系统会根据任务内容自动匹配。适合不确定该用哪个 Agent 时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "要委派的子任务描述",
                    },
                    "prefer_agent": {
                        "type": "string",
                        "description": "可选，优先使用的 Agent ID。不填则自动选择。",
                        "enum": ["code_analyzer", "waveform_analyzer", "controller_designer", "research_agent", "debug_helper"],
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_model",
            "description": "切换当前使用的 AI 模型。可以切换到不同的模型（如 MiMo、DeepSeek 等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "模型 ID，如 'deepseek-v4-pro', 'mimo-v2.5', 'mimo-v2.5-pro', 'mimo-api', 'ollama-local', 'gpt-4o'",
                    },
                },
                "required": ["model_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_models",
            "description": "列出所有可用的 AI 模型及其配置信息。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_summary",
            "description": "查看当前会话的 Tracing 摘要：LLM 调用次数、工具调用次数、耗时等。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_experience",
            "description": "回忆经验库中的相关经验。不传参数返回索引；传 category 返回该分类条目；传 query 全文搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "经验分类名称，如 'PI调参'、'SVPWM调试'。不传则返回索引。",
                    },
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，用于全文检索经验内容。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_experience",
            "description": "记录一条经验到经验库。用于沉淀调试教训、参数整定技巧、故障排除方法等可复用知识。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "经验分类，如 'PI调参'、'SVPWM调试'、'EMC整改'。",
                    },
                    "content": {
                        "type": "string",
                        "description": "经验内容，要求具体、可操作。",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签列表，如 ['电流环', 'PI', '振荡']。",
                    },
                    "source": {
                        "type": "string",
                        "description": "经验来源，如 'TI E2E 论坛'、'实测'。",
                    },
                },
                "required": ["category", "content"],
            },
        },
    },
]



# 分发表：工具名 → 实现函数
_TOOL_DISPATCH: dict[str, Callable] = {}


def _register():
    """延迟注册所有工具实现"""
    from tools._file_ops import _read_file, _read_many_files, _write_file, _edit_file
    from tools._search import _search_code, _find_files, _list_directory, _project_overview, _extract_symbols
    from tools._analysis import (
        _analyze_csv, _calculate_pi_params, _generate_svpwm_table,
        _read_matlab_script, _suggest_controller_params, _parse_slx_model,
    )
    from tools._analysis import _search_papers
    from tools._knowledge import _knowledge_search, _knowledge_add, _knowledge_list, _knowledge_import, _knowledge_rebuild
    from tools._web import _web_search, _web_fetch, _download_file
    from tools._agent_tools import (
        _reflect_tool, _scheduler_status,
        _spawn_agent, _list_agents, _handoff_to_agent, _switch_model,
        _list_models, _trace_summary,
    )
    from tools._command import _run_command, _compile_ccs

    _TOOL_DISPATCH.update({
        "read_file": _read_file,
        "read_many_files": _read_many_files,
        "write_file": _write_file,
        "edit_file": _edit_file,
        "search_code": _search_code,
        "find_files": _find_files,
        "list_directory": _list_directory,
        "project_overview": _project_overview,
        "extract_symbols": _extract_symbols,
        "run_command": _run_command,
        "analyze_csv": _analyze_csv,
        "search_papers": _search_papers,
        "parse_slx_model": _parse_slx_model,
        "calculate_pi_params": _calculate_pi_params,
        "generate_svpwm_table": _generate_svpwm_table,
        "read_matlab_script": _read_matlab_script,
        "compile_ccs": _compile_ccs,
        "suggest_controller_params": _suggest_controller_params,
        "knowledge_search": _knowledge_search,
        "knowledge_add": _knowledge_add,
        "knowledge_list": _knowledge_list,
        "knowledge_import": _knowledge_import,
        "knowledge_rebuild": _knowledge_rebuild,
        "web_search": _web_search,
        "web_fetch": _web_fetch,
        "download_file": _download_file,
        "reflect": _reflect_tool,
        "scheduler_status": _scheduler_status,
        "spawn_agent": _spawn_agent,
        "list_agents": _list_agents,
        "handoff_to_agent": _handoff_to_agent,
        "switch_model": _switch_model,
        "list_models": _list_models,
        "trace_summary": _trace_summary,
    })


def execute_tool(name: str, args: dict, danger_callback: Optional[Callable[[str], bool]] = None) -> str:
    """执行单个工具调用。"""
    if not _TOOL_DISPATCH:
        _register()

    try:
        if name == "task_complete":
            return json.dumps({"status": "complete", "summary": args.get("summary", "")})

        if name in ("recall_experience", "record_experience"):
            return _get_experience_executor().execute(name, args)

        func = _TOOL_DISPATCH.get(name)
        if func is None:
            return f"未知工具: {name}"

        sig = inspect.signature(func)
        if "danger_callback" in sig.parameters:
            return func(args, danger_callback=danger_callback)
        return func(args)
    except PermissionError as e:
        return f"权限拒绝: {e}"
