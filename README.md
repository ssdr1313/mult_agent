# 多 Agent 协作工作流

基于 LangGraph + DeepSeek 实现从需求到交付的完整软件开发生命周期多 Agent 协作系统。

## 工作流

```
用户输入 → 产品经理 → 架构师 → 开发者 → 编译验证 → 代码审查 → 测试生成 → 构建测试 → 前端测试 → DevOps → 交付
                ↑          ↑          ↑                        ↑
                └──────────┴──────────┴────────────────────────┘
                 编译失败     审查不通过                    测试失败
                (自动重试，最多 3 次)
```

## 角色职责

| 角色 | 职责 | 产出物 |
|---|---|---|
| 产品经理 | 将用户需求转化为结构化需求文档 | 功能列表、验收标准 |
| 架构师 | 输出技术设计方案，明确指定唯一框架（FastAPI/Flask 二选一），制订统一 Code Standard | 技术栈选型、模块划分、接口定义、代码规范 |
| 开发者 | 根据需求/设计/规范生成多文件项目，强制框架一致性，重试时全新生成 | `### FILE:` 格式的完整项目代码 |
| 编译验证 | 结构一致性检查 + 真实编译/语法检查（非 LLM 模拟） | 框架混用检测、重复模块检测、py_compile / node --check / mvn compile / go build |
| 代码审查 | LLM 审查代码质量、安全、需求符合性，重试时增量核对修复情况 | 审查报告 + pass/fail |
| 测试工程师 | 生成单元测试代码 | 测试文件（pytest/Jest/JUnit/Go testing） |
| 自动构建 | 运行测试，输出覆盖率，对接外部构建系统 | 测试结果 + 覆盖率报告 |
| 前端测试 | 分析前端代码的高频点击隐患 | 重复提交/竞态条件/事件泄漏检测报告 |
| DevOps | 汇总全流程产出 | 项目交付报告 |

## 快速开始

### 1. 安装依赖

```bash
pip install langgraph langchain-openai langchain-core python-dotenv python-docx
```

### 2. 配置 API Key

在项目根目录创建 `.env` 文件：

```
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

### 3. 运行

```bash
# 交互式运行
python main.py

# 从需求文档运行
python main.py 需求文档.md

# 提前停止，获取中间产出
python main.py 需求.md --stop-at review   # 审查通过后停止，把代码发给测试团队
python main.py 需求.md --stop-at validate # 编译验证通过即停止
```

可选 `--stop-at`：`product` / `design` / `code` / `validate` / `review` / `test` / `build` / `frontend` / `done`（默认）。

### 4. 产出

所有产出保存在 `output/` 目录：

```
output/
├── requirement.md          # 需求分析文档
├── design.md               # 技术设计文档（含 Code Standard）
├── <项目文件>               # 生成的项目代码（按原始目录结构）
├── tests/                  # 单元测试文件
├── project/                # 完整项目副本（供外部构建系统使用）
├── external/results.json   # 外部构建结果入口
└── delivery_report.md      # 项目交付报告
```

## 外部系统对接

将 `output/project/` 交给外部构建系统，构建系统完成后将结果写入 `output/external/results.json`：

```json
{
  "passed": true,
  "log": "测试运行日志...",
  "coverage": "覆盖率报告..."
}
```

再次运行工作流时，`auto_builder_agent` 会自动读取外部结果。

## 项目结构

```
├── state.py     # 工作流状态定义（TypedDict）
├── agents.py    # 9 个 Agent 节点 + 辅助函数（含编译验证、结构一致性检查）
├── graph.py     # LangGraph 图构建 + 条件路由
└── main.py      # CLI 入口 + 产出保存（写入前清理旧文件）
```

## 已知局限与改进方向

通过对 `output/` 产出的实际代码审查和运行验证，发现了当前多 Agent 工作流的几个系统性盲区：

### 1. LLM 审查 ≠ 实际运行

`reviewer_agent` 只阅读代码文本，无法捕获运行时错误。已验证的漏网问题：

| 问题 | 为什么 reviewer 没发现 |
|---|---|
| `HTTPBearer()` 无凭证时返回 403 而非 401 | 审查不检查 HTTP 语义正确性 |
| `datetime.utcnow()` 在 Python 3.12+ 已弃用 | 审查不验证 API 版本兼容性 |
| `fastapi-cache2` 包名与 `fastapi_cache` 模块名不一致 | 审查不执行 `pip install` 验证 |
| `allow_credentials=True` + `allow_origins=["*"]` 不安全 | 审查未覆盖 CORS 安全配置 |

### 2. 跨 Agent 信息断层

- **architect 选型 → developer 实现**：架构师选了 `fastapi-cache2==0.2.1`，开发者用 `from fastapi_cache import ...` 导入。两者的拼写差异只有实际安装才能发现。
- **tester 期望 → developer 实现**：测试期望 401，developer 的实现返回 403。两个 Agent 各自独立工作，没有真实验证。
- **design.md 约定 → 实际生成**：缺少自动化的契约校验机制来检查依赖声明和实际导入是否一致。

### 3. 构建/测试阶段执行不完整

- `auto_builder_agent` 的前端本地回退是 `echo "Test passed"`，不是真实的 `npm run test`。
- 后端 pytest 测试从未在 builder 阶段实际运行——测试被生成但被跳过。
- 缺少一个端到端冒烟测试（启动服务 → curl 所有 API → 验证状态码和返回格式）。

### 4. 建议改进

| 优先级 | 改进项 | 说明 |
|---|---|---|
| 高 | **真实构建验证** | `auto_builder_agent` 必须实际执行 `pytest` 和 `npm test`，不能用占位 echo |
| 高 | **依赖一致性校验** | `validate_agent` 增加对 `requirements.txt` 包名与实际 `import` 语句的交叉验证 |
| 中 | **冒烟测试 Agent** | 在 builder 之后新增 `smoke_test_agent`：启动服务、curl 所有 API、验证响应 |
| 中 | **审查 checklist 扩展** | reviewer 增加：CORS 安全配置、弃用 API 检测、HTTP 状态码语义正确性 |
| 低 | **Architect 输出机器可读** | 将 design.md 中的技术栈选型输出为结构化 JSON，供后续 Agent 做自动化校验 |
