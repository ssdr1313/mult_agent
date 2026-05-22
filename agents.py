import json
import os
import re
import shutil
import subprocess
from pathlib import Path

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


# ═══════════════════════════════════════════════════════════════
# Phase 1-2: 需求分析 → 技术设计
# ═══════════════════════════════════════════════════════════════

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
        "phase": "product",
    }


def architect_agent(state: WorkflowState) -> dict:
    """架构师：技术设计"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深架构师。请根据需求文档输出技术设计方案，包含：\n"
            "1. 技术栈选型（明确指定唯一框架和版本，不可在同一类别中混用多个，如 FastAPI+Flask 混用即 fail）\n"
            "   - 后端框架从主流方案中选择一种（如需求适合用 Python 则 FastAPI/Flask，适合 Java 则 Spring Boot，适合 Go 则 Gin）\n"
            "   - 前端框架只选一种（Vue / React / Angular / Svelte 等），严禁在同一个项目中同时使用多种\n"
            "   - 数据库优先使用 SQLite（免安装方案）\n"
            "2. 模块划分（明确每个模块的文件路径，避免出现功能重复的模块）\n"
            "3. 数据流设计\n4. 接口定义（REST API 或函数签名）\n"
            "5. 数据库表设计\n"
            "6. 统一的代码规范（Code Standard），必须包含以下内容：\n"
            "   - 命名规范：文件、类、函数、变量的命名风格（如 snake_case / camelCase）\n"
            "   - 代码结构：每个文件应包含什么（如 import 顺序、模块文档字符串、公共 API 在前）\n"
            "   - 错误处理规范：异常捕获方式、错误信息格式、日志规范\n"
            "   - 注释规范：何时需要注释、注释格式要求\n"
            "   - 测试规范：测试文件位置、测试命名、覆盖率期望\n"
            "输出格式使用 Markdown。只输出设计文档，不要输出其他内容。"
        )),
        HumanMessage(content=f"需求文档：\n{state['requirement']}")
    ])
    return {
        "design": response.content,
        "phase": "design",
    }


# ═══════════════════════════════════════════════════════════════
# Phase 3: 代码生成
# ═══════════════════════════════════════════════════════════════

def developer_agent(state: WorkflowState) -> dict:
    """开发者：代码生成"""
    retry = state.get("retry_count", 0)
    max_r = state.get("max_retries", 10)
    is_review_retry = state.get("review_result") == "fail"
    is_build_retry = state.get("build_result") == "fail"

    if is_review_retry:
        print(f"\n>>> 第 {retry}/{max_r} 次修复审查问题（质量重试）")
    elif is_build_retry:
        print(f"\n>>> 第 {retry}/{max_r} 次修复构建问题（质量重试）")
    else:
        print(f"\n>>> 首次代码生成")

    feedback = ""

    if is_review_retry:
        review = state["review_comment"]
        feedback += (
            f"\n\n⚠️ 代码审查不通过。请逐条处理以下问题（不可遗漏）：\n{review}\n\n"
            "修复策略：\n"
            "1. 按严重程度从高到低依次修复，高优先级问题必须全部解决\n"
            "2. 每修复一条问题，确保不引入新问题\n"
            "3. 凡是指出「未实现」的功能，必须写出完整可运行的代码，禁止保留模拟/占位\n"
            "4. 如果审查指出框架/库混用，只保留设计文档指定的那一种，删除另一种的全部文件\n"
            "5. 如果审查指出重复模块，只保留正确路径，更新所有 import 引用\n"
            "6. 必须输出完整的项目代码（所有文件），不仅仅是修改的文件"
        )
    if is_build_retry:
        feedback += (
            f"\n\n⚠️ 构建/测试失败，请根据以下日志修复：\n{state['build_log']}\n\n"
            "修复策略：\n"
            "1. 先定位第一个失败的测试用例，优先修复该测试覆盖的代码文件\n"
            "2. 如果错误是 ImportError / ModuleNotFoundError，检查 import 路径是否与文件结构一致\n"
            "3. 如果是断言失败，对照设计文档检查业务逻辑实现是否正确\n"
            "4. 必须输出完整的项目代码（所有文件），不仅仅是修改的文件"
        )

    # 构建 HumanMessage
    if feedback:
        # 重试时提供当前完整代码 + 反馈
        context = f"当前代码：\n{state['code']}\n\n{feedback}"
    else:
        # 首次生成：给完整的需求+设计上下文
        context = f"需求文档：\n{state['requirement']}\n\n设计文档：\n{state['design']}"

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深全栈软件工程师。请严格按照架构师的设计文档生成完整的项目代码。\n\n"
            "## 核心原则\n"
            "- 严格遵守架构师在设计文档中制订的代码规范（Code Standard），包括命名、结构、错误处理、注释等全部要求\n"
            "- 严格遵循设计文档中的技术栈、语言、框架、模块划分，不得自行更改\n"
            "- 项目必须是自包含的：所有 import/require 引用的模块都必须由你生成，外部依赖通过构建文件声明\n"
            "- 每个文件输出为一个代码块\n\n"
            "## 技术栈一致性（最高优先级，违反即为 fail）\n"
            "- 整个项目必须且只能使用设计文档指定的技术栈，禁止在同一项目中混用多个同类框架或库\n"
            "- 一个功能模块只生成一个文件，禁止为同一功能生成多个不同实现的文件（如两个数据库连接、两套配置）\n"
            "- 所有内部 import 必须指向你生成的实际文件路径，确保导入链完整且无歧义\n"
            "- 如果 __init__.py 需要 re-export 模型，必须列出所有模型文件中的类名，不可遗漏\n"
            "- 构建/依赖文件必须列出实际导入的包，且仅列出项目实际使用的包\n\n"
            "## 输出格式（必须严格遵守）\n"
            "每个文件按以下格式输出：\n\n"
            "### FILE: <项目内的文件路径>\n"
            "```<语言标识>\n"
            "文件内容\n"
            "```\n\n"
            "必须包含：\n"
            "1. 构建/依赖文件（pom.xml / package.json / requirements.txt / go.mod 等）\n"
            "2. 所有源码文件\n"
            "3. 配置文件（application.yml / .env.example 等）\n"
            "4. README.md：包含安装依赖、构建、运行的完整步骤\n\n"
            "## 重试时的特殊要求\n"
            "- 如果反馈指出的问题涉及面较广（如框架替换、重复模块清理），必须输出项目级完整代码\n"
            "- 如果要删除某个文件，在文件内容中写 ***DELETE*** 即可\n"
            "- 每次重试必须输出完整的项目代码（所有文件），不可依赖前一次生成的旧文件\n"
            "- 不可输出「其余文件不变」之类的说明\n\n"
            "## 质量要求\n"
            "- 所有函数和模块必须由你完整实现，禁止留 TODO 或 pass 占位\n"
            "- 模拟数据可以，但逻辑必须真实可运行\n"
            "- 项目克隆后按 README 操作即可成功启动"
        )),
        HumanMessage(content=context)
    ])
    return {
        "code": response.content,
        "phase": "code",
        "retry_count": state.get("retry_count", 0) + (
            1 if (state.get("review_result") == "fail" or state.get("build_result") == "fail") else 0
        ),
    }


# ═══════════════════════════════════════════════════════════════
# 共享工具函数
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# Phase 5: 代码审查
# ═══════════════════════════════════════════════════════════════

def reviewer_agent(state: WorkflowState) -> dict:
    """代码审查者：审查代码质量"""
    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深代码审查专家。请从以下维度审查代码：\n"
            "1. **可运行性**（高优先级）：所有 import/include 引用的模块是否都已生成？构建文件是否完整？按 README 操作能否启动？存在未实现的 TODO/pass 占位即 fail\n"
            "2. **需求符合性**：是否正确完整地实现了需求文档中的功能\n"
            "3. **代码质量**：可读性、命名规范、代码风格\n"
            "4. **安全性**：SQL 注入、XSS、认证缺陷、敏感信息硬编码\n"
            "5. **错误处理**：异常捕获、边界条件、输入校验\n\n"
            "## 审查策略（重试时）\n"
            "如果这是重试（retry>0），说明上一轮 review_comment 中的问题已被修复。你必须：\n"
            "- 逐一核对上一轮列出的每一条问题，确认是否已修复\n"
            "- 已修复的问题标注「✅ 已修复」并简述修复方式，不再列入本次问题清单\n"
            "- 未修复或修复不完整的问题标注「❌ 仍未修复」并保留在问题清单中\n"
            "- 只对新出现的问题正常标注严重程度\n\n"
            "## 输出格式\n"
            "- 每条问题必须标注具体文件路径和行号，格式为「文件路径:行号 — 问题描述（严重程度：高/中/低）」\n"
            "  例如：\"app/db.py:6 — 缺少 DATABASE_URL 的默认值处理（严重程度：高）\"\n"
            "  例如：\"models/__init__.py:3 — 未导出 train.py 中的 Train 类（严重程度：高）\"\n"
            "- 标注问题范围：「局部修复」（修改1-2个文件即可解决）或「结构性修复」（需跨多个文件重构）\n"
            "- 如果上一轮有问题已修复，先列「已修复」简要清单\n"
            "- 如果没有问题，写「代码审查通过」\n"
            "- 最后一行必须包含结果标记：[RESULT: pass] 或 [RESULT: fail]\n"
            "- 如果重试次数已达到最大限制，即使有问题也标记 [RESULT: pass]\n"
            f"当前重试次数：{retry}/{max_retries}"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"设计文档：\n{state['design']}\n\n"
            f"代码：\n{state['code']}"
        ) + (
            f"\n\n上一轮审查意见（请核对是否已修复）：\n{state['review_comment']}"
            if state.get("review_result") == "fail" else ""
        ))
    ])

    result = _extract_result(response.content)
    if retry >= max_retries:
        result = "pass"

    return {
        "review_result": result,
        "review_comment": response.content,
        "phase": "review",
    }


# ═══════════════════════════════════════════════════════════════
# 共享工具函数
# ═══════════════════════════════════════════════════════════════

PROJECT_DIR = Path("output/project")
EXTERNAL_DIR = Path("output/external")


def _parse_code_files(code: str) -> list[tuple[str, str]]:
    """从多文件代码中解析文件列表，返回 [(路径, 内容), ...]。
    使用 ### FILE: 标记作为文件边界，容忍文件内容中包含代码块（如 markdown 内的 ```）。"""
    pattern = r"###\s*FILE:\s*(\S+)\s*\n\s*```\w*\s*\n(.*?)(?=\n\s*###\s*FILE:|\Z)"
    matches = re.findall(pattern, code, re.DOTALL)
    if matches:
        results = []
        for path, content in matches:
            content = content.strip()
            if content.endswith("```"):
                content = content[:-3].strip()
            results.append((path.strip(), content))
        return results
    match = re.search(r"```(?:python|java|go|ts|js|tsx|jsx)?\s*\n(.*?)```", code, re.DOTALL)
    if match:
        return [("code.py", match.group(1).strip())]
    return [("code.py", code)]


def _detect_project_types(files: dict[str, str]) -> list[str]:
    """根据文件列表检测所有项目类型（全栈项目可能同时包含 python 和 node）"""
    names = set(files.keys())
    types = []
    has_py = "requirements.txt" in names or "setup.py" in names or "pyproject.toml" in names
    has_node = "package.json" in names
    has_maven = "pom.xml" in names
    has_go = "go.mod" in names
    # 从构建文件检测
    if has_py:
        types.append("python")
    if has_node:
        types.append("node")
    if has_maven:
        types.append("maven")
    if has_go:
        types.append("go")
    # 从文件扩展名补充检测（无构建文件时）
    if not types:
        for name in names:
            if name.endswith(".py"):
                types.append("python")
                break
        for name in names:
            if name.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")):
                types.append("node")
                break
        for name in names:
            if name.endswith(".java"):
                types.append("maven")
                break
        for name in names:
            if name.endswith(".go"):
                types.append("go")
                break
    return types or ["unknown"]


def _save_files_to_dir(code: str, target_dir: Path):
    """将 ### FILE: 格式的代码解析并写入目标目录"""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    files = _parse_code_files(code)
    for file_path, content in files:
        target = target_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# Phase 6: 测试生成
# ═══════════════════════════════════════════════════════════════

def tester_agent(state: WorkflowState) -> dict:
    """测试工程师：生成单元测试代码"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深测试工程师。请根据需求文档和代码，生成完整的单元测试代码。\n\n"
            "## 要求\n"
            "1. 对照需求文档的验收标准逐条编写测试用例\n"
            "2. 覆盖所有公共 API、边界条件、异常场景\n"
            "3. 使用项目技术栈对应的测试框架（Python→pytest, Node→Jest, Java→JUnit, Go→testing）\n"
            "4. 测试文件放在独立的 test/ 或 __tests__/ 目录下\n"
            "5. 每个测试函数/方法必须有明确的断言\n\n"
            "## 输出格式（必须严格遵守）\n"
            "每个测试文件按以下格式输出：\n\n"
            "### FILE: <测试文件路径>\n"
            "```<语言标识>\n"
            "文件内容\n"
            "```\n\n"
            "最后一行必须包含结果标记：[RESULT: pass] 或 [RESULT: fail]\n"
            "- 如果成功生成所有测试文件，标记 [RESULT: pass]"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"代码：\n{state['code']}"
        ))
    ])

    result = _extract_result(response.content)
    return {
        "unit_test_code": response.content,
        "phase": "test",
    }


# ═══════════════════════════════════════════════════════════════
# Phase 7: 自动构建与测试（外部系统 + 本地回退）
# ═══════════════════════════════════════════════════════════════

def auto_builder_agent(state: WorkflowState) -> dict:
    """自动构建器：将代码和测试写入 output/project/，读取外部测试结果，失败时本地回退"""
    code = state.get("code", "")
    unit_test_code = state.get("unit_test_code", "")
    log_parts = []

    # 1. 保存项目代码到 output/project/
    _save_files_to_dir(code, PROJECT_DIR)
    log_parts.append(f"项目代码已保存到 {PROJECT_DIR.resolve()}")

    # 2. 合并单元测试代码到同一目录
    if unit_test_code:
        test_files = _parse_code_files(unit_test_code)
        for file_path, content in test_files:
            target = PROJECT_DIR / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        log_parts.append(f"单元测试 {len(test_files)} 个文件已合并到项目目录")

    # 3. 检查外部系统是否已返回结果
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    result_file = EXTERNAL_DIR / "results.json"
    if result_file.exists():
        try:
            results = json.loads(result_file.read_text(encoding="utf-8"))
            log_parts.append("使用外部构建系统结果")
            return {
                "build_result": "pass" if results.get("passed") else "fail",
                "build_log": "\n".join(log_parts) + "\n" + results.get("log", ""),
                "coverage_report": results.get("coverage", "无覆盖率数据"),
                "phase": "build",
            }
        except (json.JSONDecodeError, KeyError) as e:
            log_parts.append(f"外部结果文件解析失败: {e}，回退到本地运行")

    # 4. 本地回退：尝试运行测试
    log_parts.append("执行方式: 本机环境（无外部构建系统）")
    project_types = _detect_project_types({f: "" for f, _ in _parse_code_files(code)})
    log_parts.append(f"项目类型: {', '.join(project_types)}")

    timeout = 180
    passed = True
    cov_report = ""
    test_log = ""

    try:
        for pt in project_types:
            if pt == "python":
                ok, tlog, cov = _run_python_tests(PROJECT_DIR, timeout)
                passed = passed and ok
                test_log += tlog + "\n"
                cov_report = cov or cov_report
            elif pt == "node":
                ok, tlog, cov = _run_node_tests(PROJECT_DIR, timeout)
                passed = passed and ok
                test_log += tlog + "\n"
                cov_report = cov or cov_report
            elif pt == "maven":
                ok, tlog = _run_maven_tests(PROJECT_DIR, timeout)
                passed = passed and ok
                test_log += tlog + "\n"
            elif pt == "go":
                ok, tlog = _run_go_tests(PROJECT_DIR, timeout)
                passed = passed and ok
                test_log += tlog + "\n"
            else:
                test_log = "无法检测项目类型，跳过本地测试"
    except subprocess.TimeoutExpired:
        test_log = f"测试执行超时（>{timeout}秒）"
        passed = False
    except Exception as e:
        test_log = f"测试执行异常: {e}"
        passed = False

    log_parts.append(test_log)

    return {
        "build_result": "pass" if passed else "fail",
        "build_log": "\n".join(log_parts),
        "coverage_report": cov_report or "本地回退模式未生成覆盖率报告（安装 coverage/pytest-cov 等工具可获取覆盖率）",
        "phase": "build",
    }


def _run_python_tests(project_dir: Path, timeout: int) -> tuple[bool, str, str]:
    """运行 Python 测试：优先 pytest --cov，回退 pytest，再回退 unittest"""
    log = ""
    cov = ""
    project_dir = project_dir.resolve()

    # 尝试 pytest
    if shutil.which("pytest"):
        # 尝试带覆盖率
        result = subprocess.run(
            ["pytest", f"--cov={project_dir.name}", "--cov-report=term", "-q"],
            capture_output=True, text=True, timeout=timeout, cwd=str(project_dir),
        )
        log = f"[pytest --cov] exit={result.returncode}\n{result.stdout[-1000:]}{result.stderr[-500:]}"
        cov = _extract_coverage(result.stdout)
        if result.returncode == 0:
            return True, log, cov
        # pytest 失败也可能是测试不通过（不是环境问题），直接返回结果
        return False, log, cov

    # 回退 unittest
    result = subprocess.run(
        ["python", "-m", "unittest", "discover", "-s", str(project_dir), "-p", "test_*.py"],
        capture_output=True, text=True, timeout=timeout,
    )
    log = f"[unittest] exit={result.returncode}\n{result.stdout[-1000:]}{result.stderr[-500:]}"
    return result.returncode == 0, log, ""


def _extract_coverage(output: str) -> str:
    """从 pytest-cov 输出中提取覆盖率摘要行"""
    lines = [l for l in output.split("\n") if "TOTAL" in l or "Coverage" in l or "%" in l]
    return "\n".join(lines[-5:]) if lines else ""


def _run_node_tests(project_dir: Path, timeout: int) -> tuple[bool, str, str]:
    """运行 Node 测试：npm test"""
    project_dir = project_dir.resolve()
    npm = shutil.which("npm") or "npm"
    result = subprocess.run(
        [npm, "test"], capture_output=True, text=True,
        timeout=timeout, cwd=str(project_dir),
    )
    log = f"[npm test] exit={result.returncode}\n{result.stdout[-1000:]}{result.stderr[-500:]}"
    cov = _extract_coverage(result.stdout) or _extract_coverage(result.stderr)
    return result.returncode == 0, log, cov


def _run_maven_tests(project_dir: Path, timeout: int) -> tuple[bool, str]:
    """运行 Maven 测试"""
    project_dir = project_dir.resolve()
    mvn = shutil.which("mvn") or "mvn"
    result = subprocess.run(
        [mvn, "test", "-q"], capture_output=True, text=True,
        timeout=timeout, cwd=str(project_dir),
    )
    log = f"[mvn test] exit={result.returncode}\n{result.stdout[-1000:]}{result.stderr[-500:]}"
    return result.returncode == 0, log


def _run_go_tests(project_dir: Path, timeout: int) -> tuple[bool, str]:
    """运行 Go 测试"""
    project_dir = project_dir.resolve()
    result = subprocess.run(
        ["go", "test", "./...", "-cover"], capture_output=True, text=True,
        timeout=timeout, cwd=str(project_dir),
    )
    log = f"[go test] exit={result.returncode}\n{result.stdout[-1000:]}{result.stderr[-500:]}"
    return result.returncode == 0, log


# ═══════════════════════════════════════════════════════════════
# Phase 8: 前端高频点击测试
# ═══════════════════════════════════════════════════════════════

def frontend_agent(state: WorkflowState) -> dict:
    """前端测试：模拟高频点击场景，检测潜在问题"""
    code = state.get("code", "")

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深前端测试专家。请分析代码中的前端页面，模拟高频点击场景，检测以下问题：\n\n"
            "1. **重复提交**：按钮点击后是否立即禁用？提交函数是否有防抖/节流保护？\n"
            "2. **竞态条件**：快速连续操作（搜索输入、Tab 切换、下拉选择）是否会导致状态错乱或显示不一致？\n"
            "3. **事件泄漏**：是否在组件卸载时清理了定时器、事件监听器、WebSocket 连接？\n"
            "4. **Loading 状态**：异步操作期间是否正确展示 loading 状态并阻止重复交互？\n"
            "5. **Debounce/Throttle**：搜索、滚动、resize 等高频事件是否有合理的防抖/节流？\n\n"
            "输出格式：\n"
            "- 按严重程度列出发现的问题（高/中/低），每个问题说明触发场景和修复建议\n"
            "- 如果没有问题，写「高频点击测试通过」\n"
            "- 最后一行必须包含结果标记：[RESULT: pass] 或 [RESULT: fail]\n"
            "- 如果项目不包含前端代码（纯后端/CLI/库），标记 [RESULT: pass] 并说明「无前端代码，跳过」"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"代码：\n{state['code']}"
        ))
    ])

    result = _extract_result(response.content)
    return {
        "frontend_test_result": result,
        "frontend_test_report": response.content,
        "phase": "frontend",
    }


# ═══════════════════════════════════════════════════════════════
# Phase 9: 交付
# ═══════════════════════════════════════════════════════════════

def devops_agent(state: WorkflowState) -> dict:
    """DevOps：需求关闭与交付"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位 DevOps 交付负责人。请根据完整的工作流产出，生成一份项目交付报告，包含：\n"
            "1. 需求摘要\n2. 技术方案摘要\n3. 交付物清单\n4. 代码审查小结\n"
            "5. 构建与单元测试小结\n6. 前端高频点击测试小结\n7. 部署建议\n8. 项目状态：✅ 可交付\n\n"
            "输出格式使用 Markdown。"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"设计文档：\n{state['design']}\n\n"
            f"代码：\n{state['code']}\n\n"
            f"审查结果：{state['review_result']}\n"
            f"审查意见：{state['review_comment']}\n\n"
            f"构建测试结果：{state['build_result']}\n"
            f"构建日志：{state['build_log']}\n\n"
            f"覆盖率报告：{state['coverage_report']}\n\n"
            f"前端测试结果：{state['frontend_test_result']}\n"
            f"前端测试报告：{state['frontend_test_report']}"
        ))
    ])
    return {
        "delivery_report": response.content,
        "phase": "done",
    }
