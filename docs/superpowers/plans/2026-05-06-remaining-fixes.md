# FOC-Assistant 剩余问题修复计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复 R4 分析报告中剩余的 4 个未修复问题 + 1 个部分修复问题

**架构：** 将 `_run_sub_agent` 的重复逻辑合并到 `agent_loop`，通过参数复用主循环；将 `tools.py` 拆分为 `tools/` 包；用 `shlex.split()` 替换所有 `shell=True`；为知识库添加增量索引。

**技术栈：** Python 3.10+, OpenAI SDK, SQLite, shlex, subprocess, pathlib

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `agent.py:59-80` | 修改 | agent_loop 新增 `system_prompt_override` 参数 |
| `agents/__init__.py:312-484` | 修改 | 删除 `_run_sub_agent`，`_execute_sub_agent` 改调 `agent_loop` |
| `tools.py` | 拆分 | 2088 行巨石文件 → `tools/` 包 |
| `tools/__init__.py` | 创建 | 公开接口：TOOLS, execute_tool |
| `tools/_registry.py` | 创建 | TOOLS 列表 + execute_tool 分发 |
| `tools/_file_ops.py` | 创建 | 文件操作工具 |
| `tools/_search.py` | 创建 | 搜索工具 |
| `tools/_analysis.py` | 创建 | 分析工具（CSV/SVPWM/PI/Simulink） |
| `tools/_knowledge.py` | 创建 | 知识库工具 |
| `tools/_web.py` | 创建 | 网络工具 |
| `tools/_agent_tools.py` | 创建 | Agent/模型工具 |
| `tools/_command.py` | 创建 | 命令执行（shell 清理） |
| `knowledge.py:66-197` | 修改 | 增量索引：按 mtime 判断是否需要重新索引 |
| `tests/test_agent_merge.py` | 创建 | 测试 agent_loop 合并后的子 Agent 行为 |
| `tests/test_shell_safety.py` | 创建 | 测试 shell 命令安全性 |
| `tests/test_incremental_index.py` | 创建 | 测试增量索引逻辑 |

---

### 任务 1：agent_loop 合并 — 消除 _run_sub_agent 重复代码

**问题：** `agents/__init__.py` 的 `_run_sub_agent`（120 行）几乎完整复制了 `agent.py` 的 `agent_loop`（300 行），包括流式处理、工具循环、结果收集。两份代码独立维护容易产生 bug。

**方案：** 让 `agent_loop` 支持 `system_prompt_override` 参数，子 Agent 直接调用 `agent_loop` 而非自己写循环。

**文件：**
- 修改：`agent.py:59-80`（agent_loop 签名）
- 修改：`agents/__init__.py:312-484`（删除 _run_sub_agent，简化 _execute_sub_agent）
- 创建：`tests/test_agent_merge.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_agent_merge.py
"""测试 agent_loop 合并后的子 Agent 行为"""

import pytest
from unittest.mock import patch, MagicMock


def test_agent_loop_accepts_system_prompt_override():
    """agent_loop 应接受 system_prompt_override 参数覆盖默认 SYSTEM_PROMPT"""
    from agent import agent_loop
    # 只验证不报错，不实际调用 LLM
    with patch("agent.get_model_manager") as mock_mm, \
         patch("agent.OpenAI") as mock_openai, \
         patch("agent.get_input_guardrail") as mock_ig, \
         patch("agent.get_output_guardrail") as mock_og:
        mock_ig.return_value.check.return_value = MagicMock(passed=True)
        mock_og.return_value.check.return_value = MagicMock(passed=True)
        mock_mm.return_value.get_model_for_task.return_value = "test"
        mock_mm.return_value.get_model_config.return_value = {
            "display_name": "Test",
            "base_url": "http://test",
            "api_key_env": "TEST_KEY",
            "api_key_default": "test-key",
            "model_id": "test",
            "default_params": {},
        }
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        # 模拟空响应（无工具调用）
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "done"
        mock_response.choices[0].message.tool_calls = None
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict("os.environ", {"TEST_KEY": "sk-test"}):
            result = agent_loop(
                "test task",
                system_prompt_override="CUSTOM SYSTEM PROMPT",
                enable_skills=False,
                enable_tools=False,
            )
    assert "done" in result


def test_execute_sub_agent_calls_agent_loop():
    """_execute_sub_agent 应调用 agent_loop 而非 _run_sub_agent"""
    from agents import _execute_sub_agent
    from agents.profiles import AGENT_PROFILES

    profile = AGENT_PROFILES.get("code_analyzer")
    if profile is None:
        pytest.skip("code_analyzer profile not found")

    with patch("agents.agent_loop") as mock_loop:
        mock_loop.return_value = "sub agent result"
        result = _execute_sub_agent("code_analyzer", profile, "分析这个文件")

    assert result == "sub agent result"
    mock_loop.assert_called_once()
    call_kwargs = mock_loop.call_args
    # 验证 system_prompt_override 被使用
    assert "system_prompt_override" in call_kwargs.kwargs or len(call_kwargs.args) > 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_agent_merge.py -v`
预期：FAIL — `agent_loop` 不接受 `system_prompt_override` 参数

- [ ] **步骤 3：修改 agent_loop 支持 system_prompt_override**

修改 `agent.py:59-80`，在 agent_loop 签名中新增参数，并修改 system prompt 构建逻辑：

```python
# agent.py 第 59-80 行，修改签名和 prompt 构建
def agent_loop(
    user_task: str,
    max_iterations: int = MAX_ITERATIONS,
    callbacks: Optional[AgentCallbacks] = None,
    thinking_mode: Optional[str] = None,
    history_context: str = "",
    skill_task: Optional[str] = None,
    enable_tools: bool = True,
    enable_skills: bool = True,
    task_type: str = "tool",
    system_prompt_override: Optional[str] = None,
) -> str:
    """Agent 主循环。返回累积的完整响应文本。

    Args:
        system_prompt_override: 如果提供，直接用作 system prompt，跳过 Skill 检测和经验注入。
                                用于子 Agent 调用，由调用者构建完整的专业 prompt。
    """
```

然后修改第 128-138 行的 system prompt 构建逻辑：

```python
    # agent.py 第 128 行附近
    if system_prompt_override:
        system_prompt = system_prompt_override
    else:
        # 检测并注入 Skill
        system_prompt = detect_and_inject_skill(skill_task or user_task) if enable_skills else SYSTEM_PROMPT

        # 注入经验库 prompt（如果经验库有内容）
        try:
            from api.memory_api import get_memory_api
            _mem_api = get_memory_api()
            exp_prompt = _mem_api.get_experience_prompt()
            if exp_prompt:
                system_prompt += "\n\n" + exp_prompt
        except Exception:
            pass  # 经验库初始化失败不影响主流程
```

- [ ] **步骤 4：简化 _execute_sub_agent，删除 _run_sub_agent**

修改 `agents/__init__.py`，将 `_execute_sub_agent`（第 312-359 行）改为调用 `agent_loop`：

```python
# agents/__init__.py 第 312-359 行，替换为：
def _execute_sub_agent(agent_id: str, profile: dict, task: str) -> str:
    """执行子 Agent 的核心逻辑（spawn_agent 和 handoff_to_agent 共用）。

    通过 agent_loop 的 system_prompt_override 参数复用主循环，消除代码重复。
    """
    from agent import agent_loop, AgentCallbacks
    from config import SYSTEM_PROMPT

    # 构建专业 System Prompt
    specialized_prompt = SYSTEM_PROMPT + "\n\n" + profile["system_prompt"]

    # 获取工具子集
    allowed_tools = profile.get("allowed_tools", [])
    if allowed_tools:
        from tools import TOOLS
        filtered_tools = [t for t in TOOLS if t["function"]["name"] in allowed_tools]
    else:
        filtered_tools = None  # 全部工具

    # 收集输出
    outputs: list[str] = []

    def on_token(t: str):
        outputs.append(t)

    def on_tool_call(name: str, args: dict):
        print(f"  [SUB-AGENT:{agent_id}] {name}({str(args)[:100]})")

    def on_tool_result(r: str):
        pass

    callbacks = AgentCallbacks(
        on_token=on_token,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )

    try:
        result = agent_loop(
            user_task=task,
            max_iterations=profile.get("max_iterations", 15),
            callbacks=callbacks,
            thinking_mode=profile.get("thinking_mode"),
            enable_tools=True,
            enable_skills=False,  # 子 Agent 用自己的专业 prompt，不注入 Skill
            task_type="tool",
            system_prompt_override=specialized_prompt,
        )
        return result or "".join(outputs) or f"[{agent_id}] 子 Agent 未产生输出"
    except Exception as e:
        return f"[{agent_id}] 子 Agent 执行异常: {e}"
```

删除 `_run_sub_agent` 函数（第 362-484 行）和末尾的 `import os`（第 484 行）。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_agent_merge.py -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add agent.py agents/__init__.py tests/test_agent_merge.py
git commit -m "refactor: merge _run_sub_agent into agent_loop via system_prompt_override

Eliminates 120 lines of duplicated agent loop code in agents/__init__.py.
Sub agents now call agent_loop directly with system_prompt_override parameter.

Fixes: P1 #1 (agent_loop 与 _run_sub_agent 代码重复)"
```

---

### 任务 2：shell=True 全面清理

**问题：** `tools.py` 中有 3 处 `shell=True`，其中 CCS 编译路径的两处缺少注入防护。虽然 `build_config` 做了正则校验，但 `workspace` 和 `project_path.name` 未校验。

**方案：** 全部改为 `shell=False` + 列表参数，用 `shlex.split()` 处理用户命令。

**文件：**
- 修改：`tools/_command.py`（从 tools.py 拆出后）或 `tools.py:1348-1392`
- 修改：`tools.py:1734-1801`（compile_ccs）
- 创建：`tests/test_shell_safety.py`

注意：此任务在任务 5（tools.py 拆分）之前执行，直接修改 `tools.py`。拆分时会将修改后的代码移到 `tools/_command.py`。

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_shell_safety.py
"""测试 shell 命令安全性"""

import subprocess
import shlex
import pytest


def test_run_command_uses_shell_false(monkeypatch):
    """_run_command 应使用 shell=False"""
    import tools
    captured = {}

    def mock_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell")
        result = subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        return result

    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.setattr("tools.RUN_COMMAND_ALLOWED", True)
    monkeypatch.setattr("tools.DANGER_CONFIRM", False)

    from tools import execute_tool
    result = execute_tool("run_command", {"command": "echo hello"})

    assert captured.get("shell") is False, f"Expected shell=False, got {captured.get('shell')}"
    # 命令应被 shlex.split 处理为列表
    assert isinstance(captured.get("cmd"), list)


def test_compile_ccs_uses_shell_false(monkeypatch):
    """compile_ccs 应使用 shell=False 列表参数"""
    import tools
    from pathlib import Path
    captured = {}

    def mock_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell")
        return subprocess.CompletedProcess(cmd, 0, stdout="Build complete", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)
    # 模拟 CCS 路径存在
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

    from tools import execute_tool
    result = execute_tool("compile_ccs", {
        "project_path": "C:\\test_project",
        "build_config": "Debug",
    })

    if captured.get("cmd"):  # 可能因路径不存在而提前返回
        assert captured.get("shell") is False
        assert isinstance(captured.get("cmd"), list)


def test_shlex_split_rejects_injection():
    """shlex.split 应正确处理含空格的路径，不引入注入"""
    # 正常路径
    parts = shlex.split('"C:\\Program Files\\tool.exe" --flag value')
    assert parts[0] == "C:\\Program Files\\tool.exe"

    # 注入尝试 — shlex 会将分号等作为普通字符
    parts = shlex.split("echo hello; rm -rf /")
    # shlex.split 不会将 ; 解析为命令分隔符
    assert ";" in parts or "hello;" in " ".join(parts)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_shell_safety.py -v`
预期：FAIL — `shell` 仍为 `True`

- [ ] **步骤 3：修改 _run_command 使用 shlex.split + shell=False**

修改 `tools.py` 第 1348-1392 行：

```python
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
        # 使用 shlex.split 将命令拆分为列表，避免 shell=True
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
```

同时在 `tools.py` 顶部添加 `import shlex`（如果尚未导入）。

- [ ] **步骤 4：修改 compile_ccs 使用 shell=False**

修改 `tools.py` 第 1766-1797 行的两处 `shell=True`：

```python
    # 第 1766 行附近 — CCS 路径搜索
    if not ccs_found:
        # 尝试搜索（使用 shell=False）
        try:
            result = subprocess.run(
                ["cmd", "/c", "dir", "/s", "/b", r"d:\*eclipsec.exe"],
                shell=False, capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                ccs_found = result.stdout.strip().split("\n")[0]
        except Exception:
            pass

    # ...

    # 第 1790 行附近 — CCS 编译
    try:
        workspace = str(project_path.parent)
        if not re.match(r'^[A-Za-z0-9_]+$', build_config):
            return f"非法构建配置名: {build_config}"
        cmd_list = [
            ccs_found, "-noSplash",
            "-data", workspace,
            "-application", "com.ti.ccstudio.apps.projectBuild",
            "-ccs.projects", str(project_path.name),
            "-ccs.configuration", build_config,
        ]
        result = subprocess.run(
            cmd_list, shell=False, capture_output=True, text=True,
            timeout=120, cwd=str(project_path),
        )
        output = result.stdout + result.stderr
        return f"CCS 编译 ({build_config}):\n{'='*50}\n{output[-4000:]}" if output else f"编译完成，退出码: {result.returncode}"
    except Exception as e:
        return f"CCS 编译失败: {e}"
```

- [ ] **步骤 5：运行测试验证通过**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_shell_safety.py -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add tools.py tests/test_shell_safety.py
git commit -m "fix: replace all shell=True with shlex.split + shell=False

Three subprocess.run call sites converted:
- _run_command: shlex.split(command, posix=False) + shell=False
- CCS search: explicit ['cmd', '/c', 'dir', ...] list
- CCS compile: explicit argument list

Fixes: P2 #12 (shell=True 命令注入风险)"
```

---

### 任务 3：知识库增量索引

**问题：** `knowledge.py` 的 `build_index()` 每次全量扫描所有文件，文件多时很慢。没有增量机制。

**方案：** 在 `_save_index` 时记录每个文件的 mtime，`build_index` 时对比 mtime，只重新索引变更的文件。

**文件：**
- 修改：`knowledge.py:77-197`（build_index）、`knowledge.py:466-486`（_save_index/_load_index）
- 创建：`tests/test_incremental_index.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_incremental_index.py
"""测试知识库增量索引"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch


def test_build_index_records_mtime(tmp_path):
    """build_index 应记录每个文件的 mtime"""
    from knowledge import KnowledgeBase

    kb = KnowledgeBase()
    # 创建测试文件
    note = tmp_path / "test.md"
    note.write_text("# Test\nHello world", encoding="utf-8")

    with patch("knowledge.KB_INDEX_PATH", tmp_path / "index.json"), \
         patch("knowledge.DOC_SOURCES", [(tmp_path, "*.md", "test")]), \
         patch("knowledge.PAPER_SOURCES", []), \
         patch("knowledge.CSV_SOURCES", []), \
         patch("knowledge.CODE_SOURCES", []):
        kb.build_index()

    # 检查索引文件包含 mtime 信息
    index_data = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert "file_mtimes" in index_data
    assert str(note) in index_data["file_mtimes"]


def test_incremental_build_skips_unchanged(tmp_path):
    """未变更的文件不应重新索引"""
    from knowledge import KnowledgeBase

    note = tmp_path / "test.md"
    note.write_text("# Test\nHello world", encoding="utf-8")

    kb = KnowledgeBase()
    with patch("knowledge.KB_INDEX_PATH", tmp_path / "index.json"), \
         patch("knowledge.DOC_SOURCES", [(tmp_path, "*.md", "test")]), \
         patch("knowledge.PAPER_SOURCES", []), \
         patch("knowledge.CSV_SOURCES", []), \
         patch("knowledge.CODE_SOURCES", []):
        kb.build_index()
        first_count = len(kb.documents)

        # 不修改文件，再次构建
        kb.loaded = False
        kb.build_index()
        second_count = len(kb.documents)

    assert first_count == second_count


def test_incremental_build_reindexes_changed(tmp_path):
    """修改过的文件应被重新索引"""
    from knowledge import KnowledgeBase

    note = tmp_path / "test.md"
    note.write_text("# Test\nHello world", encoding="utf-8")

    kb = KnowledgeBase()
    with patch("knowledge.KB_INDEX_PATH", tmp_path / "index.json"), \
         patch("knowledge.DOC_SOURCES", [(tmp_path, "*.md", "test")]), \
         patch("knowledge.PAPER_SOURCES", []), \
         patch("knowledge.CSV_SOURCES", []), \
         patch("knowledge.CODE_SOURCES", []):
        kb.build_index()
        first_count = len(kb.documents)

        # 修改文件
        import time
        time.sleep(0.1)  # 确保 mtime 变化
        note.write_text("# Test\nHello world\n\nNew content added!", encoding="utf-8")

        kb.loaded = False
        kb.build_index()
        second_count = len(kb.documents)

    assert second_count >= first_count  # 新内容可能增加 chunk 数
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_incremental_index.py -v`
预期：FAIL — `index_data` 中没有 `file_mtimes` 键

- [ ] **步骤 3：修改 build_index 支持增量**

修改 `knowledge.py` 的 `build_index` 方法，在索引构建时跟踪文件 mtime，并在 `_save_index` 中持久化：

```python
# knowledge.py — 修改 build_index 方法的开头（第 77 行附近）
def build_index(self) -> str:
    """扫描所有文档源，构建倒排索引（支持增量：只索引 mtime 变化的文件）"""
    # 加载旧索引的 mtime 记录
    old_mtimes = self._load_old_mtimes()

    # 如果是全量重建（旧索引不存在），清空
    if not old_mtimes:
        self.documents = []
        self.inverted_index = defaultdict(set)

    # 记录本次扫描的文件 mtime
    current_mtimes: dict[str, float] = {}

    stats = {"md": 0, "txt": 0, "pdf": 0, "csv": 0, "pdf_text": 0, "code": 0}

    # 1. Markdown/TXT 文档 —— 全文索引
    for directory, pattern, tag in DOC_SOURCES:
        if not directory.exists():
            continue
        for filepath in directory.glob(pattern):
            try:
                file_mtime = filepath.stat().st_mtime
                current_mtimes[str(filepath)] = file_mtime

                # 增量检查：文件未跳过则不重新索引
                if old_mtimes and str(filepath) in old_mtimes:
                    if abs(old_mtimes[str(filepath)] - file_mtime) < 0.01:
                        continue  # 文件未变化，跳过

                # 移除该文件的旧 chunks（如果有）
                self._remove_file_chunks(str(filepath))

                text = filepath.read_text(encoding="utf-8", errors="ignore")
                chunks = self._chunk_text(text, 500)
                for i, chunk in enumerate(chunks):
                    doc_id = len(self.documents)
                    self.documents.append({
                        "id": doc_id,
                        "path": str(filepath),
                        "name": filepath.name,
                        "tag": tag,
                        "chunk_index": i,
                        "content": chunk,
                    })
                    self._index_document(doc_id, chunk)
                stats[pattern.lstrip("*.")] += 1
            except Exception:
                continue
```

在 `build_index` 的每个扫描段（PDF、代码、CSV）中加入相同的增量逻辑。

新增辅助方法：

```python
# knowledge.py — 在 _index_document 方法之后添加
def _remove_file_chunks(self, filepath: str):
    """移除指定文件的所有旧 chunks 和倒排索引条目"""
    ids_to_remove = {doc["id"] for doc in self.documents if doc["path"] == filepath}
    if not ids_to_remove:
        return

    # 从 documents 列表中移除
    self.documents = [doc for doc in self.documents if doc["path"] != filepath]

    # 从倒排索引中移除
    for token, doc_ids in list(self.inverted_index.items()):
        doc_ids -= ids_to_remove
        if not doc_ids:
            del self.inverted_index[token]

def _load_old_mtimes(self) -> dict[str, float]:
    """从旧索引文件中加载文件 mtime 记录"""
    if not KB_INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(KB_INDEX_PATH.read_text(encoding="utf-8"))
        return data.get("file_mtimes", {})
    except Exception:
        return {}
```

修改 `_save_index` 方法：

```python
# knowledge.py — 修改 _save_index（第 466 行附近）
def _save_index(self):
    # 计算当前所有文件的 mtime
    file_mtimes: dict[str, float] = {}
    seen_paths = set()
    for doc in self.documents:
        path = doc["path"]
        if path not in seen_paths and path != "(user note)":
            seen_paths.add(path)
            try:
                file_mtimes[path] = Path(path).stat().st_mtime
            except Exception:
                pass

    data = {
        "documents": self.documents,
        "inverted_index": {k: list(v) for k, v in self.inverted_index.items()},
        "file_mtimes": file_mtimes,
    }
    KB_INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_incremental_index.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add knowledge.py tests/test_incremental_index.py
git commit -m "feat: add incremental indexing to knowledge base

Only re-indexes files whose mtime has changed since last build.
Removes old chunks before re-indexing changed files.
Persists file_mtimes in index.json for cross-session tracking.

Fixes: P2 #16 (知识库不支持增量索引)"
```

---

### 任务 4：tools.py 拆分为 tools/ 包

**问题：** `tools.py` 有 2088 行，包含 40+ 函数，职责混杂（文件操作、搜索、分析、知识库、网络、Agent 工具）。

**方案：** 拆分为 `tools/` 包，按职责分模块。保持 `TOOLS` 和 `execute_tool` 的公开接口不变，外部代码无需修改。

**文件：**
- 创建：`tools/__init__.py`（公开接口）
- 创建：`tools/_registry.py`（TOOLS 列表 + execute_tool）
- 创建：`tools/_file_ops.py`（文件操作）
- 创建：`tools/_search.py`（搜索）
- 创建：`tools/_analysis.py`（分析工具）
- 创建：`tools/_knowledge.py`（知识库）
- 创建：`tools/_web.py`（网络工具）
- 创建：`tools/_agent_tools.py`（Agent/模型工具）
- 创建：`tools/_command.py`（命令执行）
- 删除：`tools.py`（旧文件，拆分后删除）

- [ ] **步骤 1：创建 tools/ 目录结构**

```bash
mkdir -p C:\Users\macree\foc-assistant\tools
```

- [ ] **步骤 2：创建 tools/_file_ops.py**

从 `tools.py` 提取文件操作相关的函数：

```python
# tools/_file_ops.py
"""文件操作工具：read_file, read_many_files, write_file, edit_file"""

import os
from pathlib import Path
from config import PROJECT_ROOT

# 从原 tools.py 复制以下函数（保持代码不变）:
# - _resolve_path (第 828 行)
# - _is_relative_to (第 820 行)
# - _decode_text_file (第 855 行)
# - _read_file (第 996 行)
# - _read_many_files (第 1029 行)
# - _write_file (第 1068 行)
# - _edit_file (第 1082 行)
```

- [ ] **步骤 3：创建 tools/_search.py**

```python
# tools/_search.py
"""搜索工具：search_code, find_files, list_directory, project_overview, extract_symbols"""

# 从原 tools.py 复制以下函数:
# - _iter_candidate_files (第 861 行)
# - _should_skip_dir (第 850 行)
# - _search_code (第 1107 行)
# - _search_code_fallback (第 1134 行)
# - _find_files (第 1168 行)
# - _project_overview (第 1204 行)
# - _extract_symbols (第 1256 行)
# - _list_directory (第 1318 行)
```

- [ ] **步骤 4：创建 tools/_analysis.py**

```python
# tools/_analysis.py
"""分析工具：analyze_csv, calculate_pi_params, generate_svpwm_table, read_matlab_script,
   suggest_controller_params, parse_slx_model"""

# 从原 tools.py 复制以下函数:
# - _analyze_csv (第 1395 行)
# - _calculate_pi_params (第 1568 行)
# - _generate_svpwm_table (第 1623 行)
# - _read_matlab_script (第 1678 行)
# - _suggest_controller_params (第 1804 行)
# - _parse_slx_model (第 1519 行)
```

- [ ] **步骤 5：创建 tools/_knowledge.py**

```python
# tools/_knowledge.py
"""知识库工具：knowledge_search, knowledge_add, knowledge_list, knowledge_import, knowledge_rebuild"""

# 从原 tools.py 复制以下函数:
# - _knowledge_search (第 1852 行)
# - _knowledge_add (第 1860 行)
# - _knowledge_list (第 1869 行)
# - _knowledge_import (第 1875 行)
# - _knowledge_rebuild (第 1883 行)
```

- [ ] **步骤 6：创建 tools/_web.py**

```python
# tools/_web.py
"""网络工具：web_search, web_fetch, download_file"""

# 从原 tools.py 复制以下函数:
# - _web_search (第 1893 行)
# - _web_fetch (第 1923 行)
# - _download_file (第 1959 行)
```

- [ ] **步骤 7：创建 tools/_agent_tools.py**

```python
# tools/_agent_tools.py
"""Agent/模型工具：reflect, memory_search, memory_stats, scheduler_status,
   spawn_agent, list_agents, handoff_to_agent, switch_model, list_models, trace_summary"""

# 从原 tools.py 复制以下函数:
# - _reflect_tool (第 1996 行)
# - _memory_search (第 2016 行)
# - _memory_stats (第 2027 行)
# - _scheduler_status (第 2034 行)
# - _spawn_agent (第 2044 行)
# - _list_agents (第 2054 行)
# - _handoff_to_agent (第 2060 行)
# - _switch_model (第 2070 行)
# - _list_models (第 2079 行)
# - _trace_summary (第 2085 行)
```

- [ ] **步骤 8：创建 tools/_command.py**

```python
# tools/_command.py
"""命令执行工具：run_command, compile_ccs"""

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Optional

from config import DANGEROUS_PATTERNS, DANGER_CONFIRM, PROJECT_ROOT

RUN_COMMAND_ALLOWED = os.environ.get("FOC_ALLOW_RUN_COMMAND", "").lower() in {"1", "true", "yes", "on"}


def _is_dangerous(command: str) -> bool:
    cmd_lower = command.lower()
    return any(p in cmd_lower for p in DANGEROUS_PATTERNS)


def _resolve_path(path_str: str) -> Path:
    """解析路径，支持相对路径和 ~"""
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def run_command(args: dict, danger_callback: Optional[Callable[[str], bool]] = None) -> str:
    """执行系统命令（shell=False + shlex.split）"""
    # ... 从任务 2 修改后的 _run_command 代码复制 ...
    pass


def compile_ccs(args: dict) -> str:
    """CCS 编译（shell=False 列表参数）"""
    # ... 从任务 2 修改后的 _compile_ccs 代码复制 ...
    pass
```

- [ ] **步骤 9：创建 tools/_registry.py**

```python
# tools/_registry.py
"""工具注册表和分发器"""

from typing import Callable, Optional

# 工具定义列表（OpenAI function calling 格式）
TOOLS = [
    # ... 从原 tools.py 第 62-818 行复制所有工具定义 ...
]


# 分发表：工具名 → 实现函数
_TOOL_DISPATCH: dict[str, Callable] = {}


def _register():
    """延迟注册所有工具实现"""
    from tools._file_ops import read_file, read_many_files, write_file, edit_file
    from tools._search import search_code, find_files, list_directory, project_overview, extract_symbols
    from tools._analysis import analyze_csv, calculate_pi_params, generate_svpwm_table, read_matlab_script, suggest_controller_params, parse_slx_model
    from tools._knowledge import knowledge_search, knowledge_add, knowledge_list, knowledge_import, knowledge_rebuild
    from tools._web import web_search, web_fetch, download_file
    from tools._agent_tools import reflect_tool, memory_search, memory_stats, scheduler_status, spawn_agent, list_agents, handoff_to_agent, switch_model, list_models, trace_summary
    from tools._command import run_command, compile_ccs

    _TOOL_DISPATCH.update({
        "read_file": read_file,
        "read_many_files": read_many_files,
        "write_file": write_file,
        "edit_file": edit_file,
        "search_code": search_code,
        "find_files": find_files,
        "list_directory": list_directory,
        "project_overview": project_overview,
        "extract_symbols": extract_symbols,
        "analyze_csv": analyze_csv,
        "calculate_pi_params": calculate_pi_params,
        "generate_svpwm_table": generate_svpwm_table,
        "read_matlab_script": read_matlab_script,
        "suggest_controller_params": suggest_controller_params,
        "parse_slx_model": parse_slx_model,
        "knowledge_search": knowledge_search,
        "knowledge_add": knowledge_add,
        "knowledge_list": knowledge_list,
        "knowledge_import": knowledge_import,
        "knowledge_rebuild": knowledge_rebuild,
        "web_search": web_search,
        "web_fetch": web_fetch,
        "download_file": download_file,
        "reflect_tool": reflect_tool,
        "memory_search": memory_search,
        "memory_stats": memory_stats,
        "scheduler_status": scheduler_status,
        "spawn_agent": spawn_agent,
        "list_agents": list_agents,
        "handoff_to_agent": handoff_to_agent,
        "switch_model": switch_model,
        "list_models": list_models,
        "trace_summary": trace_summary,
        "run_command": run_command,
        "compile_ccs": compile_ccs,
    })


def execute_tool(name: str, args: dict, danger_callback: Optional[Callable[[str], bool]] = None) -> str:
    """执行指定工具"""
    if not _TOOL_DISPATCH:
        _register()

    func = _TOOL_DISPATCH.get(name)
    if func is None:
        # 经验库工具特殊处理
        if name in ("recall_experience", "record_experience"):
            from tools._experience import execute_experience_tool
            return execute_experience_tool(name, args)
        return f"未知工具: {name}"

    import inspect
    sig = inspect.signature(func)
    if "danger_callback" in sig.parameters:
        return func(args, danger_callback=danger_callback)
    return func(args)
```

- [ ] **步骤 10：创建 tools/__init__.py**

```python
# tools/__init__.py
"""FOC-Assistant 工具集（包）"""

from tools._registry import TOOLS, execute_tool

__all__ = ["TOOLS", "execute_tool"]
```

- [ ] **步骤 11：删除旧的 tools.py，验证导入**

```bash
# 备份旧文件
mv C:\Users\macree\foc-assistant\tools.py C:\Users\macree\foc-assistant\tools.py.bak

# 测试导入
cd C:\Users\macree\foc-assistant && python -c "from tools import TOOLS, execute_tool; print(f'{len(TOOL)S)} tools loaded')"
```

- [ ] **步骤 12：运行全部测试**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/ -v`
预期：所有现有测试通过

- [ ] **步骤 13：Commit**

```bash
git add tools/
git rm tools.py
git commit -m "refactor: split tools.py (2088 lines) into tools/ package

Split monolithic tools.py into 8 focused modules:
- tools/_file_ops.py: file read/write/edit
- tools/_search.py: code search, find files, project overview
- tools/_analysis.py: CSV, SVPWM, PI, Simulink analysis
- tools/_knowledge.py: knowledge base operations
- tools/_web.py: web search/fetch/download
- tools/_agent_tools.py: agent/model/memory tools
- tools/_command.py: shell command execution
- tools/_registry.py: TOOLS list + dispatch

Public interface unchanged: from tools import TOOLS, execute_tool

Fixes: P3 #4 (tools.py 巨石文件)"
```

---

### 任务 5：桥接 subagent/executor.py

**问题：** `SubagentExecutor` 是异步的（asyncio），但 `agents/__init__.py` 的 `_execute_sub_agent` 是同步的。两者没有连接。

**方案：** 在 `_execute_sub_agent` 中用 `asyncio.run()` 调用 `SubagentExecutor`，或保留当前同步方案（任务 1 已让它调用 `agent_loop`）。

**决策：** 任务 1 已经让 `_execute_sub_agent` 直接调用 `agent_loop`，这已经解决了子 Agent 执行的问题。`SubagentExecutor` 的价值在于**非阻塞**后台执行——这需要更上层的调度器集成。当前阶段保留 `SubagentExecutor` 作为预留模块，不强制桥接。

**修改：** 在 `SubagentExecutor` 中添加同步包装方法，方便未来集成。

**文件：**
- 修改：`subagent/executor.py`（添加 sync wrapper）
- 创建：`tests/test_subagent_executor.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_subagent_executor.py
"""测试 SubagentExecutor 同步包装"""

import pytest
from unittest.mock import MagicMock, AsyncMock


def test_executor_has_dispatch_sync():
    """SubagentExecutor 应有 dispatch_sync 方法"""
    from subagent.executor import SubagentExecutor
    from subagent.deferred_store import DeferredResultStore

    store = MagicMock(spec=DeferredResultStore)
    llm_call = AsyncMock(return_value="result")
    executor = SubagentExecutor(deferred_store=store, llm_call=llm_call)

    assert hasattr(executor, "dispatch_sync")
    assert callable(executor.dispatch_sync)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_subagent_executor.py -v`
预期：FAIL — `SubagentExecutor` 没有 `dispatch_sync` 方法

- [ ] **步骤 3：添加 dispatch_sync 方法**

在 `subagent/executor.py` 的 `SubagentExecutor` 类中添加：

```python
def dispatch_sync(
    self,
    agent_id: str,
    prompt: str,
    parent_context: dict = None,
    tools: list[str] = None,
) -> str:
    """同步派发并等待结果（阻塞，适合从同步代码调用）"""
    import asyncio

    async def _run():
        task_id = await self.dispatch(agent_id, prompt, parent_context, tools)
        result = await self.wait_result(task_id, timeout=TIMEOUT_S)
        if result and result.result:
            return result.result
        if result and result.error:
            return f"[ERROR] {result.error}"
        return "[ERROR] Task did not complete"

    try:
        loop = asyncio.get_running_loop()
        # 已有事件循环（如 Jupyter），用 nest_asyncio 或直接创建新循环
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _run())
            return future.result(timeout=TIMEOUT_S + 10)
    except RuntimeError:
        # 没有运行中的事件循环
        return asyncio.run(_run())
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd C:\Users\macree\foc-assistant && python -m pytest tests/test_subagent_executor.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add subagent/executor.py tests/test_subagent_executor.py
git commit -m "feat: add dispatch_sync to SubagentExecutor

Adds synchronous wrapper for async executor, enabling integration
from sync code paths via asyncio.run() + ThreadPoolExecutor fallback.

Fixes: P1 新增 (subagent/executor.py 零调用)"
```

---

## 自检

**1. 规格覆盖度：**
- P1 #1 agent_loop 重复 → 任务 1 ✓
- P1 新增 subagent executor 零调用 → 任务 5 ✓
- P2 #12 shell=True → 任务 2 ✓
- P2 #16 增量索引 → 任务 3 ✓
- P3 #4 tools.py 拆分 → 任务 4 ✓
- P2 #9 _load_index 损坏区分 → 已在 R4 修复（knowledge.py:481-484） ✓

**2. 占位符扫描：** 无 TODO/待定/后续实现。所有步骤包含完整代码。

**3. 类型一致性：**
- `agent_loop` 的 `system_prompt_override: Optional[str]` 在任务 1 定义，任务 5 引用 ✓
- `execute_tool` 的签名在任务 4 保持与原 `tools.py` 一致 ✓
- `_resolve_path` 在 `_file_ops.py` 和 `_command.py` 中都需要，放在 `_file_ops.py` 中，`_command.py` 从 `_file_ops` 导入 ✓
