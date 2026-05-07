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


def test_add_turn_basic(tmp_storage):
    mem = ChatMemory("s1", storage_dir=tmp_storage)
    mem.add_turn("user", "hi")
    mem.add_turn("assistant", "hello")
    ctx = mem.get_context()
    assert len(ctx) == 2
    assert ctx[0]["role"] == "user"
    assert ctx[0]["content"] == "hi"
    assert ctx[1]["role"] == "assistant"
