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
