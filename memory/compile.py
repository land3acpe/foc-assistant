"""compile.py — 记忆编译器（v3 四块独立编译 + assemble）

从 OpenHanako 的 lib/memory/compile.js 移植。
四个独立函数各自有指纹缓存，互不依赖：
  compile_today()    → today.md（当天 sessions）
  compile_week()     → week.md（过去7天滑动窗口）
  compile_longterm() → longterm.md（fold 周报到长期）
  compile_facts()    → facts.md（重要事实，继承上一版）
  assemble()         → memory.md（≤2000 token）
"""

import hashlib
import os
import re
import tempfile
from datetime import datetime, timedelta
from typing import Callable, Awaitable, Optional

from memory.session_summary import SessionSummaryManager, SummaryData


# LLM 调用类型：(system_prompt, user_content, max_tokens) -> str
CompactLLMFunc = Callable[[str, str, int], Awaitable[str]]


def get_logical_day() -> tuple[datetime, datetime]:
    """获取逻辑日范围（凌晨 4:00 为分界）"""
    now = datetime.now()
    if now.hour < 4:
        range_start = (now - timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
    else:
        range_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    return range_start, now


def compute_fingerprint(keys: list[str]) -> str:
    """计算指纹（MD5）"""
    return hashlib.md5("\n".join(keys).encode()).hexdigest()


def atomic_write(file_path: str, content: str):
    """原子写入文件"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(file_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def safe_read_file(file_path: str, default: str = "") -> str:
    """安全读取文件"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return default


def normalize_compiled_section_body(text: str) -> str:
    """标准化编译段落内容"""
    if not text:
        return ""
    # 去掉开头的 Markdown 标题
    text = re.sub(r'^#{1,3}\s+.*\n*', '', text.strip())
    return text.strip() + "\n" if text.strip() else ""


async def compile_today(
    summary_manager: SessionSummaryManager,
    output_path: str,
    compact_llm: CompactLLMFunc,
    is_zh: bool = True,
    since: str = None,
) -> str:
    """编译今天的 session 摘要 → today.md"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    range_start, now = get_logical_day()
    sessions = summary_manager.get_summaries_in_range(range_start, now, since=since)
    fp_path = output_path + ".fingerprint"

    if not sessions:
        try:
            os.unlink(fp_path)
        except FileNotFoundError:
            pass
        if safe_read_file(output_path):
            atomic_write(output_path, "")
        return "compiled"

    fp_keys = [f"{s.session_id}:{s.updated_at}" for s in sessions]
    fp = compute_fingerprint(fp_keys)
    try:
        if safe_read_file(fp_path).strip() == fp and os.path.exists(output_path):
            return "skipped"
    except FileNotFoundError:
        pass

    input_text = "\n\n---\n\n".join(s.summary for s in sessions)

    system_prompt = (
        "请把今天的对话摘要整理成一份\"用户近况与大主题清单\"。\n\n"
        "提炼原则：\n"
        "- 把同一主题/项目的多次往返归并为一件事，不要逐条流水账\n"
        "- 时间标注用主时段（\"上午/傍晚\"或粗略 HH:MM 区间），不需精确到分钟\n"
        "- 记忆的核心职责是维护用户模型，优先记录用户是谁、喜欢什么、在意什么、最近关注什么\n"
        "- 工作相关内容只允许保留到大主题层级\n\n"
        "输出 3-5 条粗颗粒事件，每条 1-2 句。最多 300 字。"
        "不要输出 Markdown 标题，直接输出正文列表或段落。"
    ) if is_zh else (
        "Distill today's conversation summaries into a \"user-current-state and broad-theme list\".\n\n"
        "Output 3-5 coarse events, 1-2 sentences each. Max 180 words. "
        "Do not output Markdown headings; output body text only."
    )

    result = await compact_llm(system_prompt, input_text, 450)
    atomic_write(output_path, normalize_compiled_section_body(result))
    atomic_write(fp_path, fp)
    return "compiled"


async def compile_week(
    summary_manager: SessionSummaryManager,
    output_path: str,
    compact_llm: CompactLLMFunc,
    is_zh: bool = True,
    since: str = None,
) -> str:
    """编译过去 7 天滑动窗口的摘要 → week.md"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    now = datetime.now()
    seven_days_ago = now - timedelta(days=7)
    sessions = summary_manager.get_summaries_in_range(seven_days_ago, now, since=since)
    fp_path = output_path + ".fingerprint"

    if not sessions:
        try:
            os.unlink(fp_path)
        except FileNotFoundError:
            pass
        if safe_read_file(output_path):
            atomic_write(output_path, "")
        return "compiled"

    fp_keys = [f"{s.session_id}:{s.updated_at}" for s in sessions]
    fp = compute_fingerprint(fp_keys)
    try:
        if safe_read_file(fp_path).strip() == fp and os.path.exists(output_path):
            return "skipped"
    except FileNotFoundError:
        pass

    input_text = "\n\n---\n\n".join(s.summary for s in sessions)

    system_prompt = (
        "请把过去 7 天的对话摘要整理成一份\"本周用户主题概要\"。\n\n"
        "到 week 这一层，记录已经是粗线条的了。只保留用户这一周大致在关注什么、投入什么、发生了什么重要变化。\n\n"
        "输出 3-5 条本周主题/事件。最多 400 字。"
        "不要输出 Markdown 标题，直接输出正文列表或段落。"
    ) if is_zh else (
        "Distill the past 7 days' conversation summaries into a \"weekly user-theme overview\".\n\n"
        "Output 3-5 weekly themes/events. Max 240 words. "
        "Do not output Markdown headings; output body text only."
    )

    result = await compact_llm(system_prompt, input_text, 600)
    atomic_write(output_path, normalize_compiled_section_body(result))
    atomic_write(fp_path, fp)
    return "compiled"


async def compile_longterm(
    week_md_path: str,
    longterm_path: str,
    compact_llm: CompactLLMFunc,
    is_zh: bool = True,
) -> str:
    """将 week.md fold 进 longterm.md（每日一次）"""
    os.makedirs(os.path.dirname(longterm_path), exist_ok=True)

    week_content = safe_read_file(week_md_path).strip()
    if not week_content:
        return "skipped"

    fp = compute_fingerprint([week_content])
    fp_path = longterm_path + ".fingerprint"
    try:
        if safe_read_file(fp_path).strip() == fp and os.path.exists(longterm_path):
            return "skipped"
    except FileNotFoundError:
        pass

    prev_longterm = safe_read_file(longterm_path).strip()

    if prev_longterm:
        input_text = (
            f"## 上一份长期情况\n\n{prev_longterm}\n\n## 本周新增\n\n{week_content}"
            if is_zh else
            f"## Previous long-term context\n\n{prev_longterm}\n\n## This week's additions\n\n{week_content}"
        )
    else:
        input_text = week_content

    system_prompt = (
        "请把以下内容整合成一份长期用户画像记录。\n\n"
        "只保留\"如果一年后回看仍然适合用来理解用户这个人\"的内容。\n"
        "最多 400 字。不要输出 Markdown 标题，直接输出正文列表或段落。"
    ) if is_zh else (
        "Consolidate the following into a long-term user-profile record.\n\n"
        "Max 240 words. Do not output Markdown headings; output body text only."
    )

    result = await compact_llm(system_prompt, input_text, 600)
    atomic_write(longterm_path, normalize_compiled_section_body(result))
    atomic_write(fp_path, fp)
    return "compiled"


async def compile_facts(
    summary_manager: SessionSummaryManager,
    output_path: str,
    compact_llm: CompactLLMFunc,
    is_zh: bool = True,
    since: str = None,
) -> str:
    """从近期 session 摘要的 ## 重要事实 段编译 facts.md"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    prev_facts = safe_read_file(output_path).strip()

    now = datetime.now()
    thirty_days_ago = now - timedelta(days=30)
    sessions = summary_manager.get_summaries_in_range(thirty_days_ago, now, since=since)

    fact_parts = []
    for s in sessions:
        if not s.summary:
            continue
        m = re.search(r'##\s*重要事实\s*\n([\s\S]*?)(?=\n##|$)', s.summary)
        if m:
            text = m.group(1).strip()
            if text and text != "无":
                fact_parts.append(text)

    if not fact_parts:
        if not prev_facts:
            atomic_write(output_path, "")
        return "compiled"

    new_facts = "\n".join(fact_parts)
    combined = f"{prev_facts}\n{new_facts}" if prev_facts else new_facts

    system_prompt = (
        "将以下重要事实去重合并（200字以内）。"
        "只保留稳定的、跨时间有效的用户画像。"
        "矛盾时以最新为准。不要输出 Markdown 标题，直接输出正文列表或段落。"
    ) if is_zh else (
        "Deduplicate and merge the following key facts (under 120 words). "
        "Keep only stable, time-persistent user-profile facts. "
        "When facts conflict, prefer the latest. "
        "Do not output Markdown headings; output body text only."
    )

    result = await compact_llm(system_prompt, combined, 300)
    atomic_write(output_path, normalize_compiled_section_body(result))
    return "compiled"


def assemble(
    facts_path: str,
    today_path: str,
    week_path: str,
    longterm_path: str,
    memory_md_path: str,
    is_zh: bool = True,
):
    """将四个中间文件组装成 memory.md（同步，不调 LLM）"""
    facts = normalize_compiled_section_body(safe_read_file(facts_path))
    today = normalize_compiled_section_body(safe_read_file(today_path))
    week = normalize_compiled_section_body(safe_read_file(week_path))
    longterm = normalize_compiled_section_body(safe_read_file(longterm_path))

    empty = "（暂无）" if is_zh else "(none)"

    def section(title: str, content: str) -> str:
        return f"## {title}\n\n{content or empty}"

    md = "\n\n".join([
        section("重要事实" if is_zh else "Key facts", facts),
        section("今天" if is_zh else "Today", today),
        section("本周早些时候" if is_zh else "Earlier this week", week),
        section("长期情况" if is_zh else "Long-term context", longterm),
    ]) + "\n"

    atomic_write(memory_md_path, md)
