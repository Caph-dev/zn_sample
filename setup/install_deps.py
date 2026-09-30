#!/usr/bin/env python3
"""zn_sample 依赖统一安装 / 验收（Windows 与 macOS 共用一份）。

入口（不要用系统 Python 直接跑本文件）：
  Windows  双击  setup\\安装依赖.bat
  macOS    zsh setup/install_deps.sh

两个入口都先确保 uv 可用，再用 uv 托管的 Python 3.12 运行本脚本；本脚本只用
标准库，不依赖系统 Python，也不依赖项目依赖是否已经装好。

「不同设备依赖统一」的口径固定在下面常量里，两个平台共用同一份文件：
  PINNED_PYTHON       项目 .venv 用 uv 托管的 Python 3.12，不跟随系统 Python
                       （Python 依赖严格按 uv.lock 安装，即 uv sync --frozen）
  PINNED_ZINIAO_CLI   全局 ziniao-cli 固定版本，避免各机器上自装最新版漂移
  NODE_ENGINE_RANGES  vite 7 的 engines 约束；Node 本体按《快速开始》用官网
                       LTS 安装包，本脚本只校验不代装

本脚本遵守仓库纪律，不做下面这些事：
  - 不改 PATH，不装 WebDriver，不碰紫鸟 / TikTok 的任何配置
  - 不填写密钥：只把 config.toml.example / .env.example 复制成正式文件
  - 不执行任何平台或飞书写操作，只安装依赖并做只读验收
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 与当前开发机一致的固定口径。升级依赖时改这里，并在两台机器上重新跑本脚本。
# ziniao-cli 版本要与开发机 `ziniao-cli --version` 输出一致（当前 1.0.7）。
PINNED_PYTHON = "3.12"
PINNED_ZINIAO_CLI = "1.0.7"

# vite 7 的 engines: "^20.19.0 || >=22.12.0"
NODE_ENGINE_RANGES = (
    ((20, 19, 0), (21, 0, 0)),
    ((22, 12, 0), None),
)

VENV_PYTHON = ROOT / (
    ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
)

BUILD_ARTIFACTS = (
    ROOT / "assistant/web/static/console/assets/index.js",
    ROOT / "assistant/web/static/auto-approval/assets/index.js",
)

FRONTEND_PACKAGES = ("react", "react-dom", "vite", "typescript")

# 官网 MSI 默认装到这两个位置；PATH 未热更新时也能找到。
NODE_FALLBACKS = (
    (Path("C:/Program Files/nodejs/node.exe"),) if sys.platform == "win32" else ()
)
NPM_FALLBACKS = (
    (Path("C:/Program Files/nodejs/npm.cmd"),) if sys.platform == "win32" else ()
)

IMPORT_CHECK_CODE = (
    "import fastapi, sqlalchemy, starlette, pydantic, uvicorn, httpx, alembic;"
    " print('fastapi', fastapi.__version__);"
    " print('sqlalchemy', sqlalchemy.__version__);"
    " print('starlette', starlette.__version__);"
    " print('pydantic', pydantic.__version__);"
    " print('uvicorn', uvicorn.__version__);"
    " print('httpx', httpx.__version__);"
    " print('alembic', alembic.__version__)"
)

FAILED_STEPS: list[str] = []


def configure_output_encoding() -> None:
    """Windows cmd 用 chcp 65001；这里让 Python 输出也固定 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def print_section(title: str) -> None:
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def fail(step: str, message: str) -> None:
    print(f"    [!] {message}")
    FAILED_STEPS.append(f"{step}：{message}")


def run_command(command, *, capture: bool = False) -> subprocess.CompletedProcess:
    """在仓库根目录执行命令；输出默认直接透传到终端。"""
    printable = " ".join(str(part) for part in command)
    print(f"    $ {printable}")
    return subprocess.run(
        [str(part) for part in command],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )


def find_tool(name: str, extra_candidates: tuple[Path, ...] = ()) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in extra_candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_uv(explicit_path: str | None) -> str | None:
    if explicit_path and Path(explicit_path).is_file():
        return explicit_path
    return find_tool(
        "uv",
        (
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".local" / "bin" / "uv.exe",
            Path("/opt/homebrew/bin/uv"),
            Path("/usr/local/bin/uv"),
            Path.home() / ".cargo" / "bin" / "uv",
        ),
    )


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def node_is_supported(version: tuple[int, int, int]) -> bool:
    for lower, upper in NODE_ENGINE_RANGES:
        if version >= lower and (upper is None or version < upper):
            return True
    return False


def query_global_cli_version(npm: str) -> str | None:
    result = run_command(
        [npm, "ls", "-g", "--depth=0", "--json", "@ziniao-open/cli"],
        capture=True,
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError:
        return None
    entry = (payload.get("dependencies") or {}).get("@ziniao-open/cli") or {}
    version = entry.get("version")
    return str(version) if version else None


def check_prerequisites(uv: str | None, npm: str | None, node: str | None) -> bool:
    print_section("0/6 前置检查")
    print(f"    仓库目录：{ROOT}")
    print(f"    操作系统：{sys.platform}")

    ready = True

    if uv is None:
        fail("前置检查", "找不到 uv。请重新双击安装入口，或手动安装：https://docs.astral.sh/uv/")
        ready = False
    else:
        result = run_command([uv, "--version"], capture=True)
        print(f"    uv：{(result.stdout or '').strip()}")

    for required_file in ("pyproject.toml", "uv.lock", "package.json", "package-lock.json"):
        if not (ROOT / required_file).is_file():
            fail("前置检查", f"仓库根目录缺少 {required_file}，请确认脚本在 zn_sample 仓库里")
            ready = False

    if node is None or npm is None:
        fail(
            "前置检查",
            "找不到 Node / npm。请先按《快速开始》第 2 节用官网 LTS 安装包装 Node"
            "（不要 nvm / 不要 Git Bash），关掉所有终端窗口再重新双击本文件。"
            "下载：https://nodejs.org/en/download",
        )
        return False

    result = run_command([node, "--version"], capture=True)
    node_version = parse_version(result.stdout or "")
    if node_version is None:
        fail("前置检查", "读不出 Node 版本，请重新安装官网 LTS 安装包")
        ready = False
    elif not node_is_supported(node_version):
        fail(
            "前置检查",
            f"Node {'.'.join(str(part) for part in node_version)} 不满足 vite 7 的 engines"
            "（^20.19.0 || >=22.12.0），请换官网 LTS 安装包",
        )
        ready = False
    else:
        print(f"    Node：v{'.'.join(str(part) for part in node_version)}（满足 vite 7）")

    if ready and node is not None:
        result = run_command([npm, "--version"], capture=True)
        print(f"    npm：{(result.stdout or '').strip()}")

    return ready


def install_python_dependencies(uv: str) -> None:
    print_section(f"1/6 Python 依赖（uv sync --frozen --dev，Python {PINNED_PYTHON}）")

    result = run_command([uv, "python", "install", PINNED_PYTHON])
    if result.returncode != 0:
        print("    [!] uv 托管 Python 安装失败，改用本机已有的 Python 3.12 继续")

    result = run_command(
        [uv, "sync", "--frozen", "--dev", "--python", PINNED_PYTHON]
    )
    if result.returncode != 0:
        fail(
            "Python 依赖",
            "uv sync --frozen 失败。若是 lockfile 与 pyproject.toml 不同步，"
            "请在开发机执行 uv lock 并提交 uv.lock，不要在本机改锁文件",
        )


def install_node_dependencies(npm: str) -> None:
    print_section("2/6 Node 依赖（npm ci，严格按 package-lock.json）")
    result = run_command([npm, "ci", "--no-audit", "--no-fund"])
    if result.returncode != 0:
        fail("Node 依赖", "npm ci 失败。先确认网络可访问 npm registry，再重新双击本文件")


def install_global_cli(npm: str) -> None:
    print_section(f"3/6 全局 ziniao-cli（固定 @ziniao-open/cli@{PINNED_ZINIAO_CLI}）")

    installed = query_global_cli_version(npm)
    if installed == PINNED_ZINIAO_CLI:
        print(f"    已是固定版本 {PINNED_ZINIAO_CLI}，跳过安装")
    else:
        if installed:
            print(f"    当前版本 {installed}，与固定版本不一致，重新安装")
        result = run_command(
            [
                npm,
                "install",
                "-g",
                f"@ziniao-open/cli@{PINNED_ZINIAO_CLI}",
                "--no-audit",
                "--no-fund",
            ]
        )
        if result.returncode != 0:
            fail(
                "全局 ziniao-cli",
                "npm install -g 失败。若报 EACCES/EPERM，请用官网安装包重装 Node，"
                "不要用 sudo npm，也不要手改 PATH",
            )
            return
        installed = query_global_cli_version(npm)
        if installed != PINNED_ZINIAO_CLI:
            fail("全局 ziniao-cli", f"安装后版本仍是 {installed}，请检查 npm 全局目录")
            return

    cli_on_path = find_tool("ziniao-cli")
    if cli_on_path is None:
        fail(
            "全局 ziniao-cli",
            "已装好但当前窗口找不到 ziniao-cli，关掉所有终端窗口重开一次；"
            "双击 .bat 仍找不到就注销或重启（资源管理器不热更新 PATH）",
        )
    else:
        print(f"    ziniao-cli：{cli_on_path}")


def build_frontends(npm: str, *, skip_build: bool) -> None:
    print_section("4/6 前端构建（npm run build:web）")

    if skip_build:
        print("    已按 --no-build 跳过；产物缺失时操作台网页会打不开")
        return

    result = run_command([npm, "run", "build:web"])
    if result.returncode != 0:
        fail("前端构建", "npm run build:web 失败。先修掉上面的 TypeScript / Vite 报错再重跑")


def copy_local_configs() -> None:
    print_section("5/6 本地配置（只复制示例，不代填密钥）")

    for example_name, target_name in (
        ("config.toml.example", "config.toml"),
        (".env.example", ".env"),
    ):
        example_path = ROOT / example_name
        target_path = ROOT / target_name
        if target_path.exists():
            print(f"    {target_name}：已存在，跳过（不会覆盖你填过的内容）")
            continue
        if not example_path.is_file():
            fail("本地配置", f"缺少 {example_name}，无法生成 {target_name}")
            continue
        shutil.copyfile(example_path, target_path)
        print(f"    {target_name}：已从 {example_name} 复制，待人工填写（见文末第 3 条）")

    print("    密钥只放这两个文件（已被 gitignore），不发聊天、不提交 git")


def verify_environment(
    uv: str,
    npm: str,
    node: str,
    *,
    skip_build: bool,
    with_tests: bool,
) -> None:
    print_section("6/6 验收（只读）")

    if not VENV_PYTHON.is_file():
        fail("验收", f"找不到虚拟环境 {VENV_PYTHON.relative_to(ROOT)}，请重新跑第 1 步")
    else:
        result = run_command([VENV_PYTHON, "--version"], capture=True)
        print(f"    项目 .venv Python：{(result.stdout or '').strip()}")
        result = run_command(
            [uv, "run", "--frozen", "python", "-c", IMPORT_CHECK_CODE],
            capture=True,
        )
        print((result.stdout or "").rstrip() or "    [!] 关键依赖导入失败")
        if result.returncode != 0:
            fail("验收", f"导入关键依赖失败：{(result.stderr or '').strip()[:400]}")

    result = run_command([node, "--version"], capture=True)
    node_version = (result.stdout or "").strip()
    result = run_command([npm, "--version"], capture=True)
    npm_version = (result.stdout or "").strip()
    print(f"    Node {node_version} / npm {npm_version}")

    result = run_command(
        [npm, "ls", "--depth=0", *FRONTEND_PACKAGES],
        capture=True,
    )
    print((result.stdout or "").rstrip())

    global_cli_version = query_global_cli_version(npm)
    print(f"    @ziniao-open/cli（全局）：{global_cli_version or '未找到'}")

    for artifact in BUILD_ARTIFACTS:
        relative = artifact.relative_to(ROOT)
        if artifact.is_file():
            print(f"    前端产物：{relative} 已就绪")
        elif skip_build:
            print(f"    前端产物：{relative} 缺失（被 --no-build 跳过）")
        else:
            fail("验收", f"前端产物缺失：{relative}")

    if with_tests:
        print()
        print("    跑本地离线回归（不批准、不写飞书、不发私信）：")
        result = run_command([uv, "run", "--frozen", "python", "-m", "pytest", "tests", "-q"])
        if result.returncode != 0:
            fail("验收", "pytest 未通过，先修测试再使用本机做正式操作")


def print_next_steps() -> None:
    print()
    print("=" * 68)
    print("接下来（需要人工完成，脚本不代做）")
    print("=" * 68)
    print("  1. 紫鸟开放平台 apiKey：ziniao-cli config init（选手动输入 Key；")
    print("     Key 不发聊天、不写仓库；不要新建应用）")
    print("  2. 本机终端识别码：紫鸟 GUI → 软件设置 → 本地设置 → 终端信息 复制，")
    print("     请管理员绑到 open.ziniao.com 里这把 apiKey 对应的应用（换电脑要重绑）")
    print("  3. 填两个文件：config.toml（飞书两个 App Secret）、.env（LLM_API_KEY）")
    print("  4. 紫鸟 GUI 登录，工作台只开一家店")
    print("  5. 验收紫鸟链路：ziniao-cli doctor、ziniao-cli store list --format table")
    print("  6. 双击「0-打开店铺」→ 店铺窗口登录 TikTok Shop → 双击「1-只出名单」")
    print()
    print("  文档：快速开始.md（装机）、配置清单.md（交接）、README.md（日常）")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="安装 / 统一 zn_sample 的开发依赖（通常由安装入口脚本调用）",
    )
    parser.add_argument(
        "--uv",
        dest="uv_path",
        default=None,
        help="入口脚本已解析好的 uv 可执行文件路径",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="跳过 npm run build:web（前端产物已是最新时用）",
    )
    parser.add_argument(
        "--with-test",
        action="store_true",
        help="验收时额外跑 uv run python -m pytest tests -q",
    )
    return parser.parse_args()


def print_version_snapshot(uv: str, npm: str, node: str) -> None:
    print()
    print("本机版本快照（可粘贴比对，确认各设备一致）：")
    for command in (
        [uv, "--version"],
        [VENV_PYTHON, "--version"],
        [node, "--version"],
        [npm, "--version"],
    ):
        if command[0] is None or not Path(str(command[0])).is_file():
            continue
        result = run_command(command, capture=True)
        text = (result.stdout or "").strip() or (result.stderr or "").strip()
        print(f"    {text}")
    print(f"    @ziniao-open/cli {query_global_cli_version(npm) or '未找到'}")


def main() -> int:
    configure_output_encoding()
    arguments = parse_arguments()

    uv = resolve_uv(arguments.uv_path)
    node = find_tool("node", NODE_FALLBACKS)
    npm = find_tool("npm", NPM_FALLBACKS)

    if not check_prerequisites(uv, npm, node):
        print()
        print("前置条件不满足，未开始安装。修好后重新双击本文件即可。")
        return 1

    install_python_dependencies(uv)
    install_node_dependencies(npm)
    install_global_cli(npm)
    build_frontends(npm, skip_build=arguments.no_build)
    copy_local_configs()
    verify_environment(
        uv,
        npm,
        node,
        skip_build=arguments.no_build,
        with_tests=arguments.with_test,
    )
    print_version_snapshot(uv, npm, node)
    print_next_steps()

    print()
    if FAILED_STEPS:
        print("没有全部完成：")
        for item in FAILED_STEPS:
            print(f"  - {item}")
        print("修好后重新双击本文件；已完成的步骤会自动跳过或快速通过。")
        return 1

    print("依赖已按固定口径装好，可以直接进行下面的「接下来」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
