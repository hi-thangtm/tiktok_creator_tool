from __future__ import annotations

import random
import threading
import time

from PySide6.QtCore import QObject, Signal, Slot
from playwright.sync_api import (
    Error as PlaywrightError,
    sync_playwright,
)

from core.constants import (
    BATCH_PAUSE_SECONDS,
    BATCH_SIZE,
    CAPTCHA_RECOVERY_WAIT_MS,
    MAX_CREATOR_DELAY,
    MIN_CREATOR_DELAY,
    PROCESS_LIMIT,
    status_label,
)
from core.database import DatabaseRepository
from core.paths import AppPaths
from services.browser_service import (
    BrowserStatus,
    GoogleChromeNotInstalled,
    get_or_create_page,
    is_browser_closed_error,
    launch_chrome_persistent_context,
)
from services.collector_service import (
    captcha_failed_is_visible,
    captcha_is_visible,
)
from services.contact_service import (
    ContactProgress,
    ContactResult,
    DetailErrorAction,
    StopSessionRequested,
    TemporarySkipRequested,
    process_creator,
)


class ContactWorker(QObject):
    browser_status_changed = Signal(str)
    log_message = Signal(str)
    captcha_required = Signal()
    captcha_still_visible = Signal()
    detail_error_required = Signal(str)
    paused = Signal()
    resumed = Signal()
    progress_changed = Signal(dict)
    creator_updated = Signal(str)
    completed = Signal(dict)
    error_occurred = Signal(str)
    stopped = Signal()
    finished = Signal()

    def __init__(
        self,
        paths: AppPaths,
        database_path,
        process_limit: int = PROCESS_LIMIT,
        close_browser_on_finish: bool = True,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.repository = DatabaseRepository(database_path)
        self.process_limit = process_limit
        self.close_browser_on_finish = close_browser_on_finish
        self._stop_requested = threading.Event()
        self._pause_requested = threading.Event()
        self._captcha_check_requested = threading.Event()
        self._detail_error_action_requested = threading.Event()
        self._detail_error_action: DetailErrorAction = "retry"
        self._close_browser_on_stop = True
        self._is_paused = False
        self._current_creator_id: str | None = None
        self._current_nickname: str | None = None
        self._total = 0
        self._current_index = 0
        self._saved_count = 0

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
        self._captcha_check_requested.set()
        self._detail_error_action = "stop"
        self._detail_error_action_requested.set()

    def check_captcha_again(self) -> None:
        self._captcha_check_requested.set()

    def choose_detail_error_action(
        self,
        action: DetailErrorAction,
    ) -> None:
        self._detail_error_action = action
        self._detail_error_action_requested.set()

        if action == "stop":
            self._stop_requested.set()
            self._pause_requested.clear()

    @Slot()
    def run(self) -> None:
        context = None

        try:
            self.repository.init_database()
            reset_count = self.repository.reset_processing_to_pending()

            if reset_count:
                self.log_message.emit(
                    "Đã đưa nhà sáng tạo đang xử lý cũ về chờ xử lý: "
                    f"{reset_count}."
                )

            pending_creators = self.repository.get_pending_creators(
                limit=self.process_limit
            )
            self._total = len(pending_creators)
            self._emit_progress(
                "Khởi tạo",
                creator_id=None,
                nickname=None,
            )
            self.log_message.emit(
                "Lấy liên hệ: "
                f"tổng cần xử lý={len(pending_creators)}, "
                f"giới hạn phiên={self.process_limit}."
            )

            if not pending_creators:
                self.completed.emit(
                    self._completion_payload(
                        stopped=False,
                    )
                )
                return

            self.browser_status_changed.emit(
                BrowserStatus.OPENING
            )
            self.log_message.emit(
                "Phiên lấy liên hệ đang mở Google Chrome thật..."
            )

            with sync_playwright() as playwright:
                context = launch_chrome_persistent_context(
                    playwright,
                    self.paths.browser_data_dir,
                    log=self.log_message.emit,
                )
                page = get_or_create_page(context)
                self.browser_status_changed.emit(
                    BrowserStatus.CONNECTED
                )

                for index, creator_id in enumerate(
                    pending_creators,
                    start=1,
                ):
                    self._current_index = index
                    self._current_creator_id = creator_id
                    self._current_nickname = None
                    self.check_pause_or_stop()
                    self._emit_progress(
                        "Đang mở chi tiết nhà sáng tạo",
                        creator_id=creator_id,
                        nickname=None,
                    )
                    self.log_message.emit(
                        f"[{index}/{self._total}] Mã nhà sáng tạo: "
                        f"{creator_id}"
                    )

                    if self.repository.should_skip_creator(
                        creator_id
                    ):
                        self.log_message.emit(
                            "Nhà sáng tạo đã xử lý -> bỏ qua."
                        )
                        continue

                    saved = False

                    try:
                        result = process_creator(
                            page,
                            self.repository,
                            creator_id,
                            self,
                        )
                        saved = True
                        self._saved_count += 1
                        self._current_nickname = result.nickname
                        self.creator_updated.emit(creator_id)
                        self._emit_progress(
                            "Hoàn tất",
                            creator_id=creator_id,
                            nickname=result.nickname,
                        )
                        self.log_message.emit(
                            _contact_result_to_log(result)
                        )
                        self._current_creator_id = None

                    except TemporarySkipRequested as exc:
                        self.repository.requeue_creator(creator_id)
                        self.creator_updated.emit(creator_id)
                        self.log_message.emit(str(exc))
                        self._current_creator_id = None

                    except StopSessionRequested:
                        self._requeue_current_creator()
                        raise

                    except PlaywrightError as exc:
                        if is_browser_closed_error(exc):
                            self._requeue_current_creator()
                            raise

                        self.repository.save_error(
                            creator_id,
                            exc,
                        )
                        self.creator_updated.emit(creator_id)
                        self.log_message.emit(
                            "Lỗi Playwright ở nhà sáng tạo "
                            f"{creator_id}: {exc}"
                        )
                        self._current_creator_id = None

                    except Exception as exc:
                        self.repository.save_error(
                            creator_id,
                            exc,
                        )
                        self.creator_updated.emit(creator_id)
                        self.log_message.emit(
                            f"Lỗi nhà sáng tạo {creator_id}: {exc}"
                        )
                        self._current_creator_id = None

                    if self._stop_requested.is_set():
                        self._requeue_current_creator()
                        raise StopSessionRequested(
                            "Người dùng dừng phiên lấy liên hệ."
                        )

                    self._creator_delay()

                    if saved:
                        self._batch_pause()

                self.completed.emit(
                    self._completion_payload(
                        stopped=False,
                    )
                )

                if self.close_browser_on_finish and context is not None:
                    self._close_context(context)

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
                self.log_message.emit("Trình duyệt đã đóng.")
                self.stopped.emit()
            else:
                self.browser_status_changed.emit(
                    BrowserStatus.ERROR
                )
                self.error_occurred.emit(str(exc))

        except Exception as exc:
            self.browser_status_changed.emit(
                BrowserStatus.ERROR
            )
            self.error_occurred.emit(str(exc))

        finally:
            if (
                self._current_creator_id
                and self._stop_requested.is_set()
            ):
                self._requeue_current_creator()

            self.finished.emit()

    def check_pause_or_stop(self) -> None:
        if self._stop_requested.is_set():
            raise StopSessionRequested(
                "Người dùng dừng phiên lấy liên hệ."
            )

        if self._pause_requested.is_set():
            if not self._is_paused:
                self._is_paused = True
                self.paused.emit()
                self.log_message.emit(
                    "Phiên lấy liên hệ đã tạm dừng."
                )

            while self._pause_requested.is_set():
                if self._stop_requested.is_set():
                    raise StopSessionRequested(
                        "Người dùng dừng phiên lấy liên hệ."
                    )
                time.sleep(0.2)

            self._is_paused = False
            self.resumed.emit()
            self.log_message.emit(
                "Phiên lấy liên hệ tiếp tục."
            )

    def wait_for_captcha(self, page) -> None:
        self.log_message.emit(
            "TikTok yêu cầu CAPTCHA. "
            "Công cụ tạm dừng tại nhà sáng tạo hiện tại."
        )
        self.captcha_required.emit()

        while True:
            if self._stop_requested.is_set():
                raise StopSessionRequested(
                    "Người dùng dừng phiên trong lúc CAPTCHA."
                )

            self._captcha_check_requested.wait(0.25)

            if not self._captcha_check_requested.is_set():
                continue

            self._captcha_check_requested.clear()

            if self._stop_requested.is_set():
                raise StopSessionRequested(
                    "Người dùng dừng phiên trong lúc CAPTCHA."
                )

            page.wait_for_timeout(1500)

            if (
                captcha_is_visible(page)
                or captcha_failed_is_visible(page)
            ):
                self.captcha_still_visible.emit()
                continue

            self.log_message.emit(
                "CAPTCHA đã biến mất. Đang chờ trang chi tiết nhà sáng tạo phục hồi..."
            )
            page.wait_for_timeout(CAPTCHA_RECOVERY_WAIT_MS)

            if (
                captcha_is_visible(page)
                or captcha_failed_is_visible(page)
            ):
                self.captcha_still_visible.emit()
                continue

            return

    def wait_for_detail_error(
        self,
        page,
        message: str,
    ) -> DetailErrorAction:
        self.log_message.emit(message)
        self._detail_error_action = "retry"
        self._detail_error_action_requested.clear()
        self.detail_error_required.emit(message)

        while True:
            if self._stop_requested.is_set():
                return "stop"

            self._detail_error_action_requested.wait(0.25)

            if not self._detail_error_action_requested.is_set():
                continue

            self._detail_error_action_requested.clear()
            action = self._detail_error_action

            if action == "retry":
                page.wait_for_timeout(1000)
                return "retry"

            return action

    def emit_step(
        self,
        step: str,
        creator_id: str | None = None,
        nickname: str | None = None,
    ) -> None:
        if creator_id is None:
            creator_id = self._current_creator_id

        if nickname is not None:
            self._current_nickname = nickname
        else:
            nickname = self._current_nickname

        self._emit_progress(
            step,
            creator_id=creator_id,
            nickname=nickname,
        )

    def log(
        self,
        message: str,
    ) -> None:
        self.log_message.emit(message)

    def _emit_progress(
        self,
        step: str,
        creator_id: str | None,
        nickname: str | None,
    ) -> None:
        progress = ContactProgress(
            current=self._current_index,
            total=self._total,
            creator_id=creator_id,
            nickname=nickname,
            step=step,
            saved=self._saved_count,
        )
        self.progress_changed.emit(
            _progress_to_dict(progress)
        )

    def _creator_delay(self) -> None:
        seconds = random.uniform(
            MIN_CREATOR_DELAY,
            MAX_CREATOR_DELAY,
        )
        self.log_message.emit(
            f"Nghỉ {seconds:.1f} giây trước nhà sáng tạo tiếp theo..."
        )
        self._interruptible_sleep(seconds)

    def _batch_pause(self) -> None:
        if self._saved_count <= 0:
            return

        if self._saved_count % BATCH_SIZE != 0:
            return

        self.log_message.emit(
            f"Đã xử lý {self._saved_count} nhà sáng tạo. "
            f"Tạm nghỉ {BATCH_PAUSE_SECONDS} giây."
        )
        self._interruptible_sleep(BATCH_PAUSE_SECONDS)

    def _interruptible_sleep(
        self,
        seconds: float,
    ) -> None:
        deadline = time.monotonic() + seconds

        while time.monotonic() < deadline:
            self.check_pause_or_stop()
            time.sleep(
                min(0.2, deadline - time.monotonic())
            )

    def _requeue_current_creator(self) -> None:
        if not self._current_creator_id:
            return

        self.repository.requeue_creator(
            self._current_creator_id
        )
        self.creator_updated.emit(
            self._current_creator_id
        )
        self.log_message.emit(
            "Nhà sáng tạo hiện tại đã đưa về chờ xử lý: "
            f"{self._current_creator_id}"
        )

    def _close_context_if_requested(
        self,
        context,
    ) -> None:
        if self._close_browser_on_stop:
            self._close_context(context)

    def _close_context(
        self,
        context,
    ) -> None:
        try:
            context.close()
        except Exception as exc:
            if not is_browser_closed_error(exc):
                self.log_message.emit(
                    f"Không đóng được trình duyệt sạch sẽ: {exc}"
                )

        self.browser_status_changed.emit(
            BrowserStatus.CLOSED
        )

    def _completion_payload(
        self,
        stopped: bool,
    ) -> dict:
        return {
            "total": self._total,
            "saved": self._saved_count,
            "stopped": stopped,
            "status_counts": self.repository.count_by_status(),
        }


def _progress_to_dict(
    progress: ContactProgress,
) -> dict:
    return {
        "current": progress.current,
        "total": progress.total,
        "creator_id": progress.creator_id,
        "nickname": progress.nickname,
        "step": progress.step,
        "saved": progress.saved,
    }


def _contact_result_to_log(
    result: ContactResult,
) -> str:
    return (
        "Đã lưu nhà sáng tạo "
        f"{result.creator_id}: "
        f"trạng thái={status_label(result.status)}, "
        f"SĐT={result.phone_count}, "
        f"Email={result.email_count}."
    )
