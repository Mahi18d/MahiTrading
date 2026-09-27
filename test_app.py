"""Regression checks for the single-file Mahi Trading Streamlit app."""

from pathlib import Path
import logging
import py_compile
import subprocess
import sys
import unittest


APP_PATH = Path(__file__).with_name("app.py")


class MahiTradingAppTests(unittest.TestCase):
    def test_syntax_smoke(self):
        """The complete Streamlit file must compile before it is started."""
        py_compile.compile(str(APP_PATH), doraise=True)

    def test_directional_and_broker_loader_self_tests(self):
        """Runs BUY/SELL, disconnected, and mocked-connected broker checks."""
        result = subprocess.run(
            [sys.executable, str(APP_PATH), "--self-test"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("self-tests passed", result.stdout)

    def test_streamlit_import_smoke(self):
        """Executes the complete app in Streamlit's test runtime without a browser."""
        from streamlit.testing.v1 import AppTest

        context_logger = logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context")
        previous_disabled = context_logger.disabled
        try:
            # AppTest intentionally runs outside a browser context; this one
            # framework-only warning is not an application error.
            context_logger.disabled = True
            app_test = AppTest.from_file(str(APP_PATH))
            app_test.run(timeout=45)
        finally:
            context_logger.disabled = previous_disabled
        self.assertFalse(app_test.exception, msg=str(app_test.exception))

    def test_no_deprecated_container_width_calls(self):
        """Keep the UI free of Streamlit's removed container-width argument."""
        self.assertNotIn("use_container_width", APP_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
