import io
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime_cli.ag_runtime import AnswerCollector, DEFAULT_MODEL_ID, RuntimeLocator, RuntimeRpcClient


class RuntimeLocatorTests(unittest.TestCase):
    def test_parse_process_rows_filters_language_server(self) -> None:
        output = """
          11 /bin/zsh
         123 /Applications/Antigravity.app/language_server_macos_arm --csrf_token abc --extension_server_port 3000
         456 /Applications/Antigravity.app/language_server_macos_arm --csrf_token def --extension_server_port 4000
        """
        rows = RuntimeLocator.parse_process_rows(output)
        self.assertEqual(
            rows,
            [
                (123, "/Applications/Antigravity.app/language_server_macos_arm --csrf_token abc --extension_server_port 3000"),
                (456, "/Applications/Antigravity.app/language_server_macos_arm --csrf_token def --extension_server_port 4000"),
            ],
        )

    def test_select_process_prefers_hint(self) -> None:
        rows = [
            (123, "language_server --workspace_id file_x_other_repo --csrf_token abc"),
            (456, "language_server --workspace_id file_x_antigravity_cli --csrf_token def"),
        ]
        selected = RuntimeLocator._select_process(rows, ["antigravity_cli"])
        self.assertEqual(selected, rows[1])

    def test_extract_arg_supports_space_and_equals(self) -> None:
        self.assertEqual(RuntimeLocator._extract_arg("--csrf_token abc", "csrf_token"), "abc")
        self.assertEqual(RuntimeLocator._extract_arg("--csrf_token=abc", "csrf_token"), "abc")

    def test_build_launch_command(self) -> None:
        self.assertEqual(RuntimeLocator.build_launch_command(Path("/tmp/demo")), ["antigravity", "-n"])

    def test_discover_launches_runtime_when_missing(self) -> None:
        locator = RuntimeLocator(Path("/tmp/demo"))
        with mock.patch.object(locator, "_process_rows", side_effect=[[], [(123, "language_server --csrf_token abc --extension_server_port 3000")]]), \
             mock.patch.object(locator, "_launch_runtime") as launch_runtime, \
             mock.patch.object(locator, "_listening_ports", return_value=[3000, 3001]), \
             mock.patch.object(locator, "_find_connect_port", return_value=(3001, True)):
            conn = locator.discover()

        launch_runtime.assert_called_once_with()
        self.assertEqual(conn.pid, 123)
        self.assertEqual(conn.port, 3001)
        self.assertTrue(conn.use_tls)
        self.assertEqual(conn.csrf_token, "abc")


class RuntimeRpcClientTests(unittest.TestCase):
    def test_default_model_id_is_current_antigravity_selection(self) -> None:
        self.assertEqual(DEFAULT_MODEL_ID, 1037)

    def test_build_send_payload(self) -> None:
        payload = RuntimeRpcClient.build_send_payload("cascade-1", "hello", model=7)
        self.assertEqual(payload["cascadeId"], "cascade-1")
        self.assertEqual(payload["items"][0]["text"], "hello")
        self.assertEqual(payload["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], 7)

    def test_build_send_payload_defaults_to_gemini_3_1_pro_high(self) -> None:
        payload = RuntimeRpcClient.build_send_payload("cascade-1", "hello")
        self.assertEqual(payload["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], 1037)


class AnswerCollectorTests(unittest.TestCase):
    def test_extract_assistant_text_ignores_baseline_steps(self) -> None:
        steps = [
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"userResponse": "old"}},
            {"type": "CORTEX_STEP_TYPE_NOTIFY_USER", "notifyUser": {"notificationContent": "old answer"}},
            {"type": "CORTEX_STEP_TYPE_NOTIFY_USER", "notifyUser": {"notificationContent": "new answer"}},
        ]
        self.assertEqual(AnswerCollector.extract_assistant_text(steps, 2), "new answer")

    def test_extract_assistant_text_reads_planner_response(self) -> None:
        steps = [
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"userResponse": "old"}},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {"response": "hi", "modifiedResponse": "hi"}},
        ]
        self.assertEqual(AnswerCollector.extract_assistant_text(steps, 1), "hi")

    def test_extract_assistant_text_reads_error_message(self) -> None:
        steps = [
            {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "errorMessage": {"error": {"userErrorMessage": "capacity exhausted"}}},
        ]
        self.assertEqual(AnswerCollector.extract_assistant_text(steps, 0), "capacity exhausted")

    def test_emit_delta_appends_only_new_suffix(self) -> None:
        buf = io.StringIO()
        current = AnswerCollector.emit_delta("", "hello", buf)
        current = AnswerCollector.emit_delta(current, "hello world", buf)
        self.assertEqual(current, "hello world")
        self.assertEqual(buf.getvalue(), "hello world")

    def test_emit_delta_handles_rewrite_with_common_prefix(self) -> None:
        buf = io.StringIO()
        current = AnswerCollector.emit_delta("", "hello there", buf)
        current = AnswerCollector.emit_delta(current, "hello world", buf)
        self.assertEqual(current, "hello world")
        self.assertEqual(buf.getvalue(), "hello there\nhello world")


if __name__ == "__main__":
    unittest.main()
