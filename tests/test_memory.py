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
