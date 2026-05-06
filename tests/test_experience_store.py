"""test_experience_store.py — ExperienceStore 单元测试"""

import os
import tempfile
import pytest
import json

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experience.experience_store import ExperienceStore
from experience.experience_tools import ExperienceToolExecutor, get_experience_prompt_section


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = ExperienceStore(db_path)
    yield s
    os.unlink(db_path)


@pytest.fixture
def executor(store):
    return ExperienceToolExecutor(store)


class TestExperienceAdd:
    def test_add_single(self, store):
        ok, reason = store.add("PI调参", "Ki 过大会导致低频振荡", tags=["PI", "振荡"])
        assert ok is True
        assert reason == "added"

    def test_add_duplicate(self, store):
        store.add("PI调参", "Ki 过大会导致低频振荡")
        ok, reason = store.add("PI调参", "Ki 过大会导致低频振荡")
        assert ok is False
        assert reason == "duplicate"

    def test_add_empty(self, store):
        ok, reason = store.add("", "content")
        assert ok is False
        ok, reason = store.add("cat", "")
        assert ok is False

    def test_add_batch(self, store):
        entries = [
            {"category": "PI调参", "content": "经验1"},
            {"category": "PI调参", "content": "经验2"},
            {"category": "SVPWM", "content": "经验3"},
        ]
        added = store.add_batch(entries)
        assert added == 3


class TestExperienceSearch:
    def test_search(self, store):
        store.add("PI调参", "电流环 Ki 过大会导致振荡")
        store.add("SVPWM", "过调制会导致电流畸变")
        results = store.search("电流")
        assert len(results) >= 1

    def test_search_empty(self, store):
        results = store.search("不存在")
        assert len(results) == 0


class TestExperienceCategory:
    def test_get_categories(self, store):
        store.add("PI调参", "经验1")
        store.add("PI调参", "经验2")
        store.add("SVPWM", "经验3")
        cats = store.get_categories()
        assert len(cats) == 2
        pi_cat = next(c for c in cats if c.name == "PI调参")
        assert pi_cat.count == 2

    def test_get_by_category(self, store):
        store.add("PI调参", "经验1")
        store.add("PI调参", "经验2")
        entries = store.get_by_category("PI调参")
        assert len(entries) == 2

    def test_index_text(self, store):
        store.add("PI调参", "经验1")
        index = store.get_index_text()
        assert "PI调参" in index


class TestExperienceTools:
    def test_recall_index(self, executor):
        result = executor.execute("recall_experience", {})
        data = json.loads(result)
        assert "index" in data

    def test_recall_category(self, executor):
        executor.execute("record_experience", {"category": "PI调参", "content": "经验1"})
        result = executor.execute("recall_experience", {"category": "PI调参"})
        data = json.loads(result)
        assert data["count"] == 1

    def test_recall_search(self, executor):
        executor.execute("record_experience", {"category": "PI调参", "content": "电流环振荡"})
        result = executor.execute("recall_experience", {"query": "振荡"})
        data = json.loads(result)
        assert data["count"] >= 1

    def test_record(self, executor):
        result = executor.execute("record_experience", {
            "category": "调试",
            "content": "先用小 Ki 启动",
            "tags": ["PI"],
        })
        data = json.loads(result)
        assert "已记录" in data["result"]

    def test_record_duplicate(self, executor):
        executor.execute("record_experience", {"category": "调试", "content": "经验"})
        result = executor.execute("record_experience", {"category": "调试", "content": "经验"})
        data = json.loads(result)
        assert "已存在" in data["result"]


class TestExperiencePrompt:
    def test_empty_store(self, store):
        prompt = get_experience_prompt_section(store)
        assert prompt == ""

    def test_with_entries(self, store):
        store.add("PI调参", "经验1")
        prompt = get_experience_prompt_section(store)
        assert "经验库" in prompt
        assert "PI调参" in prompt
