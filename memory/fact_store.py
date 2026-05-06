"""fact_store.py — 深度记忆存储（元事实 + 标签）

从 OpenHanako 的 lib/memory/fact-store.js 移植。
使用 SQLite + FTS5 全文搜索，支持 CJK ngram 分词。
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Schema 版本，每次改表结构时递增
SCHEMA_VERSION = 2

# CJK 字符正则（中文、日文、韩文）
CJK_RUN_RE = re.compile(r'[一-鿿぀-ゟ゠-ヿ가-힯]+')


@dataclass
class Fact:
    """元事实"""
    id: int
    fact: str
    tags: list[str] = field(default_factory=list)
    time: Optional[str] = None
    session_id: Optional[str] = None
    created_at: str = ""
    match_count: Optional[int] = None


def normalize_search_text(text: str) -> str:
    """标准化搜索文本"""
    return (text or "").strip()


def parse_tags(raw_tags) -> list[str]:
    """解析标签（兼容 JSON 字符串和列表）"""
    if isinstance(raw_tags, list):
        return [t for t in raw_tags if isinstance(t, str)]
    try:
        tags = json.loads(raw_tags or "[]")
        return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def cjk_ngrams(text: str) -> list[str]:
    """提取 CJK 2-gram 和 3-gram 以及 ASCII 词用于 FTS5 索引"""
    tokens = []
    normalized = normalize_search_text(text)
    # 提取 ASCII 词
    for word in re.finditer(r'[A-Za-z0-9_]+', normalized):
        tokens.append(word.group())
    # 提取 CJK ngram
    for match in CJK_RUN_RE.finditer(normalized):
        chars = list(match.group())
        for size in [2, 3]:
            if len(chars) < size:
                continue
            for i in range(len(chars) - size + 1):
                tokens.append("".join(chars[i:i + size]))
    return tokens


def unique_tokens(tokens: list[str]) -> list[str]:
    """去重保序"""
    seen = set()
    out = []
    for token in tokens:
        normalized = normalize_search_text(token)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def build_fact_search_text(fact: str, tags: list[str] = None) -> str:
    """构建 FTS5 索引文本（fact + tags + CJK ngram）"""
    tags = tags or []
    parts = [normalize_search_text(fact)] + [normalize_search_text(t) for t in tags]
    base = " ".join(p for p in parts if p)
    grams = cjk_ngrams(base)
    return " ".join(unique_tokens([base] + grams))


def build_fts_query(query: str) -> str:
    """构建 FTS5 查询（支持 CJK ngram）"""
    normalized = normalize_search_text(query)
    if not normalized:
        return ""
    lexical_tokens = normalized.split()
    grams = cjk_ngrams(normalized)
    return " OR ".join(
        f'"{w.replace(chr(34), chr(34)*2)}"'
        for w in unique_tokens(lexical_tokens + grams)
    )


def has_cjk(text: str) -> bool:
    """判断文本是否包含 CJK 字符"""
    return bool(CJK_RUN_RE.search(normalize_search_text(text)))


class FactStore:
    """深度记忆存储（SQLite + FTS5）"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA cache_size = -16000")  # 16MB
        self.conn.execute("PRAGMA temp_store = MEMORY")
        self._init_schema()
        self._migrate()
        self._create_fts_triggers()
        self._tag_search_cache: dict[str, str] = {}

    def _init_schema(self):
        """初始化表结构"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                fact       TEXT NOT NULL,
                search_text TEXT NOT NULL DEFAULT '',
                tags       TEXT NOT NULL DEFAULT '[]',
                time       TEXT,
                session_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_facts_time ON facts(time);
            CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(session_id);
        """)
        self._ensure_search_text_column()

        # FTS5 全文搜索
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                    fact,
                    search_text,
                    content=facts,
                    content_rowid=id,
                    tokenize='unicode61'
                )
            """)
        except sqlite3.OperationalError:
            pass  # 表已存在

    def _create_fts_triggers(self):
        """创建 FTS 同步触发器"""
        self.conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                INSERT INTO facts_fts(rowid, fact, search_text)
                VALUES (new.id, new.fact, new.search_text);
            END;
            CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, fact, search_text)
                VALUES ('delete', old.id, old.fact, old.search_text);
            END;
            CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, fact, search_text)
                VALUES ('delete', old.id, old.fact, old.search_text);
                INSERT INTO facts_fts(rowid, fact, search_text)
                VALUES (new.id, new.fact, new.search_text);
            END;
        """)

    def _ensure_search_text_column(self):
        """确保 search_text 列存在"""
        cursor = self.conn.execute("PRAGMA table_info(facts)")
        columns = [row[1] for row in cursor.fetchall()]
        if "search_text" not in columns:
            self.conn.execute("ALTER TABLE facts ADD COLUMN search_text TEXT NOT NULL DEFAULT ''")

    def _migrate(self):
        """Schema 迁移"""
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current >= SCHEMA_VERSION:
            return

        # 新数据库（user_version=0）：schema 已由 _init_schema 创建，直接标记版本
        if current == 0:
            # 检查表是否已有 search_text 列（_init_schema 已创建）
            cursor = self.conn.execute("PRAGMA table_info(facts)")
            columns = [row[1] for row in cursor.fetchall()]
            if "search_text" in columns:
                self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                return

        # 旧数据库迁移
        self.conn.isolation_level = None
        try:
            self.conn.execute("BEGIN")
            v = current
            while v < SCHEMA_VERSION:
                if v == 1:
                    self._migrate_to_search_text()
                v += 1
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.execute("COMMIT")
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            self.conn.isolation_level = ""

        print(f"[FactStore] schema migrated: v{current} → v{SCHEMA_VERSION}")

    def _migrate_to_search_text(self):
        """v1 → v2：补充 CJK 友好的搜索文本"""
        self._ensure_search_text_column()

        rows = self.conn.execute("SELECT id, fact, tags FROM facts").fetchall()
        for row_id, fact, tags in rows:
            search_text = build_fact_search_text(fact, parse_tags(tags))
            self.conn.execute("UPDATE facts SET search_text = ? WHERE id = ?", (search_text, row_id))

        self.conn.executescript("""
            DROP TRIGGER IF EXISTS facts_ai;
            DROP TRIGGER IF EXISTS facts_ad;
            DROP TRIGGER IF EXISTS facts_au;
            DROP TABLE IF EXISTS facts_fts;
            CREATE VIRTUAL TABLE facts_fts USING fts5(
                fact,
                search_text,
                content=facts,
                content_rowid=id,
                tokenize='unicode61'
            );
        """)
        self._create_fts_triggers()
        self.conn.execute("INSERT INTO facts_fts(facts_fts) VALUES ('rebuild')")

    def add(self, fact: str, tags: list[str] = None, time: str = None,
            session_id: str = None) -> int:
        """新增一条元事实，返回 ID"""
        tags = tags or []
        now = datetime.now().isoformat()
        search_text = build_fact_search_text(fact, tags)
        cursor = self.conn.execute(
            """INSERT INTO facts (fact, search_text, tags, time, session_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fact, search_text, json.dumps(tags), time, session_id, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def add_batch(self, entries: list[dict]) -> int:
        """批量新增（事务），返回写入条数"""
        count = 0
        for entry in entries:
            self.add(
                fact=entry["fact"],
                tags=entry.get("tags", []),
                time=entry.get("time"),
                session_id=entry.get("session_id"),
            )
            count += 1
        return count

    def search_by_tags(self, query_tags: list[str], date_range: dict = None,
                       limit: int = 20) -> list[Fact]:
        """按标签搜索（精确匹配，OR 逻辑，按匹配数降序）"""
        if not query_tags:
            return []

        tag_count = len(query_tags)
        date_key = (1 if date_range and date_range.get("from") else 0) | \
                   (2 if date_range and date_range.get("to") else 0)
        cache_key = f"{tag_count}:{date_key}"

        if cache_key not in self._tag_search_cache:
            placeholders = ", ".join(f"@tag{i}" for i in range(tag_count))
            date_where = ""
            if date_key & 1:
                date_where += " AND f.time >= @date_from"
            if date_key & 2:
                date_where += " AND f.time <= @date_to"

            sql = f"""
                SELECT f.*, COUNT(DISTINCT je.value) as match_count
                FROM facts f, json_each(f.tags) je
                WHERE je.value IN ({placeholders}){date_where}
                GROUP BY f.id
                ORDER BY match_count DESC, f.time DESC
                LIMIT @limit
            """
            self._tag_search_cache[cache_key] = sql

        sql = self._tag_search_cache[cache_key]
        params = {"limit": limit}
        for i, tag in enumerate(query_tags):
            params[f"tag{i}"] = tag
        if date_range:
            if date_range.get("from"):
                params["date_from"] = date_range["from"]
            if date_range.get("to"):
                params["date_to"] = date_range["to"]

        cursor = self.conn.execute(sql, params)
        return [self._row_to_fact(row) for row in cursor.fetchall()]

    def search_full_text(self, query: str, limit: int = 20) -> list[Fact]:
        """全文搜索（FTS5）"""
        if not query or not query.strip():
            return []

        try:
            fts_query = build_fts_query(query)
            if not fts_query:
                return []

            cursor = self.conn.execute(
                """SELECT f.*, rank
                   FROM facts_fts fts
                   JOIN facts f ON f.id = fts.rowid
                   WHERE facts_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            )
            rows = cursor.fetchall()
            if not rows and has_cjk(query):
                return self._like_fallback(query, limit)
            return [self._row_to_fact(row) for row in rows]
        except sqlite3.OperationalError:
            return self._like_fallback(query, limit)

    def _like_fallback(self, query: str, limit: int) -> list[Fact]:
        """LIKE 降级搜索（FTS 失败时使用）"""
        cursor = self.conn.execute(
            "SELECT * FROM facts WHERE fact LIKE '%' || ? || '%' ORDER BY time DESC LIMIT ?",
            (query, limit),
        )
        return [self._row_to_fact(row) for row in cursor.fetchall()]

    def get_all(self) -> list[Fact]:
        """获取所有元事实（按时间降序）"""
        cursor = self.conn.execute("SELECT * FROM facts ORDER BY time DESC")
        return [self._row_to_fact(row) for row in cursor.fetchall()]

    def get_by_session(self, session_id: str) -> list[Fact]:
        """按 session_id 查询"""
        cursor = self.conn.execute(
            "SELECT * FROM facts WHERE session_id = ? ORDER BY time DESC",
            (session_id,),
        )
        return [self._row_to_fact(row) for row in cursor.fetchall()]

    def get_by_id(self, fact_id: int) -> Optional[Fact]:
        """按 ID 查询"""
        cursor = self.conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,))
        row = cursor.fetchone()
        return self._row_to_fact(row) if row else None

    def count(self) -> int:
        """获取事实总数"""
        cursor = self.conn.execute("SELECT COUNT(*) FROM facts")
        return cursor.fetchone()[0]

    def delete(self, fact_id: int) -> bool:
        """删除单条"""
        cursor = self.conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def clear_all(self):
        """清空所有"""
        self.conn.execute("DELETE FROM facts")
        self.conn.execute("INSERT INTO facts_fts(facts_fts) VALUES ('rebuild')")
        self.conn.commit()

    def export_all(self) -> list[dict]:
        """导出所有（不含内部字段），供 API 使用"""
        return [
            {"fact": f.fact, "tags": f.tags, "time": f.time, "session_id": f.session_id}
            for f in self.get_all()
        ]

    def import_all(self, entries: list[dict]):
        """批量导入"""
        self.conn.isolation_level = None
        try:
            self.conn.execute("BEGIN")
            for entry in entries:
                self.add(
                    fact=entry["fact"],
                    tags=entry.get("tags", []),
                    time=entry.get("time"),
                    session_id=entry.get("session_id"),
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self.conn.isolation_level = ""

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _row_to_fact(self, row) -> Fact:
        """数据库行 → Fact 对象"""
        # 获取列名
        if hasattr(row, "keys"):
            # sqlite3.Row
            return Fact(
                id=row["id"],
                fact=row["fact"],
                tags=parse_tags(row["tags"]),
                time=row["time"],
                session_id=row["session_id"],
                created_at=row["created_at"],
                match_count=row.get("match_count"),
            )
        else:
            # tuple (id, fact, search_text, tags, time, session_id, created_at, ...)
            return Fact(
                id=row[0],
                fact=row[1],
                tags=parse_tags(row[3]),
                time=row[4],
                session_id=row[5],
                created_at=row[6],
                match_count=row[7] if len(row) > 7 else None,
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
