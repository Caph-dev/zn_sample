"""Explicit application payload policy; never copy a checkout or user state."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

if __package__:
    from .runtime_artifacts import RuntimeBlocked, get_target
else:
    from runtime_artifacts import RuntimeBlocked, get_target

APPLICATION_SCOPE = "browser-application-bundle"

# New executable modules must be reviewed and registered here, not discovered
# from git or copied recursively from a developer's checkout.
ASSISTANT_MODULES = (
    "__init__", "app", "bootstrap", "lifecycle", "paths", "settings",
    "api/__init__", "api/auto_approval", "api/dashboard", "api/exports",
    "api/followups", "api/health", "api/jobs", "api/shipments", "api/stores",
    "database/__init__", "database/engine", "database/models", "database/types",
    "database/migrations/env",
    "database/migrations/versions/0001_initial",
    "database/migrations/versions/0002_add_shipment_sync_metadata",
    "database/migrations/versions/0003_add_sample_case_bio",
    "database/migrations/versions/0004_followup_status_model",
    "database/migrations/versions/0005_auto_approval",
    "database/migrations/versions/0006_followup_previewed_at",
    "domain/__init__", "domain/content_thanks", "domain/followup_labels",
    "domain/followup_stage", "domain/message_templates", "domain/platform_status",
    "domain/policies", "domain/shipment_status", "domain/sku_images", "domain/timeutil",
    "jobs/__init__", "jobs/locks", "jobs/progress", "jobs/registry", "jobs/worker",
    "jobs/handlers/__init__", "jobs/handlers/auto_approval",
    "jobs/handlers/content_thanks", "jobs/handlers/creator_enrich",
    "jobs/handlers/daily_refresh", "jobs/handlers/environment_check",
    "jobs/handlers/followup_generate", "jobs/handlers/followup_send",
    "jobs/handlers/operator", "jobs/handlers/report_export", "jobs/handlers/shipment_sync",
    "security/__init__", "security/csrf", "security/secret_redaction",
    "services/auto_approval_reconciliation", "services/auto_approval_recovery",
    "services/auto_approval_service", "services/content_thanks_service",
    "services/creator_enrich_service", "services/export_service",
    "services/followup_service", "services/local_state_import",
    "services/order_backfill", "services/page_lock", "services/release_lifecycle",
    "services/shipment_service", "services/store_service",
    "web/__init__", "web/console_pages", "web/routes",
)
SCRIPT_ENTRIES = (
    "auto_approval", "check_creator_content_review", "launch_assistant", "launch_sample",
    "open_sample_store", "release_launcher", "screen_sample_requests",
    "send_followup_message", "send_sample_intro", "sync_shipped_tracking",
)
SCRIPT_LIBRARIES = (
    "__init__", "app_config", "app_log", "approve_dom", "auto_approval_reconciliation",
    "auto_approval_rules", "auto_approval_sku", "completed_content_dom", "console",
    "creator_api", "creator_detail", "creator_video_contract", "creator_video_review",
    "detect_lang", "export_util", "feishu_bitable", "feishu_hero", "filters",
    "im_api", "im_dom", "job_cancel", "message_templates", "network_observer",
    "operation_cancel", "operator_launch", "order_api", "order_backfill", "order_dom",
    "page_api", "parse_metrics", "run_summary", "sample_api", "sample_data_source",
    "sample_dom", "sample_navigation", "sample_write_api", "shipped_dom",
    "store_launcher", "sync_errors", "tiktok_creator_videos", "time_budget",
    "tracking_parse", "zclaw", "zclaw_cli",
)
BUSINESS_ATTACHMENT = "\u6837\u54c1\u7533\u8bf7\u7b5b\u67e5sop/\u56fe\u7247\u548c\u9644\u4ef6/2-\u67e5\u770b\u5230\u8d27+\u8fbe\u4eba\u8ddf\u8fdb-b05.png"
FIXED_RESOURCES = (
    *(f"assistant/{module}.py" for module in ASSISTANT_MODULES),
    *(f"scripts/{module}.py" for module in SCRIPT_ENTRIES),
    *(f"scripts/lib/{module}.py" for module in SCRIPT_LIBRARIES),
    "assistant/database/alembic.ini", "assistant/database/migrations/script.py.mako",
    "assistant/web/templates/console.html", "assistant/web/templates/auto_approval.html",
    "assistant/web/templates/_task_panel.html", "assistant/web/templates/_job_status.html",
    "assistant/web/templates/_ui.html", "assistant/web/static/app.js",
    "assistant/web/static/console-shell.css", "assistant/web/static/geist-theme.css",
    "assistant/web/static/fonts/Geist.woff2", "assistant/web/static/fonts/GeistMono.woff2",
    "assistant/web/static/sop-images/2-\u67e5\u770b\u5230\u8d27+\u8fbe\u4eba\u8ddf\u8fdb-b05.png",
    "setup/install_deps.py", "setup/release/runtime_artifacts.py", "setup/release/resources.py",
    "setup/release/import_legacy_state.py", BUSINESS_ATTACHMENT,
)
FRONTEND_BUILDS = {
    "assistant/web/static/console": "console.html",
    "assistant/web/static/auto-approval": "index.html",
}
FRONTEND_ASSET_SUFFIXES = frozenset({".js", ".css", ".woff2", ".png", ".svg"})
CONFIGURATION_TEMPLATE = """# Technician: fill in the user's configuration directory, not app/.
# Empty stores are deliberate. Release startup must reject missing store IDs.
[stores]
default_store_id = ""
default_store_name = ""
prepare_store_id = ""
prepare_store_name = ""

[feishu]
app_id = ""
app_secret = ""
hero_url = ""
sheet = ""

[feishu.bitable]
app_id = ""
app_secret = ""
app_token = ""
table_id = ""
view_id = ""

[content_review]
enabled = true
tikhub_api_key = ""
ark_api_key = ""
ark_model = "doubao-seed-2-1-pro-260628"
max_visual_videos = 5
max_pages = 10
max_videos = 200
max_frames = 8
max_media_bytes = 33554432
run_timeout_seconds = 600
"""
ENVIRONMENT_TEMPLATE = """# Technician: configure the user's config/.env; never put secrets in app/.
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=
LLM_MODEL_ID=deepseek-v4-flash
CONTENT_REVIEW_ENABLED=true
TIKHUB_API_KEY=
ARK_API_KEY=
ARK_MODEL=doubao-seed-2-1-pro-260628
"""
LAUNCHER_MODES = {
    "Start": "start", "Stop": "stop", "Diagnose": "diagnose",
    "0-Prepare": "operator prepare", "1-Screen": "operator screen",
    "2-Pipeline": "operator pipeline", "3-Tracking": "operator tracking",
}


def require_regular_resource(source_root: Path, relative_path: str) -> Path:
    """Reject all application symlinks, including ancestors and in-root links."""
    parts = PurePosixPath(relative_path)
    if (
        parts.is_absolute() or PureWindowsPath(relative_path).drive
        or ".." in parts.parts or "\\" in relative_path or source_root.is_symlink()
    ):
        raise RuntimeBlocked("application-resource-path-invalid")
    candidate = source_root
    for component in parts.parts:
        candidate = candidate / component
        if candidate.is_symlink():
            raise RuntimeBlocked("application-resource-symlink")
    if not candidate.is_file() or (candidate.suffix != ".py" and candidate.stat().st_size == 0):
        raise RuntimeBlocked(f"application-resource-required: {relative_path}")
    if not candidate.resolve().is_relative_to(source_root.resolve()):
        raise RuntimeBlocked("application-resource-escape")
    return candidate


def collect_application_resources(source_root: Path) -> tuple[str, ...]:
    resources = list(FIXED_RESOURCES)
    for relative_directory, document_name in FRONTEND_BUILDS.items():
        resources.extend(
            f"{relative_directory}/{required}"
            for required in (document_name, "assets/index.js", "assets/index.css")
        )
        assets_directory = source_root / relative_directory / "assets"
        if assets_directory.is_symlink():
            raise RuntimeBlocked("application-resource-symlink")
        if assets_directory.exists():
            for asset_path in sorted(assets_directory.rglob("*")):
                if asset_path.is_symlink():
                    raise RuntimeBlocked("application-resource-symlink")
                if asset_path.is_file():
                    relative_asset = asset_path.relative_to(assets_directory)
                    if (
                        asset_path.suffix not in FRONTEND_ASSET_SUFFIXES
                        or any(part.startswith(".") for part in relative_asset.parts)
                    ):
                        raise RuntimeBlocked("unexpected-frontend-build-resource")
                    resources.append(asset_path.relative_to(source_root).as_posix())
    unique_resources = tuple(dict.fromkeys(resources))
    for relative_path in unique_resources:
        require_regular_resource(source_root, relative_path)
    validate_theme_order(source_root)
    return unique_resources


def validate_theme_order(source_root: Path) -> None:
    for page_name, build_name in (("console", "console"), ("auto_approval", "auto-approval")):
        template_path = require_regular_resource(
            source_root, f"assistant/web/templates/{page_name}.html"
        )
        template = template_path.read_text(encoding="utf-8")
        component_position = template.find(f"/static/{build_name}/assets/index.css")
        theme_position = template.find("/static/geist-theme.css")
        if component_position < 0 or theme_position <= component_position:
            raise RuntimeBlocked("application-theme-order-invalid")


def copy_application_resources(source_root: Path, bundle_root: Path) -> tuple[str, ...]:
    if bundle_root.is_symlink() or bundle_root.resolve() != bundle_root.absolute():
        raise RuntimeBlocked("application-destination-symlink")
    selected_resources = collect_application_resources(source_root)
    application_root = bundle_root / "app"
    if application_root.exists() or application_root.is_symlink():
        raise RuntimeBlocked("application-destination-must-be-new")
    for relative_path in selected_resources:
        destination = application_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(require_regular_resource(source_root, relative_path), destination)
        destination.chmod(0o644)
    # Generate independent templates: the source examples contain real company IDs.
    (application_root / "config.toml.example").write_text(CONFIGURATION_TEMPLATE, encoding="utf-8")
    (application_root / ".env.example").write_text(ENVIRONMENT_TEMPLATE, encoding="utf-8")
    return selected_resources


def write_platform_launchers(source_root: Path, bundle_root: Path, target_name: str, python_executable: str) -> None:
    if target_name not in {"macos-arm64", "windows-x64"}:
        raise RuntimeBlocked("unsupported-launcher-target")
    if python_executable != get_target(target_name)["python_executable"]:
        raise RuntimeBlocked("launcher-python-path-invalid")
    if bundle_root.is_symlink() or bundle_root.resolve() != bundle_root.absolute():
        raise RuntimeBlocked("launcher-destination-symlink")
    template_name = "macos.command.in" if target_name == "macos-arm64" else "windows.cmd.in"
    template_path = require_regular_resource(source_root, f"setup/release/launchers/{template_name}")
    template = template_path.read_text(encoding="utf-8")
    executable_parts = PurePosixPath(python_executable)
    if (
        executable_parts.is_absolute() or PureWindowsPath(python_executable).drive
        or ".." in executable_parts.parts or "\\" in python_executable
    ):
        raise RuntimeBlocked("launcher-python-path-invalid")
    windows_target = target_name == "windows-x64"
    executable_text = python_executable.replace("/", "\\") if windows_target else python_executable
    extension = ".cmd" if windows_target else ".command"
    for launcher_name, arguments in LAUNCHER_MODES.items():
        content = template.replace("@PYTHON_EXECUTABLE@", executable_text).replace("@MODE@", arguments)
        destination = bundle_root / (launcher_name + extension)
        destination.write_bytes(content.replace("\n", "\r\n").encode("utf-8") if windows_target else content.encode("utf-8"))
        destination.chmod(0o644 if windows_target else 0o755)
