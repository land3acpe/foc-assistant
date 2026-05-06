"""test_memory_api.py — MemoryAPI 集成测试"""

import os
import tempfile
import shutil
import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.memory_api import MemoryAPI


@pytest.fixture
def api():
    tmpdir = tempfile.mkdtemp()
    a = MemoryAPI(tmpdir)
    yield a
    a.close()
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestFactOperations:
    def test_add_and_search(self, api):
        api.add_fact("Ki 过大会导致振荡", tags=["PI"])
        results = api.search_facts("振荡")
        assert len(results) >= 1

    def test_add_returns_id(self, api):
        id1 = api.add_fact("test fact")
        assert id1 > 0


class TestSessionSummary:
    def test_save_and_get(self, api):
        api.save_session_summary("s1", "讨论了 PI 参数整定")
        summary = api.get_session_summary("s1")
        assert summary == "讨论了 PI 参数整定"

    def test_get_nonexistent(self, api):
        assert api.get_session_summary("no-such") is None


class TestExperience:
    def test_record_and_recall(self, api):
        result = api.record_experience("PI调参", "先用小 Ki 启动", tags=["PI"])
        assert "已记录" in result

        result = api.recall_experience(category="PI调参")
        assert "先用小 Ki" in result

    def test_recall_search(self, api):
        api.record_experience("调试", "电流环振荡问题")
        result = api.recall_experience(query="振荡")
        assert "振荡" in result

    def test_experience_prompt(self, api):
        api.record_experience("PI调参", "经验1")
        prompt = api.get_experience_prompt()
        assert "经验库" in prompt


class TestLifecycle:
    def test_cleanup(self, api):
        api.add_fact("old fact")
        # cleanup 不会报错
        api.cleanup(days=1)
