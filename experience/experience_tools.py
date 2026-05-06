"""experience_tools.py — 经验库工具

从 OpenHanako 的 lib/tools/experience.js 移植。
提供 recall_experience / record_experience 两个工具接口，
可直接集成到 foc-assistant 的工具系统中。
"""

import json
from typing import Any

from experience.experience_store import ExperienceStore


# ── 工具定义（OpenAI function calling 格式） ──

RECALL_EXPERIENCE_TOOL = {
    "type": "function",
    "function": {
        "name": "recall_experience",
        "description": (
            "回忆经验库中的相关经验。"
            "不传 category 时返回经验库索引（所有分类列表）；"
            "传入 category 时返回该分类下的所有经验条目；"
            "传入 query 时进行全文搜索。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "经验分类名称，如 'PI调参'、'SVPWM调试'。不传则返回索引。",
                },
                "query": {
                    "type": "string",
                    "description": "搜索关键词，用于全文检索经验内容。",
                },
            },
            "required": [],
        },
    },
}

RECORD_EXPERIENCE_TOOL = {
    "type": "function",
    "function": {
        "name": "record_experience",
        "description": (
            "记录一条经验到经验库。"
            "用于沉淀调试教训、参数整定技巧、故障排除方法等可复用知识。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "经验分类，如 'PI调参'、'SVPWM调试'、'EMC整改'。",
                },
                "content": {
                    "type": "string",
                    "description": "经验内容，要求具体、可操作。如：'电流环 Ki 过大会导致低频振荡，建议先用小 Ki 启动再逐步增大'。",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "标签列表，如 ['电流环', 'PI', '振荡']。",
                },
                "source": {
                    "type": "string",
                    "description": "经验来源，如 'TI E2E 论坛'、'实测'、'论文: xxx'。",
                },
            },
            "required": ["category", "content"],
        },
    },
}


# ── 工具执行器 ──

class ExperienceToolExecutor:
    """经验库工具执行器"""

    def __init__(self, store: ExperienceStore):
        self.store = store

    def execute(self, tool_name: str, arguments: dict) -> str:
        """执行经验库工具"""
        if tool_name == "recall_experience":
            return self._recall(arguments)
        elif tool_name == "record_experience":
            return self._record(arguments)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)

    def _recall(self, args: dict) -> str:
        """recall_experience 执行"""
        category = args.get("category", "").strip()
        query = args.get("query", "").strip()

        # 全文搜索
        if query:
            entries = self.store.search(query, limit=10)
            if not entries:
                return json.dumps({"result": f"未找到与 '{query}' 相关的经验。"}, ensure_ascii=False)
            results = []
            for e in entries:
                results.append({
                    "category": e.category,
                    "content": e.content,
                    "tags": e.tags,
                    "source": e.source,
                })
            return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False, indent=2)

        # 按分类查询
        if category:
            entries = self.store.get_by_category(category)
            if not entries:
                return json.dumps({"result": f"分类 '{category}' 下没有经验条目。"}, ensure_ascii=False)
            results = []
            for e in entries:
                results.append({
                    "content": e.content,
                    "tags": e.tags,
                    "source": e.source,
                })
            return json.dumps({
                "category": category,
                "entries": results,
                "count": len(results),
            }, ensure_ascii=False, indent=2)

        # 返回索引
        index_text = self.store.get_index_text()
        return json.dumps({"index": index_text}, ensure_ascii=False, indent=2)

    def _record(self, args: dict) -> str:
        """record_experience 执行"""
        category = args.get("category", "").strip().lstrip("#").strip()
        content = args.get("content", "").strip()
        tags = args.get("tags", [])
        source = args.get("source", "")

        if not category or not content:
            return json.dumps({"error": "category 和 content 不能为空"}, ensure_ascii=False)

        added, reason = self.store.add(
            category=category,
            content=content,
            tags=tags,
            source=source,
        )

        if not added:
            if reason == "duplicate":
                return json.dumps({"result": "该经验已存在，跳过重复记录。"}, ensure_ascii=False)
            return json.dumps({"error": f"记录失败: {reason}"}, ensure_ascii=False)

        return json.dumps({
            "result": f"已记录到分类 [{category}]",
            "content": content,
        }, ensure_ascii=False, indent=2)


# ── 经验库 system prompt 注入 ──

def get_experience_prompt_section(store: ExperienceStore) -> str:
    """生成经验库引导文本，注入到 system prompt"""
    count = store.count()
    if count == 0:
        return ""

    categories = store.get_categories()
    cat_names = ", ".join(c.name for c in categories[:5])

    return f"""
## 经验库

你拥有一个经验库（{count} 条经验，{len(categories)} 个分类：{cat_names}）。

**何时使用经验库：**
- 调试遇到问题时，先 recall_experience 搜索相关经验
- 解决了一个难题后，用 record_experience 沉淀教训
- 参数整定、故障排除、EMC 整改等可复用知识都应该记录

**记录要求：**
- 内容具体、可操作，不要写空泛的结论
- 带上标签便于检索
- 注明来源（实测/论文/论坛）
"""
