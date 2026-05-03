"""FOC-Assistant Tracing 系统

参考 OpenAI Agents SDK 的内置 Tracing，记录：
- 每次 LLM 调用的输入/输出/耗时/模型
- 每次工具调用的名称/参数/结果/耗时
- 子 Agent 的转交（Handoff）记录
- 整体任务的执行轨迹

日志输出到 logs/ 目录，JSONL 格式，便于后续分析。
"""

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

LOGS_DIR = Path(__file__).parent / "logs"


@dataclass
class TraceSpan:
    """一个 trace span，可以是 LLM 调用、工具调用、或整个任务。"""
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: Optional[str] = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    span_type: str = "unknown"  # "task" | "llm_call" | "tool_call" | "handoff" | "guardrail"
    name: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    duration_ms: float = 0.0
    status: str = "ok"  # "ok" | "error" | "blocked"
    metadata: dict = field(default_factory=dict)
    input_data: Optional[str] = None
    output_data: Optional[str] = None
    error: Optional[str] = None


class Tracer:
    """全局 Tracer，记录所有 Agent 活动。"""

    def __init__(self, enabled: bool = True, log_dir: Optional[Path] = None):
        self.enabled = enabled
        self.log_dir = log_dir or LOGS_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_trace_id: Optional[str] = None
        self._spans: list[TraceSpan] = []

    def set_trace_id(self, trace_id: str):
        self._current_trace_id = trace_id

    def start_trace(self, task: str) -> str:
        """开始一个新的 trace（整个任务）。"""
        trace_id = uuid.uuid4().hex[:12]
        self._current_trace_id = trace_id
        span = TraceSpan(
            trace_id=trace_id,
            span_type="task",
            name="agent_task",
            input_data=task[:500],
        )
        self._spans.append(span)
        self._write_span(span)
        return trace_id

    def end_trace(self, trace_id: str, output: str = "", status: str = "ok"):
        """结束一个 trace。"""
        for span in reversed(self._spans):
            if span.trace_id == trace_id and span.span_type == "task" and span.end_time == 0:
                span.end_time = time.time()
                span.duration_ms = (span.end_time - span.start_time) * 1000
                span.output_data = output[:500] if output else ""
                span.status = status
                self._write_span(span)
                break

    @contextmanager
    def trace_llm_call(self, model: str, messages_count: int, tools_count: int = 0,
                       thinking_mode: str = "", task_type: str = ""):
        """上下文管理器：追踪一次 LLM 调用。"""
        span = TraceSpan(
            trace_id=self._current_trace_id or "",
            span_type="llm_call",
            name=f"llm:{model}",
            metadata={
                "model": model,
                "messages_count": messages_count,
                "tools_count": tools_count,
                "thinking_mode": thinking_mode,
                "task_type": task_type,
            },
        )
        self._spans.append(span)
        start = time.time()
        try:
            yield span
            span.status = "ok"
        except Exception as e:
            span.status = "error"
            span.error = str(e)
            raise
        finally:
            span.end_time = time.time()
            span.duration_ms = (span.end_time - start) * 1000
            self._write_span(span)

    @contextmanager
    def trace_tool_call(self, tool_name: str, tool_args: dict):
        """上下文管理器：追踪一次工具调用。"""
        span = TraceSpan(
            trace_id=self._current_trace_id or "",
            span_type="tool_call",
            name=f"tool:{tool_name}",
            input_data=json.dumps(tool_args, ensure_ascii=False)[:300],
            metadata={"tool_name": tool_name},
        )
        self._spans.append(span)
        start = time.time()
        try:
            yield span
            span.status = "ok"
        except Exception as e:
            span.status = "error"
            span.error = str(e)
            raise
        finally:
            span.end_time = time.time()
            span.duration_ms = (span.end_time - start) * 1000
            self._write_span(span)

    def trace_handoff(self, from_agent: str, to_agent: str, task: str, result: str = ""):
        """记录一次 Handoff（子 Agent 转交）。"""
        span = TraceSpan(
            trace_id=self._current_trace_id or "",
            span_type="handoff",
            name=f"handoff:{from_agent}->{to_agent}",
            input_data=task[:300],
            output_data=result[:300] if result else "",
            metadata={"from": from_agent, "to": to_agent},
        )
        self._spans.append(span)
        self._write_span(span)

    def trace_guardrail(self, direction: str, rule: str, blocked: bool, detail: str = ""):
        """记录一次 Guardrail 拦截/放行。"""
        span = TraceSpan(
            trace_id=self._current_trace_id or "",
            span_type="guardrail",
            name=f"guardrail:{direction}:{rule}",
            status="blocked" if blocked else "ok",
            metadata={"direction": direction, "rule": rule, "blocked": blocked},
            output_data=detail[:300],
        )
        self._spans.append(span)
        self._write_span(span)

    def _write_span(self, span: TraceSpan):
        """写入一条 trace span 到日志文件。"""
        if not self.enabled:
            return
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = self.log_dir / f"trace_{today}.jsonl"
            entry = {
                "ts": datetime.fromtimestamp(span.start_time).isoformat(),
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_id": span.parent_id,
                "type": span.span_type,
                "name": span.name,
                "duration_ms": round(span.duration_ms, 1),
                "status": span.status,
                "metadata": span.metadata,
            }
            if span.input_data:
                entry["input"] = span.input_data
            if span.output_data:
                entry["output"] = span.output_data
            if span.error:
                entry["error"] = span.error
            with log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # tracing 不应影响主流程

    def get_summary(self, trace_id: Optional[str] = None) -> str:
        """获取 trace 摘要。"""
        spans = [s for s in self._spans if (trace_id is None or s.trace_id == trace_id)]
        if not spans:
            return "无 trace 记录"

        total_ms = sum(s.duration_ms for s in spans)
        llm_calls = [s for s in spans if s.span_type == "llm_call"]
        tool_calls = [s for s in spans if s.span_type == "tool_call"]
        handoffs = [s for s in spans if s.span_type == "handoff"]
        guardrails = [s for s in spans if s.span_type == "guardrail"]
        blocked = [s for s in guardrails if s.status == "blocked"]
        errors = [s for s in spans if s.status == "error"]

        lines = [
            f"Trace 摘要 ({len(spans)} spans)",
            f"  总耗时: {total_ms:.0f}ms",
            f"  LLM 调用: {len(llm_calls)} 次, 总耗时 {sum(s.duration_ms for s in llm_calls):.0f}ms",
            f"  工具调用: {len(tool_calls)} 次, 总耗时 {sum(s.duration_ms for s in tool_calls):.0f}ms",
            f"  Handoff: {len(handoffs)} 次",
            f"  Guardrail: {len(guardrails)} 次 (拦截 {len(blocked)} 次)",
            f"  错误: {len(errors)} 次",
        ]

        if llm_calls:
            lines.append("\n  LLM 调用明细:")
            for s in llm_calls:
                m = s.metadata
                lines.append(f"    {m.get('model', '?')} | {s.duration_ms:.0f}ms | tools={m.get('tools_count', 0)} | {s.status}")

        if tool_calls:
            lines.append("\n  工具调用明细:")
            for s in tool_calls:
                lines.append(f"    {s.name} | {s.duration_ms:.0f}ms | {s.status}")

        return "\n".join(lines)


# 全局单例
_tracer = Tracer()

def get_tracer() -> Tracer:
    return _tracer
