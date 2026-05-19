# 多 Agent 协作工作流

基于 LangGraph + DeepSeek 实现从需求到交付的完整软件开发生命周期多 Agent 协作系统。

## 工作流

```
用户输入 → 产品经理(需求分析) → 架构师(技术设计) → 开发者(代码生成) → 审查者(代码审查) -> pass → 测试(验证) ── pass → DevOps(交付) → END
                                                                   │                          │
                                    回到开发者修复（最多重试 3 次)<——└──       fail       ──────┘ 

```

## 角色职责

| 角色 | 职责 | 产出物 |
|---|---|---|
| 产品经理 | 将用户需求转化为结构化需求文档 | 功能列表、验收标准 |
| 架构师 | 输出技术设计方案 | 模块划分、接口定义、数据流 |
| 开发者 | 根据需求和设计生成代码 | 可运行的 Python 代码 |
| 审查者 | 审查代码质量（可读性/性能/安全） | 审查报告 + pass/fail |
| 测试工程师 | 验证代码正确性 | 测试用例 + 测试报告 + pass/fail |
| DevOps | 汇总全流程产出 | 项目交付报告 |

## 快速开始

### 1. 安装依赖

```bash
pip install langgraph langchain-openai langchain-core python-dotenv
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
python main.py
```

输入你的需求描述，回车后各 Agent 依次执行，控制台实时输出每个阶段的产出物。
直接回车可运行默认示例："实现一个用户登录注册系统"。

## 项目结构

```
├── state.py     # 工作流状态定义（TypedDict）
├── agents.py    # 6 个 Agent 节点实现
├── graph.py     # LangGraph 图构建 + 条件路由
└── main.py      # 入口：交互式运行
```
