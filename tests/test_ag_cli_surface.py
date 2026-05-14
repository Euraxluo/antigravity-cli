import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ag_cli


def run_ag(args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = ag_cli.main(args)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, stdout.getvalue(), stderr.getvalue()


class AgCliSurfaceTests(unittest.TestCase):
    def test_repo_launcher_bootstraps_editable_environment_with_uv(self):
        launcher = (REPO_ROOT / "ag").read_text(encoding="utf-8")
        self.assertIn('"venv"', launcher)
        self.assertIn('"pip"', launcher)
        self.assertIn('"install"', launcher)
        self.assertIn('"-e"', launcher)
        self.assertIn("astral.sh/uv/install.sh", launcher)
        self.assertIn(".ag-editable-installed", launcher)

    def test_every_public_command_help_parses(self):
        command_paths = [
            ["--help"],
            ["ask", "--help"],
            ["models", "--help"],
            ["doctor", "--help"],
            ["chat", "--help"],
            ["chat", "send", "--help"],
            ["chat", "resume", "--help"],
            ["chat", "start", "--help"],
            ["chat", "stream", "--help"],
            ["sessions", "--help"],
            ["sessions", "list", "--help"],
            ["sessions", "show", "--help"],
            ["sessions", "files", "--help"],
            ["sessions", "messages", "--help"],
            ["ui", "--help"],
            ["ui", "serve", "--help"],
            ["attachments", "--help"],
            ["attachments", "bytes", "--help"],
            ["cache", "--help"],
            ["cache", "warm", "--help"],
            ["runtime", "--help"],
            ["runtime", "send", "--help"],
            ["runtime", "resume", "--help"],
            ["runtime", "models", "--help"],
            ["send", "--help"],
            ["resume", "--help"],
            ["webui", "--help"],
        ]
        for args in command_paths:
            with self.subTest(args=args):
                code, stdout, stderr = run_ag(args)
                self.assertEqual(code, 0, stderr)
                self.assertIn("usage:", stdout)

    def test_removed_bridge_debug_commands_are_not_public_surface(self):
        code, stdout, stderr = run_ag(["debug", "--help"])
        self.assertNotEqual(code, 0)
        self.assertIn("invalid choice", stderr)

        code, stdout, stderr = run_ag(["ui", "new", "--help"])
        self.assertNotEqual(code, 0)
        self.assertIn("invalid choice", stderr)

    def test_models_outputs_dynamic_json_and_text(self):
        models = [{"id": 2048, "label": "Runtime Model", "default": True, "supportsImages": True}]
        with mock.patch.object(ag_cli, "load_models", return_value=models):
            code, stdout, stderr = run_ag(["models", "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout), {"models": models})

        with mock.patch.object(ag_cli, "load_models", return_value=models):
            code, stdout, stderr = run_ag(["models"])
        self.assertEqual(code, 0, stderr)
        self.assertIn("2048", stdout)
        self.assertIn("Runtime Model", stdout)

    def test_doctor_json_can_pass_with_mocked_runtime(self):
        class FakeLocator:
            def __init__(self, root):
                self.root = root

            def _process_rows(self):
                return [(123, "language_server --csrf_token tok --extension_server_port 9999")]

            def discover(self):
                return SimpleNamespace(pid=123, port=9999, use_tls=True, csrf_token="tok")

        class FakeRpcClient:
            def __init__(self, connection):
                self.connection = connection

            def call(self, method, payload):
                if method != "GetUserStatus":
                    raise AssertionError(method)
                return {"userStatus": {}}

        writable = lambda path: {"ok": True, "path": str(path)}
        with mock.patch("runtime_cli.ag_runtime.RuntimeLocator", FakeLocator), \
             mock.patch("runtime_cli.ag_runtime.RuntimeRpcClient", FakeRpcClient), \
             mock.patch.object(ag_cli, "load_models", return_value=[{"id": 1037, "label": "Gemini 3.1 Pro (High)", "default": True}]), \
             mock.patch.object(ag_cli, "check_path_writable", side_effect=writable):
            code, stdout, stderr = run_ag(["doctor", "--json"])

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["checks"]["language_server"]["ok"])
        self.assertTrue(payload["checks"]["get_user_status"]["ok"])
        self.assertNotIn("bridge", payload["checks"])

    def test_doctor_json_fails_when_runtime_is_unavailable(self):
        class FakeLocator:
            def __init__(self, root):
                self.root = root

            def _process_rows(self):
                return []

        writable = lambda path: {"ok": True, "path": str(path)}
        with mock.patch("runtime_cli.ag_runtime.RuntimeLocator", FakeLocator), \
             mock.patch.object(ag_cli, "load_models", return_value=[{"id": 1037, "label": "Gemini 3.1 Pro (High)", "default": True}]), \
             mock.patch.object(ag_cli, "check_path_writable", side_effect=writable):
            code, stdout, stderr = run_ag(["doctor", "--json"])

        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["checks"]["language_server"]["ok"])
        self.assertNotIn("bridge", payload["checks"])

    def test_store_backed_commands_route_and_render(self):
        calls = []

        def fake_store_json(cmd):
            calls.append(cmd)
            if cmd == ["sessions", "list"]:
                return [{"id": "s1", "title": "Demo", "updated_local": "now", "file_count": 2}]
            if cmd[:1] == ["show"]:
                return {"session": {"id": cmd[1]}, "files": [], "messages": []}
            if cmd[:2] == ["sessions", "files"]:
                return [{"name": "demo.txt", "kind": "file", "size": 4, "summary": "demo"}]
            if cmd[:2] == ["sessions", "messages"]:
                return [{"role": "user", "content": "hello"}]
            if cmd[:2] == ["attachment", "bytes"]:
                return {"name": "demo.txt", "mime_type": "text/plain", "base64": "aGk="}
            if cmd[:2] == ["cache", "warm"]:
                return {"processed": 1, "limit": int(cmd[-1])}
            if cmd[:1] in (["send"], ["start"]):
                return {"session_id": "s1", "answer": "ok"}
            raise AssertionError(f"unexpected store command: {cmd}")

        with mock.patch.object(ag_cli, "run_store_json", side_effect=fake_store_json), \
             mock.patch.object(ag_cli, "load_models", return_value=[{"id": 2048, "label": "Runtime Model", "default": True}]):
            scenarios = [
                (["sessions", "list"], "Demo"),
                (["sessions", "show", "s1", "--json"], '"session"'),
                (["sessions", "files", "s1"], "demo.txt"),
                (["sessions", "messages", "s1"], "user: hello"),
                (["attachments", "bytes", "/tmp/demo.txt"], '"base64"'),
                (["cache", "warm", "--limit", "5", "--json"], '"processed"'),
                (["ask", "hello", "--model-label", "Runtime Model"], "ok"),
                (["ask", "hello", "--async"], "ok"),
                (["chat", "send", "hello", "--session", "s1", "--model", "1037"], "ok"),
                (["chat", "resume", "--session", "s1", "hello"], "ok"),
                (["chat", "start", "hello"], "ok"),
                (["chat", "start", "hello", "--async"], "ok"),
                (["send", "--session", "s1", "hello"], "ok"),
                (["resume", "--session", "s1", "hello"], "ok"),
            ]
            for args, expected in scenarios:
                with self.subTest(args=args):
                    code, stdout, stderr = run_ag(args)
                    self.assertEqual(code, 0, stderr)
                    self.assertIn(expected, stdout)

        self.assertIn(["sessions", "list"], calls)
        self.assertTrue(any(call[:1] == ["send"] for call in calls))
        self.assertTrue(any(call[:1] == ["start"] for call in calls))

    def test_json_stream_routes_to_store_passthrough(self):
        with mock.patch.object(ag_cli, "run_store_passthrough", return_value=0) as passthrough:
            code, stdout, stderr = run_ag(["chat", "stream", "hello", "--session", "s1", "--json"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(passthrough.call_args.args[0][:3], ["stream", "hello", "--session"])

    def test_workspace_option_sets_environment_for_models(self):
        captured = {}

        def fake_load_models(*, launch_runtime=False):
            captured["workspace"] = os.environ.get("AG_WORKSPACE")
            return [{"id": 2048, "label": "Runtime Model", "default": True}]

        with mock.patch.object(ag_cli, "load_models", side_effect=fake_load_models):
            code, stdout, stderr = run_ag(["models", "--workspace", "/tmp/demo-workspace"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(captured["workspace"], str(Path("/tmp/demo-workspace").resolve()))

    def test_ui_serve_workspace_flows_to_ui_process(self):
        with mock.patch.object(ag_cli, "python_with_module", return_value="/python"), \
             mock.patch.object(ag_cli, "run_passthrough", return_value=0) as passthrough:
            code, stdout, stderr = run_ag(["ui", "serve", "--workspace", "/tmp/demo-workspace"])
        self.assertEqual(code, 0, stderr)
        command = passthrough.call_args.args[0]
        self.assertIn("--workspace", command)
        self.assertIn(str(Path("/tmp/demo-workspace").resolve()), command)

    def test_runtime_and_ui_serve_route_to_existing_modules(self):
        scenarios = [
            ["runtime", "models", "--fallback"],
            ["runtime", "send", "hello", "--model", "1037"],
            ["runtime", "resume", "--session", "s1", "hello", "--model-label", "Runtime Model"],
        ]
        with mock.patch.object(ag_cli, "run_passthrough", return_value=0) as passthrough, \
             mock.patch.object(ag_cli, "load_models", return_value=[{"id": 2048, "label": "Runtime Model", "default": True}]):
            for args in scenarios:
                with self.subTest(args=args):
                    code, stdout, stderr = run_ag(args)
                    self.assertEqual(code, 0, stderr)
                    self.assertEqual(stdout, "")

        self.assertEqual(passthrough.call_count, len(scenarios))

    def test_ui_serve_and_webui_route_to_chat_composer_ui(self):
        with mock.patch.object(ag_cli, "python_with_module", return_value="/python"), \
             mock.patch.object(ag_cli, "run_passthrough", return_value=0) as passthrough:
            for args in (
                ["ui", "serve", "--host", "127.0.0.1", "--port", "8765"],
                ["webui", "--host", "127.0.0.1", "--port", "8765"],
            ):
                with self.subTest(args=args):
                    code, stdout, stderr = run_ag(args)
                    self.assertEqual(code, 0, stderr)
                    command = passthrough.call_args.args[0]
                    self.assertEqual(command[:2], ["/python", str(ag_cli.REPO_ROOT / "ui.py")])
                    self.assertNotIn("antigravity-chat.py", command)

    @unittest.skipUnless(shutil.which("uv"), "uv is required for fresh clone bootstrap smoke")
    def test_fresh_clone_launcher_smoke_creates_venv_and_runs_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "antigravity-cli"
            ignore = shutil.ignore_patterns(".git", ".venv", ".cache", "__pycache__", "*.pyc", "antigravity_cli.egg-info")
            shutil.copytree(REPO_ROOT, clone, ignore=ignore)
            env = {**os.environ, "AG_BOOTSTRAP_SMOKE": "1"}
            proc = subprocess.run([str(clone / "ag"), "--help"], cwd=str(clone), env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Antigravity sessions, chat, and local UI", proc.stdout)
            self.assertTrue((clone / ".venv" / "bin" / "python").exists())
            self.assertTrue((clone / ".venv" / ".ag-editable-installed").exists())


if __name__ == "__main__":
    unittest.main()
