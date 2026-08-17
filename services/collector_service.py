from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from core.constants import (
    BOTTOM_EXTRA_WAIT,
    CREATOR_CHECKPOINT_CAPTURE_TIMEOUT_MS,
    CREATOR_CHECKPOINT_ENABLED,
    CREATOR_EXHAUSTED_REFRESH_ENABLED,
    CREATOR_EXHAUSTED_REFRESH_WINDOW_PAGES,
    CREATOR_HEAD_REFRESH_PAGES,
    FAST_SCROLL_STEP,
    FAST_WAIT,
    MAX_STAGNANT_BOTTOM_ROUNDS,
    NORMAL_SCROLL_STEP,
    NORMAL_WAIT,
    QUEUE_TARGET,
)
from core.database import (
    CreatorScanCheckpoint,
    DatabaseRepository,
)
from services.discovery_checkpoint import (
    ApiDiscoveryTotals,
    ExhaustedRefreshStart,
    ExhaustedRefreshResult,
    MarketplaceRequestTemplate,
    MarketplacePageResult,
    PaginationState,
    build_request_template,
    checkpoint_is_valid,
    choose_exhausted_refresh_start,
    exhausted_refresh_window_pages,
    fetch_marketplace_page,
    format_pagination_debug,
    is_marketplace_pagination_request,
    should_reactivate_exhausted_checkpoint,
    resume_target_from_checkpoint,
)


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


@dataclass(frozen=True)
class ProcessedApiBatch:
    page_result: MarketplacePageResult
    old_count: int
    inserted_count: int
    scanned_count: int
    pending_total: int


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
    if CREATOR_CHECKPOINT_ENABLED:
        try:
            result = collect_until_queue_target_with_checkpoint(
                page,
                repository,
                controller,
                target=target,
            )

            if result is not None:
                return result

        except StopSessionRequested:
            raise

        except Exception as exc:
            controller.log(
                "[CHECKPOINT] Không dùng được resume an toàn; "
                f"quay về luồng cuộn cũ. Lý do: {exc}"
            )

    return collect_until_queue_target_legacy(
        page,
        repository,
        controller,
        target=target,
    )


def collect_until_queue_target_with_checkpoint(
    page,
    repository: DatabaseRepository,
    controller: CollectorController,
    target: int = QUEUE_TARGET,
) -> CollectorResult | None:
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
    total_new = 0
    total_old = 0

    visible = collect_visible_creator_ids(page)
    dom_result = process_new_dom_ids(
        repository,
        visible,
        seen_session_ids,
        target=target,
    )
    total_old += len(dom_result["old_db_ids"])
    total_new += len(dom_result["inserted_ids"])

    for creator_id in dom_result["inserted_ids"]:
        controller.emit_creator_inserted(creator_id)

    info = get_scroll_info(scroll_container)
    controller.emit_progress(
        CollectorProgress(
            round_number=1,
            visible_count=len(visible),
            old_count=len(dom_result["old_db_ids"]),
            inserted_count=len(dom_result["inserted_ids"]),
            total_old=total_old,
            total_new=total_new,
            pending_total=repository.count_pending_creators(),
            target=target,
            fast_mode=True,
            scroll_top=int(info["scrollTop"]),
            scroll_height=int(info["scrollHeight"]),
        )
    )
    controller.log(
        "[HEAD_REFRESH] DOM đầu danh sách: "
        f"đang thấy={len(visible)} | "
        f"Cũ={len(dom_result['old_db_ids'])} | "
        f"Mới={len(dom_result['inserted_ids'])}."
    )

    if repository.count_pending_creators() >= target:
        return CollectorResult(
            new_added=total_new,
            pending_total=repository.count_pending_creators(),
            old_skipped=total_old,
        )

    template = capture_fresh_pagination_template(
        page,
        controller,
        scroll_container,
    )

    if template is None:
        return None

    segment = template.segment_key[:12]
    controller.log(
        f"[DISCOVERY] segment={segment} page_size="
        f"{template.first_state.page_size}"
    )

    checkpoint = repository.get_creator_scan_checkpoint(
        template.segment_key
    )
    valid_checkpoint = (
        checkpoint
        if (
            checkpoint
            and checkpoint_is_valid(
                checkpoint,
                template.filters_json,
            )
        )
        else None
    )

    if checkpoint and not valid_checkpoint:
        controller.log(
            "[CHECKPOINT] Checkpoint không hợp lệ; bắt đầu mới."
        )

    if valid_checkpoint:
        controller.log(
            "[CHECKPOINT] found "
            f"page={valid_checkpoint.next_page} "
            f"cursor={valid_checkpoint.next_item_cursor} "
            f"has_more={valid_checkpoint.has_more}"
        )
    else:
        controller.log("[CHECKPOINT] Chưa có checkpoint cho segment này.")

    controller.log(
        f"[HEAD_REFRESH] pages={CREATOR_HEAD_REFRESH_PAGES}"
    )
    head_result = collect_api_pages(
        page=page,
        template=template,
        repository=repository,
        controller=controller,
        seen_session_ids=seen_session_ids,
        start_state=template.first_state,
        target=target,
        max_pages=CREATOR_HEAD_REFRESH_PAGES,
        save_checkpoint=valid_checkpoint is None,
        label="HEAD_REFRESH",
    )

    if head_result is None:
        return None

    total_new += head_result.new_added
    total_old += head_result.old_skipped

    if repository.count_pending_creators() >= target:
        return CollectorResult(
            new_added=total_new,
            pending_total=repository.count_pending_creators(),
            old_skipped=total_old,
        )

    if valid_checkpoint and not valid_checkpoint.has_more:
        controller.log(
            "[CHECKPOINT] Segment đã hết dữ liệu ở lần trước; "
            "chuyển sang rotating refresh."
        )

        if (
            not CREATOR_EXHAUSTED_REFRESH_ENABLED
            or head_result.next_state is None
            or not head_result.next_state.has_more
        ):
            controller.log(
                "[EXHAUSTED_REFRESH] Không có trang sau HEAD_REFRESH."
            )
            return CollectorResult(
                new_added=total_new,
                pending_total=repository.count_pending_creators(),
                old_skipped=total_old,
            )

        refresh_result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=seen_session_ids,
            exhausted_checkpoint=valid_checkpoint,
            post_head_state=head_result.next_state,
            target=target,
            window_pages=CREATOR_EXHAUSTED_REFRESH_WINDOW_PAGES,
        )

        if refresh_result is None:
            return None

        total_new += refresh_result.new_added
        total_old += refresh_result.old_skipped

        if repository.count_pending_creators() >= target:
            return CollectorResult(
                new_added=total_new,
                pending_total=repository.count_pending_creators(),
                old_skipped=total_old,
            )

        if refresh_result.reactivated_state is not None:
            resume_result = collect_api_pages(
                page=page,
                template=template,
                repository=repository,
                controller=controller,
                seen_session_ids=seen_session_ids,
                start_state=refresh_result.reactivated_state,
                target=target,
                max_pages=None,
                save_checkpoint=True,
                label="RESUME",
            )

            if resume_result is None:
                return None

            total_new += resume_result.new_added
            total_old += resume_result.old_skipped

        return CollectorResult(
            new_added=total_new,
            pending_total=repository.count_pending_creators(),
            old_skipped=total_old,
        )

    if valid_checkpoint:
        resume_state = resume_target_from_checkpoint(
            valid_checkpoint
        )
        controller.log(
            "[RESUME] saved="
            f"{valid_checkpoint.next_page}/"
            f"{valid_checkpoint.next_item_cursor} "
            "target="
            f"{resume_state.page}/{resume_state.next_item_cursor}"
        )
    else:
        resume_state = head_result.next_state

        if resume_state is None:
            return CollectorResult(
                new_added=total_new,
                pending_total=repository.count_pending_creators(),
                old_skipped=total_old,
            )

        controller.log(
            "[RESUME] Segment mới; tiếp tục từ "
            f"{resume_state.page}/{resume_state.next_item_cursor}."
        )

    resume_result = collect_api_pages(
        page=page,
        template=template,
        repository=repository,
        controller=controller,
        seen_session_ids=seen_session_ids,
        start_state=resume_state,
        target=target,
        max_pages=None,
        save_checkpoint=True,
        label="RESUME",
    )

    if resume_result is None:
        return None

    total_new += resume_result.new_added
    total_old += resume_result.old_skipped

    return CollectorResult(
        new_added=total_new,
        pending_total=repository.count_pending_creators(),
        old_skipped=total_old,
    )


def collect_until_queue_target_legacy(
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


def capture_fresh_pagination_template(
    page,
    controller: CollectorController,
    scroll_container,
) -> MarketplaceRequestTemplate | None:
    controller.log(
        "[CHECKPOINT] Đang lấy search_key mới từ phiên TikTok hiện tại."
    )

    try:
        with page.expect_request(
            is_marketplace_pagination_request,
            timeout=CREATOR_CHECKPOINT_CAPTURE_TIMEOUT_MS,
        ) as request_info:
            wait_seconds = scroll_down(
                scroll_container,
                fast_mode=True,
            )
            controller.sleep(wait_seconds)

        template = build_request_template(
            request_info.value
        )

    except Exception as exc:
        controller.log(
            "[CHECKPOINT] Không bắt được request phân trang; "
            f"fallback legacy. Lý do: {exc}"
        )
        return None

    if template is None:
        controller.log(
            "[CHECKPOINT] Request phân trang thiếu dữ liệu cần thiết; "
            "fallback legacy."
        )
        return None

    controller.log(
        "[CHECKPOINT] fresh search context acquired; "
        "search_key chỉ giữ trong bộ nhớ."
    )
    return template


def fetch_and_process_api_batch(
    page,
    template: MarketplaceRequestTemplate,
    repository: DatabaseRepository,
    controller: CollectorController,
    seen_session_ids: set[str],
    state: PaginationState,
    target: int,
    label: str,
) -> ProcessedApiBatch | None:
    controller.check_pause_or_stop()
    controller.log(
        f"[PAGINATION] {label} page={state.page} "
        f"cursor={state.next_item_cursor}"
    )

    fetch_outcome = fetch_marketplace_page(
        page,
        template,
        state,
    )
    controller.log(
        format_pagination_debug(
            fetch_outcome.debug
        )
    )
    page_result = fetch_outcome.page_result

    if page_result is None:
        controller.log(
            "[PAGINATION] Response thiếu pagination hợp lệ "
            "hoặc server từ chối request; fallback legacy."
        )
        return None

    result = process_new_dom_ids(
        repository,
        page_result.creator_ids,
        seen_session_ids,
        target=target,
    )

    for creator_id in result["inserted_ids"]:
        controller.emit_creator_inserted(creator_id)

    return ProcessedApiBatch(
        page_result=page_result,
        old_count=len(result["old_db_ids"]),
        inserted_count=len(result["inserted_ids"]),
        scanned_count=len(result["new_session_ids"]),
        pending_total=repository.count_pending_creators(),
    )


def collect_api_pages(
    page,
    template: MarketplaceRequestTemplate,
    repository: DatabaseRepository,
    controller: CollectorController,
    seen_session_ids: set[str],
    start_state: PaginationState,
    target: int,
    max_pages: int | None,
    save_checkpoint: bool,
    label: str,
) -> ApiDiscoveryTotals | None:
    state = start_state
    totals = ApiDiscoveryTotals()

    while True:
        if (
            max_pages is not None
            and totals.pages_processed >= max_pages
        ):
            break

        batch = fetch_and_process_api_batch(
            page,
            template,
            repository,
            controller,
            seen_session_ids,
            state,
            target=target,
            label=label,
        )

        if batch is None:
            return None

        page_result = batch.page_result

        if save_checkpoint:
            repository.save_creator_scan_checkpoint(
                segment_key=template.segment_key,
                filters_json=template.filters_json,
                next_page=page_result.next_state.page,
                next_item_cursor=page_result.next_state.next_item_cursor,
                page_size=page_result.next_state.page_size,
                has_more=page_result.next_state.has_more,
                scanned_delta=batch.scanned_count,
                new_delta=batch.inserted_count,
                duplicate_delta=batch.old_count,
            )
            controller.log(
                "[CHECKPOINT] updated next_page="
                f"{page_result.next_state.page} next_cursor="
                f"{page_result.next_state.next_item_cursor}"
            )

        totals = totals.add(
            batch.inserted_count,
            batch.old_count,
            page_result.next_state,
        )
        controller.emit_progress(
            CollectorProgress(
                round_number=totals.pages_processed,
                visible_count=len(page_result.creator_ids),
                old_count=batch.old_count,
                inserted_count=batch.inserted_count,
                total_old=totals.old_skipped,
                total_new=totals.new_added,
                pending_total=batch.pending_total,
                target=target,
                fast_mode=False,
                scroll_top=0,
                scroll_height=0,
            )
        )
        controller.log(
            f"[BATCH] received={len(page_result.creator_ids)} "
            f"new={batch.inserted_count} duplicate={batch.old_count} "
            f"pending={batch.pending_total}/{target}"
        )

        if batch.pending_total >= target:
            break

        if not page_result.next_state.has_more:
            controller.log(
                "[PAGINATION] has_more=false; dừng segment."
            )
            break

        state = page_result.next_state

    return totals


def collect_exhausted_refresh_until_stop(
    page,
    template: MarketplaceRequestTemplate,
    repository: DatabaseRepository,
    controller: CollectorController,
    seen_session_ids: set[str],
    exhausted_checkpoint: CreatorScanCheckpoint,
    post_head_state: PaginationState,
    target: int,
    window_pages: int = CREATOR_EXHAUSTED_REFRESH_WINDOW_PAGES,
) -> ExhaustedRefreshResult | None:
    totals = ExhaustedRefreshResult()
    window_number = 0
    allow_stale_cursor_reset = True

    while True:
        window_number += 1
        controller.log(
            f"[EXHAUSTED_REFRESH] window={window_number} bắt đầu."
        )
        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=seen_session_ids,
            exhausted_checkpoint=exhausted_checkpoint,
            post_head_state=post_head_state,
            target=target,
            window_pages=window_pages,
            allow_stale_cursor_reset=allow_stale_cursor_reset,
        )
        allow_stale_cursor_reset = False

        if result is None:
            return None

        totals = ExhaustedRefreshResult(
            new_added=totals.new_added + result.new_added,
            old_skipped=totals.old_skipped + result.old_skipped,
            pages_processed=(
                totals.pages_processed + result.pages_processed
            ),
            next_state=result.next_state,
            reactivated_state=(
                result.reactivated_state
                or totals.reactivated_state
            ),
        )
        pending = repository.count_pending_creators()

        if result.reactivated_state is not None:
            return totals

        if pending >= target:
            controller.log(
                "[EXHAUSTED_REFRESH] queue target reached "
                f"pending={pending}/{target}; stopping collection."
            )
            return totals

        if (
            result.next_state is None
            or not result.next_state.has_more
        ):
            controller.log(
                "[EXHAUSTED_REFRESH] current scan session complete."
            )
            return totals

        controller.check_pause_or_stop()
        controller.log(
            "[EXHAUSTED_REFRESH] target chưa đạt và còn dữ liệu; "
            "tiếp tục window tiếp theo."
        )


def collect_exhausted_refresh_pages(
    page,
    template: MarketplaceRequestTemplate,
    repository: DatabaseRepository,
    controller: CollectorController,
    seen_session_ids: set[str],
    exhausted_checkpoint: CreatorScanCheckpoint,
    post_head_state: PaginationState,
    target: int,
    window_pages: int = CREATOR_EXHAUSTED_REFRESH_WINDOW_PAGES,
    allow_stale_cursor_reset: bool = True,
) -> ExhaustedRefreshResult | None:
    window_pages = exhausted_refresh_window_pages(
        window_pages
    )
    refresh_state = repository.get_creator_scan_refresh_state(
        template.segment_key
    )
    start = choose_exhausted_refresh_start(
        refresh_state,
        post_head_state,
        template.filters_json,
    )
    retry_saved_cursor = start.from_saved_state

    while True:
        state = start.state
        cycle = start.cycle
        totals = ExhaustedRefreshResult()
        retry_from_post_head = False

        if start.from_saved_state:
            controller.log(
                "[EXHAUSTED_REFRESH] cycle="
                f"{cycle} resume page={state.page} "
                f"cursor={state.next_item_cursor} "
                f"window_pages={window_pages}"
            )
        else:
            controller.log(
                "[EXHAUSTED_REFRESH] cycle="
                f"{cycle} start_page={state.page} "
                f"start_cursor={state.next_item_cursor} "
                f"window_pages={window_pages}"
            )

        while totals.pages_processed < window_pages:
            batch = fetch_and_process_api_batch(
                page,
                template,
                repository,
                controller,
                seen_session_ids,
                state,
                target=target,
                label="ROTATING_REFRESH",
            )

            if batch is None:
                if (
                    totals.pages_processed == 0
                    and start.from_saved_state
                    and retry_saved_cursor
                    and allow_stale_cursor_reset
                ):
                    controller.log(
                        "[EXHAUSTED_REFRESH] Saved cursor không dùng "
                        "được; reset về vị trí sau HEAD_REFRESH."
                    )
                    repository.save_creator_scan_refresh_state(
                        segment_key=template.segment_key,
                        filters_json=template.filters_json,
                        refresh_next_page=post_head_state.page,
                        refresh_next_cursor=(
                            post_head_state.next_item_cursor
                        ),
                        page_size=post_head_state.page_size,
                        refresh_cycle=cycle,
                        refresh_restart_after_head=False,
                    )
                    start = ExhaustedRefreshStart(
                        state=post_head_state,
                        cycle=cycle,
                        from_saved_state=False,
                    )
                    retry_saved_cursor = False
                    retry_from_post_head = True
                    break

                return None

            page_result = batch.page_result
            next_state = page_result.next_state
            if (
                next_state.has_more
                and next_state.page == state.page
                and next_state.next_item_cursor
                == state.next_item_cursor
            ):
                controller.log(
                    "[EXHAUSTED_REFRESH] Pagination không tiến "
                    f"page={state.page} cursor="
                    f"{state.next_item_cursor}; fallback legacy."
                )
                return None

            cycle_completed = not next_state.has_more
            saved_cycle = cycle + 1 if cycle_completed else cycle
            repository.save_creator_scan_refresh_state(
                segment_key=template.segment_key,
                filters_json=template.filters_json,
                refresh_next_page=next_state.page,
                refresh_next_cursor=next_state.next_item_cursor,
                page_size=next_state.page_size,
                refresh_cycle=saved_cycle,
                refresh_restart_after_head=cycle_completed,
                scanned_delta=batch.scanned_count,
                new_delta=batch.inserted_count,
                duplicate_delta=batch.old_count,
                cycle_completed=cycle_completed,
            )

            if cycle_completed:
                controller.log(
                    "[EXHAUSTED_REFRESH] reached current end; "
                    f"cycle={cycle} complete"
                )
                controller.log(
                    "[EXHAUSTED_REFRESH] next cycle will restart "
                    "after HEAD_REFRESH"
                )
            else:
                controller.log(
                    "[EXHAUSTED_REFRESH] updated next_page="
                    f"{next_state.page} next_cursor="
                    f"{next_state.next_item_cursor}"
                )

            reactivated_state = None
            if should_reactivate_exhausted_checkpoint(
                exhausted_checkpoint,
                state,
                next_state,
            ):
                repository.save_creator_scan_checkpoint(
                    segment_key=template.segment_key,
                    filters_json=template.filters_json,
                    next_page=next_state.page,
                    next_item_cursor=next_state.next_item_cursor,
                    page_size=next_state.page_size,
                    has_more=True,
                )
                reactivated_state = next_state
                controller.log(
                    "[EXHAUSTED_REFRESH] result set expanded beyond "
                    "previous terminal checkpoint"
                )
                controller.log(
                    "[CHECKPOINT] segment reactivated next_page="
                    f"{next_state.page} next_cursor="
                    f"{next_state.next_item_cursor}"
                )

            totals = totals.add(
                batch.inserted_count,
                batch.old_count,
                next_state,
                reactivated_state=reactivated_state,
            )
            controller.emit_progress(
                CollectorProgress(
                    round_number=totals.pages_processed,
                    visible_count=len(page_result.creator_ids),
                    old_count=batch.old_count,
                    inserted_count=batch.inserted_count,
                    total_old=totals.old_skipped,
                    total_new=totals.new_added,
                    pending_total=batch.pending_total,
                    target=target,
                    fast_mode=False,
                    scroll_top=0,
                    scroll_height=0,
                )
            )
            controller.log(
                f"[BATCH] received={len(page_result.creator_ids)} "
                f"new={batch.inserted_count} "
                f"duplicate={batch.old_count} "
                f"pending={batch.pending_total}/{target}"
            )

            if (
                reactivated_state is not None
                or batch.pending_total >= target
                or cycle_completed
            ):
                return totals

            state = next_state

        if retry_from_post_head:
            continue

        if totals.next_state is not None:
            controller.log(
                "[EXHAUSTED_REFRESH] window complete next_page="
                f"{totals.next_state.page} next_cursor="
                f"{totals.next_state.next_item_cursor}"
            )

        return totals
