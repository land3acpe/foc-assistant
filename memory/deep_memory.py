"""deep_memory.py — 深度记忆处理器

从 OpenHanako 的 lib/memory/deep-memory.js 移植。
每日执行一次。遍历所有"脏" session（summary !== snapshot），
通过 snapshot diff 发现新增内容，调 LLM 拆成元事实 + 打标签，写入 FactStore。
"""

import asyncio
import json
import re
import time
from typing import Callable, Awaitable

from memory.session_summary import SessionSummaryManager
from memory.fact_store import FactStore

MAX_RETRIES = 3
MAX_CONCURRENT = 3
FAIL_COUNT_TTL_S = 3600  # 1 小时

# session → { count, last_updated }
_fail_counts: dict[str, dict] = {}


def _clean_expired_fail_counts():
    """清理过期的失败计数"""
    cutoff = time.time() - FAIL_COUNT_TTL_S
    expired = [k for k, v in _fail_counts.items() if v["last_updated"] < cutoff]
    for k in expired:
        del _fail_counts[k]


# LLM 调用类型
CompactLLMFunc = Callable[[str, str, int], Awaitable[str]]


def build_fact_extraction_prompt(has_previous: bool, is_zh: bool = True) -> str:
    """构建元事实提取 prompt"""
    if is_zh:
        diff_instruction = (
            "你会收到两部分输入：\n"
            "1. **上次快照**：上次已处理的摘要内容\n"
            "2. **当前摘要**：最新的完整摘要\n\n"
            "请找出\"当前摘要\"相对于\"上次快照\"新增或变化的内容，将其拆分成独立的元事实。\n"
            "已经在上次快照中存在的内容不要重复提取。"
        ) if has_previous else "将以下摘要内容拆分成独立的元事实。"

        return f"""你是一个记忆拆分器。{diff_instruction}

## 规则

1. 只提取用户画像和粗颗粒近况相关的客观事实。

2. 禁止提取工作方式偏好、协作流程偏好、工具偏好、项目工程规则、执行细节。

3. 每条事实必须是原子的（一条只记一件事）。

4. 标签用于后续检索，选择有辨识度的关键词，2~5 个。

5. time 字段从摘要中的时间标注提取，格式 YYYY-MM-DDTHH:MM。无法确定填 null。

6. 不要提取助手的内心活动，只提取客观事实和事件。

7. 如果没有新增内容值得提取，返回空数组 []。

## 输出格式

严格 JSON 数组，不要 markdown 代码块：
[
  {{"fact": "用户最近在关注记忆系统", "tags": ["记忆系统", "近况"], "time": "2026-03-01T14:30"}}
]"""
    else:
        diff_instruction = (
            "You will receive two inputs:\n"
            "1. **Previous Snapshot**: the summary content from last processing\n"
            "2. **Current Summary**: the latest full summary\n\n"
            "Find content that is new or changed in \"Current Summary\" compared to \"Previous Snapshot\", "
            "and split it into independent atomic facts.\n"
            "Do not re-extract content that already exists in the previous snapshot."
        ) if has_previous else "Split the following summary content into independent atomic facts."

        return f"""You are a memory splitter. {diff_instruction}

## Rules

1. Extract only objective facts about the user profile and coarse current state.

2. Do not extract work-style preferences, collaboration-process preferences, tool preferences, or execution details.

3. Each fact must be atomic (one fact per entry).

4. Tags are for later retrieval; choose distinctive keywords, 2-5 per fact.

5. The time field should be extracted from time annotations in the summary, format YYYY-MM-DDTHH:MM. Use null if uncertain.

6. Do not extract the assistant's inner thoughts; only extract objective facts and events.

7. If there is no new content worth extracting, return an empty array [].

## Output Format

Strict JSON array, no markdown code blocks:
[
  {{"fact": "The user has recently been focused on memory systems", "tags": ["memory-systems", "current-state"], "time": "2026-03-01T14:30"}}
]"""


async def extract_facts_from_diff(
    current_summary: str,
    previous_snapshot: str,
    compact_llm: CompactLLMFunc,
    is_zh: bool = True,
) -> list[dict]:
    """从摘要 diff 中提取元事实"""
    has_previous = bool(previous_snapshot)

    if has_previous:
        prev_label = "## 上次快照" if is_zh else "## Previous Snapshot"
        curr_label = "## 当前摘要" if is_zh else "## Current Summary"
        user_content = f"{prev_label}\n\n{previous_snapshot}\n\n{curr_label}\n\n{current_summary}"
    else:
        label = "## 摘要内容" if is_zh else "## Summary Content"
        user_content = f"{label}\n\n{current_summary}"

    system_prompt = build_fact_extraction_prompt(has_previous, is_zh)
    raw = await compact_llm(system_prompt, user_content, 4096)

    # 兼容 markdown 代码块包裹
    fence_match = re.match(r'^```(?:json)?\s*\n([\s\S]*?)\n\s*```\s*$', raw)
    json_str = (fence_match.group(1) if fence_match else raw).strip()

    try:
        facts = json.loads(json_str)
        if not isinstance(facts, list):
            return []
        return [f for f in facts if f and isinstance(f.get("fact"), str) and f["fact"]]
    except json.JSONDecodeError:
        print(f"[deep-memory] JSON 解析失败: {json_str[:200]}")
        return []


async def process_dirty_sessions(
    summary_manager: SessionSummaryManager,
    fact_store: FactStore,
    compact_llm: CompactLLMFunc,
    is_zh: bool = True,
    since: str = None,
) -> dict:
    """处理所有脏 session，提取新增元事实写入 fact-store"""
    dirty = summary_manager.get_dirty_sessions(since=since)
    if not dirty:
        return {"processed": 0, "facts_added": 0}

    print(f"[deep-memory] {len(dirty)} 个脏 session 待处理")
    total_facts = 0

    async def process_one(session):
        nonlocal total_facts
        try:
            facts = await extract_facts_from_diff(
                session.summary,
                session.snapshot or "",
                compact_llm,
                is_zh=is_zh,
            )

            if facts:
                fact_store.add_batch([
                    {
                        "fact": f["fact"],
                        "tags": f.get("tags", []),
                        "time": f.get("time"),
                        "session_id": session.session_id,
                    }
                    for f in facts
                ])
                total_facts += len(facts)
                print(f"[deep-memory] {session.session_id[:8]}...: {len(facts)} 条元事实")

            summary_manager.mark_processed(session.session_id)
            _fail_counts.pop(session.session_id, None)
        except Exception as e:
            _clean_expired_fail_counts()
            prev = _fail_counts.get(session.session_id, {"count": 0})
            count = prev["count"] + 1
            _fail_counts[session.session_id] = {"count": count, "last_updated": time.time()}

            if count >= MAX_RETRIES:
                print(f"[deep-memory] {session.session_id[:8]}... 连续失败 {count} 次，标记跳过: {e}")
                summary_manager.mark_processed(session.session_id)
                _fail_counts.pop(session.session_id, None)
            else:
                print(f"[deep-memory] 处理失败 ({session.session_id[:8]}... {count}/{MAX_RETRIES}): {e}")

    # 分批并行处理
    for i in range(0, len(dirty), MAX_CONCURRENT):
        batch = dirty[i:i + MAX_CONCURRENT]
        await asyncio.gather(*[process_one(s) for s in batch], return_exceptions=True)

    print(f"[deep-memory] 完成：{len(dirty)} 个 session，{total_facts} 条新元事实")
    return {"processed": len(dirty), "facts_added": total_facts}
