import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime_cli.ag_runtime import RuntimeRpcClient


class RuntimePayloadTests(unittest.TestCase):
    def test_build_items_with_attachments(self) -> None:
        first = Path("/tmp/demo.txt")
        second = Path("/tmp/demo.png")
        items = RuntimeRpcClient.build_items("hello", [first, second])
        self.assertEqual(
            items,
            [
                {"item": {"file": {"absoluteUri": first.resolve().as_uri()}}},
                {"item": {"file": {"absoluteUri": second.resolve().as_uri()}}},
                {"text": "hello"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
