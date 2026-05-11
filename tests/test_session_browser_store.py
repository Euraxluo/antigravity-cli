import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import store as session_store_module
from store import AntigravitySessionStore


class SessionBrowserStoreTests(unittest.TestCase):
    def test_steps_to_messages_uses_modified_response_and_error_message(self) -> None:
        steps = [
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "metadata": {}, "userInput": {"userResponse": "hello"}},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "metadata": {}, "plannerResponse": {"response": "old", "modifiedResponse": "new"}},
            {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "metadata": {}, "errorMessage": {"error": {"userErrorMessage": "rate limited"}}},
        ]
        store = AntigravitySessionStore()
        messages = store._steps_to_messages(steps)
        self.assertEqual(messages[0]["content"], "hello")
        self.assertEqual(messages[1]["content"], "new")
        self.assertEqual(messages[2]["content"], "rate limited")

    def test_list_sessions_includes_cache_only_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv = root / "conversations"
            brain = root / "brain"
            cache = root / "cache"
            workspace = root / "workspace"
            conv.mkdir()
            brain.mkdir()
            cache.mkdir()
            workspace.mkdir()
            store = AntigravitySessionStore(conversations_dir=conv, brain_dir=brain)
            store.cache_root = cache
            session_cache = cache / "cache-only-session"
            session_cache.mkdir()
            (session_cache / "messages.json").write_text('[{"role":"user","content":"hello"}]', encoding="utf-8")
            sessions = store.list_sessions()
            self.assertEqual(sessions[0]["id"], "cache-only-session")
            self.assertTrue(sessions[0]["has_messages"])

    def test_list_sessions_ignores_internal_cache_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv = root / "conversations"
            brain = root / "brain"
            cache = root / "cache"
            conv.mkdir()
            brain.mkdir()
            cache.mkdir()
            store = AntigravitySessionStore(conversations_dir=conv, brain_dir=brain)
            store.cache_root = cache
            (cache / "_pending_uploads").mkdir()
            self.assertEqual(store.list_sessions(), [])

    def test_list_sessions_includes_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv = root / "conversations"
            brain = root / "brain"
            cache = root / "cache"
            workspace = root / "workspace"
            conv.mkdir()
            brain.mkdir()
            cache.mkdir()
            workspace.mkdir()
            store = AntigravitySessionStore(conversations_dir=conv, brain_dir=brain)
            store.cache_root = cache
            session_cache = cache / "session-with-workspace"
            session_cache.mkdir()
            (session_cache / "messages.json").write_text(
                f'[{{"role":"user","content":"@[{workspace}]\\\\nhello"}}]',
                encoding="utf-8",
            )
            sessions = store.list_sessions()
            self.assertEqual(sessions[0]["workspace_path"], str(workspace))

    def test_send_message_returns_session_and_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv = root / "conversations"
            brain = root / "brain"
            cache = root / "cache"
            conv.mkdir()
            brain.mkdir()
            cache.mkdir()
            store = AntigravitySessionStore(conversations_dir=conv, brain_dir=brain)
            store.cache_root = cache
            fake_locator = mock.Mock()
            fake_locator.discover.return_value = object()
            fake_client = mock.Mock()
            fake_client.start_cascade.return_value = "session-1"
            fake_client.send_user_message.return_value = None
            fake_collector = mock.Mock()
            fake_collector.capture_baseline.return_value = 0
            fake_collector.collect.return_value = "hi"
            with mock.patch.object(session_store_module, "RuntimeLocator", return_value=fake_locator), \
                 mock.patch.object(session_store_module, "RuntimeRpcClient", return_value=fake_client), \
                 mock.patch.object(session_store_module, "AnswerCollector", return_value=fake_collector), \
                 mock.patch.object(store, "get_session_messages", return_value=[
                     {"role": "user", "content": "hello"},
                     {"role": "assistant", "content": "hi"},
                 ]):
                result = store.send_message("hello")
            self.assertEqual(result["session_id"], "session-1")
            self.assertTrue(result["created"])
            self.assertEqual(result["answer"], "hi")
            self.assertEqual(result["messages"][-1]["content"], "hi")
            self.assertEqual(result["session"]["title"], "hello")

    def test_send_message_moves_uploads_into_session_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv = root / "conversations"
            brain = root / "brain"
            cache = root / "cache"
            conv.mkdir()
            brain.mkdir()
            cache.mkdir()
            store = AntigravitySessionStore(conversations_dir=conv, brain_dir=brain)
            store.cache_root = cache
            upload = cache / "_pending_uploads" / "20260329"
            upload.mkdir(parents=True)
            pending = upload / "demo.txt"
            pending.write_text("hello upload", encoding="utf-8")
            fake_locator = mock.Mock()
            fake_locator.discover.return_value = object()
            fake_client = mock.Mock()
            fake_client.start_cascade.return_value = "session-1"
            fake_client.send_user_message.return_value = None
            fake_collector = mock.Mock()
            fake_collector.capture_baseline.return_value = 0
            fake_collector.collect.return_value = "ok"
            with mock.patch.object(session_store_module, "RuntimeLocator", return_value=fake_locator), \
                 mock.patch.object(session_store_module, "RuntimeRpcClient", return_value=fake_client), \
                 mock.patch.object(session_store_module, "AnswerCollector", return_value=fake_collector), \
                 mock.patch.object(store, "get_session_messages", return_value=[]):
                store.send_message("hello", attachment_paths=[pending])
            session_upload = cache / "session-1" / "uploads" / "demo.txt"
            self.assertTrue(session_upload.exists())
            files = store.list_session_files("session-1")
            self.assertEqual(files[0]["name"], "demo.txt")
            content = store.get_session_file_content("session-1", "demo.txt")
            self.assertEqual(content["content"], "hello upload")

    def test_steps_to_messages_extracts_user_attachments(self) -> None:
        store = AntigravitySessionStore()
        steps = [{
            "type": "CORTEX_STEP_TYPE_USER_INPUT",
            "metadata": {},
            "userInput": {
                "items": [{"item": {"file": {"absoluteUri": "file:///tmp/demo.png"}}}, {"text": "see attachment"}],
                "userResponse": "@[/tmp/demo.png] see attachment",
            },
        }]
        messages = store._steps_to_messages(steps)
        self.assertEqual(messages[0]["attachments"][0]["path"], "/tmp/demo.png")
        self.assertEqual(messages[0]["attachments"][0]["kind"], "image")

    def test_start_session_send_returns_immediate_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conv = root / "conversations"
            brain = root / "brain"
            cache = root / "cache"
            conv.mkdir()
            brain.mkdir()
            cache.mkdir()
            store = AntigravitySessionStore(conversations_dir=conv, brain_dir=brain)
            store.cache_root = cache
            fake_locator = mock.Mock()
            fake_locator.discover.return_value = object()
            fake_client = mock.Mock()
            fake_client.start_cascade.return_value = "session-fast"
            with mock.patch.object(session_store_module, "RuntimeLocator", return_value=fake_locator), \
                 mock.patch.object(session_store_module, "RuntimeRpcClient", return_value=fake_client), \
                 mock.patch.object(session_store_module.threading, "Thread") as thread_cls:
                result = store.start_session_send("hello")
            self.assertEqual(result["session_id"], "session-fast")
            self.assertTrue(result["created"])
            self.assertEqual(result["messages"][0]["role"], "user")
            thread_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
