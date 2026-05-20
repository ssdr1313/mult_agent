import argparse
import re
from pathlib import Path

from docx import Document
from langchain_core.messages import HumanMessage
from graph import build_graph

OUTPUT_DIR = Path("output")


def read_document(path: Path) -> str:
    """读取文档文件，支持 .txt/.md（纯文本）和 .docx（Word 文档）"""
    if path.suffix.lower() == ".docx":
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    return path.read_text(encoding="utf-8")


def parse_files(text: str) -> list[tuple[str, str]]:
    """从多文件输出中解析文件列表，返回 [(路径, 内容), ...]"""
    pattern = r"###\s*FILE:\s*(\S+)\s*\n\s*```\w*\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return [(path.strip(), content.strip()) for path, content in matches]
    match = re.search(r"```(?:python|java|go|ts|js|tsx|jsx)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return [("code.py", match.group(1).strip())]
    return [("code.py", text)]


def save_outputs(state: dict):
    """将工作流产出保存到 output/ 目录"""
    OUTPUT_DIR.mkdir(exist_ok=True)

    if state.get("requirement"):
        (OUTPUT_DIR / "requirement.md").write_text(state["requirement"], encoding="utf-8")
    if state.get("design"):
        (OUTPUT_DIR / "design.md").write_text(state["design"], encoding="utf-8")
    if state.get("code"):
        files = parse_files(state["code"])
        for file_path, content in files:
            target = OUTPUT_DIR / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        print(f"  生成 {len(files)} 个文件")
    if state.get("unit_test_code"):
        # 测试代码保存到 output/tests/ 子目录
        test_dir = OUTPUT_DIR / "tests"
        test_files = parse_files(state["unit_test_code"])
        for file_path, content in test_files:
            target = test_dir / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        print(f"  生成 {len(test_files)} 个测试文件")
    if state.get("delivery_report"):
        (OUTPUT_DIR / "delivery_report.md").write_text(state["delivery_report"], encoding="utf-8")

    print(f"\n产出已保存到 {OUTPUT_DIR.resolve()}/")


PHASE_NAMES = {
    "product": "需求分析",
    "design": "设计文档",
    "code": "代码生成",
    "validate": "编译验证",
    "review": "代码审查",
    "test": "单元测试生成",
    "build": "构建与测试",
    "frontend": "前端高频点击测试",
    "devops": "需求关闭与交付",
    "done": "完成",
}

STOP_ORDER = ["product", "design", "code", "validate", "review", "test", "build", "frontend", "done"]

# 用于 --stop-at 判断：每个 phase 值对应的流程位置（越大越靠后）
_PHASE_POS = {
    "product": 0, "design": 1, "code": 2,
    "validate": 3, "review": 4, "test": 5,
    "build": 6, "frontend": 7, "devops": 8, "done": 9,
}

# 各阶段对应的 pass/fail 结果字段
_PHASE_RESULT_KEY = {
    "validate": "validation_result",
    "review": "review_result",
    "build": "build_result",
    "frontend": "frontend_test_result",
}


def _phase_passed(phase: str, state: dict) -> bool:
    """该阶段是否通过（无结果字段的阶段如 product/design/code 默认通过）"""
    key = _PHASE_RESULT_KEY.get(phase)
    return state.get(key) != "fail" if key else True


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
    elif phase == "validate":
        print(f"编译验证结果: {state.get('validation_result', '?')}")
        print(state.get("validation_log", ""))
    elif phase == "review":
        print(f"审查结果: {state.get('review_result', '?')}")
        print(state.get("review_comment", ""))
    elif phase == "test":
        unit_code = state.get("unit_test_code", "")
        if unit_code:
            files = parse_files(unit_code)
            print(f"已生成 {len(files)} 个单元测试文件")
    elif phase == "build":
        print(f"构建结果: {state.get('build_result', '?')}")
        print(state.get("build_log", ""))
        if state.get("coverage_report"):
            print(f"\n覆盖率:\n{state['coverage_report']}")
    elif phase == "frontend":
        print(f"前端测试结果: {state.get('frontend_test_result', '?')}")
        print(state.get("frontend_test_report", ""))
    elif phase == "done":
        print(state.get("delivery_report", ""))


def main():
    parser = argparse.ArgumentParser(description="多 Agent 协作工作流")
    parser.add_argument(
        "file", nargs="?", type=Path,
        help="需求文档路径（不传则交互式输入）",
    )
    parser.add_argument(
        "--stop-at", type=str, choices=STOP_ORDER, default="done",
        help="指定停止阶段（默认 done 即跑完全程），如 --stop-at review 则在审查通过后保存代码退出",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  多 Agent 协作工作流")
    print("  需求分析 → 设计 → 代码生成 → 编译验证 → 审查 → 测试生成 → 构建测试 → 前端测试 → 交付")
    print("=" * 60)
    print()

    if args.file:
        user_input = read_document(args.file).strip()
        if not user_input:
            print("错误：文件内容为空")
            return
        print(f"已读取需求文档: {args.file}")
    else:
        user_input = input("请输入你的需求: ").strip()
        if not user_input:
            user_input = "实现一个用户登录注册系统"

    graph = build_graph()
    print(f"\n开始处理需求: {user_input[:80]}{'...' if len(user_input) > 80 else ''}\n")

    stop_pos = _PHASE_POS.get(args.stop_at, 99)

    prev_phase = None
    full_state = {}
    for event in graph.stream(
        {
            "messages": [HumanMessage(content=user_input)],
            "phase": "product",
            "requirement": "",
            "design": "",
            "code": "",
            "validation_result": "",
            "validation_log": "",
            "review_result": "",
            "review_comment": "",
            "unit_test_code": "",
            "build_result": "",
            "build_log": "",
            "coverage_report": "",
            "frontend_test_result": "",
            "frontend_test_report": "",
            "delivery_report": "",
            "retry_count": 0,
            "max_retries": 5,
        },
        stream_mode="updates",
    ):
        for node_name, state_update in event.items():
            full_state.update(state_update)
            phase = state_update.get("phase", "")
            if phase and phase != prev_phase:
                print_phase(phase, full_state)
                prev_phase = phase
            # 检查是否到达停止点（阶段通过才停）
            if _PHASE_POS.get(phase, -1) >= stop_pos and _phase_passed(phase, full_state):
                break
        else:
            continue
        break

    save_outputs(full_state)

    if prev_phase and _PHASE_POS.get(prev_phase, -1) >= stop_pos:
        print("\n" + "=" * 60)
        print(f"  已停止于 {PHASE_NAMES.get(args.stop_at, args.stop_at)} 阶段")
        print(f"  产出文件在 {OUTPUT_DIR.resolve()}/")
    else:
        print("\n" + "=" * 60)
        print("  工作流执行完毕")
        print("=" * 60)


if __name__ == "__main__":
    main()
