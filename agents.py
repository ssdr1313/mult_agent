import os
import re
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from state import WorkflowState

load_dotenv()

llm = ChatOpenAI(
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    openai_api_key=os.getenv("DEEPSEEK_API_KEY"),
    openai_api_base=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    temperature=0.3,
)


def _extract_result(text: str) -> str:
    """从 LLM 输出末尾提取 [RESULT: pass] 或 [RESULT: fail]"""
    match = re.search(r"\[RESULT:\s*(pass|fail)\]", text, re.IGNORECASE)
    return match.group(1).lower() if match else "pass"


def product_agent(state: WorkflowState) -> dict:
    """产品经理：需求分析"""
    user_input = state["messages"][-1].content if state["messages"] else ""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深产品经理。请将用户的原始需求转化为结构化的需求文档，包含：\n"
            "1. 功能概述\n2. 功能点列表（每项带优先级 P0/P1/P2）\n"
            "3. 验收标准\n4. 非功能性需求\n"
            "输出格式使用 Markdown。只输出需求文档，不要输出其他内容。"
        )),
        HumanMessage(content=f"用户需求：{user_input}")
    ])
    return {
        "requirement": response.content,
        "phase": "design",
    }


def architect_agent(state: WorkflowState) -> dict:
    """架构师：技术设计"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深架构师。请根据需求文档输出技术设计方案，包含：\n"
            "1. 技术栈选型\n2. 模块划分\n3. 数据流设计\n4. 接口定义（REST API 或函数签名）\n"
            "5. 数据库表设计（如涉及）\n"
            "输出格式使用 Markdown。只输出设计文档，不要输出其他内容。"
        )),
        HumanMessage(content=f"需求文档：\n{state['requirement']}")
    ])
    return {
        "design": response.content,
        "phase": "code",
    }


def developer_agent(state: WorkflowState) -> dict:
    """开发者：代码生成"""
    feedback = ""
    if state.get("review_result") == "fail":
        feedback += f"\n\n⚠️ 代码审查不通过，请根据以下意见修复：\n{state['review_comment']}"
    if state.get("test_result") == "fail":
        feedback += f"\n\n⚠️ 测试不通过，请根据以下测试报告修复：\n{state['test_report']}"

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深软件开发工程师。请根据需求文档和设计文档生成完整可运行的 Python 代码。\n"
            "要求：\n"
            "- 代码结构清晰，有适当的注释\n"
            "- 包含必要的错误处理\n"
            "- 如果是 Web 应用，使用 FastAPI 或 Flask\n"
            "- 输出纯代码（用 ```python 代码块包裹）\n"
            "- 只输出代码，不要输出其他解释"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"设计文档：\n{state['design']}"
            f"{feedback}"
        ))
    ])
    return {
        "code": response.content,
        "phase": "review",
        "retry_count": state.get("retry_count", 0) + 1,
    }


def reviewer_agent(state: WorkflowState) -> dict:
    """代码审查者：审查代码质量"""
    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深代码审查专家。请从以下维度审查代码：\n"
            "1. 可读性与代码风格\n2. 性能问题\n3. 安全漏洞\n4. 是否正确实现了需求\n"
            "5. 错误处理是否完善\n\n"
            "输出格式：\n"
            "- 先列出发现的问题（如有），每条标注严重程度（高/中/低）\n"
            "- 如果没有问题，写「代码审查通过」\n"
            "- 最后一行必须包含结果标记：[RESULT: pass] 或 [RESULT: fail]\n"
            "- 如果重试次数已达到最大限制，即使有问题也标记 [RESULT: pass]\n"
            f"当前重试次数：{retry}/{max_retries}"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"设计文档：\n{state['design']}\n\n"
            f"代码：\n{state['code']}"
        ))
    ])

    result = _extract_result(response.content)
    if retry >= max_retries:
        result = "pass"

    return {
        "review_result": result,
        "review_comment": response.content,
        "phase": "review_done",
    }


def tester_agent(state: WorkflowState) -> dict:
    """测试工程师：测试验证"""
    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深测试工程师。请对代码进行测试验证：\n"
            "1. 生成关键测试用例（列出输入和预期输出）\n"
            "2. 静态分析：检查逻辑缺陷、边界条件、异常场景\n"
            "3. 验证代码是否符合需求文档的验收标准\n\n"
            "输出格式：\n"
            "- 测试用例列表\n"
            "- 问题列表（如有）\n"
            "- 最后一行必须包含结果标记：[RESULT: pass] 或 [RESULT: fail]\n"
            "- 如果重试次数已达到最大限制，即使有问题也标记 [RESULT: pass]\n"
            f"当前重试次数：{retry}/{max_retries}"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"代码：\n{state['code']}\n\n"
            f"审查结果：{state['review_result']}\n审查意见：{state['review_comment']}"
        ))
    ])

    result = _extract_result(response.content)
    if retry >= max_retries:
        result = "pass"

    return {
        "test_result": result,
        "test_report": response.content,
        "phase": "test_done",
    }


def devops_agent(state: WorkflowState) -> dict:
    """DevOps：需求关闭与交付"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位 DevOps 交付负责人。请根据完整的工作流产出，生成一份项目交付报告，包含：\n"
            "1. 需求摘要\n2. 技术方案摘要\n3. 交付物清单\n4. 代码审查小结\n"
            "5. 测试验证小结\n6. 部署建议\n7. 项目状态：✅ 可交付\n\n"
            "输出格式使用 Markdown。"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"设计文档：\n{state['design']}\n\n"
            f"代码：\n{state['code']}\n\n"
            f"审查结果：{state['review_result']}\n"
            f"审查意见：{state['review_comment']}\n\n"
            f"测试结果：{state['test_result']}\n"
            f"测试报告：{state['test_report']}"
        ))
    ])
    return {
        "delivery_report": response.content,
        "phase": "done",
    }
