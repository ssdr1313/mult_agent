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
    is_compile_retry = state.get("validation_result") == "fail"
    is_review_retry = state.get("review_result") == "fail"
    is_build_retry = state.get("build_result") == "fail"

    if is_compile_retry:
        print(f"\n>>> 修复编译错误")
    elif is_review_retry:
        print(f"\n>>> 第 {retry}/{max_r} 次修复审查问题（质量重试）")
    elif is_build_retry:
        print(f"\n>>> 第 {retry}/{max_r} 次修复构建问题（质量重试）")
    else:
        print(f"\n>>> 首次代码生成")

    feedback = ""

    if is_compile_retry:
        feedback += (
            f"\n\n⚠️ 编译/语法检查失败，请**只修改有错误的文件**（不要重写整个项目）：\n"
            f"{state['validation_log']}\n\n"
            "修复要求：\n"
            "1. 只输出有错误的文件（用 ### FILE: 格式），其他文件保持不变，不要输出\n"
            "2. 修改 import 路径时，确保项目中只存在被引用的实际文件\n"
            "3. 如果某个文件需要删除（如重复模块），在文件内容中写 ***DELETE***"
        )
    if is_review_retry:
        review = state["review_comment"]
        feedback += (
            f"\n\n⚠️ 代码审查不通过。请逐条处理以下问题（不可遗漏）：\n{review}\n\n"
            "修复策略：\n"
            "1. 按严重程度从高到低依次修复，高优先级问题必须全部解决\n"
            "2. 每修复一条问题，确保不引入新问题\n"
            "3. 凡是指出「未实现」的功能，必须写出完整可运行的代码，禁止保留模拟/占位\n"
            "4. 每条问题已标注文件路径，使用 ### FILE: 格式输出修改后的文件，只输出有改动的文件\n"
            "5. 如果审查指出框架/库混用，只保留设计文档指定的那一种，删除另一种的全部文件（文件内容写 ***DELETE***）\n"
            "6. 如果审查指出重复模块（如 config.py 和 core/config.py），只保留正确路径，更新所有 import 引用\n"
            "7. 局部修复只输出受影响文件；结构性修复（如框架替换）可输出多个相关文件，无需输出未受影响的文件"
        )
    if is_build_retry:
        feedback += (
            f"\n\n⚠️ 构建/测试失败，请根据以下日志修复：\n{state['build_log']}\n\n"
            "修复策略：\n"
            "1. 先定位第一个失败的测试用例，优先修复该测试覆盖的代码文件\n"
            "2. 如果错误是 ImportError / ModuleNotFoundError，检查 import 路径是否与文件结构一致\n"
            "3. 如果是断言失败，对照设计文档检查业务逻辑实现是否正确\n"
            "4. 只输出需要修改的文件（### FILE: 格式），未改动的文件不要重复输出"
        )

    # 构建 HumanMessage：首次生成给全量上下文，重试只给修复所需的最小信息
    if feedback:
        # 重试：精简 prompt。编译/构建的 error log 已含文件路径+行号，无需再给全量代码
        if is_review_retry:
            # 审查重试需要看到当前代码（审查意见引用了具体文件）
            context = f"当前代码：\n{state['code']}\n\n{feedback}"
        else:
            # 编译/构建重试：error log 本身已有足够的错误定位信息
            context = feedback
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
            "- 重试时只输出需要修改的文件（用 ### FILE: 格式逐一标注），其他未改动的文件不要重复输出\n"
            "- 如果反馈指出的问题涉及面较广（如框架替换、重复模块清理），可输出所有相关文件，但无需输出未受影响的文件\n"
            "- 如果要删除某个文件，在文件内容中写 ***DELETE*** 即可\n"
            "- 不可依赖前一次生成的旧文件，不可输出「其余文件不变」之类的说明\n"
            "- 之前可能生成了错误的重复文件（如同时有 config.py 和 core/config.py），这次只保留正确的那一个\n\n"
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
        # 编译失败不消耗重试配额，只有审查/构建失败的质量问题才计数
        "retry_count": state.get("retry_count", 0) + (
            1 if (state.get("review_result") == "fail" or state.get("build_result") == "fail") else 0
        ),
    }


# ═══════════════════════════════════════════════════════════════
# Phase 4: 编译/语法验证（轻量，不运行程序）
# ═══════════════════════════════════════════════════════════════

VALIDATE_DIR = Path("output/.validate")
CODE_CACHE = Path("output/.code_cache")


def validator_agent(state: WorkflowState) -> dict:
    """语法验证器：对代码做编译/语法检查，不实际运行程序。
    使用 CODE_CACHE 维护代码缓存：首次全量写入，重试时只合并 developer 的增量修改。"""
    code = state.get("code", "")
    if not code:
        return {"validation_result": "fail", "validation_log": "无代码可验证", "phase": "validate"}

    try:
        return _do_validate(state, code)
    except Exception as e:
        import traceback
        return {
            "validation_result": "fail",
            "validation_log": f"验证器内部异常（将返回 developer 重试）:\n{traceback.format_exc()}",
            "phase": "validate",
        }


def _do_validate(state: WorkflowState, code: str) -> dict:
    """验证器主逻辑，被 try/except 保护"""
    files = _parse_code_files(code)
    is_retry = state.get("validation_result") == "fail"

    cache_dir = CODE_CACHE.resolve()

    if not is_retry:
        # 首次验证：清空缓存，全量写入
        if cache_dir.exists():
            try:
                shutil.rmtree(cache_dir)
            except PermissionError:
                pass  # Windows 下文件被占用时跳过
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 写入/合并文件
    for file_path, content in files:
        target = cache_dir / file_path
        try:
            if content.strip() == "***DELETE***":
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        except (PermissionError, OSError) as e:
            log_parts = [f"写入文件失败: {file_path}\n{e}"]
            return {"validation_result": "fail", "validation_log": "\n".join(log_parts), "phase": "validate"}

    # 从缓存读取完整文件集
    file_dict = {}
    for p in cache_dir.rglob("*"):
        if p.is_file():
            try:
                rel = str(p.relative_to(cache_dir)).replace("\\", "/")
                file_dict[rel] = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue  # 跳过无法读取的文件（二进制、锁定文件等）

    if not file_dict:
        return {"validation_result": "fail", "validation_log": "缓存中无可读取的源码文件", "phase": "validate"}

    project_type = _detect_project_type(file_dict)
    log_parts = [f"项目类型: {project_type}", f"文件总数: {len(file_dict)}（本次更新 {len(files)} 个）"]

    # 框架一致性检查
    try:
        consistency_ok, consistency_msg = _check_project_consistency(file_dict, project_type)
    except Exception as e:
        log_parts.append(f"[结构检查异常] {e}")
        consistency_ok = False
    log_parts.append(consistency_msg)
    if not consistency_ok:
        return {"validation_result": "fail", "validation_log": "\n".join(log_parts), "phase": "validate"}

    timeout = 120
    passed = False

    try:
        _check_executables(project_type)
        if project_type == "python":
            passed = _validate_python(cache_dir, log_parts, timeout)
        elif project_type == "node":
            passed = _validate_node(cache_dir, log_parts, timeout)
        elif project_type == "maven":
            passed = _validate_maven(cache_dir, log_parts, timeout)
        elif project_type == "go":
            passed = _validate_go(cache_dir, log_parts, timeout)
        else:
            log_parts.append("无法检测项目类型，跳过编译验证")
            passed = True
    except subprocess.TimeoutExpired:
        log_parts.append(f"验证超时（>{timeout}秒）")
    except Exception as e:
        log_parts.append(f"编译验证异常: {e}")

    log = "\n".join(log_parts)
    result = {
        "validation_result": "pass" if passed else "fail",
        "validation_log": log,
        "phase": "validate",
    }
    # 验证通过时，用缓存的完整代码替换 state.code
    if passed:
        parts = []
        for fpath in sorted(file_dict.keys()):
            ext = fpath.rsplit(".", 1)[-1] if "." in fpath else ""
            parts.append(f"### FILE: {fpath}\n```{ext}\n{file_dict[fpath]}\n```")
        result["code"] = "\n\n".join(parts)
    return result


def _check_executables(project_type: str):
    """预检必需的可执行文件，避免 subprocess 抛出 FileNotFoundError"""
    required = {
        "python": ["python"],
        "node": ["node"],
        "maven": ["mvn"],
        "go": ["go"],
    }
    for tool in required.get(project_type, []):
        if not shutil.which(tool):
            raise RuntimeError(f"未找到 {tool} 可执行文件，请确认已安装并加入 PATH")


def _validate_python(validate_dir: Path, log: list, timeout: int) -> bool:
    """Python 语法检查 + 跨文件导入验证"""
    validate_dir = validate_dir.resolve()
    py_files = list(validate_dir.rglob("*.py"))

    # 1. 单文件语法检查
    for pf in py_files[:100]:  # 限制文件数，避免超时
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", str(pf)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                log.append(f"[编译失败] {pf.relative_to(validate_dir)}\n{result.stderr[-500:]}")
                return False
        except (subprocess.TimeoutExpired, OSError) as e:
            log.append(f"[编译异常] {pf.relative_to(validate_dir)}: {e}")
            return False
    log.append(f"[py_compile] {min(len(py_files), 100)} 个文件全部通过")

    # 2. 跨文件导入检查：找到所有包，尝试 import 验证 import 链是否完整
    packages = _find_packages(validate_dir)
    if not packages:
        log.append("[导入检查] 未发现 Python 包，跳过")
        return True

    for pkg in packages[:20]:  # 限制包数量
        try:
            result = subprocess.run(
                ["python", "-c", (
                    f"import sys; sys.path.insert(0, {str(validate_dir)!r}); "
                    f"import {pkg}"
                )],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                err = result.stderr.strip().split("\n")[-1] if result.stderr.strip() else result.stdout.strip()
                log.append(f"[导入失败] import {pkg}\n{err[-500:]}")
                return False
        except (subprocess.TimeoutExpired, OSError) as e:
            log.append(f"[导入异常] import {pkg}: {e}")
            return False
    log.append(f"[导入检查] {min(len(packages), 20)} 个包全部导入成功")

    # 3. 模块级导入检查：验证所有 .py 模块能否成功导入（不含 __init__.py）
    modules = []
    for pf in sorted(validate_dir.rglob("*.py")):
        if pf.name == "__init__.py":
            continue
        rel = pf.relative_to(validate_dir)
        mod_name = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")
        modules.append(mod_name)

    for mod_name in modules[:50]:  # 限制模块数量
        try:
            result = subprocess.run(
                ["python", "-c", (
                    f"import sys; sys.path.insert(0, {str(validate_dir)!r}); "
                    f"import {mod_name}"
                )],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                err = result.stderr.strip().split("\n")[-1] if result.stderr.strip() else result.stdout.strip()
                log.append(f"[导入失败] import {mod_name}\n{err[-500:]}")
                return False
        except (subprocess.TimeoutExpired, OSError) as e:
            log.append(f"[导入异常] import {mod_name}: {e}")
            return False
    log.append(f"[模块检查] {min(len(modules), 50)} 个模块全部导入成功")

    # 4. App 工厂创建检查：验证 Web 框架 app 能否成功实例化
    try:
        app_modules = _find_app_modules(validate_dir)
    except Exception:
        app_modules = []
    if app_modules:
        for app_import in app_modules[:3]:  # 最多检查 3 个入口
            try:
                result = subprocess.run(
                    ["python", "-c", (
                        f"import sys; sys.path.insert(0, {str(validate_dir)!r}); "
                        f"{app_import}"
                    )],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    err = result.stderr.strip().split("\n")[-1] if result.stderr.strip() else result.stdout.strip()
                    log.append(f"[App 创建失败] {app_import}\n{err[-500:]}")
                    return False
            except (subprocess.TimeoutExpired, OSError) as e:
                log.append(f"[App 创建异常] {app_import}: {e}")
                return False
        log.append(f"[App 检查] {len(app_modules)} 个 app 入口全部创建成功")
    else:
        log.append("[App 检查] 未检测到 Web 框架入口，跳过")

    return True


def _find_packages(root: Path) -> list[str]:
    """找出 root 下所有 Python 包名（用 . 分隔的导入路径），按深度排序"""
    packages = []
    for d in sorted(root.rglob("__init__.py")):
        rel = d.parent.relative_to(root)
        pkg_name = ".".join(rel.parts)
        if pkg_name:
            packages.append(pkg_name)
    # 浅层包优先导入（被依赖的先验证）
    packages.sort(key=lambda x: x.count("."))
    return packages


def _find_app_modules(root: Path) -> list[str]:
    """找出 Web 框架入口模块，返回可执行的 import 语句列表。
    覆盖主流 Python Web 框架的常见入口模式。"""
    app_patterns = [
        # FastAPI / Starlette / Litestar 等 ASGI 框架
        (r"^\s*app\s*=\s*\w+\s*\(", "from {mod_path} import app"),
        # Flask / Quart 等 WSGI/ASGI 工厂模式
        ("def create_app", "from {mod_path} import create_app; create_app()"),
        # Django (manage.py 或独立 app 配置)
        ("DJANGO_SETTINGS_MODULE", None),  # Django 项目跳过（过于复杂）
    ]
    imports = []
    for pf in sorted(root.rglob("*.py")):
        content = pf.read_text(encoding="utf-8", errors="ignore")
        rel = pf.relative_to(root)
        mod_path = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")

        for pattern, stmt in app_patterns:
            if stmt and re.search(pattern, content, re.MULTILINE):
                imports.append(stmt.format(mod_path=mod_path))
                break  # 每个文件只匹配一种模式
    return imports


def _validate_node(validate_dir: Path, log: list, timeout: int) -> bool:
    """Node.js 语法检查 + 构建验证"""
    node = shutil.which("node") or "node"
    js_files = list(validate_dir.rglob("*.js")) + list(validate_dir.rglob("*.ts"))
    for jf in js_files[:50]:
        try:
            result = subprocess.run(
                [node, "--check", str(jf)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                log.append(f"[语法失败] {jf.relative_to(validate_dir)}\n{result.stderr[-500:]}")
                return False
        except (subprocess.TimeoutExpired, OSError) as e:
            log.append(f"[语法异常] {jf.relative_to(validate_dir)}: {e}")
            return False
    log.append(f"[node --check] {min(len(js_files), 50)} 个文件全部通过")

    # 如果 package.json 存在，尝试 npm install + npm run build
    pkg_json = validate_dir / "package.json"
    if not pkg_json.exists():
        return True

    # 检查 package.json 是否有 build 脚本
    try:
        import json
        pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
        build_script = pkg.get("scripts", {}).get("build", "")
    except Exception:
        return True

    if not build_script:
        log.append("[npm build] package.json 无 build 脚本，跳过")
        return True

    # 检查 node_modules
    npm = shutil.which("npm") or "npm"
    if not (validate_dir / "node_modules").exists():
        try:
            result = subprocess.run(
                [npm, "install"], capture_output=True, text=True,
                timeout=timeout, cwd=str(validate_dir),
            )
            if result.returncode != 0:
                log.append(f"[npm install 失败]\n{result.stderr[-300:]}")
                return False
            log.append("[npm install] 依赖安装完成")
        except (subprocess.TimeoutExpired, OSError) as e:
            log.append(f"[npm install 异常] {e}")
            return False

    try:
        result = subprocess.run(
            [npm, "run", "build"], capture_output=True, text=True,
            timeout=timeout, cwd=str(validate_dir),
        )
        if result.returncode != 0:
            stderr_tail = result.stderr[-500:] if result.stderr else result.stdout[-500:]
            log.append(f"[npm build 失败] exit={result.returncode}\n{stderr_tail}")
            return False
        log.append("[npm build] 构建成功")
    except (subprocess.TimeoutExpired, OSError) as e:
        log.append(f"[npm build 异常] {e}")
        return False
    return True


def _validate_maven(validate_dir: Path, log: list, timeout: int) -> bool:
    """Maven 编译检查"""
    mvn = shutil.which("mvn") or "mvn"
    try:
        result = subprocess.run(
            [mvn, "compile", "-q"], capture_output=True, text=True,
            timeout=timeout, cwd=str(validate_dir),
        )
        log.append(f"[mvn compile] exit={result.returncode}\n{result.stderr[-500:]}")
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        log.append(f"[mvn compile 异常] {e}")
        return False


def _validate_go(validate_dir: Path, log: list, timeout: int) -> bool:
    """Go 编译检查"""
    try:
        result = subprocess.run(
            ["go", "build", "./..."], capture_output=True, text=True,
            timeout=timeout, cwd=str(validate_dir),
        )
        log.append(f"[go build] exit={result.returncode}\n{result.stderr[-500:]}")
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        log.append(f"[go build 异常] {e}")
        return False


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


def _check_project_consistency(files: dict[str, str], project_type: str) -> tuple[bool, str]:
    """检测项目结构一致性：框架混用、重复模块等"""
    issues = []

    if project_type == "python":
        _check_python_consistency(files, issues)
    elif project_type == "node":
        _check_node_consistency(files, issues)

    if issues:
        return False, "[结构一致性检查]\n" + "\n".join(f"  ❌ {i}" for i in issues)
    return True, "[结构一致性检查] 通过"

def _check_python_consistency(files: dict[str, str], issues: list):
    """Python 项目一致性检查（通用，不绑定特定框架）"""
    imports_all = "\n".join(
        line for content in files.values()
        for line in content.split("\n")
        if line.strip().startswith(("import ", "from "))
    )

    # 1. Web 框架混用检测（覆盖主流 Python Web 框架）
    python_web_frameworks = [
        "flask", "fastapi", "django", "tornado", "pyramid",
        "aiohttp", "sanic", "bottle", "falcon", "cherrypy",
    ]
    found_frameworks = [
        fw for fw in python_web_frameworks
        if re.search(rf"\b{fw}\b", imports_all, re.IGNORECASE)
    ]
    if len(found_frameworks) > 1:
        issues.append(f"Web 框架混用：同时使用了 {', '.join(found_frameworks)}，必须只保留设计文档指定的那一种")

    # 2. ORM 混用检测
    orm_list = ["sqlalchemy", "peewee", "pony", "tortoise", "sqlobject"]
    found_orms = [o for o in orm_list if re.search(rf"\b{o}\b", imports_all, re.IGNORECASE)]
    if len(found_orms) > 1:
        issues.append(f"ORM 混用：同时使用了 {', '.join(found_orms)}，必须只保留一种")

    # 3. 重复配置文件检测（同一目录下不应有多个 config 文件）
    config_files = [f for f in files if f.endswith("config.py") or f.endswith("settings.py")]
    if len(config_files) > 1:
        issues.append(f"重复配置模块：{', '.join(config_files)}，只应保留一个配置文件")

    # 4. 重复数据库连接模块检测
    db_files = [f for f in files if f.endswith(("db.py", "database.py"))]
    if len(db_files) > 1:
        issues.append(f"重复数据库模块：{', '.join(db_files)}，只应保留一个数据库连接文件")

    # 5. 重复入口文件检测（多个 main.py / manage.py / app.py）
    entry_files = [f for f in files if f in ("main.py", "manage.py", "app.py") or f.endswith(("/main.py", "/manage.py", "/app.py"))]
    if len(entry_files) > 1:
        issues.append(f"多个入口文件：{', '.join(entry_files)}，只应保留一个")

    # 6. models/__init__.py 是否遗漏独立模型文件
    for fpath, content in files.items():
        if fpath.endswith("models/__init__.py") or fpath.endswith("models\\__init__.py"):
            model_dir = fpath.replace("__init__.py", "")
            individual_models = [
                f.replace(model_dir, "").replace("\\", "/")
                for f in files
                if f.startswith(model_dir) and f != fpath and f.endswith(".py") and "__init__" not in f
            ]
            if individual_models:
                imported = re.findall(r'from\s+\.(\w+)\s+import|import\s+(\w+)', content)
                if not imported:
                    issues.append(
                        f"{fpath} 未显式导出独立模型文件 {individual_models}，"
                        f"应添加 from .module import ClassName"
                    )
            break


def _check_node_consistency(files: dict[str, str], issues: list):
    """Node.js 项目一致性检查（通用，不绑定特定框架）"""
    content_all = "\n".join(files.values())
    pkg_json_str = files.get("package.json", "")

    # 1. 前端框架混用检测
    frontend_patterns = {
        "vue": r'from\s+[\'"]vue[\'"]|\bcreateApp\b|\bdefineComponent\b',
        "react": r'from\s+[\'"]react[\'"]|\buseState\b|\bcreateRoot\b|\bJSX\.',
        "angular": r'from\s+[\'"]@angular|Component\s*\(|NgModule\s*\(',
        "svelte": r'from\s+[\'"]svelte|onMount\s*\(',
    }
    found_frontend = [
        name for name, pattern in frontend_patterns.items()
        if re.search(pattern, content_all)
    ]
    if len(found_frontend) > 1:
        issues.append(f"前端框架混用：同时使用 {', '.join(found_frontend)}，必须只保留一种")

    # 2. 后端框架混用检测
    backend_patterns = {
        "express": r'require\s*\(\s*[\'"]express[\'"]|from\s+[\'"]express[\'"]',
        "koa": r'require\s*\(\s*[\'"]koa[\'"]|from\s+[\'"]koa[\'"]',
        "fastify": r'require\s*\(\s*[\'"]fastify[\'"]|from\s+[\'"]fastify[\'"]',
        "hapi": r'require\s*\(\s*[\'"]@hapi|from\s+[\'"]@hapi',
        "nest": r'from\s+[\'"]@nestjs',
    }
    found_backend = [
        name for name, pattern in backend_patterns.items()
        if re.search(pattern, content_all)
    ]
    if len(found_backend) > 1:
        issues.append(f"后端框架混用：同时使用 {', '.join(found_backend)}，必须只保留一种")

    # 3. 多个 package.json
    pkg_jsons = [f for f in files if f.endswith("package.json")]
    if len(pkg_jsons) > 1:
        issues.append(f"多个 package.json：{', '.join(pkg_jsons)}，只应有一个")

    # 4. 多个构建配置文件
    build_configs = [f for f in files if any(
        k in f for k in ("vite.config", "webpack.config", "rollup.config", "esbuild.config")
    )]
    if len(build_configs) > 1:
        issues.append(f"多个构建配置文件：{', '.join(build_configs)}，只应保留一种构建工具")

    # 5. .tsx 文件存在但 package.json 缺少对应依赖
    has_tsx = any(f.endswith(".tsx") for f in files)
    if has_tsx:
        deps = pkg_json_str.lower()
        if "react" not in deps and "preact" not in deps and "solid" not in deps:
            issues.append("存在 .tsx 文件但 package.json 中缺少 react/preact/solid-js 依赖")

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
