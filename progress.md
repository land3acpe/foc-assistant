# 改造进度日志

## 2026-05-07：项目改造启动

**会话目标**：基于代码审计产出 3 周求职改造任务清单

**完成事项**：
- 完成 foc-assistant 项目代码审计（55 文件 / 11654 行）
- 重新评估项目定位：从"个人工具"改为"求职 AI Agent 作品集"
- 产出 `task_plan.md`：18 个 Issue，按 Phase 1（止血）/ Phase 2（立人设）/ Phase 3（包装）三阶段
- 产出 `findings.md`：审计精华，含具体文件路径和行号
- 产出 `progress.md`：本会话日志

**关键决策**：
1. **不重写**，在现仓库改造（保留 git 历史和测试 12 文件 1115 行的资产）
2. **保留 LangGraph + Multi-Agent + RAG + Memory 关键词矩阵**——求职市场需要这些 buzzwords
3. **砍掉同质化部分**：5 套记忆 → 1 套，5 个子 Agent → 2 个，双主循环 → 单循环
4. **加领域差异化**：用 python-control / scipy 做真实 Bode 分析和参数辨识工具，作为护城河
5. **必上云**：阿里云 2c4g ECS 部署，公网体验链接是求职项目的"加分项"

**未完成 / 下一步**：
- [ ] 用户 review `task_plan.md`，调整优先级
- [ ] 决定是否把这 18 个 Issue 同步到 GitHub Issues（建议同步，方便面试时讲"项目管理"）
- [ ] 启动 Phase 1 Issue #1（合并记忆系统）

**风险**：
- python-control 在 Windows 安装可能踩坑（Phase 2 验证）
- 删 agent.py:agent_loop 可能破坏现有行为（Issue #2 需端到端测试保护）

---

## 待开工事项快速索引

- Phase 1（Week 1）：Issues #1–#8，删冗余 + 修 README
- Phase 2（Week 2）：Issues #9–#13，加真实 PMSM 工具 + 测试 + CI + 架构图
- Phase 3（Week 3）：Issues #14–#18，Docker + 部署 + Demo + 讲稿

---

## 2026-05-07：Issue #1 完成

**完成事项**：
- 三层记忆架构落地：ChatMemory（短期）+ KnowledgeBase（RAG）+ ExperienceStore（经验）
- 新增 `memory.py`（约 140 行）+ `tests/test_memory.py`（10 用例全过）
- 删除 `conversation_memory.py` / `memory/`（6 文件）/ `api/memory_api.py` / 2 个旧测试
- 迁移 `agent.py` / `graph_agent.py` / `qq_bot.py` / `tools/_agent_tools.py` / `tools/_registry.py`
- 净减码 **-2026 行**（验收要求 ≥ -2000）

**计划偏离**：
- 任务 1 因 `memory/` 目录遮蔽新建的 `memory.py`，提前删除了 `memory/` 和两个测试文件
- 任务 11 因此并入任务 1，无额外工作
- 任务 10 新增 qq_bot.py 修复（误 commit 历史修改后回滚重新精确暂存）

**未完成（属 Issue #2）**：
- ChatMemory 在 LangGraph chat/qa 节点的实际集成

**commit 历史**（9 个）：
- 62eef49 fix(qq_bot): 移除对已删除的 get_memory 的调用
- 4492ac6 refactor(tools): 删除 memory_search 与 memory_stats 两个工具
- 7171c5f refactor(memory): 删除 api/memory_api（facade 层）
- 9d0e2c9 refactor(agent): experience prompt 直接调用 experience 模块
- 5524a63 refactor(graph_agent): 移除洞察提取节点
- 6aacc2e feat(memory): 工厂 get_chat_memory 实例缓存
- c2fe1ea feat(memory): 实现 clear 与 get_context 格式校验
- 139582e feat(memory): 触发式 LLM 摘要 + 失败降级 FIFO
- 29c9ffe feat(memory): JSON 持久化 + session 隔离 + 特殊字符净化
- e721de2 feat(memory): 实现 ChatMemory 基础 add_turn / get_context
- 8367260 feat(memory): 任务 1 — 基线 + ChatMemory 骨架

**下一步**：Issue #2（统一双主循环为 LangGraph）
