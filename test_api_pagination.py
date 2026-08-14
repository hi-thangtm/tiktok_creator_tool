from copy import deepcopy
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Request,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = BASE_DIR / "browser_data"

AFFILIATE_URL = "https://affiliate.tiktok.com/"

MARKETPLACE_FIND_PATH = (
    "/api/v1/oec/affiliate/creator/marketplace/find"
)

# Cho toi da 10 phut de thao tac tren trinh duyet
CAPTURE_TIMEOUT = 600_000


# =========================================================
# REQUEST HELPERS
# =========================================================

def get_request_payload(
    request: Request,
) -> dict[str, Any] | None:
    try:
        payload = request.post_data_json
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    return payload


def is_target_pagination_request(
    request: Request,
) -> bool:
    """
    Chi bat request phan trang thuc su:

    - POST marketplace/find
    - page >= 1
    - co next_item_cursor

    Bo qua cac request page=0 ban dau.
    """

    if request.method != "POST":
        return False

    if MARKETPLACE_FIND_PATH not in request.url:
        return False

    payload = get_request_payload(
        request
    )

    if not payload:
        return False

    pagination = payload.get(
        "pagination"
    )

    if not isinstance(
        pagination,
        dict,
    ):
        return False

    try:
        page_number = int(
            pagination.get("page")
            or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        return False

    cursor = pagination.get(
        "next_item_cursor"
    )

    return (
        page_number >= 1
        and cursor is not None
    )


def sanitize_request_headers(
    headers: dict[str, str],
) -> dict[str, str]:
    """
    Loai header ma Playwright tu tinh lai
    hoac lay tu cookie jar cua browser context.
    """

    blocked_headers = {
        "accept-encoding",
        "connection",
        "content-length",
        "cookie",
        "host",
    }

    result: dict[str, str] = {}

    for key, value in headers.items():
        normalized_key = key.lower()

        if normalized_key in blocked_headers:
            continue

        if normalized_key.startswith(":"):
            continue

        result[key] = value

    result["content-type"] = (
        "application/json;charset=UTF-8"
    )

    return result


# =========================================================
# RESPONSE HELPERS
# =========================================================

def get_response_root(
    body: dict[str, Any],
) -> dict[str, Any]:
    """
    Ho tro response co du lieu:
    - nam ngay o root
    - hoac nam trong body["data"]
    """

    data = body.get("data")

    if isinstance(data, dict):
        return data

    return body


def extract_creator_ids(
    body: dict[str, Any],
) -> list[str]:
    root = get_response_root(
        body
    )

    creator_ids: list[str] = []

    # Uu tien creator_profile_list
    profile_list = root.get(
        "creator_profile_list",
        [],
    )

    if isinstance(profile_list, list):
        for item in profile_list:
            if not isinstance(
                item,
                dict,
            ):
                continue

            creator_id = item.get(
                "creator_id"
            )

            if creator_id is None:
                continue

            creator_id = str(
                creator_id
            ).strip()

            if (
                creator_id
                and creator_id
                not in creator_ids
            ):
                creator_ids.append(
                    creator_id
                )

    if creator_ids:
        return creator_ids

    # Fallback creator_connect_info_list
    connect_list = root.get(
        "creator_connect_info_list",
        [],
    )

    if isinstance(connect_list, list):
        for item in connect_list:
            if not isinstance(
                item,
                dict,
            ):
                continue

            creator_id = item.get(
                "creator_id"
            )

            if creator_id is None:
                continue

            creator_id = str(
                creator_id
            ).strip()

            if (
                creator_id
                and creator_id
                not in creator_ids
            ):
                creator_ids.append(
                    creator_id
                )

    return creator_ids


def extract_next_pagination(
    body: dict[str, Any],
) -> dict[str, Any]:
    root = get_response_root(
        body
    )

    pagination = root.get(
        "next_pagination",
        {},
    )

    if isinstance(pagination, dict):
        return pagination

    return {}


# =========================================================
# API TEST
# =========================================================

def send_api_request(
    api,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    label: str,
) -> tuple[
    bool,
    dict[str, Any] | None,
    list[str],
]:
    print("")
    print(
        "=" * 60
    )
    print(label)
    print(
        "=" * 60
    )

    pagination = payload.get(
        "pagination",
        {},
    )

    print(
        "Gui page:",
        pagination.get("page"),
    )

    print(
        "Gui cursor:",
        pagination.get(
            "next_item_cursor"
        ),
    )

    print(
        "Gui size:",
        pagination.get("size"),
    )

    try:
        response = api.post(
            url,
            headers=headers,
            data=payload,
            timeout=120_000,
        )

    except Exception as exc:
        print(
            "Loi khi gui request:"
        )
        print(
            str(exc)
        )

        return (
            False,
            None,
            [],
        )

    print(
        "HTTP status:",
        response.status,
    )

    try:
        body = response.json()

    except Exception:
        print(
            "Response khong phai JSON."
        )

        try:
            print(
                response.text()[:1000]
            )
        except Exception:
            pass

        return (
            False,
            None,
            [],
        )

    if not isinstance(
        body,
        dict,
    ):
        print(
            "Response JSON khong phai object."
        )

        return (
            False,
            None,
            [],
        )

    code = body.get("code")
    message = body.get("message")

    creator_ids = extract_creator_ids(
        body
    )

    next_pagination = (
        extract_next_pagination(
            body
        )
    )

    print(
        "TikTok code:",
        code,
    )

    print(
        "TikTok message:",
        message,
    )

    print(
        "So creator:",
        len(creator_ids),
    )

    print(
        "Creator IDs:",
        creator_ids,
    )

    print(
        "next_pagination:",
        next_pagination,
    )

    success = (
        response.ok
        and (
            code == 0
            or code is None
        )
        and len(creator_ids) > 0
    )

    return (
        success,
        body,
        creator_ids,
    )


# =========================================================
# MAIN
# =========================================================

def main() -> None:
    with sync_playwright() as p:
        context = (
            p.chromium
            .launch_persistent_context(
                user_data_dir=str(
                    USER_DATA_DIR
                ),
                headless=False,
                viewport={
                    "width": 1440,
                    "height": 900,
                },
            )
        )

        page = (
            context.pages[0]
            if context.pages
            else context.new_page()
        )

        page.goto(
            AFFILIATE_URL,
            wait_until="domcontentloaded",
            timeout=120_000,
        )

        print("")
        print(
            "=" * 60
        )
        print(
            "TEST API PAGINATION"
        )
        print(
            "=" * 60
        )
        print("")
        print(
            "1. Vao trang Tim nha sang tao."
        )
        print(
            "2. Ap dung dung bo loc ban muon."
        )
        print(
            "3. Cho danh sach creator hien ra."
        )
        print(
            "4. Scroll xuong cho TikTok load "
            "them it nhat 1 batch."
        )
        print(
            "5. KHONG can quay lai Terminal "
            "de nhan ENTER."
        )
        print("")
        print(
            "Tool dang cho request page >= 1..."
        )

        # =================================================
        # CHO REQUEST THUC SU, KHONG DUNG input()
        # =================================================

        try:
            captured_request = (
                page.wait_for_event(
                    "request",
                    predicate=(
                        is_target_pagination_request
                    ),
                    timeout=CAPTURE_TIMEOUT,
                )
            )

        except PlaywrightTimeoutError:
            print("")
            print(
                "Het thoi gian cho request."
            )
            print(
                "Khong bat duoc marketplace/find "
                "page >= 1."
            )

            context.close()
            return

        captured_payload = (
            get_request_payload(
                captured_request
            )
        )

        if not captured_payload:
            print(
                "Khong doc duoc payload."
            )

            context.close()
            return

        captured_headers = (
            sanitize_request_headers(
                captured_request.all_headers()
            )
        )

        captured_url = (
            captured_request.url
        )

        captured_pagination = (
            captured_payload.get(
                "pagination",
                {},
            )
        )

        print("")
        print(
            "=" * 60
        )
        print(
            "DA BAT REQUEST TIKTOK"
        )
        print(
            "=" * 60
        )

        print(
            "Page:",
            captured_pagination.get(
                "page"
            ),
        )

        print(
            "Cursor:",
            captured_pagination.get(
                "next_item_cursor"
            ),
        )

        print(
            "Size:",
            captured_pagination.get(
                "size"
            ),
        )

        print(
            "Search key:",
            str(
                captured_pagination.get(
                    "search_key",
                    "",
                )
            )[:12]
            + "...",
        )

        # =================================================
        # TEST 1: REPLAY REQUEST NGUYEN BAN
        # =================================================

        (
            replay_success,
            replay_body,
            replay_ids,
        ) = send_api_request(
            api=context.request,
            url=captured_url,
            headers=captured_headers,
            payload=captured_payload,
            label="REPLAY NGUYEN BAN",
        )

        if not replay_success:
            print("")
            print(
                "=" * 60
            )
            print(
                "KET LUAN"
            )
            print(
                "=" * 60
            )

            print(
                "Khong replay duoc request "
                "nguyen ban."
            )

            print(
                "Khong the trien khai checkpoint "
                "bang API direct theo cach nay."
            )

            input(
                "\nNhan ENTER de dong trinh duyet..."
            )

            context.close()
            return

        # =================================================
        # TAO PAYLOAD TRANG KE TIEP
        # =================================================

        next_payload = deepcopy(
            captured_payload
        )

        next_payload_pagination = (
            next_payload.get(
                "pagination"
            )
        )

        if not isinstance(
            next_payload_pagination,
            dict,
        ):
            print(
                "Pagination khong hop le."
            )

            context.close()
            return

        size = int(
            next_payload_pagination.get(
                "size",
                12,
            )
            or 12
        )

        current_page = int(
            next_payload_pagination.get(
                "page",
                0,
            )
            or 0
        )

        current_cursor = int(
            next_payload_pagination.get(
                "next_item_cursor",
                0,
            )
            or 0
        )

        replay_next_pagination = {}

        if replay_body:
            replay_next_pagination = (
                extract_next_pagination(
                    replay_body
                )
            )

        response_next_page = (
            replay_next_pagination.get(
                "next_page"
            )
        )

        response_next_cursor = (
            replay_next_pagination.get(
                "next_item_cursor"
            )
        )

        response_search_key = (
            replay_next_pagination.get(
                "search_key"
            )
        )

        if response_next_page is not None:
            next_payload_pagination[
                "page"
            ] = int(
                response_next_page
            )
        else:
            next_payload_pagination[
                "page"
            ] = (
                current_page + 1
            )

        if response_next_cursor is not None:
            next_payload_pagination[
                "next_item_cursor"
            ] = int(
                response_next_cursor
            )
        else:
            next_payload_pagination[
                "next_item_cursor"
            ] = (
                current_cursor + size
            )

        if response_search_key:
            next_payload_pagination[
                "search_key"
            ] = response_search_key

        # =================================================
        # TEST 2: GUI REQUEST TRANG KE TIEP
        # =================================================

        (
            next_success,
            _next_body,
            next_ids,
        ) = send_api_request(
            api=context.request,
            url=captured_url,
            headers=captured_headers,
            payload=next_payload,
            label="REQUEST TRANG KE TIEP",
        )

        print("")
        print(
            "=" * 60
        )
        print(
            "KET LUAN"
        )
        print(
            "=" * 60
        )

        if not next_success:
            print(
                "Replay request nguyen ban thanh cong, "
                "nhung sua page/cursor bi tu choi."
            )

            print(
                "Chu ky dong co the phu thuoc payload."
            )

            print(
                "Chua the dung API checkpoint direct."
            )

        elif (
            replay_ids
            and next_ids
            and set(replay_ids)
            != set(next_ids)
        ):
            print(
                "THANH CONG."
            )

            print(
                "Request trang ke tiep tra ve "
                "batch creator khac."
            )

            print(
                "Co the luu checkpoint:"
            )

            print(
                "- search_key"
            )
            print(
                "- page"
            )
            print(
                "- next_item_cursor"
            )

        else:
            print(
                "Request trang ke tiep thanh cong, "
                "nhung creator IDs van giong batch cu."
            )

            print(
                "Can kiem tra lai quy tac pagination."
            )

        print(
            "=" * 60
        )

        input(
            "\nNhan ENTER de dong trinh duyet..."
        )

        context.close()


if __name__ == "__main__":
    main()