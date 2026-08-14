from pathlib import Path
import time

from playwright.sync_api import (
    Error as PlaywrightError,
    sync_playwright,
)

from database import (
    count_creators,
    get_connection,
    init_database,
)


# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = BASE_DIR / "browser_data"

AFFILIATE_URL = (
    "https://affiliate.tiktok.com/"
)

QUEUE_TARGET = 500


# =========================================================
# SCROLL CONFIG
# =========================================================

# Fast-forward qua creator da co trong DB
FAST_SCROLL_STEP = 1800
FAST_WAIT = 0.60

# Khi bat dau gap creator moi
NORMAL_SCROLL_STEP = 700
NORMAL_WAIT = 1.20

BOTTOM_EXTRA_WAIT = 1.80
MAX_STAGNANT_BOTTOM_ROUNDS = 5


# =========================================================
# CUSTOM EXCEPTION
# =========================================================

class StopSessionRequested(Exception):
    pass


# =========================================================
# BROWSER
# =========================================================

def launch_browser_context(playwright):
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
                    "Fallback sang Chromium "
                    "cua Playwright..."
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
# CAPTCHA
# =========================================================

def captcha_title_visible(page):
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
    if captcha_title_visible(
        page
    ):
        return True

    selectors = [
        'iframe[src*="captcha"]',
        '[id*="captcha"]',
        '[class*="captcha"]',
    ]

    for selector in selectors:
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
    Collector dung tai cho cho den khi:
    - user giai CAPTCHA;
    - user quay lai Terminal nhan ENTER;
    - CAPTCHA thuc su bien mat.

    Go q de dung collector.
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
        "Collector dang TAM DUNG."
    )

    print(
        "Hay giai CAPTCHA bang tay tren Chrome."
    )

    print(
        "Sau khi thao tac xong, "
        "quay lai Terminal va nhan ENTER."
    )

    print(
        "Go q neu muon dung collector."
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
                "Hay refresh CAPTCHA, "
                "giai lai bang tay va nhan ENTER."
            )

        command = input(
            "\nNhan ENTER de kiem tra lai; "
            "go q de dung: "
        ).strip().lower()

        if command == "q":
            raise StopSessionRequested(
                "Nguoi dung dung collector "
                "trong luc CAPTCHA dang hien."
            )

        page.wait_for_timeout(
            1500
        )

        if captcha_is_visible(
            page
        ):
            print(
                "CAPTCHA van dang hien. "
                "Collector tiep tuc cho."
            )

            continue

        print(
            "CAPTCHA da bien mat. "
            "Cho trang on dinh..."
        )

        page.wait_for_timeout(
            2500
        )

        if captcha_is_visible(
            page
        ):
            continue

        return True


# =========================================================
# DATABASE HELPERS
# =========================================================

def count_pending_creators():
    connection = get_connection()

    row = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM creators
        WHERE status = 'PENDING'
        """
    ).fetchone()

    connection.close()

    return int(
        row["total"]
    )


def count_completed_creators():
    connection = get_connection()

    row = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM creators
        WHERE status IN (
            'FOUND_PHONE_EMAIL',
            'FOUND_PHONE',
            'FOUND_EMAIL',
            'NO_CONTACT'
        )
        """
    ).fetchone()

    connection.close()

    return int(
        row["total"]
    )


def get_existing_creator_ids(
    creator_ids,
):
    if not creator_ids:
        return set()

    result = set()
    connection = get_connection()

    try:
        chunk_size = 500

        for start in range(
            0,
            len(creator_ids),
            chunk_size,
        ):
            chunk = creator_ids[
                start:start + chunk_size
            ]

            if not chunk:
                continue

            placeholders = ",".join(
                "?"
                for _ in chunk
            )

            rows = connection.execute(
                f"""
                SELECT creator_id
                FROM creators
                WHERE creator_id IN (
                    {placeholders}
                )
                """,
                chunk,
            ).fetchall()

            for row in rows:
                result.add(
                    str(
                        row["creator_id"]
                    )
                )

    finally:
        connection.close()

    return result


def insert_creators_pending(
    creator_ids,
    max_to_insert,
):
    if (
        not creator_ids
        or max_to_insert <= 0
    ):
        return []

    creator_ids = creator_ids[
        :max_to_insert
    ]

    inserted = []
    connection = get_connection()

    try:
        for creator_id in creator_ids:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO creators (
                    creator_id,
                    status,
                    first_seen_at,
                    last_checked_at
                )
                VALUES (
                    ?,
                    'PENDING',
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """,
                (creator_id,),
            )

            if cursor.rowcount > 0:
                inserted.append(
                    creator_id
                )

        connection.commit()

    finally:
        connection.close()

    return inserted


# =========================================================
# DOM
# =========================================================

def collect_visible_creator_ids(page):
    """
    Lay toan bo value checkbox trong mot lan evaluate,
    nhanh hon query tung row bang Playwright.
    """

    values = (
        page.locator(
            '#creator-list-content '
            'tbody input[type="checkbox"]'
        )
        .evaluate_all(
            """
            elements => elements.map(
                element => element.value
            )
            """
        )
    )

    creator_ids = []

    for value in values:
        if value is None:
            continue

        creator_id = str(
            value
        ).strip()

        if not creator_id.isdigit():
            continue

        creator_ids.append(
            creator_id
        )

    return list(
        dict.fromkeys(
            creator_ids
        )
    )


# =========================================================
# SCROLL
# =========================================================

def get_scroll_container(page):
    container = page.locator(
        "div.flex.flex-col.m-auto."
        "bg-neutral-bg2.box-border."
        "min-h-full.overflow-auto"
    ).first

    if container.count() == 0:
        raise RuntimeError(
            "Khong tim thay scroll container TikTok."
        )

    return container


def get_scroll_info(container):
    return container.evaluate(
        """
        element => ({
            scrollTop: element.scrollTop,
            scrollHeight: element.scrollHeight,
            clientHeight: element.clientHeight
        })
        """
    )


def is_at_bottom(info):
    return (
        info["scrollTop"]
        + info["clientHeight"]
        >= info["scrollHeight"] - 30
    )


def scroll_down(
    container,
    fast_mode,
):
    if fast_mode:
        step = FAST_SCROLL_STEP
        wait_seconds = FAST_WAIT

    else:
        step = NORMAL_SCROLL_STEP
        wait_seconds = NORMAL_WAIT

    container.evaluate(
        f"""
        element => {{
            const maxTop = Math.max(
                0,
                element.scrollHeight
                - element.clientHeight
            );

            element.scrollTop = Math.min(
                maxTop,
                element.scrollTop + {step}
            );
        }}
        """
    )

    time.sleep(
        wait_seconds
    )


# =========================================================
# PROCESS IDS
# =========================================================

def process_new_dom_ids(
    visible_ids,
    seen_session_ids,
):
    new_session_ids = []

    for creator_id in visible_ids:
        if creator_id in seen_session_ids:
            continue

        seen_session_ids.add(
            creator_id
        )

        new_session_ids.append(
            creator_id
        )

    if not new_session_ids:
        return {
            "new_session_ids": [],
            "old_db_ids": [],
            "inserted_ids": [],
        }

    existing = get_existing_creator_ids(
        new_session_ids
    )

    old_ids = [
        creator_id
        for creator_id in new_session_ids
        if creator_id in existing
    ]

    new_ids = [
        creator_id
        for creator_id in new_session_ids
        if creator_id not in existing
    ]

    remaining = max(
        0,
        QUEUE_TARGET
        - count_pending_creators(),
    )

    inserted = insert_creators_pending(
        new_ids,
        remaining,
    )

    return {
        "new_session_ids":
            new_session_ids,

        "old_db_ids":
            old_ids,

        "inserted_ids":
            inserted,
    }


# =========================================================
# COLLECT
# =========================================================

def collect_until_queue_target(page):
    pending_before = (
        count_pending_creators()
    )

    print("")
    print("=" * 70)
    print("TRANG THAI DATABASE")
    print("=" * 70)

    print(
        "Tong creator DB:",
        count_creators(),
    )

    print(
        "Da xu ly:",
        count_completed_creators(),
    )

    print(
        "Dang PENDING:",
        pending_before,
    )

    print(
        "Muc tieu:",
        QUEUE_TARGET,
    )

    print("=" * 70)

    if pending_before >= QUEUE_TARGET:
        return {
            "new_added": 0,
            "pending_total":
                pending_before,
            "old_skipped": 0,
        }

    page.locator(
        "#creator-list-content"
    ).wait_for(
        state="visible",
        timeout=30000,
    )

    page.wait_for_timeout(
        1500
    )

    scroll_container = (
        get_scroll_container(
            page
        )
    )

    scroll_container.evaluate(
        """
        element => {
            element.scrollTop = 0;
        }
        """
    )

    time.sleep(
        NORMAL_WAIT
    )

    seen_session_ids = set()

    fast_mode = True
    total_new = 0
    total_old = 0

    round_number = 0
    stagnant = 0

    while True:
        wait_for_captcha_if_needed(
            page
        )

        round_number += 1

        visible = collect_visible_creator_ids(
            page
        )

        result = process_new_dom_ids(
            visible,
            seen_session_ids,
        )

        old_count = len(
            result["old_db_ids"]
        )

        inserted_count = len(
            result["inserted_ids"]
        )

        total_old += old_count
        total_new += inserted_count

        if inserted_count > 0:
            if fast_mode:
                print("")
                print(
                    ">>> Da gap creator moi."
                )

                print(
                    ">>> Chuyen FAST -> NORMAL."
                )

            fast_mode = False

        pending = count_pending_creators()
        info = get_scroll_info(
            scroll_container
        )

        print(
            f"Round {round_number}: "
            f"DOM={len(visible)} | "
            f"Cu={old_count} | "
            f"Moi={inserted_count} | "
            f"Moi phien={total_new} | "
            f"PENDING={pending}/{QUEUE_TARGET} | "
            f"Mode={'FAST' if fast_mode else 'NORMAL'} | "
            f"Scroll="
            f"{int(info['scrollTop'])}/"
            f"{int(info['scrollHeight'])}"
        )

        if pending >= QUEUE_TARGET:
            break

        if is_at_bottom(
            info
        ):
            old_height = int(
                info["scrollHeight"]
            )

            old_dom_count = len(
                visible
            )

            time.sleep(
                BOTTOM_EXTRA_WAIT
            )

            wait_for_captcha_if_needed(
                page
            )

            updated_info = get_scroll_info(
                scroll_container
            )

            updated_dom_count = len(
                collect_visible_creator_ids(
                    page
                )
            )

            if (
                int(
                    updated_info[
                        "scrollHeight"
                    ]
                )
                > old_height
                or updated_dom_count
                > old_dom_count
            ):
                stagnant = 0

            else:
                stagnant += 1

                print(
                    "  -> Bottom stagnant:",
                    f"{stagnant}/"
                    f"{MAX_STAGNANT_BOTTOM_ROUNDS}"
                )

        else:
            stagnant = 0

        if (
            stagnant
            >= MAX_STAGNANT_BOTTOM_ROUNDS
        ):
            print(
                "TikTok khong load them creator."
            )

            break

        scroll_down(
            scroll_container,
            fast_mode,
        )

    return {
        "new_added":
            total_new,

        "pending_total":
            count_pending_creators(),

        "old_skipped":
            total_old,
    }


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

    pending = count_pending_creators()

    if pending >= QUEUE_TARGET:
        print("")
        print("=" * 70)
        print("KHONG CAN QUET TIKTOK")
        print("=" * 70)

        print(
            "PENDING:",
            pending,
        )

        print(
            "Hay chay:"
        )

        print(
            "python3 main.py"
        )

        return

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

            page.goto(
                AFFILIATE_URL,
                wait_until="domcontentloaded",
                timeout=120000,
            )

            print("")
            print("=" * 70)
            print("TRINH DUYET DA MO")
            print("=" * 70)

            print(
                "1. Vao trang Tim nha sang tao."
            )

            print(
                "2. Ap dung dung bo loc."
            )

            print(
                "3. Cho danh sach hien ra."
            )

            print(
                "4. Quay lai Terminal va nhan ENTER."
            )

            input(
                "\nNhan ENTER khi danh sach "
                "da san sang: "
            )

            try:
                result = collect_until_queue_target(
                    page
                )

                print("")
                print("=" * 70)
                print("KET QUA COLLECTOR")
                print("=" * 70)

                print(
                    "Creator moi:",
                    result["new_added"],
                )

                print(
                    "Creator cu bo qua:",
                    result["old_skipped"],
                )

                print(
                    "PENDING:",
                    result["pending_total"],
                )

                print(
                    "Tong DB:",
                    count_creators(),
                )

                print("=" * 70)

            except StopSessionRequested as exc:
                stop_requested = True

                print("")
                print("=" * 70)
                print("DA DUNG COLLECTOR")
                print("=" * 70)

                print(
                    str(exc)
                )

                print(
                    "CID da tim thay truoc do "
                    "van duoc luu trong SQLite."
                )

                print(
                    "PENDING hien tai:",
                    count_pending_creators(),
                )

            if stop_requested:
                print(
                    "Chrome van dang duoc giu mo."
                )

            input(
                "\nNhan ENTER khi ban muon "
                "dong Chrome: "
            )

            try:
                context.close()
            except Exception:
                pass

    except KeyboardInterrupt:
        print(
            "Da dung collector bang Ctrl+C."
        )

        print(
            "PENDING:",
            count_pending_creators(),
        )

    except PlaywrightError as exc:
        if is_browser_closed_error(
            exc
        ):
            print(
                "Browser da bi dong."
            )

        else:
            print(
                "Playwright error:"
            )

            print(
                str(exc)
            )

        print(
            "PENDING:",
            count_pending_creators(),
        )

    except Exception as exc:
        print(
            "Collector error:"
        )

        print(
            str(exc)
        )

        print(
            "PENDING:",
            count_pending_creators(),
        )


if __name__ == "__main__":
    main()