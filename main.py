from pathlib import Path
import json
import random
import re
import time

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from database import (
    count_by_status,
    count_creators,
    get_connection,
    init_database,
    print_database_path,
    save_creator,
    save_error,
)

from creator_queue import (
    get_pending_creators,
    set_creator_status,
    should_skip_creator,
)


# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = BASE_DIR / "browser_data"

PROCESS_LIMIT = 500

CREATOR_DETAIL_BASE_URL = (
    "https://affiliate.tiktok.com/"
    "affiliate/creator/detail"
)


# =========================================================
# TIMING
# =========================================================

# Thoi gian nghi de trang on dinh giua cac creator
MIN_CREATOR_DELAY = 4.0
MAX_CREATOR_DELAY = 7.0

# Sau moi batch thi nghi lau hon
BATCH_SIZE = 15
BATCH_PAUSE_SECONDS = 45

CAPTCHA_RECOVERY_WAIT_MS = 3000

PROFILE_TIMEOUT_SECONDS = 60
PROFILE_RETRY_LIMIT = 2
PROFILE_RETRY_WAIT_MS = 4000

CONTACT_POPUP_TIMEOUT_MS = 8000
CONTACT_OPEN_ATTEMPTS = 2


# =========================================================
# CUSTOM EXCEPTIONS
# =========================================================

class StopSessionRequested(Exception):
    """
    Nguoi dung chu dong dung phien hien tai.
    Creator dang xu ly se duoc dua ve PENDING.
    """
    pass


# =========================================================
# REGEX
# =========================================================

PHONE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(?:"
    r"(?:\+?84|0)(?:3|5|7|8|9)(?:[\s.\-]?\d){8}"
    r"|"
    r"(?:3|5|7|8|9)(?:[\s.\-]?\d){8}"
    r")"
    r"(?!\d)"
)

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+"
    r"@[A-Za-z0-9.-]+"
    r"\.[A-Za-z]{2,}\b"
)


# =========================================================
# BROWSER
# =========================================================

def launch_browser_context(playwright):
    """
    Uu tien Google Chrome that va bat Chromium sandbox.

    Neu cau hinh sandbox khong duoc ho tro,
    thu lai Chrome khong truyen tuy chon do.

    Cuoi cung moi fallback sang Chromium cua Playwright.
    """

    common_options = {
        "user_data_dir": str(USER_DATA_DIR),
        "headless": False,
        "viewport": {
            "width": 1440,
            "height": 900,
        },
    }

    launch_attempts = [
        {
            "channel": "chrome",
            "chromium_sandbox": True,
        },
        {
            "channel": "chrome",
        },
        {},
    ]

    errors = []

    for index, extra_options in enumerate(
        launch_attempts,
        start=1,
    ):
        try:
            if index == 1:
                print(
                    "Dang mo Google Chrome "
                    "voi Chromium sandbox..."
                )

            elif index == 2:
                print(
                    "Thu lai Google Chrome "
                    "voi cau hinh tuong thich..."
                )

            else:
                print(
                    "Fallback sang Chromium cua Playwright..."
                )

            return (
                playwright.chromium
                .launch_persistent_context(
                    **common_options,
                    **extra_options,
                )
            )

        except Exception as exc:
            errors.append(
                f"Lan {index}: {exc}"
            )

    raise RuntimeError(
        "Khong mo duoc trinh duyet.\n"
        + "\n".join(errors)
    )


# =========================================================
# DATABASE STATE
# =========================================================

def reset_stuck_processing():
    """
    Neu phien truoc bi tat giua chung,
    dua cac creator PROCESSING ve PENDING.
    """

    connection = get_connection()

    try:
        cursor = connection.execute(
            """
            UPDATE creators
            SET
                status = 'PENDING',
                last_checked_at = CURRENT_TIMESTAMP
            WHERE status = 'PROCESSING'
            """
        )

        connection.commit()

        if cursor.rowcount > 0:
            print(
                "Da dua creator PROCESSING "
                "ve PENDING:",
                cursor.rowcount,
            )

    finally:
        connection.close()


# =========================================================
# BASIC HELPERS
# =========================================================

def clean_text(value):
    if not value:
        return None

    return " ".join(
        value.split()
    ).strip()


def normalize_phone(value):
    if not value:
        return None

    digits = re.sub(
        r"\D",
        "",
        value,
    )

    # 84974581131
    if (
        digits.startswith("84")
        and len(digits) == 11
    ):
        return "+84" + digits[2:]

    # 0974581131
    if (
        digits.startswith("0")
        and len(digits) == 10
    ):
        return "+84" + digits[1:]

    # 974581131
    if (
        len(digits) == 9
        and digits[0] in "35789"
    ):
        return "+84" + digits

    return None


def find_all_phones(text):
    if not text:
        return []

    result = []

    for match in PHONE_PATTERN.findall(text):
        phone = normalize_phone(
            match
        )

        if (
            phone
            and phone not in result
        ):
            result.append(
                phone
            )

    return result


def find_all_emails(text):
    if not text:
        return []

    result = []

    for email in EMAIL_PATTERN.findall(text):
        email = (
            email
            .strip()
            .lower()
        )

        if email not in result:
            result.append(
                email
            )

    return result


# =========================================================
# CAPTCHA
# =========================================================

def captcha_title_visible(page):
    """
    Tieu de popup CAPTCHA la dau hieu chinh xac nhat.
    """

    titles = [
        "Xác minh để tiếp tục",
        "Verify to continue",
    ]

    for title_text in titles:
        try:
            title = page.get_by_text(
                title_text,
                exact=False,
            )

            if (
                title.count() > 0
                and title.first.is_visible()
            ):
                return True

        except Exception:
            continue

    return False


def captcha_is_visible(page):
    """
    Phat hien CAPTCHA dang thuc su hien.

    Khong dung text "Vui long thu lai" tren toan trang,
    vi Creator Detail cung co the co text nay.
    """

    if captcha_title_visible(
        page
    ):
        return True

    fallback_selectors = [
        'iframe[src*="captcha"]',
        '[id*="captcha"]',
        '[class*="captcha"]',
    ]

    for selector in fallback_selectors:
        try:
            locator = page.locator(
                selector
            )

            count = locator.count()

            for index in range(
                min(count, 5)
            ):
                try:
                    if locator.nth(
                        index
                    ).is_visible():
                        return True

                except Exception:
                    continue

        except Exception:
            continue

    return False


def captcha_failed_is_visible(page):
    """
    Chi xem la CAPTCHA that bai khi:
    - popup CAPTCHA dang hien;
    - dong "Khong the xac minh" dang visible.

    Khong dung rieng text "Vui long thu lai",
    de tranh nham voi loi Creator Detail.
    """

    if not captcha_title_visible(
        page
    ):
        return False

    failure_texts = [
        "Không thể xác minh",
        "Verification failed",
    ]

    for text in failure_texts:
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


def wait_for_captcha_if_needed(page):
    """
    CAPTCHA xuat hien:
    - tool dung tai creator hien tai;
    - user tu giai bang tay;
    - user quay lai Terminal nhan ENTER de kiem tra.

    Neu TikTok bao xac minh that bai:
    - Chrome van giu mo;
    - creator van giu nguyen;
    - user refresh CAPTCHA va thu lai;
    - sau do nhan ENTER de kiem tra lai.

    Go q de dung phien.
    """

    if not captcha_is_visible(
        page
    ):
        return False

    print("")
    print("=" * 70)
    print("PHAT HIEN CAPTCHA")
    print("=" * 70)

    print(
        "Tool dang TAM DUNG tai creator hien tai."
    )

    print(
        "Hay giai CAPTCHA bang tay tren Chrome."
    )

    print("")
    print(
        "Sau khi thao tac xong:"
    )

    print(
        "- quay lai Terminal;"
    )

    print(
        "- nhan ENTER de tool kiem tra;"
    )

    print(
        "- go q neu muon dung phien."
    )

    print("")
    print(
        "Tool se khong tu dong dong Chrome, "
        "khong ghi ERROR va khong chuyen creator."
    )

    print("=" * 70)

    while True:
        if captcha_failed_is_visible(
            page
        ):
            print("")
            print("=" * 70)
            print("CAPTCHA CHUA DUOC XAC MINH")
            print("=" * 70)

            print(
                "TikTok dang hien:"
            )

            print(
                "'Khong the xac minh. "
                "Vui long thu lai.'"
            )

            print("")
            print(
                "Hay bam icon refresh trong popup CAPTCHA, "
                "giai lai bang tay, roi quay lai Terminal."
            )

            print("=" * 70)

        command = input(
            "\nNhan ENTER de kiem tra lai; "
            "go q de dung phien: "
        ).strip().lower()

        if command == "q":
            raise StopSessionRequested(
                "Nguoi dung dung phien "
                "trong luc xu ly CAPTCHA."
            )

        page.wait_for_timeout(
            1500
        )

        if captcha_is_visible(
            page
        ):
            print(
                "Popup CAPTCHA van dang hien. "
                "Tool tiep tuc cho."
            )

            continue

        print("")
        print("=" * 70)
        print("CAPTCHA DA BIEN MAT")
        print("=" * 70)

        print(
            "Dang cho Creator Detail phuc hoi..."
        )

        page.wait_for_timeout(
            CAPTCHA_RECOVERY_WAIT_MS
        )

        # CAPTCHA co the xuat hien lai ngay sau do
        if captcha_is_visible(
            page
        ):
            print(
                "CAPTCHA xuat hien lai. "
                "Tool tiep tuc cho."
            )

            continue

        return True


# =========================================================
# CREATOR DETAIL STATE
# =========================================================

def creator_page_error_visible(page):
    """
    Phat hien thong bao:
    Tai du lieu khong thanh cong, vui long thu lai sau.
    """

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


def profile_is_ready(page):
    """
    Tranh coi skeleton loading la profile da load.

    Uu tien:
    - marker creator-profile-loaded;
    - hoac username span.text-head-l.
    """

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


def click_creator_retry(page):
    """
    Bam nut Thu lai cua Creator Detail.
    Chi duoc goi khi CAPTCHA khong con hien.
    """

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
                button = buttons.nth(
                    index
                )

                try:
                    if button.is_visible():
                        button.click(
                            timeout=5000
                        )

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
                item = locators.nth(
                    index
                )

                try:
                    if item.is_visible():
                        item.click(
                            timeout=5000
                        )

                        return True

                except Exception:
                    continue

        except Exception:
            continue

    return False


def wait_for_profile_ready(
    page,
    timeout_seconds=PROFILE_TIMEOUT_SECONDS,
):
    """
    Cho Creator Detail load.

    Neu CAPTCHA:
    - chuyen sang che do cho thao tac thu cong.

    Neu trang bao loi:
    - tu bam Thu lai mot so lan;
    - neu van loi, cho user xu ly bang tay;
    - khong tu dong dong Chrome.
    """

    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    retry_count = 0

    while True:
        # -----------------------------------------
        # CAPTCHA
        # -----------------------------------------

        if captcha_is_visible(
            page
        ):
            wait_for_captcha_if_needed(
                page
            )

            deadline = (
                time.monotonic()
                + timeout_seconds
            )

            retry_count = 0

            continue

        # -----------------------------------------
        # PROFILE READY
        # -----------------------------------------

        if profile_is_ready(
            page
        ) and not creator_page_error_visible(
            page
        ):
            return page.locator(
                "#creator-detail-profile-container"
            ).first

        # -----------------------------------------
        # PAGE ERROR
        # -----------------------------------------

        if creator_page_error_visible(
            page
        ):
            if retry_count < PROFILE_RETRY_LIMIT:
                retry_count += 1

                print("")
                print(
                    "Creator Detail dang bao loi. "
                    f"Tu thu lai {retry_count}/"
                    f"{PROFILE_RETRY_LIMIT}..."
                )

                clicked = click_creator_retry(
                    page
                )

                if clicked:
                    page.wait_for_timeout(
                        PROFILE_RETRY_WAIT_MS
                    )

                    continue

            print("")
            print("=" * 70)
            print("CREATOR DETAIL VAN DANG LOI")
            print("=" * 70)

            print(
                "Chrome se giu mo."
            )

            print(
                "Hay bam Thu lai tren Chrome "
                "hoac cho trang phuc hoi."
            )

            command = input(
                "\nSau khi xu ly xong, "
                "nhan ENTER de kiem tra; "
                "go q de dung phien: "
            ).strip().lower()

            if command == "q":
                raise StopSessionRequested(
                    "Nguoi dung dung phien "
                    "khi Creator Detail dang loi."
                )

            deadline = (
                time.monotonic()
                + timeout_seconds
            )

            retry_count = 0

            continue

        # -----------------------------------------
        # TIMEOUT
        # -----------------------------------------

        if time.monotonic() >= deadline:
            print("")
            print("=" * 70)
            print("CREATOR DETAIL LOAD QUA LAU")
            print("=" * 70)

            print(
                "Chrome van giu mo."
            )

            print(
                "Hay doi them, tai lai trang "
                "hoac bam Thu lai neu co."
            )

            command = input(
                "\nNhan ENTER de kiem tra lai; "
                "go q de dung phien: "
            ).strip().lower()

            if command == "q":
                raise StopSessionRequested(
                    "Nguoi dung dung phien "
                    "vi Creator Detail load qua lau."
                )

            deadline = (
                time.monotonic()
                + timeout_seconds
            )

            retry_count = 0

            continue

        page.wait_for_timeout(
            500
        )


# =========================================================
# CREATOR IDENTITY
# =========================================================

def extract_creator_identity(page):
    result = {
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
                username_candidates
                .first
                .inner_text()
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
                nickname_candidates
                .nth(index)
                .inner_text()
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


# =========================================================
# BIO
# =========================================================

def extract_bio(page):
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
                candidates
                .nth(index)
                .inner_text()
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


# =========================================================
# CONTACT POPUP
# =========================================================

def get_contact_button(page):
    profile = page.locator(
        "#creator-detail-profile-container"
    )

    zalo_icon = profile.locator(
        ".alliance-icon-Zalo_Circle"
    ).first

    if zalo_icon.count() > 0:
        print(
            "Tim thay icon Zalo."
        )

        return zalo_icon.locator(
            "xpath=ancestor::div"
            "[contains(@class,'cursor-pointer')]"
            "[1]"
        )

    email_icon = profile.locator(
        ".alliance-icon-Email"
    ).first

    if email_icon.count() > 0:
        print(
            "Khong co icon Zalo, "
            "nhung tim thay icon Email."
        )

        return email_icon.locator(
            "xpath=ancestor::div"
            "[contains(@class,'cursor-pointer')]"
            "[1]"
        )

    return None


def open_contact_popover(page):
    """
    Mo popup contact.

    Neu click lam CAPTCHA xuat hien:
    - cho user giai;
    - cho profile load lai;
    - click lai icon contact.

    Neu icon ton tai nhung popup khong mo duoc,
    raise loi de khong danh nham creator la NO_CONTACT.
    """

    had_contact_button = False

    for attempt in range(
        1,
        CONTACT_OPEN_ATTEMPTS + 1,
    ):
        wait_for_profile_ready(
            page
        )

        contact_button = get_contact_button(
            page
        )

        if contact_button is None:
            return None

        had_contact_button = True

        try:
            contact_button.click(
                timeout=10000
            )

        except Exception:
            if captcha_is_visible(
                page
            ):
                wait_for_captcha_if_needed(
                    page
                )

                continue

            if creator_page_error_visible(
                page
            ):
                continue

            raise

        # CAPTCHA co the xuat hien sau khi click
        if captcha_is_visible(
            page
        ):
            wait_for_captcha_if_needed(
                page
            )

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
            if captcha_is_visible(
                page
            ):
                wait_for_captcha_if_needed(
                    page
                )

                continue

            if creator_page_error_visible(
                page
            ):
                continue

            print(
                "Popup contact chua hien, "
                f"thu lai {attempt}/"
                f"{CONTACT_OPEN_ATTEMPTS}."
            )

            continue

        popover = (
            page.locator(
                ".core-popover-content"
            )
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
            "Co icon contact nhung "
            "khong mo duoc popup lien he."
        )

    return None


def extract_contact_popup(page):
    result = {
        "zalo": None,
        "email": None,
    }

    popover = open_contact_popover(
        page
    )

    if popover is None:
        print(
            "Creator khong co icon "
            "Zalo/Email."
        )

        return result

    popup_text = clean_text(
        popover.inner_text()
    )

    print(
        "Popup contact:",
        popup_text,
    )

    # -----------------------------------------
    # ZALO
    # -----------------------------------------

    zalo_label = (
        popover
        .locator("span")
        .filter(
            has_text="Zalo:"
        )
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

    # -----------------------------------------
    # EMAIL
    # -----------------------------------------

    email_label = (
        popover
        .locator("span")
        .filter(
            has_text="Email:"
        )
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
                result["email"] = (
                    email.lower()
                )

    # -----------------------------------------
    # CLOSE POPUP
    # -----------------------------------------

    try:
        close_button = popover.get_by_role(
            "button",
            name="Đã hiểu",
        )

        if close_button.count() > 0:
            close_button.click(
                timeout=3000
            )

    except Exception:
        pass

    return result


# =========================================================
# MERGE CONTACT
# =========================================================

def merge_contacts(
    popup_contact,
    bio,
):
    bio_phones = find_all_phones(
        bio or ""
    )

    bio_emails = find_all_emails(
        bio or ""
    )

    official_zalo = popup_contact.get(
        "zalo"
    )

    official_email = popup_contact.get(
        "email"
    )

    all_phones = []
    phone_sources = {}

    if official_zalo:
        all_phones.append(
            official_zalo
        )

        phone_sources.setdefault(
            official_zalo,
            [],
        )

        phone_sources[
            official_zalo
        ].append(
            "Zalo"
        )

    for phone in bio_phones:
        if phone not in all_phones:
            all_phones.append(
                phone
            )

        phone_sources.setdefault(
            phone,
            [],
        )

        if (
            "Bio"
            not in phone_sources[phone]
        ):
            phone_sources[
                phone
            ].append(
                "Bio"
            )

    all_emails = []
    email_sources = {}

    if official_email:
        all_emails.append(
            official_email
        )

        email_sources.setdefault(
            official_email,
            [],
        )

        email_sources[
            official_email
        ].append(
            "Contact"
        )

    for email in bio_emails:
        if email not in all_emails:
            all_emails.append(
                email
            )

        email_sources.setdefault(
            email,
            [],
        )

        if (
            "Bio"
            not in email_sources[email]
        ):
            email_sources[
                email
            ].append(
                "Bio"
            )

    return {
        "official_zalo":
            official_zalo,

        "official_email":
            official_email,

        "bio":
            bio,

        "bio_phones":
            bio_phones,

        "bio_emails":
            bio_emails,

        "all_phones":
            all_phones,

        "all_emails":
            all_emails,

        "phone_sources":
            phone_sources,

        "email_sources":
            email_sources,
    }


# =========================================================
# NOTE / STATUS
# =========================================================

def build_contact_note(result):
    notes = []

    official_zalo = result[
        "official_zalo"
    ]

    extra_bio_phones = [
        phone
        for phone in result["bio_phones"]
        if phone != official_zalo
    ]

    if extra_bio_phones:
        notes.append(
            "SDT khac trong Bio: "
            + ", ".join(
                extra_bio_phones
            )
        )

    if result["bio"]:
        notes.append(
            "Bio: "
            + result["bio"]
        )

    return " | ".join(
        notes
    )


def determine_status(result):
    has_phone = bool(
        result["all_phones"]
    )

    has_email = bool(
        result["all_emails"]
    )

    if has_phone and has_email:
        return "FOUND_PHONE_EMAIL"

    if has_phone:
        return "FOUND_PHONE"

    if has_email:
        return "FOUND_EMAIL"

    return "NO_CONTACT"


# =========================================================
# PROCESS ONE CREATOR
# =========================================================

def process_creator(
    page,
    creator_id,
):
    url = (
        f"{CREATOR_DETAIL_BASE_URL}"
        f"?cid={creator_id}"
    )

    print("")
    print("=" * 70)

    print(
        "DANG XU LY CREATOR:",
        creator_id,
    )

    print("=" * 70)

    set_creator_status(
        creator_id,
        "PROCESSING",
    )

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=120000,
    )

    wait_for_profile_ready(
        page
    )

    page.wait_for_timeout(
        1000
    )

    identity = extract_creator_identity(
        page
    )

    bio = extract_bio(
        page
    )

    print(
        "Username:",
        identity["username"],
    )

    print(
        "Nickname:",
        identity["nickname"],
    )

    print(
        "Bio:",
        bio,
    )

    popup_contact = (
        extract_contact_popup(
            page
        )
    )

    result = merge_contacts(
        popup_contact,
        bio,
    )

    status = determine_status(
        result
    )

    contact_note = build_contact_note(
        result
    )

    data = {
        "creator_id":
            creator_id,

        "username":
            identity["username"],

        "nickname":
            identity["nickname"],

        "detail_url":
            page.url,

        "zalo":
            result["official_zalo"],

        "official_email":
            result["official_email"],

        "bio":
            result["bio"],

        "bio_phones":
            json.dumps(
                result["bio_phones"],
                ensure_ascii=False,
            ),

        "bio_emails":
            json.dumps(
                result["bio_emails"],
                ensure_ascii=False,
            ),

        "phones_all":
            json.dumps(
                result["all_phones"],
                ensure_ascii=False,
            ),

        "emails_all":
            json.dumps(
                result["all_emails"],
                ensure_ascii=False,
            ),

        "phone_sources":
            json.dumps(
                result["phone_sources"],
                ensure_ascii=False,
            ),

        "email_sources":
            json.dumps(
                result["email_sources"],
                ensure_ascii=False,
            ),

        "contact_note":
            contact_note,

        "status":
            status,
    }

    save_creator(
        data
    )

    print("")
    print("KET QUA:")

    print(
        "Zalo:",
        result["official_zalo"],
    )

    print(
        "Email:",
        result["official_email"],
    )

    print(
        "Tat ca SDT:",
        result["all_phones"],
    )

    print(
        "Status:",
        status,
    )

    return status


# =========================================================
# DELAY
# =========================================================

def creator_delay():
    seconds = random.uniform(
        MIN_CREATOR_DELAY,
        MAX_CREATOR_DELAY,
    )

    print(
        f"Nghi {seconds:.1f} giay..."
    )

    time.sleep(
        seconds
    )


def batch_pause(
    processed_count,
):
    if processed_count <= 0:
        return

    if (
        processed_count
        % BATCH_SIZE
        != 0
    ):
        return

    print("")
    print("=" * 70)

    print(
        f"DA XU LY "
        f"{processed_count} CREATOR"
    )

    print("=" * 70)

    print(
        f"Tam nghi "
        f"{BATCH_PAUSE_SECONDS} giay."
    )

    print("=" * 70)

    time.sleep(
        BATCH_PAUSE_SECONDS
    )


# =========================================================
# PLAYWRIGHT ERROR
# =========================================================

def is_browser_closed_error(exc):
    text = str(
        exc
    ).lower()

    patterns = (
        "target page, context or browser has been closed",
        "browser has been closed",
        "context has been closed",
        "page has been closed",
        "target closed",
    )

    return any(
        pattern in text
        for pattern in patterns
    )


# =========================================================
# MAIN
# =========================================================

def main():
    init_database()

    reset_stuck_processing()

    print_database_path()

    pending_creators = (
        get_pending_creators()
    )

    total_pending = len(
        pending_creators
    )

    pending_creators = (
        pending_creators[
            :PROCESS_LIMIT
        ]
    )

    print("")
    print("=" * 70)
    print("TRANG THAI XU LY")
    print("=" * 70)

    print(
        "Tong creator trong DB:",
        count_creators(),
    )

    print(
        "Tong creator can xu ly:",
        total_pending,
    )

    print(
        "Gioi han phien:",
        PROCESS_LIMIT,
    )

    print(
        "Se xu ly:",
        len(pending_creators),
    )

    print("=" * 70)

    if not pending_creators:
        print(
            "Khong co creator can xu ly."
        )

        print(
            "Hay chay creator_collector.py "
            "de bo sung creator moi."
        )

        return

    current_creator_id = None
    processed_count = 0
    stop_requested = False

    try:
        with sync_playwright() as p:
            context = launch_browser_context(
                p
            )

            page = (
                context.pages[0]
                if context.pages
                else context.new_page()
            )

            total = len(
                pending_creators
            )

            for index, creator_id in enumerate(
                pending_creators,
                start=1,
            ):
                current_creator_id = (
                    creator_id
                )

                print("")
                print("#" * 70)

                print(
                    f"[{index}/{total}] "
                    f"Creator ID: {creator_id}"
                )

                print("#" * 70)

                if should_skip_creator(
                    creator_id
                ):
                    print(
                        "Creator da xu ly "
                        "-> SKIP."
                    )

                    continue

                success = False

                try:
                    process_creator(
                        page,
                        creator_id,
                    )

                    processed_count += 1
                    success = True

                except StopSessionRequested as exc:
                    set_creator_status(
                        creator_id,
                        "PENDING",
                    )

                    print("")
                    print("=" * 70)
                    print("DA DUNG PHIEN")
                    print("=" * 70)

                    print(
                        str(exc)
                    )

                    print(
                        "Creator hien tai "
                        "da dua ve PENDING:"
                    )

                    print(
                        creator_id
                    )

                    stop_requested = True

                    break

                except KeyboardInterrupt:
                    set_creator_status(
                        creator_id,
                        "PENDING",
                    )

                    print("")
                    print(
                        "Nhan Ctrl+C. "
                        "Creator hien tai "
                        "da dua ve PENDING."
                    )

                    stop_requested = True

                    break

                except PlaywrightError as exc:
                    if is_browser_closed_error(
                        exc
                    ):
                        set_creator_status(
                            creator_id,
                            "PENDING",
                        )

                        raise

                    print("")
                    print(
                        "LOI PLAYWRIGHT CREATOR:",
                        creator_id,
                    )

                    print(
                        str(exc)
                    )

                    save_error(
                        creator_id,
                        exc,
                    )

                except Exception as exc:
                    print("")
                    print(
                        "LOI CREATOR:",
                        creator_id,
                    )

                    print(
                        str(exc)
                    )

                    save_error(
                        creator_id,
                        exc,
                    )

                creator_delay()

                if success:
                    batch_pause(
                        processed_count
                    )

            if stop_requested:
                print("")
                print(
                    "Chrome van dang duoc giu mo."
                )

                input(
                    "Nhan ENTER khi ban muon "
                    "dong Chrome va ket thuc: "
                )

            try:
                context.close()
            except Exception:
                pass

    except PlaywrightError as exc:
        print("")
        print("=" * 70)

        if is_browser_closed_error(
            exc
        ):
            print(
                "BROWSER DA BI DONG"
            )

            if current_creator_id:
                try:
                    set_creator_status(
                        current_creator_id,
                        "PENDING",
                    )
                except Exception:
                    pass

        else:
            print(
                "PLAYWRIGHT GAP LOI"
            )

            print(
                str(exc)
            )

        print("=" * 70)

    print("")
    print("=" * 70)

    print(
        "TRANG THAI DATABASE HIEN TAI"
    )

    print("=" * 70)

    print(
        "Tong creator:",
        count_creators(),
    )

    for status, total in (
        count_by_status().items()
    ):
        print(
            f"{status}: {total}"
        )

    print("=" * 70)


if __name__ == "__main__":
    main()