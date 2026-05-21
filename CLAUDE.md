# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 LangGraph + DeepSeek 的多 Agent 协作软件开发工作流。接收自然语言需求，由 9 个 AI Agent 依次协作，模拟完整的软件开发生命周期。

## 常用命令

```bash
# 安装依赖
pip install langgraph langchain-openai langchain-core python-dotenv python-docx

# 传需求文档文件（支持 .txt / .md / .docx）
python main.py 需求文档.md

# 不传文件则交互式输入
python main.py

# 提前停止在指定阶段（获取中间产出）
python main.py 需求.md --stop-at review   # 审查通过后停止，获取审查后的代码
python main.py 需求.md --stop-at validate # 编译验证通过后停止
```

可选 `--stop-at` 值：`product` / `design` / `code` / `validate` / `review` / `test` / `build` / `frontend` / `done`（默认跑完全程）。

工作流结束后，产出自动保存到 `output/` 目录：
- `requirement.md` — 需求分析文档
- `design.md` — 技术设计文档（含 Code Standard）
- `<项目文件>` — developer 按 `### FILE: <path>` 格式输出的完整项目，`main.py` 自动解析并还原目录结构
- `tests/` — tester 生成的单元测试文件
- `project/` — auto_builder 保存的完整项目副本（供外部系统对接）
- `external/results.json` — 外部构建系统的结果文件入口
- `delivery_report.md` — 项目交付报告

## 架构

四个源文件，线性依赖：

```
main.py → graph.py → agents.py → state.py
```

- **`state.py`** — `WorkflowState` TypedDict。包含全部产出物字段：`requirement`、`design`、`code`、`validation_result`/`validation_log`、`review_result`/`review_comment`、`unit_test_code`、`build_result`/`build_log`/`coverage_report`、`frontend_test_result`/`frontend_test_report`、`delivery_report`，以及 `retry_count` 和 `max_retries`。
- **`agents.py`** — 9 个 Agent 函数 + 辅助函数：
  - `product_agent` — 需求分析
  - `architect_agent` — 技术设计（明确指定唯一框架 FastAPI/Flask 二选一、Vue/React 二选一，制订统一 Code Standard）
  - `developer_agent` — 多文件代码生成（`### FILE: <path>` 格式），严格遵循 Code Standard，强制框架一致性（不混用、不产生重复模块），重试时全新生成所有文件
  - `validator_agent` — **编译/语法检查**（py_compile / node --check / mvn compile / go build）+ **结构一致性检查**（框架混用检测、重复模块检测、models/__init__.py 完整性、.tsx 缺失 react 依赖等），不实际运行程序
  - `reviewer_agent` — LLM 代码审查（可运行性、需求符合性、安全、错误处理），重试时增量审查（核对上一轮问题修复情况）
  - `tester_agent` — 生成单元测试代码（`### FILE:` 格式）
  - `auto_builder_agent` — 保存代码到 `output/project/`，检查 `output/external/results.json`（外部构建结果），本地回退运行 pytest/npm test/mvn test/go test
  - `frontend_agent` — LLM 分析前端代码的高频点击问题（重复提交、竞态条件、事件泄漏、debounce/throttle）
  - `devops_agent` — 生成项目交付报告
- **`graph.py`** — `build_graph()` 构建 StateGraph。三个条件路由：`route_after_validate()`、`route_after_review()`、`route_after_build()`，失败时回到 `developer` 重试（最多 `max_retries` 次，默认 10）。超过重试次数时熔断强制推进。
- **`main.py`** — CLI 入口。支持传需求文档（`.txt` / `.md` / `.docx`）和 `--stop-at` 提前退出。`parse_files()` 解析多文件输出并还原目录结构，`save_outputs()` 保存到 `output/`。

## 工作流图

```
START → product → architect → developer → validator → reviewer → tester → auto_builder → frontend → devops → END
                      ↑            ↑           ↑                        ↑
                      └────────────┴───────────┴────────────────────────┘
                         validator fail   reviewer fail         auto_builder fail
                         (compile error)  (code quality)        (test failure)
```

反馈回路均回到 developer 重试。**编译验证失败不消耗重试配额**（代码必须能编译，不设熔断），只有审查和构建失败才计入 `retry_count`，受 `max_retries`（默认 10）熔断保护。

编译重试时 developer 只输出需要修复的文件（增量），validator 通过 `output/.code_cache/` 维护代码缓存，自动合并增量修改。审查重试时 developer 输出全量文件（结构性问题需要完整上下文）。

## 配置

项目根目录的 `.env` 文件包含三个变量：`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`（默认 `deepseek-chat`）、`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）。由 `agents.py` 中 `python-dotenv` 加载。

## 外部系统对接

`auto_builder_agent` 通过文件系统与外部构建/测试系统对接：

1. 将 developer 代码 + tester 单元测试写入 `output/project/`
2. 检查 `output/external/results.json`（格式：`{"passed": true/false, "log": "...", "coverage": "..."}`)
3. 若文件存在，使用外部结果；否则本地回退运行测试

## 注意事项

- `retry_count` 在 `developer_agent` 首次执行时就会递增，因此 `max_retries=10` 实际只提供 9 次重试机会，之后熔断机制会强制推进。
- `validator_agent` 在编译检查前先进行**结构一致性检查**（框架混用、重复模块、导入链完整性），不通过则直接 fail。编译检查包含四步：py_compile 单文件语法 → 包级别 import → 模块级别 import（逐文件验证所有 import 链）→ App 工厂创建检查（实例化 FastAPI/Flask app）。Node 项目先 node --check 语法检查，再 npm install + npm run build 构建验证。编译/语法检查和运行测试是分开的——validator 只做编译检查，实际运行测试由 `auto_builder_agent` 负责。验证通过后用缓存完整代码替换 `state.code`，确保下游 agent 看到完整项目。
- `--stop-at review` 时代码至少已经过编译验证 + LLM 审查，但未经过实际运行测试。
- `save_outputs()` 在写入新产出前会清理 `output/` 下的旧项目代码，但会跳过被其他进程锁定的文件（如 SQLite db）。
