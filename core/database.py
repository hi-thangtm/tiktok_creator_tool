"""SQLite repository and safe legacy database migration."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import (
    COMPLETED_STATUSES,
    PROCESSABLE_STATUSES,
)
from .paths import (
    AppPaths,
    default_paths,
    ensure_app_directories,
)


CREATOR_COLUMNS = (
    "id",
    "creator_id",
    "username",
    "nickname",
    "detail_url",
    "zalo",
    "official_email",
    "bio",
    "bio_phones",
    "bio_emails",
    "phones_all",
    "emails_all",
    "phone_sources",
    "email_sources",
    "contact_note",
    "status",
    "error_message",
    "retry_count",
    "first_seen_at",
    "last_checked_at",
)


@dataclass(frozen=True)
class MigrationResult:
    migrated: bool
    source_path: Path | None
    destination_path: Path
    backup_path: Path | None
    message: str


@dataclass(frozen=True)
class CreatorScanCheckpoint:
    segment_key: str
    filters_json: str
    next_page: int
    next_item_cursor: int
    page_size: int
    has_more: bool
    total_scanned: int
    total_new: int
    total_duplicate: int
    last_success_at: str | None
    updated_at: str


@dataclass(frozen=True)
class CreatorScanRefreshState:
    segment_key: str
    filters_json: str
    refresh_next_page: int
    refresh_next_cursor: int
    page_size: int
    refresh_cycle: int
    refresh_total_scanned: int
    refresh_total_new: int
    refresh_total_duplicate: int
    refresh_restart_after_head: bool
    last_refresh_at: str | None
    last_cycle_completed_at: str | None
    updated_at: str


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _log_migration(
    paths: AppPaths,
    message: str,
) -> None:
    paths.logs_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_path = paths.logs_dir / "migration.log"

    with log_path.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            f"{datetime.now().isoformat(timespec='seconds')} "
            f"{message}\n"
        )


def sqlite_integrity_check(db_path: Path) -> bool:
    if not db_path.exists():
        return False

    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(
        uri,
        uri=True,
    )

    try:
        row = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()

        return bool(
            row
            and row[0] == "ok"
        )
    finally:
        connection.close()


def _backup_path_for(source_path: Path) -> Path:
    backup_path = (
        source_path.parent
        / f"creators_backup_{_timestamp()}.db"
    )

    if not backup_path.exists():
        return backup_path

    index = 2

    while True:
        candidate = (
            source_path.parent
            / f"creators_backup_{_timestamp()}_{index}.db"
        )

        if not candidate.exists():
            return candidate

        index += 1


def migrate_legacy_database(
    paths: AppPaths | None = None,
) -> MigrationResult:
    paths = paths or default_paths()
    ensure_app_directories(paths)

    destination = paths.database_path
    source = paths.legacy_database_path

    if destination.exists():
        message = (
            "Application Support database already exists; "
            "legacy database was not copied."
        )
        _log_migration(paths, message)

        return MigrationResult(
            migrated=False,
            source_path=source if source.exists() else None,
            destination_path=destination,
            backup_path=None,
            message=message,
        )

    if not source.exists():
        message = (
            "No legacy database found; a new database will be "
            "created in Application Support."
        )
        _log_migration(paths, message)

        return MigrationResult(
            migrated=False,
            source_path=None,
            destination_path=destination,
            backup_path=None,
            message=message,
        )

    if not sqlite_integrity_check(source):
        message = (
            "Legacy database failed SQLite integrity_check; "
            "migration was not attempted."
        )
        _log_migration(paths, message)
        raise RuntimeError(message)

    backup_path = _backup_path_for(source)
    shutil.copy2(source, backup_path)

    if not sqlite_integrity_check(backup_path):
        message = (
            "Legacy database backup failed integrity_check; "
            "migration was not attempted."
        )
        _log_migration(paths, message)
        raise RuntimeError(message)

    shutil.copy2(source, destination)

    if not sqlite_integrity_check(destination):
        try:
            destination.unlink()
        except OSError:
            pass

        message = (
            "Copied Application Support database failed "
            "integrity_check; copied file was removed."
        )
        _log_migration(paths, message)
        raise RuntimeError(message)

    message = (
        f"Migrated legacy database from {source} "
        f"to {destination}; backup at {backup_path}."
    )
    _log_migration(paths, message)

    return MigrationResult(
        migrated=True,
        source_path=source,
        destination_path=destination,
        backup_path=backup_path,
        message=message,
    )


class DatabaseRepository:
    def __init__(
        self,
        db_path: Path,
    ) -> None:
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row

        return connection

    def init_database(self) -> None:
        connection = self.get_connection()

        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS creators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    creator_id TEXT NOT NULL UNIQUE,

                    username TEXT,
                    nickname TEXT,

                    detail_url TEXT,

                    zalo TEXT,
                    official_email TEXT,

                    bio TEXT,

                    bio_phones TEXT,
                    bio_emails TEXT,

                    phones_all TEXT,
                    emails_all TEXT,

                    phone_sources TEXT,
                    email_sources TEXT,

                    contact_note TEXT,

                    status TEXT NOT NULL DEFAULT 'PENDING',

                    error_message TEXT,

                    retry_count INTEGER NOT NULL DEFAULT 0,

                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            existing_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(creators)"
                ).fetchall()
            }

            if "error_message" not in existing_columns:
                connection.execute(
                    """
                    ALTER TABLE creators
                    ADD COLUMN error_message TEXT
                    """
                )

            if "retry_count" not in existing_columns:
                connection.execute(
                    """
                    ALTER TABLE creators
                    ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0
                    """
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_scan_checkpoints (
                    segment_key TEXT PRIMARY KEY,
                    filters_json TEXT NOT NULL,
                    next_page INTEGER NOT NULL DEFAULT 0,
                    next_item_cursor INTEGER NOT NULL DEFAULT 0,
                    page_size INTEGER NOT NULL DEFAULT 12,
                    has_more INTEGER NOT NULL DEFAULT 1,
                    total_scanned INTEGER NOT NULL DEFAULT 0,
                    total_new INTEGER NOT NULL DEFAULT 0,
                    total_duplicate INTEGER NOT NULL DEFAULT 0,
                    last_success_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_creator_scan_checkpoints_updated_at
                ON creator_scan_checkpoints(updated_at)
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_scan_refresh_state (
                    segment_key TEXT PRIMARY KEY,
                    filters_json TEXT NOT NULL,
                    refresh_next_page INTEGER NOT NULL DEFAULT 0,
                    refresh_next_cursor INTEGER NOT NULL DEFAULT 0,
                    page_size INTEGER NOT NULL DEFAULT 12,
                    refresh_cycle INTEGER NOT NULL DEFAULT 1,
                    refresh_total_scanned INTEGER NOT NULL DEFAULT 0,
                    refresh_total_new INTEGER NOT NULL DEFAULT 0,
                    refresh_total_duplicate INTEGER NOT NULL DEFAULT 0,
                    refresh_restart_after_head INTEGER NOT NULL DEFAULT 1,
                    last_refresh_at TEXT,
                    last_cycle_completed_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_creator_scan_refresh_state_updated_at
                ON creator_scan_refresh_state(updated_at)
                """
            )

            connection.commit()
        finally:
            connection.close()

    def add_creator_to_queue(
        self,
        creator_id: str,
    ) -> None:
        connection = self.get_connection()

        try:
            connection.execute(
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
            connection.commit()
        finally:
            connection.close()

    def add_creators_to_queue(
        self,
        creator_ids: list[str],
    ) -> None:
        for creator_id in creator_ids:
            creator_id = str(creator_id).strip()

            if creator_id:
                self.add_creator_to_queue(creator_id)

    def get_creator(
        self,
        creator_id: str,
    ) -> dict[str, Any] | None:
        connection = self.get_connection()

        try:
            row = connection.execute(
                """
                SELECT *
                FROM creators
                WHERE creator_id = ?
                LIMIT 1
                """,
                (creator_id,),
            ).fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        return dict(row)

    def list_creators(
        self,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        connection = self.get_connection()

        try:
            sql = """
                SELECT *
                FROM creators
                ORDER BY id ASC
            """
            params: list[Any] = []

            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])

            rows = connection.execute(
                sql,
                params,
            ).fetchall()
        finally:
            connection.close()

        return [
            dict(row)
            for row in rows
        ]

    def list_creators_for_phone_export(
        self,
    ) -> list[dict[str, Any]]:
        connection = self.get_connection()

        try:
            rows = connection.execute(
                """
                SELECT *
                FROM creators
                ORDER BY
                    last_checked_at DESC,
                    id DESC
                """
            ).fetchall()
        finally:
            connection.close()

        return [
            dict(row)
            for row in rows
        ]

    def get_pending_creators(
        self,
        limit: int | None = None,
    ) -> list[str]:
        connection = self.get_connection()

        try:
            placeholders = ",".join(
                "?"
                for _ in PROCESSABLE_STATUSES
            )
            sql = f"""
                SELECT creator_id
                FROM creators
                WHERE status IN ({placeholders})
                ORDER BY id ASC
            """
            params: list[Any] = list(PROCESSABLE_STATUSES)

            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)

            rows = connection.execute(
                sql,
                params,
            ).fetchall()
        finally:
            connection.close()

        return [
            row["creator_id"]
            for row in rows
        ]

    def count_pending_creators(self) -> int:
        connection = self.get_connection()

        try:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM creators
                WHERE status = 'PENDING'
                """
            ).fetchone()
        finally:
            connection.close()

        return int(row["total"])

    def count_completed_creators(self) -> int:
        connection = self.get_connection()

        try:
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
        finally:
            connection.close()

        return int(row["total"])

    def get_existing_creator_ids(
        self,
        creator_ids: list[str],
    ) -> set[str]:
        if not creator_ids:
            return set()

        result: set[str] = set()
        connection = self.get_connection()

        try:
            chunk_size = 500

            for start in range(
                0,
                len(creator_ids),
                chunk_size,
            ):
                chunk = creator_ids[start:start + chunk_size]

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
                    WHERE creator_id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()

                for row in rows:
                    result.add(
                        str(row["creator_id"])
                    )
        finally:
            connection.close()

        return result

    def insert_creators_pending(
        self,
        creator_ids: list[str],
        max_to_insert: int,
    ) -> list[str]:
        if (
            not creator_ids
            or max_to_insert <= 0
        ):
            return []

        creator_ids = creator_ids[:max_to_insert]
        inserted: list[str] = []
        connection = self.get_connection()

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
                    inserted.append(creator_id)

            connection.commit()
        finally:
            connection.close()

        return inserted

    def set_creator_status(
        self,
        creator_id: str,
        status: str,
    ) -> None:
        connection = self.get_connection()

        try:
            connection.execute(
                """
                UPDATE creators
                SET
                    status = ?,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE creator_id = ?
                """,
                (
                    status,
                    creator_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def requeue_creator(
        self,
        creator_id: str,
    ) -> None:
        connection = self.get_connection()

        try:
            connection.execute(
                """
                UPDATE creators
                SET
                    status = 'PENDING',
                    error_message = NULL,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE creator_id = ?
                """,
                (creator_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def reset_processing_to_pending(self) -> int:
        connection = self.get_connection()

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
            return int(cursor.rowcount)
        finally:
            connection.close()

    def should_skip_creator(
        self,
        creator_id: str,
    ) -> bool:
        row = self.get_creator(creator_id)

        if not row:
            return False

        return row["status"] in COMPLETED_STATUSES

    def save_creator(
        self,
        data: dict[str, Any],
    ) -> None:
        now = datetime.now().isoformat(
            timespec="seconds"
        )

        connection = self.get_connection()

        try:
            connection.execute(
                """
                INSERT INTO creators (
                    creator_id,

                    username,
                    nickname,

                    detail_url,

                    zalo,
                    official_email,

                    bio,

                    bio_phones,
                    bio_emails,

                    phones_all,
                    emails_all,

                    phone_sources,
                    email_sources,

                    contact_note,

                    status,

                    error_message,

                    first_seen_at,
                    last_checked_at
                )
                VALUES (
                    :creator_id,

                    :username,
                    :nickname,

                    :detail_url,

                    :zalo,
                    :official_email,

                    :bio,

                    :bio_phones,
                    :bio_emails,

                    :phones_all,
                    :emails_all,

                    :phone_sources,
                    :email_sources,

                    :contact_note,

                    :status,

                    NULL,

                    :first_seen_at,
                    :last_checked_at
                )

                ON CONFLICT(creator_id)
                DO UPDATE SET

                    username = excluded.username,
                    nickname = excluded.nickname,

                    detail_url = excluded.detail_url,

                    zalo = excluded.zalo,
                    official_email = excluded.official_email,

                    bio = excluded.bio,

                    bio_phones = excluded.bio_phones,
                    bio_emails = excluded.bio_emails,

                    phones_all = excluded.phones_all,
                    emails_all = excluded.emails_all,

                    phone_sources = excluded.phone_sources,
                    email_sources = excluded.email_sources,

                    contact_note = excluded.contact_note,

                    status = excluded.status,

                    error_message = NULL,

                    last_checked_at = excluded.last_checked_at
                """,
                {
                    **data,
                    "first_seen_at": now,
                    "last_checked_at": now,
                },
            )
            connection.commit()
        finally:
            connection.close()

    def save_error(
        self,
        creator_id: str,
        error_message: Exception | str,
    ) -> None:
        connection = self.get_connection()

        try:
            connection.execute(
                """
                UPDATE creators
                SET
                    status = 'ERROR',
                    error_message = ?,
                    retry_count = retry_count + 1,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE creator_id = ?
                """,
                (
                    str(error_message),
                    creator_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def count_creators(self) -> int:
        connection = self.get_connection()

        try:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM creators
                """
            ).fetchone()
        finally:
            connection.close()

        return int(row["total"])

    def count_by_status(self) -> dict[str, int]:
        connection = self.get_connection()

        try:
            rows = connection.execute(
                """
                SELECT
                    status,
                    COUNT(*) AS total
                FROM creators
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        finally:
            connection.close()

        return {
            row["status"]: int(row["total"])
            for row in rows
        }

    def get_stats(self) -> dict[str, int]:
        status_counts = self.count_by_status()

        return {
            "total": self.count_creators(),
            "pending": status_counts.get("PENDING", 0),
            "has_phone": (
                status_counts.get("FOUND_PHONE", 0)
                + status_counts.get("FOUND_PHONE_EMAIL", 0)
            ),
            "has_email": (
                status_counts.get("FOUND_EMAIL", 0)
                + status_counts.get("FOUND_PHONE_EMAIL", 0)
            ),
            "has_phone_email": status_counts.get(
                "FOUND_PHONE_EMAIL",
                0,
            ),
            "no_contact": status_counts.get("NO_CONTACT", 0),
            "error": status_counts.get("ERROR", 0),
            "processing": status_counts.get("PROCESSING", 0),
        }

    def get_creator_scan_checkpoint(
        self,
        segment_key: str,
    ) -> CreatorScanCheckpoint | None:
        connection = self.get_connection()

        try:
            row = connection.execute(
                """
                SELECT *
                FROM creator_scan_checkpoints
                WHERE segment_key = ?
                LIMIT 1
                """,
                (segment_key,),
            ).fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        return CreatorScanCheckpoint(
            segment_key=str(row["segment_key"]),
            filters_json=str(row["filters_json"]),
            next_page=int(row["next_page"]),
            next_item_cursor=int(row["next_item_cursor"]),
            page_size=int(row["page_size"]),
            has_more=bool(row["has_more"]),
            total_scanned=int(row["total_scanned"]),
            total_new=int(row["total_new"]),
            total_duplicate=int(row["total_duplicate"]),
            last_success_at=row["last_success_at"],
            updated_at=str(row["updated_at"]),
        )

    def save_creator_scan_checkpoint(
        self,
        segment_key: str,
        filters_json: str,
        next_page: int,
        next_item_cursor: int,
        page_size: int,
        has_more: bool,
        scanned_delta: int = 0,
        new_delta: int = 0,
        duplicate_delta: int = 0,
    ) -> None:
        now = datetime.now().isoformat(
            timespec="seconds"
        )
        connection = self.get_connection()

        try:
            connection.execute(
                """
                INSERT INTO creator_scan_checkpoints (
                    segment_key,
                    filters_json,
                    next_page,
                    next_item_cursor,
                    page_size,
                    has_more,
                    total_scanned,
                    total_new,
                    total_duplicate,
                    last_success_at,
                    updated_at
                )
                VALUES (
                    :segment_key,
                    :filters_json,
                    :next_page,
                    :next_item_cursor,
                    :page_size,
                    :has_more,
                    :scanned_delta,
                    :new_delta,
                    :duplicate_delta,
                    :now,
                    :now
                )
                ON CONFLICT(segment_key)
                DO UPDATE SET
                    filters_json = excluded.filters_json,
                    next_page = excluded.next_page,
                    next_item_cursor = excluded.next_item_cursor,
                    page_size = excluded.page_size,
                    has_more = excluded.has_more,
                    total_scanned = (
                        creator_scan_checkpoints.total_scanned
                        + excluded.total_scanned
                    ),
                    total_new = (
                        creator_scan_checkpoints.total_new
                        + excluded.total_new
                    ),
                    total_duplicate = (
                        creator_scan_checkpoints.total_duplicate
                        + excluded.total_duplicate
                    ),
                    last_success_at = excluded.last_success_at,
                    updated_at = excluded.updated_at
                """,
                {
                    "segment_key": segment_key,
                    "filters_json": filters_json,
                    "next_page": next_page,
                    "next_item_cursor": next_item_cursor,
                    "page_size": page_size,
                    "has_more": 1 if has_more else 0,
                    "scanned_delta": scanned_delta,
                    "new_delta": new_delta,
                    "duplicate_delta": duplicate_delta,
                    "now": now,
                },
            )
            connection.commit()
        finally:
            connection.close()

    def get_creator_scan_refresh_state(
        self,
        segment_key: str,
    ) -> CreatorScanRefreshState | None:
        connection = self.get_connection()

        try:
            row = connection.execute(
                """
                SELECT *
                FROM creator_scan_refresh_state
                WHERE segment_key = ?
                LIMIT 1
                """,
                (segment_key,),
            ).fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        return CreatorScanRefreshState(
            segment_key=str(row["segment_key"]),
            filters_json=str(row["filters_json"]),
            refresh_next_page=int(row["refresh_next_page"]),
            refresh_next_cursor=int(row["refresh_next_cursor"]),
            page_size=int(row["page_size"]),
            refresh_cycle=int(row["refresh_cycle"]),
            refresh_total_scanned=int(row["refresh_total_scanned"]),
            refresh_total_new=int(row["refresh_total_new"]),
            refresh_total_duplicate=int(
                row["refresh_total_duplicate"]
            ),
            refresh_restart_after_head=bool(
                row["refresh_restart_after_head"]
            ),
            last_refresh_at=row["last_refresh_at"],
            last_cycle_completed_at=row["last_cycle_completed_at"],
            updated_at=str(row["updated_at"]),
        )

    def save_creator_scan_refresh_state(
        self,
        segment_key: str,
        filters_json: str,
        refresh_next_page: int,
        refresh_next_cursor: int,
        page_size: int,
        refresh_cycle: int,
        refresh_restart_after_head: bool,
        scanned_delta: int = 0,
        new_delta: int = 0,
        duplicate_delta: int = 0,
        cycle_completed: bool = False,
    ) -> None:
        now = datetime.now().isoformat(
            timespec="seconds"
        )
        last_cycle_completed_at = now if cycle_completed else None
        connection = self.get_connection()

        try:
            connection.execute(
                """
                INSERT INTO creator_scan_refresh_state (
                    segment_key,
                    filters_json,
                    refresh_next_page,
                    refresh_next_cursor,
                    page_size,
                    refresh_cycle,
                    refresh_total_scanned,
                    refresh_total_new,
                    refresh_total_duplicate,
                    refresh_restart_after_head,
                    last_refresh_at,
                    last_cycle_completed_at,
                    updated_at
                )
                VALUES (
                    :segment_key,
                    :filters_json,
                    :refresh_next_page,
                    :refresh_next_cursor,
                    :page_size,
                    :refresh_cycle,
                    :scanned_delta,
                    :new_delta,
                    :duplicate_delta,
                    :refresh_restart_after_head,
                    :now,
                    :last_cycle_completed_at,
                    :now
                )
                ON CONFLICT(segment_key)
                DO UPDATE SET
                    filters_json = excluded.filters_json,
                    refresh_next_page = excluded.refresh_next_page,
                    refresh_next_cursor = excluded.refresh_next_cursor,
                    page_size = excluded.page_size,
                    refresh_cycle = excluded.refresh_cycle,
                    refresh_total_scanned = (
                        creator_scan_refresh_state.refresh_total_scanned
                        + excluded.refresh_total_scanned
                    ),
                    refresh_total_new = (
                        creator_scan_refresh_state.refresh_total_new
                        + excluded.refresh_total_new
                    ),
                    refresh_total_duplicate = (
                        creator_scan_refresh_state.refresh_total_duplicate
                        + excluded.refresh_total_duplicate
                    ),
                    refresh_restart_after_head =
                        excluded.refresh_restart_after_head,
                    last_refresh_at = excluded.last_refresh_at,
                    last_cycle_completed_at = COALESCE(
                        excluded.last_cycle_completed_at,
                        creator_scan_refresh_state.last_cycle_completed_at
                    ),
                    updated_at = excluded.updated_at
                """,
                {
                    "segment_key": segment_key,
                    "filters_json": filters_json,
                    "refresh_next_page": refresh_next_page,
                    "refresh_next_cursor": refresh_next_cursor,
                    "page_size": page_size,
                    "refresh_cycle": refresh_cycle,
                    "refresh_restart_after_head": (
                        1 if refresh_restart_after_head else 0
                    ),
                    "scanned_delta": scanned_delta,
                    "new_delta": new_delta,
                    "duplicate_delta": duplicate_delta,
                    "now": now,
                    "last_cycle_completed_at": last_cycle_completed_at,
                },
            )
            connection.commit()
        finally:
            connection.close()


def prepare_database(
    paths: AppPaths | None = None,
) -> tuple[DatabaseRepository, MigrationResult]:
    paths = paths or default_paths()
    ensure_app_directories(paths)
    migration = migrate_legacy_database(paths)
    repository = DatabaseRepository(paths.database_path)
    repository.init_database()
    repository.reset_processing_to_pending()

    return repository, migration
