"""FOC-Assistant 自反思模块 —— 执行后质量评估

在 Agent 完成一次任务后，用轻量 LLM 调用评估输出质量：
- 是否完整覆盖用户需求
- 是否有工具失败被忽略
- 输出是否有实际价值
- 是否需要重试
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI

from config import get_model_manager

REFLECTION_LOG = Path(__file__).parent / "reflection_log.jsonl"


@dataclass
class ReflectionResult:
    quality: float = 0.5          # 0-1 质量评分
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    should_retry: bool = False
    summary: str = ""


REFLECTION_PROMPT = """你是 FOC-Assistant 的输出质量评估器。你的任务是评估一次 Agent 执行的结果质量。

评估维度（每项 0-1 分，最终取加权平均）：
1. 完整性（0.3）：回答是否完整覆盖了用户的需求
2. 准确性（0.3）：技术内容是否正确，无明显错误
3. 实用性（0.2）：用户能否直接使用这个结果
4. 执行质量（0.2）：工具调用是否合理，是否有失败被忽略

只输出 JSON，不要任何解释。格式:
{"quality": 0.0到1的分数, "issues": ["问题1", "问题2"], "suggestions": ["建议1"], "should_retry": true/false, "summary": "一句话评估"}

should_retry 为 true 的条件：
- quality < 0.4
- 或有明确的工具失败导致结果不完整
- 或输出明显偏离了用户需求

注意：
- 如果 Agent 只是聊天/问答（没有工具调用），不要因为没有工具调用就扣分
- 代码类任务要检查是否写出了实际代码（不只是框架/伪代码）
- 文件操作类任务要检查文件是否真的被创建/修改了"""


def reflect_on_execution(
    task: str,
    response: str,
    tool_calls: list[dict],
    tool_results: list[str],
    intent: str = "",
) -> ReflectionResult:
    """评估一次 Agent 执行的质量。

    Args:
        task: 用户原始任务
        response: Agent 最终回复
        tool_calls: 工具调用列表 [{"name": ..., "args": ...}, ...]
        tool_results: 工具返回结果列表
        intent: 路由意图 (chat/qa/research/execution)
    """
    # 动态获取反思模型配置
    mm = get_model_manager()
    model_id = mm.get_model_for_task("reflection")
    model_cfg = mm.get_model_config(model_id)
    import os as _os
    api_key = _os.environ.get(model_cfg.get("api_key_env", ""), "") or model_cfg.get("api_key_default", "")
    base_url = model_cfg["base_url"]
    model_name = model_cfg["model_id"]

    if not api_key:
        return ReflectionResult(
            quality=0.5, summary="反思不可用: 缺少 API Key"
        )

    # 构建评估上下文
    tool_summary = ""
    if tool_calls:
        lines = []
        for i, (tc, tr) in enumerate(zip(tool_calls, tool_results), 1):
            name = tc.get("name", "?")
            args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)[:200]
            result_preview = (tr or "")[:300].replace("\n", " ")
            failed = any(err in (tr or "").lower() for err in ["错误", "error", "失败", "failed", "不存在"])
            status = "FAIL" if failed else "OK"
            lines.append(f"  {i}. [{status}] {name}({args_str}) → {result_preview}")
        tool_summary = "\n".join(lines)
    else:
        tool_summary = "(无工具调用)"

    user_prompt = (
        f"## 用户任务\n{task[:1000]}\n\n"
        f"## 路由意图\n{intent or '未知'}\n\n"
        f"## 工具调用记录\n{tool_summary}\n\n"
        f"## Agent 最终回复\n{response[:3000]}"
    )

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": REFLECTION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=500,
            stream=False,
            extra_body={"thinking_mode": "non-thinking"},
        )
        content = resp.choices[0].message.content or "{}"
        data = _parse_json(content)

        result = ReflectionResult(
            quality=max(0.0, min(float(data.get("quality", 0.5)), 1.0)),
            issues=data.get("issues", []),
            suggestions=data.get("suggestions", []),
            should_retry=bool(data.get("should_retry", False)),
            summary=data.get("summary", ""),
        )

        # 记录到日志
        _log_reflection(task[:200], result)
        return result

    except Exception as e:
        return ReflectionResult(
            quality=0.5, summary=f"反思异常: {e}"
        )


def _parse_json(content: str) -> dict:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _log_reflection(task_preview: str, result: ReflectionResult):
    try:
        REFLECTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(),
            "task": task_preview,
            "quality": result.quality,
            "issues": result.issues,
            "should_retry": result.should_retry,
            "summary": result.summary,
        }
        with REFLECTION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
