"""FOC-Assistant 本地知识库 —— 文档索引与语义搜索

支持:
- Markdown/TXT 全文索引
- PDF 全文提取（PyPDF2）
- CSV 列名+统计索引
- 知识库专用目录: knowledge_base/{papers,data,notes}/
"""

import csv
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT, DESKTOP

# 知识库索引文件路径
KB_INDEX_PATH = Path(__file__).parent / "knowledge_index.json"

# 知识库专用目录 —— 你把文件丢这里就会自动索引
KB_DIR = Path(__file__).parent / "knowledge_base"
KB_PAPERS_DIR = KB_DIR / "papers"      # 放 PDF 论文
KB_DATA_DIR = KB_DIR / "data"          # 放 CSV 实验数据
KB_NOTES_DIR = KB_DIR / "notes"        # 放 MD/TXT 笔记
KB_CODES_DIR = KB_DIR / "codes"        # 放参考代码 (.c/.h/.m 等)

# 文档源（自动扫描）
DOC_SOURCES = [
    # (目录, 文件模式, 标签)
    (PROJECT_ROOT, "*.md", "项目文档"),
    (PROJECT_ROOT, "*.txt", "项目笔记"),
    (DESKTOP, "*.md", "桌面文档"),
    (DESKTOP, "*.txt", "桌面笔记"),
    (KB_NOTES_DIR, "*.md", "知识库笔记"),
    (KB_NOTES_DIR, "*.txt", "知识库笔记"),
    (KB_NOTES_DIR, "*.tex", "LaTeX 源码"),
    (KB_NOTES_DIR, "*.bib", "BibTeX 文献"),
]

# 代码源（只索引源文件，排除编译产物）
CODE_SOURCES = [
    (KB_CODES_DIR, "*.c", "参考代码"),
    (KB_CODES_DIR, "*.h", "参考头文件"),
    (KB_CODES_DIR, "*.cmd", "链接器脚本"),
    (KB_CODES_DIR, "*.m", "MATLAB 脚本"),
    (KB_CODES_DIR, "*.py", "Python 脚本"),
    (KB_CODES_DIR, "*.mk", "Makefile"),
]

PAPER_SOURCES = [
    (DESKTOP, "*.pdf", "桌面论文"),
    (KB_PAPERS_DIR, "*.pdf", "知识库论文"),
]

CSV_SOURCES = [
    (DESKTOP, "*.csv", "实验数据"),
    (KB_DATA_DIR, "*.csv", "实验数据"),
]


class KnowledgeBase:
    """本地知识库搜索引擎"""

    def __init__(self):
        self.documents: list[dict] = []
        self.inverted_index: dict[str, set] = defaultdict(set)
        self.loaded = False

    # ================================================================
    # 索引构建
    # ================================================================

    def build_index(self) -> str:
        """扫描所有文档源，构建倒排索引（支持增量：只索引 mtime 变化的文件）"""
        # 加载旧索引的 mtime 记录
        old_mtimes = self._load_old_mtimes()

        # 如果是全量重建（旧索引不存在），清空
        if not old_mtimes:
            self.documents = []
            self.inverted_index = defaultdict(set)

        # 记录本次扫描的文件 mtime
        current_mtimes: dict[str, float] = {}

        stats = {"md": 0, "txt": 0, "pdf": 0, "csv": 0, "pdf_text": 0, "code": 0}

        # 1. Markdown/TXT 文档 —— 全文索引
        for directory, pattern, tag in DOC_SOURCES:
            if not directory.exists():
                continue
            for filepath in directory.glob(pattern):
                try:
                    filepath_str = str(filepath)
                    file_mtime = filepath.stat().st_mtime
                    current_mtimes[filepath_str] = file_mtime

                    # 增量检查：文件未变化则跳过
                    if not self._should_reindex(filepath_str, old_mtimes):
                        continue

                    # 移除该文件的旧 chunks（如果有）
                    self._remove_file_chunks(filepath_str)

                    text = filepath.read_text(encoding="utf-8", errors="ignore")
                    chunks = self._chunk_text(text, 500)
                    for i, chunk in enumerate(chunks):
                        doc_id = len(self.documents)
                        self.documents.append({
                            "id": doc_id,
                            "path": filepath_str,
                            "name": filepath.name,
                            "tag": tag,
                            "chunk_index": i,
                            "content": chunk,
                        })
                        self._index_document(doc_id, chunk)
                    stats[pattern.lstrip("*.")] += 1
                except Exception:
                    continue

        # 2. PDF 论文 —— 提取正文
        for directory, pattern, tag in PAPER_SOURCES:
            if not directory.exists():
                continue
            for filepath in directory.glob(pattern):
                try:
                    filepath_str = str(filepath)
                    file_mtime = filepath.stat().st_mtime
                    current_mtimes[filepath_str] = file_mtime

                    if not self._should_reindex(filepath_str, old_mtimes):
                        stats["pdf"] += 1
                        continue

                    self._remove_file_chunks(filepath_str)

                    pdf_text = self._extract_pdf_text(filepath)
                    if pdf_text and len(pdf_text) > 50:
                        chunks = self._chunk_text(pdf_text, 500)
                        for i, chunk in enumerate(chunks):
                            doc_id = len(self.documents)
                            self.documents.append({
                                "id": doc_id,
                                "path": filepath_str,
                                "name": filepath.name,
                                "tag": f"{tag}(全文)",
                                "chunk_index": i,
                                "content": chunk,
                            })
                            self._index_document(doc_id, chunk)
                        stats["pdf_text"] += 1
                    else:
                        self._index_filename_only(filepath, tag)
                    stats["pdf"] += 1
                except Exception:
                    self._index_filename_only(filepath, tag)
                    stats["pdf"] += 1

        # 3. 参考代码 —— 全文索引
        for directory, pattern, tag in CODE_SOURCES:
            if not directory.exists():
                continue
            for filepath in directory.rglob(pattern):
                try:
                    filepath_str = str(filepath)
                    file_mtime = filepath.stat().st_mtime
                    current_mtimes[filepath_str] = file_mtime

                    if not self._should_reindex(filepath_str, old_mtimes):
                        continue

                    self._remove_file_chunks(filepath_str)

                    text = filepath.read_text(encoding="utf-8", errors="ignore")
                    chunks = self._chunk_text(text, 500)
                    for i, chunk in enumerate(chunks):
                        doc_id = len(self.documents)
                        self.documents.append({
                            "id": doc_id,
                            "path": filepath_str,
                            "name": filepath.name,
                            "tag": tag,
                            "chunk_index": i,
                            "content": chunk,
                        })
                        self._index_document(doc_id, chunk)
                    stats["code"] += 1
                except Exception:
                    continue

        # 4. CSV 数据 —— 索引列名+统计摘要
        for directory, pattern, tag in CSV_SOURCES:
            if not directory.exists():
                continue
            for filepath in directory.glob(pattern):
                try:
                    filepath_str = str(filepath)
                    file_mtime = filepath.stat().st_mtime
                    current_mtimes[filepath_str] = file_mtime

                    if not self._should_reindex(filepath_str, old_mtimes):
                        continue

                    self._remove_file_chunks(filepath_str)

                    summary = self._summarize_csv(filepath)
                    doc_id = len(self.documents)
                    self.documents.append({
                        "id": doc_id,
                        "path": filepath_str,
                        "name": filepath.name,
                        "tag": tag,
                        "chunk_index": 0,
                        "content": summary,
                    })
                    self._index_document(doc_id, summary)
                    stats["csv"] += 1
                except Exception:
                    continue

        self._save_index()
        self.loaded = True

        parts = []
        if stats["code"]: parts.append(f"{stats['code']} code")
        if stats["md"]: parts.append(f"{stats['md']} md")
        if stats["txt"]: parts.append(f"{stats['txt']} txt")
        if stats["pdf_text"]: parts.append(f"{stats['pdf_text']} pdf(full-text)")
        if stats["pdf"] - stats["pdf_text"] > 0:
            parts.append(f"{stats['pdf'] - stats['pdf_text']} pdf(filename)")
        if stats["csv"]: parts.append(f"{stats['csv']} csv")

        return (
            f"Indexed: {', '.join(parts)} -> {len(self.documents)} chunks, "
            f"{len(self.inverted_index)} terms"
        )

    def rebuild(self) -> str:
        """强制重建索引（清空后重新扫描）"""
        if KB_INDEX_PATH.exists():
            KB_INDEX_PATH.unlink()
        self.loaded = False
        return self.build_index()

    def _index_filename_only(self, filepath: Path, tag: str):
        """仅用文件名建立索引（PDF 无文本时回退）"""
        display_name = filepath.stem
        keywords = re.sub(r'[_-]', ' ', display_name)
        doc_id = len(self.documents)
        self.documents.append({
            "id": doc_id,
            "path": str(filepath),
            "name": filepath.name,
            "tag": f"{tag}(文件名)",
            "chunk_index": 0,
            "content": f"[论文] {display_name}\n关键词: {keywords}",
        })
        self._index_document(doc_id, keywords)

    def _extract_pdf_text(self, filepath: Path) -> str:
        """从 PDF 提取文本内容"""
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(filepath))
            texts = []
            for page in reader.pages[:10]:  # 只取前 10 页（节省内存）
                t = page.extract_text()
                if t:
                    texts.append(t)
            return "\n\n".join(texts)
        except Exception:
            return ""

    def _summarize_csv(self, filepath: Path) -> str:
        """生成 CSV 摘要: 列名 + 行数 + 数值列统计"""
        try:
            raw = filepath.read_bytes()
            # 检测编码
            import chardet
            encoding = chardet.detect(raw)["encoding"] or "utf-8"
            text = raw.decode(encoding, errors="replace")

            reader = csv.DictReader(text.splitlines())
            if not reader.fieldnames:
                return f"[CSV] {filepath.name}\n表头无法解析"

            rows = list(reader)
            numeric_cols = []
            for col in reader.fieldnames:
                try:
                    float(rows[0][col]) if rows else None
                    numeric_cols.append(col)
                except (ValueError, KeyError, IndexError):
                    continue

            summary = [
                f"[CSV] {filepath.name}",
                f"行数: {len(rows)}",
                f"列名: {', '.join(reader.fieldnames)}",
            ]
            if numeric_cols:
                summary.append(f"数值列: {', '.join(numeric_cols)}")
            return "\n".join(summary)
        except Exception:
            return f"[CSV] {filepath.name}"

    def _chunk_text(self, text: str, chunk_size: int = 500) -> list[str]:
        """将长文本切分为有重叠的 chunk"""
        paragraphs = text.split("\n\n")
        chunks = []
        current = ""
        for p in paragraphs:
            if len(current) + len(p) < chunk_size:
                current += p + "\n\n"
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = p + "\n\n"
        if current.strip():
            chunks.append(current.strip())
        return chunks if chunks else [text]

    def _tokenize(self, text: str) -> list[str]:
        """中文+英文混合分词"""
        tokens = []
        tokens.extend(re.findall(r'[a-zA-Z_]\w+', text.lower()))
        chinese_chars = re.findall(r'[一-鿿]+', text)
        for phrase in chinese_chars:
            for i in range(len(phrase)):
                for size in [2, 3, 4]:
                    if i + size <= len(phrase):
                        tokens.append(phrase[i:i + size])
        tokens.extend(re.findall(r'\d+\.?\d*', text))
        return tokens

    def _index_document(self, doc_id: int, text: str):
        """将文档加入倒排索引"""
        tokens = set(self._tokenize(text))
        for token in tokens:
            if len(token) >= 2:
                self.inverted_index[token].add(doc_id)

    def _remove_file_chunks(self, filepath: str):
        """移除指定文件的所有旧 chunks 和倒排索引条目"""
        ids_to_remove = {doc["id"] for doc in self.documents if doc["path"] == filepath}
        if not ids_to_remove:
            return

        # 从 documents 列表中移除
        self.documents = [doc for doc in self.documents if doc["path"] != filepath]

        # 从倒排索引中移除
        for token, doc_ids in list(self.inverted_index.items()):
            doc_ids -= ids_to_remove
            if not doc_ids:
                del self.inverted_index[token]

    def _load_old_mtimes(self) -> dict[str, float]:
        """从旧索引文件中加载文件 mtime 记录"""
        if not KB_INDEX_PATH.exists():
            return {}
        try:
            data = json.loads(KB_INDEX_PATH.read_text(encoding="utf-8"))
            return data.get("file_mtimes", {})
        except Exception:
            return {}

    def _should_reindex(self, filepath: str, old_mtimes: dict[str, float]) -> bool:
        """判断文件是否需要重新索引"""
        if not old_mtimes:
            return True  # 旧索引无 mtime 记录，需要全量重建
        if filepath not in old_mtimes:
            return True  # 新文件
        try:
            current_mtime = Path(filepath).stat().st_mtime
            return abs(old_mtimes[filepath] - current_mtime) >= 0.01
        except Exception:
            return True

    # ================================================================
    # 搜索
    # ================================================================

    def search(self, query: str, top_k: int = 8) -> str:
        """搜索知识库"""
        if not self.loaded:
            self._load_index()
        if not self.documents:
            self.build_index()

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return "Search query too short."

        scores: dict[int, float] = defaultdict(float)
        doc_count = len(self.documents)

        for token in query_tokens:
            matching_docs = self.inverted_index.get(token, set())
            if not matching_docs:
                continue
            idf = math.log((doc_count + 1) / (len(matching_docs) + 1)) + 1
            for doc_id in matching_docs:
                doc = self.documents[doc_id]
                tf = doc.get("content", "").lower().count(token) / max(len(doc["content"]), 1) * 100
                scores[doc_id] += tf * idf

        if not scores:
            return (
                f"No results in knowledge base for: '{query}'\n"
                f"Tip: Try different keywords, or drop files into:\n"
                f"  {KB_PAPERS_DIR}  (PDF papers)\n"
                f"  {KB_DATA_DIR}    (CSV data)\n"
                f"  {KB_NOTES_DIR}   (MD/TXT notes)"
            )

        ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]

        output = [f"[Knowledge Base] {len(ranked)} results for: '{query}'\n"]
        for rank, (doc_id, score) in enumerate(ranked, 1):
            doc = self.documents[doc_id]
            content = doc["content"][:350]
            output.append(
                f"--- Result {rank} (score: {score:.1f}) ---\n"
                f"  Source: {doc['name']} [{doc['tag']}]\n"
                f"  Path: {doc['path']}\n"
                f"  Content:\n    {content.strip()}\n"
            )

        return "\n".join(output)

    # ================================================================
    # 导入 / 添加
    # ================================================================

    def import_file(self, filepath: str, tags: str = "") -> str:
        """导入单个文件到知识库（复制到 KB 目录并索引）"""
        src = Path(filepath)
        if not src.exists():
            return f"Error: file not found: {filepath}"

        suffix = src.suffix.lower()

        if suffix == ".pdf":
            dest_dir = KB_PAPERS_DIR
        elif suffix == ".csv":
            dest_dir = KB_DATA_DIR
        elif suffix in (".c", ".h", ".cmd", ".m", ".py", ".mk", ".asm"):
            dest_dir = KB_CODES_DIR
        elif suffix in (".md", ".txt", ".tex", ".bib", ".cls", ".sty"):
            dest_dir = KB_NOTES_DIR
        else:
            return f"Unsupported file type: {suffix}. Supported: .pdf .csv .c .h .cmd .m .md .txt .tex .bib"

        # 复制到 KB 目录
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        if dest.exists():
            return f"File already in knowledge base: {dest}"

        dest.write_bytes(src.read_bytes())

        # 重新索引
        return self.rebuild() + f"\nImported: {src.name} -> {dest}"

    def import_batch(self, directory: str, tag: str = "") -> str:
        """批量导入目录中的所有支持文件"""
        src_dir = Path(directory)
        if not src_dir.exists():
            return f"Error: directory not found: {directory}"

        imported = []
        failed = []
        for filepath in sorted(src_dir.iterdir()):
            if filepath.is_file():
                try:
                    result = self.import_file(str(filepath), tag)
                    imported.append(filepath.name)
                except Exception as e:
                    failed.append(f"{filepath.name}: {e}")

        return (
            f"Batch import from: {directory}\n"
            f"Imported: {len(imported)} files\n" +
            ("\n".join(f"  + {n}" for n in imported) if imported else "") +
            ("\nFailed: " + ", ".join(failed) if failed else "")
        )

    def add_note(self, title: str, content: str, tags: str = "") -> str:
        """向知识库添加一条笔记"""
        if not self.loaded:
            self._load_index()

        doc_id = len(self.documents)
        full_content = f"[Note] {title}\nTags: {tags}\n\n{content}"
        self.documents.append({
            "id": doc_id,
            "path": "(user note)",
            "name": title,
            "tag": f"笔记 {tags}",
            "chunk_index": 0,
            "content": full_content,
        })
        self._index_document(doc_id, full_content)
        self._save_index()

        # 同时存为文件
        KB_NOTES_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", title)
        note_path = KB_NOTES_DIR / f"{safe_name}.md"
        note_path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")

        return f"Note saved: '{title}' ({len(content)} chars) -> {note_path}"

    def list_documents(self) -> str:
        """列出知识库中的所有文档"""
        if not self.loaded:
            self._load_index()
        if not self.documents:
            return "Knowledge base is empty.\n\nDrop files into:\n  " + str(KB_PAPERS_DIR) + "\n  " + str(KB_DATA_DIR) + "\n  " + str(KB_NOTES_DIR)

        by_tag: dict[str, list[str]] = defaultdict(list)
        seen = set()
        for doc in self.documents:
            key = doc["path"]
            if key not in seen:
                seen.add(key)
                by_tag[doc["tag"]].append(doc["name"])

        output = [f"[Knowledge Base] {len(seen)} files, {len(self.documents)} chunks\n"]
        for tag, names in sorted(by_tag.items()):
            output.append(f"\n{tag} ({len(names)}):")
            for name in sorted(names):
                output.append(f"  - {name}")
        return "\n".join(output)

    # ================================================================
    # 持久化
    # ================================================================

    def _save_index(self):
        # 计算当前所有文件的 mtime
        file_mtimes: dict[str, float] = {}
        seen_paths = set()
        for doc in self.documents:
            path = doc["path"]
            if path not in seen_paths and path != "(user note)":
                seen_paths.add(path)
                try:
                    file_mtimes[path] = Path(path).stat().st_mtime
                except Exception:
                    pass

        data = {
            "documents": self.documents,
            "inverted_index": {k: list(v) for k, v in self.inverted_index.items()},
            "file_mtimes": file_mtimes,
        }
        KB_INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_index(self):
        if KB_INDEX_PATH.exists():
            try:
                data = json.loads(KB_INDEX_PATH.read_text(encoding="utf-8"))
                self.documents = data["documents"]
                self.inverted_index = defaultdict(set, {k: set(v) for k, v in data["inverted_index"].items()})
                self.loaded = True
                return
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"  [KB] 索引文件损坏，将重建: {e}")
            except Exception as e:
                print(f"  [KB] 索引加载异常，将重建: {e}")
        self.build_index()


# 全局单例
_kb_instance: Optional[KnowledgeBase] = None


def get_kb() -> KnowledgeBase:
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase()
        _kb_instance._load_index()
    return _kb_instance
