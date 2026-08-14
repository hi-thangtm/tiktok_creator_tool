from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor

from core.contact_utils import (
    build_display_contact,
    row_search_text,
)
from core.constants import status_label


class CreatorTableModel(QAbstractTableModel):
    COLUMNS = (
        "STT",
        "Mã nhà sáng tạo",
        "Tên hiển thị",
        "Tên người dùng",
        "SĐT chính",
        "Nguồn SĐT",
        "SĐT khác",
        "Email",
        "Nguồn Email",
        "Tiểu sử",
        "Trạng thái",
        "Kiểm tra lần cuối",
    )

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self._rows: list[dict[str, Any]] = []
        self.set_rows(rows or [])

    def rowCount(
        self,
        parent: QModelIndex = QModelIndex(),
    ) -> int:
        if parent.isValid():
            return 0

        return len(self._rows)

    def columnCount(
        self,
        parent: QModelIndex = QModelIndex(),
    ) -> int:
        if parent.isValid():
            return 0

        return len(self.COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if (
            role == Qt.DisplayRole
            and orientation == Qt.Horizontal
            and 0 <= section < len(self.COLUMNS)
        ):
            return self.COLUMNS[section]

        return None

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None

        row = self._rows[index.row()]
        column = index.column()

        if role in (
            Qt.DisplayRole,
            Qt.EditRole,
        ):
            return self._display_value(
                row,
                index.row(),
                column,
            )

        if role == Qt.UserRole:
            return row

        if role == Qt.TextAlignmentRole:
            if column == 0:
                return Qt.AlignRight | Qt.AlignVCenter

            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.ForegroundRole:
            status = row.get("status") or ""

            if status == "ERROR":
                return QColor("#991b1b")

            return QColor("#111827")

        if role == Qt.BackgroundRole:
            status = row.get("status") or ""

            if status == "ERROR":
                return QColor("#fff1f2")

            if status == "PENDING":
                return QColor("#f8fafc")

            if status in {
                "FOUND_PHONE",
                "FOUND_EMAIL",
                "FOUND_PHONE_EMAIL",
            }:
                return QColor("#f0fdf4")

            if status == "NO_CONTACT":
                return QColor("#fffbeb")

        return None

    def flags(
        self,
        index: QModelIndex,
    ) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags

        return (
            Qt.ItemIsEnabled
            | Qt.ItemIsSelectable
        )

    def set_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        self.beginResetModel()
        self._rows = [
            self._prepare_row(row)
            for row in rows
        ]
        self.endResetModel()

    def get_row(
        self,
        row_index: int,
    ) -> dict[str, Any] | None:
        if not 0 <= row_index < len(self._rows):
            return None

        return self._rows[row_index]

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def _prepare_row(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = dict(row)
        prepared["_display_contact"] = build_display_contact(prepared)
        prepared["_search_text"] = row_search_text(prepared)

        return prepared

    def _display_value(
        self,
        row: dict[str, Any],
        row_index: int,
        column: int,
    ) -> str:
        contact = row.get("_display_contact") or {}

        values = (
            str(row_index + 1),
            row.get("creator_id") or "",
            row.get("nickname") or "",
            row.get("username") or "",
            contact.get("main_phone") or "",
            contact.get("phone_source") or "",
            contact.get("other_phones") or "",
            contact.get("email") or "",
            contact.get("email_source") or "",
            row.get("bio") or "",
            status_label(row.get("status")),
            row.get("last_checked_at") or "",
        )

        if not 0 <= column < len(values):
            return ""

        return str(values[column])


class CreatorFilterProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self._search_text = ""
        self._contact_filter = "all"
        self._status_filter = "all"
        self._phone_source_filter = "all"
        self.setFilterCaseSensitivity(
            Qt.CaseInsensitive
        )
        self.setSortCaseSensitivity(
            Qt.CaseInsensitive
        )

    def set_search_text(
        self,
        value: str,
    ) -> None:
        value = value.strip().lower()

        if value == self._search_text:
            return

        self._search_text = value
        self.invalidateFilter()

    def set_contact_filter(
        self,
        value: str,
    ) -> None:
        if value == self._contact_filter:
            return

        self._contact_filter = value
        self.invalidateFilter()

    def set_status_filter(
        self,
        value: str,
    ) -> None:
        if value == self._status_filter:
            return

        self._status_filter = value
        self.invalidateFilter()

    def set_phone_source_filter(
        self,
        value: str,
    ) -> None:
        if value == self._phone_source_filter:
            return

        self._phone_source_filter = value
        self.invalidateFilter()

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QModelIndex,
    ) -> bool:
        model = self.sourceModel()

        if not isinstance(model, CreatorTableModel):
            return True

        row = model.get_row(source_row)

        if row is None:
            return False

        contact = row.get("_display_contact") or {}
        has_phone = bool(contact.get("main_phone"))
        has_email = bool(contact.get("email"))
        status = row.get("status") or ""

        if (
            self._search_text
            and self._search_text
            not in (row.get("_search_text") or "")
        ):
            return False

        if (
            self._status_filter != "all"
            and status != self._status_filter
        ):
            return False

        if (
            self._contact_filter == "has_phone"
            and not has_phone
        ):
            return False

        if (
            self._contact_filter == "has_email"
            and not has_email
        ):
            return False

        if (
            self._contact_filter == "has_phone_email"
            and not (
                has_phone
                and has_email
            )
        ):
            return False

        if (
            self._contact_filter == "no_contact"
            and (
                has_phone
                or has_email
            )
        ):
            return False

        source = contact.get("phone_source") or ""

        if (
            self._phone_source_filter == "zalo"
            and "Zalo" not in source
        ):
            return False

        if (
            self._phone_source_filter == "bio"
            and "Bio" not in source
        ):
            return False

        if (
            self._phone_source_filter == "zalo_bio"
            and source != "Zalo + Bio"
        ):
            return False

        return True
