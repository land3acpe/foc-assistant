# 设计文档：合并 5 套记忆系统为三层架构（Issue #1）

- **日期**：2026-05-07
- **Issue**：[#1](https://github.com/land3acpe/foc-assistant/issues/1)
- **Phase**：1（止血）
- **关联**：`task_plan.md` / `findings.md` / `progress.md`

## 背景

项目当前并行存在 5 套"记忆/知识"子系统，约 2900+ 行，职责高度重叠且面试时会被质疑：

- `conversation_memory.py`（331 行）：实际是**规则洞察提取器 + 用户画像**，不是对话轮次缓冲
- `memory/`（5 文件 1573 行）：fact_store + session_summary + compile + deep_memory + memory_ticker
- `experience/`（574 行）：SQLite + FTS5 经验库（移植自 OpenHanako，已有测试）
- `knowledge.py`（591 行）：倒排索引 + PDF 提取（RAG）
- `api/memory_api.py`（150 行）：facade

调研发现：**项目里目前没有任何地方在做"上轮对话 → 这轮 LLM context"的注入**。多轮上下文管理是缺失的。

## 设计目标

1. 用清晰的三层记忆架构替代 5 套混乱实现
2. 减少代码 ≥ 2000 行
3. 保留求职关键词："短期对话记忆 / RAG / 经验库"三层叙事完整
4. 留出稳定的 ChatMemory API 契约，供 Issue #2 集成进 LangGraph

## 范围

### 本 Issue 做

- 新建 `memory.py`（< 200 行）实现 `ChatMemory` 类
- 新建 `tests/test_memory.py`
- 删除冗余的 5 套子系统
- 把现有调用 `get_memory()`/`memory_api` 的代码改为"删除或 no-op"
- 同步删除 `tools/_registry.py` 中的相关工具

### 本 Issue 不做（属 Issue #2）

- ChatMemory 在 graph_agent 主循环中的实际集成（构造 LLM messages 时注入对话历史）
- 理由：Issue #2 要统一双主循环为 LangGraph，那时再做集成更自然

### 留出的契约（Issue #2 直接用）

```python
mem = get_chat_memory(session_key)
mem.add_turn("user", user_msg)
context = mem.get_context()  # → list[dict] for LLM messages
mem.add_turn("assistant", assistant_reply)
```

## 三层架构

```
┌──────────────────────────────────────────────────┐
│ ChatMemory（短期，memory.py，< 200 行，新建）    │
│  · 滑动窗口 deque(maxlen=20)                     │
│  · 触发式 LLM 摘要（超出时压缩最早 10 轮）       │
│  · JSON 持久化，按 session_key 隔离              │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│ KnowledgeBase（长期 RAG，knowledge.py 591，保留）│
│  · 倒排索引 + PDF 提取                           │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│ ExperienceStore（经验，experience/ 574，保留）   │
│  · SQLite + FTS5（场景-解法对）                  │
└──────────────────────────────────────────────────┘
```

## memory.py 详细设计

### API

```python
from collections.abc import Callable
from pathlib import Path
from typing import Optional

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
        """
        Args:
            session_key: session 隔离键（CLI 用 "default"，QQ 用 "qq:{group}:{user}"）
            maxlen: deque 最大长度，超出触发摘要
            summary_keep: 摘要触发后保留最近多少轮（其余被压缩进 summary）
            storage_dir: 持久化目录，默认 ~/.foc-assistant/memory/
            llm_client: 摘要用的 LLM 调用函数；None 时降级为 FIFO drop
        """

    # ======== 核心 API ========
    def add_turn(self, role: str, content: str) -> None:
        """追加一轮对话；超过 maxlen 时触发摘要并 dump 到磁盘"""

    def get_context(self) -> list[dict]:
        """组装 LLM messages 格式：
        - 有 summary：[{"role":"system","content":summary}, *recent_turns]
        - 无 summary：[*recent_turns]
        每个 turn: {"role": "user"|"assistant", "content": str}
        """

    # ======== 维护 ========
    def clear(self) -> None:
        """清空内存和持久化文件"""

    def stats(self) -> dict:
        """返回 {"turns": int, "has_summary": bool, "summary_chars": int}"""

    # ======== 私有 ========
    def _load(self) -> None: ...
    def _save(self) -> None: ...
    def _summarize(self, turns: list[dict]) -> str: ...
    def _summary_path(self) -> Path: ...


def get_chat_memory(session_key: str = "default") -> ChatMemory:
    """工厂方法，按 session_key 缓存默认参数的实例（避免重复 load）。
    自定义参数（maxlen/summary_keep/storage_dir/llm_client）请直接 ChatMemory(...)，
    不走工厂缓存。"""
```

### 默认值

| 参数 | 默认值 | 理由 |
|---|---|---|
| `maxlen` | 20 | 大多数对话不会超，超了说明确实需要摘要 |
| `summary_keep` | 10 | 保留近 10 轮即时上下文，剩余压缩 |
| `storage_dir` | `~/.foc-assistant/memory/` | 与现有 MEMORY_DIR 习惯一致 |
| `llm_client` | None（调用方注入） | 解耦；测试可注入 mock |

### session_key 命名规则

| 场景 | session_key 形式 | 示例 |
|---|---|---|
| CLI | `"default"` | `"default"` |
| QQ Bot | `f"qq:{group_id}:{user_id}"` | `"qq:12345:67890"` |
| 测试 | 任意 | `"test_session_1"` |

### 持久化格式

文件路径：`~/.foc-assistant/memory/<session_key>.json`（`session_key` 中的 `:` 替换为 `_`）

```json
{
  "session_key": "qq:12345:67890",
  "version": 1,
  "summary": "用户在调试 PMSM 速度环 PI 控制器，遇到 i_q 振荡问题，已尝试调整 Kp 至 0.5...",
  "turns": [
    {"role": "user", "content": "再分析一下", "ts": "2026-05-07T14:23:01"},
    {"role": "assistant", "content": "...", "ts": "2026-05-07T14:23:05"}
  ]
}
```

### 摘要触发逻辑

反复触发时，把"旧 summary + 要丢弃的 turns"一起喂给 LLM 输出**单一新 summary**，
避免 summary 无限累积；prompt 限定 ≤ 300 字让长度自然受控。

```python
def add_turn(self, role, content):
    self._turns.append({"role": role, "content": content, "ts": now()})
    if len(self._turns) > self.maxlen:
        # 取出最早 (maxlen - summary_keep + 1) 轮压缩；保留最近 summary_keep 轮
        to_compress = []
        while len(self._turns) > self.summary_keep:
            to_compress.append(self._turns.popleft())
        try:
            self._summary = self._summarize(self._summary, to_compress)
        except Exception as e:
            logger.warning(f"summary failed, drop oldest as FIFO: {e}")
            # 已经 popleft 出来了，直接丢弃即可（不恢复到 deque）
    self._save()
```

### 摘要 prompt（写在 _summarize 内）

```
你是对话摘要助手。请把以下信息压缩为**不超过 300 字**的中文摘要，保留：
- 用户的核心诉求和已确定的参数
- 已尝试的方案和结果
- 待解决的问题

【已有摘要（如有）】
{old_summary or "（无）"}

【新增对话】
{formatted_turns}

输出新的统一摘要（替代已有摘要，不要追加）：
```

`_summarize(old_summary: str, new_turns: list[dict]) -> str` 接受旧 summary 与新 turns，
返回**替代式**新 summary，长度始终受 prompt 约束。

## 删除清单（约 -2060 行）

| 文件 | 行数 | 处理 |
|---|---|---|
| `conversation_memory.py` | 331 | 删 |
| `memory/__init__.py` | ~5 | 删 |
| `memory/fact_store.py` | ~400 | 删 |
| `memory/session_summary.py` | ~250 | 删 |
| `memory/compile.py` | ~300 | 删 |
| `memory/deep_memory.py` | ~300 | 删 |
| `memory/memory_ticker.py` | ~330 | 删 |
| `api/memory_api.py` | 150 | 删（整个 api/ 目录如果空了一并删） |
| `tests/test_memory_api.py` | - | 删 |
| `tests/test_fact_store.py` | - | 删 |

**保留**：`experience/` 整个目录 + `tests/test_experience_store.py` + `knowledge.py` + `tests/test_incremental_index.py`

## 修改清单

| 位置 | 修改 |
|---|---|
| `agent.py:139` 附近 | 删除 `from api.memory_api import get_memory_api` 调用块 |
| `graph_agent.py:345-355` | 整个 memorize/洞察提取节点删除 |
| `tools/_agent_tools.py:31-41` | 删除 `_search_memory` 和 `_memory_stats` 两个函数 |
| `tools/_registry.py` | 同步从 schema 列表和 dispatch 表移除上述两工具的 entry |

## 新增

| 文件 | 行数预算 |
|---|---|
| `memory.py` | < 200 |
| `tests/test_memory.py` | ~120 |

## 测试清单（TDD 阶段写）

1. **test_add_turn_basic** — 加 5 轮，`get_context()` 返回 5 个 turn
2. **test_persistence_round_trip** — 写 → 新实例 load → 内容一致
3. **test_session_isolation** — 两个 session_key 互不影响
4. **test_maxlen_triggers_summary** — 加 25 轮（>maxlen=20），`stats()["has_summary"]` 为 True，`turns` 长度等于 `summary_keep=10`
5. **test_summary_fallback_on_llm_error** — `llm_client` 抛异常时，降级 FIFO drop，turns 长度 = maxlen
6. **test_get_context_format** — 有 summary 时第一个 message role=system，否则全是 user/assistant
7. **test_clear** — 清空后 turns/summary 都为空，磁盘文件被删
8. **test_session_key_with_special_chars** — `session_key="qq:1:2"` 落盘为 `qq_1_2.json`，能正确 load
9. **test_concurrent_load_no_corruption** — 同一 session_key 两次 `get_chat_memory()` 返回同一实例（工厂缓存）

## 集成边界（与 Issue #2 的契约）

Issue #2（统一双主循环）将做：
- 在 LangGraph 的 chat/qa 节点起始处：`mem = get_chat_memory(session_key); ctx = mem.get_context()`
- 把 `ctx` 拼到 LLM messages 前面
- 节点末尾：`mem.add_turn("user", user_msg); mem.add_turn("assistant", reply)`

Issue #1 完成后，调用 Issue #2 时无需修改 memory.py 内部，只用上述契约。

## 风险与回退

| 风险 | 缓解 |
|---|---|
| LLM 摘要失败导致功能不可用 | 设计内已含 FIFO 降级，5 行 try/except 兜底 |
| JSON 文件并发写损坏 | 短期不做并发保护；QQ 单消息处理是串行的；测试 #9 验证工厂缓存 |
| `session_key` 含路径穿越字符（如 `..`） | `_summary_path()` 内 `re.sub(r'[^\w\-]', '_', session_key)` 净化 |
| 删除 `tools/_search_memory` 后用户感知"功能减少" | 实际原工具搜的是洞察库，砍掉洞察提取后已无意义；README 不再提及 |
| 现有持久化数据（`~/.foc-assistant/memory/*.json` 旧格式）不兼容 | 新格式 `version: 1`，旧文件直接忽略（load 时校验 version） |

## 验收标准（来自 Issue #1）

- [ ] `grep -r "from memory\|from experience\|conversation_memory\|memory_api"` 全部为空（experience 例外，需保留）
- [ ] 总代码量减少 ≥ 2000 行
- [ ] `tests/test_memory.py` 通过
- [ ] `tests/test_experience_store.py` 仍通过
- [ ] `python run_cli.py` / QQ Bot 启动不报 ImportError
