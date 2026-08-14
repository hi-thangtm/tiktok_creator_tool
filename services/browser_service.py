from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
)

from core.constants import AFFILIATE_URL
from core.paths import (
    find_chrome_executable,
    google_chrome_install_hint,
    google_chrome_is_installed,
)


class BrowserStatus:
    NOT_OPEN = "Chưa mở"
    OPENING = "Đang mở Chrome"
    CONNECTED = "Đã kết nối"
    CLOSED = "Trình duyệt đã đóng"
    ERROR = "Lỗi"


class GoogleChromeNotInstalled(RuntimeError):
    pass


def build_persistent_context_options(
    user_data_dir: Path,
) -> dict[str, Any]:
    return {
        "user_data_dir": str(user_data_dir),
        "headless": False,
        "viewport": {
            "width": 1440,
            "height": 900,
        },
    }


def chrome_launch_attempts() -> tuple[dict[str, Any], ...]:
    return (
        {
            "channel": "chrome",
            "chromium_sandbox": True,
        },
        {
            "channel": "chrome",
        },
    )


def launch_chrome_persistent_context(
    playwright,
    user_data_dir: Path,
    log: Callable[[str], None] | None = None,
) -> BrowserContext:
    if not google_chrome_is_installed():
        raise GoogleChromeNotInstalled(
            "Google Chrome chưa được cài đặt. "
            "Vui lòng cài Google Chrome trước khi sử dụng.\n\n"
            "Các vị trí đã kiểm tra:\n"
            f"{google_chrome_install_hint()}"
        )

    user_data_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    common_options = build_persistent_context_options(
        user_data_dir
    )
    errors: list[str] = []

    for index, extra_options in enumerate(
        chrome_launch_attempts(),
        start=1,
    ):
        try:
            if log:
                if index == 1:
                    log(
                        "Đang mở Google Chrome với persistent profile..."
                    )
                    executable = find_chrome_executable()

                    if executable:
                        log(
                            f"Google Chrome: {executable}"
                        )
                else:
                    log(
                        "Thử lại Google Chrome với cấu hình tương thích..."
                    )

            return (
                playwright.chromium
                .launch_persistent_context(
                    **common_options,
                    **extra_options,
                )
            )

        except Exception as exc:
            errors.append(
                f"Lan {index}: {exc}"
            )

    raise RuntimeError(
        "Không mở được Google Chrome.\n"
        + "\n".join(errors)
    )


def get_or_create_page(
    context: BrowserContext,
) -> Page:
    return (
        context.pages[0]
        if context.pages
        else context.new_page()
    )


def open_affiliate_home(
    context: BrowserContext,
) -> Page:
    page = get_or_create_page(context)
    page.goto(
        AFFILIATE_URL,
        wait_until="domcontentloaded",
        timeout=120_000,
    )

    return page


def is_browser_closed_error(
    exc: Exception,
) -> bool:
    text = str(exc).lower()
    patterns = (
        "target page, context or browser has been closed",
        "browser has been closed",
        "context has been closed",
        "page has been closed",
        "target closed",
    )

    return any(
        pattern in text
        for pattern in patterns
    )


def context_is_alive(
    context: BrowserContext,
) -> bool:
    try:
        context.pages
        return True
    except PlaywrightError as exc:
        if is_browser_closed_error(exc):
            return False

        raise
