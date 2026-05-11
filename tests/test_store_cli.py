import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import store as store_module
import runtime_cli.model_registry as model_registry
from runtime_cli.model_registry import extract_model_options_from_user_status, load_model_options, model_enum_id


class FakeStore:
    def list_sessions(self):
        return [{"id": "s1"}]

    def list_session_files(self, session_id):
        return [{"name": f"{session_id}.txt"}]

    def get_session_file_content(self, session_id, file_name):
        return {"session_id": session_id, "name": file_name, "content": "hello"}

    def get_session_file_bytes(self, session_id, file_name):
        return {"session_id": session_id, "name": file_name, "mime_type": "text/plain", "bytes": b"hello"}

    def get_session_messages(self, session_id, force_refresh=False):
        return [{"role": "user", "content": session_id, "refresh": force_refresh}]

    def get_session_summary(self, session_id, fallback_title=None):
        return {"id": session_id, "title": fallback_title or session_id}

    def get_attachment_bytes(self, path):
        return {"name": Path(path).name, "mime_type": "text/plain", "bytes": b"attach"}

    def send_message(self, *args, **kwargs):
        return {"session_id": kwargs.get("session_id") or "new", "answer": "ok"}

    def start_session_send(self, *args, **kwargs):
        return {"session_id": "started", "created": True}

    def stream_message(self, *args, **kwargs):
        yield {"type": "session", "session_id": "s1"}
        yield {"type": "delta", "delta": "hi", "full": "hi"}
        yield {"type": "done", "session_id": "s1", "answer": "hi"}

    def warm_all_messages(self, limit=None):
        return {"processed": 1, "limit": limit}


class StoreCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.patcher = mock.patch.object(store_module, "_store", return_value=FakeStore())
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_models(self):
        result = self.runner.invoke(store_module.app, ["models"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Gemini 3.1 Pro (High)", result.stdout)
        self.assertIn('"id": 1037', result.stdout)

    def test_models_env_override(self):
        payload = json.dumps([{"id": 2048, "label": "Custom Antigravity Model", "default": True}])
        with mock.patch.dict(os.environ, {"ANTIGRAVITY_MODELS_JSON": payload}):
            result = self.runner.invoke(store_module.app, ["models"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Custom Antigravity Model", result.stdout)
        self.assertIn('"id": 2048', result.stdout)

    def test_model_registry_extracts_antigravity_ui_models(self):
        self.assertEqual(model_enum_id("MODEL_PLACEHOLDER_M37"), 1037)
        self.assertEqual(model_enum_id("MODEL_PLACEHOLDER_M84"), 1084)
        payload = {
            "userStatus": {
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {"label": "Gemini 3 Flash", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M84"}},
                        {"label": "Gemini 3.1 Pro (High)", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M37"}},
                        {"label": "GPT-OSS 120B (Medium)", "modelOrAlias": {"model": "MODEL_OPENAI_GPT_OSS_120B_MEDIUM"}},
                    ],
                    "clientModelSorts": [{
                        "name": "Recommended",
                        "groups": [{"modelLabels": ["Gemini 3.1 Pro (High)", "Gemini 3 Flash", "GPT-OSS 120B (Medium)"]}],
                    }],
                    "defaultOverrideModelConfig": {"modelOrAlias": {"model": "MODEL_PLACEHOLDER_M37"}},
                }
            }
        }
        options = extract_model_options_from_user_status(payload)
        self.assertEqual([item["id"] for item in options], [1037, 1084, 342])
        self.assertTrue(options[0]["default"])

    def test_model_registry_dynamic_loader_uses_runtime_status(self):
        payload = {
            "userStatus": {
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {"label": "Runtime Model", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M55"}},
                    ],
                    "defaultOverrideModelConfig": {"modelOrAlias": {"model": "MODEL_PLACEHOLDER_M55"}},
                }
            }
        }
        with mock.patch.object(model_registry, "fetch_antigravity_user_status", return_value=payload):
            options = load_model_options(dynamic=True)
        self.assertEqual(options, [{"id": 1055, "label": "Runtime Model", "default": True}])

    def test_sessions_list_alias_and_group(self):
        for args in (["list"], ["sessions", "list"]):
            result = self.runner.invoke(store_module.app, args)
            self.assertEqual(result.exit_code, 0)
            self.assertIn('"id": "s1"', result.stdout)

    def test_files_alias_and_group(self):
        for args in (["files", "abc"], ["sessions", "files", "abc"]):
            result = self.runner.invoke(store_module.app, args)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("abc.txt", result.stdout)

    def test_messages_alias_and_group(self):
        for args in (["messages", "abc", "--refresh"], ["sessions", "messages", "abc", "--refresh"]):
            result = self.runner.invoke(store_module.app, args)
            self.assertEqual(result.exit_code, 0)
            self.assertIn('"refresh": true', result.stdout)

    def test_file_content_and_bytes(self):
        result = self.runner.invoke(store_module.app, ["sessions", "file-content", "abc", "demo.txt"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('"content": "hello"', result.stdout)
        result = self.runner.invoke(store_module.app, ["sessions", "file-bytes", "abc", "demo.txt"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn(base64.b64encode(b"hello").decode("ascii"), result.stdout)

    def test_show(self):
        result = self.runner.invoke(store_module.app, ["show", "abc"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('"session"', result.stdout)
        self.assertIn('"messages"', result.stdout)

    def test_send_alias_and_group(self):
        for args in (
            ["send", "hello", "--session", "abc", "--model", "1037"],
            ["chat", "send", "hello", "--session-id", "abc", "--model", "1037"],
        ):
            result = self.runner.invoke(store_module.app, args)
            self.assertEqual(result.exit_code, 0)
            self.assertIn('"answer": "ok"', result.stdout)

    def test_resume(self):
        result = self.runner.invoke(store_module.app, ["resume", "--session", "abc", "hello"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('"answer": "ok"', result.stdout)

    def test_start_alias_and_group(self):
        for args in (["start", "hello"], ["chat", "start", "hello"]):
            result = self.runner.invoke(store_module.app, args)
            self.assertEqual(result.exit_code, 0)
            self.assertIn('"session_id": "started"', result.stdout)

    def test_stream_alias_and_group(self):
        for args in (["stream", "hello"], ["chat", "stream", "hello"]):
            result = self.runner.invoke(store_module.app, args)
            self.assertEqual(result.exit_code, 0)
            self.assertIn('"type": "session"', result.stdout)
            self.assertIn('"type": "delta"', result.stdout)
            self.assertIn('"type": "done"', result.stdout)

    def test_attachment_bytes(self):
        result = self.runner.invoke(store_module.app, ["attachment", "bytes", "/tmp/demo.txt"])
        self.assertEqual(result.exit_code, 0)
        expected = base64.b64encode(b"attach").decode("ascii")
        self.assertIn(expected, result.stdout)

    def test_cache_warm(self):
        result = self.runner.invoke(store_module.app, ["cache", "warm", "--limit", "5"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('"limit": 5', result.stdout)


if __name__ == "__main__":
    unittest.main()
