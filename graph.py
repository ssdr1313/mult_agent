from langgraph.graph import StateGraph, START, END
from state import WorkflowState
from agents import (
    product_agent,
    architect_agent,
    developer_agent,
    validator_agent,
    reviewer_agent,
    tester_agent,
    auto_builder_agent,
    frontend_agent,
    devops_agent,
)


def route_after_validate(state: WorkflowState) -> str:
    """编译验证后的路由：pass → 审查，fail → 回到开发"""
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "reviewer"
    return "reviewer" if state["validation_result"] == "pass" else "developer"


def route_after_review(state: WorkflowState) -> str:
    """审查后的路由：pass → 测试生成，fail → 回到开发"""
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "tester"
    return "tester" if state["review_result"] == "pass" else "developer"


def route_after_build(state: WorkflowState) -> str:
    """构建测试后的路由：pass → 前端测试，fail → 回到开发"""
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "frontend"
    return "frontend" if state["build_result"] == "pass" else "developer"


def build_graph() -> StateGraph:
    builder = StateGraph(WorkflowState)

    builder.add_node("product", product_agent)
    builder.add_node("architect", architect_agent)
    builder.add_node("developer", developer_agent)
    builder.add_node("validator", validator_agent)
    builder.add_node("reviewer", reviewer_agent)
    builder.add_node("tester", tester_agent)
    builder.add_node("auto_builder", auto_builder_agent)
    builder.add_node("frontend", frontend_agent)
    builder.add_node("devops", devops_agent)

    # 线性边
    builder.add_edge(START, "product")
    builder.add_edge("product", "architect")
    builder.add_edge("architect", "developer")
    builder.add_edge("developer", "validator")

    # 编译验证条件路由：pass → reviewer，fail → developer（重试）
    builder.add_conditional_edges(
        "validator",
        route_after_validate,
        {"reviewer": "reviewer", "developer": "developer"},
    )

    # 审查条件路由：pass → tester，fail → developer（重试）
    builder.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"tester": "tester", "developer": "developer"},
    )

    # tester → auto_builder
    builder.add_edge("tester", "auto_builder")

    # 构建条件路由：pass → frontend，fail → developer（重试）
    builder.add_conditional_edges(
        "auto_builder",
        route_after_build,
        {"frontend": "frontend", "developer": "developer"},
    )

    # frontend → devops → END
    builder.add_edge("frontend", "devops")
    builder.add_edge("devops", END)

    return builder.compile()
