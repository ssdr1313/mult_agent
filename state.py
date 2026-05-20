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
    validation_result: str  # "pass" | "fail" — 语法/编译检查结果
    validation_log: str     # 编译检查的 stdout+stderr
    review_result: str      # "pass" | "fail"
    review_comment: str
    unit_test_code: str     # tester 生成的单元测试代码
    build_result: str       # "pass" | "fail" — auto_builder 构建+测试结果
    build_log: str          # auto_builder 的 stdout+stderr
    coverage_report: str    # 测试覆盖率报告
    frontend_test_result: str    # "pass" | "fail" — 前端高频点击测试
    frontend_test_report: str    # 前端测试报告
    delivery_report: str
    # 重试计数
    retry_count: int
    max_retries: int
