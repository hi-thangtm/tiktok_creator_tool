from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot
from playwright.sync_api import (
    Error as PlaywrightError,
    sync_playwright,
)

from core.constants import QUEUE_TARGET
from core.database import DatabaseRepository
from core.paths import AppPaths
from services.browser_service import (
    BrowserStatus,
    GoogleChromeNotInstalled,
    context_is_alive,
    is_browser_closed_error,
    launch_chrome_persistent_context,
    open_affiliate_home,
)
from services.collector_service import (
    CollectorProgress,
    CollectorResult,
    StopSessionRequested,
    captcha_is_visible,
    collect_until_queue_target,
)


class CollectorWorker(QObject):
    browser_status_changed = Signal(str)
    log_message = Signal(str)
    ready_prompt_required = Signal()
    captcha_required = Signal()
    captcha_still_visible = Signal()
    paused = Signal()
    resumed = Signal()
    progress_changed = Signal(dict)
    creator_inserted = Signal(str)
    completed = Signal(dict)
    error_occurred = Signal(str)
    stopped = Signal()
    finished = Signal()

    def __init__(
        self,
        paths: AppPaths,
        database_path,
        target: int = QUEUE_TARGET,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.repository = DatabaseRepository(database_path)
        self.target = target
        self._list_ready = threading.Event()
        self._stop_requested = threading.Event()
        self._pause_requested = threading.Event()
        self._captcha_check_requested = threading.Event()
        self._close_browser_on_stop = True
        self._is_paused = False

    def mark_list_ready(self) -> None:
        self._list_ready.set()

    def request_pause(self) -> None:
        self._pause_requested.set()

    def request_resume(self) -> None:
        self._pause_requested.clear()

    def request_stop(
        self,
        close_browser: bool = True,
    ) -> None:
        self._close_browser_on_stop = close_browser
        self._stop_requested.set()
        self._pause_requested.clear()
        self._list_ready.set()
        self._captcha_check_requested.set()

    def check_captcha_again(self) -> None:
        self._captcha_check_requested.set()

    @Slot()
    def run(self) -> None:
        context = None

        try:
            self.browser_status_changed.emit(
                BrowserStatus.OPENING
            )
            self.log_message.emit(
                "Phiên quét đang mở Google Chrome thật..."
            )

            with sync_playwright() as playwright:
                context = launch_chrome_persistent_context(
                    playwright,
                    self.paths.browser_data_dir,
                    log=self.log_message.emit,
                )
                page = open_affiliate_home(context)
                self.browser_status_changed.emit(
                    BrowserStatus.CONNECTED
                )
                self.log_message.emit(
                    "Chrome đã mở. Hãy vào trang Tìm nhà sáng tạo "
                    "và áp dụng bộ lọc mong muốn."
                )
                self.log_message.emit(
                    f"Trang hiện tại: {page.url}"
                )
                self.ready_prompt_required.emit()
                self._wait_for_list_ready()

                if self._stop_requested.is_set():
                    raise StopSessionRequested(
                        "Người dùng dừng phiên quét trước khi bắt đầu."
                    )

                result = collect_until_queue_target(
                    page,
                    self.repository,
                    self,
                    target=self.target,
                )
                self.completed.emit(
                    _result_to_dict(result)
                )
                self.log_message.emit(
                    "Phiên quét hoàn tất: "
                    f"mới={result.new_added}, "
                    f"cũ bỏ qua={result.old_skipped}, "
                    f"chờ xử lý={result.pending_total}."
                )
                self._wait_after_completion_or_error(context)

        except StopSessionRequested as exc:
            self.log_message.emit(str(exc))
            self.stopped.emit()

            if context is not None:
                self._close_context_if_requested(context)

        except GoogleChromeNotInstalled as exc:
            self.browser_status_changed.emit(
                BrowserStatus.ERROR
            )
            self.error_occurred.emit(str(exc))

        except PlaywrightError as exc:
            if is_browser_closed_error(exc):
                self.browser_status_changed.emit(
                    BrowserStatus.CLOSED
                )
                self.log_message.emit(
                    "Trình duyệt đã đóng."
                )
                self.stopped.emit()
            else:
                self.browser_status_changed.emit(
                    BrowserStatus.ERROR
                )
                self.error_occurred.emit(str(exc))

                if context is not None:
                    self._wait_after_completion_or_error(context)

        except Exception as exc:
            self.browser_status_changed.emit(
                BrowserStatus.ERROR
            )
            self.error_occurred.emit(str(exc))

            if context is not None:
                self._wait_after_completion_or_error(context)

        finally:
            self.finished.emit()

    def _wait_for_list_ready(self) -> None:
        while not self._list_ready.wait(0.25):
            if self._stop_requested.is_set():
                raise StopSessionRequested(
                    "Người dùng dừng phiên quét."
                )

    def check_pause_or_stop(self) -> None:
        if self._stop_requested.is_set():
            raise StopSessionRequested(
                "Người dùng dừng phiên quét an toàn."
            )

        if self._pause_requested.is_set():
            if not self._is_paused:
                self._is_paused = True
                self.paused.emit()
                self.log_message.emit("Phiên quét đã tạm dừng.")

            while self._pause_requested.is_set():
                if self._stop_requested.is_set():
                    raise StopSessionRequested(
                        "Người dùng dừng phiên quét an toàn."
                    )
                time.sleep(0.2)

            self._is_paused = False
            self.resumed.emit()
            self.log_message.emit("Phiên quét tiếp tục.")

    def wait_for_captcha(self, page) -> None:
        self.log_message.emit(
            "TikTok yêu cầu xác minh. "
            "Hãy xử lý CAPTCHA thủ công trên Chrome."
        )
        self.captcha_required.emit()

        while True:
            if self._stop_requested.is_set():
                raise StopSessionRequested(
                    "Người dùng dừng phiên quét trong lúc CAPTCHA."
                )

            self._captcha_check_requested.wait(0.25)

            if not self._captcha_check_requested.is_set():
                continue

            self._captcha_check_requested.clear()

            if self._stop_requested.is_set():
                raise StopSessionRequested(
                    "Người dùng dừng phiên quét trong lúc CAPTCHA."
                )

            page.wait_for_timeout(1500)

            if captcha_is_visible(page):
                self.captcha_still_visible.emit()
                continue

            self.log_message.emit(
                "CAPTCHA đã biến mất. Phiên quét tiếp tục."
            )
            page.wait_for_timeout(2500)

            if captcha_is_visible(page):
                self.captcha_still_visible.emit()
                continue

            return

    def sleep(
        self,
        seconds: float,
    ) -> None:
        deadline = time.monotonic() + seconds

        while time.monotonic() < deadline:
            self.check_pause_or_stop()
            time.sleep(min(0.2, deadline - time.monotonic()))

    def log(
        self,
        message: str,
    ) -> None:
        self.log_message.emit(message)

    def emit_progress(
        self,
        progress: CollectorProgress,
    ) -> None:
        self.progress_changed.emit(
            _progress_to_dict(progress)
        )

    def emit_creator_inserted(
        self,
        creator_id: str,
    ) -> None:
        self.creator_inserted.emit(creator_id)

    def _wait_after_completion_or_error(
        self,
        context,
    ) -> None:
        self.log_message.emit(
            "Chrome vẫn đang mở. Nhấn DỪNG AN TOÀN "
            "để đóng phiên quét."
        )

        while not self._stop_requested.is_set():
            if not context_is_alive(context):
                self.browser_status_changed.emit(
                    BrowserStatus.CLOSED
                )
                return

            time.sleep(0.5)

        self._close_context_if_requested(context)

    def _close_context_if_requested(
        self,
        context,
    ) -> None:
        if self._close_browser_on_stop:
            try:
                context.close()
            except PlaywrightError as exc:
                text = str(exc).lower()

                if (
                    "event loop is closed" not in text
                    and not is_browser_closed_error(exc)
                ):
                    self.log_message.emit(
                        f"Không đóng được trình duyệt sạch sẽ: {exc}"
                    )

            self.browser_status_changed.emit(
                BrowserStatus.CLOSED
            )


def _progress_to_dict(
    progress: CollectorProgress,
) -> dict:
    return {
        "round_number": progress.round_number,
        "visible_count": progress.visible_count,
        "old_count": progress.old_count,
        "inserted_count": progress.inserted_count,
        "total_old": progress.total_old,
        "total_new": progress.total_new,
        "pending_total": progress.pending_total,
        "target": progress.target,
        "fast_mode": progress.fast_mode,
        "scroll_top": progress.scroll_top,
        "scroll_height": progress.scroll_height,
    }


def _result_to_dict(
    result: CollectorResult,
) -> dict:
    return {
        "new_added": result.new_added,
        "pending_total": result.pending_total,
        "old_skipped": result.old_skipped,
    }
