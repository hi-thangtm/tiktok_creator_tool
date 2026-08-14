from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from core.constants import (
    CAPTCHA_RECOVERY_WAIT_MS,
    CONTACT_OPEN_ATTEMPTS,
    CONTACT_POPUP_TIMEOUT_MS,
    CREATOR_DETAIL_BASE_URL,
    PROFILE_RETRY_LIMIT,
    PROFILE_RETRY_WAIT_MS,
    PROFILE_TIMEOUT_SECONDS,
    status_label,
)
from core.contact_utils import (
    build_contact_note,
    clean_text,
    determine_status,
    merge_contacts,
    normalize_phone,
)
from core.database import DatabaseRepository
from services.collector_service import (
    captcha_failed_is_visible,
    captcha_is_visible,
)


ContactStep = Literal[
    "Đang mở chi tiết nhà sáng tạo",
    "Đang chờ hồ sơ",
    "Đang đọc tiểu sử",
    "Đang tìm Zalo",
    "Đang mở thông tin liên hệ",
    "Đang đọc Email",
    "Đang lưu cơ sở dữ liệu",
    "Hoàn tất",
]

DetailErrorAction = Literal[
    "retry",
    "skip",
    "stop",
]


class StopSessionRequested(Exception):
    pass


class TemporarySkipRequested(Exception):
    pass


@dataclass(frozen=True)
class ContactProgress:
    current: int
    total: int
    creator_id: str | None
    nickname: str | None
    step: str
    saved: int


@dataclass(frozen=True)
class ContactResult:
    creator_id: str
    username: str | None
    nickname: str | None
    status: str
    phone_count: int
    email_count: int


class ContactController(Protocol):
    def check_pause_or_stop(self) -> None:
        ...

    def wait_for_captcha(self, page: Page) -> None:
        ...

    def wait_for_detail_error(
        self,
        page: Page,
        message: str,
    ) -> DetailErrorAction:
        ...

    def emit_step(
        self,
        step: str,
        creator_id: str | None = None,
        nickname: str | None = None,
    ) -> None:
        ...

    def log(self, message: str) -> None:
        ...


def creator_page_error_visible(page: Page) -> bool:
    error_texts = [
        "Tải dữ liệu không thành công",
        "Tai du lieu khong thanh cong",
        "Failed to load data",
    ]

    for text in error_texts:
        try:
            locator = page.get_by_text(
                text,
                exact=False,
            )

            if (
                locator.count() > 0
                and locator.first.is_visible()
            ):
                return True

        except Exception:
            continue

    return False


def profile_is_ready(page: Page) -> bool:
    try:
        profile = page.locator(
            "#creator-detail-profile-container"
        )

        if (
            profile.count() == 0
            or not profile.first.is_visible()
        ):
            return False

        loaded_marker = page.locator(
            '[data-id="creator-profile-loaded"]'
        )

        if loaded_marker.count() > 0:
            return True

        username = profile.locator(
            "span.text-head-l"
        )

        if username.count() > 0:
            return True

    except Exception:
        return False

    return False


def click_creator_retry(page: Page) -> bool:
    names = [
        "Thử lại",
        "Retry",
    ]

    for name in names:
        try:
            buttons = page.get_by_role(
                "button",
                name=name,
                exact=False,
            )

            count = buttons.count()

            for index in range(count):
                button = buttons.nth(index)

                try:
                    if button.is_visible():
                        button.click(timeout=5000)
                        return True

                except Exception:
                    continue

        except Exception:
            continue

    for name in names:
        try:
            locators = page.get_by_text(
                name,
                exact=True,
            )

            count = locators.count()

            for index in range(count):
                item = locators.nth(index)

                try:
                    if item.is_visible():
                        item.click(timeout=5000)
                        return True

                except Exception:
                    continue

        except Exception:
            continue

    return False


def wait_for_profile_ready(
    page: Page,
    controller: ContactController,
    timeout_seconds: int = PROFILE_TIMEOUT_SECONDS,
):
    deadline = time.monotonic() + timeout_seconds
    retry_count = 0

    while True:
        controller.check_pause_or_stop()

        if captcha_is_visible(page):
            controller.wait_for_captcha(page)
            deadline = time.monotonic() + timeout_seconds
            retry_count = 0
            continue

        if (
            profile_is_ready(page)
            and not creator_page_error_visible(page)
        ):
            return page.locator(
                "#creator-detail-profile-container"
            ).first

        if creator_page_error_visible(page):
            if retry_count < PROFILE_RETRY_LIMIT:
                retry_count += 1
                controller.log(
                    "Trang chi tiết nhà sáng tạo đang báo lỗi. "
                    f"Tự thử lại {retry_count}/"
                    f"{PROFILE_RETRY_LIMIT}..."
                )
                clicked = click_creator_retry(page)

                if clicked:
                    page.wait_for_timeout(PROFILE_RETRY_WAIT_MS)
                    continue

            action = controller.wait_for_detail_error(
                page,
                "Trang chi tiết nhà sáng tạo vẫn đang lỗi. "
                "Chrome sẽ giữ mở để bạn xử lý thủ công.",
            )

            if action == "stop":
                raise StopSessionRequested(
                    "Người dùng dừng phiên khi trang chi tiết nhà sáng tạo lỗi."
                )

            if action == "skip":
                raise TemporarySkipRequested(
                    "Trang chi tiết nhà sáng tạo lỗi, đưa nhà sáng tạo về chờ xử lý."
                )

            deadline = time.monotonic() + timeout_seconds
            retry_count = 0
            continue

        if time.monotonic() >= deadline:
            action = controller.wait_for_detail_error(
                page,
                "Trang chi tiết nhà sáng tạo tải quá lâu. "
                "Hãy đợi thêm, reload hoặc bấm Thử lại trên Chrome.",
            )

            if action == "stop":
                raise StopSessionRequested(
                    "Người dùng dừng phiên vì trang chi tiết nhà sáng tạo tải quá lâu."
                )

            if action == "skip":
                raise TemporarySkipRequested(
                    "Trang chi tiết nhà sáng tạo tải quá lâu, đưa nhà sáng tạo về chờ xử lý."
                )

            deadline = time.monotonic() + timeout_seconds
            retry_count = 0
            continue

        page.wait_for_timeout(500)


def extract_creator_identity(page: Page) -> dict[str, str | None]:
    result: dict[str, str | None] = {
        "username": None,
        "nickname": None,
    }

    profile = page.locator(
        "#creator-detail-profile-container"
    )

    username_candidates = profile.locator(
        "span.text-head-l"
    )

    if username_candidates.count() > 0:
        try:
            result["username"] = clean_text(
                username_candidates.first.inner_text()
            )
        except Exception:
            pass

    nickname_candidates = profile.locator(
        "span.text-body-m-regular"
    )

    for index in range(
        nickname_candidates.count()
    ):
        try:
            text = clean_text(
                nickname_candidates.nth(index).inner_text()
            )

            if not text:
                continue

            if (
                result["username"]
                and text == result["username"]
            ):
                continue

            if len(text) <= 100:
                result["nickname"] = text
                break

        except Exception:
            continue

    return result


def extract_bio(page: Page) -> str | None:
    profile = page.locator(
        "#creator-detail-profile-container"
    )

    candidates = profile.locator(
        "span.text-body-s-regular"
    )

    if candidates.count() == 0:
        return None

    best_text = None

    for index in range(
        candidates.count()
    ):
        try:
            text = clean_text(
                candidates.nth(index).inner_text()
            )

            if not text:
                continue

            if (
                best_text is None
                or len(text) > len(best_text)
            ):
                best_text = text

        except Exception:
            continue

    return best_text


def get_contact_button(page: Page):
    profile = page.locator(
        "#creator-detail-profile-container"
    )

    zalo_icon = profile.locator(
        ".alliance-icon-Zalo_Circle"
    ).first

    if zalo_icon.count() > 0:
        return zalo_icon.locator(
            "xpath=ancestor::div"
            "[contains(@class,'cursor-pointer')]"
            "[1]"
        )

    email_icon = profile.locator(
        ".alliance-icon-Email"
    ).first

    if email_icon.count() > 0:
        return email_icon.locator(
            "xpath=ancestor::div"
            "[contains(@class,'cursor-pointer')]"
            "[1]"
        )

    return None


def open_contact_popover(
    page: Page,
    controller: ContactController,
):
    had_contact_button = False

    for attempt in range(
        1,
        CONTACT_OPEN_ATTEMPTS + 1,
    ):
        wait_for_profile_ready(page, controller)
        controller.emit_step("Đang tìm Zalo")
        contact_button = get_contact_button(page)

        if contact_button is None:
            return None

        had_contact_button = True
        controller.emit_step("Đang mở thông tin liên hệ")

        try:
            contact_button.click(timeout=10000)

        except Exception:
            if captcha_is_visible(page):
                controller.wait_for_captcha(page)
                continue

            if creator_page_error_visible(page):
                continue

            raise

        if captcha_is_visible(page):
            controller.wait_for_captcha(page)
            continue

        contact_title = page.get_by_text(
            "Thông tin liên hệ",
            exact=True,
        )

        try:
            contact_title.wait_for(
                state="visible",
                timeout=CONTACT_POPUP_TIMEOUT_MS,
            )

        except PlaywrightTimeoutError:
            if captcha_is_visible(page):
                controller.wait_for_captcha(page)
                continue

            if creator_page_error_visible(page):
                continue

            controller.log(
                "Popup liên hệ chưa hiện, "
                f"thử lại {attempt}/"
                f"{CONTACT_OPEN_ATTEMPTS}."
            )
            continue

        popover = (
            page.locator(".core-popover-content")
            .filter(
                has=page.get_by_text(
                    "Thông tin liên hệ",
                    exact=True,
                )
            )
            .first
        )

        if popover.count() > 0:
            return popover

    if had_contact_button:
        raise RuntimeError(
            "Có biểu tượng liên hệ nhưng không mở được popup liên hệ."
        )

    return None


def extract_contact_popup(
    page: Page,
    controller: ContactController,
) -> dict[str, str | None]:
    result = {
        "zalo": None,
        "email": None,
    }

    popover = open_contact_popover(page, controller)

    if popover is None:
        controller.log(
            "Nhà sáng tạo không có biểu tượng Zalo/Email."
        )
        return result

    controller.emit_step("Đang đọc Email")
    popup_text = clean_text(
        popover.inner_text()
    )
    controller.log(
        f"Popup liên hệ: {popup_text}"
    )

    zalo_label = (
        popover
        .locator("span")
        .filter(has_text="Zalo:")
        .first
    )

    if zalo_label.count() > 0:
        zalo_value = zalo_label.locator(
            "xpath=following-sibling::*[1]"
        )

        if zalo_value.count() > 0:
            result["zalo"] = normalize_phone(
                clean_text(
                    zalo_value.inner_text()
                )
            )

    email_label = (
        popover
        .locator("span")
        .filter(has_text="Email:")
        .first
    )

    if email_label.count() > 0:
        email_value = email_label.locator(
            "xpath=following-sibling::*[1]"
        )

        if email_value.count() > 0:
            email = clean_text(
                email_value.inner_text()
            )

            if email:
                result["email"] = email.lower()

    try:
        close_button = popover.get_by_role(
            "button",
            name="Đã hiểu",
        )

        if close_button.count() > 0:
            close_button.click(timeout=3000)

    except Exception:
        pass

    return result


def build_creator_save_data(
    creator_id: str,
    detail_url: str,
    identity: dict[str, str | None],
    bio: str | None,
    popup_contact: dict[str, str | None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = merge_contacts(
        popup_contact,
        bio,
    )
    status = determine_status(result)
    contact_note = build_contact_note(result)

    data = {
        "creator_id": creator_id,
        "username": identity["username"],
        "nickname": identity["nickname"],
        "detail_url": detail_url,
        "zalo": result["official_zalo"],
        "official_email": result["official_email"],
        "bio": result["bio"],
        "bio_phones": json.dumps(
            result["bio_phones"],
            ensure_ascii=False,
        ),
        "bio_emails": json.dumps(
            result["bio_emails"],
            ensure_ascii=False,
        ),
        "phones_all": json.dumps(
            result["all_phones"],
            ensure_ascii=False,
        ),
        "emails_all": json.dumps(
            result["all_emails"],
            ensure_ascii=False,
        ),
        "phone_sources": json.dumps(
            result["phone_sources"],
            ensure_ascii=False,
        ),
        "email_sources": json.dumps(
            result["email_sources"],
            ensure_ascii=False,
        ),
        "contact_note": contact_note,
        "status": status,
    }

    return data, result


def process_creator(
    page: Page,
    repository: DatabaseRepository,
    creator_id: str,
    controller: ContactController,
) -> ContactResult:
    url = f"{CREATOR_DETAIL_BASE_URL}?cid={creator_id}"

    controller.log(
        f"Đang xử lý nhà sáng tạo: {creator_id}"
    )
    controller.emit_step(
        "Đang mở chi tiết nhà sáng tạo",
        creator_id=creator_id,
    )
    repository.set_creator_status(
        creator_id,
        "PROCESSING",
    )

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=120000,
    )

    controller.emit_step(
        "Đang chờ hồ sơ",
        creator_id=creator_id,
    )
    wait_for_profile_ready(page, controller)
    page.wait_for_timeout(1000)
    controller.check_pause_or_stop()

    identity = extract_creator_identity(page)
    nickname = identity["nickname"]
    controller.emit_step(
        "Đang đọc tiểu sử",
        creator_id=creator_id,
        nickname=nickname,
    )
    bio = extract_bio(page)

    controller.log(
        "Thông tin nhà sáng tạo: "
        f"tên người dùng={identity['username']}, "
        f"tên hiển thị={identity['nickname']}"
    )
    controller.log(
        f"Tiểu sử: {bio}"
    )

    popup_contact = extract_contact_popup(
        page,
        controller,
    )

    controller.emit_step(
        "Đang lưu cơ sở dữ liệu",
        creator_id=creator_id,
        nickname=nickname,
    )
    data, result = build_creator_save_data(
        creator_id=creator_id,
        detail_url=page.url,
        identity=identity,
        bio=bio,
        popup_contact=popup_contact,
    )
    repository.save_creator(data)

    status = str(data["status"])
    controller.log(
        "Kết quả: "
        f"Zalo={result['official_zalo']} | "
        f"Email={result['official_email']} | "
        f"Tất cả SĐT={result['all_phones']} | "
        f"Trạng thái={status_label(status)}"
    )

    return ContactResult(
        creator_id=creator_id,
        username=identity["username"],
        nickname=nickname,
        status=status,
        phone_count=len(result["all_phones"]),
        email_count=len(result["all_emails"]),
    )
