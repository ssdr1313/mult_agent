from langgraph.graph import StateGraph, START, END
from state import WorkflowState
from agents import (
    product_agent,
    architect_agent,
    developer_agent,
    executor_agent,
    reviewer_agent,
    tester_agent,
    devops_agent,
)


def route_after_exec(state: WorkflowState) -> str:
    """执行后的路由：pass 进入审查，fail 回到开发"""
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "reviewer"
    return "reviewer" if state["execution_result"] == "pass" else "developer"


def route_after_review(state: WorkflowState) -> str:
    """审查后的路由：pass 进入测试，fail 回到开发（retry 会重新走 executor）"""
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
    builder.add_node("executor", executor_agent)
    builder.add_node("reviewer", reviewer_agent)
    builder.add_node("tester", tester_agent)
    builder.add_node("devops", devops_agent)

    # 线性边
    builder.add_edge(START, "product")
    builder.add_edge("product", "architect")
    builder.add_edge("architect", "developer")
    builder.add_edge("developer", "executor")

    # 执行条件路由：pass -> reviewer, fail -> developer
    builder.add_conditional_edges(
        "executor",
        route_after_exec,
        {"reviewer": "reviewer", "developer": "developer"},
    )

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
    #todo tester后加入auto(全拼) bulider agent：
    #编译develpment代码，打成可执行的包，基于包测试tester生成的单元测试代码，输出1 unit test结果报告 2 unit test测试完后占代码覆盖率
    #agent：end to end test（端到端）
    #agent：模拟高频点击测页面效果，返回产生结果

    # 交付节点后结束
    builder.add_edge("devops", END)

    return builder.compile()
