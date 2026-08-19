"""业务员双击入口：预检、确认、再调用对应脚本。

不传 --store-id / --store-name。写操作在同一窗口输入 y/n，不弹系统对话框。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]

from .run_summary import (
    format_confirm_aborted,
    format_confirm_prompt,
    format_force_afternoon_aborted,
    format_force_afternoon_prompt,
    format_platform_wait_banner,
    format_start_banner,
    format_wait_progress,
)
from .sample_navigation import INSPECT_NAVIGATION_PAGE_JS, validate_navigation_start
from .zclaw import list_running_stores, zclaw_exec
from .zclaw_cli import CLI_NOT_FOUND, fs_path, resolve_ziniao_cli_command

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
OPEN_SCRIPT = ROOT / "scripts" / "open_sample_store.py"
SCREEN_SCRIPT = ROOT / "scripts" / "screen_sample_requests.py"
INTRO_SCRIPT = ROOT / "scripts" / "send_sample_intro.py"
TRACKING_SCRIPT = ROOT / "scripts" / "sync_shipped_tracking.py"
ZINIAO_STATUS = ROOT / "scripts" / "ziniao-status.sh"
YES_ANSWERS = {"y", "yes"}
OPERATOR_EXECUTE_LIMIT = 20
PLATFORM_WAIT_SECONDS = 10 * 60
try:
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo unavailable")
    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:
    # Windows 未装 tzdata 时 ZoneInfo 不可用；中国无夏令时，固定 UTC+8 即可。
    BEIJING = timezone(timedelta(hours=8))
CLICKABLE_NAMES = (
    "0-打开店铺",
    "1-只出名单",
    "2-筛查批准写飞书发私信",
    "3-获取物流信息写飞书发单号",
)

AskFn = Callable[[str, str, str], str | None]
RunnerFn = Callable[..., subprocess.CompletedProcess]


class OperatorLaunchError(RuntimeError):
    """给业务员看的中文失败，不要往上抛内部术语。"""


@dataclass(frozen=True)
class LaunchMode:
    key: str
    title: str
    script: Path
    extra_args: tuple[str, ...]
    confirm: str  # none | yesno
    confirm_message: str
    abort_idle: str
    abort_not_this: str
    start_message: str
    report_stem: str
    needs_afternoon: bool = False
    is_pipeline: bool = False
    require_running_store: bool = True
    inspect_page: bool = True
    writes_report: bool = True


def _limit_args() -> tuple[str, ...]:
    return ("--execute-limit", str(OPERATOR_EXECUTE_LIMIT))


SCREEN_EXTRA = (
    "--from-seller-home",
    "--data-source",
    "auto",
    "--with-detail",
    "--require-detail",
)
APPROVE_EXTRA = (
    "--from-seller-home",
    "--data-source",
    "auto",
    "--execute",
    "--yes",
    "--write-feishu",
    *_limit_args(),
)
CONFIRM_EXTRA = (
    "--from-seller-home",
    "--data-source",
    "auto",
    "--confirm-export",
    "--write-feishu",
    *_limit_args(),
)
INTRO_EXTRA = ("--execute", "--yes", "--write-feishu", *_limit_args())


MODES: dict[str, LaunchMode] = {
    "prepare": LaunchMode(
        key="prepare",
        title="打开店铺",
        script=OPEN_SCRIPT,
        extra_args=("--reopen",),
        confirm="none",
        confirm_message="",
        abort_idle="",
        abort_not_this="",
        start_message=(
            "开始打开店铺（带调试口）。"
            "若店已经开着，会先关掉再开；这是正常的。"
            "若一家都没开，默认打开 2 号店。"
            "打开后请在店铺窗口登录商家中心（不必停在首页）。"
        ),
        report_stem="sample_open",
        require_running_store=False,
        inspect_page=False,
        writes_report=False,
    ),
    "screen": LaunchMode(
        key="screen",
        title="只出名单",
        script=SCREEN_SCRIPT,
        extra_args=SCREEN_EXTRA,
        confirm="none",
        confirm_message="",
        abort_idle="",
        abort_not_this="",
        start_message="开始筛查，只会出名单，不会点同意，也不会发私信。",
        report_stem="sample_screen",
    ),
    "pipeline": LaunchMode(
        key="pipeline",
        title="筛查批准写飞书发私信",
        script=SCREEN_SCRIPT,
        extra_args=(),
        confirm="yesno",
        confirm_message=(
            "将连续做完：出名单 → 批准并写飞书 → 空等 10 分钟 → "
            "核对待发货并补写 → 发介绍私信，并写回「使用语言」。\n"
            f"本轮最多批准 / 发信 {OPERATOR_EXECUTE_LIMIT} 条。\n"
            "中间必须空等 10 分钟：TikTok 商家中心的「待发货」刷新慢，"
            "是平台侧问题，不是脚本卡住。\n"
            "请确认：只开了一家店，店铺窗口已登录商家中心（不必停在首页）。\n"
            "平台同意和已发私信无法用脚本撤销。"
        ),
        abort_idle="没有批准、没有写飞书、也没有发介绍私信。",
        abort_not_this="这不是「名单里没有可批准的人」。",
        start_message="开始筛查、批准、写飞书并发介绍私信。请看着店铺窗口，不要自己点页面。",
        report_stem="sample_screen",
        is_pipeline=True,
    ),
    "tracking": LaunchMode(
        key="tracking",
        title="获取物流信息写飞书发单号",
        script=TRACKING_SCRIPT,
        extra_args=(
            "--write-feishu",
            "--send-tracking",
            "--execute",
            "--yes",
            "--execute-limit",
            "0",
        ),
        confirm="yesno",
        confirm_message=(
            "将读取「已发货」全部记录，把 TikTok 物流单号写回飞书，"
            "并向对应达人发送物流单号私信。\n"
            "本轮不限条数。发出后无法用脚本撤回。\n"
            "请确认：只开了一家店。"
        ),
        abort_idle="没有写飞书，也没有发送物流单号。",
        abort_not_this="这不是「已发货列表是空的」。",
        start_message="开始获取物流、写飞书并发送单号。请看着店铺窗口，不要自己点页面。",
        report_stem="sample_shipped",
        needs_afternoon=True,
    ),
}


def clickable_hint() -> str:
    *rest, last = CLICKABLE_NAMES
    return f"请双击仓库里的「{'」「'.join(rest)}」或「{last}」。"


def parse_mode(name: str | None) -> LaunchMode:
    key = (name or "").strip().lower()
    if key not in MODES:
        raise OperatorLaunchError(f"内部参数不对。{clickable_hint()}")
    return MODES[key]


def default_out_prefix(mode: LaunchMode | str, *, now: datetime | None = None) -> Path:
    spec = parse_mode(mode) if isinstance(mode, str) else mode
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return ROOT / "exports" / f"{spec.report_stem}_{stamp}"


def pipeline_prefixes(*, now: datetime | None = None, base: Path | None = None) -> dict[str, Path]:
    if base is not None:
        return {
            "screen": base.parent / f"{base.name}_screen",
            "approve": base.parent / f"{base.name}_approved",
            "confirm": base.parent / f"{base.name}_confirm",
            "intro": base.parent / f"{base.name}_intro",
        }
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    exports = ROOT / "exports"
    return {
        "screen": exports / f"sample_screen_{stamp}",
        "approve": exports / f"sample_screen_{stamp}_approved",
        "confirm": exports / f"sample_screen_{stamp}_confirm",
        "intro": exports / f"sample_intro_{stamp}",
    }


def build_step_argv(
    *,
    python: str,
    script: Path,
    extra_args: Sequence[str],
    out_prefix: Path | None = None,
    from_export: Path | None = None,
    writes_report: bool = True,
) -> list[str]:
    argv = [python, fs_path(script), *extra_args]
    if from_export is not None:
        argv.extend(["--from-export", fs_path(from_export)])
    if writes_report:
        if out_prefix is None:
            raise OperatorLaunchError("内部参数不对：缺少报表路径。")
        argv.extend(["--out", fs_path(out_prefix)])
    if "--store-id" in argv or "--store-name" in argv:
        raise OperatorLaunchError("入口不允许指定店铺编号。")
    return argv


def build_job_argv(
    mode: LaunchMode | str,
    *,
    python: str,
    out_prefix: Path,
    from_export: Path | None = None,
    force: bool = False,
) -> list[str]:
    spec = parse_mode(mode) if isinstance(mode, str) else mode
    if spec.is_pipeline:
        raise OperatorLaunchError("筛查批准写飞书发私信不是单步命令。")
    argv = build_step_argv(
        python=python,
        script=spec.script,
        extra_args=spec.extra_args,
        out_prefix=out_prefix,
        from_export=from_export,
        writes_report=spec.writes_report,
    )
    if force:
        if spec.key != "tracking":
            raise OperatorLaunchError("只有写物流才会询问是否强制绕过 16:00。")
        argv.append("--force")
    return argv


def count_export_approved(path: Path) -> int:
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else (data.get("rows") or data.get("items") or [])
    if not isinstance(rows, list):
        return 0
    return sum(
        1
        for row in rows
        if isinstance(row, dict) and row.get("approve_status") == "approved"
    )


def parse_ziniao_status(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("mode:"):
            return stripped.split(":", 1)[1].strip() or "UNKNOWN"
    return "UNKNOWN"


def require_macos_gui(mode: str) -> None:
    if mode == "GUI":
        return
    if mode == "OFF":
        raise OperatorLaunchError(
            "紫鸟没打开。请先打开「应用程序」里的紫鸟并登录，工作台只开一家店。"
        )
    if mode == "WEBDRIVER":
        raise OperatorLaunchError(
            "紫鸟现在没有工作台窗口。请先完全退出紫鸟，再从「应用程序」打开紫鸟。"
        )
    raise OperatorLaunchError("无法判断紫鸟是否已打开。请先打开紫鸟工作台后再双击。")


def read_macos_ziniao_mode(*, runner: RunnerFn | None = None) -> str:
    run = runner or subprocess.run
    proc = run(
        ["zsh", str(ZINIAO_STATUS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return parse_ziniao_status(proc.stdout or "")


def store_label(store: dict[str, Any]) -> str:
    name = str(store.get("storeName") or store.get("name") or "").strip()
    return name or "未命名店铺"


def require_single_running_store(
    list_running_stores_fn: Callable[[], list[dict[str, Any]]] | None = None,
    *,
    resolve_cli_fn: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    if list_running_stores_fn is None:
        try:
            (resolve_cli_fn or resolve_ziniao_cli_command)()
        except RuntimeError as exc:
            raise OperatorLaunchError(_humanize_bridge_error(exc)) from exc
        lister = list_running_stores
    else:
        lister = list_running_stores_fn
    try:
        running = [row for row in lister() if str(row.get("storeId") or "").strip()]
    except Exception as exc:
        raise OperatorLaunchError(_humanize_bridge_error(exc)) from exc

    if not running:
        raise OperatorLaunchError(
            "工作台里还没有打开的店。请只打开一家店，在店铺窗口登录 TikTok Shop。"
        )
    if len(running) > 1:
        names = "、".join(store_label(row) for row in running)
        raise OperatorLaunchError(f"工作台开了多家店（{names}）。请关掉其它店，只留一家。")
    return running[0]


def precheck_shop_page(
    store_id: str,
    *,
    execute_script_fn: Callable[..., Any] | None = None,
) -> str:
    exec_fn = execute_script_fn or zclaw_exec
    try:
        state = exec_fn(store_id, INSPECT_NAVIGATION_PAGE_JS, timeout=30)
    except Exception as exc:
        raise OperatorLaunchError(_humanize_bridge_error(exc)) from exc
    if not isinstance(state, dict):
        raise OperatorLaunchError("看不了店铺窗口当前页面。请确认紫鸟已登录、店窗还开着。")
    try:
        return validate_navigation_start(state)
    except RuntimeError as exc:
        raise OperatorLaunchError(_humanize_page_error(str(exc))) from exc


def precheck(
    *,
    platform: str | None = None,
    list_running_stores_fn: Callable[[], list[dict[str, Any]]] | None = None,
    inspect_fn: Callable[[str], str] | None = None,
    status_fn: Callable[[], str] | None = None,
    require_running_store: bool = True,
    inspect_page: bool = True,
) -> dict[str, Any]:
    host = sys.platform if platform is None else platform
    if host == "darwin":
        require_macos_gui((status_fn or read_macos_ziniao_mode)())

    if require_running_store:
        store = require_single_running_store(list_running_stores_fn)
        store_id = str(store["storeId"]).strip()
        page_type = (
            (inspect_fn or (lambda sid: precheck_shop_page(sid)))(store_id)
            if inspect_page
            else ""
        )
        return {"store": store, "page_type": page_type}

    if list_running_stores_fn is None:
        try:
            resolve_ziniao_cli_command()
        except RuntimeError as exc:
            raise OperatorLaunchError(_humanize_bridge_error(exc)) from exc
        lister = list_running_stores
    else:
        lister = list_running_stores_fn
    try:
        running = [row for row in lister() if str(row.get("storeId") or "").strip()]
    except Exception as exc:
        raise OperatorLaunchError(_humanize_bridge_error(exc)) from exc
    if len(running) > 1:
        names = "、".join(store_label(row) for row in running)
        raise OperatorLaunchError(f"工作台开了多家店（{names}）。请关掉其它店，只留一家。")
    if not running:
        return {"store": {}, "page_type": ""}
    store = running[0]
    store_id = str(store["storeId"]).strip()
    page_type = (
        (inspect_fn or (lambda sid: precheck_shop_page(sid)))(store_id)
        if inspect_page
        else ""
    )
    return {"store": store, "page_type": page_type}


def before_four_pm_beijing(now: datetime | None = None) -> bool:
    current = now or datetime.now(BEIJING)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING)
    return current.astimezone(BEIJING).hour < 16


def require_afternoon(*, now: datetime | None = None) -> None:
    if not before_four_pm_beijing(now):
        return
    clock = (now or datetime.now(BEIJING)).astimezone(BEIJING).strftime("%H:%M")
    raise OperatorLaunchError(
        f"现在北京时间 {clock}，还不到 16:00。物流请下午 4 点后再双击「3-获取物流信息写飞书发单号」。"
    )


def wait_platform_refresh(
    *,
    seconds: int = PLATFORM_WAIT_SECONDS,
    sleep_fn: Callable[[float], None] | None = None,
    progress_fn: Callable[[str], None] | None = None,
    clock_fn: Callable[[], float] | None = None,
) -> None:
    sleeper = sleep_fn or time.sleep
    clock = clock_fn or time.monotonic
    write = progress_fn or _print_progress_line
    started = clock()
    while True:
        elapsed = int(clock() - started)
        if elapsed > seconds:
            elapsed = seconds
        write(format_wait_progress(elapsed, seconds))
        if elapsed >= seconds:
            write("")
            return
        sleeper(1)


def _print_progress_line(line: str) -> None:
    if line == "":
        sys.stdout.write("\n")
    else:
        sys.stdout.write("\r" + line)
    sys.stdout.flush()


def choose_report(out_prefix: Path) -> Path | None:
    csv_path = out_prefix.with_suffix(".csv")
    if csv_path.is_file():
        return csv_path
    xlsx = out_prefix.with_suffix(".xlsx")
    if xlsx.is_file():
        return xlsx
    return None


def reveal_report(path: Path, *, opener: Callable[[Path], None] | None = None) -> None:
    if opener is not None:
        opener(path)
        return
    try:
        if sys.platform == "darwin":
            revealed = subprocess.run(
                ["open", "-R", str(path)],
                check=False,
                capture_output=True,
            )
            if revealed.returncode != 0:
                subprocess.run(
                    ["open", str(path.parent)],
                    check=False,
                    capture_output=True,
                )
            return
        if sys.platform == "win32":
            subprocess.run(
                ["explorer", "/select,", str(path.resolve())],
                check=False,
                capture_output=True,
            )
            return
        subprocess.run(
            ["xdg-open", str(path)],
            check=False,
            capture_output=True,
        )
    except Exception:
        return


def parse_yes_no(answer: str) -> bool:
    return str(answer or "").strip().lower() in YES_ANSWERS


def parse_force(answer: str) -> bool:
    return str(answer or "").strip() == "FORCE"


def beijing_clock(now: datetime | None = None) -> str:
    current = now or datetime.now(BEIJING)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING)
    return current.astimezone(BEIJING).strftime("%H:%M")


def ask_force_afternoon(
    clock: str,
    *,
    ask: AskFn | None = None,
) -> bool:
    title = "强制在 16:00 前运行"
    message = f"现在北京时间 {clock}。"
    if ask is not None:
        return parse_force(str(ask("force", title, message) or ""))
    _require_tty("强制运行")
    logger.info("%s", format_force_afternoon_prompt(clock))
    answer = input("请输入 FORCE 强制运行（不推荐），或直接回车退出：")
    return parse_force(answer)


def ask_yes_no(
    title: str,
    message: str,
    *,
    no_means: str,
    ask: AskFn | None = None,
) -> bool:
    if ask is not None:
        return parse_yes_no(str(ask("yesno", title, message) or ""))
    return _stdin_yes_no(title, message, no_means=no_means)


def confirm_mode(
    mode: LaunchMode,
    *,
    ask: AskFn | None = None,
) -> bool:
    if mode.confirm == "none":
        return True
    if mode.confirm == "yesno":
        return ask_yes_no(
            mode.title,
            mode.confirm_message,
            no_means="退出，不改店铺、不写飞书、不发私信",
            ask=ask,
        )
    raise OperatorLaunchError("内部确认类型不对。")


def run_job(
    argv: Sequence[str],
    *,
    runner: RunnerFn | None = None,
) -> int:
    run = runner or subprocess.run
    proc = run(list(argv), cwd=fs_path(ROOT))
    return int(proc.returncode)


def run_operator_mode(
    mode_name: str,
    *,
    python: str | None = None,
    now: datetime | None = None,
    precheck_fn: Callable[[], dict[str, Any]] | None = None,
    confirm_fn: Callable[[LaunchMode], bool] | None = None,
    force_fn: Callable[[], bool] | None = None,
    run_job_fn: Callable[[Sequence[str]], int] | None = None,
    open_report_fn: Callable[[Path], None] | None = None,
    wait_fn: Callable[[], None] | None = None,
    printer: Callable[[str], None] | None = None,
    ask: AskFn | None = None,
    alert_fn: Callable[[str, str], None] | None = None,
    out_prefix: Path | None = None,
) -> int:
    emit = printer or (lambda line: logger.info("%s", line))
    # 说明只打在终端。系统弹窗在 macOS 上中文会乱码，而且和黑窗口重复。
    notify = alert_fn or (lambda _title, _msg: None)
    try:
        mode = parse_mode(mode_name)
    except OperatorLaunchError as exc:
        emit(str(exc))
        notify("还不能开始", str(exc))
        return 2
    emit(format_start_banner(mode.key, mode.title))

    try:
        if precheck_fn is not None:
            checked = precheck_fn()
        else:
            checked = precheck(
                require_running_store=mode.require_running_store,
                inspect_page=mode.inspect_page,
            )
    except OperatorLaunchError as exc:
        emit(f"还不能开始：{exc}")
        notify("还不能开始", str(exc))
        return 2
    except Exception as exc:
        text = _humanize_bridge_error(exc)
        emit(f"还不能开始：{text}")
        notify("还不能开始", text)
        return 2

    store = checked.get("store") or {}
    if str(store.get("storeId") or "").strip():
        emit(f"检查通过：当前打开的店是「{store_label(store)}」。")
    else:
        emit("检查通过：工作台目前没有打开的店，将打开 2 号店。")

    force = False
    if mode.needs_afternoon and before_four_pm_beijing(now):
        clock = beijing_clock(now)
        allowed_force = (
            force_fn or (lambda: ask_force_afternoon(clock, ask=ask))
        )()
        if not allowed_force:
            emit(format_force_afternoon_aborted(clock))
            return 0
        force = True
        emit(f"已输入 FORCE。现在北京时间 {clock}，将绕过 16:00 时间门（不推荐）。")

    if mode.confirm == "none":
        allowed = True
    else:
        allowed = (confirm_fn or (lambda spec: confirm_mode(spec, ask=ask)))(mode)
    if not allowed:
        emit(
            format_confirm_aborted(
                mode.title,
                idle_message=mode.abort_idle,
                not_this=mode.abort_not_this,
            )
        )
        return 0

    if mode.is_pipeline:
        return _run_pipeline(
            python=python or sys.executable,
            now=now,
            out_prefix=out_prefix,
            run_job_fn=run_job_fn or run_job,
            open_report_fn=open_report_fn,
            wait_fn=wait_fn,
            emit=emit,
            notify=notify,
        )

    emit(mode.start_message)

    prefix = out_prefix or default_out_prefix(mode, now=now)
    argv = build_job_argv(
        mode,
        python=python or sys.executable,
        out_prefix=prefix,
        force=force,
    )
    code = (run_job_fn or run_job)(argv)
    if code != 0:
        return _job_failed(emit, notify, code)
    if mode.writes_report:
        _open_or_hint_report(prefix, emit=emit, open_report_fn=open_report_fn)
    else:
        emit("店铺已打开。请在店铺窗口登录商家中心后，再双击「1-只出名单」。")
    return 0


def _run_pipeline(
    *,
    python: str,
    now: datetime | None,
    out_prefix: Path | None,
    run_job_fn: Callable[[Sequence[str]], int],
    open_report_fn: Callable[[Path], None] | None,
    wait_fn: Callable[[], None] | None,
    emit: Callable[[str], None],
    notify: Callable[[str, str], None],
) -> int:
    prefixes = pipeline_prefixes(now=now, base=out_prefix)
    emit("第 1 步：筛查名单。")
    screen_code = run_job_fn(
        build_step_argv(
            python=python,
            script=SCREEN_SCRIPT,
            extra_args=SCREEN_EXTRA,
            out_prefix=prefixes["screen"],
        )
    )
    if screen_code != 0:
        return _job_failed(emit, notify, screen_code)
    screen_json = prefixes["screen"].with_suffix(".json")
    if not screen_json.is_file():
        emit("筛查跑完了，但没找到名单文件。请到 exports 文件夹里看。")
        notify("没有跑完", "筛查跑完了，但没找到名单文件。")
        return 1

    emit("第 2 步：批准并通过飞书建档。")
    approve_code = run_job_fn(
        build_step_argv(
            python=python,
            script=SCREEN_SCRIPT,
            extra_args=APPROVE_EXTRA,
            out_prefix=prefixes["approve"],
            from_export=screen_json,
        )
    )
    if approve_code != 0:
        return _job_failed(emit, notify, approve_code)
    approve_json = prefixes["approve"].with_suffix(".json")
    approved = count_export_approved(approve_json)
    emit(f"这一轮批准成功 {approved} 条。")
    if approved <= 0:
        emit("没有批准成功的申请，不再等待，也不发介绍私信。")
        _open_or_hint_report(prefixes["screen"], emit=emit, open_report_fn=open_report_fn)
        return 0

    emit(format_platform_wait_banner())
    try:
        if wait_fn is not None:
            wait_fn()
        else:
            wait_platform_refresh(seconds=PLATFORM_WAIT_SECONDS)
    except KeyboardInterrupt:
        emit("等待被中断。没有补写飞书，也没有发介绍私信。")
        notify("等待被中断", "10 分钟等待被中断。没有补写飞书，也没有发介绍私信。")
        return 130
    emit("10 分钟已到。这 10 分钟是在等商家中心刷新，不是脚本在干活。")

    emit("第 3 步：核对待发货并补写飞书。")
    confirm_code = run_job_fn(
        build_step_argv(
            python=python,
            script=SCREEN_SCRIPT,
            extra_args=CONFIRM_EXTRA,
            out_prefix=prefixes["confirm"],
            from_export=approve_json if approve_json.is_file() else screen_json,
        )
    )
    if confirm_code != 0:
        return _job_failed(emit, notify, confirm_code)
    intro_source = prefixes["confirm"].with_suffix(".json")
    if not intro_source.is_file():
        intro_source = approve_json

    emit("第 4 步：发送介绍私信。")
    intro_code = run_job_fn(
        build_step_argv(
            python=python,
            script=INTRO_SCRIPT,
            extra_args=INTRO_EXTRA,
            out_prefix=prefixes["intro"],
            from_export=intro_source,
        )
    )
    if intro_code != 0:
        return _job_failed(emit, notify, intro_code)
    _open_or_hint_report(prefixes["screen"], emit=emit, open_report_fn=open_report_fn)
    return 0


def _job_failed(
    emit: Callable[[str], None],
    notify: Callable[[str, str], None],
    code: int,
) -> int:
    emit("没有跑完。请看上面的说明，或把窗口里的报错发给技术人员。")
    notify(
        "没有跑完",
        "没有跑完。请看上面的说明，或把窗口里的报错发给技术人员。",
    )
    return code


def _open_or_hint_report(
    prefix: Path,
    *,
    emit: Callable[[str], None],
    open_report_fn: Callable[[Path], None] | None,
) -> None:
    report = choose_report(prefix)
    if report is not None:
        try:
            reveal_report(report, opener=open_report_fn)
            emit(f"报表已生成：{report.name}。已在文件夹里标出，请打开 CSV。")
        except Exception as exc:
            emit(f"报表已生成，但自动打开失败：{exc}。请到 exports 文件夹里打开 CSV。")
        return
    emit("跑完了，但没找到报表。请到 exports 文件夹里看最新的表格。")


def _humanize_bridge_error(exc: BaseException) -> str:
    text = str(exc)
    lowered = text.lower()
    missing_cli = (
        CLI_NOT_FOUND in text
        or "找不到 ziniao-cli" in text
        or (
            "ziniao-cli" in lowered
            and (
                "not found" in lowered
                or "no such file" in lowered
                or "cannot find the file" in lowered
                or "winerror 2" in lowered
            )
        )
    )
    if missing_cli:
        return "这台电脑还没配完（找不到 ziniao-cli）。请按 README 里的检查步骤，或找技术人员。"
    if "WINDOWS_SHIM" in text or "run.js" in text:
        return "这台电脑还没配完（ziniao-cli 不完整）。请按 README 里的检查步骤，或找技术人员。"
    return "连不上紫鸟。请先打开紫鸟（要能看见工作台窗口）并登录，然后再双击。"


def _humanize_page_error(message: str) -> str:
    if "about:blank" in message:
        return "店铺窗口还是空白页。请先在这个窗口里登录 TikTok Shop。"
    if "登录页" in message or "login" in message.lower():
        return "店铺窗口还在登录页。请先完成登录（验证码要自己点）。"
    return "店铺窗口还没停在已登录的商家中心。请先登录 TikTok Shop 后再双击。"


def _require_tty(action: str) -> None:
    if not sys.stdin.isatty():
        raise OperatorLaunchError(f"{action}必须在这个窗口里输入 y 或 n，当前读不到键盘。")


def _stdin_yes_no(title: str, message: str, *, no_means: str) -> bool:
    _require_tty(title)
    logger.info("%s", format_confirm_prompt(title, message, no_means=no_means))
    answer = input("请输入 y 或 n，然后回车：")
    return parse_yes_no(answer)
