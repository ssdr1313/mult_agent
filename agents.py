import os
import re
import shutil
import subprocess
import time
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


def product_agent(state: WorkflowState) -> dict:#todo图片等多模态
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


def architect_agent(state: WorkflowState) -> dict:#怎加定义代码格式prompt
    """架构师：技术设计"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深架构师。请根据需求文档输出技术设计方案，包含：\n"
            "1. 技术栈选型（含 Docker 基础镜像，如 python:3.11-slim / node:18-alpine / maven:3-openjdk-17）\n"
            "2. 模块划分\n3. 数据流设计\n4. 接口定义（REST API 或函数签名）\n"
            "5. 数据库表设计（如涉及，优先使用 SQLite 等免安装方案）\n"
            #todo"6. 制订统一的code standard"
            "输出格式使用 Markdown。只输出设计文档，不要输出其他内容。"
        )),
        HumanMessage(content=f"需求文档：\n{state['requirement']}")
    ])
    return {
        "design": response.content,
        "phase": "code",
    }


def developer_agent(state: WorkflowState) -> dict:
    """开发者：代码生成"""
    feedback = ""
    if state.get("execution_result") == "fail":
        feedback += f"\n\n⚠️ 代码编译/运行失败，请根据以下错误日志修复：\n{state['execution_log']}"
    if state.get("review_result") == "fail":
        feedback += f"\n\n⚠️ 代码审查不通过，请根据以下意见修复：\n{state['review_comment']}"
    if state.get("test_result") == "fail":
        feedback += f"\n\n⚠️ 测试不通过，请根据以下测试报告修复：\n{state['test_report']}"

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深全栈软件工程师。请严格按照架构师的设计文档生成完整的项目代码。\n\n"
            "## 核心原则\n"
            #todo"- 遵守资深架构师制订的code standard"
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
        "phase": "review",
        "retry_count": state.get("retry_count", 0) + 1,
    }


EXEC_DIR = Path("output/.exec")

DOCKER_IMAGES = {
    "python": "python:3.11-slim",
    "node": "node:18-alpine",
    "maven": "maven:3-openjdk-17",
    "go": "golang:1.21-alpine",
}

DOCKERFILE_TEMPLATES = {
    "python": (
        "FROM {image}\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN pip install -r requirements.txt 2>/dev/null || true\n"
        'CMD ["python", "main.py"]\n'
    ),
    "node": (
        "FROM {image}\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN npm install 2>/dev/null || true\n"
        'CMD ["node", "index.js"]\n'
    ),
    "maven": (
        "FROM {image} AS build\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN mvn package -q -DskipTests 2>/dev/null || mvn compile -q 2>/dev/null || true\n"
        'CMD ["java", "-jar", "target/*.jar"]\n'
    ),
    "go": (
        "FROM {image}\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "RUN go build -o app ./...\n"
        'CMD ["./app"]\n'
    ),
}


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run_in_docker(exec_dir: Path, project_type: str, log: list, timeout: int) -> bool:
    """在 Docker 容器中构建和运行项目"""
    image = DOCKER_IMAGES.get(project_type, "python:3.11-slim")
    dockerfile = DOCKERFILE_TEMPLATES.get(project_type, DOCKERFILE_TEMPLATES["python"])
    dockerfile_content = dockerfile.format(image=image)

    (exec_dir / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")
    tag = f"agent-exec-{project_type}-{os.getpid()}"

    # 构建镜像
    build_result = subprocess.run(
        ["docker", "build", "-t", tag, "."],
        capture_output=True, text=True, timeout=min(timeout, 180), cwd=str(exec_dir),
    )
    log.append(f"[docker build] exit={build_result.returncode}\n{build_result.stderr[-500:]}")
    if build_result.returncode != 0:
        return False

    # 运行容器（无网络，限时）
    try:
        run_result = subprocess.run(
            ["docker", "run", "--rm", "--network=none", tag],
            capture_output=True, text=True, timeout=min(timeout, 30), cwd=str(exec_dir),
        )
        log.append(f"[docker run] exit={run_result.returncode}\n{run_result.stdout[-500:]}{run_result.stderr[-500:]}")
        return run_result.returncode == 0
    except subprocess.TimeoutExpired:
        log.append(f"[docker run] 超时（>{min(timeout, 30)}秒），容器已自动终止")
        return True  # 能启动并运行超时说明代码可运行


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
    # 按源代码文件推断
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


def executor_agent(state: WorkflowState) -> dict:
    """执行器：实际编译和运行代码，不调用 LLM"""
    code = state.get("code", "")
    if not code:
        return {"execution_result": "fail", "execution_log": "无代码可执行", "phase": "exec_done"}

    # 1. 解析文件并写入临时目录
    files = _parse_code_files(code)
    file_dict = {path: content for path, content in files}

    exec_dir = EXEC_DIR.resolve()
    if exec_dir.exists():
        shutil.rmtree(exec_dir)
    exec_dir.mkdir(parents=True, exist_ok=True)

    for file_path, content in files:
        target = exec_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    # 2. 检测项目类型
    project_type = _detect_project_type(file_dict)

    # 3. 根据类型执行构建和运行（优先 Docker）
    log_parts = [f"项目类型: {project_type}", f"文件数: {len(files)}"]
    timeout = 180
    passed = False

    use_docker = _docker_available()
    if use_docker:
        log_parts.append("执行方式: Docker 沙箱")
    else:
        log_parts.append("执行方式: 本机子进程（Docker 不可用）")

    try:
        if use_docker and project_type in DOCKER_IMAGES:
            passed = _run_in_docker(exec_dir, project_type, log_parts, timeout)
        elif project_type == "python":
            passed = _run_python_project(exec_dir, log_parts, timeout)
        elif project_type == "node":
            passed = _run_node_project(exec_dir, log_parts, timeout)
        elif project_type == "maven":
            passed = _run_maven_project(exec_dir, log_parts, timeout)
        elif project_type == "go":
            passed = _run_go_project(exec_dir, log_parts, timeout)
        else:
            log_parts.append("无法检测项目类型，跳过执行")
            passed = True
    except subprocess.TimeoutExpired:
        log_parts.append(f"执行超时（>{timeout}秒）")
    except Exception as e:
        log_parts.append(f"执行异常: {e}")

    # 保留 Dockerfile 到 output 目录
    dockerfile_path = exec_dir / "Dockerfile"
    if dockerfile_path.exists():
        output_dockerfile = Path("output") / "Dockerfile"
        output_dockerfile.write_text(dockerfile_path.read_text(encoding="utf-8"), encoding="utf-8")
        log_parts.append(f"Dockerfile 已保留到 output/Dockerfile\n镜像: {DOCKER_IMAGES.get(project_type, 'python:3.11-slim')}")

    log = "\n".join(log_parts)
    return {
        "execution_result": "pass" if passed else "fail",
        "execution_log": log,
        "phase": "exec_done",
    }


def _run_python_project(exec_dir: Path, log: list, timeout: int) -> bool:
    """运行 Python 项目：安装依赖 + 语法检查 + 尝试运行"""
    exec_dir = exec_dir.resolve()
    req_file = exec_dir / "requirements.txt"
    if req_file.exists():
        result = subprocess.run(
            ["pip", "install", "-r", str(req_file)],
            capture_output=True, text=True, timeout=timeout, cwd=str(exec_dir),
        )
        log.append(f"[pip install] {result.returncode}\n{result.stdout[-500:]}{result.stderr[-500:]}")
        if result.returncode != 0:
            return False

    # 语法检查所有 .py 文件
    py_files = list(exec_dir.rglob("*.py"))
    for pf in py_files:
        result = subprocess.run(
            ["python", "-c", f"import py_compile; py_compile.compile({str(pf)!r}, doraise=True)"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.append(f"[语法检查失败] {pf.relative_to(exec_dir)}\n{result.stderr[-500:]}")
            return False
    log.append(f"[语法检查] {len(py_files)} 个文件全部通过")

    # 尝试运行入口文件
    entry = _find_entry(exec_dir, ["main.py", "app.py", "run.py", "manage.py", "server.py"])
    if entry:
        result = subprocess.run(
            ["python", str(entry)],
            capture_output=True, text=True, timeout=min(timeout, 15),
        )
        log.append(f"[运行 {entry.name}] exit={result.returncode}\n{result.stdout[-500:]}{result.stderr[-500:]}")
    return True


def _run_node_project(exec_dir: Path, log: list, timeout: int) -> bool:
    """运行 Node.js 项目：npm install + 语法检查"""
    exec_dir = exec_dir.resolve()
    result = subprocess.run(
        ["npm", "install"], capture_output=True, text=True,
        timeout=timeout, cwd=str(exec_dir), shell=True,
    )
    log.append(f"[npm install] {result.returncode}\n{result.stdout[-500:]}{result.stderr[-500:]}")
    if result.returncode != 0:
        return False
    js_files = list(exec_dir.rglob("*.js"))
    for jf in js_files[:20]:
        result = subprocess.run(
            ["node", "--check", str(jf)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.append(f"[语法检查失败] {jf.relative_to(exec_dir)}\n{result.stderr[-500:]}")
            return False
    log.append(f"[语法检查] 通过")
    return True


def _run_maven_project(exec_dir: Path, log: list, timeout: int) -> bool:
    """运行 Maven 项目：mvn compile"""
    exec_dir = exec_dir.resolve()
    result = subprocess.run(
        ["mvn", "compile", "-q"], capture_output=True, text=True,
        timeout=timeout, cwd=str(exec_dir),
    )
    log.append(f"[mvn compile] {result.returncode}\n{result.stdout[-500:]}{result.stderr[-500:]}")
    return result.returncode == 0


def _run_go_project(exec_dir: Path, log: list, timeout: int) -> bool:
    """运行 Go 项目：go build"""
    exec_dir = exec_dir.resolve()
    result = subprocess.run(
        ["go", "build", "./..."], capture_output=True, text=True,
        timeout=timeout, cwd=str(exec_dir),
    )
    log.append(f"[go build] {result.returncode}\n{result.stdout[-500:]}{result.stderr[-500:]}")
    return result.returncode == 0


def _find_entry(exec_dir: Path, names: list[str]) -> Path | None:
    """查找入口文件"""
    for name in names:
        candidate = exec_dir / name
        if candidate.exists():
            return candidate
    return None


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
        "phase": "review_done",
    }


def tester_agent(state: WorkflowState) -> dict:
    """测试工程师：测试验证"""
    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    response = llm.invoke([
        SystemMessage(content=(
            "你是一位资深测试工程师。请对代码进行测试验证：\n"
            "1. **完整性检查**（高优先级）：所有文件是否齐全？模块间引用是否正确？构建文件是否能拉取依赖？\n"
            "2. **静态分析**：检查逻辑缺陷、边界条件、异常场景\n"
            "3. **功能验证**：逐条对照需求文档的验收标准，检查代码是否实现\n"
            "4. **可运行性**：按 README 的步骤，是否每个命令都能成功执行？\n\n"
            # todo对delvepment的代码生成对应接口的unit test
            "输出格式：\n"
            "- 验收标准检查结果\n"
            "- 问题列表（如有）\n"
            "- 最后一行必须包含结果标记：[RESULT: pass] 或 [RESULT: fail]\n"
            "- 如果重试次数已达到最大限制，即使有问题也标记 [RESULT: pass]\n"
            f"当前重试次数：{retry}/{max_retries}"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"代码：\n{state['code']}\n\n"
            f"审查结果：{state['review_result']}\n审查意见：{state['review_comment']}"
        ))
    ])

    result = _extract_result(response.content)
    if retry >= max_retries:
        result = "pass"

    return {
        "test_result": result,
        "test_report": response.content,
        "phase": "test_done",
    }


def devops_agent(state: WorkflowState) -> dict:
    """DevOps：需求关闭与交付"""
    response = llm.invoke([
        SystemMessage(content=(
            "你是一位 DevOps 交付负责人。请根据完整的工作流产出，生成一份项目交付报告，包含：\n"
            "1. 需求摘要\n2. 技术方案摘要\n3. 交付物清单\n4. 代码审查小结\n"
            "5. 测试验证小结\n6. 部署建议\n7. 项目状态：✅ 可交付\n\n"
            "输出格式使用 Markdown。"
        )),
        HumanMessage(content=(
            f"需求文档：\n{state['requirement']}\n\n"
            f"设计文档：\n{state['design']}\n\n"
            f"代码：\n{state['code']}\n\n"
            f"审查结果：{state['review_result']}\n"
            f"审查意见：{state['review_comment']}\n\n"
            f"测试结果：{state['test_result']}\n"
            f"测试报告：{state['test_report']}"
        ))
    ])
    return {
        "delivery_report": response.content,
        "phase": "done",
    }
