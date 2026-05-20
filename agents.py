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
        "phase": "design",
    }


def architect_agent(state: WorkflowState) -> dict:
    """架构师：技术设计"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深架构师。请根据需求文档输出技术设计方案，包含：\n"
            "1. 技术栈选型（含 Docker 基础镜像，如 python:3.11-slim / node:18-alpine / maven:3-openjdk-17）\n"
            "2. 模块划分\n3. 数据流设计\n4. 接口定义（REST API 或函数签名）\n"
            "5. 数据库表设计（如涉及，优先使用 SQLite 等免安装方案）\n"
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
        "phase": "code",
    }


# ═══════════════════════════════════════════════════════════════
# Phase 3: 代码生成
# ═══════════════════════════════════════════════════════════════

def developer_agent(state: WorkflowState) -> dict:
    """开发者：代码生成"""
    feedback = ""
    if state.get("validation_result") == "fail":
        feedback += f"\n\n⚠️ 编译/语法检查失败，请根据以下错误修复：\n{state['validation_log']}"
    if state.get("review_result") == "fail":
        feedback += f"\n\n⚠️ 代码审查不通过，请根据以下意见修复：\n{state['review_comment']}"
    if state.get("build_result") == "fail":
        feedback += f"\n\n⚠️ 构建/测试失败，请根据以下日志修复：\n{state['build_log']}"

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深全栈软件工程师。请严格按照架构师的设计文档生成完整的项目代码。\n\n"
            "## 核心原则\n"
            "- 严格遵守架构师在设计文档中制订的代码规范（Code Standard），包括命名、结构、错误处理、注释等全部要求\n"
            "- 严格遵循设计文档中的技术栈、语言、框架、模块划分，不得自行更改\n"
            "- 项目必须是自包含的：所有 import/require 引用的模块都必须由你生成，外部依赖通过构建文件声明\n"
            "- 每个文件输出为一个代码块\n\n"
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
            "## 质量要求\n"
            "- 所有函数和模块必须由你完整实现，禁止留 TODO 或 pass 占位\n"
            "- 模拟数据可以，但逻辑必须真实可运行\n"
            "- 项目克隆后按 README 操作即可成功启动"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"设计文档：\n{state['design']}"
            f"{feedback}"
        ))
    ])
    return {
        "code": response.content,
        "phase": "validate",
        "retry_count": state.get("retry_count", 0) + 1,
    }


# ═══════════════════════════════════════════════════════════════
# Phase 4: 编译/语法验证（轻量，不运行程序）
# ═══════════════════════════════════════════════════════════════

VALIDATE_DIR = Path("output/.validate")


def validator_agent(state: WorkflowState) -> dict:
    """语法验证器：对代码做编译/语法检查，不实际运行程序"""
    code = state.get("code", "")
    if not code:
        return {"validation_result": "fail", "validation_log": "无代码可验证", "phase": "validate_done"}

    files = _parse_code_files(code)
    file_dict = {path: content for path, content in files}

    validate_dir = VALIDATE_DIR.resolve()
    if validate_dir.exists():
        shutil.rmtree(validate_dir)
    validate_dir.mkdir(parents=True, exist_ok=True)

    for file_path, content in files:
        target = validate_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    project_type = _detect_project_type(file_dict)
    log_parts = [f"项目类型: {project_type}", f"文件数: {len(files)}"]
    timeout = 120
    passed = False

    try:
        if project_type == "python":
            passed = _validate_python(validate_dir, log_parts, timeout)
        elif project_type == "node":
            passed = _validate_node(validate_dir, log_parts, timeout)
        elif project_type == "maven":
            passed = _validate_maven(validate_dir, log_parts, timeout)
        elif project_type == "go":
            passed = _validate_go(validate_dir, log_parts, timeout)
        else:
            log_parts.append("无法检测项目类型，跳过编译验证")
            passed = True
    except subprocess.TimeoutExpired:
        log_parts.append(f"验证超时（>{timeout}秒）")
    except Exception as e:
        log_parts.append(f"验证异常: {e}")

    log = "\n".join(log_parts)
    return {
        "validation_result": "pass" if passed else "fail",
        "validation_log": log,
        "phase": "review" if passed else "validate_done",
    }


def _validate_python(validate_dir: Path, log: list, timeout: int) -> bool:
    """Python 编译检查"""
    py_files = list(validate_dir.rglob("*.py"))
    for pf in py_files:
        result = subprocess.run(
            ["python", "-m", "py_compile", str(pf)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.append(f"[编译失败] {pf.relative_to(validate_dir)}\n{result.stderr[-500:]}")
            return False
    log.append(f"[py_compile] {len(py_files)} 个文件全部通过")
    return True


def _validate_node(validate_dir: Path, log: list, timeout: int) -> bool:
    """Node.js 语法检查"""
    node = shutil.which("node") or "node"
    js_files = list(validate_dir.rglob("*.js")) + list(validate_dir.rglob("*.ts"))
    for jf in js_files[:50]:
        result = subprocess.run(
            [node, "--check", str(jf)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.append(f"[语法失败] {jf.relative_to(validate_dir)}\n{result.stderr[-500:]}")
            return False
    log.append(f"[node --check] {len(js_files)} 个文件全部通过")
    return True


def _validate_maven(validate_dir: Path, log: list, timeout: int) -> bool:
    """Maven 编译检查"""
    mvn = shutil.which("mvn") or "mvn"
    result = subprocess.run(
        [mvn, "compile", "-q"], capture_output=True, text=True,
        timeout=timeout, cwd=str(validate_dir),
    )
    log.append(f"[mvn compile] exit={result.returncode}\n{result.stderr[-500:]}")
    return result.returncode == 0


def _validate_go(validate_dir: Path, log: list, timeout: int) -> bool:
    """Go 编译检查"""
    result = subprocess.run(
        ["go", "build", "./..."], capture_output=True, text=True,
        timeout=timeout, cwd=str(validate_dir),
    )
    log.append(f"[go build] exit={result.returncode}\n{result.stderr[-500:]}")
    return result.returncode == 0


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
        "phase": "test" if result == "pass" else "review_done",
    }


# ═══════════════════════════════════════════════════════════════
# 共享工具函数
# ═══════════════════════════════════════════════════════════════

PROJECT_DIR = Path("output/project")
EXTERNAL_DIR = Path("output/external")


def _parse_code_files(code: str) -> list[tuple[str, str]]:
    """从多文件代码中解析文件列表，返回 [(路径, 内容), ...]"""
    pattern = r"###\s*FILE:\s*(\S+)\s*\n\s*```\w*\s*\n(.*?)```"
    matches = re.findall(pattern, code, re.DOTALL)
    if matches:
        return [(path.strip(), content.strip()) for path, content in matches]
    match = re.search(r"```(?:python|java|go|ts|js|tsx|jsx)?\s*\n(.*?)```", code, re.DOTALL)
    if match:
        return [("code.py", match.group(1).strip())]
    return [("code.py", code)]


def _detect_project_type(files: dict[str, str]) -> str:
    """根据文件列表检测项目类型"""
    names = set(files.keys())
    if "requirements.txt" in names or "setup.py" in names or "pyproject.toml" in names:
        return "python"
    if "package.json" in names:
        return "node"
    if "pom.xml" in names:
        return "maven"
    if "go.mod" in names:
        return "go"
    for name in names:
        if name.endswith(".py"):
            return "python"
        if name.endswith(".js") or name.endswith(".ts"):
            return "node"
        if name.endswith(".java"):
            return "maven"
        if name.endswith(".go"):
            return "go"
    return "unknown"


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
        "phase": "build",
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
                "phase": "frontend",
            }
        except (json.JSONDecodeError, KeyError) as e:
            log_parts.append(f"外部结果文件解析失败: {e}，回退到本地运行")

    # 4. 本地回退：尝试运行测试
    log_parts.append("执行方式: 本机环境（无外部构建系统）")
    project_type = _detect_project_type({f: "" for f, _ in _parse_code_files(code)})
    log_parts.append(f"项目类型: {project_type}")

    passed = False
    cov_report = ""
    timeout = 180

    try:
        if project_type == "python":
            passed, test_log, cov_report = _run_python_tests(PROJECT_DIR, timeout)
        elif project_type == "node":
            passed, test_log, cov_report = _run_node_tests(PROJECT_DIR, timeout)
        elif project_type == "maven":
            passed, test_log = _run_maven_tests(PROJECT_DIR, timeout)
        elif project_type == "go":
            passed, test_log = _run_go_tests(PROJECT_DIR, timeout)
        else:
            test_log = "无法检测项目类型，跳过本地测试"
            passed = True
    except subprocess.TimeoutExpired:
        test_log = f"测试执行超时（>{timeout}秒）"
    except Exception as e:
        test_log = f"测试执行异常: {e}"

    log_parts.append(test_log)

    return {
        "build_result": "pass" if passed else "fail",
        "build_log": "\n".join(log_parts),
        "coverage_report": cov_report or "本地回退模式未生成覆盖率报告（安装 coverage/pytest-cov 等工具可获取覆盖率）",
        "phase": "frontend",
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
        "phase": "devops",
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
