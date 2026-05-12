import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui import SessionUIHandler


class SessionUISurfaceTests(unittest.TestCase):
    def test_main_ui_contains_chat_composer_controls(self) -> None:
        html = SessionUIHandler._html(SessionUIHandler)
        required = [
            'id="composer-input"',
            'id="model-select"',
            'id="file-trigger-btn"',
            'id="new-chat-btn"',
            'id="send-btn"',
            "/api/chat/stream",
            "Send as new chat",
        ]
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_main_ui_is_not_the_pb_artifact_browser(self) -> None:
        html = SessionUIHandler._html(SessionUIHandler)
        self.assertNotIn("pb inspect", html)
        self.assertNotIn("_pb_inspect.json", html)


if __name__ == "__main__":
    unittest.main()
