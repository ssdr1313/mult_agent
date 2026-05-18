from langchain_core.messages import HumanMessage
from graph import build_graph

PHASE_NAMES = {
    "product": "需求分析",
    "design": "设计文档",
    "code": "代码生成",
    "review": "代码审查",
    "test": "测试验证",
    "done": "需求关闭",
}


def print_phase(phase: str, state: dict):
    """打印当前阶段的关键产出"""
    print("\n" + "=" * 60)
    print(f"【{PHASE_NAMES.get(phase, phase)}】")
    print("=" * 60)

    if phase == "product" and state.get("requirement"):
        print(state["requirement"])
    elif phase == "design" and state.get("design"):
        print(state["design"])
    elif phase == "code" and state.get("code"):
        print(state["code"])
    elif phase in ("review", "review_done"):
        print(f"审查结果: {state.get('review_result', '?')}")
        print(state.get("review_comment", ""))
    elif phase in ("test", "test_done"):
        print(f"测试结果: {state.get('test_result', '?')}")
        print(state.get("test_report", ""))
    elif phase == "done":
        print(state.get("delivery_report", ""))


def main():
    print("=" * 60)
    print("  多 Agent 协作工作流")
    print("  需求分析 → 设计 → 代码生成 → 审查 → 测试 → 交付")
    print("=" * 60)
    print()
    user_input = input("请输入你的需求: ").strip()
    if not user_input:
        user_input = "实现一个用户登录注册系统"

    graph = build_graph()
    print(f"\n开始处理需求: {user_input}\n")

    prev_phase = None
    for event in graph.stream(
        {
            "messages": [HumanMessage(content=user_input)],
            "phase": "product",
            "requirement": "",
            "design": "",
            "code": "",
            "review_result": "",
            "review_comment": "",
            "test_result": "",
            "test_report": "",
            "delivery_report": "",
            "retry_count": 0,
            "max_retries": 3,
        },
        stream_mode="updates",
    ):
        for node_name, state_update in event.items():
            phase = state_update.get("phase", "")
            if phase and phase != prev_phase:
                # 合并当前 state 用于打印
                merged = {
                    **({} if prev_phase is None else {}),
                    **state_update,
                }
                print_phase(phase, state_update)
                prev_phase = phase

    print("\n" + "=" * 60)
    print("  工作流执行完毕")
    print("=" * 60)


if __name__ == "__main__":
    main()
