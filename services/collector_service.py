from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from core.constants import (
    BOTTOM_EXTRA_WAIT,
    FAST_SCROLL_STEP,
    FAST_WAIT,
    MAX_STAGNANT_BOTTOM_ROUNDS,
    NORMAL_SCROLL_STEP,
    NORMAL_WAIT,
    QUEUE_TARGET,
)
from core.database import DatabaseRepository


class StopSessionRequested(Exception):
    pass


@dataclass(frozen=True)
class CollectorProgress:
    round_number: int
    visible_count: int
    old_count: int
    inserted_count: int
    total_old: int
    total_new: int
    pending_total: int
    target: int
    fast_mode: bool
    scroll_top: int
    scroll_height: int


@dataclass(frozen=True)
class CollectorResult:
    new_added: int
    pending_total: int
    old_skipped: int


class CollectorController(Protocol):
    def check_pause_or_stop(self) -> None:
        ...

    def wait_for_captcha(self, page) -> None:
        ...

    def sleep(self, seconds: float) -> None:
        ...

    def log(self, message: str) -> None:
        ...

    def emit_progress(
        self,
        progress: CollectorProgress,
    ) -> None:
        ...

    def emit_creator_inserted(
        self,
        creator_id: str,
    ) -> None:
        ...


def captcha_title_visible(page) -> bool:
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


def captcha_is_visible(page) -> bool:
    if captcha_title_visible(page):
        return True

    selectors = [
        'iframe[src*="captcha"]',
        '[id*="captcha"]',
        '[class*="captcha"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()

            for index in range(min(count, 5)):
                try:
                    if locator.nth(index).is_visible():
                        return True
                except Exception:
                    continue

        except Exception:
            continue

    return False


def captcha_failed_is_visible(page) -> bool:
    if not captcha_title_visible(page):
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


def collect_visible_creator_ids(page) -> list[str]:
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

    creator_ids: list[str] = []

    for value in values:
        if value is None:
            continue

        creator_id = str(value).strip()

        if not creator_id.isdigit():
            continue

        creator_ids.append(creator_id)

    return list(dict.fromkeys(creator_ids))


def get_scroll_container(page):
    container = page.locator(
        "div.flex.flex-col.m-auto."
        "bg-neutral-bg2.box-border."
        "min-h-full.overflow-auto"
    ).first

    if container.count() == 0:
        raise RuntimeError(
            "Không tìm thấy vùng cuộn danh sách TikTok."
        )

    return container


def get_scroll_info(container) -> dict:
    return container.evaluate(
        """
        element => ({
            scrollTop: element.scrollTop,
            scrollHeight: element.scrollHeight,
            clientHeight: element.clientHeight
        })
        """
    )


def is_at_bottom(info: dict) -> bool:
    return (
        info["scrollTop"]
        + info["clientHeight"]
        >= info["scrollHeight"] - 30
    )


def scroll_down(
    container,
    fast_mode: bool,
) -> float:
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

    return wait_seconds


def process_new_dom_ids(
    repository: DatabaseRepository,
    visible_ids: list[str],
    seen_session_ids: set[str],
    target: int = QUEUE_TARGET,
) -> dict[str, list[str]]:
    new_session_ids: list[str] = []

    for creator_id in visible_ids:
        if creator_id in seen_session_ids:
            continue

        seen_session_ids.add(creator_id)
        new_session_ids.append(creator_id)

    if not new_session_ids:
        return {
            "new_session_ids": [],
            "old_db_ids": [],
            "inserted_ids": [],
        }

    existing = repository.get_existing_creator_ids(
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
        target - repository.count_pending_creators(),
    )

    inserted = repository.insert_creators_pending(
        new_ids,
        remaining,
    )

    return {
        "new_session_ids": new_session_ids,
        "old_db_ids": old_ids,
        "inserted_ids": inserted,
    }


def collect_until_queue_target(
    page,
    repository: DatabaseRepository,
    controller: CollectorController,
    target: int = QUEUE_TARGET,
) -> CollectorResult:
    pending_before = repository.count_pending_creators()
    controller.log(
        f"Trạng thái CSDL: tổng={repository.count_creators()}, "
        f"đã xử lý={repository.count_completed_creators()}, "
        f"chờ xử lý={pending_before}, mục tiêu={target}."
    )

    if pending_before >= target:
        return CollectorResult(
            new_added=0,
            pending_total=pending_before,
            old_skipped=0,
        )

    page.locator("#creator-list-content").wait_for(
        state="visible",
        timeout=30000,
    )

    page.wait_for_timeout(1500)
    scroll_container = get_scroll_container(page)
    scroll_container.evaluate(
        """
        element => {
            element.scrollTop = 0;
        }
        """
    )
    controller.sleep(NORMAL_WAIT)

    seen_session_ids: set[str] = set()
    fast_mode = True
    total_new = 0
    total_old = 0
    round_number = 0
    stagnant = 0

    while True:
        controller.check_pause_or_stop()

        if captcha_is_visible(page):
            controller.wait_for_captcha(page)

        round_number += 1
        visible = collect_visible_creator_ids(page)
        result = process_new_dom_ids(
            repository,
            visible,
            seen_session_ids,
            target=target,
        )

        old_count = len(result["old_db_ids"])
        inserted_count = len(result["inserted_ids"])
        total_old += old_count
        total_new += inserted_count

        if inserted_count > 0:
            if fast_mode:
                controller.log(
                    "Đã gặp nhà sáng tạo mới. Chuyển sang tốc độ thường."
                )
            fast_mode = False

            for creator_id in result["inserted_ids"]:
                controller.emit_creator_inserted(creator_id)

        pending = repository.count_pending_creators()
        info = get_scroll_info(scroll_container)

        progress = CollectorProgress(
            round_number=round_number,
            visible_count=len(visible),
            old_count=old_count,
            inserted_count=inserted_count,
            total_old=total_old,
            total_new=total_new,
            pending_total=pending,
            target=target,
            fast_mode=fast_mode,
            scroll_top=int(info["scrollTop"]),
            scroll_height=int(info["scrollHeight"]),
        )
        controller.emit_progress(progress)
        controller.log(
            f"Lượt {round_number}: đang thấy={len(visible)} | "
            f"Cũ={old_count} | Mới={inserted_count} | "
            f"Mới trong phiên={total_new} | "
            f"Chờ xử lý={pending}/{target} | "
            f"Tốc độ={'nhanh' if fast_mode else 'thường'} | "
            f"Cuộn={progress.scroll_top}/{progress.scroll_height}"
        )

        if pending >= target:
            break

        if is_at_bottom(info):
            old_height = int(info["scrollHeight"])
            old_dom_count = len(visible)
            controller.sleep(BOTTOM_EXTRA_WAIT)

            if captcha_is_visible(page):
                controller.wait_for_captcha(page)

            updated_info = get_scroll_info(scroll_container)
            updated_dom_count = len(
                collect_visible_creator_ids(page)
            )

            if (
                int(updated_info["scrollHeight"]) > old_height
                or updated_dom_count > old_dom_count
            ):
                stagnant = 0
            else:
                stagnant += 1
                controller.log(
                    "Cuối danh sách chưa tải thêm: "
                    f"{stagnant}/{MAX_STAGNANT_BOTTOM_ROUNDS}"
                )
        else:
            stagnant = 0

        if stagnant >= MAX_STAGNANT_BOTTOM_ROUNDS:
            controller.log(
                "TikTok không tải thêm nhà sáng tạo."
            )
            break

        wait_seconds = scroll_down(
            scroll_container,
            fast_mode,
        )
        controller.sleep(wait_seconds)

    return CollectorResult(
        new_added=total_new,
        pending_total=repository.count_pending_creators(),
        old_skipped=total_old,
    )
