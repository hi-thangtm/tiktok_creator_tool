import tempfile
import unittest
from pathlib import Path

from services.browser_service import (
    BrowserStatus,
    build_persistent_context_options,
    chrome_launch_attempts,
    is_browser_closed_error,
)


class BrowserServiceTest(unittest.TestCase):
    def test_context_options_use_requested_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            user_data_dir = Path(temp_dir) / "browser_data"
            options = build_persistent_context_options(user_data_dir)

            self.assertEqual(
                options["user_data_dir"],
                str(user_data_dir),
            )
            self.assertFalse(options["headless"])
            self.assertEqual(
                options["viewport"],
                {
                    "width": 1440,
                    "height": 900,
                },
            )

    def test_launch_attempts_use_real_google_chrome_channel_only(self):
        attempts = chrome_launch_attempts()

        self.assertEqual(len(attempts), 2)

        for attempt in attempts:
            self.assertEqual(
                attempt["channel"],
                "chrome",
            )

        self.assertNotIn(
            {},
            attempts,
        )

    def test_browser_closed_error_detection(self):
        self.assertTrue(
            is_browser_closed_error(
                RuntimeError("Target page, context or browser has been closed")
            )
        )
        self.assertFalse(
            is_browser_closed_error(
                RuntimeError("Some other Playwright error")
            )
        )

    def test_status_labels(self):
        self.assertEqual(
            BrowserStatus.NOT_OPEN,
            "Chưa mở",
        )
        self.assertEqual(
            BrowserStatus.OPENING,
            "Đang mở Chrome",
        )
        self.assertEqual(
            BrowserStatus.CONNECTED,
            "Đã kết nối",
        )


if __name__ == "__main__":
    unittest.main()
