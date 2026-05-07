# foc-assistant 求职改造 3 周任务清单

> 每条 Issue 可直接复制到 GitHub。所有文件路径相对于项目根目录。

## 项目目标
把当前 11000+ 行的过度工程化项目，改造为 1500-3000 行、架构清晰、有 demo、有测试、有 CI、有 PMSM 领域差异化的 **AI Agent 求职作品集项目**。

## 北极星指标（3 周后达成）
- [ ] 代码量 1500-3000 行
- [ ] 测试覆盖率 ≥ 60%
- [ ] GitHub Actions CI 绿勾
- [ ] Dockerfile 能一键跑
- [ ] 阿里云 ECS 上有公网体验链接
- [ ] README 第一屏有架构图 + 30 秒 demo GIF
- [ ] 中英双语 README
- [ ] 至少 1 个真实可用的 PMSM 工具（用 python-control / scipy 实现，非字符串模板）
- [ ] 自己 5 分钟能讲清架构和取舍

---

## Phase 1：止血（Week 1）—— 删除冗余、修复假象

### Issue #1：合并 5 套并存的记忆系统为 1 套
**当前问题**：knowledge.py + memory/ + experience/ + conversation_memory.py + api/memory_api.py 五套并行
**操作**：
- 删除 `memory/` 整个目录（5 文件，约 1573 行）
- 删除 `experience/experience_store.py` 和 `experience/experience_tools.py`（约 574 行）
- 删除 `conversation_memory.py`（331 行）
- 删除 `api/memory_api.py`（150 行）
- 保留 `knowledge.py`（591 行）作为长期记忆
- 新建 `memory.py`（< 200 行）：`ChatMemory` 类，deque 存最近 N 轮 + 自动 summary
**验收**：
- `grep -r "from memory\|from experience\|conversation_memory\|memory_api"` 全部为空
- 减少代码 ≥ 2300 行
- 新增 `tests/test_memory.py` 通过

---

### Issue #2：统一双主循环，只保留 LangGraph 一条
**当前问题**：CLI 走 `agent.py:agent_loop`，QQ 走 `graph_agent.py:FOCGraphAgent`，两套并存
**操作**：
- 删除 `agent.py:59-389` 的 `agent_loop` 函数（330 行）
- `agent.py:434` 的 `main` 入口改为调用 `FOCGraphAgent.run`
- `qq_bot.py:36` 保持调 `FOCGraphAgent`（已经在用）
- 检查 `graph_agent.py` 的工作流是否合理，必要时简化为：route → tool_use → memorize → final
**验收**：
- 项目内只有一个 agent 主循环
- CLI 和 QQ 都走 LangGraph
- `tests/test_graph_agent.py` 端到端能通

---

### Issue #3：5 个子 Agent 精简到 2 个
**当前问题**：5 个子 Agent prompt 差别极小，加上拼音模糊路由（玄学）
**操作**：
- `agents/profiles.py` 只保留 `research_agent`（搜索/查文档）+ `execution_agent`（跑工具/写代码）
- 删除 `code_analyzer / waveform_analyzer / controller_designer / debug_helper`
- 删除 `agents/__init__.py:27-86` 的 `_fuzzy_score` 和 `_pinyin_fuzzy_score`
- 路由改为：让 LLM 直接根据 task_type 字段返回 `"research" | "execution"`
**验收**：
- `agents/` 总代码量 < 200 行
- 路由测试通过率 ≥ 90%

---

### Issue #4：删除虚假的 PMSM 工具（量纲都错）
**当前问题**：`tools/_analysis.py` 几个工具是字符串模板伪装成功能，电机方向面试官一眼识破
**操作**：
- 删除 `tools/_analysis.py:186` `_calculate_pi_params`（额定电流硬编码 10A，对称最优公式量纲错）
- 删除 `tools/_analysis.py:241` `_generate_svpwm_table`（纯字符串模板）
- 删除 `tools/_analysis.py:352` `_suggest_controller_params`（硬编码字典）
- 同步从 `tools/_registry.py` 的 schema 列表和 dispatch 表移除
- 在 README 暂时不提这些能力，留待 Issue #8/#9 重做
**验收**：
- 不再有"假装计算实则字符串模板"的工具
- `tools/_registry.py` 工具数量与 README 数字一致

---

### Issue #5：重写 README，让 clone 能跑
**当前问题**：README 提到 `tools.py / semantic_router.py / memory.py` 这三个**不存在的文件**；工具数量自相矛盾（37 vs 33 vs 36）
**操作**：
- 重写 `README.md`
- 删除所有引用不存在文件的段落
- 工具数量与代码同步
- 新增 "Quick Start" 章节：`pip install -r requirements.txt && python run_cli.py`
- 新增 ".env.example" 文件作为配置模板
**验收**：
- 在干净的虚拟环境 clone 后，按 README 步骤能跑通
- 找一位同学验证（不指导前提下能否跑起来）

---

### Issue #6：启动脚本瘦身
**当前问题**：14 个 .bat / .ps1 脚本互相重复
**操作**：
- 删除全部 `run*.bat` `run*.ps1` `start_bg.*` `stop_qq.*` `restart_qq.*` `install_service.*`
- 保留唯一入口 `run_cli.py`（< 50 行）
- 保留 `run_qq.py`（< 50 行）
- 后续用 Docker 替代守护
**验收**：
- 仓库根目录脚本数 ≤ 3
- README 启动方式 = 一行命令

---

### Issue #7：修复 config.py 模块加载期 bug
**当前问题**：`config.py:248-253` 在模块加载时把 `API_KEY/BASE_URL/MODEL` 求值成全局常量，`switch_model` 之后这些常量不刷新
**操作**：
- 删除 `config.py:248-253` 的全局常量
- 改为通过 `ModelManager.current()` 方法访问
- 修复所有引用这些常量的代码
- 新增 `tests/test_model_switch.py` 验证切换后生效
**验收**：
- 单元测试覆盖 switch_model 场景
- `grep API_KEY config.py` 只在类内部出现

---

### Issue #8：清理 87 处裸 except
**当前问题**：87 处 `except Exception` / `except:`，其中 20+ 处直接 pass
**操作**：
- 全项目 grep 裸 except，改为具体异常类型 + 日志记录
- 优先处理：`knowledge.py`（8 处）、`qq_bot.py`、`agent.py`
- 引入 `logging` 替代 `print`
- 顶层兜底 except 保留但必须记录 traceback
**验收**：
- 裸 except 数量 < 10
- 所有 except 都有 logger.error 或具体处理

---

## Phase 2：立人设（Week 2）—— 加上真本事

### Issue #9：用 python-control 实现真实的 Bode 分析工具
**目标**：让面试官看到"这人是真懂控制系统"
**操作**：
- 新增依赖：`control>=0.10.0` `matplotlib`
- 新增 `tools/_pmsm_analysis.py`
- 实现 `bode_analysis(plant_tf, controller_tf)`：
  - 用 `control.bode_plot` 计算
  - 返回幅值/相位裕度、穿越频率
  - 输出 PNG 到 `outputs/bode_<timestamp>.png`
- 注册到 `tools/_registry.py`
**验收**：
- 给定 PMSM 速度环典型 PI 参数，输出与 MATLAB 一致（误差 < 1%）
- `tests/test_pmsm_analysis.py::test_bode` 通过

---

### Issue #10：用 scipy 实现最小二乘参数辨识
**目标**：再加一个真实可用的领域工具
**操作**：
- `tools/_pmsm_analysis.py` 新增 `identify_pmsm_params(csv_path)`
- 输入：包含 uvw 电压、idq 电流、转速的 CSV
- 实现：基于稳态/暂态数据的 Rs/Ls/磁链最小二乘辨识
- 引用算法来源（论文或教材，写在 docstring）
**验收**：
- 给定仿真数据，辨识误差 ≤ 5%
- 在 README 单独列出这个工具的能力示例

---

### Issue #11：测试覆盖率提到 ≥ 60%
**当前**：tests/ 12 文件 1115 行，但根目录还有割裂的 test_features.py
**操作**：
- 合并 `test_features.py`（331 行）进 `tests/`
- 新增 `tests/test_memory.py`（Issue #1 的产物）
- 新增 `tests/test_graph_agent.py`（端到端 mock LLM）
- 新增 `tests/test_pmsm_analysis.py`（Issue #9/#10 的产物）
- `pyproject.toml` 添加 `[tool.coverage]` 配置
**验收**：
- `pytest --cov=. --cov-report=term-missing` ≥ 60%
- README 加 coverage badge

---

### Issue #12：加 GitHub Actions CI
**操作**：
- 新增 `.github/workflows/ci.yml`：
  - matrix: Python 3.11 / 3.12
  - 跑 `pytest --cov`
  - 跑 `ruff check`
  - 上传 coverage 到 codecov（可选）
- 新增 `pyproject.toml` 中 `[tool.ruff]` 配置
- 跑一次本地 `ruff check` 修掉所有报错
**验收**：
- 任意 PR 触发 CI 并通过
- README 顶部有 CI badge

---

### Issue #13：架构图 + 中英双语 README
**目标**：面试官打开 GitHub 30 秒内能 get 到核心
**操作**：
- 新增 `docs/architecture.md`：mermaid graph 显示 LangGraph 节点 + 工具 + 记忆
- 重写 `README.md`（中文）：
  - 第一屏：标题 + 一句话价值 + 架构图 + demo GIF（GIF 待 Issue #16）
  - 章节：核心能力 / 技术栈 / Quick Start / 部署 / 路线图
- 新增 `README.en.md`（英文版，结构对齐）
**验收**：
- 找一位非电机背景的同学看 README，30 秒内能说出"这是干嘛的"
- 找一位电机背景的同学看，能认可工具是真的

---

## Phase 3：包装（Week 3）—— 上云、出片、能讲

### Issue #14：Dockerfile + docker-compose
**操作**：
- 新增 `Dockerfile`（基于 `python:3.12-slim`）
- 新增 `docker-compose.yml`：app + 可选 redis
- `.env.example` 完善
- README 添加 "Run with Docker" 章节
**验收**：
- `docker-compose up` 能启动 CLI/Web 服务
- 容器内存占用 < 1GB（云服务器只有 4G）

---

### Issue #15：部署到阿里云 ECS
**操作**：
- 在 2c4g Ubuntu 22.04 上拉镜像
- 配置 nginx 反代 443 → 容器端口
- 用 systemd 或 docker restart 策略守护
- 申请免费域名或用 IP（建议申请 Let's Encrypt 证书）
- 给一个 Web UI 或 OpenAPI 文档体验链接
- README 顶部加 "Live Demo: https://..."
**验收**：
- 公网可访问，5 分钟内能体验主要功能
- 同时在线 2 用户不崩

---

### Issue #16：录制 30 秒 demo GIF
**操作**：
- 用 ScreenToGif（Windows）或 asciinema 录制
- 场景脚本：
  1. 用户问："分析下 outputs/eso_test.csv 的纹波"
  2. Agent 调 csv_analyze 工具
  3. Agent 调 bode_analysis 工具（Issue #9 的产物）
  4. Agent 输出中文分析报告
- 保存为 `docs/demo.gif`，体积 < 5MB
- README 第一屏嵌入
**验收**：
- GitHub README 渲染流畅
- GIF 在 < 5 秒内能看懂在做什么

---

### Issue #17：面试讲稿（不上传 GitHub）
**操作**：
- 新建 `docs/talk.md`（gitignore）
- 5 分钟版本提纲：
  - 项目动机：研究生做 PMSM，调试痛点 → 用 Agent 解决
  - 核心能力：LangGraph + Multi-Agent + RAG + 真实控制系统工具
  - 技术取舍：为什么不用 LangChain 直接用 LangGraph、为什么砍到 2 个子 Agent
  - 难点：Tool Use 稳定性、上下文长度管理
- 30 分钟版本：在 5 分钟基础上加架构演进、踩坑、未来规划
**验收**：
- 自己对镜子讲 5 分钟版本不卡壳
- 准备 3 个高频问题的 STAR 答案

---

### Issue #18：GitHub 项目美化
**操作**：
- 仓库描述：`AI Agent for PMSM FOC development with LangGraph + RAG + real control-system tools`
- Topics: `ai-agent` `langgraph` `rag` `pmsm` `motor-control` `foc` `multi-agent` `tool-use`
- 新增 `LICENSE`（MIT）
- 顶部 badge 行：CI / Coverage / License / Python version / Live Demo
- 把现有 `README.md` 中文档与代码不一致的问题彻底清理
**验收**：
- GitHub 项目主页第一屏专业、干净
- "Insights → Community Standards" 显示满分

---

## 阶段状态跟踪

| Phase | 状态 | 完成 Issues |
|---|---|---|
| Phase 1 止血 | ⏳ pending | 0 / 8 |
| Phase 2 立人设 | ⏳ pending | 0 / 5 |
| Phase 3 包装 | ⏳ pending | 0 / 5 |

## 风险记录

| 风险 | 缓解 |
|---|---|
| python-control 在 Windows 下安装可能踩坑 | Phase 2 开头先验证安装，跑不通则降级用 scipy.signal |
| 2c4g 云服务器内存不够跑容器 | Issue #14 强约束容器 < 1GB；必要时用裸进程部署 |
| Issue #2 删除 agent_loop 后破坏现有 QQ 行为 | 先留分支 + 写端到端测试，确认通过后再合并 |
| 时间不够 3 周完成 | 优先级：Phase 1 全做完 > Issue #9 #13 #14 #16 > 其他 |

## 遇到的错误

（开工后随时填这里）

| 错误 | 尝试次数 | 解决方案 |
|---|---|---|
| | | |
