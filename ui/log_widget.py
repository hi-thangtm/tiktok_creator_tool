from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QTextEdit


class LogWidget(QTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setMinimumHeight(120)

    def append_log(
        self,
        message: str,
    ) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.append(
            f"{timestamp} - {message}"
        )

