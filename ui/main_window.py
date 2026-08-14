from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QModelIndex,
    QThread,
    QTimer,
    Qt,
    QUrl,
)
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.constants import (
    CREATOR_DETAIL_BASE_URL,
    PROCESS_LIMIT,
    QUEUE_TARGET,
    STATUSES,
    status_label,
)
from core.database import DatabaseRepository
from core.paths import (
    AppPaths,
    google_chrome_is_installed,
)
from services.browser_service import BrowserStatus
from services.export_service import (
    NoPhoneCreatorsToExport,
    export_creators_to_excel,
)
from ui.creator_detail_dialog import CreatorDetailDialog
from ui.creator_table import (
    CreatorFilterProxyModel,
    CreatorTableModel,
)
from ui.log_widget import LogWidget
from workers.browser_worker import BrowserWorker
from workers.collector_worker import CollectorWorker
from workers.contact_worker import ContactWorker


class MainWindow(QMainWindow):
    def __init__(
        self,
        repository: DatabaseRepository,
        db_path: Path,
        app_paths: AppPaths,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.db_path = db_path
        self.app_paths = app_paths
        self._all_rows: list[dict[str, Any]] = []
        self._last_db_mtime: float | None = None
        self.browser_thread: QThread | None = None
        self.browser_worker: BrowserWorker | None = None
        self.collector_thread: QThread | None = None
        self.collector_worker: CollectorWorker | None = None
        self.contact_thread: QThread | None = None
        self.contact_worker: ContactWorker | None = None
        self._close_after_browser_stopped = False
        self._close_after_collector_stopped = False
        self._close_after_contact_stopped = False
        self._allow_close = False
        self._pending_start_collector = False
        self._pending_start_contact = False
        self._collector_completed = False
        self._contact_completed = False
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(250)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(
            self._apply_search
        )

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(
            self._refresh_if_database_changed
        )
        self._table_refresh_timer = QTimer(self)
        self._table_refresh_timer.setInterval(400)
        self._table_refresh_timer.setSingleShot(True)
        self._table_refresh_timer.timeout.connect(
            self.load_data
        )

        self.setWindowTitle(
            "Công cụ lấy liên hệ nhà sáng tạo TikTok"
        )
        self.resize(1320, 820)
        self._build_ui()
        self._apply_styles()
        self.load_data()
        self._refresh_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self.setCentralWidget(root)

        title_row = QHBoxLayout()
        title = QLabel(
            "Công cụ lấy liên hệ nhà sáng tạo TikTok"
        )
        title.setObjectName("appTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.database_label = QLabel(
            str(self.db_path)
        )
        self.database_label.setObjectName("databaseLabel")
        title_row.addWidget(self.database_label)
        layout.addLayout(title_row)

        self.stats_labels: dict[str, QLabel] = {}
        stats_grid = QGridLayout()
        stats_grid.setHorizontalSpacing(10)
        stats_grid.setVerticalSpacing(10)

        stats = (
            ("total", "Tổng nhà sáng tạo"),
            ("pending", "Chờ xử lý"),
            ("has_phone", "Có SĐT"),
            ("has_email", "Có Email"),
            ("has_phone_email", "Có SĐT + Email"),
            ("no_contact", "Không có liên hệ"),
            ("error", "Lỗi"),
        )

        for column, (key, label) in enumerate(stats):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(4)

            label_widget = QLabel(label)
            label_widget.setObjectName("statLabel")
            value_widget = QLabel("0")
            value_widget.setObjectName("statValue")

            card_layout.addWidget(label_widget)
            card_layout.addWidget(value_widget)
            stats_grid.addWidget(card, 0, column)
            self.stats_labels[key] = value_widget

        layout.addLayout(stats_grid)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.open_tiktok_button = QPushButton("MỞ TIKTOK / ĐĂNG NHẬP")
        self.open_tiktok_button.clicked.connect(
            self._open_tiktok_login
        )
        actions_row.addWidget(self.open_tiktok_button)

        self.collect_button = QPushButton("QUÉT NHÀ SÁNG TẠO")
        self.collect_button.clicked.connect(
            self._start_collector
        )
        actions_row.addWidget(self.collect_button)

        self.contact_button = QPushButton("LẤY LIÊN HỆ")
        self.contact_button.clicked.connect(
            self._start_contact_worker
        )
        actions_row.addWidget(self.contact_button)

        self.pause_button = QPushButton("TẠM DỪNG")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(
            self._toggle_active_pause
        )
        actions_row.addWidget(self.pause_button)

        self.stop_button = QPushButton("DỪNG AN TOÀN")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(
            self._stop_active_worker_safely
        )
        actions_row.addWidget(self.stop_button)

        self.refresh_button = QPushButton("LÀM MỚI")
        self.refresh_button.clicked.connect(
            self.load_data
        )
        actions_row.addWidget(self.refresh_button)

        self.export_button = QPushButton("XUẤT EXCEL")
        self.export_button.clicked.connect(
            self._export_excel
        )
        actions_row.addWidget(self.export_button)

        actions_row.addStretch(1)
        self.browser_status_label = QLabel(
            f"Trình duyệt: {BrowserStatus.NOT_OPEN}"
        )
        self.browser_status_label.setObjectName("browserStatus")
        actions_row.addWidget(self.browser_status_label)
        layout.addLayout(actions_row)

        filters_row = QHBoxLayout()
        filters_row.setSpacing(8)

        filters_row.addWidget(QLabel("Tìm kiếm"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Tên hiển thị, tên người dùng, mã nhà sáng tạo, SĐT, email"
        )
        self.search_input.textChanged.connect(
            self._search_timer.start
        )
        filters_row.addWidget(
            self.search_input,
            stretch=1,
        )

        filters_row.addWidget(QLabel("Liên hệ"))
        self.contact_filter = QComboBox()
        self.contact_filter.addItem("Tất cả", "all")
        self.contact_filter.addItem("Có SĐT", "has_phone")
        self.contact_filter.addItem("Có Email", "has_email")
        self.contact_filter.addItem("Có SĐT + Email", "has_phone_email")
        self.contact_filter.addItem("Không có liên hệ", "no_contact")
        self.contact_filter.currentIndexChanged.connect(
            self._apply_filters
        )
        filters_row.addWidget(self.contact_filter)

        filters_row.addWidget(QLabel("Trạng thái"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("Tất cả", "all")

        for status in STATUSES:
            self.status_filter.addItem(
                status_label(status),
                status,
            )

        self.status_filter.currentIndexChanged.connect(
            self._apply_filters
        )
        filters_row.addWidget(self.status_filter)

        filters_row.addWidget(QLabel("Nguồn SĐT"))
        self.phone_source_filter = QComboBox()
        self.phone_source_filter.addItem("Tất cả", "all")
        self.phone_source_filter.addItem("Zalo", "zalo")
        self.phone_source_filter.addItem("Bio", "bio")
        self.phone_source_filter.addItem("Zalo + Bio", "zalo_bio")
        self.phone_source_filter.currentIndexChanged.connect(
            self._apply_filters
        )
        filters_row.addWidget(self.phone_source_filter)

        layout.addLayout(filters_row)

        self.table_model = CreatorTableModel()
        self.proxy_model = CreatorFilterProxyModel()
        self.proxy_model.setSourceModel(self.table_model)

        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QTableView.SelectRows
        )
        self.table.setSelectionMode(
            QTableView.SingleSelection
        )
        self.table.setContextMenuPolicy(
            Qt.CustomContextMenu
        )
        self.table.doubleClicked.connect(
            self._open_detail_from_index
        )
        self.table.customContextMenuRequested.connect(
            self._show_context_menu
        )
        self._configure_table_columns()
        layout.addWidget(
            self.table,
            stretch=1,
        )

        bottom_row = QHBoxLayout()
        self.progress_label = QLabel("Tiến độ: 0 / 0")
        bottom_row.addWidget(self.progress_label)
        self.collector_progress_label = QLabel("Quét: chưa chạy")
        bottom_row.addWidget(self.collector_progress_label)
        bottom_row.addStretch(1)
        self.visible_label = QLabel("Hiển thị 0 / 0")
        bottom_row.addWidget(self.visible_label)
        layout.addLayout(bottom_row)

        self.log_widget = LogWidget()
        layout.addWidget(QLabel("Nhật ký hoạt động"))
        layout.addWidget(self.log_widget)

    def _configure_table_columns(self) -> None:
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(
            QHeaderView.Interactive
        )
        widths = {
            0: 54,
            1: 178,
            2: 160,
            3: 160,
            4: 132,
            5: 105,
            6: 150,
            7: 220,
            8: 115,
            9: 320,
            10: 150,
            11: 155,
        }

        for column, width in widths.items():
            self.table.setColumnWidth(
                column,
                width,
            )

        header.setSectionResizeMode(
            9,
            QHeaderView.Stretch,
        )

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f6f7f9;
            }
            QWidget {
                color: #111827;
                font-size: 13px;
            }
            QLabel {
                color: #374151;
            }
            QLabel#appTitle {
                color: #111827;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#databaseLabel {
                color: #6b7280;
                font-size: 11px;
            }
            QLabel#browserStatus {
                color: #374151;
                font-weight: 700;
                padding-left: 8px;
            }
            QFrame#statCard {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            QLabel#statLabel {
                color: #6b7280;
                font-size: 12px;
            }
            QLabel#statValue {
                color: #111827;
                font-size: 24px;
                font-weight: 700;
            }
            QLineEdit,
            QComboBox {
                min-height: 30px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 4px 8px;
                background: #ffffff;
                color: #111827;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #111827;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QPushButton {
                min-height: 30px;
                padding: 4px 12px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background: #ffffff;
                color: #111827;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f3f4f6;
            }
            QPushButton:disabled {
                color: #9ca3af;
                background: #f9fafb;
            }
            QTableView {
                background: #ffffff;
                alternate-background-color: #fafafa;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                gridline-color: #edf0f2;
                color: #111827;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QTableView::item {
                color: #111827;
                padding: 3px 8px;
            }
            QTableView::item:selected {
                color: #111827;
                background: #dbeafe;
            }
            QHeaderView::section {
                background: #f9fafb;
                color: #374151;
                border: 0;
                border-right: 1px solid #e5e7eb;
                border-bottom: 1px solid #e5e7eb;
                padding: 7px 8px;
                font-weight: 700;
            }
            QTextEdit {
                background: #ffffff;
                color: #111827;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 8px;
            }
            QLabel#dialogTitle {
                color: #111827;
                font-size: 20px;
                font-weight: 700;
            }
            """
        )

    def load_data(self) -> None:
        rows = self.repository.list_creators()
        self._all_rows = rows
        self.table_model.set_rows(rows)
        self._update_stats()
        self._update_counts()
        self._remember_db_mtime()
        self.log_widget.append_log(
            f"Đã tải {len(rows)} nhà sáng tạo từ SQLite."
        )

    def _update_stats(self) -> None:
        stats = self.repository.get_stats()

        for key, label in self.stats_labels.items():
            label.setText(
                str(stats.get(key, 0))
            )

        total_pending = stats.get("pending", 0)
        total_done = (
            stats.get("has_phone", 0)
            + stats.get("has_email", 0)
            - stats.get("has_phone_email", 0)
            + stats.get("no_contact", 0)
        )
        total = total_pending + total_done + stats.get("error", 0)
        self.progress_label.setText(
            f"Tiến độ: {total_done} / {total}"
        )

    def _update_counts(self) -> None:
        self.visible_label.setText(
            "Hiển thị "
            f"{self.proxy_model.rowCount()} / "
            f"{self.table_model.rowCount()}"
        )

    def _apply_search(self) -> None:
        self.proxy_model.set_search_text(
            self.search_input.text()
        )
        self._update_counts()

    def _apply_filters(self) -> None:
        self.proxy_model.set_contact_filter(
            self.contact_filter.currentData()
        )
        self.proxy_model.set_status_filter(
            self.status_filter.currentData()
        )
        self.proxy_model.set_phone_source_filter(
            self.phone_source_filter.currentData()
        )
        self._update_counts()

    def _remember_db_mtime(self) -> None:
        try:
            self._last_db_mtime = self.db_path.stat().st_mtime
        except OSError:
            self._last_db_mtime = None

    def _refresh_if_database_changed(self) -> None:
        try:
            mtime = self.db_path.stat().st_mtime
        except OSError:
            return

        if (
            self._last_db_mtime is not None
            and mtime == self._last_db_mtime
        ):
            self._update_stats()
            return

        self.load_data()

    def _selected_source_row(self) -> dict[str, Any] | None:
        indexes = self.table.selectionModel().selectedRows()

        if not indexes:
            return None

        return self._row_from_proxy_index(indexes[0])

    def _row_from_proxy_index(
        self,
        proxy_index: QModelIndex,
    ) -> dict[str, Any] | None:
        if not proxy_index.isValid():
            return None

        source_index = self.proxy_model.mapToSource(
            proxy_index
        )

        return self.table_model.get_row(
            source_index.row()
        )

    def _open_detail_from_index(
        self,
        proxy_index: QModelIndex,
    ) -> None:
        row = self._row_from_proxy_index(proxy_index)

        if row is None:
            return

        dialog = CreatorDetailDialog(
            row,
            self,
        )
        dialog.exec()

    def _show_context_menu(
        self,
        position,
    ) -> None:
        proxy_index = self.table.indexAt(position)

        if not proxy_index.isValid():
            return

        self.table.selectRow(proxy_index.row())
        row = self._row_from_proxy_index(proxy_index)

        if row is None:
            return

        contact = row.get("_display_contact") or {}
        menu = QMenu(self)

        copy_phone = QAction("Sao chép SĐT", self)
        copy_phone.triggered.connect(
            lambda: self._copy_text(
                contact.get("main_phone") or ""
            )
        )
        menu.addAction(copy_phone)

        copy_email = QAction("Sao chép Email", self)
        copy_email.triggered.connect(
            lambda: self._copy_text(
                contact.get("email") or ""
            )
        )
        menu.addAction(copy_email)

        copy_creator_id = QAction("Sao chép mã nhà sáng tạo", self)
        copy_creator_id.triggered.connect(
            lambda: self._copy_text(
                row.get("creator_id") or ""
            )
        )
        menu.addAction(copy_creator_id)

        open_creator = QAction("Mở trang TikTok", self)
        open_creator.triggered.connect(
            lambda: self._open_creator_url(row)
        )
        menu.addAction(open_creator)

        menu.exec(
            self.table.viewport().mapToGlobal(position)
        )

    def _copy_text(
        self,
        value: str,
    ) -> None:
        QApplication.clipboard().setText(value)
        self.log_widget.append_log("Đã sao chép vào clipboard.")

    def _open_creator_url(
        self,
        row: dict[str, Any],
    ) -> None:
        url = row.get("detail_url")

        if not url:
            creator_id = row.get("creator_id") or ""
            url = f"{CREATOR_DETAIL_BASE_URL}?cid={creator_id}"

        QDesktopServices.openUrl(QUrl(url))

    def _export_excel(self) -> None:
        self.export_button.setEnabled(False)

        try:
            rows = self.repository.list_creators_for_phone_export()
            result = export_creators_to_excel(
                rows,
                self.app_paths.exports_dir,
            )

        except NoPhoneCreatorsToExport as exc:
            QMessageBox.information(
                self,
                "Chưa có dữ liệu để xuất",
                str(exc),
            )
            return

        except Exception as exc:
            self.log_widget.append_log(
                f"Lỗi xuất Excel: {exc}"
            )
            QMessageBox.critical(
                self,
                "Lỗi xuất Excel",
                str(exc),
            )
            return

        finally:
            self.export_button.setEnabled(True)

        self.log_widget.append_log(
            "Xuất Excel thành công: "
            f"{result.exported_count} nhà sáng tạo có SĐT -> "
            f"{result.path}"
        )
        self._show_export_success_dialog(
            result.path,
            result.exported_count,
        )

    def _show_export_success_dialog(
        self,
        path: Path,
        exported_count: int,
    ) -> None:
        message_box = QMessageBox(self)
        message_box.setWindowTitle("Xuất Excel thành công")
        message_box.setText(
            "Xuất Excel thành công"
        )
        message_box.setInformativeText(
            f"Số nhà sáng tạo có SĐT: {exported_count}\n\n"
            f"Tệp:\n{path.name}"
        )
        open_file_button = message_box.addButton(
            "MỞ FILE",
            QMessageBox.AcceptRole,
        )
        open_folder_button = message_box.addButton(
            "MỞ THƯ MỤC",
            QMessageBox.ActionRole,
        )
        close_button = message_box.addButton(
            "ĐÓNG",
            QMessageBox.RejectRole,
        )
        message_box.setDefaultButton(close_button)
        message_box.exec()

        clicked = message_box.clickedButton()

        if clicked == open_file_button:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(path))
            )
        elif clicked == open_folder_button:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(path.parent))
            )

    def _open_tiktok_login(self) -> None:
        if (
            self._collector_is_running()
            or self._contact_is_running()
        ):
            QMessageBox.information(
                self,
                "Worker đang chạy",
                "Một phiên xử lý đang chạy. Hãy dừng an toàn trước khi "
                "mở phiên đăng nhập riêng.",
            )
            return

        if self._browser_is_running():
            QMessageBox.information(
                self,
                "Trình duyệt đang mở",
                "Google Chrome đã được mở cho phiên hiện tại.",
            )
            return

        if not google_chrome_is_installed():
            self._set_browser_status(BrowserStatus.ERROR)
            QMessageBox.warning(
                self,
                "Google Chrome chưa cài đặt",
                "Google Chrome chưa được cài đặt. "
                "Vui lòng cài Google Chrome trước khi sử dụng.",
            )
            return

        self.browser_thread = QThread(self)
        self.browser_worker = BrowserWorker(
            self.app_paths
        )
        self.browser_worker.moveToThread(
            self.browser_thread
        )

        self.browser_thread.started.connect(
            self.browser_worker.run
        )
        self.browser_worker.status_changed.connect(
            self._set_browser_status
        )
        self.browser_worker.log_message.connect(
            self.log_widget.append_log
        )
        self.browser_worker.error_occurred.connect(
            self._on_browser_error
        )
        self.browser_worker.finished.connect(
            self.browser_thread.quit
        )
        self.browser_worker.finished.connect(
            self.browser_worker.deleteLater
        )
        self.browser_thread.finished.connect(
            self.browser_thread.deleteLater
        )
        self.browser_thread.finished.connect(
            self._on_browser_thread_finished
        )

        self.open_tiktok_button.setEnabled(False)
        self._set_browser_status(BrowserStatus.OPENING)
        self.browser_thread.start()

    def _browser_is_running(self) -> bool:
        return bool(
            self.browser_thread
            and self.browser_thread.isRunning()
        )

    def _collector_is_running(self) -> bool:
        return bool(
            self.collector_thread
            and self.collector_thread.isRunning()
        )

    def _contact_is_running(self) -> bool:
        return bool(
            self.contact_thread
            and self.contact_thread.isRunning()
        )

    def _active_captcha_worker(self):
        if self.contact_worker:
            return self.contact_worker

        if self.collector_worker:
            return self.collector_worker

        return None

    def _set_browser_status(
        self,
        status: str,
    ) -> None:
        self.browser_status_label.setText(
            f"Trình duyệt: {status}"
        )

        if status in {
            BrowserStatus.NOT_OPEN,
            BrowserStatus.CLOSED,
            BrowserStatus.ERROR,
        }:
            self.open_tiktok_button.setEnabled(True)

        else:
            self.open_tiktok_button.setEnabled(False)

    def _on_browser_error(
        self,
        message: str,
    ) -> None:
        self.log_widget.append_log(
            f"Lỗi trình duyệt: {message}"
        )
        QMessageBox.critical(
            self,
            "Lỗi trình duyệt",
            message,
        )

    def _on_browser_thread_finished(self) -> None:
        self.browser_thread = None
        self.browser_worker = None

        if self._pending_start_collector:
            self._pending_start_collector = False
            QTimer.singleShot(
                0,
                self._start_collector,
            )
            return

        if self._pending_start_contact:
            self._pending_start_contact = False
            QTimer.singleShot(
                0,
                self._start_contact_worker,
            )
            return

        if self._close_after_browser_stopped:
            self._allow_close = True
            QTimer.singleShot(
                0,
                self.close,
            )

    def _start_collector(self) -> None:
        if self._collector_is_running():
            QMessageBox.information(
                self,
                "Phiên quét đang chạy",
                "Phiên quét đang chạy trong phiên hiện tại.",
            )
            return

        if self._contact_is_running():
            QMessageBox.information(
                self,
                "Phiên lấy liên hệ đang chạy",
                "Đang lấy liên hệ. Hãy dừng phiên hiện tại "
                "trước khi quét nhà sáng tạo.",
            )
            return

        if self._browser_is_running():
            answer = QMessageBox.question(
                self,
                "Đang có trình duyệt đăng nhập",
                "Đang có trình duyệt đăng nhập mở bằng profile này. "
                "Cần đóng phiên đó trước khi bắt đầu quét để tránh "
                "khóa profile trình duyệt. Đóng trình duyệt đăng nhập và "
                "bắt đầu quét?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer != QMessageBox.Yes:
                return

            self._pending_start_collector = True
            self.open_tiktok_button.setEnabled(False)

            if self.browser_worker:
                self.browser_worker.request_stop(
                    close_browser=True,
                )

            return

        if not google_chrome_is_installed():
            self._set_browser_status(BrowserStatus.ERROR)
            QMessageBox.warning(
                self,
                "Google Chrome chưa cài đặt",
                "Google Chrome chưa được cài đặt. "
                "Vui lòng cài Google Chrome trước khi sử dụng.",
            )
            return

        self.collector_thread = QThread(self)
        self.collector_worker = CollectorWorker(
            paths=self.app_paths,
            database_path=self.db_path,
            target=QUEUE_TARGET,
        )
        self.collector_worker.moveToThread(
            self.collector_thread
        )

        self.collector_thread.started.connect(
            self.collector_worker.run
        )
        self.collector_worker.browser_status_changed.connect(
            self._set_browser_status
        )
        self.collector_worker.log_message.connect(
            self.log_widget.append_log
        )
        self.collector_worker.ready_prompt_required.connect(
            self._show_collector_ready_dialog
        )
        self.collector_worker.captcha_required.connect(
            self._show_captcha_dialog
        )
        self.collector_worker.captcha_still_visible.connect(
            self._on_captcha_still_visible
        )
        self.collector_worker.paused.connect(
            self._on_collector_paused
        )
        self.collector_worker.resumed.connect(
            self._on_collector_resumed
        )
        self.collector_worker.progress_changed.connect(
            self._on_collector_progress
        )
        self.collector_worker.creator_inserted.connect(
            self._on_creator_inserted
        )
        self.collector_worker.completed.connect(
            self._on_collector_completed
        )
        self.collector_worker.error_occurred.connect(
            self._on_collector_error
        )
        self.collector_worker.stopped.connect(
            self._on_collector_stopped
        )
        self.collector_worker.finished.connect(
            self.collector_thread.quit
        )
        self.collector_worker.finished.connect(
            self.collector_worker.deleteLater
        )
        self.collector_thread.finished.connect(
            self.collector_thread.deleteLater
        )
        self.collector_thread.finished.connect(
            self._on_collector_thread_finished
        )

        self._collector_completed = False
        self.collect_button.setEnabled(False)
        self.contact_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.pause_button.setText("TẠM DỪNG")
        self.stop_button.setEnabled(True)
        self.open_tiktok_button.setEnabled(False)
        self.collector_progress_label.setText(
            "Quét: đang mở Chrome"
        )
        self.log_widget.append_log(
            "Bắt đầu quét nhà sáng tạo."
        )
        self.collector_thread.start()

    def _show_collector_ready_dialog(self) -> None:
        if not self.collector_worker:
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("Danh sách nhà sáng tạo")
        message_box.setText(
            "Vui lòng mở trang Tìm nhà sáng tạo và áp dụng "
            "bộ lọc mong muốn trên Chrome."
        )
        message_box.setInformativeText(
            "Khi danh sách nhà sáng tạo đã hiển thị, nhấn "
            "DANH SÁCH ĐÃ SẴN SÀNG để bắt đầu scroll."
        )
        ready_button = message_box.addButton(
            "DANH SÁCH ĐÃ SẴN SÀNG",
            QMessageBox.AcceptRole,
        )
        stop_button = message_box.addButton(
            "DỪNG PHIÊN",
            QMessageBox.RejectRole,
        )
        message_box.setDefaultButton(ready_button)
        message_box.exec()

        if message_box.clickedButton() == ready_button:
            self.log_widget.append_log(
                "Người dùng xác nhận danh sách đã sẵn sàng."
            )
            self.collector_worker.mark_list_ready()
        elif message_box.clickedButton() == stop_button:
            self.collector_worker.request_stop(
                close_browser=True,
            )

    def _show_captcha_dialog(self) -> None:
        worker = self._active_captcha_worker()

        if not worker:
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("TikTok yêu cầu xác minh")
        message_box.setText(
            "Hãy xử lý CAPTCHA thủ công trên Chrome."
        )
        check_button = message_box.addButton(
            "KIỂM TRA LẠI",
            QMessageBox.AcceptRole,
        )
        stop_button = message_box.addButton(
            "DỪNG PHIÊN",
            QMessageBox.RejectRole,
        )
        message_box.setDefaultButton(check_button)
        message_box.exec()

        if message_box.clickedButton() == check_button:
            worker.check_captcha_again()
        elif message_box.clickedButton() == stop_button:
            worker.request_stop(
                close_browser=True,
            )

    def _on_captcha_still_visible(self) -> None:
        QMessageBox.information(
            self,
            "CAPTCHA vẫn đang hiển thị",
            "CAPTCHA vẫn đang hiển thị. Hãy thử lại trên Chrome.",
        )
        QTimer.singleShot(
            0,
            self._show_captcha_dialog,
        )

    def _toggle_active_pause(self) -> None:
        if self.contact_worker:
            self._toggle_contact_pause()
            return

        if self.collector_worker:
            self._toggle_collector_pause()

    def _stop_active_worker_safely(self) -> None:
        if self.contact_worker:
            self._stop_contact_worker_safely()
            return

        if self.collector_worker:
            self._stop_collector_safely()

    def _toggle_collector_pause(self) -> None:
        if not self.collector_worker:
            return

        if self.pause_button.text() == "TIẾP TỤC":
            self.collector_worker.request_resume()
            self.pause_button.setText("TẠM DỪNG")
            return

        self.collector_worker.request_pause()
        self.pause_button.setText("ĐANG TẠM DỪNG")

    def _on_collector_paused(self) -> None:
        self.pause_button.setText("TIẾP TỤC")
        self.collector_progress_label.setText(
            "Quét: tạm dừng"
        )

    def _on_collector_resumed(self) -> None:
        self.pause_button.setText("TẠM DỪNG")

    def _stop_collector_safely(self) -> None:
        if not self.collector_worker:
            return

        answer = QMessageBox.question(
            self,
            "Dừng phiên quét",
            "Dừng phiên quét an toàn và đóng Chrome?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if answer != QMessageBox.Yes:
            return

        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.collector_progress_label.setText(
            "Quét: đang dừng an toàn"
        )
        self.collector_worker.request_stop(
            close_browser=True,
        )

    def _on_collector_progress(
        self,
        progress: dict,
    ) -> None:
        self.progress_label.setText(
            "Tiến độ: "
            f"{progress['pending_total']} / {progress['target']}"
        )
        self.collector_progress_label.setText(
            "Quét: "
            f"lượt {progress['round_number']} | "
            f"đang thấy {progress['visible_count']} | "
            f"cũ {progress['total_old']} | "
            f"mới {progress['total_new']} | "
            f"chờ xử lý {progress['pending_total']}/{progress['target']}"
        )
        self._update_stats()

    def _on_creator_inserted(
        self,
        creator_id: str,
    ) -> None:
        self.log_widget.append_log(
            f"Đã lưu nhà sáng tạo mới: {creator_id}"
        )

        if not self._table_refresh_timer.isActive():
            self._table_refresh_timer.start()

    def _on_collector_completed(
        self,
        result: dict,
    ) -> None:
        self._collector_completed = True
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.load_data()
        self.collector_progress_label.setText(
            "Quét: hoàn tất | "
            f"mới {result['new_added']} | "
            f"cũ {result['old_skipped']} | "
            f"chờ xử lý {result['pending_total']}"
        )
        answer = QMessageBox.question(
            self,
            "Quét hoàn tất",
            "Phiên quét đã hoàn tất. Bạn có muốn đóng Chrome "
            "phiên quét không?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if (
            answer == QMessageBox.Yes
            and self.collector_worker
        ):
            self.stop_button.setEnabled(False)
            self.collector_worker.request_stop(
                close_browser=True,
            )

    def _on_collector_error(
        self,
        message: str,
    ) -> None:
        self.log_widget.append_log(
            f"Lỗi phiên quét: {message}"
        )
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.collector_progress_label.setText(
            "Quét: lỗi"
        )
        QMessageBox.critical(
            self,
            "Lỗi phiên quét",
            message,
        )

    def _on_collector_stopped(self) -> None:
        self.log_widget.append_log(
            "Phiên quét đã dừng."
        )

    def _on_collector_thread_finished(self) -> None:
        self.collector_thread = None
        self.collector_worker = None
        self.collect_button.setEnabled(True)
        self.contact_button.setEnabled(True)
        self.open_tiktok_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("TẠM DỪNG")
        self.stop_button.setEnabled(False)

        if not self._collector_completed:
            self.collector_progress_label.setText(
                "Quét: đã dừng"
            )

        if self._close_after_collector_stopped:
            self._allow_close = True
            QTimer.singleShot(
                0,
                self.close,
            )

    def _start_contact_worker(self) -> None:
        if self._contact_is_running():
            QMessageBox.information(
                self,
                "Phiên lấy liên hệ đang chạy",
                "Phiên lấy liên hệ đang chạy.",
            )
            return

        if self._collector_is_running():
            QMessageBox.information(
                self,
                "Phiên quét đang chạy",
                "Phiên quét đang chạy. Hãy dừng an toàn trước khi "
                "lấy liên hệ.",
            )
            return

        if self._browser_is_running():
            answer = QMessageBox.question(
                self,
                "Đang có trình duyệt đăng nhập",
                "Đang có trình duyệt đăng nhập mở bằng profile này. "
                "Cần đóng phiên đó trước khi lấy liên hệ để tránh "
                "khóa profile trình duyệt. Đóng trình duyệt đăng nhập và "
                "bắt đầu lấy liên hệ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer != QMessageBox.Yes:
                return

            self._pending_start_contact = True
            self.open_tiktok_button.setEnabled(False)

            if self.browser_worker:
                self.browser_worker.request_stop(
                    close_browser=True,
                )

            return

        if not google_chrome_is_installed():
            self._set_browser_status(BrowserStatus.ERROR)
            QMessageBox.warning(
                self,
                "Google Chrome chưa cài đặt",
                "Google Chrome chưa được cài đặt. "
                "Vui lòng cài Google Chrome trước khi sử dụng.",
            )
            return

        self.contact_thread = QThread(self)
        self.contact_worker = ContactWorker(
            paths=self.app_paths,
            database_path=self.db_path,
            process_limit=PROCESS_LIMIT,
        )
        self.contact_worker.moveToThread(
            self.contact_thread
        )

        self.contact_thread.started.connect(
            self.contact_worker.run
        )
        self.contact_worker.browser_status_changed.connect(
            self._set_browser_status
        )
        self.contact_worker.log_message.connect(
            self.log_widget.append_log
        )
        self.contact_worker.captcha_required.connect(
            self._show_captcha_dialog
        )
        self.contact_worker.captcha_still_visible.connect(
            self._on_captcha_still_visible
        )
        self.contact_worker.detail_error_required.connect(
            self._show_contact_detail_error_dialog
        )
        self.contact_worker.paused.connect(
            self._on_contact_paused
        )
        self.contact_worker.resumed.connect(
            self._on_contact_resumed
        )
        self.contact_worker.progress_changed.connect(
            self._on_contact_progress
        )
        self.contact_worker.creator_updated.connect(
            self._on_contact_creator_updated
        )
        self.contact_worker.completed.connect(
            self._on_contact_completed
        )
        self.contact_worker.error_occurred.connect(
            self._on_contact_error
        )
        self.contact_worker.stopped.connect(
            self._on_contact_stopped
        )
        self.contact_worker.finished.connect(
            self.contact_thread.quit
        )
        self.contact_worker.finished.connect(
            self.contact_worker.deleteLater
        )
        self.contact_thread.finished.connect(
            self.contact_thread.deleteLater
        )
        self.contact_thread.finished.connect(
            self._on_contact_thread_finished
        )

        self._contact_completed = False
        self.contact_button.setEnabled(False)
        self.collect_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.pause_button.setText("TẠM DỪNG")
        self.stop_button.setEnabled(True)
        self.open_tiktok_button.setEnabled(False)
        self.collector_progress_label.setText(
            "Lấy liên hệ: đang mở Chrome"
        )
        self.log_widget.append_log(
            "Bắt đầu lấy liên hệ."
        )
        self.contact_thread.start()

    def _show_contact_detail_error_dialog(
        self,
        message: str,
    ) -> None:
        if not self.contact_worker:
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("Chi tiết nhà sáng tạo chưa sẵn sàng")
        message_box.setText(message)
        message_box.setInformativeText(
            "Chrome vẫn giữ nguyên nhà sáng tạo hiện tại. "
            "Bạn có thể xử lý thủ công rồi kiểm tra lại, "
            "bỏ qua tạm thời, hoặc dừng phiên."
        )
        retry_button = message_box.addButton(
            "KIỂM TRA LẠI",
            QMessageBox.AcceptRole,
        )
        skip_button = message_box.addButton(
            "BỎ QUA TẠM THỜI",
            QMessageBox.DestructiveRole,
        )
        stop_button = message_box.addButton(
            "DỪNG PHIÊN",
            QMessageBox.RejectRole,
        )
        message_box.setDefaultButton(retry_button)
        message_box.exec()

        if message_box.clickedButton() == retry_button:
            self.contact_worker.choose_detail_error_action(
                "retry"
            )
        elif message_box.clickedButton() == skip_button:
            self.contact_worker.choose_detail_error_action(
                "skip"
            )
        elif message_box.clickedButton() == stop_button:
            self.contact_worker.choose_detail_error_action(
                "stop"
            )

    def _toggle_contact_pause(self) -> None:
        if not self.contact_worker:
            return

        if self.pause_button.text() == "TIẾP TỤC":
            self.contact_worker.request_resume()
            self.pause_button.setText("TẠM DỪNG")
            return

        self.contact_worker.request_pause()
        self.pause_button.setText("ĐANG TẠM DỪNG")

    def _on_contact_paused(self) -> None:
        self.pause_button.setText("TIẾP TỤC")
        self.collector_progress_label.setText(
            "Lấy liên hệ: tạm dừng"
        )

    def _on_contact_resumed(self) -> None:
        self.pause_button.setText("TẠM DỪNG")

    def _stop_contact_worker_safely(self) -> None:
        if not self.contact_worker:
            return

        answer = QMessageBox.question(
            self,
            "Dừng lấy liên hệ",
            "Dừng phiên lấy liên hệ an toàn và đóng Chrome?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if answer != QMessageBox.Yes:
            return

        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.collector_progress_label.setText(
            "Lấy liên hệ: đang dừng an toàn"
        )
        self.contact_worker.request_stop(
            close_browser=True,
        )

    def _on_contact_progress(
        self,
        progress: dict,
    ) -> None:
        current = progress.get("current", 0)
        total = progress.get("total", 0)
        creator_id = progress.get("creator_id") or "-"
        nickname = progress.get("nickname") or "-"
        step = progress.get("step") or "-"
        saved = progress.get("saved", 0)

        self.progress_label.setText(
            f"Lấy liên hệ: {current} / {total}"
        )
        self.collector_progress_label.setText(
            "Lấy liên hệ: "
            f"{current}/{total} | "
            f"Mã {creator_id} | "
            f"{nickname} | "
            f"{step} | "
            f"đã lưu {saved}"
        )
        self._update_stats()

    def _on_contact_creator_updated(
        self,
        creator_id: str,
    ) -> None:
        self.log_widget.append_log(
            f"Đã cập nhật nhà sáng tạo: {creator_id}"
        )
        self.load_data()

    def _on_contact_completed(
        self,
        result: dict,
    ) -> None:
        self._contact_completed = True
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.load_data()
        self.collector_progress_label.setText(
            "Lấy liên hệ: hoàn tất | "
            f"đã lưu {result['saved']} / {result['total']}"
        )
        QMessageBox.information(
            self,
            "Lấy liên hệ hoàn tất",
            "Phiên lấy liên hệ đã hoàn tất. "
            f"Đã lưu {result['saved']} nhà sáng tạo.",
        )

    def _on_contact_error(
        self,
        message: str,
    ) -> None:
        self.log_widget.append_log(
            f"Lỗi lấy liên hệ: {message}"
        )
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.collector_progress_label.setText(
            "Lấy liên hệ: lỗi"
        )
        QMessageBox.critical(
            self,
            "Lỗi lấy liên hệ",
            message,
        )

    def _on_contact_stopped(self) -> None:
        self.log_widget.append_log(
            "Phiên lấy liên hệ đã dừng."
        )

    def _on_contact_thread_finished(self) -> None:
        self.contact_thread = None
        self.contact_worker = None
        self.collect_button.setEnabled(True)
        self.contact_button.setEnabled(True)
        self.open_tiktok_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("TẠM DỪNG")
        self.stop_button.setEnabled(False)
        self.load_data()

        if not self._contact_completed:
            self.collector_progress_label.setText(
                "Lấy liên hệ: đã dừng"
            )

        if self._close_after_contact_stopped:
            self._allow_close = True
            QTimer.singleShot(
                0,
                self.close,
            )

    def closeEvent(
        self,
        event,
    ) -> None:
        if self._allow_close:
            event.accept()
            return

        if self._contact_is_running():
            answer = QMessageBox.question(
                self,
                "Phiên lấy liên hệ đang chạy",
                "Phiên lấy liên hệ đang chạy. Bạn muốn dừng an toàn "
                "trước khi thoát?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if answer != QMessageBox.Yes:
                event.ignore()
                return

            event.ignore()
            self._close_after_contact_stopped = True
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)

            if self.contact_worker:
                self.contact_worker.request_stop(
                    close_browser=True,
                )

            return

        if self._collector_is_running():
            answer = QMessageBox.question(
                self,
                "Phiên quét đang chạy",
                "Phiên quét đang chạy. Bạn muốn dừng an toàn "
                "trước khi thoát?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if answer != QMessageBox.Yes:
                event.ignore()
                return

            event.ignore()
            self._close_after_collector_stopped = True
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)

            if self.collector_worker:
                self.collector_worker.request_stop(
                    close_browser=True,
                )

            return

        if self._browser_is_running():
            answer = QMessageBox.question(
                self,
                "Trình duyệt đang mở",
                "Trình duyệt đang mở. Bạn có muốn đóng trình duyệt "
                "trước khi thoát app không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if answer != QMessageBox.Yes:
                event.ignore()
                return

            event.ignore()
            self._close_after_browser_stopped = True
            self.open_tiktok_button.setEnabled(False)
            self._set_browser_status(
                "Đang đóng trình duyệt"
            )

            if self.browser_worker:
                self.browser_worker.request_stop(
                    close_browser=True,
                )

            return

        event.accept()

    def smoke_summary(self) -> dict[str, Any]:
        return {
            "window_title": self.windowTitle(),
            "rows": self.table_model.rowCount(),
            "visible_rows": self.proxy_model.rowCount(),
            "stats": {
                key: label.text()
                for key, label in self.stats_labels.items()
            },
            "database": str(self.db_path),
            "browser_status": self.browser_status_label.text(),
            "browser_profile": str(self.app_paths.browser_data_dir),
            "collector_status": self.collector_progress_label.text(),
            "contact_status": self.collector_progress_label.text(),
        }
