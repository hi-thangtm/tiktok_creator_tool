import tempfile
import unittest
from pathlib import Path

from core.database import (
    CreatorScanCheckpoint,
    DatabaseRepository,
)
from services.collector_service import (
    StopSessionRequested,
    collect_api_pages,
    collect_exhausted_refresh_pages,
    collect_exhausted_refresh_until_stop,
)
from services.discovery_checkpoint import (
    MarketplaceRequestTemplate,
    PaginationState,
    build_page_payload,
    canonical_json,
    filters_from_payload,
    resume_target_from_checkpoint,
    segment_key_for_payload,
    should_reactivate_exhausted_checkpoint,
)


class FakeBrowserResponse:
    def __init__(
        self,
        body,
        ok: bool = True,
        status: int | None = None,
        content_type: str = "application/json;charset=UTF-8",
        json_parse_ok: bool = True,
    ) -> None:
        self.ok = ok
        self.status = status if status is not None else (
            200 if ok else 500
        )
        self.body = body
        self.content_type = content_type
        self.json_parse_ok = json_parse_ok


class FakeBrowserPage:
    def __init__(
        self,
        responses,
    ) -> None:
        self.responses = list(responses)
        self.requests = []
        self.scripts = []

    def evaluate(
        self,
        script,
        params,
    ):
        self.scripts.append(script)
        self.requests.append(params)
        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return {
            "ok": response.ok,
            "http_status": response.status,
            "content_type": response.content_type,
            "json_parse_ok": response.json_parse_ok,
            "body": response.body if response.json_parse_ok else None,
        }


class FakeController:
    def __init__(self) -> None:
        self.inserted = []
        self.logs = []
        self.progress = []

    def check_pause_or_stop(self) -> None:
        pass

    def wait_for_captcha(self, page) -> None:
        pass

    def sleep(self, seconds: float) -> None:
        pass

    def log(self, message: str) -> None:
        self.logs.append(message)

    def emit_progress(self, progress) -> None:
        self.progress.append(progress)

    def emit_creator_inserted(
        self,
        creator_id: str,
    ) -> None:
        self.inserted.append(creator_id)


class StopImmediatelyController(FakeController):
    def check_pause_or_stop(self) -> None:
        raise StopSessionRequested()


class StopAfterChecksController(FakeController):
    def __init__(
        self,
        stop_after: int,
    ) -> None:
        super().__init__()
        self.stop_after = stop_after
        self.check_count = 0

    def check_pause_or_stop(self) -> None:
        self.check_count += 1

        if self.check_count > self.stop_after:
            raise StopSessionRequested()


class FailingInsertRepository(DatabaseRepository):
    def insert_creators_pending(
        self,
        creator_ids: list[str],
        max_to_insert: int,
    ) -> list[str]:
        raise RuntimeError("insert failed")


def make_payload(
    category: str = "beauty",
    *,
    search_key: str = "fresh-search-key",
    page: int = 1,
    cursor: int = 12,
) -> dict:
    return {
        "algorithm": 1,
        "query": "",
        "filter_params": {
            "category_list": [
                category,
            ],
            "gmv_group_v2": [
                "100_500",
            ],
            "units_sold_group": [
                "10_100",
            ],
        },
        "pagination": {
            "page": page,
            "size": 12,
            "next_item_cursor": cursor,
            "search_key": search_key,
        },
    }


def make_template(
    payload: dict | None = None,
) -> MarketplaceRequestTemplate:
    payload = payload or make_payload()
    segment_key, filters_json = segment_key_for_payload(payload)
    pagination = payload["pagination"]

    return MarketplaceRequestTemplate(
        url="https://affiliate.tiktok.com/api/v1/oec/affiliate/creator/marketplace/find",
        headers={
            "content-type": "application/json;charset=UTF-8",
        },
        payload=payload,
        search_key=pagination["search_key"],
        filters_json=filters_json,
        segment_key=segment_key,
        first_state=PaginationState(
            page=pagination["page"],
            next_item_cursor=pagination["next_item_cursor"],
            page_size=pagination["size"],
        ),
    )


def make_body(
    creator_ids: list[str],
    *,
    next_page: int,
    next_cursor: int,
    has_more: bool = True,
) -> dict:
    return {
        "code": 0,
        "message": "success",
        "data": {
            "creator_profile_list": [
                {
                    "creator_id": creator_id,
                }
                for creator_id in creator_ids
            ],
            "next_pagination": {
                "has_more": has_more,
                "next_page": next_page,
                "search_key": "new-server-search-key",
                "next_item_cursor": next_cursor,
            },
        },
    }


def make_checkpoint(
    template: MarketplaceRequestTemplate,
    *,
    next_page: int = 110,
    next_cursor: int = 1318,
    has_more: bool = False,
) -> CreatorScanCheckpoint:
    return CreatorScanCheckpoint(
        segment_key=template.segment_key,
        filters_json=template.filters_json,
        next_page=next_page,
        next_item_cursor=next_cursor,
        page_size=12,
        has_more=has_more,
        total_scanned=0,
        total_new=0,
        total_duplicate=0,
        last_success_at=None,
        updated_at="2026-08-15T00:00:00",
    )


def body_for_page(
    page_number: int,
    cursor: int,
    *,
    creator_ids: list[str] | None = None,
    has_more: bool = True,
) -> dict:
    return make_body(
        creator_ids or [
            f"creator-{page_number}",
        ],
        next_page=page_number + 1,
        next_cursor=cursor + 12,
        has_more=has_more,
    )


class DiscoveryCheckpointTest(unittest.TestCase):
    def make_repository(
        self,
        repository_class=DatabaseRepository,
    ):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repository = repository_class(
            Path(temp_dir.name) / "creators.db"
        )
        repository.init_database()

        return repository

    def test_new_segment_creates_checkpoint_after_successful_batch(self):
        repository = self.make_repository()
        template = make_template()
        controller = FakeController()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["100", "101"],
                        next_page=2,
                        next_cursor=24,
                    )
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="TEST",
        )

        checkpoint = repository.get_creator_scan_checkpoint(
            template.segment_key
        )

        self.assertIsNotNone(totals)
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.next_page, 2)
        self.assertEqual(checkpoint.next_item_cursor, 24)
        self.assertEqual(repository.count_pending_creators(), 2)
        self.assertTrue(
            any(
                "[PAGINATION_DEBUG]" in log
                and "http_status=200" in log
                and "response_code=0" in log
                and "has_next_pagination=True" in log
                for log in controller.logs
            )
        )

    def test_existing_checkpoint_resume_overlap(self):
        checkpoint = CreatorScanCheckpoint(
            segment_key="segment",
            filters_json="{}",
            next_page=400,
            next_item_cursor=4800,
            page_size=12,
            has_more=True,
            total_scanned=0,
            total_new=0,
            total_duplicate=0,
            last_success_at=None,
            updated_at="2026-08-15T00:00:00",
        )

        target = resume_target_from_checkpoint(
            checkpoint,
            overlap_pages=10,
        )

        self.assertEqual(target.page, 390)
        self.assertEqual(target.next_item_cursor, 4680)

    def test_head_refresh_can_find_new_creator_without_rewinding_checkpoint(self):
        repository = self.make_repository()
        template = make_template()
        repository.save_creator_scan_checkpoint(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            next_page=400,
            next_item_cursor=4800,
            page_size=12,
            has_more=True,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["999"],
                        next_page=2,
                        next_cursor=24,
                    )
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=False,
            label="HEAD_REFRESH",
        )
        checkpoint = repository.get_creator_scan_checkpoint(
            template.segment_key
        )

        self.assertIsNotNone(totals)
        self.assertEqual(repository.count_pending_creators(), 1)
        self.assertEqual(checkpoint.next_page, 400)
        self.assertEqual(checkpoint.next_item_cursor, 4800)

    def test_duplicate_overlap_does_not_create_duplicate(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("100")
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["100", "101"],
                        next_page=2,
                        next_cursor=24,
                    )
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )

        self.assertIsNotNone(totals)
        self.assertEqual(totals.old_skipped, 1)
        self.assertEqual(totals.new_added, 1)
        self.assertEqual(repository.count_creators(), 2)

    def test_checkpoint_does_not_advance_if_batch_insert_fails(self):
        repository = self.make_repository(FailingInsertRepository)
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["100"],
                        next_page=2,
                        next_cursor=24,
                    )
                )
            ]
        )

        with self.assertRaises(RuntimeError):
            collect_api_pages(
                page=page,
                template=template,
                repository=repository,
                controller=FakeController(),
                seen_session_ids=set(),
                start_state=template.first_state,
                target=500,
                max_pages=1,
                save_checkpoint=True,
                label="RESUME",
            )

        self.assertIsNone(
            repository.get_creator_scan_checkpoint(
                template.segment_key
            )
        )

    def test_successful_batch_uses_response_pagination_as_checkpoint(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["100"],
                        next_page=51,
                        next_cursor=612,
                    )
                )
            ]
        )

        collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )
        checkpoint = repository.get_creator_scan_checkpoint(
            template.segment_key
        )

        self.assertEqual(checkpoint.next_page, 51)
        self.assertEqual(checkpoint.next_item_cursor, 612)

    def test_different_filters_have_different_segment_keys(self):
        first_key, _ = segment_key_for_payload(
            make_payload(category="beauty")
        )
        second_key, _ = segment_key_for_payload(
            make_payload(category="electronics")
        )

        self.assertNotEqual(first_key, second_key)

    def test_fresh_search_key_does_not_change_segment_key(self):
        old_payload = make_payload(search_key="old-search-key")
        fresh_payload = make_payload(search_key="fresh-search-key")
        old_key, old_filters = segment_key_for_payload(old_payload)
        fresh_key, fresh_filters = segment_key_for_payload(fresh_payload)
        template = make_template(fresh_payload)
        request_payload = build_page_payload(
            template,
            PaginationState(
                page=390,
                next_item_cursor=4680,
                page_size=12,
            ),
        )

        self.assertEqual(old_key, fresh_key)
        self.assertEqual(old_filters, fresh_filters)
        self.assertEqual(
            request_payload["pagination"]["search_key"],
            "fresh-search-key",
        )

    def test_missing_pagination_returns_none_for_legacy_fallback(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    {
                        "code": 0,
                        "data": {
                            "creator_profile_list": [
                                {
                                    "creator_id": "100",
                                }
                            ],
                        },
                    }
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )

        self.assertIsNone(totals)
        self.assertEqual(repository.count_creators(), 0)

    def test_http_200_nonzero_code_returns_none_for_legacy_fallback(self):
        repository = self.make_repository()
        template = make_template()
        controller = FakeController()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    {
                        "code": 10001,
                        "message": "invalid cursor",
                        "data": {},
                    },
                    ok=True,
                    status=200,
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )

        self.assertIsNone(totals)
        self.assertEqual(repository.count_creators(), 0)
        self.assertIn(
            "response_code=10001",
            "\n".join(controller.logs),
        )

    def test_http_403_returns_none_for_legacy_fallback(self):
        repository = self.make_repository()
        template = make_template()
        controller = FakeController()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    {
                        "code": 403,
                        "message": "forbidden",
                    },
                    ok=False,
                    status=403,
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )

        self.assertIsNone(totals)
        self.assertEqual(repository.count_creators(), 0)
        self.assertIn(
            "http_status=403",
            "\n".join(controller.logs),
        )

    def test_invalid_json_returns_none_for_legacy_fallback(self):
        repository = self.make_repository()
        template = make_template()
        controller = FakeController()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    "not-json",
                    ok=True,
                    status=200,
                    content_type="text/html",
                    json_parse_ok=False,
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )

        self.assertIsNone(totals)
        self.assertEqual(repository.count_creators(), 0)
        self.assertIn(
            "json_parse_ok=False",
            "\n".join(controller.logs),
        )

    def test_browser_context_fetch_uses_credentials_without_cookie_header(self):
        repository = self.make_repository()
        template = make_template()
        template.headers.update(
            {
                "accept": "application/json",
                "authorization": "Bearer secret-token",
                "cookie": "sessionid=secret-cookie",
                "sec-fetch-site": "same-origin",
                "user-agent": "Fake UA",
                "x-bogus": "secret-bogus",
                "x-gnarly": "secret-gnarly",
                "x-tts-oec-bsid": "secret-bsid",
            }
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["100"],
                        next_page=2,
                        next_cursor=24,
                    )
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )
        request_headers = {
            key.lower(): value
            for key, value in page.requests[0]["headers"].items()
        }

        self.assertIsNotNone(totals)
        self.assertIn(
            'credentials: "include"',
            page.scripts[0],
        )
        self.assertEqual(
            request_headers["accept"],
            "application/json",
        )
        self.assertNotIn("cookie", request_headers)
        self.assertNotIn("authorization", request_headers)
        self.assertNotIn("sec-fetch-site", request_headers)
        self.assertNotIn("user-agent", request_headers)
        self.assertNotIn("x-bogus", request_headers)
        self.assertNotIn("x-gnarly", request_headers)
        self.assertNotIn("x-tts-oec-bsid", request_headers)

    def test_no_secret_values_appear_in_pagination_debug_logs(self):
        repository = self.make_repository()
        template = make_template()
        controller = FakeController()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    {
                        "code": 10001,
                        "message": (
                            "authorization=Bearer secret-token "
                            "cookie=secret-cookie X-Bogus=secret-bogus"
                        ),
                        "data": {},
                    }
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )
        logs = "\n".join(controller.logs)

        self.assertIsNone(totals)
        self.assertIn("response_message=[redacted]", logs)
        self.assertNotIn("secret-token", logs)
        self.assertNotIn("secret-cookie", logs)
        self.assertNotIn("secret-bogus", logs)

    def test_fetch_exception_logs_safe_diagnostic(self):
        repository = self.make_repository()
        template = make_template()
        controller = FakeController()
        page = FakeBrowserPage(
            [
                RuntimeError(
                    "failed https://affiliate.tiktok.com/path?cookie=secret"
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )
        logs = "\n".join(controller.logs)

        self.assertIsNone(totals)
        self.assertIn("exception_type=RuntimeError", logs)
        self.assertIn(
            "safe_exception_message=failed [redacted-url]",
            logs,
        )
        self.assertNotIn("affiliate.tiktok.com/path", logs)
        self.assertNotIn("secret", logs)

    def test_checkpoint_does_not_advance_when_pagination_replay_fails(self):
        repository = self.make_repository()
        template = make_template()
        repository.save_creator_scan_checkpoint(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            next_page=50,
            next_item_cursor=600,
            page_size=12,
            has_more=True,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    {
                        "code": 403,
                        "message": "forbidden",
                    },
                    ok=False,
                    status=403,
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )
        checkpoint = repository.get_creator_scan_checkpoint(
            template.segment_key
        )

        self.assertIsNone(totals)
        self.assertEqual(checkpoint.next_page, 50)
        self.assertEqual(checkpoint.next_item_cursor, 600)

    def test_active_segment_normal_resume_behavior_unchanged(self):
        repository = self.make_repository()
        template = make_template()
        checkpoint = make_checkpoint(
            template,
            next_page=50,
            next_cursor=600,
            has_more=True,
        )
        resume_state = resume_target_from_checkpoint(
            checkpoint,
            overlap_pages=10,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["active-1"],
                        next_page=41,
                        next_cursor=492,
                    )
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=resume_state,
            target=500,
            max_pages=1,
            save_checkpoint=True,
            label="RESUME",
        )

        self.assertIsNotNone(totals)
        self.assertEqual(
            page.requests[0]["payload"]["pagination"]["page"],
            40,
        )
        self.assertIsNone(
            repository.get_creator_scan_refresh_state(
                template.segment_key
            )
        )

    def test_exhausted_without_rotation_state_starts_after_head(self):
        repository = self.make_repository()
        template = make_template()
        post_head = PaginationState(
            page=11,
            next_item_cursor=132,
            page_size=12,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        11,
                        132,
                    )
                )
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=post_head,
            target=500,
            window_pages=1,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertIsNotNone(result)
        self.assertEqual(
            page.requests[0]["payload"]["pagination"]["page"],
            11,
        )
        self.assertEqual(refresh_state.refresh_next_page, 12)
        self.assertEqual(refresh_state.refresh_next_cursor, 144)

    def test_rotation_window_limits_to_twenty_batches(self):
        repository = self.make_repository()
        template = make_template()
        responses = [
            FakeBrowserResponse(
                body_for_page(
                    page_number,
                    132 + (page_number - 11) * 12,
                )
            )
            for page_number in range(11, 31)
        ]
        page = FakeBrowserPage(responses)

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=500,
            window_pages=20,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(result.pages_processed, 20)
        self.assertEqual(len(page.requests), 20)
        self.assertEqual(refresh_state.refresh_next_page, 31)
        self.assertEqual(refresh_state.refresh_next_cursor, 372)

    def test_restart_uses_saved_rotation_pointer_after_head(self):
        repository = self.make_repository()
        template = make_template()
        repository.save_creator_scan_refresh_state(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            refresh_next_page=31,
            refresh_next_cursor=372,
            page_size=12,
            refresh_cycle=1,
            refresh_restart_after_head=False,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        31,
                        372,
                    )
                )
            ]
        )

        collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )

        self.assertEqual(
            page.requests[0]["payload"]["pagination"]["page"],
            31,
        )
        self.assertEqual(
            page.requests[0]["payload"]["pagination"][
                "next_item_cursor"
            ],
            372,
        )

    def test_new_creator_in_middle_rotation_is_inserted(self):
        repository = self.make_repository()
        template = make_template()
        repository.save_creator_scan_refresh_state(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            refresh_next_page=45,
            refresh_next_cursor=540,
            page_size=12,
            refresh_cycle=1,
            refresh_restart_after_head=False,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        45,
                        540,
                        creator_ids=["new-45"],
                    )
                )
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )

        self.assertEqual(result.new_added, 1)
        self.assertEqual(repository.count_creators(), 1)

    def test_duplicate_creator_in_rotation_does_not_duplicate(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("dupe-1")
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        11,
                        132,
                        creator_ids=["dupe-1"],
                    )
                )
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )

        self.assertEqual(result.old_skipped, 1)
        self.assertEqual(result.new_added, 0)
        self.assertEqual(repository.count_creators(), 1)

    def test_rotation_reaches_end_increments_cycle(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["tail"],
                        next_page=110,
                        next_cursor=1318,
                        has_more=False,
                    )
                )
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=109,
                next_item_cursor=1306,
                page_size=12,
            ),
            target=500,
            window_pages=20,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(result.pages_processed, 1)
        self.assertEqual(refresh_state.refresh_cycle, 2)
        self.assertTrue(refresh_state.refresh_restart_after_head)
        self.assertIsNotNone(refresh_state.last_cycle_completed_at)

    def test_rotation_pointer_does_not_advance_if_insert_fails(self):
        repository = self.make_repository(FailingInsertRepository)
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        11,
                        132,
                    )
                )
            ]
        )

        with self.assertRaises(RuntimeError):
            collect_exhausted_refresh_pages(
                page=page,
                template=template,
                repository=repository,
                controller=FakeController(),
                seen_session_ids=set(),
                exhausted_checkpoint=make_checkpoint(template),
                post_head_state=PaginationState(
                    page=11,
                    next_item_cursor=132,
                    page_size=12,
                ),
                target=500,
                window_pages=1,
            )

        self.assertIsNone(
            repository.get_creator_scan_refresh_state(
                template.segment_key
            )
        )

    def test_rotation_pointer_uses_exact_server_cursor(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["custom"],
                        next_page=51,
                        next_cursor=777,
                    )
                )
            ]
        )

        collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=50,
                next_item_cursor=600,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(refresh_state.refresh_next_page, 51)
        self.assertEqual(refresh_state.refresh_next_cursor, 777)

    def test_queue_target_reached_mid_window_stops_after_saved_batch(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("existing")
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        11,
                        132,
                        creator_ids=["target-new", "target-extra"],
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(
                        12,
                        144,
                    )
                ),
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=2,
            window_pages=20,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(result.pages_processed, 1)
        self.assertEqual(len(page.requests), 1)
        self.assertEqual(repository.count_creators(), 2)
        self.assertEqual(refresh_state.refresh_next_page, 12)
        self.assertEqual(refresh_state.refresh_next_cursor, 144)

    def test_rotation_state_is_independent_per_segment(self):
        repository = self.make_repository()
        template_a = make_template(make_payload(category="beauty"))
        template_b = make_template(
            make_payload(category="electronics")
        )

        repository.save_creator_scan_refresh_state(
            segment_key=template_a.segment_key,
            filters_json=template_a.filters_json,
            refresh_next_page=31,
            refresh_next_cursor=372,
            page_size=12,
            refresh_cycle=1,
            refresh_restart_after_head=False,
        )
        repository.save_creator_scan_refresh_state(
            segment_key=template_b.segment_key,
            filters_json=template_b.filters_json,
            refresh_next_page=51,
            refresh_next_cursor=612,
            page_size=12,
            refresh_cycle=3,
            refresh_restart_after_head=False,
        )

        state_a = repository.get_creator_scan_refresh_state(
            template_a.segment_key
        )
        state_b = repository.get_creator_scan_refresh_state(
            template_b.segment_key
        )

        self.assertEqual(state_a.refresh_next_page, 31)
        self.assertEqual(state_b.refresh_next_page, 51)
        self.assertEqual(state_b.refresh_cycle, 3)

    def test_rotation_uses_fresh_search_key_with_saved_pointer(self):
        repository = self.make_repository()
        template = make_template(
            make_payload(search_key="fresh-run-key")
        )
        repository.save_creator_scan_refresh_state(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            refresh_next_page=31,
            refresh_next_cursor=372,
            page_size=12,
            refresh_cycle=1,
            refresh_restart_after_head=False,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        31,
                        372,
                    )
                )
            ]
        )

        collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )

        self.assertEqual(
            page.requests[0]["payload"]["pagination"]["search_key"],
            "fresh-run-key",
        )

    def test_stale_rotation_cursor_resets_once_to_post_head(self):
        repository = self.make_repository()
        template = make_template()
        repository.save_creator_scan_refresh_state(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            refresh_next_page=31,
            refresh_next_cursor=372,
            page_size=12,
            refresh_cycle=2,
            refresh_restart_after_head=False,
        )
        controller = FakeController()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    {
                        "code": 403,
                        "message": "forbidden",
                    },
                    ok=False,
                    status=403,
                ),
                FakeBrowserResponse(
                    body_for_page(
                        11,
                        132,
                    )
                ),
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=controller,
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )
        request_pages = [
            request["payload"]["pagination"]["page"]
            for request in page.requests
        ]
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertIsNotNone(result)
        self.assertEqual(request_pages, [31, 11])
        self.assertEqual(refresh_state.refresh_cycle, 2)
        self.assertEqual(refresh_state.refresh_next_page, 12)
        self.assertIn(
            "reset về vị trí sau HEAD_REFRESH",
            "\n".join(controller.logs),
        )

    def test_expansion_near_previous_tail_reactivates_main_checkpoint(self):
        repository = self.make_repository()
        template = make_template()
        repository.save_creator_scan_checkpoint(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            next_page=110,
            next_item_cursor=1318,
            page_size=12,
            has_more=False,
        )
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["expanded-tail"],
                        next_page=110,
                        next_cursor=1320,
                        has_more=True,
                    )
                )
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=109,
                next_item_cursor=1306,
                page_size=12,
            ),
            target=500,
            window_pages=20,
        )
        checkpoint = repository.get_creator_scan_checkpoint(
            template.segment_key
        )

        self.assertIsNotNone(result.reactivated_state)
        self.assertTrue(checkpoint.has_more)
        self.assertEqual(checkpoint.next_page, 110)
        self.assertEqual(checkpoint.next_item_cursor, 1320)

    def test_early_has_more_true_does_not_reactivate_main_checkpoint(self):
        template = make_template()
        checkpoint = make_checkpoint(template)

        self.assertFalse(
            should_reactivate_exhausted_checkpoint(
                checkpoint,
                PaginationState(
                    page=20,
                    next_item_cursor=240,
                    page_size=12,
                ),
                PaginationState(
                    page=21,
                    next_item_cursor=252,
                    page_size=12,
                    has_more=True,
                ),
            )
        )

    def test_safe_stop_before_rotation_batch_does_not_write_state(self):
        repository = self.make_repository()
        template = make_template()

        with self.assertRaises(StopSessionRequested):
            collect_exhausted_refresh_pages(
                page=FakeBrowserPage(
                    [
                        FakeBrowserResponse(
                            body_for_page(
                                11,
                                132,
                            )
                        )
                    ]
                ),
                template=template,
                repository=repository,
                controller=StopImmediatelyController(),
                seen_session_ids=set(),
                exhausted_checkpoint=make_checkpoint(template),
                post_head_state=PaginationState(
                    page=11,
                    next_item_cursor=132,
                    page_size=12,
                ),
                target=500,
                window_pages=1,
            )

        self.assertIsNone(
            repository.get_creator_scan_refresh_state(
                template.segment_key
            )
        )

    def test_legacy_fallback_when_rotation_pagination_unavailable(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    {
                        "code": 0,
                        "data": {},
                    }
                )
            ]
        )

        result = collect_exhausted_refresh_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=11,
                next_item_cursor=132,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )

        self.assertIsNone(result)
        self.assertIsNone(
            repository.get_creator_scan_refresh_state(
                template.segment_key
            )
        )

    def test_existing_checkpoint_survives_refresh_state_migration(self):
        repository = self.make_repository()
        template = make_template()
        repository.save_creator_scan_checkpoint(
            segment_key=template.segment_key,
            filters_json=template.filters_json,
            next_page=110,
            next_item_cursor=1318,
            page_size=12,
            has_more=False,
            scanned_delta=123,
            new_delta=45,
            duplicate_delta=78,
        )

        repository.init_database()
        checkpoint = repository.get_creator_scan_checkpoint(
            template.segment_key
        )

        self.assertEqual(checkpoint.next_page, 110)
        self.assertEqual(checkpoint.next_item_cursor, 1318)
        self.assertFalse(checkpoint.has_more)
        self.assertEqual(checkpoint.total_scanned, 123)

    def test_continuous_rotation_starts_second_window(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        51,
                        612,
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(
                        52,
                        624,
                        has_more=False,
                    )
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=51,
                next_item_cursor=612,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )
        request_pages = [
            request["payload"]["pagination"]["page"]
            for request in page.requests
        ]

        self.assertEqual(result.pages_processed, 2)
        self.assertEqual(request_pages, [51, 52])

    def test_continuous_rotation_runs_multiple_windows_until_end(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(51, 612)
                ),
                FakeBrowserResponse(
                    body_for_page(52, 624)
                ),
                FakeBrowserResponse(
                    body_for_page(53, 636)
                ),
                FakeBrowserResponse(
                    body_for_page(54, 648)
                ),
                FakeBrowserResponse(
                    body_for_page(
                        55,
                        660,
                        has_more=False,
                    )
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=51,
                next_item_cursor=612,
                page_size=12,
            ),
            target=500,
            window_pages=2,
        )
        request_pages = [
            request["payload"]["pagination"]["page"]
            for request in page.requests
        ]

        self.assertEqual(result.pages_processed, 5)
        self.assertEqual(request_pages, [51, 52, 53, 54, 55])

    def test_continuous_rotation_stops_when_target_reached_in_second_window(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("existing")
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        51,
                        612,
                        creator_ids=["existing"],
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(
                        52,
                        624,
                        creator_ids=["target-new"],
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(53, 636)
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=51,
                next_item_cursor=612,
                page_size=12,
            ),
            target=2,
            window_pages=1,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(result.pages_processed, 2)
        self.assertEqual(len(page.requests), 2)
        self.assertEqual(repository.count_creators(), 2)
        self.assertEqual(refresh_state.refresh_next_page, 53)

    def test_continuous_rotation_stops_at_dataset_end_first_window(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        58,
                        696,
                        has_more=False,
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(59, 708)
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=58,
                next_item_cursor=696,
                page_size=12,
            ),
            target=500,
            window_pages=20,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(result.pages_processed, 1)
        self.assertEqual(len(page.requests), 1)
        self.assertEqual(refresh_state.refresh_cycle, 2)

    def test_zero_new_window_continues_to_next_window(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("dupe-51")
        repository.add_creator_to_queue("dupe-52")
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        51,
                        612,
                        creator_ids=["dupe-51"],
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(
                        52,
                        624,
                        creator_ids=["dupe-52"],
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(
                        53,
                        636,
                        has_more=False,
                    )
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=51,
                next_item_cursor=612,
                page_size=12,
            ),
            target=500,
            window_pages=2,
        )

        self.assertEqual(result.pages_processed, 3)
        self.assertEqual(result.new_added, 1)

    def test_zero_new_until_end_stops_only_at_end(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("dupe-51")
        repository.add_creator_to_queue("dupe-52")
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        51,
                        612,
                        creator_ids=["dupe-51"],
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(
                        52,
                        624,
                        creator_ids=["dupe-52"],
                        has_more=False,
                    )
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=51,
                next_item_cursor=612,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )

        self.assertEqual(result.pages_processed, 2)
        self.assertEqual(result.new_added, 0)
        self.assertEqual(result.old_skipped, 2)

    def test_safe_stop_between_windows_does_not_start_next_window(self):
        repository = self.make_repository()
        template = make_template()
        controller = StopAfterChecksController(stop_after=1)
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(51, 612)
                ),
                FakeBrowserResponse(
                    body_for_page(52, 624)
                ),
            ]
        )

        with self.assertRaises(StopSessionRequested):
            collect_exhausted_refresh_until_stop(
                page=page,
                template=template,
                repository=repository,
                controller=controller,
                seen_session_ids=set(),
                exhausted_checkpoint=make_checkpoint(template),
                post_head_state=PaginationState(
                    page=51,
                    next_item_cursor=612,
                    page_size=12,
                ),
                target=500,
                window_pages=1,
            )

        self.assertEqual(len(page.requests), 1)

    def test_safe_stop_inside_window_before_next_page(self):
        repository = self.make_repository()
        template = make_template()
        controller = StopAfterChecksController(stop_after=1)
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(51, 612)
                ),
                FakeBrowserResponse(
                    body_for_page(52, 624)
                ),
            ]
        )

        with self.assertRaises(StopSessionRequested):
            collect_exhausted_refresh_until_stop(
                page=page,
                template=template,
                repository=repository,
                controller=controller,
                seen_session_ids=set(),
                exhausted_checkpoint=make_checkpoint(template),
                post_head_state=PaginationState(
                    page=51,
                    next_item_cursor=612,
                    page_size=12,
                ),
                target=500,
                window_pages=20,
            )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(len(page.requests), 1)
        self.assertEqual(refresh_state.refresh_next_page, 52)

    def test_pagination_failure_in_later_window_preserves_last_pointer(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(51, 612)
                ),
                FakeBrowserResponse(
                    {
                        "code": 403,
                        "message": "forbidden",
                    },
                    ok=False,
                    status=403,
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=51,
                next_item_cursor=612,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertIsNone(result)
        self.assertEqual(refresh_state.refresh_next_page, 52)
        self.assertEqual(refresh_state.refresh_next_cursor, 624)

    def test_repeated_identical_cursor_stops_without_looping(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["same-cursor"],
                        next_page=51,
                        next_cursor=612,
                        has_more=True,
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(52, 624)
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=51,
                next_item_cursor=612,
                page_size=12,
            ),
            target=500,
            window_pages=20,
        )

        self.assertIsNone(result)
        self.assertEqual(len(page.requests), 1)
        self.assertIsNone(
            repository.get_creator_scan_refresh_state(
                template.segment_key
            )
        )

    def test_cycle_completion_does_not_start_next_cycle_same_invocation(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    body_for_page(
                        110,
                        1318,
                        has_more=False,
                    )
                ),
                FakeBrowserResponse(
                    body_for_page(11, 132)
                ),
            ]
        )

        result = collect_exhausted_refresh_until_stop(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            exhausted_checkpoint=make_checkpoint(template),
            post_head_state=PaginationState(
                page=110,
                next_item_cursor=1318,
                page_size=12,
            ),
            target=500,
            window_pages=1,
        )
        refresh_state = repository.get_creator_scan_refresh_state(
            template.segment_key
        )

        self.assertEqual(result.pages_processed, 1)
        self.assertEqual(len(page.requests), 1)
        self.assertEqual(refresh_state.refresh_cycle, 2)

    def test_has_more_false_stops_and_marks_checkpoint_exhausted(self):
        repository = self.make_repository()
        template = make_template()
        page = FakeBrowserPage(
            [
                FakeBrowserResponse(
                    make_body(
                        ["100"],
                        next_page=2,
                        next_cursor=24,
                        has_more=False,
                    )
                )
            ]
        )

        totals = collect_api_pages(
            page=page,
            template=template,
            repository=repository,
            controller=FakeController(),
            seen_session_ids=set(),
            start_state=template.first_state,
            target=500,
            max_pages=None,
            save_checkpoint=True,
            label="RESUME",
        )
        checkpoint = repository.get_creator_scan_checkpoint(
            template.segment_key
        )

        self.assertEqual(totals.pages_processed, 1)
        self.assertFalse(checkpoint.has_more)

    def test_filter_json_is_canonical(self):
        first = canonical_json(
            filters_from_payload(make_payload())
        )
        reordered = make_payload()
        reordered = {
            "pagination": reordered["pagination"],
            "filter_params": reordered["filter_params"],
            "query": reordered["query"],
            "algorithm": reordered["algorithm"],
        }
        second = canonical_json(
            filters_from_payload(reordered)
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
