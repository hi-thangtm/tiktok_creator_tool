"""Shared constants kept in sync with the legacy scripts."""

from .version import APP_NAME

AFFILIATE_URL = "https://affiliate.tiktok.com/"

CREATOR_DETAIL_BASE_URL = (
    "https://affiliate.tiktok.com/"
    "affiliate/creator/detail"
)

QUEUE_TARGET = 500
PROCESS_LIMIT = 500

FAST_SCROLL_STEP = 1800
FAST_WAIT = 0.60

NORMAL_SCROLL_STEP = 700
NORMAL_WAIT = 1.20

BOTTOM_EXTRA_WAIT = 1.80
MAX_STAGNANT_BOTTOM_ROUNDS = 5

MIN_CREATOR_DELAY = 4.0
MAX_CREATOR_DELAY = 7.0

BATCH_SIZE = 15
BATCH_PAUSE_SECONDS = 45

CAPTCHA_RECOVERY_WAIT_MS = 3000

PROFILE_TIMEOUT_SECONDS = 60
PROFILE_RETRY_LIMIT = 2
PROFILE_RETRY_WAIT_MS = 4000

CONTACT_POPUP_TIMEOUT_MS = 8000
CONTACT_OPEN_ATTEMPTS = 2

COMPLETED_STATUSES = {
    "FOUND_PHONE_EMAIL",
    "FOUND_PHONE",
    "FOUND_EMAIL",
    "NO_CONTACT",
}

PROCESSABLE_STATUSES = {
    "PENDING",
    "RETRY",
    "ERROR",
}

STATUSES = (
    "PENDING",
    "PROCESSING",
    "FOUND_PHONE",
    "FOUND_EMAIL",
    "FOUND_PHONE_EMAIL",
    "NO_CONTACT",
    "ERROR",
)

STATUS_LABELS = {
    "PENDING": "Chờ xử lý",
    "PROCESSING": "Đang xử lý",
    "FOUND_PHONE": "Có SĐT",
    "FOUND_EMAIL": "Có Email",
    "FOUND_PHONE_EMAIL": "Có SĐT + Email",
    "NO_CONTACT": "Không có liên hệ",
    "ERROR": "Lỗi",
}


def status_label(
    status: str | None,
) -> str:
    if not status:
        return ""

    return STATUS_LABELS.get(
        status,
        status,
    )
