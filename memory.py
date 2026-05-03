"""FOC-Assistant 持久记忆模块 —— 自动从对话中提取洞察

不依赖 LLM，用规则从工具调用历史中提取有价值的信息：
- 技术决策和参数配置
- 调试经验和解决方案
- 用户关注的主题
- 项目变更记录
"""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import MEMORY_DIR, USER_PROFILE_PATH, MEMORY_EXTRACT_THRESHOLD


# 记忆存储目录
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# 用户画像文件


class ConversationMemory:
    """对话记忆管理器"""

    def __init__(self):
        self._profile: Optional[dict] = None

    # ================================================================
    # 洞察提取
    # ================================================================

    def extract_insights(
        self,
        task: str,
        response: str,
        tool_calls: list[dict],
        tool_results: list[str],
        intent: str = "",
    ) -> list[dict]:
        """从一次对话中提取值得记忆的信息。

        Returns:
            list[dict]: [{"title": ..., "content": ..., "tags": ..., "type": ...}, ...]
        """
        insights = []

        # 1. 从工具调用中提取
        for tc, tr in zip(tool_calls, tool_results):
            name = tc.get("name", "")
            args = tc.get("args", {})
            result = tr or ""

            if name == "knowledge_add":
                insights.append({
                    "title": f"知识库新增: {args.get('title', '?')}",
                    "content": f"用户主动添加了知识笔记「{args.get('title', '')}」",
                    "tags": args.get("tags", ""),
                    "type": "knowledge_added",
                })

            elif name == "write_file":
                path = args.get("path", "?")
                content_len = len(args.get("content", ""))
                insights.append({
                    "title": f"文件创建: {Path(path).name}",
                    "content": f"创建了文件 {path} ({content_len} 字符)，任务: {task[:200]}",
                    "tags": f"文件操作,{Path(path).suffix}",
                    "type": "file_created",
                })

            elif name == "edit_file":
                path = args.get("path", "?")
                insights.append({
                    "title": f"文件修改: {Path(path).name}",
                    "content": f"修改了文件 {path}，任务: {task[:200]}",
                    "tags": f"文件操作,{Path(path).suffix}",
                    "type": "file_edited",
                })

            elif name == "calculate_pi_params":
                insights.append({
                    "title": "PI 参数计算记录",
                    "content": f"计算了 PI 参数: Rs={args.get('Rs')}, Ld={args.get('Ld')}, Lq={args.get('Lq')}",
                    "tags": "PI, 调参, 控制器",
                    "type": "calculation",
                })

            elif name == "compile_ccs":
                success = "error" not in result.lower() and "失败" not in result
                status = "成功" if success else "失败"
                insights.append({
                    "title": f"CCS 编译{status}: {Path(args.get('project_path', '?')).name}",
                    "content": f"CCS 编译{status}，项目: {args.get('project_path', '')}",
                    "tags": f"CCS, 编译, {status}",
                    "type": "build",
                })

            elif name == "web_search":
                query = args.get("query", "")
                insights.append({
                    "title": f"联网搜索: {query[:50]}",
                    "content": f"用户关注主题: {query}",
                    "tags": f"搜索,{query[:30]}",
                    "type": "search",
                })

        # 2. 从对话主题中提取
        topic_keywords = self._extract_topic_keywords(task)
        if topic_keywords:
            insights.append({
                "title": f"对话主题: {', '.join(topic_keywords[:5])}",
                "content": f"用户询问: {task[:300]}",
                "tags": ",".join(topic_keywords[:5]),
                "type": "topic",
            })

        # 3. 更新用户画像
        self._update_profile(task, tool_calls, intent)

        return insights

    def _extract_topic_keywords(self, text: str) -> list[str]:
        """从文本中提取 FOC 相关关键词"""
        foc_keywords = [
            "FOC", "PMSM", "SVPWM", "ESO", "SMC", "ADRC", "PI",
            "电流环", "速度环", "MTPA", "弱磁", "观测器",
            "DSP", "CCS", "TMS320", "Simulink", "MATLAB",
            "PWM", "ADC", "编码器", "Hall", "死区",
            "谐波", "纹波", "超调", "带宽", "极点",
            "双三相", "VSD", "解耦", "有限时间",
            "调参", "参数整定", "离散化", "定点运算",
        ]
        text_upper = text.upper()
        found = [kw for kw in foc_keywords if kw.upper() in text_upper]
        return found[:10]

    # ================================================================
    # 自动存储
    # ================================================================

    def auto_store(self, insights: list[dict], max_per_session: int = 3) -> list[str]:
        """自动将洞察存入知识库文件。

        Args:
            insights: extract_insights 返回的洞察列表
            max_per_session: 单次对话最多存储条数（防噪音）

        Returns:
            list[str]: 实际存储的文件路径
        """
        stored = []
        # 按优先级过滤：calculation > build > file > search > topic
        priority = {"calculation": 0, "build": 1, "file_created": 2, "file_edited": 2, "knowledge_added": 3, "search": 4, "topic": 5}
        sorted_insights = sorted(insights, key=lambda x: priority.get(x.get("type", ""), 99))

        for insight in sorted_insights[:max_per_session]:
            title = insight.get("title", "untitled")
            content = insight.get("content", "")
            tags = insight.get("tags", "")
            insight_type = insight.get("type", "")

            # 生成文件名
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", title[:40])
            filename = f"auto_{ts}_{safe_name}.md"

            filepath = MEMORY_DIR / filename
            try:
                filepath.write_text(
                    f"# {title}\n\n"
                    f"- **类型**: {insight_type}\n"
                    f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"- **标签**: {tags}\n\n"
                    f"{content}\n",
                    encoding="utf-8",
                )
                stored.append(str(filepath))
            except Exception:
                continue

        return stored

    # ================================================================
    # 用户画像
    # ================================================================

    def get_user_profile(self) -> dict:
        """获取用户画像"""
        if self._profile is None:
            self._profile = self._load_profile()
        return self._profile

    def _load_profile(self) -> dict:
        if USER_PROFILE_PATH.exists():
            try:
                return json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "topic_counts": {},
            "tool_counts": {},
            "intent_counts": {},
            "total_sessions": 0,
            "last_active": "",
            "created": datetime.now().isoformat(),
        }

    def _save_profile(self):
        try:
            USER_PROFILE_PATH.write_text(
                json.dumps(self._profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _update_profile(self, task: str, tool_calls: list[dict], intent: str):
        profile = self.get_user_profile()

        # 主题统计
        topics = self._extract_topic_keywords(task)
        topic_counts = profile.get("topic_counts", {})
        for t in topics:
            topic_counts[t] = topic_counts.get(t, 0) + 1
        profile["topic_counts"] = topic_counts

        # 工具使用统计
        tool_counts = profile.get("tool_counts", {})
        for tc in tool_calls:
            name = tc.get("name", "")
            if name:
                tool_counts[name] = tool_counts.get(name, 0) + 1
        profile["tool_counts"] = tool_counts

        # 意图统计
        if intent:
            intent_counts = profile.get("intent_counts", {})
            intent_counts[intent] = intent_counts.get(intent, 0) + 1
            profile["intent_counts"] = intent_counts

        profile["total_sessions"] = profile.get("total_sessions", 0) + 1
        profile["last_active"] = datetime.now().isoformat()

        self._profile = profile
        self._save_profile()

    # ================================================================
    # 搜索和统计
    # ================================================================

    def search_memory(self, query: str, max_results: int = 5) -> str:
        """搜索记忆文件"""
        if not MEMORY_DIR.exists():
            return "记忆库为空。"

        query_lower = query.lower()
        results = []

        for f in sorted(MEMORY_DIR.glob("*.md"), reverse=True):
            try:
                content = f.read_text(encoding="utf-8")
                if query_lower in content.lower():
                    preview = content[:300].replace("\n", " ")
                    results.append(f"- [{f.stem}] {preview}")
                    if len(results) >= max_results:
                        break
            except Exception:
                continue

        if not results:
            return f"记忆库中未找到与 '{query}' 相关的内容。"
        return f"记忆搜索结果 ({len(results)} 条):\n" + "\n".join(results)

    def get_stats(self) -> str:
        """获取记忆统计信息"""
        profile = self.get_user_profile()

        # 记忆文件统计
        memory_files = list(MEMORY_DIR.glob("*.md")) if MEMORY_DIR.exists() else []
        type_counts = Counter()
        for f in memory_files:
            # 从文件名提取类型
            parts = f.stem.split("_")
            if len(parts) >= 3:
                # auto_20240101_type_xxx
                pass
            try:
                content = f.read_text(encoding="utf-8")
                match = re.search(r"\*\*类型\*\*:\s*(\w+)", content)
                if match:
                    type_counts[match.group(1)] += 1
            except Exception:
                continue

        # 热门主题
        topic_counts = profile.get("topic_counts", {})
        top_topics = sorted(topic_counts.items(), key=lambda x: -x[1])[:10]

        # 常用工具
        tool_counts = profile.get("tool_counts", {})
        top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:10]

        return (
            f"记忆系统统计\n"
            f"{'='*40}\n"
            f"记忆条目: {len(memory_files)} 条\n"
            f"总对话次数: {profile.get('total_sessions', 0)}\n"
            f"最后活跃: {profile.get('last_active', '未知')}\n\n"
            f"记忆类型分布:\n"
            + "\n".join(f"  {t}: {c}" for t, c in type_counts.most_common(8))
            + f"\n\n热门关注主题:\n"
            + "\n".join(f"  {t}: {c}次" for t, c in top_topics)
            + f"\n\n常用工具:\n"
            + "\n".join(f"  {t}: {c}次" for t, c in top_tools)
        )


# 全局单例
_memory_instance: Optional[ConversationMemory] = None


def get_memory() -> ConversationMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = ConversationMemory()
    return _memory_instance
