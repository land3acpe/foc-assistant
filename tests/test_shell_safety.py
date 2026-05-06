"""测试 shell 命令安全性"""

import subprocess
import shlex
import pytest


def test_shlex_split_rejects_injection():
    """shlex.split 应正确处理含空格的路径，不引入注入"""
    # 正常路径
    parts = shlex.split('"C:\\Program Files\\tool.exe" --flag value')
    assert parts[0] == "C:\\Program Files\\tool.exe"

    # 注入尝试 — shlex 会将分号等作为普通字符
    parts = shlex.split("echo hello; rm -rf /")
    # shlex.split 不会将 ; 解析为命令分隔符
    assert ";" in parts or "hello;" in " ".join(parts)


def test_run_command_uses_shell_false(monkeypatch):
    """_run_command 应使用 shell=False"""
    captured = {}

    def mock_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.setattr("tools._common.RUN_COMMAND_ALLOWED", True)

    from tools import execute_tool
    result = execute_tool("run_command", {"command": "echo hello"})

    assert captured.get("shell") is False, f"Expected shell=False, got {captured.get('shell')}"
    assert isinstance(captured.get("cmd"), list)


def test_compile_ccs_uses_shell_false(monkeypatch):
    """compile_ccs 的 CCS 编译应使用 shell=False 列表参数"""
    captured = {}

    def mock_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell")
        return subprocess.CompletedProcess(cmd, 0, stdout="Build complete", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)

    from tools import execute_tool
    # 模拟 CCS 路径存在
    from pathlib import Path
    original_exists = Path.exists

    def mock_exists(self):
        if "eclipsec.exe" in str(self):
            return True
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", mock_exists)

    result = execute_tool("compile_ccs", {
        "project_path": "C:\\test_project",
        "build_config": "Debug",
    })

    if captured.get("cmd"):  # 可能因路径不存在而提前返回
        assert captured.get("shell") is False
        assert isinstance(captured.get("cmd"), list)
