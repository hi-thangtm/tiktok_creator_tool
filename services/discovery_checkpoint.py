from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from playwright.sync_api import Request

from core.constants import (
    CREATOR_EXHAUSTED_REFRESH_WINDOW_PAGES,
    CREATOR_PAGE_SIZE,
    CREATOR_RESUME_OVERLAP_PAGES,
)
from core.database import (
    CreatorScanCheckpoint,
    CreatorScanRefreshState,
)


MARKETPLACE_FIND_PATH = (
    "/api/v1/oec/affiliate/creator/marketplace/find"
)

DYNAMIC_PAGINATION_KEYS = {
    "page",
    "next_item_cursor",
    "search_key",
}

SENSITIVE_LOG_MARKERS = (
    "authorization",
    "cookie",
    "mstoken",
    "session",
    "x-bogus",
    "x-gnarly",
    "x-tts-oec-bsid",
)

BLOCKED_REPLAY_HEADERS = {
    "accept-encoding",
    "authorization",
    "connection",
    "content-length",
    "cookie",
    "host",
    "origin",
    "referer",
    "user-agent",
    "x-bogus",
    "x-gnarly",
    "x-tts-oec-bsid",
}

BLOCKED_REPLAY_HEADER_PREFIXES = (
    ":",
    "proxy-",
    "sec-",
)

BROWSER_FETCH_SCRIPT = """
async ({ url, headers, payload }) => {
    const response = await fetch(url, {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify(payload),
    });
    const contentType = response.headers.get("content-type") || "";
    const text = await response.text();
    let body = null;
    let jsonParseOk = false;

    try {
        body = text ? JSON.parse(text) : null;
        jsonParseOk = true;
    } catch (error) {
        jsonParseOk = false;
    }

    return {
        ok: response.ok,
        http_status: response.status,
        content_type: contentType,
        json_parse_ok: jsonParseOk,
        body,
    };
}
"""


@dataclass(frozen=True)
class PaginationState:
    page: int
    next_item_cursor: int
    page_size: int
    has_more: bool = True


@dataclass(frozen=True)
class MarketplaceRequestTemplate:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]
    search_key: str
    filters_json: str
    segment_key: str
    first_state: PaginationState


@dataclass(frozen=True)
class MarketplacePageResult:
    creator_ids: list[str]
    next_state: PaginationState


@dataclass(frozen=True)
class PaginationDebugInfo:
    http_status: int | None = None
    content_type: str = ""
    json_parse_ok: bool = False
    response_code: Any = None
    response_message: str = ""
    has_next_pagination: bool = False
    next_page: int | None = None
    next_cursor: int | None = None
    has_more: bool | None = None
    exception_type: str = ""
    safe_exception_message: str = ""


@dataclass(frozen=True)
class MarketplaceFetchOutcome:
    page_result: MarketplacePageResult | None
    debug: PaginationDebugInfo


@dataclass(frozen=True)
class ExhaustedRefreshStart:
    state: PaginationState
    cycle: int
    from_saved_state: bool


@dataclass(frozen=True)
class ExhaustedRefreshResult:
    new_added: int = 0
    old_skipped: int = 0
    pages_processed: int = 0
    next_state: PaginationState | None = None
    reactivated_state: PaginationState | None = None

    def add(
        self,
        new_count: int,
        old_count: int,
        next_state: PaginationState,
        reactivated_state: PaginationState | None = None,
    ) -> ExhaustedRefreshResult:
        return ExhaustedRefreshResult(
            new_added=self.new_added + new_count,
            old_skipped=self.old_skipped + old_count,
            pages_processed=self.pages_processed + 1,
            next_state=next_state,
            reactivated_state=(
                reactivated_state or self.reactivated_state
            ),
        )


@dataclass(frozen=True)
class ApiDiscoveryTotals:
    new_added: int = 0
    old_skipped: int = 0
    pages_processed: int = 0
    next_state: PaginationState | None = None

    def add(
        self,
        new_count: int,
        old_count: int,
        next_state: PaginationState,
    ) -> ApiDiscoveryTotals:
        return ApiDiscoveryTotals(
            new_added=self.new_added + new_count,
            old_skipped=self.old_skipped + old_count,
            pages_processed=self.pages_processed + 1,
            next_state=next_state,
        )


def canonical_json(
    value: Any,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def filters_from_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    filters = deepcopy(dict(payload))
    pagination = filters.get("pagination")

    if isinstance(pagination, dict):
        pagination = {
            key: value
            for key, value in pagination.items()
            if key not in DYNAMIC_PAGINATION_KEYS
        }

        if pagination:
            filters["pagination"] = pagination
        else:
            filters.pop(
                "pagination",
                None,
            )

    return {
        "endpoint": MARKETPLACE_FIND_PATH,
        "payload": filters,
    }


def segment_key_for_filters_json(
    filters_json: str,
) -> str:
    return hashlib.sha256(
        filters_json.encode("utf-8")
    ).hexdigest()


def segment_key_for_payload(
    payload: Mapping[str, Any],
) -> tuple[str, str]:
    filters_json = canonical_json(
        filters_from_payload(payload)
    )

    return (
        segment_key_for_filters_json(filters_json),
        filters_json,
    )


def resume_target_from_checkpoint(
    checkpoint: CreatorScanCheckpoint,
    overlap_pages: int = CREATOR_RESUME_OVERLAP_PAGES,
) -> PaginationState:
    overlap_cursor = overlap_pages * checkpoint.page_size

    return PaginationState(
        page=max(
            0,
            checkpoint.next_page - overlap_pages,
        ),
        next_item_cursor=max(
            0,
            checkpoint.next_item_cursor - overlap_cursor,
        ),
        page_size=checkpoint.page_size,
        has_more=checkpoint.has_more,
    )


def checkpoint_is_valid(
    checkpoint: CreatorScanCheckpoint,
    filters_json: str,
) -> bool:
    return (
        checkpoint.filters_json == filters_json
        and checkpoint.next_page >= 0
        and checkpoint.next_item_cursor >= 0
        and checkpoint.page_size > 0
        and isinstance(checkpoint.has_more, bool)
    )


def refresh_state_is_valid(
    refresh_state: CreatorScanRefreshState,
    filters_json: str,
) -> bool:
    return (
        refresh_state.filters_json == filters_json
        and refresh_state.refresh_next_page >= 0
        and refresh_state.refresh_next_cursor >= 0
        and refresh_state.page_size > 0
        and refresh_state.refresh_cycle >= 1
        and isinstance(
            refresh_state.refresh_restart_after_head,
            bool,
        )
    )


def choose_exhausted_refresh_start(
    refresh_state: CreatorScanRefreshState | None,
    post_head_state: PaginationState,
    filters_json: str,
) -> ExhaustedRefreshStart:
    if (
        refresh_state
        and refresh_state_is_valid(
            refresh_state,
            filters_json,
        )
        and not refresh_state.refresh_restart_after_head
    ):
        return ExhaustedRefreshStart(
            state=PaginationState(
                page=refresh_state.refresh_next_page,
                next_item_cursor=(
                    refresh_state.refresh_next_cursor
                ),
                page_size=refresh_state.page_size,
            ),
            cycle=refresh_state.refresh_cycle,
            from_saved_state=True,
        )

    cycle = refresh_state.refresh_cycle if refresh_state else 1

    return ExhaustedRefreshStart(
        state=post_head_state,
        cycle=max(
            1,
            cycle,
        ),
        from_saved_state=False,
    )


def should_reactivate_exhausted_checkpoint(
    checkpoint: CreatorScanCheckpoint,
    requested_state: PaginationState,
    next_state: PaginationState,
) -> bool:
    if (
        checkpoint.has_more
        or not next_state.has_more
    ):
        return False

    return (
        requested_state.page >= checkpoint.next_page
        or (
            next_state.page >= checkpoint.next_page
            and next_state.next_item_cursor
            >= checkpoint.next_item_cursor
        )
    )


def exhausted_refresh_window_pages(
    value: int = CREATOR_EXHAUSTED_REFRESH_WINDOW_PAGES,
) -> int:
    return max(
        1,
        value,
    )


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


def is_marketplace_pagination_request(
    request: Request,
) -> bool:
    if request.method != "POST":
        return False

    if MARKETPLACE_FIND_PATH not in request.url:
        return False

    payload = get_request_payload(request)

    if not payload:
        return False

    pagination = payload.get("pagination")

    if not isinstance(pagination, dict):
        return False

    search_key = str(
        pagination.get("search_key") or ""
    ).strip()

    if not search_key:
        return False

    return (
        _to_int(pagination.get("page")) is not None
        and _to_int(pagination.get("next_item_cursor")) is not None
    )


def sanitize_request_headers(
    headers: Mapping[str, str],
) -> dict[str, str]:
    result: dict[str, str] = {}

    for key, value in headers.items():
        normalized = key.lower()

        if (
            normalized in BLOCKED_REPLAY_HEADERS
            or any(
                normalized.startswith(prefix)
                for prefix in BLOCKED_REPLAY_HEADER_PREFIXES
            )
            or "mstoken" in normalized
            or "bsid" in normalized
        ):
            continue

        result[key] = value

    result["content-type"] = "application/json;charset=UTF-8"

    return result


def build_request_template(
    request: Request,
) -> MarketplaceRequestTemplate | None:
    payload = get_request_payload(request)

    if not payload:
        return None

    pagination = payload.get("pagination")

    if not isinstance(pagination, dict):
        return None

    page = _to_int(pagination.get("page"))
    cursor = _to_int(pagination.get("next_item_cursor"))
    page_size = (
        _to_int(pagination.get("size"))
        or CREATOR_PAGE_SIZE
    )
    search_key = str(
        pagination.get("search_key") or ""
    ).strip()

    if (
        page is None
        or cursor is None
        or page_size <= 0
        or not search_key
    ):
        return None

    try:
        headers = sanitize_request_headers(
            request.all_headers()
        )
    except Exception:
        headers = {
            "content-type": "application/json;charset=UTF-8",
        }

    segment_key, filters_json = segment_key_for_payload(payload)

    return MarketplaceRequestTemplate(
        url=request.url,
        headers=headers,
        payload=deepcopy(payload),
        search_key=search_key,
        filters_json=filters_json,
        segment_key=segment_key,
        first_state=PaginationState(
            page=page,
            next_item_cursor=cursor,
            page_size=page_size,
        ),
    )


def get_response_root(
    body: Mapping[str, Any],
) -> Mapping[str, Any]:
    data = body.get("data")

    if isinstance(data, dict):
        return data

    return body


def extract_creator_ids(
    body: Mapping[str, Any],
) -> list[str]:
    root = get_response_root(body)
    creator_ids: list[str] = []

    for list_key in (
        "creator_profile_list",
        "creator_connect_info_list",
    ):
        values = root.get(list_key)

        if not isinstance(values, list):
            continue

        for item in values:
            if not isinstance(item, dict):
                continue

            creator_id = item.get("creator_id")

            if creator_id is None:
                continue

            creator_id = str(creator_id).strip()

            if (
                creator_id
                and creator_id not in creator_ids
            ):
                creator_ids.append(creator_id)

        if creator_ids:
            break

    return creator_ids


def extract_next_pagination(
    body: Mapping[str, Any],
    page_size: int,
) -> PaginationState | None:
    root = get_response_root(body)
    pagination = root.get("next_pagination")

    if not isinstance(pagination, dict):
        return None

    page = _to_int(pagination.get("next_page"))
    cursor = _to_int(pagination.get("next_item_cursor"))
    has_more = _to_bool(pagination.get("has_more"))

    if (
        page is None
        or cursor is None
        or has_more is None
    ):
        return None

    return PaginationState(
        page=page,
        next_item_cursor=cursor,
        page_size=page_size,
        has_more=has_more,
    )


def build_page_payload(
    template: MarketplaceRequestTemplate,
    state: PaginationState,
) -> dict[str, Any]:
    payload = deepcopy(template.payload)
    pagination = payload.setdefault(
        "pagination",
        {},
    )
    pagination["page"] = state.page
    pagination["size"] = state.page_size
    pagination["next_item_cursor"] = state.next_item_cursor
    pagination["search_key"] = template.search_key

    return payload


def fetch_marketplace_page(
    page,
    template: MarketplaceRequestTemplate,
    state: PaginationState,
) -> MarketplaceFetchOutcome:
    payload = build_page_payload(
        template,
        state,
    )

    try:
        raw = page.evaluate(
            BROWSER_FETCH_SCRIPT,
            {
                "url": template.url,
                "headers": sanitize_request_headers(
                    template.headers
                ),
                "payload": payload,
            },
        )
    except Exception as exc:
        return MarketplaceFetchOutcome(
            page_result=None,
            debug=PaginationDebugInfo(
                exception_type=type(exc).__name__,
                safe_exception_message=safe_debug_value(exc),
            ),
        )

    if not isinstance(raw, dict):
        return MarketplaceFetchOutcome(
            page_result=None,
            debug=PaginationDebugInfo(
                safe_exception_message="fetch returned non-object",
            ),
        )

    body = raw.get("body")
    body_is_dict = isinstance(body, dict)
    code = body.get("code") if body_is_dict else None
    message = body.get("message") if body_is_dict else ""
    next_state = (
        extract_next_pagination(
            body,
            state.page_size,
        )
        if body_is_dict
        else None
    )
    root = get_response_root(body) if body_is_dict else {}
    next_pagination = (
        root.get("next_pagination")
        if isinstance(root, dict)
        else None
    )
    debug = PaginationDebugInfo(
        http_status=_to_int(raw.get("http_status")),
        content_type=safe_debug_value(
            raw.get("content_type") or ""
        ),
        json_parse_ok=bool(raw.get("json_parse_ok")),
        response_code=code,
        response_message=safe_debug_value(message),
        has_next_pagination=isinstance(
            next_pagination,
            dict,
        ),
        next_page=next_state.page if next_state else None,
        next_cursor=(
            next_state.next_item_cursor
            if next_state
            else None
        ),
        has_more=next_state.has_more if next_state else None,
    )

    if (
        not raw.get("ok")
        or not raw.get("json_parse_ok")
        or code != 0
        or next_state is None
    ):
        return MarketplaceFetchOutcome(
            page_result=None,
            debug=debug,
        )

    return MarketplaceFetchOutcome(
        page_result=MarketplacePageResult(
            creator_ids=extract_creator_ids(body),
            next_state=next_state,
        ),
        debug=debug,
    )


def format_pagination_debug(
    debug: PaginationDebugInfo,
) -> str:
    parts = [
        "[PAGINATION_DEBUG]",
        f"http_status={_debug_scalar(debug.http_status)}",
        f"content_type={_debug_scalar(debug.content_type)}",
        f"json_parse_ok={debug.json_parse_ok}",
        f"response_code={_debug_scalar(debug.response_code)}",
        f"response_message={_debug_scalar(debug.response_message)}",
        f"has_next_pagination={debug.has_next_pagination}",
        f"next_page={_debug_scalar(debug.next_page)}",
        f"next_cursor={_debug_scalar(debug.next_cursor)}",
        f"has_more={_debug_scalar(debug.has_more)}",
    ]

    if debug.exception_type:
        parts.extend(
            [
                f"exception_type={safe_debug_value(debug.exception_type)}",
                "safe_exception_message="
                f"{safe_debug_value(debug.safe_exception_message)}",
            ]
        )

    return " ".join(parts)


def safe_debug_value(
    value: Any,
) -> str:
    if value is None:
        return ""

    text = str(value)
    text = re.sub(
        r"https?://\S+",
        "[redacted-url]",
        text,
    )
    text = " ".join(text.split())

    lowered = text.lower()

    if any(
        marker in lowered
        for marker in SENSITIVE_LOG_MARKERS
    ):
        return "[redacted]"

    if len(text) > 160:
        return f"{text[:157]}..."

    return text


def _to_int(
    value: Any,
) -> int | None:
    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _debug_scalar(
    value: Any,
) -> str:
    if value is None:
        return ""

    return safe_debug_value(value)


def _to_bool(
    value: Any,
) -> bool | None:
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        return None

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized == "true":
            return True

        if normalized == "false":
            return False

    return None
