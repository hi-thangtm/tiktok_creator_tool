import tempfile
import unittest
from pathlib import Path

from core.constants import APP_NAME
from core.paths import (
    chrome_executable_candidates,
    default_paths,
    find_chrome_executable,
)


class PathsTest(unittest.TestCase):
    def test_macos_paths_keep_application_support_location(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            project_root = Path(temp_dir) / "project"

            paths = default_paths(
                home=home,
                project_root=project_root,
                platform_name="darwin",
                environment={},
            )

            self.assertEqual(
                paths.app_support_dir,
                home / "Library" / "Application Support" / APP_NAME,
            )
            self.assertEqual(
                paths.database_path,
                paths.app_support_dir / "data" / "creators.db",
            )
            self.assertEqual(
                paths.browser_data_dir,
                paths.app_support_dir / "browser_data",
            )

    def test_windows_paths_use_local_app_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "Users" / "tester"
            local_app_data = root / "LocalAppData"

            paths = default_paths(
                home=home,
                project_root=root / "project",
                platform_name="win32",
                environment={
                    "LOCALAPPDATA": str(local_app_data),
                },
            )

            self.assertEqual(
                paths.app_support_dir,
                local_app_data / APP_NAME,
            )
            self.assertEqual(
                paths.database_path,
                local_app_data / APP_NAME / "data" / "creators.db",
            )
            self.assertEqual(
                paths.browser_data_dir,
                local_app_data / APP_NAME / "browser_data",
            )
            self.assertEqual(
                paths.logs_dir,
                local_app_data / APP_NAME / "logs",
            )
            self.assertEqual(
                paths.exports_dir,
                home / "Documents" / APP_NAME / "Exports",
            )

    def test_path_overrides_still_win(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_support = root / "custom-support"
            documents = root / "custom-documents"

            paths = default_paths(
                home=root / "home",
                project_root=root / "project",
                platform_name="win32",
                environment={
                    "TIKTOK_CREATOR_TOOL_APP_SUPPORT": str(app_support),
                    "TIKTOK_CREATOR_TOOL_DOCUMENTS": str(documents),
                },
            )

            self.assertEqual(paths.app_support_dir, app_support)
            self.assertEqual(paths.documents_dir, documents)

    def test_windows_chrome_lookup_checks_common_env_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            program_files = root / "Program Files"
            chrome = (
                program_files
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe"
            )
            chrome.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            chrome.touch()

            found = find_chrome_executable(
                platform_name="win32",
                environment={
                    "PROGRAMFILES": str(program_files),
                },
                home=root / "home",
            )

            self.assertEqual(found, chrome)

    def test_windows_chrome_candidates_fall_back_to_home_local_app_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"

            candidates = chrome_executable_candidates(
                platform_name="win32",
                environment={},
                home=home,
            )

            self.assertIn(
                home
                / "AppData"
                / "Local"
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe",
                candidates,
            )


if __name__ == "__main__":
    unittest.main()
