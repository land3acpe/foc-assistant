# Issue #1：合并 5 套记忆系统为三层架构 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用 ChatMemory + KnowledgeBase + ExperienceStore 三层记忆架构替换当前 5 套并存的混乱实现，减码 ≥ 2000 行，保留 RAG 与经验库的求职关键词，留出稳定 API 给 Issue #2。

**架构：** 新建 `memory.py`（< 200 行）实现 ChatMemory（滑动 deque + 触发式 LLM 摘要 + JSON 持久化按 session_key 隔离）；删除 `conversation_memory.py / memory/* / api/memory_api.py`；保留 `knowledge.py`（RAG）与 `experience/`（经验库）；调用方迁移到新接口或删除。

**技术栈：** Python 3.12 + pytest + collections.deque + json + pathlib + 现有 `config.ModelManager`

**关联文档：**
- 设计：`docs/superpowers/specs/2026-05-07-merge-memory-systems-design.md`
- Issue：[#1](https://github.com/land3acpe/foc-assistant/issues/1)
- 整体计划：`task_plan.md`

**关键约束：**
- 当前仓库已有大量历史未提交修改（不是本次工作产物）。每次 commit 必须用 `git add <精确文件>`，**绝不能用 `git add -A` 或 `git add .`**
- 集成边界：本 Issue **不做** ChatMemory 在 graph_agent 中的 LLM messages 注入（属 Issue #2）

---

## 文件结构

### 创建
- `memory.py` — ChatMemory 类 + 工厂 `get_chat_memory()`，< 200 行
- `tests/test_memory.py` — 10 个测试用例，~150 行

### 修改
- `agent.py:138-144` — `from api.memory_api import get_memory_api` → 改为直接 `from experience.experience_store import ExperienceStore` + `from experience.experience_tools import get_experience_prompt_section`
- `graph_agent.py:345-355` — 删除整个 memorize 节点的 try/except 块
- `tools/_agent_tools.py:26-41` — 删除 `_memory_search` 和 `_memory_stats` 两个函数
- `tools/_registry.py` — 删除 4 处：L606 附近 `memory_search` schema、L627 附近 `memory_stats` schema、L796 import、L830-831 dispatch

### 删除
- `conversation_memory.py`
- `memory/` 整个目录（`__init__.py / fact_store.py / session_summary.py / compile.py / deep_memory.py / memory_ticker.py`）
- `api/memory_api.py`（删完后如 `api/` 空了一并删目录）
- `tests/test_memory_api.py`
- `tests/test_fact_store.py`

### 保留（验证不被破坏）
- `knowledge.py` 591 行
- `experience/experience_store.py` + `experience/experience_tools.py` + `experience/__init__.py`
- `tests/test_experience_store.py`
- `tests/test_incremental_index.py`

---

## 任务列表

### 任务 1：建立基线 + 创建 memory.py 骨架 + 空测试文件

**文件：**
- 创建：`memory.py`
- 创建：`tests/test_memory.py`

- [ ] **步骤 1：建立测试基线**

运行：
```bash
cd "C:/Users/TianqinWu/foc-assistant"
python -m pytest --tb=no -q 2>&1 | tail -3
```

预期：`125 passed` 或类似（记下数字作为基线）

- [ ] **步骤 2：创建 memory.py 骨架**

写入 `memory.py`：

```python
"""ChatMemory: 短期对话记忆
- 滑动窗口 deque(maxlen)
- 触发式 LLM 摘要（替代式更新，避免无限累积）
- JSON 持久化，按 session_key 隔离
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_DIR = Path.home() / ".foc-assistant" / "memory"
SCHEMA_VERSION = 1


class ChatMemory:
    """短期对话记忆：滑动窗口 + 触发式 LLM 摘要 + JSON 持久化"""

    def __init__(
        self,
        session_key: str,
        maxlen: int = 20,
        summary_keep: int = 10,
        storage_dir: Optional[Path] = None,
        llm_client: Optional[Callable[[list[dict]], str]] = None,
    ):
        raise NotImplementedError("骨架，由后续任务实现")


def get_chat_memory(session_key: str = "default") -> ChatMemory:
    raise NotImplementedError("骨架，由后续任务实现")
```

- [ ] **步骤 3：创建 tests/test_memory.py 框架**

写入 `tests/test_memory.py`：

```python
"""ChatMemory 单元测试"""
import pytest
from pathlib import Path

from memory import ChatMemory, get_chat_memory


@pytest.fixture
def tmp_storage(tmp_path: Path) -> Path:
    return tmp_path / "memory"


def _stub_llm(messages: list[dict]) -> str:
    return "STUB_SUMMARY"


def _failing_llm(messages: list[dict]) -> str:
    raise RuntimeError("simulated LLM error")
```

- [ ] **步骤 4：跑测试确认未破坏其他用例**

运行：
```bash
python -m pytest --tb=no -q 2>&1 | tail -3
```

预期：仍是 125 通过（test_memory.py 没有用例所以不计入）

- [ ] **步骤 5：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add memory.py tests/test_memory.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
feat(memory): 添加 ChatMemory 骨架与测试框架

Issue #1 三层记忆架构第一步。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 2：实现 add_turn 与 get_context 基础

**文件：**
- 修改：`memory.py`
- 测试：`tests/test_memory.py`

- [ ] **步骤 1：编写失败测试 test_add_turn_basic**

追加到 `tests/test_memory.py`：

```python
def test_add_turn_basic(tmp_storage):
    mem = ChatMemory("s1", storage_dir=tmp_storage)
    mem.add_turn("user", "hi")
    mem.add_turn("assistant", "hello")
    ctx = mem.get_context()
    assert len(ctx) == 2
    assert ctx[0]["role"] == "user"
    assert ctx[0]["content"] == "hi"
    assert ctx[1]["role"] == "assistant"
```

- [ ] **步骤 2：跑测试确认失败**

运行：
```bash
python -m pytest tests/test_memory.py::test_add_turn_basic -v
```

预期：`FAIL` 报 `NotImplementedError`

- [ ] **步骤 3：实现最小 ChatMemory（不含持久化与摘要）**

替换 `memory.py` 中的骨架为完整实现（暂不实现 _save/_load，留空操作）：

```python
class ChatMemory:
    """短期对话记忆：滑动窗口 + 触发式 LLM 摘要 + JSON 持久化"""

    def __init__(
        self,
        session_key: str,
        maxlen: int = 20,
        summary_keep: int = 10,
        storage_dir: Optional[Path] = None,
        llm_client: Optional[Callable[[list[dict]], str]] = None,
    ):
        if summary_keep >= maxlen:
            raise ValueError("summary_keep must be < maxlen")
        self.session_key = session_key
        self.maxlen = maxlen
        self.summary_keep = summary_keep
        self.storage_dir = storage_dir or DEFAULT_STORAGE_DIR
        self.llm_client = llm_client
        self._summary: str = ""
        self._turns: deque = deque()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def add_turn(self, role: str, content: str) -> None:
        self._turns.append({
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    def get_context(self) -> list[dict]:
        msgs: list[dict] = []
        if self._summary:
            msgs.append({"role": "system", "content": self._summary})
        for t in self._turns:
            msgs.append({"role": t["role"], "content": t["content"]})
        return msgs

    def stats(self) -> dict:
        return {
            "turns": len(self._turns),
            "has_summary": bool(self._summary),
            "summary_chars": len(self._summary),
        }
```

`get_chat_memory` 暂留 `NotImplementedError`，下一任务再实现。

- [ ] **步骤 4：跑测试确认通过**

运行：
```bash
python -m pytest tests/test_memory.py::test_add_turn_basic -v
```

预期：`PASS`

- [ ] **步骤 5：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add memory.py tests/test_memory.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
feat(memory): 实现 ChatMemory 基础 add_turn / get_context

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 3：实现 JSON 持久化与 session 隔离

**文件：**
- 修改：`memory.py`
- 测试：`tests/test_memory.py`

- [ ] **步骤 1：编写失败测试 test_persistence_round_trip + test_session_isolation + test_session_key_with_special_chars**

追加到 `tests/test_memory.py`：

```python
def test_persistence_round_trip(tmp_storage):
    m1 = ChatMemory("s1", storage_dir=tmp_storage)
    m1.add_turn("user", "foo")
    m1.add_turn("assistant", "bar")

    m2 = ChatMemory("s1", storage_dir=tmp_storage)
    ctx = m2.get_context()
    assert len(ctx) == 2
    assert ctx[0]["content"] == "foo"
    assert ctx[1]["content"] == "bar"


def test_session_isolation(tmp_storage):
    a = ChatMemory("alice", storage_dir=tmp_storage)
    b = ChatMemory("bob", storage_dir=tmp_storage)
    a.add_turn("user", "alice msg")
    b.add_turn("user", "bob msg")
    assert a.get_context()[0]["content"] == "alice msg"
    assert b.get_context()[0]["content"] == "bob msg"


def test_session_key_with_special_chars(tmp_storage):
    mem = ChatMemory("qq:1:2", storage_dir=tmp_storage)
    mem.add_turn("user", "x")
    files = list(tmp_storage.glob("*.json"))
    assert len(files) == 1
    assert files[0].name == "qq_1_2.json"

    mem2 = ChatMemory("qq:1:2", storage_dir=tmp_storage)
    assert mem2.get_context()[0]["content"] == "x"
```

- [ ] **步骤 2：跑测试确认失败**

运行：
```bash
python -m pytest tests/test_memory.py::test_persistence_round_trip tests/test_memory.py::test_session_isolation tests/test_memory.py::test_session_key_with_special_chars -v
```

预期：3 个 `FAIL`（数据没持久化）

- [ ] **步骤 3：在 memory.py 实现 _save / _load / _summary_path**

在 `ChatMemory` 类内追加私有方法：

```python
    def _summary_path(self) -> Path:
        safe = re.sub(r"[^\w\-]", "_", self.session_key)
        return self.storage_dir / f"{safe}.json"

    def _load(self) -> None:
        path = self._summary_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != SCHEMA_VERSION:
                logger.warning(f"incompatible memory schema, ignoring {path}")
                return
            self._summary = data.get("summary", "")
            for t in data.get("turns", []):
                self._turns.append(t)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"failed to load {path}: {e}")

    def _save(self) -> None:
        path = self._summary_path()
        data = {
            "session_key": self.session_key,
            "version": SCHEMA_VERSION,
            "summary": self._summary,
            "turns": list(self._turns),
        }
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error(f"failed to save {path}: {e}")
```

修改 `__init__` 末尾，调用 `_load`：

```python
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._load()  # 新增
```

修改 `add_turn` 末尾，调用 `_save`：

```python
    def add_turn(self, role: str, content: str) -> None:
        self._turns.append({
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        self._save()  # 新增
```

- [ ] **步骤 4：跑测试确认通过**

运行：
```bash
python -m pytest tests/test_memory.py -v
```

预期：4 个 `PASS`

- [ ] **步骤 5：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add memory.py tests/test_memory.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
feat(memory): JSON 持久化、session 隔离、特殊字符净化

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 4：实现摘要触发与 LLM 失败降级

**文件：**
- 修改：`memory.py`
- 测试：`tests/test_memory.py`

- [ ] **步骤 1：编写失败测试 test_maxlen_triggers_summary + test_summary_fallback_on_llm_error**

追加到 `tests/test_memory.py`：

```python
def test_maxlen_triggers_summary(tmp_storage):
    mem = ChatMemory(
        "s1",
        maxlen=5,
        summary_keep=2,
        storage_dir=tmp_storage,
        llm_client=_stub_llm,
    )
    for i in range(7):
        mem.add_turn("user", f"m{i}")
    assert mem.stats()["has_summary"] is True
    assert mem.stats()["turns"] == 2
    ctx = mem.get_context()
    assert ctx[0]["role"] == "system"
    assert ctx[0]["content"] == "STUB_SUMMARY"
    assert len(ctx) == 3


def test_summary_fallback_on_llm_error(tmp_storage):
    mem = ChatMemory(
        "s1",
        maxlen=5,
        summary_keep=2,
        storage_dir=tmp_storage,
        llm_client=_failing_llm,
    )
    for i in range(7):
        mem.add_turn("user", f"m{i}")
    # LLM 失败 → 降级 FIFO drop，turns 数等于 summary_keep
    assert mem.stats()["has_summary"] is False
    assert mem.stats()["turns"] == 2
```

- [ ] **步骤 2：跑测试确认失败**

运行：
```bash
python -m pytest tests/test_memory.py::test_maxlen_triggers_summary tests/test_memory.py::test_summary_fallback_on_llm_error -v
```

预期：2 个 `FAIL`（摘要逻辑还没写）

- [ ] **步骤 3：在 memory.py 实现 _summarize 与摘要触发**

在 `ChatMemory` 类内追加 `_summarize`：

```python
    def _summarize(self, old_summary: str, new_turns: list[dict]) -> str:
        if not self.llm_client:
            raise RuntimeError("llm_client is None")
        formatted = "\n".join(
            f"[{t['role']}] {t['content']}" for t in new_turns
        )
        old_block = old_summary or "（无）"
        prompt = (
            "你是对话摘要助手。请把以下信息压缩为不超过 300 字的中文摘要，保留：\n"
            "- 用户的核心诉求和已确定的参数\n"
            "- 已尝试的方案和结果\n"
            "- 待解决的问题\n\n"
            f"【已有摘要（如有）】\n{old_block}\n\n"
            f"【新增对话】\n{formatted}\n\n"
            "输出新的统一摘要（替代已有摘要，不要追加）："
        )
        result = self.llm_client([{"role": "user", "content": prompt}])
        return result.strip() if isinstance(result, str) else str(result)
```

修改 `add_turn`，加入触发逻辑：

```python
    def add_turn(self, role: str, content: str) -> None:
        self._turns.append({
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        if len(self._turns) > self.maxlen:
            to_compress = []
            while len(self._turns) > self.summary_keep:
                to_compress.append(self._turns.popleft())
            try:
                self._summary = self._summarize(self._summary, to_compress)
            except Exception as e:
                logger.warning(f"summary failed, drop oldest as FIFO: {e}")
                # to_compress 已 popleft 出来，丢弃即可
        self._save()
```

- [ ] **步骤 4：跑测试确认通过**

运行：
```bash
python -m pytest tests/test_memory.py -v
```

预期：6 个 `PASS`

- [ ] **步骤 5：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add memory.py tests/test_memory.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
feat(memory): 触发式 LLM 摘要 + 失败降级 FIFO

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 5：实现 clear 与 get_context 格式校验测试

**文件：**
- 修改：`memory.py`
- 测试：`tests/test_memory.py`

- [ ] **步骤 1：编写失败测试**

追加到 `tests/test_memory.py`：

```python
def test_get_context_format_no_summary(tmp_storage):
    mem = ChatMemory("s1", storage_dir=tmp_storage)
    mem.add_turn("user", "hi")
    ctx = mem.get_context()
    assert ctx[0]["role"] == "user"
    assert all(m["role"] != "system" for m in ctx)


def test_get_context_format_with_summary(tmp_storage):
    mem = ChatMemory(
        "s1",
        maxlen=5,
        summary_keep=2,
        storage_dir=tmp_storage,
        llm_client=_stub_llm,
    )
    for i in range(7):
        mem.add_turn("user", f"m{i}")
    ctx = mem.get_context()
    assert ctx[0]["role"] == "system"
    assert ctx[0]["content"] == "STUB_SUMMARY"


def test_clear(tmp_storage):
    mem = ChatMemory("s1", storage_dir=tmp_storage)
    mem.add_turn("user", "hi")
    path = tmp_storage / "s1.json"
    assert path.exists()
    mem.clear()
    assert mem.stats()["turns"] == 0
    assert mem.stats()["has_summary"] is False
    assert not path.exists()
```

- [ ] **步骤 2：跑测试确认失败**

运行：
```bash
python -m pytest tests/test_memory.py::test_get_context_format_no_summary tests/test_memory.py::test_get_context_format_with_summary tests/test_memory.py::test_clear -v
```

预期：前两个 `PASS`（已实现），`test_clear` 报 `AttributeError`

- [ ] **步骤 3：在 ChatMemory 类内实现 clear**

```python
    def clear(self) -> None:
        self._turns.clear()
        self._summary = ""
        path = self._summary_path()
        if path.exists():
            path.unlink()
```

- [ ] **步骤 4：跑测试确认通过**

运行：
```bash
python -m pytest tests/test_memory.py -v
```

预期：9 个 `PASS`

- [ ] **步骤 5：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add memory.py tests/test_memory.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
feat(memory): 实现 clear 与 get_context 格式校验

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 6：实现工厂 get_chat_memory + 实例缓存

**文件：**
- 修改：`memory.py`
- 测试：`tests/test_memory.py`

- [ ] **步骤 1：编写失败测试**

追加到 `tests/test_memory.py`（注意工厂使用默认存储路径，测试后需清理）：

```python
def test_factory_returns_same_instance(tmp_path, monkeypatch):
    # 重定向工厂的默认存储路径，避免污染用户目录
    import memory as memory_mod
    monkeypatch.setattr(memory_mod, "DEFAULT_STORAGE_DIR", tmp_path / "memory")
    memory_mod._INSTANCES.clear()  # 清掉可能存在的缓存

    m1 = get_chat_memory("test_session_factory")
    m2 = get_chat_memory("test_session_factory")
    assert m1 is m2
```

- [ ] **步骤 2：跑测试确认失败**

运行：
```bash
python -m pytest tests/test_memory.py::test_factory_returns_same_instance -v
```

预期：`FAIL` 报 `NotImplementedError`

- [ ] **步骤 3：在 memory.py 实现工厂与缓存字典**

在文件末尾追加：

```python
# ======== 工厂 ========
_INSTANCES: dict[str, "ChatMemory"] = {}


def get_chat_memory(session_key: str = "default") -> ChatMemory:
    """工厂方法，按 session_key 缓存默认参数实例。
    自定义参数请直接 ChatMemory(...)。
    """
    if session_key not in _INSTANCES:
        _INSTANCES[session_key] = ChatMemory(session_key)
    return _INSTANCES[session_key]
```

删除 `get_chat_memory` 的旧 `NotImplementedError` 实现。

- [ ] **步骤 4：跑测试确认通过**

运行：
```bash
python -m pytest tests/test_memory.py -v
```

预期：10 个 `PASS`

- [ ] **步骤 5：跑全套测试确认基线没退化**

运行：
```bash
python -m pytest --tb=no -q 2>&1 | tail -3
```

预期：`135 passed`（基线 125 + 新增 10）

- [ ] **步骤 6：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add memory.py tests/test_memory.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
feat(memory): 工厂 get_chat_memory 实例缓存

完成 ChatMemory 全部基础设施。下一步：调用方迁移与冗余删除。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 7：迁移 graph_agent.py（删除 memorize 节点）

**文件：**
- 修改：`graph_agent.py:340-360` 附近

- [ ] **步骤 1：用 Read 确认当前代码**

读取 `graph_agent.py:340-360`，确认 try/except 块结构与设计文档一致。

- [ ] **步骤 2：删除整个洞察提取调用块**

把 `graph_agent.py:345-355`（含 `try: from memory import get_memory ... except Exception as e: print(f"  [MEMORY] 记忆提取失败: {e}")`）整段替换为：

```python
        # 洞察提取已废（Issue #1：记忆系统简化为三层架构）
        # ChatMemory 集成将在 Issue #2 中接入 LangGraph chat/qa 节点
        return {"memory_stored": []}
```

注意：此节点最终行为应该是 no-op 但保留返回结构兼容上游 LangGraph 状态。

- [ ] **步骤 3：跑全套测试确认未破坏**

运行：
```bash
python -m pytest --tb=no -q 2>&1 | tail -3
```

预期：`135 passed`（不变）

- [ ] **步骤 4：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add graph_agent.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
refactor(graph_agent): 移除洞察提取节点（Issue #1）

ChatMemory 集成留给 Issue #2。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 8：迁移 agent.py（experience prompt 改为直接 import）

**文件：**
- 修改：`agent.py:138-144` 附近

- [ ] **步骤 1：用 Read 确认当前代码**

读取 `agent.py:130-150`，确认 `from api.memory_api import get_memory_api` 调用上下文。

- [ ] **步骤 2：替换 import 与调用**

把 `agent.py:138-144` 的：

```python
        try:
            from api.memory_api import get_memory_api
            _mem_api = get_memory_api()
            exp_prompt = _mem_api.get_experience_prompt()
            if exp_prompt:
                system_prompt += "\n\n" + exp_prompt
        except Exception as e:
```

替换为：

```python
        try:
            from experience.experience_store import ExperienceStore
            from experience.experience_tools import get_experience_prompt_section
            exp_prompt = get_experience_prompt_section(ExperienceStore())
            if exp_prompt:
                system_prompt += "\n\n" + exp_prompt
        except Exception as e:
```

注意：保留 try/except 包裹（experience_store 初始化可能失败）。

- [ ] **步骤 3：跑全套测试确认未破坏**

运行：
```bash
python -m pytest --tb=no -q 2>&1 | tail -3
```

预期：`135 passed`

- [ ] **步骤 4：grep 确认 api.memory_api 在代码中已无引用**

运行：
```bash
grep -rn "api.memory_api\|api/memory_api\|get_memory_api" --include="*.py" .
```

预期：仅 `tests/test_memory_api.py` 还有引用（该测试将在任务 11 删除）

- [ ] **步骤 5：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add agent.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
refactor(agent): experience prompt 注入直接调用 experience 模块

不再绕道 api.memory_api facade。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 9：删除 _memory_search 与 _memory_stats 工具

**文件：**
- 修改：`tools/_agent_tools.py`
- 修改：`tools/_registry.py`

- [ ] **步骤 1：删除 tools/_agent_tools.py 中的两个函数**

删除 `tools/_agent_tools.py:26-41`（即 `_memory_search` 和 `_memory_stats` 两个函数完整定义），保留首部 docstring 和其他函数不变。

同时修改文件首部 docstring（L1-2），把 `memory_search, memory_stats,` 从字符串中去掉。

- [ ] **步骤 2：删除 tools/_registry.py 中的 schema 与 dispatch**

修改 `tools/_registry.py`，4 处删除：

a. **L606 附近**：删除 `memory_search` 工具的整个 schema 字典（从 `{` 到对应 `},`，注意是 dict 不是 list 项，仔细数括号）

b. **L627 附近**：删除 `memory_stats` 工具的整个 schema 字典

c. **L796**：把 `_reflect_tool, _memory_search, _memory_stats, _scheduler_status,` 中的 `_memory_search, _memory_stats,` 删掉，变成 `_reflect_tool, _scheduler_status,`

d. **L830-831**：删除两行
```python
        "memory_search": _memory_search,
        "memory_stats": _memory_stats,
```

- [ ] **步骤 3：python -c 测试 import 不报错**

运行：
```bash
python -c "from tools._registry import get_tools_schema, get_dispatch_table; print('ok', len(get_tools_schema()))"
```

预期：输出 `ok <数字>`，数字应比之前少 2

- [ ] **步骤 4：跑全套测试确认未破坏**

运行：
```bash
python -m pytest --tb=no -q 2>&1 | tail -3
```

预期：`135 passed`

- [ ] **步骤 5：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add tools/_agent_tools.py tools/_registry.py
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
refactor(tools): 删除 memory_search 与 memory_stats 两个工具

洞察提取已废，搜索老 memory 库已无意义。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 10：删除 conversation_memory.py + memory/ 整个目录 + api/memory_api.py

**文件：**
- 删除：`conversation_memory.py`
- 删除：`memory/` 整个目录
- 删除：`api/memory_api.py`

- [ ] **步骤 1：grep 全项目最后一次确认无残留 import**

运行：
```bash
grep -rn "from memory import\|import memory$\|from conversation_memory\|from api.memory_api\|from memory\." --include="*.py" .
```

预期：仅可能在 `tests/test_memory_api.py` / `tests/test_fact_store.py` 出现（任务 11 删除）

如有任何 .py 文件还在 import 上述模块（除测试），停下排查后再继续。

- [ ] **步骤 2：删除文件**

注意 `conversation_memory.py` 当前是 untracked（尚未被 git 追踪），需要直接删除文件而非用 `git rm`：

```bash
cd "C:/Users/TianqinWu/foc-assistant"
rm conversation_memory.py
git rm -r memory/
git rm api/memory_api.py
# 检查 api/ 是否空了
ls api/ 2>/dev/null
# 如果为空，删目录
rmdir api/ 2>/dev/null || true
```

- [ ] **步骤 3：跑全套测试**

运行：
```bash
python -m pytest --tb=short -q 2>&1 | tail -10
```

预期：`tests/test_memory_api.py` 与 `tests/test_fact_store.py` 报 `ImportError`，**其他测试全过**。

记下当前 pass 数（应为 `135 - failed_count`）。

- [ ] **步骤 4：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add -u  # 仅暂存已跟踪文件的删除
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
refactor(memory): 删除 conversation_memory / memory/ / api/memory_api

5 套并存的记忆系统瘦身为三层架构（ChatMemory + Knowledge + Experience）。
约减 -2050 行。

Issue #1 三层记忆架构合并第 4 步。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

注：`git add -u` 只暂存已跟踪文件的修改与删除，不会误加未跟踪文件。

---

### 任务 11：删除冗余测试文件

**文件：**
- 删除：`tests/test_memory_api.py`
- 删除：`tests/test_fact_store.py`

- [ ] **步骤 1：删除两个测试文件**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" rm tests/test_memory_api.py tests/test_fact_store.py
```

- [ ] **步骤 2：跑全套测试确认全绿**

运行：
```bash
python -m pytest --tb=no -q 2>&1 | tail -3
```

预期：全 `passed`，无 `failed`、无 `errors`

记下数字 N（应该 ≈ 任务 6 的 135 减去原 test_memory_api 与 test_fact_store 的用例数）。

- [ ] **步骤 3：commit**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
test: 删除 test_memory_api 与 test_fact_store

对应模块已删除。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### 任务 12：最终验收 + 更新 progress.md + 关闭 Issue

**文件：**
- 修改：`progress.md`

- [ ] **步骤 1：跑验收命令清单**

> 注：开始执行任务 1 之前，请先记录基线 commit hash：
> `BASE=$(git -C "C:/Users/TianqinWu/foc-assistant" rev-parse HEAD)`
> 后续 diff 命令将用 `$BASE` 替代具体 hash。

```bash
cd "C:/Users/TianqinWu/foc-assistant"

# 1. import 残留检查（experience 应当还在）
grep -rn "from memory import\|import memory$\|from conversation_memory\|from api.memory_api" --include="*.py" .
# 预期：无输出

# 2. experience 还在
grep -rn "from experience" --include="*.py" .
# 预期：有输出，至少包括 agent.py / experience/experience_tools.py / tests/test_experience_store.py

# 3. memory.py 能 import
python -c "from memory import ChatMemory, get_chat_memory; m = get_chat_memory('verify'); m.add_turn('user', 'ok'); print(m.get_context())"
# 预期：输出 [{'role': 'user', 'content': 'ok'}]

# 4. 全套测试
python -m pytest --tb=no -q 2>&1 | tail -3
# 预期：全 passed

# 5. 减码量统计（用任务 1 之前记录的基线 hash）
git diff --stat "$BASE" HEAD -- '*.py' | tail -5
git log --oneline "$BASE"..HEAD | head -20
# 预期：净减少 ≥ 2000 行
```

- [ ] **步骤 2：清理 verify 测试痕迹**

```bash
rm -rf ~/.foc-assistant/memory/verify.json 2>/dev/null || true
```

- [ ] **步骤 3：更新 progress.md**

在文件末尾追加：

```markdown
---

## 2026-05-XX：Issue #1 完成

**完成事项**：
- 三层记忆架构落地：ChatMemory（短期）+ KnowledgeBase（RAG）+ ExperienceStore（经验）
- 新增 `memory.py`（约 XXX 行）+ `tests/test_memory.py`（10 个用例全过）
- 删除 conversation_memory.py / memory/* / api/memory_api.py / 2 个旧测试
- 迁移 agent.py / graph_agent.py / tools/_agent_tools.py / tools/_registry.py
- 净减码 -XXXX 行（验收要求 ≥ -2000）

**未完成（属 Issue #2）**：
- ChatMemory 在 LangGraph chat/qa 节点的实际集成

**下一步**：开干 Issue #2（统一双主循环为 LangGraph）
```

把 `XXX` 与 `XXXX` 替换为实际数字。

- [ ] **步骤 4：commit progress.md**

```bash
git -C "C:/Users/TianqinWu/foc-assistant" add progress.md
git -C "C:/Users/TianqinWu/foc-assistant" commit -m "$(cat <<'EOF'
docs(progress): 记录 Issue #1 完成

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **步骤 5：在 GitHub 关闭 Issue #1**

```bash
gh issue close 1 --repo land3acpe/foc-assistant --comment "完成。三层记忆架构落地，详见 commit 历史与 docs/superpowers/specs/2026-05-07-merge-memory-systems-design.md"
```

- [ ] **步骤 6：（可选）push 到远端**

询问用户是否 push（不要主动 push）：

```bash
# 仅在用户明确同意时执行：
# git -C "C:/Users/TianqinWu/foc-assistant" push origin master
```

---

## 完成标准（与 Issue #1 验收一致）

- [ ] `grep -r "from memory import\|from conversation_memory\|from api.memory_api"` 全部为空
- [ ] `grep -r "from experience"` 仍有输出（experience 保留）
- [ ] 新增 `tests/test_memory.py` 10 个用例全过
- [ ] `tests/test_experience_store.py` / `tests/test_incremental_index.py` 仍过
- [ ] 净减码 ≥ 2000 行（任务 12 步骤 1 命令验证）
- [ ] `python -c "from memory import ChatMemory"` 不报错
- [ ] GitHub Issue #1 已关闭
- [ ] `progress.md` 已更新

---

## 风险记录

| 风险 | 缓解 |
|---|---|
| 删除文件时误删 experience/ | 每次 `git rm` 前先 grep 确认 import；experience/ 在保留清单中 |
| 仓库已有的未提交修改被误 commit | 严格使用 `git add <精确文件>` 或 `git add -u`，绝不用 `git add -A`/`git add .` |
| LLM 摘要测试不稳定 | 使用 `_stub_llm` 桩；不在 CI 调真 LLM |
| `~/.foc-assistant/memory/` 旧版本数据干扰 | `_load` 校验 `version` 字段，不兼容直接忽略 |
| `tools/_registry.py` 删除 schema 时计数括号错误 | 删后立即 `python -c "from tools._registry import get_tools_schema"` 自检 |
