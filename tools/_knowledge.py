"""知识库工具：knowledge_search, knowledge_add, knowledge_list, knowledge_import, knowledge_rebuild"""

from knowledge import get_kb

def _knowledge_search(args: dict) -> str:
    """搜索本地知识库"""
    query = args["query"]
    top_k = int(args.get("top_k", 8))
    kb = get_kb()
    return kb.search(query, top_k)


def _knowledge_add(args: dict) -> str:
    """向知识库添加笔记"""
    title = args["title"]
    content = args["content"]
    tags = args.get("tags", "")
    kb = get_kb()
    return kb.add_note(title, content, tags)


def _knowledge_list(args: dict) -> str:
    """列出知识库文档"""
    kb = get_kb()
    return kb.list_documents()


def _knowledge_import(args: dict) -> str:
    """导入文件到知识库"""
    filepath = args["filepath"]
    tags = args.get("tags", "")
    kb = get_kb()
    return kb.import_file(filepath, tags)


def _knowledge_rebuild(args: dict) -> str:
    """重建知识库索引"""
    kb = get_kb()
    return kb.rebuild()
