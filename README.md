# FOC-Assistant

> 专为永磁同步电机（PMSM）FOC 矢量控制开发打造的 AI Agent 助手

支持多模型切换（DeepSeek V4 / MiMo-V2.5 / GPT-4o），集成 37 个专业工具、5 个子 Agent、本地知识库、LangGraph 工作流编排、Tracing、Guardrails 和声明式 Handoff。

## 架构

```
用户输入 (CLI / QQ / 微信)
        │
        ▼
┌───────────────────────────┐
│  输入 Guardrail            │ ← prompt injection / 敏感路径 / 危险命令
└───────┬───────────────────┘
        │
        ▼
┌───────────────────────────┐
│  意图路由                  │ ← 规则匹配 + 语义路由
└───────┬───────────────────┘
        │
        ▼
┌───────────────────────────┐
│  LangGraph 编排层          │ ← 状态机工作流
│  route → 6条分支 →         │
│  validate → reflect →      │
│  memorize → final          │
└───────┬───────────────────┘
        │
        ▼
┌───────────────────────────┐
│  Agent 核心循环            │ ← ReAct 模式 (LLM + Tools)
│  多模型切换 (混合策略)      │ ← DeepSeek / MiMo / GPT-4o
│  37 个专业工具              │
│  Tracing 全程记录           │
└───────┬───────────────────┘
        │
        ▼
┌───────────────────────────┐
│  声明式 Handoff            │ ← 模糊语义路由 → 5 个专业子 Agent
│  code_analyzer / waveform  │
│  controller / research /   │
│  debug_helper              │
└───────┬───────────────────┘
        │
        ▼
┌───────────────────────────┐
│  输出 Guardrail            │ ← API key 泄露 / 环境变量泄露
└───────────────────────────┘
```

## 核心能力

| 能力 | 说明 |
|------|------|
| **多模型切换** | 支持 DeepSeek V4 / MiMo-V2.5 / GPT-4o / Ollama，混合策略自动选模型 |
| **Agent 循环** | ReAct 模式，LLM 自主决定工具调用序列 |
| **工具调用** | 37 个专业工具：文件操作、代码搜索、知识库、联网、CSV 分析、CCS 编译、PI 参数计算等 |
| **LangGraph 编排** | 状态机工作流：路由→执行→校验→反思→记忆→输出 |
| **Tracing** | 内置追踪：记录 LLM 调用、工具执行、Handoff、Guardrail（JSONL 日志） |
| **Guardrails** | 输入护栏：prompt injection、敏感路径、危险命令；输出护栏：API key 泄露检测 |
| **声明式 Handoff** | 子 Agent 转交支持自动路由，模糊语义匹配（n-gram + 编辑距离 + 拼音） |
| **自反思** | 任务完成后 LLM 自评质量，低分自动重试 |
| **持久记忆** | 自动从对话提取技术洞察，存入本地知识库 |
| **知识库** | 本地倒排索引搜索引擎，支持 PDF/CSV/MD/TXT/代码 |
| **Skill 系统** | 8 个专业领域 Skill，根据关键词自动注入 |
| **多端接入** | CLI、QQ Bot、微信 Bot |

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/land3acpe/foc-assistant.git
cd foc-assistant
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

复制 `.env.example` 为 `.env`，填入你的配置：

```bash
cp .env.example .env
```

`.env` 中需要配置的关键项：

```env
# 必填：DeepSeek API Key
DEEPSEEK_API_KEY=sk-your-key-here

# 必填：你的 FOC 工程目录路径
FOC_PROJECT_ROOT=C:\Users\YourName\Desktop\your_foc_project

# 可选：启动时使用的模型（默认 deepseek-v4-pro）
FOC_ACTIVE_MODEL=deepseek-v4-pro
```

### 4. 运行

**CLI 模式:**
```bash
python agent.py
```

**QQ Bot 调试模式:**
```bash
run_qq_debug.bat
```

**自测（验证安装是否成功）:**
```bash
python test_features.py
```

## 多模型配置

### 已注册模型

| 模型 ID | 名称 | 工具调用 | 说明 |
|---------|------|---------|------|
| `deepseek-v4-pro` | DeepSeek V4 Pro | 支持 | 默认模型，强推理 |
| `deepseek-v4-flash` | DeepSeek V4 Flash | 支持 | 轻量快速 |
| `mimo-v2.5` | MiMo V2.5 | 支持 | 小米 310B MoE，需本地部署 |
| `mimo-v2.5-pro` | MiMo V2.5 Pro | 支持 | 小米 1T MoE，最强 agent 能力 |
| `mimo-api` | MiMo API (小米云) | 支持 | 小米官方 API，无需本地部署 |
| `ollama-local` | Ollama 本地模型 | 不支持 | 本地实验用 |
| `gpt-4o` | GPT-4o | 支持 | OpenAI |

### 切换模型

```bash
# 方式一：环境变量（启动前设置）
set FOC_ACTIVE_MODEL=mimo-v2.5
python agent.py

# 方式二：对话中切换
# 对 Agent 说: "切换到 mimo-v2.5 模型"

# 方式三：代码中切换
from config import get_model_manager
get_model_manager().switch_model("mimo-v2.5")
```

### 混合模型策略

不同任务类型自动选用最合适的模型（在 `config.py` 的 `HYBRID_STRATEGY` 中配置）：

| 任务类型 | 默认模型 | 原因 |
|---------|---------|------|
| 工具调用 (tool) | DeepSeek V4 Pro | 工具调用最稳定 |
| 推理分析 (reasoning) | MiMo V2.5 | 推理能力最强 |
| 简单聊天 (chat) | DeepSeek V4 Flash | 速度快、成本低 |
| 反思评估 (reflection) | DeepSeek V4 Flash | 轻量即可 |

### MiMo-V2.5 接入

MiMo-V2.5 是小米开源的推理模型，**原生支持 function calling**。

**方式 A: SGLang 部署（推荐）**
```bash
pip install sglang
python -m sglang.launch_server \
  --model-path XiaomiMiMo/MiMo-V2.5 \
  --served-model-name mimo-v2.5 \
  --tp-size 8 --context-length 262144 --quantization fp8 \
  --trust-remote-code --reasoning-parser qwen3 \
  --tool-call-parser mimo --host 127.0.0.1 --port 9001
```

**方式 B: vLLM 部署**
```bash
pip install vllm
vllm serve XiaomiMiMo/MiMo-V2.5 --served-model-name mimo-v2.5 --host 127.0.0.1 --port 9001
```

**方式 C: 小米官方 API**
```env
MIMO_API_KEY=your-key
FOC_ACTIVE_MODEL=mimo-api
```

## Tracing

自动记录所有 Agent 活动，输出到 `logs/trace_YYYY-MM-DD.jsonl`。

记录内容：
- `llm_call`: 每次 LLM 调用的模型、消息数、工具数、耗时
- `tool_call`: 每次工具调用的名称、参数、耗时
- `handoff`: 子 Agent 转交的来源/目标/任务
- `guardrail`: 每次护栏检查的方向/规则/是否拦截

查看摘要：对 Agent 说 "查看 trace 摘要"。

## Guardrails

### 输入护栏

| 规则 | 检测内容 | 动作 |
|------|---------|------|
| prompt_injection | "忽略之前的指令"、伪造 system message | 拦截 |
| sensitive_path | 访问 .env、id_rsa、密钥文件 | 拦截 |
| dangerous_command | rm -rf /、format、shutdown | 拦截 |

### 输出护栏

| 规则 | 检测内容 | 动作 |
|------|---------|------|
| api_key_leak | 输出包含 sk-xxx 等 API key | 拦截 |
| private_path_leak | 非文件任务泄露环境变量 | 拦截 |

## 专业 Agent（声明式 Handoff）

| Agent ID | 名称 | 擅长领域 |
|----------|------|---------|
| `code_analyzer` | 代码分析专家 | C/H/M 源代码结构、调用链、数据流分析 |
| `waveform_analyzer` | 波形分析专家 | CSV 数据：阶跃响应、稳态性能、ESO 精度 |
| `controller_designer` | 控制器设计专家 | PI/SMC/ADRC/ESO 参数整定 |
| `research_agent` | 研究/检索专家 | 联网搜索 + 本地知识库 + 论文分析 |
| `debug_helper` | 调试助手 | 编译错误、运行时异常排查 |

Handoff 支持两种模式：
- **显式**: `spawn_agent("code_analyzer", "分析 main.c")`
- **声明式**: `handoff_to_agent("分析这个函数的调用链")` — 系统自动选择最匹配的子 Agent（模糊语义匹配）

## 工具列表（37 个）

<details>
<summary>点击展开全部工具</summary>

**文件操作:** `read_file`, `read_many_files`, `write_file`, `edit_file`, `find_files`, `list_directory`, `download_file`

**代码分析:** `search_code`, `extract_symbols`, `project_overview`

**知识库:** `knowledge_search`, `knowledge_add`, `knowledge_list`, `knowledge_import`, `knowledge_rebuild`

**联网:** `web_search`, `web_fetch`

**专业工具:** `analyze_csv`, `search_papers`, `parse_slx_model`, `read_matlab_script`, `calculate_pi_params`, `generate_svpwm_table`, `compile_ccs`, `suggest_controller_params`

**系统:** `run_command`, `task_complete`

**反思/记忆/调度:** `reflect`, `memory_search`, `memory_stats`, `scheduler_status`

**多 Agent:** `spawn_agent`, `list_agents`, `handoff_to_agent`

**模型管理:** `switch_model`, `list_models`

**Tracing:** `trace_summary`

</details>

## 项目结构

```
foc-assistant/
├── agent.py              # Agent 核心循环 (ReAct)，集成 Tracing/Guardrails
├── config.py             # 多模型注册表、混合策略、项目配置
├── graph_agent.py        # LangGraph 工作流编排
├── router.py             # 意图路由 (规则)
├── semantic_router.py    # 意图路由 (LLM 语义)
├── tools.py              # 37 个工具定义与实现
├── knowledge.py          # 本地知识库引擎
├── reflection.py         # 自反思模块
├── memory.py             # 持久记忆模块
├── scheduler.py          # 后台调度器
├── validators.py         # 输出校验
├── tracing.py            # Tracing 系统 (JSONL 日志)
├── guardrails.py         # 输入/输出护栏
├── agents/               # 多 Agent 协作系统
│   ├── __init__.py       # Agent 协调器 + 声明式 Handoff
│   └── profiles.py       # 专业 Agent 定义
├── qq_bot.py             # QQ Bot 网关
├── wechat_bot.py         # 微信 Bot 网关
├── test_features.py      # 功能自测脚本
├── knowledge_base/       # 本地知识库目录 (gitignore)
├── logs/                 # Tracing 日志 (gitignore)
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

### 添加新模型

在 `config.py` 的 `MODEL_REGISTRY` 字典中新增条目，包含 `display_name`, `base_url`, `model_id`, `default_params` 等字段。

## License

MIT
