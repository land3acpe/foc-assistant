# FOC-Assistant

> 专为永磁同步电机（PMSM）FOC 矢量控制开发打造的 AI Agent 助手

基于 DeepSeek V4 Pro 大模型，集成 33 个专业工具、5 个子 Agent、本地知识库和 LangGraph 工作流编排。

## 架构

```
用户输入 (QQ / 微信 / CLI)
        │
        ▼
┌───────────────────────┐
│  意图路由              │ ← 规则匹配 + 语义路由
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│  LangGraph 编排层      │ ← 状态机工作流
│  route → 6条分支 →     │
│  validate → reflect →  │
│  memorize → final      │
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│  Agent 核心循环        │ ← ReAct 模式 (LLM + Tools)
│  DeepSeek V4 Pro       │
│  33 个专业工具          │
└───────┬───────────────┘
        │
        ▼
┌───────────────────────┐
│  多 Agent 协作系统     │ ← 5 个专业子 Agent
│  代码分析 / 波形分析 /  │
│  控制器设计 / 研究 / 调试│
└───────────────────────┘
```

## 核心能力

| 能力 | 说明 |
|------|------|
| **Agent 循环** | ReAct 模式，LLM 自主决定工具调用序列 |
| **工具调用** | 33 个专业工具：文件操作、代码搜索、知识库、联网、CSV 分析、CCS 编译、PI 参数计算等 |
| **LangGraph 编排** | 状态机工作流：路由→执行→校验→反思→记忆→输出 |
| **自反思** | 任务完成后 LLM 自评质量，低分自动重试 |
| **持久记忆** | 自动从对话提取技术洞察，存入本地知识库 |
| **自主触发** | 后台调度器：知识库维护、项目文件监控、日志轮转 |
| **多 Agent 协作** | 5 个专业子 Agent：代码分析、波形分析、控制器设计、研究检索、调试 |
| **知识库** | 本地倒排索引搜索引擎，支持 PDF/CSV/MD/TXT/代码 |
| **Skill 系统** | 8 个专业领域 Skill，根据关键词自动注入 |
| **三级推理** | non-thinking / thinking / thinking_max |
| **多端接入** | CLI、QQ Bot、微信 Bot |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，填入你的 API Key：

```bash
cp .env.example .env
```

```env
DEEPSEEK_API_KEY=sk-your-key-here
QQ_APP_ID=your-qq-app-id
QQ_APP_SECRET=your-qq-app-secret
```

### 3. 运行

**CLI 模式:**
```bash
python agent.py
```

**QQ Bot 调试模式:**
```bash
run_qq_debug.bat
```

**QQ Bot 后台模式:**
```bash
run_qq.bat
```

## 专业 Agent

| Agent ID | 名称 | 擅长领域 |
|----------|------|----------|
| `code_analyzer` | 代码分析专家 | C/H/M 源代码结构、调用链、数据流分析 |
| `waveform_analyzer` | 波形分析专家 | CSV 数据：阶跃响应、稳态性能、ESO 精度 |
| `controller_designer` | 控制器设计专家 | PI/SMC/ADRC/ESO 参数整定 |
| `research_agent` | 研究/检索专家 | 联网搜索 + 本地知识库 + 论文分析 |
| `debug_helper` | 调试助手 | 编译错误、运行时异常排查 |

## 工具列表

<details>
<summary>点击展开全部 33 个工具</summary>

**文件操作:** `read_file`, `read_many_files`, `write_file`, `edit_file`, `find_files`, `list_directory`, `download_file`

**代码分析:** `search_code`, `extract_symbols`, `project_overview`

**知识库:** `knowledge_search`, `knowledge_add`, `knowledge_list`, `knowledge_import`, `knowledge_rebuild`

**联网:** `web_search`, `web_fetch`

**专业工具:** `analyze_csv`, `search_papers`, `parse_slx_model`, `read_matlab_script`, `calculate_pi_params`, `generate_svpwm_table`, `compile_ccs`, `suggest_controller_params`

**系统:** `run_command`, `task_complete`

**反思/记忆/调度:** `reflect`, `memory_search`, `memory_stats`, `scheduler_status`

**多 Agent:** `spawn_agent`, `list_agents`

</details>

## 项目结构

```
foc-assistant/
├── agent.py              # Agent 核心循环 (ReAct)
├── config.py             # 配置文件
├── graph_agent.py        # LangGraph 工作流编排
├── router.py             # 意图路由 (规则)
├── semantic_router.py    # 意图路由 (LLM 语义)
├── tools.py              # 33 个工具定义与实现
├── knowledge.py          # 本地知识库引擎
├── reflection.py         # 自反思模块
├── memory.py             # 持久记忆模块
├── scheduler.py          # 后台调度器
├── validators.py         # 输出校验
├── agents/               # 多 Agent 协作系统
│   ├── __init__.py       # Agent 协调器
│   └── profiles.py       # 专业 Agent 定义
├── qq_bot.py             # QQ Bot 网关
├── wechat_bot.py         # 微信 Bot 网关
├── knowledge_base/       # 本地知识库目录
│   ├── notes/            # 笔记 (自动索引)
│   ├── papers/           # PDF 论文
│   ├── data/             # CSV 数据
│   ├── codes/            # 参考代码
│   └── memory/           # 自动记忆存储
├── requirements.txt      # Python 依赖
├── .env.example          # 环境变量模板
├── .gitignore
├── run.bat               # CLI 启动
├── run_qq.bat            # QQ Bot 启动
└── run_qq_debug.bat      # QQ Bot 调试模式
```

## 扩展指南

### 添加新工具

在 `tools.py` 中：
1. 在 `TOOLS` 列表添加工具定义（JSON Schema）
2. 在 `execute_tool()` 添加分发分支
3. 实现工具函数 `_your_tool(args) -> str`

### 添加新 Agent

在 `agents/profiles.py` 的 `AGENT_PROFILES` 字典中新增一个条目即可，无需修改其他代码。

### 添加新 Skill

在 `config.py` 的 `SKILLS` 字典中新增条目，包含 `name`, `trigger`, `prompt_addon` 三个字段。

## License

MIT
