"""FOC-Assistant 工具集 — 共享常量和辅助函数"""

import os
from pathlib import Path
from typing import Optional

import chardet

from config import DANGEROUS_PATTERNS, DANGER_CONFIRM, DESKTOP, PROJECT_ROOT

TOOL_ROOT = Path(__file__).resolve().parent.parent
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


def _iter_candidate_files(directory, file_filter: str = "", skip_build_dirs: bool = False):
    import fnmatch
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not _should_skip_dir(d, skip_build_dirs)]
        for fname in files:
            if file_filter and not fnmatch.fnmatch(fname, file_filter):
                continue
            yield Path(root) / fname
