"""命令执行工具：run_command, compile_ccs"""

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Optional

from config import DANGEROUS_PATTERNS, DANGER_CONFIRM, PROJECT_ROOT
from tools._common import _resolve_path, _is_dangerous, RUN_COMMAND_ALLOWED

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

    # 防御命令注入：拒绝 shell 元字符
    _INJECTION_CHARS = set('|&;>`$')
    if _INJECTION_CHARS.intersection(command):
        return f"命令包含潜在注入字符，已拒绝: {command}"

    try:
        cmd_list = shlex.split(command, posix=False)
        result = subprocess.run(
            cmd_list,
            shell=False,
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
        # 尝试搜索（使用 shell=False + 显式列表）
        try:
            result = subprocess.run(
                ["cmd", "/c", "dir", "/s", "/b", r"d:\*eclipsec.exe"],
                shell=False, capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                ccs_found = result.stdout.strip().split("\n")[0]
        except Exception:
            pass

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
        # 注入防护：build_config 只允许字母数字和下划线
        if not re.match(r'^[A-Za-z0-9_]+$', build_config):
            return f"非法构建配置名: {build_config}"
        cmd_list = [
            ccs_found, "-noSplash",
            "-data", workspace,
            "-application", "com.ti.ccstudio.apps.projectBuild",
            "-ccs.projects", str(project_path.name),
            "-ccs.configuration", build_config,
        ]
        result = subprocess.run(cmd_list, shell=False, capture_output=True, text=True, timeout=120, cwd=str(project_path))
        output = result.stdout + result.stderr
        return f"CCS 编译 ({build_config}):\n{'='*50}\n{output[-4000:]}" if output else f"编译完成，退出码: {result.returncode}"
    except Exception as e:
        return f"CCS 编译失败: {e}"
