from __future__ import annotations

import unittest
from pathlib import Path

from scrapper.runtime_capabilities import selected_lock_versions


ROOT = Path(__file__).resolve().parents[2]


class RuntimeCapabilityContractTests(unittest.TestCase):
    def test_runtime_and_test_locks_use_identical_runtime_pins(self) -> None:
        runtime = selected_lock_versions(
            ROOT / "scrapper" / "requirements.lock", evaluate_markers=False
        )
        test = selected_lock_versions(
            ROOT / "scrapper" / "requirements.test.lock", evaluate_markers=False
        )
        self.assertEqual(runtime, {name: test[name] for name in runtime})
        self.assertEqual(test["pytest"], "9.1.1")

    def test_runtime_lock_contains_declared_capability_packages(self) -> None:
        runtime = selected_lock_versions(
            ROOT / "scrapper" / "requirements.lock", evaluate_markers=False
        )
        self.assertEqual(
            {
                "playwright": "1.63.0",
                "prompt-toolkit": "3.0.53",
                "pytesseract": "0.3.13",
                "rich": "15.0.0",
            },
            {name: runtime[name] for name in ("playwright", "prompt-toolkit", "pytesseract", "rich")},
        )


if __name__ == "__main__":
    unittest.main()
