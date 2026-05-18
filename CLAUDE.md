# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 LangGraph + DeepSeek 的多 Agent 协作软件开发工作流。接收自然语言需求，由六个 AI Agent 依次协作，模拟完整的软件开发生命周期。

## 常用命令

```bash
# 安装依赖（当前无 requirements.txt）
pip install langgraph langchain-openai langchain-core python-dotenv

# 运行交互式工作流
python main.py
```

暂无测试、lint 或格式化配置。

## 架构

四个源文件，线性依赖：

```
main.py → graph.py → agents.py → state.py
```

- **`state.py`** — `WorkflowState` TypedDict。保存所有产出物（`requirement`、`design`、`code`、`review_result`、`test_result`、`delivery_report`）以及 `retry_count` 和 `max_retries`。
- **`agents.py`** — 六个 Agent 函数，每个接收 state 并返回 state 更新字典。模块级共享一个 `ChatOpenAI` 实例（通过 OpenAI 兼容 API 调用 DeepSeek，`temperature=0.3`）。`_extract_result()` 用正则从 LLM 输出中提取 `[RESULT: pass/fail]`。
- **`graph.py`** — `build_graph()` 构建包含六个节点的 `StateGraph`。在 `reviewer` 和 `tester` 节点设有条件边，失败时回到 `developer` 重试（最多 `max_retries` 次，默认 3）。`route_after_review()` 和 `route_after_test()` 在 `retry_count >= max_retries` 时强制继续前进。
- **`main.py`** — 交互式 CLI 入口。提示用户输入需求（默认："实现一个用户登录注册系统"），流式执行图工作流，以中文标题打印各阶段产出物。

## 工作流图

```
START → product_agent → architect_agent → developer_agent → reviewer_agent
                ┌──────── pass ──────── tester_agent ──── pass ─── devops_agent → END
                │                              │
                └──── fail → developer ────────┘ fail → developer（最多重试 3 次）
```

## 配置

项目根目录的 `.env` 文件包含三个变量：`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`（默认 `deepseek-chat`）、`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）。由 `agents.py` 中 `python-dotenv` 加载。

## 注意事项

- `retry_count` 在 `developer_agent` 首次执行时就会递增，因此默认 `max_retries=3` 实际只提供 2 次重试机会，之后熔断机制会强制推进。
