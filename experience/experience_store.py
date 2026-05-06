"""experience_store.py — 经验库存储

从 OpenHanako 的 lib/tools/experience.js 移植。
使用 SQLite + FTS5 存储经验条目，支持全文检索。
原版使用 Markdown 文件，本版改用 SQLite 以统一存储方案。
"""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# CJK 字符正则
_CJK_RE = re.compile(r'[一-鿿぀-ゟ゠-ヿ가-힯]+')


def _cjk_ngrams(text: str) -> str:
    """提取 CJK 2-gram 和 3-gram 用于 FTS5 索引"""
    tokens = []
    # ASCII 词
    for word in re.finditer(r'[A-Za-z0-9_]+', text):
        tokens.append(word.group())
    # CJK ngram
    for match in _CJK_RE.finditer(text):
        chars = list(match.group())
        for size in [2, 3]:
            for i in range(len(chars) - size + 1):
                tokens.append("".join(chars[i:i + size]))
    return " ".join(tokens)


def _build_fts_query(query: str) -> str:
    """构建 FTS5 查询（支持 CJK ngram）"""
    # 提取所有 token
    tokens = []
    # ASCII 词
    for word in re.finditer(r'[A-Za-z0-9_]+', query):
        tokens.append(word.group())
    # CJK ngram
    for match in _CJK_RE.finditer(query):
        chars = list(match.group())
        for size in [2, 3]:
            for i in range(len(chars) - size + 1):
                tokens.append("".join(chars[i:i + size]))
    # 去重
    seen = set()
    unique = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    if not unique:
        return ""
    return " OR ".join(f'"{t}"' for t in unique)


@dataclass
class ExperienceEntry:
    """单条经验"""
    id: str
    category: str
    content: str
    tags: list[str] = field(default_factory=list)
    source: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ExperienceCategory:
    """经验分类"""
    name: str
    count: int
    description: str = ""


class ExperienceStore:
    """经验库存储（SQLite + FTS5）"""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # 主表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiences (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # FTS5 索引（含 CJK ngram 搜索文本）
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS experiences_fts USING fts5(
                content, category, tags, search_text,
                content_rowid='rowid',
                tokenize='unicode61'
            )
        """)

        # FTS5 触发器
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS experiences_ai AFTER INSERT ON experiences BEGIN
                INSERT INTO experiences_fts(rowid, content, category, tags, search_text)
                VALUES (new.rowid, new.content, new.category, new.tags, '');
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS experiences_ad AFTER DELETE ON experiences BEGIN
                INSERT INTO experiences_fts(experiences_fts, rowid, content, category, tags, search_text)
                VALUES ('delete', old.rowid, old.content, old.category, old.tags, '');
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS experiences_au AFTER UPDATE ON experiences BEGIN
                INSERT INTO experiences_fts(experiences_fts, rowid, content, category, tags)
                VALUES ('delete', old.rowid, old.content, old.category, old.tags);
                INSERT INTO experiences_fts(rowid, content, category, tags)
                VALUES (new.rowid, new.content, new.category, new.tags);
            END
        """)

        # 版本表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO schema_version (key, value)
            VALUES ('experience_store_version', ?)
        """, (self.SCHEMA_VERSION,))

        # 索引
        conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_category ON experiences(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_created ON experiences(created_at)")

        conn.commit()
        conn.close()

    def _generate_id(self, category: str, content: str) -> str:
        """生成确定性 ID（基于 category + content hash）"""
        text = f"{category}:{content}"
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def add(
        self,
        category: str,
        content: str,
        tags: list[str] = None,
        source: str = "",
        dedup: bool = True,
    ) -> tuple[bool, str]:
        """添加一条经验

        Args:
            category: 分类名称
            content: 经验内容
            tags: 标签列表
            source: 来源
            dedup: 是否去重

        Returns:
            (added, reason) — added=True 表示新增，False 表示重复
        """
        category = category.strip()
        content = content.strip()
        if not category or not content:
            return False, "empty input"

        tags = tags or []

        # 去重检查
        if dedup:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT id FROM experiences WHERE category = ? AND content = ?",
                (category, content),
            ).fetchone()
            conn.close()
            if row:
                return False, "duplicate"

        now = datetime.now().isoformat()
        entry_id = self._generate_id(category, content)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO experiences
                (id, category, content, tags, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (entry_id, category, content, json.dumps(tags, ensure_ascii=False), source, now, now))
            # 更新 FTS5 search_text（CJK ngram）
            search_text = _cjk_ngrams(content + " " + category + " " + " ".join(tags))
            rowid = conn.execute("SELECT rowid FROM experiences WHERE id = ?", (entry_id,)).fetchone()
            if rowid:
                conn.execute(
                    "UPDATE experiences_fts SET search_text = ? WHERE rowid = ?",
                    (search_text, rowid[0]),
                )
            conn.commit()
        finally:
            conn.close()

        return True, "added"

    def add_batch(self, entries: list[dict]) -> int:
        """批量添加经验，返回实际新增数"""
        added = 0
        for entry in entries:
            ok, _ = self.add(
                category=entry.get("category", ""),
                content=entry.get("content", ""),
                tags=entry.get("tags", []),
                source=entry.get("source", ""),
            )
            if ok:
                added += 1
        return added

    def search(self, query: str, limit: int = 10) -> list[ExperienceEntry]:
        """全文搜索经验"""
        if not query.strip():
            return []
        query = query.strip()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 构建 FTS 查询（支持 CJK ngram）
        fts_query = _build_fts_query(query)

        # FTS5 搜索
        try:
            rows = conn.execute("""
                SELECT e.id, e.category, e.content, e.tags, e.source, e.created_at, e.updated_at
                FROM experiences_fts f
                JOIN experiences e ON e.rowid = f.rowid
                WHERE experiences_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit)).fetchall()
        except sqlite3.OperationalError:
            # FTS5 不支持的查询语法，降级为 LIKE
            like_pattern = f"%{query}%"
            rows = conn.execute("""
                SELECT id, category, content, tags, source, created_at, updated_at
                FROM experiences
                WHERE content LIKE ? OR category LIKE ? OR tags LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (like_pattern, like_pattern, like_pattern, limit)).fetchall()

        conn.close()
        return [self._row_to_entry(row) for row in rows]

    def get_by_category(self, category: str, limit: int = 50) -> list[ExperienceEntry]:
        """按分类获取经验"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, category, content, tags, source, created_at, updated_at
            FROM experiences
            WHERE category = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (category, limit)).fetchall()
        conn.close()
        return [self._row_to_entry(row) for row in rows]

    def get_categories(self) -> list[ExperienceCategory]:
        """获取所有分类及其条目数"""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("""
            SELECT category, COUNT(*) as cnt
            FROM experiences
            GROUP BY category
            ORDER BY cnt DESC
        """).fetchall()
        conn.close()

        result = []
        for row in rows:
            # 取该分类下第一条内容的前 20 字作为描述
            conn2 = sqlite3.connect(self.db_path)
            first = conn2.execute(
                "SELECT content FROM experiences WHERE category = ? ORDER BY created_at LIMIT 1",
                (row[0],),
            ).fetchone()
            conn2.close()
            desc = first[0][:20] + "…" if first and len(first[0]) > 20 else (first[0] if first else "")
            result.append(ExperienceCategory(name=row[0], count=row[1], description=desc))
        return result

    def get_index_text(self) -> str:
        """生成索引文本（供 recall_experience 无参调用）"""
        categories = self.get_categories()
        if not categories:
            return "经验库为空。使用 record_experience 工具记录经验。"

        blocks = []
        for cat in categories:
            blocks.append(f"# {cat.name}（{cat.count} 条）\n{cat.description}")
        return "\n\n".join(blocks) + "\n"

    def get_by_id(self, entry_id: str) -> Optional[ExperienceEntry]:
        """按 ID 获取经验"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, category, content, tags, source, created_at, updated_at FROM experiences WHERE id = ?",
            (entry_id,),
        ).fetchone()
        conn.close()
        return self._row_to_entry(row) if row else None

    def delete(self, entry_id: str) -> bool:
        """删除一条经验"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("DELETE FROM experiences WHERE id = ?", (entry_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted

    def delete_category(self, category: str) -> int:
        """删除整个分类"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("DELETE FROM experiences WHERE category = ?", (category,))
        conn.commit()
        count = cursor.rowcount
        conn.close()
        return count

    def count(self) -> int:
        """获取总条目数"""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()
        conn.close()
        return row[0] if row else 0

    def cleanup_old(self, days: int = 90):
        """清理旧记录"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"DELETE FROM experiences WHERE created_at < datetime('now', '-{days} days')"
        )
        conn.commit()
        conn.close()

    def _row_to_entry(self, row) -> ExperienceEntry:
        """数据库行 → ExperienceEntry"""
        tags_raw = row["tags"] if isinstance(row, sqlite3.Row) else row[3]
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []

        return ExperienceEntry(
            id=row["id"] if isinstance(row, sqlite3.Row) else row[0],
            category=row["category"] if isinstance(row, sqlite3.Row) else row[1],
            content=row["content"] if isinstance(row, sqlite3.Row) else row[2],
            tags=tags,
            source=row["source"] if isinstance(row, sqlite3.Row) else row[4],
            created_at=row["created_at"] if isinstance(row, sqlite3.Row) else row[5],
            updated_at=row["updated_at"] if isinstance(row, sqlite3.Row) else row[6],
        )
