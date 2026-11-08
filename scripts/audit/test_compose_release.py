import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check_compose_release.py")
SPEC = importlib.util.spec_from_file_location("check_compose_release", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


class ComposeReleaseContractTests(unittest.TestCase):
    def test_prepared_production_and_development_contracts(self):
        image_reference = checker.validate_identities()
        checker.validate_production(
            checker.compose_config("compose.yml"),
            checker.EXPECTED_PRODUCTION_SERVICES,
            image_reference,
        )
        checker.validate_development(
            checker.compose_config("compose.yml", "compose.development.yml"),
            checker.EXPECTED_PRODUCTION_SERVICES,
        )
        checker.validate_production(
            checker.compose_config("compose.browser-recovery.yml"),
            checker.EXPECTED_BROWSER_SERVICES,
            image_reference,
        )
        checker.validate_development(
            checker.compose_config(
                "compose.browser-recovery.yml",
                "compose.browser-recovery.development.yml",
            ),
            checker.EXPECTED_BROWSER_SERVICES,
        )


if __name__ == "__main__":
    unittest.main()
