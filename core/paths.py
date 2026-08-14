"""Filesystem paths for development and packaged cross-platform builds."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .constants import APP_NAME


@dataclass(frozen=True)
class AppPaths:
    project_root: Path
    app_support_dir: Path
    documents_dir: Path

    @property
    def data_dir(self) -> Path:
        return self.app_support_dir / "data"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "creators.db"

    @property
    def browser_data_dir(self) -> Path:
        return self.app_support_dir / "browser_data"

    @property
    def logs_dir(self) -> Path:
        return self.app_support_dir / "logs"

    @property
    def settings_path(self) -> Path:
        return self.app_support_dir / "settings.json"

    @property
    def exports_dir(self) -> Path:
        return self.documents_dir / APP_NAME / "Exports"

    @property
    def legacy_database_path(self) -> Path:
        return self.project_root / "data" / "creators.db"

    @property
    def legacy_browser_data_dir(self) -> Path:
        return self.project_root / "browser_data"


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _platform_key(
    platform_name: str | None = None,
) -> str:
    value = platform_name or sys.platform

    if value.startswith("darwin"):
        return "darwin"

    if value.startswith("win"):
        return "win32"

    if value.startswith("linux"):
        return "linux"

    return value


def _env_path(
    environment: Mapping[str, str],
    key: str,
) -> Path | None:
    value = environment.get(key)

    if not value:
        return None

    return Path(value).expanduser()


def default_paths(
    home: Path | None = None,
    project_root: Path | None = None,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> AppPaths:
    home = home or Path.home()
    project_root = project_root or get_project_root()
    platform_key = _platform_key(platform_name)
    if environment is None:
        environment = os.environ

    app_support_override = environment.get(
        "TIKTOK_CREATOR_TOOL_APP_SUPPORT"
    )

    if app_support_override:
        app_support_dir = Path(app_support_override).expanduser()
    elif platform_key == "win32":
        local_app_data = (
            _env_path(environment, "LOCALAPPDATA")
            or home / "AppData" / "Local"
        )
        app_support_dir = local_app_data / APP_NAME
    elif platform_key == "darwin":
        app_support_dir = (
            home
            / "Library"
            / "Application Support"
            / APP_NAME
        )
    else:
        data_home = (
            _env_path(environment, "XDG_DATA_HOME")
            or home / ".local" / "share"
        )
        app_support_dir = data_home / APP_NAME

    documents_override = environment.get(
        "TIKTOK_CREATOR_TOOL_DOCUMENTS"
    )

    if documents_override:
        documents_dir = Path(documents_override).expanduser()
    else:
        documents_dir = home / "Documents"

    return AppPaths(
        project_root=project_root,
        app_support_dir=app_support_dir,
        documents_dir=documents_dir,
    )


def ensure_app_directories(paths: AppPaths) -> None:
    for directory in (
        paths.app_support_dir,
        paths.data_dir,
        paths.browser_data_dir,
        paths.logs_dir,
        paths.exports_dir,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def chrome_executable_candidates(
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, ...]:
    platform_key = _platform_key(platform_name)
    if environment is None:
        environment = os.environ
    home = home or Path.home()
    candidates: list[Path] = []

    if platform_key == "darwin":
        candidates.extend(
            [
                Path(
                    "/Applications/Google Chrome.app"
                )
                / "Contents"
                / "MacOS"
                / "Google Chrome",
                home
                / "Applications"
                / "Google Chrome.app"
                / "Contents"
                / "MacOS"
                / "Google Chrome",
            ]
        )
    elif platform_key == "win32":
        for env_key in (
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "LOCALAPPDATA",
        ):
            base_path = _env_path(
                environment,
                env_key,
            )

            if base_path:
                candidates.append(
                    base_path
                    / "Google"
                    / "Chrome"
                    / "Application"
                    / "chrome.exe"
                )

        if not environment.get("LOCALAPPDATA"):
            candidates.append(
                home
                / "AppData"
                / "Local"
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe"
            )
    elif platform_key == "linux":
        candidates.extend(
            [
                Path("/usr/bin/google-chrome"),
                Path("/usr/bin/google-chrome-stable"),
                Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"),
            ]
        )

    return _dedupe_paths(candidates)


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()

    for path in paths:
        key = str(path).casefold()

        if key in seen:
            continue

        seen.add(key)
        result.append(path)

    return tuple(result)


def find_chrome_executable(
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    for candidate in chrome_executable_candidates(
        platform_name=platform_name,
        environment=environment,
        home=home,
    ):
        if candidate.exists():
            return candidate

    return None


def google_chrome_app_path() -> Path:
    if _platform_key() == "darwin":
        return Path("/Applications/Google Chrome.app")

    executable = find_chrome_executable()

    if executable:
        return executable

    candidates = chrome_executable_candidates()

    if candidates:
        return candidates[0]

    return Path("Google Chrome")


def google_chrome_install_hint(
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> str:
    platform_key = _platform_key(platform_name)
    candidates = list(
        chrome_executable_candidates(
            platform_name=platform_key,
            environment=environment,
            home=home,
        )
    )

    if platform_key == "darwin":
        candidates.insert(
            0,
            Path("/Applications/Google Chrome.app"),
        )

    if not candidates:
        return "Google Chrome"

    return "\n".join(
        str(path)
        for path in _dedupe_paths(candidates)
    )


def google_chrome_is_installed() -> bool:
    return find_chrome_executable() is not None
