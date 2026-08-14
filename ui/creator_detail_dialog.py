from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.constants import CREATOR_DETAIL_BASE_URL
from core.constants import status_label


class CreatorDetailDialog(QDialog):
    def __init__(
        self,
        row: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.row = row
        self.contact = row.get("_display_contact") or {}
        self.setWindowTitle("Chi tiết nhà sáng tạo")
        self.setMinimumSize(640, 560)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel(
            self.row.get("nickname")
            or self.row.get("username")
            or self.row.get("creator_id")
            or "Nhà sáng tạo"
        )
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(
            form.labelAlignment()
        )
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)

        for label, value in (
            ("Tên hiển thị", self.row.get("nickname") or ""),
            ("Tên người dùng", self.row.get("username") or ""),
            ("Mã nhà sáng tạo", self.row.get("creator_id") or ""),
            ("SĐT chính", self.contact.get("main_phone") or ""),
            ("Nguồn SĐT", self.contact.get("phone_source") or ""),
            ("SĐT khác", self.contact.get("other_phones") or ""),
            ("Email", self.contact.get("email") or ""),
            ("Nguồn Email", self.contact.get("email_source") or ""),
            ("Trạng thái", status_label(self.row.get("status"))),
            ("Kiểm tra lần cuối", self.row.get("last_checked_at") or ""),
            ("Lỗi", self.row.get("error_message") or ""),
        ):
            value_label = QLabel(str(value))
            value_label.setTextInteractionFlags(
                value_label.textInteractionFlags()
                | QtTextSelectableByMouse
            )
            value_label.setWordWrap(True)
            form.addRow(
                QLabel(label),
                value_label,
            )

        layout.addLayout(form)

        bio = QTextEdit()
        bio.setReadOnly(True)
        bio.setPlainText(
            self.row.get("bio") or ""
        )
        bio.setMinimumHeight(120)
        layout.addWidget(QLabel("Tiểu sử"))
        layout.addWidget(bio)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)

        copy_phone_button = QPushButton("SAO CHÉP SĐT")
        copy_phone_button.clicked.connect(
            self._copy_phone
        )
        action_layout.addWidget(copy_phone_button)

        copy_email_button = QPushButton("SAO CHÉP EMAIL")
        copy_email_button.clicked.connect(
            self._copy_email
        )
        action_layout.addWidget(copy_email_button)

        copy_id_button = QPushButton("SAO CHÉP MÃ NHÀ SÁNG TẠO")
        copy_id_button.clicked.connect(
            self._copy_creator_id
        )
        action_layout.addWidget(copy_id_button)

        open_button = QPushButton("MỞ NHÀ SÁNG TẠO TRÊN TIKTOK")
        open_button.clicked.connect(
            self._open_creator
        )
        action_layout.addWidget(open_button)

        layout.addLayout(action_layout)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Close
        )
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _clipboard_set(
        self,
        value: str,
    ) -> None:
        QApplication.clipboard().setText(value)

    def _copy_phone(self) -> None:
        self._clipboard_set(
            self.contact.get("main_phone") or ""
        )

    def _copy_email(self) -> None:
        self._clipboard_set(
            self.contact.get("email") or ""
        )

    def _copy_creator_id(self) -> None:
        self._clipboard_set(
            self.row.get("creator_id") or ""
        )

    def _open_creator(self) -> None:
        url = self.row.get("detail_url")

        if not url:
            creator_id = self.row.get("creator_id") or ""
            url = f"{CREATOR_DETAIL_BASE_URL}?cid={creator_id}"

        QDesktopServices.openUrl(
            QUrl(url)
        )


# PySide6 exposes enum flags; this alias keeps the form setup compact.
from PySide6.QtCore import Qt

QtTextSelectableByMouse = Qt.TextSelectableByMouse
