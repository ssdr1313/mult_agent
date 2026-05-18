from typing import TypedDict


class WorkflowState(TypedDict):
    # 消息历史
    messages: list
    # 当前阶段
    phase: str
    # 各阶段产出物
    requirement: str
    design: str
    code: str
    review_result: str      # "pass" | "fail"
    review_comment: str
    test_result: str         # "pass" | "fail"
    test_report: str
    delivery_report: str
    # 重试计数
    retry_count: int
    max_retries: int
