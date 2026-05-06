"""test_fact_store.py — FactStore 单元测试"""

import os
import tempfile
import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.fact_store import FactStore, cjk_ngrams, build_fts_query


@pytest.fixture
def store():
    """创建临时 FactStore"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = FactStore(db_path)
    yield s
    s.close()
    os.unlink(db_path)


class TestCjkNgrams:
    def test_chinese_bigrams(self):
        tokens = cjk_ngrams("电机控制")
        assert "电机" in tokens
        assert "机控" in tokens
        assert "控制" in tokens

    def test_chinese_trigrams(self):
        tokens = cjk_ngrams("电机控制")
        assert "电机控" in tokens
        assert "机控制" in tokens

    def test_ascii_passthrough(self):
        tokens = cjk_ngrams("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_mixed(self):
        tokens = cjk_ngrams("PMSM电机")
        assert "PMSM" in tokens
        assert "电机" in tokens

    def test_empty(self):
        tokens = cjk_ngrams("")
        assert tokens == []


class TestBuildFtsQuery:
    def test_chinese(self):
        q = build_fts_query("电流环")
        assert "电流" in q
        assert "流环" in q

    def test_english(self):
        q = build_fts_query("PI controller")
        assert "PI" in q
        assert "controller" in q


class TestFactStoreAdd:
    def test_add_single(self, store):
        rowid = store.add("Ki 过大会导致振荡", tags=["PI", "电流环"])
        assert rowid > 0

    def test_add_duplicate(self, store):
        """add 不做去重，两次都会成功"""
        id1 = store.add("Ki 过大会导致振荡")
        id2 = store.add("Ki 过大会导致振荡")
        assert id1 > 0
        assert id2 > 0
        assert store.count() == 2

    def test_add_batch(self, store):
        facts = [
            {"fact": "SVPWM 过调制会导致电流畸变"},
            {"fact": "ESO 带宽应设为控制环带宽的 3-5 倍"},
            {"fact": "弱磁控制需要考虑电压极限圆"},
        ]
        added = store.add_batch(facts)
        assert added == 3
        assert store.count() == 3

    def test_add_with_session(self, store):
        store.add("测试事实", session_id="session-123")
        assert store.count() == 1


class TestFactStoreSearch:
    def test_search_chinese(self, store):
        store.add("电流环 PI 参数 Kp=0.5, Ki=100", tags=["电流环"])
        store.add("速度环带宽应为电流环的 1/10", tags=["速度环"])
        results = store.search_full_text("电流环")
        assert len(results) >= 1

    def test_search_english(self, store):
        store.add("FOC control uses Park transformation")
        store.add("SVPWM generates three-phase voltages")
        results = store.search_full_text("FOC")
        assert len(results) >= 1

    def test_search_empty(self, store):
        results = store.search_full_text("不存在的内容")
        assert len(results) == 0

    def test_search_by_tags(self, store):
        store.add("事实1", tags=["PI"])
        store.add("事实2", tags=["SMC"])
        store.add("事实3", tags=["PI", "SMC"])
        results = store.search_by_tags(["PI"])
        assert len(results) == 2


class TestFactStoreLifecycle:
    def test_count(self, store):
        assert store.count() == 0
        store.add("test")
        assert store.count() == 1

    def test_delete(self, store):
        store.add("to delete")
        facts = store.search_full_text("to delete")
        assert len(facts) == 1
        store.delete(facts[0].id)
        assert store.count() == 0
