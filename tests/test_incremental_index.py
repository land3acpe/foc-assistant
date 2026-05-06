"""测试知识库增量索引"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch


def test_build_index_records_mtime(tmp_path):
    """build_index 应记录每个文件的 mtime"""
    from knowledge import KnowledgeBase

    kb = KnowledgeBase()
    note = tmp_path / "test.md"
    note.write_text("# Test\nHello world", encoding="utf-8")

    with patch("knowledge.KB_INDEX_PATH", tmp_path / "index.json"), \
         patch("knowledge.DOC_SOURCES", [(tmp_path, "*.md", "test")]), \
         patch("knowledge.PAPER_SOURCES", []), \
         patch("knowledge.CSV_SOURCES", []), \
         patch("knowledge.CODE_SOURCES", []):
        kb.build_index()

    index_data = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert "file_mtimes" in index_data
    assert str(note) in index_data["file_mtimes"]


def test_incremental_build_skips_unchanged(tmp_path):
    """未变更的文件不应重新索引"""
    from knowledge import KnowledgeBase

    note = tmp_path / "test.md"
    note.write_text("# Test\nHello world", encoding="utf-8")

    kb = KnowledgeBase()
    with patch("knowledge.KB_INDEX_PATH", tmp_path / "index.json"), \
         patch("knowledge.DOC_SOURCES", [(tmp_path, "*.md", "test")]), \
         patch("knowledge.PAPER_SOURCES", []), \
         patch("knowledge.CSV_SOURCES", []), \
         patch("knowledge.CODE_SOURCES", []):
        kb.build_index()
        first_count = len(kb.documents)

        # 不修改文件，再次构建
        kb.loaded = False
        kb.build_index()
        second_count = len(kb.documents)

    assert first_count == second_count


def test_incremental_build_reindexes_changed(tmp_path):
    """修改过的文件应被重新索引"""
    from knowledge import KnowledgeBase

    note = tmp_path / "test.md"
    note.write_text("# Test\nHello world", encoding="utf-8")

    kb = KnowledgeBase()
    with patch("knowledge.KB_INDEX_PATH", tmp_path / "index.json"), \
         patch("knowledge.DOC_SOURCES", [(tmp_path, "*.md", "test")]), \
         patch("knowledge.PAPER_SOURCES", []), \
         patch("knowledge.CSV_SOURCES", []), \
         patch("knowledge.CODE_SOURCES", []):
        kb.build_index()
        first_count = len(kb.documents)

        # 修改文件
        time.sleep(0.1)
        note.write_text("# Test\nHello world\n\nNew content added!", encoding="utf-8")

        kb.loaded = False
        kb.build_index()
        second_count = len(kb.documents)

    assert second_count >= first_count


def test_incremental_build_removes_old_chunks(tmp_path):
    """重新索引时应移除旧文件的 chunks"""
    from knowledge import KnowledgeBase

    note = tmp_path / "test.md"
    # 用多个段落生成多个 chunks（_chunk_text 按 \n\n 分段，chunk_size=500）
    paragraphs = ["Paragraph " + str(i) + ". " + "x" * 100 for i in range(20)]
    note.write_text("\n\n".join(paragraphs), encoding="utf-8")

    kb = KnowledgeBase()
    with patch("knowledge.KB_INDEX_PATH", tmp_path / "index.json"), \
         patch("knowledge.DOC_SOURCES", [(tmp_path, "*.md", "test")]), \
         patch("knowledge.PAPER_SOURCES", []), \
         patch("knowledge.CSV_SOURCES", []), \
         patch("knowledge.CODE_SOURCES", []):
        kb.build_index()
        first_count = len(kb.documents)
        assert first_count > 1  # 应有多个 chunks

        # 缩短文件
        time.sleep(0.1)
        note.write_text("Short", encoding="utf-8")

        kb.loaded = False
        kb.build_index()
        second_count = len(kb.documents)

    assert second_count < first_count  # chunks 应减少
