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
    # 匹配 ### FILE: <path> 后紧跟代码块
    pattern = r"###\s*FILE:\s*(\S+)\s*\n\s*```\w*\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return [(path.strip(), content.strip()) for path, content in matches]
    # 兼容旧格式：单文件输出
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
    if state.get("test_report"):
        (OUTPUT_DIR / "test_report.md").write_text(state["test_report"], encoding="utf-8")
    if state.get("delivery_report"):
        (OUTPUT_DIR / "delivery_report.md").write_text(state["delivery_report"], encoding="utf-8")

    print(f"\n产出已保存到 {OUTPUT_DIR.resolve()}/")

PHASE_NAMES = {
    "product": "需求分析",
    "design": "设计文档",
    "code": "代码生成",
    "exec": "编译运行",
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
    elif phase in ("exec", "exec_done"):
        print(f"执行结果: {state.get('execution_result', '?')}")
        print(state.get("execution_log", ""))
    elif phase in ("review", "review_done"):
        print(f"审查结果: {state.get('review_result', '?')}")
        print(state.get("review_comment", ""))
    elif phase in ("test", "test_done"):
        print(f"测试结果: {state.get('test_result', '?')}")
        print(state.get("test_report", ""))
    elif phase == "done":
        print(state.get("delivery_report", ""))


def main():
    parser = argparse.ArgumentParser(description="多 Agent 协作工作流")
    parser.add_argument(
        "file", nargs="?", type=Path,
        help="需求文档路径（不传则交互式输入）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  多 Agent 协作工作流")
    print("  需求分析 → 设计 → 代码生成 → 审查 → 测试 → 交付")
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

    prev_phase = None
    full_state = {}
    for event in graph.stream(
        {
            "messages": [HumanMessage(content=user_input)],
            "phase": "product",
            "requirement": "",
            "design": "",
            "code": "",
            "execution_result": "",
            "execution_log": "",
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
            full_state.update(state_update)
            phase = state_update.get("phase", "")
            if phase and phase != prev_phase:
                print_phase(phase, state_update)
                prev_phase = phase

    save_outputs(full_state)

    print("\n" + "=" * 60)
    print("  工作流执行完毕")
    print("=" * 60)


if __name__ == "__main__":
    main()
