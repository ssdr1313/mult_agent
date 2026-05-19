# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 LangGraph + DeepSeek 的多 Agent 协作软件开发工作流。接收自然语言需求，由六个 AI Agent 依次协作，模拟完整的软件开发生命周期。

## 常用命令

```bash
# 安装依赖（当前无 requirements.txt）
pip install langgraph langchain-openai langchain-core python-dotenv python-docx

# 传需求文档文件（支持 .txt / .md / .docx）
python main.py 需求文档.md

# 不传文件则交互式输入
python main.py
```

暂无测试、lint 或格式化配置。

工作流结束后，产出自动保存到 `output/` 目录：
- `requirement.md` — 需求分析文档
- `design.md` — 技术设计文档
- `<项目文件>` — 开发者按 `### FILE: <path>` 格式输出的完整项目，`main.py` 自动解析并还原目录结构
- `Dockerfile` — executor 自动生成（有 Docker 时），用于在任何机器上复现运行环境
- `delivery_report.md` — 项目交付报告

## 架构

四个源文件，线性依赖：

```
main.py → graph.py → agents.py → state.py
```

- **`state.py`** — `WorkflowState` TypedDict。保存所有产出物（`requirement`、`design`、`code`、`review_result`、`test_result`、`delivery_report`）以及 `retry_count` 和 `max_retries`。
- **`agents.py`** — 七个 Agent 函数。developer 按架构师选型生成多文件项目（`### FILE: <path>` 格式）；executor 在 Docker 沙箱中编译运行代码（Docker 不可用时回退子进程），并生成 Dockerfile 保证环境一致性；reviewer/tester 检查可运行性和完整性。
- **`graph.py`** — `build_graph()` 构建包含六个节点的 `StateGraph`。在 `reviewer` 和 `tester` 节点设有条件边，失败时回到 `developer` 重试（最多 `max_retries` 次，默认 3）。`route_after_review()` 和 `route_after_test()` 在 `retry_count >= max_retries` 时强制继续前进。
- **`main.py`** — CLI 入口。支持传需求文档（`.txt` / `.md` / `.docx`）。`parse_files()` 解析多文件输出并还原目录结构，`save_outputs()` 保存到 `output/`。

## 工作流图

```
START → product_agent → architect_agent → developer_agent → executor_agent
         ┌──────── pass ──────── reviewer_agent ─── pass ─── tester_agent ─── pass ─── devops_agent → END
         │                              │                          │
         └──── fail → developer ────────┘ fail → developer ───────┘ fail → developer（最多重试 3 次）
```

## 配置

项目根目录的 `.env` 文件包含三个变量：`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`（默认 `deepseek-chat`）、`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）。由 `agents.py` 中 `python-dotenv` 加载。

## 注意事项

- `retry_count` 在 `developer_agent` 首次执行时就会递增，因此默认 `max_retries=3` 实际只提供 2 次重试机会，之后熔断机制会强制推进。
