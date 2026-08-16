from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.zclaw_cli import (  # noqa: E402
    CLI_NOT_FOUND,
    WINDOWS_SHIM_INCOMPLETE,
    resolve_ziniao_cli_command,
    run_ziniao_cli,
)
from lib.zclaw import zclaw_invoke  # noqa: E402


class ResolveZiniaoCliCommandTests(unittest.TestCase):
    def test_plain_binary_uses_resolved_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "ziniao-cli"
            cli.write_text("", encoding="utf-8")
            cli.chmod(0o755)

            def which(name: str) -> str | None:
                return str(cli) if name == "ziniao-cli" else None

            self.assertEqual(
                resolve_ziniao_cli_command(which=which),
                [str(cli.resolve())],
            )

    def test_windows_cmd_uses_node_and_run_js(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            npm_dir = Path(tmp)
            cmd = npm_dir / "ziniao-cli.cmd"
            cmd.write_text("ignored", encoding="utf-8")
            runner = (
                npm_dir / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"
            )
            runner.parent.mkdir(parents=True)
            runner.write_text("module.exports = {}", encoding="utf-8")
            node = npm_dir / "node.exe"
            node.write_text("", encoding="utf-8")

            def which(name: str) -> str | None:
                if name == "ziniao-cli.cmd":
                    return str(cmd)
                if name in {"node", "node.exe"}:
                    return str(node)
                return None

            self.assertEqual(
                resolve_ziniao_cli_command(which=which),
                [str(node.resolve()), str(runner.resolve())],
            )

    def test_windows_cmd_missing_runner_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cmd = Path(tmp) / "ziniao-cli.cmd"
            cmd.write_text("ignored", encoding="utf-8")
            node = Path(tmp) / "node.exe"
            node.write_text("", encoding="utf-8")

            def which(name: str) -> str | None:
                if name == "ziniao-cli.cmd":
                    return str(cmd)
                if name in {"node", "node.exe"}:
                    return str(node)
                return None

            with self.assertRaisesRegex(RuntimeError, "run.js"):
                resolve_ziniao_cli_command(which=which)

    def test_windows_cmd_missing_node_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            npm_dir = Path(tmp)
            cmd = npm_dir / "ziniao-cli.cmd"
            cmd.write_text("ignored", encoding="utf-8")
            runner = (
                npm_dir / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"
            )
            runner.parent.mkdir(parents=True)
            runner.write_text("", encoding="utf-8")

            def which(name: str) -> str | None:
                return str(cmd) if name == "ziniao-cli.cmd" else None

            with self.assertRaisesRegex(RuntimeError, WINDOWS_SHIM_INCOMPLETE):
                resolve_ziniao_cli_command(which=which)

    def test_missing_cli_does_not_mention_path_env(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            resolve_ziniao_cli_command(which=lambda _name: None)
        message = str(ctx.exception)
        self.assertEqual(message, CLI_NOT_FOUND)
        self.assertNotIn("PATH", message)
        self.assertNotIn("path", message)


class RunZiniaoCliTests(unittest.TestCase):
    def test_subprocess_uses_utf8_and_no_shell(self) -> None:
        runner = Mock()
        runner.return_value = Mock(stdout="{}", stderr="", returncode=0)
        run_ziniao_cli(
            ["zclaw", "invoke", "list_stores", "--args", "{}"],
            timeout=30,
            resolve_command=lambda: ["/abs/node", "/abs/run.js"],
            runner=runner,
        )
        runner.assert_called_once()
        args, kwargs = runner.call_args
        self.assertEqual(
            args[0],
            ["/abs/node", "/abs/run.js", "zclaw", "invoke", "list_stores", "--args", "{}"],
        )
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], 30)


class ZclawInvokeTests(unittest.TestCase):
    def test_parses_json_from_stderr_when_stdout_empty(self) -> None:
        proc = Mock(stdout="", stderr='{"ok": false, "error": {"type": "network"}}', returncode=1)
        with patch("lib.zclaw.run_ziniao_cli", return_value=proc) as run:
            outer = zclaw_invoke("extract_data", {"mode": "running"}, timeout=12)
        self.assertEqual(outer["ok"], False)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], "zclaw")
        self.assertEqual(kwargs["timeout"], 12)


if __name__ == "__main__":
    unittest.main()
