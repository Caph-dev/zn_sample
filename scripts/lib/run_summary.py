"""把启动提示、确认和收尾收成一块 ASCII 框，避免和中间日志糊在一起。"""
from __future__ import annotations

import unicodedata
from pathlib import Path


def visual_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def pad_visual(text: str, width: int) -> str:
    extra = max(0, width - visual_width(text))
    return text + (" " * extra)


def display_path(path: Path, *, root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def format_ascii_box(lines: list[str], *, min_inner: int = 46) -> str:
    inner = max([min_inner, *(visual_width(line) for line in lines)])
    edge = "+" + ("-" * (inner + 2)) + "+"
    body = [f"| {pad_visual(line, inner)} |" for line in lines]
    return "\n".join([edge, *body, edge])


START_BANNER_EXTRA = {
    "prepare": "会关掉已开的店再打开（带调试口）。没有开着的店就默认打开 2 号店。不会筛查、不会批准。",
    "screen": "只会出名单。不会批准、不会发私信。",
    "pipeline": "接下来请输入 y 或 n。输入 y 才会筛查、批准、写飞书并发介绍私信。",
    "tracking": "接下来请输入 y 或 n。输入 y 才会获取物流、写飞书并发送单号。",
}

START_BANNER_FOOTER = {
    "prepare": (
        "请不要关闭这个窗口。",
        "店铺窗口可能会关掉再开，这是正常的。",
    ),
}


def format_start_banner(mode_key: str, title: str) -> str:
    extra = START_BANNER_EXTRA.get(mode_key, "")
    lines = [f"「{title}」"]
    if extra:
        lines.append(extra)
    footer = START_BANNER_FOOTER.get(
        mode_key,
        (
            "请不要关闭这个窗口。",
            "紫鸟店铺窗口也请留着。",
        ),
    )
    lines.append("")
    lines.extend(footer)
    return format_ascii_box(lines)


def format_confirm_prompt(title: str, message: str, *, no_means: str) -> str:
    lines = [f"请确认：「{title}」", ""]
    lines.extend(message.splitlines() or [""])
    lines.extend(
        [
            "",
            "输入 y 然后回车 = 继续",
            f"输入 n 或直接回车 = {no_means}",
        ]
    )
    return format_ascii_box(lines)


def format_force_afternoon_prompt(clock: str) -> str:
    return format_ascii_box(
        [
            "还不到北京时间 16:00",
            "",
            f"现在北京时间 {clock}。SOP 规定物流要下午 4 点后再跑。",
            "不建议现在强制运行：单号可能还没出来，也容易发错。",
            "",
            "输入 FORCE 然后回车 = 仍要强制运行（不推荐）",
            "直接回车 = 退出，等到 4 点再双击（推荐）",
        ]
    )


def format_force_afternoon_aborted(clock: str) -> str:
    return format_ascii_box(
        [
            "「获取物流信息写飞书发单号」没有继续",
            "",
            "原因：没有输入 FORCE。",
            f"现在北京时间 {clock}，还不到 16:00。",
            "",
            "没有读物流，没有写飞书，也没有发单号。",
            "这是推荐做法。请下午 4 点后再双击。",
        ]
    )


def format_confirm_aborted(title: str, *, idle_message: str, not_this: str) -> str:
    return format_ascii_box(
        [
            f"「{title}」没有继续",
            "",
            "原因：没有输入 y。",
            "输入了 n、其它字符，或直接回车，都会退出。",
            "",
            idle_message,
            not_this,
        ]
    )


def format_job_summary(
    *,
    title: str,
    stats: list[str],
    csv_path: Path | None,
    json_path: Path | None,
    xlsx_path: Path | None,
    root: Path,
    hint: str,
) -> str:
    lines = [title, ""]
    lines.extend(stats)
    lines.extend(["", "报表"])
    if csv_path is not None:
        lines.append(f"CSV    : {display_path(csv_path, root=root)}")
    if json_path is not None:
        lines.append(f"JSON   : {display_path(json_path, root=root)}")
    if xlsx_path is not None:
        lines.append(f"Excel  : {display_path(xlsx_path, root=root)}")
    if csv_path is None and json_path is None and xlsx_path is None:
        lines.append("（未生成文件）")
    if hint:
        lines.extend(["", hint])
    return format_ascii_box(lines)


def format_platform_wait_banner() -> str:
    return format_ascii_box(
        [
            "等待 TikTok 商家中心刷新",
            "",
            "刚点过「同意」后，平台「待发货」大约 10 分钟才会出现。",
            "这是商家中心平台侧的延迟，不是脚本卡住，也不是电脑死机。",
            "请不要关闭这个窗口，紫鸟店铺窗口也请留着。",
        ]
    )


def format_wait_progress(
    elapsed_sec: int,
    total_sec: int,
    *,
    bar_width: int = 24,
) -> str:
    total = max(1, int(total_sec))
    elapsed = max(0, min(int(elapsed_sec), total))
    filled = int(bar_width * elapsed / total)
    bar = "#" * filled + "-" * (bar_width - filled)
    return (
        f"已等待 {elapsed // 60:02d}:{elapsed % 60:02d} / "
        f"{total // 60:02d}:{total % 60:02d}  [{bar}]"
    )
