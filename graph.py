from langgraph.graph import StateGraph, START, END
from state import WorkflowState
from agents import (
    product_agent,
    architect_agent,
    developer_agent,
    reviewer_agent,
    tester_agent,
    devops_agent,
)


def route_after_review(state: WorkflowState) -> str:
    """审查后的路由：pass 进入测试，fail 回到开发"""
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "tester"
    return "tester" if state["review_result"] == "pass" else "developer"


def route_after_test(state: WorkflowState) -> str:
    """测试后的路由：pass 进入交付，fail 回到开发"""
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "devops"
    return "devops" if state["test_result"] == "pass" else "developer"


def build_graph() -> StateGraph:
    builder = StateGraph(WorkflowState)

    # 添加节点
    builder.add_node("product", product_agent)
    builder.add_node("architect", architect_agent)
    builder.add_node("developer", developer_agent)
    builder.add_node("reviewer", reviewer_agent)
    builder.add_node("tester", tester_agent)
    builder.add_node("devops", devops_agent)

    # 线性边
    builder.add_edge(START, "product")
    builder.add_edge("product", "architect")
    builder.add_edge("architect", "developer")
    builder.add_edge("developer", "reviewer")

    # 审查条件路由：pass -> tester, fail -> developer
    builder.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"tester": "tester", "developer": "developer"},
    )

    # 测试条件路由：pass -> devops, fail -> developer
    builder.add_conditional_edges(
        "tester",
        route_after_test,
        {"devops": "devops", "developer": "developer"},
    )

    # 交付节点后结束
    builder.add_edge("devops", END)

    return builder.compile()
