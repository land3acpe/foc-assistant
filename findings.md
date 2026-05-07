# foc-assistant 项目审计发现

> 2026-05-07 由 Explore agent 扫描产出。每条都有具体文件/行号证据。

## 项目宏观

- **代码总量**：55 个 .py 文件，**约 11654 行**
- **最大文件**：`tools/_registry.py` 863 行（纯 schema）、`qq_bot.py` 662 行、`wechat_bot.py` 634 行、`knowledge.py` 591 行、`provider/deepseek_compat.py` 476 行、`config.py` 475 行、`agent.py` 471 行
- **依赖**：12 个包（requirements.txt），`langgraph>=1.1.10` 版本号可疑
- **测试**：tests/ 12 文件 1115 行（**项目最体面的部分**）
- **CI/lint**：无 .github/workflows、无 pre-commit、无 mypy/ruff/flake8

## 评分（求职视角）

| 维度 | 分数 | 备注 |
|---|---|---|
| 项目方向 | 8/10 | PMSM × AI Agent 在求职市场稀缺 |
| 关键词覆盖 | 9/10 | LangGraph/Multi-Agent/Memory/RAG/Tool Use/Guardrails 全有 |
| 代码质量 | 3/10 | 87 处裸 except、PI 公式错、5 套记忆 |
| 可演示性 | 2/10 | 无 demo、无架构图、README 与代码不符 |
| 差异化 | 8/10 | PMSM × Agent 组合很少见 |

## 核心问题（按严重程度）

### 1. 双主循环并存
- `agent.py:59-389` 老的 `agent_loop`（330 行上帝函数）
- `graph_agent.py:78` 新的 LangGraph 工作流
- CLI 入口（`agent.py:434`）走老的，QQ Bot（`qq_bot.py:36`）走新的
- **典型"加新东西不删旧东西"**

### 2. 5 套并行的"记忆/知识"子系统
- `knowledge.py` = 倒排索引文档库（591 行）
- `experience/experience_store.py` = SQLite + FTS5（注释说从 OpenHanako 移植）
- `memory/fact_store.py` = 又一个事实库
- `memory/session_summary.py` + `deep_memory.py` + `memory_ticker.py` + `compile.py` = 会话压缩链路
- `conversation_memory.py` = 短期对话记忆
- `api/memory_api.py` = facade
- 总计约 2900+ 行，职责高度重叠

### 3. 假的 PMSM 工具（电机方向面试致命）
- `tools/_analysis.py:218`：`i_limit = 1.2 * 10  # 假设额定电流约 10A`——硬编码
- `tools/_analysis.py:213`：速度环对称最优 `Ki_s = Kp_s * ws / 4` **量纲不对**（对称最优应是 Ti=4Tσ，Ki=Kp/Ti）
- `tools/_analysis.py:241` `_generate_svpwm_table`：返回的"扇区切换""作用时间"全是字符串模板，没做任何计算
- `tools/_analysis.py:352` `_suggest_controller_params`：返回硬编码字典

### 4. 玄学 NLP
- `agents/__init__.py:27-86` `_fuzzy_score`：SequenceMatcher + 字符 2-gram Jaccard + 子串包含按 0.4/0.3/0.3 加权——**权重纯拍脑袋**
- `agents/__init__.py:73` `_pinyin_fuzzy_score`：注释说"声母韵母"实际只比较前两个字符——**完全不是拼音匹配**

### 5. 5 个子 Agent 同质化
- `agents/profiles.py` 5 个子 Agent prompt 差别只在几句话
- `agents/__init__.py:93-174` 又复制了一份 `AGENT_SEMANTIC_PROFILES`——**两份描述各活各的**
- `subagent/` 目录是空的（只有 `__init__.py:1`）

### 6. README 与代码不符
- README 第 270 行写 `tools.py`，实际是 `tools/` 包
- README 第 269 行写 `semantic_router.py`，**仓库里根本没这个文件**
- README 第 271 行写 `memory.py`，实际是 `memory/` 包
- README 标题写 "37 个工具"、第 5 行写 "33 个工具 + 5 个子 Agent"、dispatch 表是 36+2 = 38 个——**三处数字打架**

### 7. config.py 模块加载期 bug
- `config.py:248-253` 在模块加载时把 `API_KEY/BASE_URL/MODEL` 求值成全局常量
- `switch_model` 之后这些常量**不会刷新** → 是个埋着的 bug

### 8. 启动脚本爆炸
- 7 个 `run*.bat` + 7 个 `run*.ps1` + `start_bg/stop_qq/restart_qq/install_service`
- **14 个启动脚本**，绝大多数互相重复

### 9. 87 处裸 except
- 全项目 87 处 `except Exception` / `except:`
- 至少 20 处直接 `pass` 或仅 `print`
- `knowledge.py` 一个文件就 8 处（L123/160/193/223/276/309/370/382/557）

### 10. 资源泄漏
- `qq_bot.py:50`：`AGENT_RUN_LOG.open("a", encoding="utf-8").write(...)` —— **文件句柄不关**

### 11. pyproject.toml 配置错
- `pyproject.toml:42` `[tool.setuptools.packages.find] include = ["foc_assistant*"]`
- 仓库里**根本没有 `foc_assistant` 包**，所有代码平铺，这一行永远找不到任何包

## 真有价值的部分（约 2000 行）

- ✅ `knowledge.py` 倒排索引 + PDF 提取
- ✅ `tools/_file_ops.py / _search.py / _web.py / _analysis.py:_analyze_csv`（CSV 真分析）
- ✅ `tests/` 12 个测试文件（必须保留+扩展）
- ✅ QQ Bot 网关（差异化触达能力）
- ✅ `config.py` 的 `MODEL_REGISTRY` 多模型机制（设计是对的）

## 凑数的部分（约 9000+ 行）

- ❌ 5 套记忆系统（留 1 套）
- ❌ 5 个子 Agent + 拼音模糊路由
- ❌ LangGraph 之外的 `agent_loop`
- ❌ Tracing 231 行 + Guardrails 226 行 + Reflection 169 行 + Validators 91 行 + Scheduler 334 行（全保留没必要，精简）
- ❌ 14 个启动脚本
- ❌ `provider/deepseek_compat.py` 476 行（评估是否真需要）
- ❌ 字符串模板伪装成功能的"PMSM 工具"

## 安全相关

- ✅ `.env` 里有真 key，已 gitignore，**没有泄露**
- ✅ `tools/_command.py:42-44` shell 注入黑名单 + `subprocess(shell=False)`，安全够用
- ⚠️ `guardrails.py:60` 正则 prompt-injection 防御只能防小白，但作为闸门 OK

## 结论

**项目不是"屎"，是"穿了西装但里子破了的玩具"**。

求职视角下，最值钱的是：
1. **关键词矩阵**（LangGraph/Multi-Agent/RAG/Tool Use/Memory/Guardrails 都有）
2. **领域差异化**（PMSM × Agent 在市场上稀缺）
3. **现成的 tests/**（项目最体面的部分）

最致命的是：
1. **README clone 跑不起来**（提到的文件根本不存在）→ 面试官直接淘汰
2. **假 PMSM 工具**（量纲错）→ 电机方向面试官 5 秒识破
3. **代码量虚高**（11000 行只有 2000 行做事）→ HR 看 GitHub stat 觉得"水"

3 周改造目标：保留完整的 AI Agent 架构门面，里子彻底重做，加上真领域能力。详见 `task_plan.md`。
