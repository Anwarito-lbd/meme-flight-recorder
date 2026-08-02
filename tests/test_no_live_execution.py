import unittest
from pathlib import Path


class NoLiveExecutionTests(unittest.TestCase):
    def test_source_contains_no_live_execution_or_secret_handling(self):
        root = Path(__file__).parents[1] / "src"
        source = "\n".join(path.read_text() for path in root.rglob("*.py"))
        forbidden = (
            "send" + "Transaction",
            "private" + "_key",
            "seed" + "_phrase",
            'execution_mode == "live"',
        )
        for marker in forbidden:
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
