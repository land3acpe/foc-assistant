"""搜索工具：search_code, find_files, list_directory, project_overview, extract_symbols"""

import json
import os
from pathlib import Path
from config import PROJECT_ROOT, DESKTOP
from tools._common import _resolve_path, _should_skip_dir, _iter_candidate_files, TEXT_SUFFIXES

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
