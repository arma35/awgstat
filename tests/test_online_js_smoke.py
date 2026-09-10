from __future__ import annotations

import unittest
from pathlib import Path


class OnlineJsSmokeTests(unittest.TestCase):
    def test_dynamic_period_support_is_present(self) -> None:
        script = Path(__file__).resolve().parents[1] / "online.js"
        content = script.read_text(encoding="utf-8")
        self.assertIn("function optionBounds", content)
        self.assertIn("function showDynamicPreset", content)
        self.assertIn('data-from-epoch', content)
        self.assertIn('traffic-history.json', content)


if __name__ == "__main__":
    unittest.main()
