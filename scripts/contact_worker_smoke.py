from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import (
    QObject,
    QCoreApplication,
    QThread,
    QTimer,
)

from core.database import DatabaseRepository
from core.paths import default_paths
from workers.contact_worker import ContactWorker


class ContactSmokeRunner(QObject):
    def __init__(
        self,
        limit: int,
        max_seconds: int,
    ) -> None:
        super().__init__()
        self.paths = default_paths()
        self.repository = DatabaseRepository(
            self.paths.database_path
        )
        self.limit = limit
        self.max_seconds = max_seconds
        self.thread: QThread | None = None
        self.worker: ContactWorker | None = None
        self.before_counts: dict[str, int] = {}
        self.after_counts: dict[str, int] = {}
        self.updated_creator_ids: list[str] = []
        self.progress_events: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error_message: str | None = None
        self.captcha_required = False
        self.timed_out = False
        self.stopped = False

    def start(self) -> None:
        self.repository.init_database()
        self.before_counts = self.repository.count_by_status()

        self.thread = QThread(self)
        self.worker = ContactWorker(
            paths=self.paths,
            database_path=self.paths.database_path,
            process_limit=self.limit,
        )
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.browser_status_changed.connect(
            lambda status: print(f"BROWSER_STATUS={status}")
        )
        self.worker.log_message.connect(
            lambda message: print(f"LOG={message}")
        )
        self.worker.progress_changed.connect(
            self._on_progress
        )
        self.worker.creator_updated.connect(
            self._on_creator_updated
        )
        self.worker.completed.connect(
            self._on_completed
        )
        self.worker.stopped.connect(
            self._on_stopped
        )
        self.worker.error_occurred.connect(
            self._on_error
        )
        self.worker.captcha_required.connect(
            self._on_captcha_required
        )
        self.worker.detail_error_required.connect(
            self._on_detail_error_required
        )
        self.worker.finished.connect(
            self.thread.quit
        )
        self.worker.finished.connect(
            self.worker.deleteLater
        )
        self.thread.finished.connect(
            self.thread.deleteLater
        )
        self.thread.finished.connect(
            self._on_finished
        )

        QTimer.singleShot(
            self.max_seconds * 1000,
            self._on_timeout,
        )
        self.thread.start()

    def _on_progress(
        self,
        progress: dict[str, Any],
    ) -> None:
        self.progress_events.append(progress)
        print(
            "PROGRESS="
            f"{progress.get('current')}/{progress.get('total')}|"
            f"{progress.get('creator_id')}|"
            f"{progress.get('nickname')}|"
            f"{progress.get('step')}|"
            f"saved={progress.get('saved')}"
        )

    def _on_creator_updated(
        self,
        creator_id: str,
    ) -> None:
        self.updated_creator_ids.append(creator_id)
        print(f"CREATOR_UPDATED={creator_id}")

    def _on_completed(
        self,
        result: dict[str, Any],
    ) -> None:
        self.result = result
        print(f"COMPLETED={result}")

    def _on_stopped(self) -> None:
        self.stopped = True
        print("STOPPED=1")

    def _on_error(
        self,
        message: str,
    ) -> None:
        self.error_message = message
        print(f"ERROR={message}")

    def _on_captcha_required(self) -> None:
        self.captcha_required = True
        print("CAPTCHA_REQUIRED=1")

        if self.worker:
            self.worker.request_stop(
                close_browser=True,
            )

    def _on_detail_error_required(
        self,
        message: str,
    ) -> None:
        print(f"DETAIL_ERROR_REQUIRED={message}")

        if self.worker:
            self.worker.choose_detail_error_action(
                "skip"
            )

    def _on_timeout(self) -> None:
        if not self.worker:
            return

        self.timed_out = True
        print("TIMEOUT=1")
        self.worker.request_stop(
            close_browser=True,
        )

    def _on_finished(self) -> None:
        self.after_counts = self.repository.count_by_status()
        print(f"DB_PATH={self.paths.database_path}")
        print(f"BEFORE_STATUS={self.before_counts}")
        print(f"AFTER_STATUS={self.after_counts}")
        print(f"UPDATED_CREATORS={self.updated_creator_ids}")

        if self.error_message:
            exit_code = 1
        elif self.timed_out:
            exit_code = 2
        elif self.captcha_required:
            exit_code = 3
        elif len(self.updated_creator_ids) < self.limit:
            exit_code = 4
        else:
            exit_code = 0

        QCoreApplication.instance().exit(exit_code)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a small real ContactWorker smoke test."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=180,
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QCoreApplication(sys.argv)
    runner = ContactSmokeRunner(
        limit=args.limit,
        max_seconds=args.max_seconds,
    )
    QTimer.singleShot(0, runner.start)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
