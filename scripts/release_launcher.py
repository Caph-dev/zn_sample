#!/usr/bin/env python3
"""Fixed release entry points; never restart, kill, or accept shell commands."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from assistant.lifecycle import InstanceLock, _is_process_alive
from assistant.paths import (
    ReleasePathError, _release_manifest, assert_private_interpreter, bundled_tool_path,
    configuration_dir, database_path, ensure_user_dirs, python_subprocess_environment,
    runtime_dir, user_data_dir,
)
from assistant.services.release_lifecycle import (
    ReleaseLifecycleError, SubprocessRegistry, check_residual_jobs, read_private_json,
    run_release_server, write_private_json,
)
from assistant.settings import APP_NAME, BIND_HOST

logger = logging.getLogger(__name__)
START_WAIT_SECONDS = 30.0
STOP_WAIT_SECONDS = 15.0
OPERATOR_MODES = ("prepare", "screen", "pipeline", "tracking")
ENVIRONMENT_KEYS = frozenset({"TIKHUB_API_KEY", "ARK_API_KEY", "ARK_MODEL", "FFMPEG_PATH"})
ENVIRONMENT_PREFIXES = ("FEISHU_", "LLM_", "CONTENT_REVIEW_")


def check_configuration() -> list[str]:
    """Require both fixed entry-point stores; never consult developer defaults."""
    path = configuration_dir() / "config.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["config.toml"]
    stores = raw.get("stores")
    if not isinstance(stores, dict):
        return ["stores.default_store_id", "stores.prepare_store_id"]
    missing_fields = [f"stores.{field}" for field in ("default_store_id", "prepare_store_id")
                      if not isinstance(stores.get(field), str) or not re.fullmatch(r"[1-9][0-9]{0,29}", stores[field])]
    environment_path = configuration_dir() / ".env"
    if environment_path.exists():
        try:
            for line in environment_path.read_text(encoding="utf-8").splitlines():
                name, separator, value = line.strip().partition("=")
                name = name.strip()
                if separator and not name.startswith("#") and not (name in ENVIRONMENT_KEYS or name.startswith(ENVIRONMENT_PREFIXES)):
                    missing_fields.append(".env.business-fields-only")
                    break
        except (OSError, ValueError):
            missing_fields.append(".env")
    return missing_fields


def require_configuration() -> None:
    missing_fields = check_configuration()
    if missing_fields:
        print(json.dumps({"error": "configuration-required", "fields": missing_fields,
                          "repair_path": str(configuration_dir() / "config.toml")}, ensure_ascii=True))
        raise ReleaseLifecycleError("configuration-required")


def load_user_environment() -> None:
    """Load only business settings, never Python/Node/PATH launch controls."""
    # Fix store selection to the technician-reviewed user config in this mode.
    for name in tuple(os.environ):
        if name == "ZN_SAMPLE_CONFIG" or name.startswith(("ZN_SAMPLE_STORE_", "ZN_SAMPLE_PREPARE_STORE_")):
            os.environ.pop(name)
    environment_path = configuration_dir() / ".env"
    if not environment_path.is_file():
        return
    for line in environment_path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().partition("=")
        name = name.strip()
        if separator and (name in ENVIRONMENT_KEYS or name.startswith(ENVIRONMENT_PREFIXES)):
            os.environ.setdefault(name, value.strip().strip("\"'"))


def check_release_resources() -> dict:
    from setup.release.resources import APPLICATION_SCOPE, collect_application_resources

    release = _release_manifest()
    if release is None or release[1].get("scope") != APPLICATION_SCOPE:
        raise ReleaseLifecycleError("complete-release-bundle-required")
    bundle_root, manifest = release
    assert_private_interpreter()
    for tool_name in ("python", "node", "ffmpeg"):
        bundled_tool_path(tool_name)
    required_paths = {"app/" + relative for relative in collect_application_resources(ROOT)}
    manifest_paths = {entry["path"] for entry in manifest["files"]}
    if not required_paths.issubset(manifest_paths):
        raise ReleaseLifecycleError("release-resource-unlisted")
    for entry in manifest["files"]:
        if not entry["path"].startswith("app/"):
            continue
        path = bundle_root / entry["path"]
        if not path.is_file() or path.is_symlink():
            raise ReleaseLifecycleError("release-resource-missing")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry.get("sha256"):
                raise ReleaseLifecycleError("release-resource-hash")
    if not manifest.get("application_version") or manifest["application_version"] == "dev":
        raise ReleaseLifecycleError("release-version-required")
    return manifest


def read_state() -> dict | None:
    path = runtime_dir() / "state.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        state = read_private_json(path)
        if type(state.get("pid")) is not int or state["pid"] <= 0 or type(state.get("port")) is not int or not 1 <= state["port"] <= 65535:
            raise ValueError("invalid identity")
        return state
    except (OSError, ValueError, ReleaseLifecycleError) as error:
        raise ReleaseLifecycleError("runtime-state-invalid") from error


def verified_health(state: dict) -> dict | None:
    if _is_process_alive(state["pid"]) is not True:
        return None

    class LocalHealthOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *arguments, **options):
            return None

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalHealthOnly())
        with opener.open(f"http://{BIND_HOST}:{state['port']}/api/health", timeout=1) as response:
            health = json.loads(response.read(16384))
        if not isinstance(health, dict) or health.get("app") != APP_NAME or health.get("ok") is not True:
            return None
        if "instance_id" in state and (health.get("instance_id") != state["instance_id"] or health.get("pid") != state["pid"]):
            return None
        if any(field in state and health.get(field) != state[field] for field in ("version", "target")):
            return None
        return health
    except (OSError, ValueError):
        return None


def open_healthy_instance(state: dict, health: dict) -> int:
    # A different version is deliberately not replaced or migrated.
    print(json.dumps({"status": "opened-existing", "version": health.get("version"), "pid": state["pid"]}))
    webbrowser.open(f"http://{BIND_HOST}:{state['port']}/")
    return 0


class ReleaseInstanceLock(InstanceLock):
    def _try_reclaim_windows_lock(self) -> bool:
        # A stale release lock is a maintenance blocker, not permission to
        # unlink an owner based on PID alone or race another reclaiming client.
        return False


def acquire_lock(name: str) -> InstanceLock:
    lock_path = runtime_dir() / name
    if runtime_dir().is_symlink() or lock_path.is_symlink():
        raise ReleaseLifecycleError("runtime-path-unsafe")
    lock = ReleaseInstanceLock(lock_path)
    if not lock.acquire():
        raise ReleaseLifecycleError("instance-busy-or-unknown")
    return lock


def start() -> int:
    ensure_user_dirs()
    launch_lock = acquire_lock("launch.lock")
    try:
        state = read_state()
        if state is not None:
            health = verified_health(state)
            if health is None:
                raise ReleaseLifecycleError("existing-instance-unverified")
            return open_healthy_instance(state, health)
        # An old source entry may hold the same lock without a readable state.
        instance_lock = acquire_lock("instance.lock")
        try:
            require_configuration()
            check_residual_jobs(database_path())
        finally:
            instance_lock.release()
        log_path = user_data_dir() / "logs" / "release-service.log"
        environment = python_subprocess_environment()
        options = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        }
        # -I ignores PYTHONUTF8; redirected Windows service logs need a flag.
        interpreter_flags = ["-I", "-B", "-X", "utf8"] if os.name == "nt" else ["-I", "-B"]
        with log_path.open("ab", buffering=0) as log_stream:
            process = subprocess.Popen(
                [str(bundled_tool_path("python")), *interpreter_flags, str(Path(__file__).resolve()), "serve"],
                cwd=str(ROOT), env=environment, stdin=subprocess.DEVNULL,
                stdout=log_stream, stderr=log_stream, close_fds=True, **options,
            )
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline:
            state = read_state()
            if state is not None:
                health = verified_health(state)
                if health is not None:
                    return open_healthy_instance(state, health)
            if process.poll() is not None:
                raise ReleaseLifecycleError("service-start-failed")
            time.sleep(0.1)
        # Unknown means unknown. The child may be starting; do not kill it.
        raise ReleaseLifecycleError("service-start-timeout-unknown")
    finally:
        launch_lock.release()


def serve() -> int:
    application_directory = ensure_user_dirs()
    instance_lock = acquire_lock("instance.lock")
    try:
        if read_state() is not None:
            raise ReleaseLifecycleError("existing-instance-unverified")
        require_configuration()
        load_user_environment()
        check_residual_jobs(database_path())
        from assistant.bootstrap import upgrade_database

        upgrade_database(sqlite_path=database_path())
        return run_release_server(application_directory, instance_lock)
    finally:
        instance_lock.release()


def stop() -> int:
    launch_lock = acquire_lock("launch.lock")
    try:
        return request_service_stop()
    finally:
        launch_lock.release()


def request_service_stop() -> int:
    state = read_state()
    if state is None or not re.fullmatch(r"[0-9a-f]{32}", str(state.get("instance_id", ""))) or verified_health(state) is None:
        raise ReleaseLifecycleError("existing-instance-unverified")
    request_id = uuid.uuid4().hex
    control_directory = runtime_dir() / "control"
    response_path = control_directory / f"response-{request_id}.json"
    request_path = control_directory / f"request-{request_id}.json"
    write_private_json(request_path, {"action": "stop", "instance_id": state["instance_id"], "request_id": request_id})
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    accepted = False
    try:
        while time.monotonic() < deadline:
            if not accepted and response_path.exists():
                response = read_private_json(response_path)
                if response.get("request_id") != request_id or response.get("instance_id") != state["instance_id"]:
                    raise ReleaseLifecycleError("control-response-invalid")
                if response.get("result") != "accepted":
                    raise ReleaseLifecycleError(str(response.get("result", "control-response-invalid")))
                accepted = True
            if accepted:
                # Service deletes state only after worker/children are joined.
                if read_state() is None:
                    try:
                        lock = acquire_lock("instance.lock")
                    except ReleaseLifecycleError:
                        pass
                    else:
                        lock.release()
                        print(json.dumps({"status": "stopped", "instance_id": state["instance_id"]}))
                        return 0
            time.sleep(0.1)
        raise ReleaseLifecycleError("stop-timeout-unknown")
    finally:
        request_path.unlink(missing_ok=True)
        response_path.unlink(missing_ok=True)


def operator(mode: str) -> int:
    if mode not in OPERATOR_MODES:
        raise ReleaseLifecycleError("invalid-operator-mode")
    ensure_user_dirs()
    launch_lock = acquire_lock("launch.lock")
    instance_lock = None
    try:
        if read_state() is not None:
            raise ReleaseLifecycleError("stop-console-before-operator")
        instance_lock = acquire_lock("instance.lock")
        require_configuration()
        load_user_environment()
        check_residual_jobs(database_path())
        from launch_sample import main as launch_sample_main

        # Keeps original operator argv, interactive y and business limits.
        with SubprocessRegistry(runtime_dir() / "children").track():
            return launch_sample_main([mode])
    finally:
        if instance_lock is not None:
            instance_lock.release()
        launch_lock.release()


def diagnose(manifest: dict) -> int:
    missing_fields = check_configuration()
    print(json.dumps({"status": "configuration-required" if missing_fields else "ready-offline",
                      "version": manifest["application_version"], "target": manifest["target"],
                      "data_path": str(user_data_dir()), "config_path": str(configuration_dir() / "config.toml"),
                      "fields": missing_fields, "external_cli": "technician-authorization-required"}, ensure_ascii=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local release lifecycle (no automatic restart).")
    parser.add_argument("mode", choices=("start", "serve", "stop", "diagnose", "operator"))
    parser.add_argument("operator_mode", nargs="?", choices=OPERATOR_MODES)
    arguments = parser.parse_args(argv)
    if (arguments.mode == "operator") != (arguments.operator_mode is not None):
        parser.error("operator requires exactly one fixed operator mode")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = check_release_resources()
        if arguments.mode == "diagnose":
            return diagnose(manifest)
        if arguments.mode == "operator":
            return operator(arguments.operator_mode)
        return {"start": start, "serve": serve, "stop": stop}[arguments.mode]()
    except (ReleaseLifecycleError, OSError, ValueError, RuntimeError) as error:
        code = error.code if isinstance(error, (ReleaseLifecycleError, ReleasePathError)) else "release-operation-failed"
        error_number = error.errno if isinstance(error, OSError) else None
        # Keep a safe type/code, not str(error), traceback locals, or config values.
        logger.error("%s; exception=%s; errno=%s; logs: %s", code, type(error).__name__,
                     error_number, user_data_dir() / "logs" / "release-service.log")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
