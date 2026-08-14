from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import tempfile
from pathlib import Path


def ensure_qt_runtime() -> None:
    try:
        import PySide6.QtWidgets  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    if getattr(sys, "frozen", False):
        raise ModuleNotFoundError(
            "PySide6 không có trong bản ứng dụng đã đóng gói."
        )

    project_root = Path(__file__).resolve().parent
    venv_dir = project_root / ".venv"
    if os.name == "nt":
        venv_python = venv_dir / "Scripts" / "python.exe"
        install_command = (
            ".venv\\Scripts\\pip install -r requirements.txt"
        )
    else:
        venv_python = venv_dir / "bin" / "python"
        install_command = (
            ".venv/bin/pip install -r requirements.txt"
        )

    if (
        venv_python.exists()
        and Path(sys.prefix).resolve() != venv_dir.resolve()
        and os.environ.get("TIKTOK_CREATOR_TOOL_BOOTSTRAPPED") != "1"
    ):
        environment = dict(os.environ)
        environment["TIKTOK_CREATOR_TOOL_BOOTSTRAPPED"] = "1"
        os.execve(
            str(venv_python),
            [
                str(venv_python),
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ],
            environment,
        )

    raise ModuleNotFoundError(
        "PySide6 chưa được cài. Cài dependencies bằng lệnh: "
        f"{install_command}"
    )


ensure_qt_runtime()

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from core.database import (
    DatabaseRepository,
    migrate_legacy_database,
    sqlite_integrity_check,
)
from core.paths import (
    AppPaths,
    default_paths,
    ensure_app_directories,
    find_chrome_executable,
    google_chrome_is_installed,
)
from core.version import APP_VERSION
from services.export_service import (
    NoPhoneCreatorsToExport,
    export_creators_to_excel,
)
from ui.creator_detail_dialog import CreatorDetailDialog
from ui.main_window import MainWindow
from workers.collector_worker import CollectorWorker
from workers.contact_worker import ContactWorker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Giao diện công cụ lấy liên hệ nhà sáng tạo TikTok"
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Tạo GUI, tải dữ liệu, in tóm tắt rồi thoát.",
    )
    parser.add_argument(
        "--auto-close-ms",
        type=int,
        default=0,
        help="Tự đóng GUI sau số mili-giây đã nhập.",
    )
    parser.add_argument(
        "--smoke-ui-checks",
        action="store_true",
        help="Chạy kiểm tra search, filter và dialog chi tiết.",
    )
    parser.add_argument(
        "--smoke-export",
        action="store_true",
        help="Chạy kiểm tra xuất Excel bằng thư mục Documents hiện tại.",
    )
    parser.add_argument(
        "--smoke-open-browser",
        action="store_true",
        help="Mở Chrome bằng workflow nút đăng nhập rồi đóng lại.",
    )
    parser.add_argument(
        "--smoke-worker-open",
        choices=[
            "collector",
            "contact",
        ],
        help="Mở Chrome bằng worker trong App Support tạm rồi dừng.",
    )
    parser.add_argument(
        "--smoke-browser-timeout-ms",
        type=int,
        default=20000,
        help="Timeout cho kiểm tra mở Chrome bằng Playwright.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    application = QApplication(sys.argv)
    application.setApplicationName(
        "Công cụ lấy liên hệ nhà sáng tạo TikTok"
    )
    application.setStyle("Fusion")
    apply_light_palette(application)
    paths = default_paths()
    db_path = paths.database_path
    ensure_app_directories(paths)
    configure_startup_logging(paths)
    log_startup_environment(paths)
    repository = DatabaseRepository(db_path)

    try:
        if not db_path.exists():
            migration = migrate_legacy_database(paths)
            logging.info(
                "Database migration check: %s",
                migration.message,
            )

        if db_path.exists() and not sqlite_integrity_check(db_path):
            QMessageBox.critical(
                None,
                "Lỗi database",
                "SQLite integrity_check không đạt:\n"
                f"{db_path}",
            )
            return 1

        repository.init_database()

        if not sqlite_integrity_check(db_path):
            QMessageBox.critical(
                None,
                "Lỗi database",
                "SQLite integrity_check không đạt sau khi khởi tạo:\n"
                f"{db_path}",
            )
            return 1

    except Exception as exc:
        logging.exception("Startup database initialization failed")
        QMessageBox.critical(
            None,
            "Lỗi khởi động",
            "Không thể khởi động cơ sở dữ liệu ứng dụng.\n\n"
            f"Chi tiết: {exc}\n\n"
            f"Log: {paths.logs_dir / 'app.log'}",
        )
        return 1

    window = MainWindow(
        repository=repository,
        db_path=repository.db_path,
        app_paths=paths,
    )

    window.show()

    if args.smoke_test:
        summary = window.smoke_summary()
        print("SMOKE_OK")
        print(f"window_title={summary['window_title']}")
        print(f"database={summary['database']}")
        print(f"rows={summary['rows']}")
        print(f"visible_rows={summary['visible_rows']}")
        print(f"stats={summary['stats']}")
        print(f"browser_status={summary['browser_status']}")
        print(f"browser_profile={summary['browser_profile']}")
        print(f"collector_status={summary['collector_status']}")
        print(f"contact_status={summary['contact_status']}")

        if args.smoke_ui_checks:
            run_ui_smoke_checks(window)

        if args.smoke_export:
            run_export_smoke(window)

        if args.smoke_worker_open:
            start_worker_open_smoke(
                application,
                paths,
                args.smoke_worker_open,
                args.smoke_browser_timeout_ms,
            )
        elif args.smoke_open_browser:
            start_browser_smoke(
                application,
                window,
                args.smoke_browser_timeout_ms,
            )
        else:
            QTimer.singleShot(
                100,
                application.quit,
            )

    elif args.auto_close_ms > 0:
        QTimer.singleShot(
            args.auto_close_ms,
            application.quit,
        )

    exit_code = application.exec()
    smoke_exit_code = application.property("smoke_exit_code")

    if smoke_exit_code is not None:
        return int(smoke_exit_code)

    return exit_code


def run_ui_smoke_checks(
    window: MainWindow,
) -> None:
    rows = window.table_model.rows()

    if not rows:
        print("ui_search=NOT RUN no rows")
        print("ui_filter=NOT RUN no rows")
        print("ui_detail=NOT RUN no rows")
        return

    first_row = rows[0]
    query = str(
        first_row.get("creator_id")
        or first_row.get("nickname")
        or first_row.get("username")
        or ""
    )
    window.search_input.setText(query)
    window._apply_search()
    print(
        "ui_search="
        + ("PASS" if window.proxy_model.rowCount() >= 1 else "FAIL")
    )
    window.search_input.clear()
    window._apply_search()

    window.proxy_model.set_contact_filter("has_phone")
    expected_has_phone = int(
        window.smoke_summary()["stats"]["has_phone"]
    )
    print(
        "ui_filter_contact_phone="
        + (
            "PASS"
            if window.proxy_model.rowCount() == expected_has_phone
            else "FAIL"
        )
    )
    window.proxy_model.set_contact_filter("all")

    dialog = CreatorDetailDialog(
        first_row,
        window,
    )
    print(
        "ui_detail="
        + (
            "PASS"
            if dialog.windowTitle() == "Chi tiết nhà sáng tạo"
            else "FAIL"
        )
    )
    dialog.close()


def run_export_smoke(
    window: MainWindow,
) -> None:
    try:
        result = export_creators_to_excel(
            window.repository.list_creators_for_phone_export(),
            window.app_paths.exports_dir,
        )
        print(
            "export_excel=PASS "
            f"count={result.exported_count} path={result.path}"
        )
    except NoPhoneCreatorsToExport as exc:
        print(f"export_excel=NOT RUN {exc}")
    except Exception as exc:
        print(f"export_excel=FAIL {exc}")


def start_browser_smoke(
    application: QApplication,
    window: MainWindow,
    timeout_ms: int,
) -> None:
    if not google_chrome_is_installed():
        print("smoke_browser=FAIL Google Chrome missing")
        application.setProperty("smoke_exit_code", 1)
        QTimer.singleShot(100, application.quit)
        return

    state = {
        "finished": False,
    }

    def finish(
        message: str,
        exit_code: int,
    ) -> None:
        if state["finished"]:
            return

        state["finished"] = True
        print(message)
        application.setProperty(
            "smoke_exit_code",
            exit_code,
        )

        if window.browser_worker:
            window.browser_worker.request_stop(
                close_browser=True,
            )
        else:
            application.quit()

    def attach_worker() -> None:
        worker = window.browser_worker

        if worker is None:
            if not state["finished"]:
                QTimer.singleShot(
                    100,
                    attach_worker,
                )
            return

        worker.connected.connect(
            lambda: finish(
                "smoke_browser=PASS connected",
                0,
            )
        )
        worker.error_occurred.connect(
            lambda message: finish(
                f"smoke_browser=FAIL {message}",
                1,
            )
        )
        worker.finished.connect(application.quit)

    QTimer.singleShot(
        0,
        window._open_tiktok_login,
    )
    QTimer.singleShot(
        100,
        attach_worker,
    )
    QTimer.singleShot(
        timeout_ms,
        lambda: finish(
            "smoke_browser=FAIL timeout",
            1,
        ),
    )


def start_worker_open_smoke(
    application: QApplication,
    base_paths,
    worker_kind: str,
    timeout_ms: int,
) -> None:
    if not google_chrome_is_installed():
        print(f"{worker_kind}_worker_chrome=FAIL Google Chrome missing")
        application.setProperty("smoke_exit_code", 1)
        QTimer.singleShot(100, application.quit)
        return

    temp_root = Path(
        tempfile.mkdtemp(
            prefix=f"tiktok_creator_tool_{worker_kind}_smoke_",
        )
    )
    smoke_paths = AppPaths(
        project_root=base_paths.project_root,
        app_support_dir=temp_root / "Application Support",
        documents_dir=temp_root / "Documents",
    )
    ensure_app_directories(smoke_paths)
    smoke_repository = DatabaseRepository(
        smoke_paths.database_path
    )
    smoke_repository.init_database()

    if worker_kind == "contact":
        smoke_repository.add_creator_to_queue("7494000000000000000")
        worker = ContactWorker(
            smoke_paths,
            smoke_paths.database_path,
            process_limit=1,
            close_browser_on_finish=True,
        )
    else:
        worker = CollectorWorker(
            smoke_paths,
            smoke_paths.database_path,
            target=1,
        )

    thread = QThread()
    worker.moveToThread(thread)

    state = {
        "finished": False,
    }

    def finish(
        message: str,
        exit_code: int,
    ) -> None:
        if state["finished"]:
            return

        state["finished"] = True
        print(message)
        application.setProperty(
            "smoke_exit_code",
            exit_code,
        )
        worker.request_stop(
            close_browser=True,
        )

    def on_browser_status(
        status: str,
    ) -> None:
        if status == "Đã kết nối":
            finish(
                f"{worker_kind}_worker_chrome=PASS connected",
                0,
            )

    def on_error(
        message: str,
    ) -> None:
        finish(
            f"{worker_kind}_worker_chrome=FAIL {message}",
            1,
        )

    worker.browser_status_changed.connect(on_browser_status)
    worker.error_occurred.connect(on_error)
    worker.finished.connect(thread.quit)
    thread.started.connect(worker.run)
    thread.finished.connect(application.quit)
    application._smoke_worker_refs = (thread, worker)

    QTimer.singleShot(
        timeout_ms,
        lambda: finish(
            f"{worker_kind}_worker_chrome=FAIL timeout",
            1,
        ),
    )
    thread.start()


def configure_startup_logging(paths) -> None:
    log_path = paths.logs_dir / "app.log"
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s: %(message)s"
        ),
    )


def log_startup_environment(paths) -> None:
    logging.info("App version: %s", APP_VERSION)
    logging.info("Platform: %s", platform.platform())
    logging.info("Architecture: %s", platform.machine())
    logging.info("Data directory: %s", paths.data_dir)
    logging.info("Browser data directory: %s", paths.browser_data_dir)
    logging.info("Chrome detected: %s", google_chrome_is_installed())
    logging.info("Chrome executable: %s", find_chrome_executable())


def apply_light_palette(
    application: QApplication,
) -> None:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f6f7f9"))
    palette.setColor(QPalette.WindowText, QColor("#111827"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f9fafb"))
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipText, QColor("#111827"))
    palette.setColor(QPalette.Text, QColor("#111827"))
    palette.setColor(QPalette.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ButtonText, QColor("#111827"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor("#dbeafe"))
    palette.setColor(QPalette.HighlightedText, QColor("#111827"))
    palette.setColor(QPalette.PlaceholderText, QColor("#9ca3af"))
    application.setPalette(palette)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("Unhandled startup exception")
        raise
