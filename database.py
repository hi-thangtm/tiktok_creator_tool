import sqlite3
from pathlib import Path
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "creators.db"


def get_connection():
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_database():
    connection = get_connection()

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

    connection.commit()

    # Migration nhe cho database cu
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

    connection.commit()
    connection.close()


def creator_exists(
    creator_id,
):
    connection = get_connection()

    row = connection.execute(
        """
        SELECT creator_id
        FROM creators
        WHERE creator_id = ?
        LIMIT 1
        """,
        (creator_id,),
    ).fetchone()

    connection.close()

    return row is not None


def get_creator(
    creator_id,
):
    connection = get_connection()

    row = connection.execute(
        """
        SELECT *
        FROM creators
        WHERE creator_id = ?
        LIMIT 1
        """,
        (creator_id,),
    ).fetchone()

    connection.close()

    if row is None:
        return None

    return dict(row)


def save_creator(
    data,
):
    now = datetime.now().isoformat(
        timespec="seconds"
    )

    connection = get_connection()

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
    connection.close()


def save_error(
    creator_id,
    error_message,
):
    connection = get_connection()

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
    connection.close()


def count_creators():
    connection = get_connection()

    row = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM creators
        """
    ).fetchone()

    connection.close()

    return int(
        row["total"]
    )


def count_by_status():
    connection = get_connection()

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

    connection.close()

    return {
        row["status"]: row["total"]
        for row in rows
    }


def print_database_path():
    print(
        "Database:",
        DATABASE_PATH,
    )