from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot
from playwright.sync_api import (
    Error as PlaywrightError,
    sync_playwright,
)

from core.paths import AppPaths
from services.browser_service import (
    BrowserStatus,
    GoogleChromeNotInstalled,
    context_is_alive,
    is_browser_closed_error,
    launch_chrome_persistent_context,
    open_affiliate_home,
)


class BrowserWorker(QObject):
    status_changed = Signal(str)
    log_message = Signal(str)
    connected = Signal()
    closed = Signal()
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(
        self,
        paths: AppPaths,
    ) -> None:
        super().__init__()
        self.paths = paths
        self._stop_requested = threading.Event()
        self._close_browser_on_stop = True

    def request_stop(
        self,
        close_browser: bool = True,
    ) -> None:
        self._close_browser_on_stop = close_browser
        self._stop_requested.set()

    @Slot()
    def run(self) -> None:
        context = None

        try:
            self.status_changed.emit(
                BrowserStatus.OPENING
            )
            self.log_message.emit(
                "Đang mở Google Chrome thật..."
            )

            with sync_playwright() as playwright:
                context = launch_chrome_persistent_context(
                    playwright,
                    self.paths.browser_data_dir,
                    log=self.log_message.emit,
                )
                page = open_affiliate_home(context)
                self.status_changed.emit(
                    BrowserStatus.CONNECTED
                )
                self.log_message.emit(
                    "Đã mở TikTok Shop Affiliate. "
                    "Hãy đăng nhập thủ công trên Chrome."
                )
                self.log_message.emit(
                    f"Trang hiện tại: {page.url}"
                )
                self.connected.emit()

                while not self._stop_requested.is_set():
                    if not context_is_alive(context):
                        self.status_changed.emit(
                            BrowserStatus.CLOSED
                        )
                        self.log_message.emit(
                            "Trình duyệt đã đóng."
                        )
                        self.closed.emit()
                        return

                    time.sleep(0.5)

                if self._close_browser_on_stop:
                    self.log_message.emit(
                        "Đang đóng trình duyệt theo yêu cầu..."
                    )
                    context.close()
                    self.status_changed.emit(
                        BrowserStatus.CLOSED
                    )
                    self.closed.emit()

        except GoogleChromeNotInstalled as exc:
            self.status_changed.emit(
                BrowserStatus.ERROR
            )
            self.error_occurred.emit(str(exc))

        except PlaywrightError as exc:
            if is_browser_closed_error(exc):
                self.status_changed.emit(
                    BrowserStatus.CLOSED
                )
                self.log_message.emit(
                    "Trình duyệt đã đóng."
                )
                self.closed.emit()
            else:
                self.status_changed.emit(
                    BrowserStatus.ERROR
                )
                self.error_occurred.emit(str(exc))

        except Exception as exc:
            self.status_changed.emit(
                BrowserStatus.ERROR
            )
            self.error_occurred.emit(str(exc))

        finally:
            self.finished.emit()
