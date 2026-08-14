from database import get_connection


def add_creator_to_queue(creator_id: str):
    """
    Tao creator trong database neu chua ton tai.
    Khong ghi de du lieu creator da co.
    """

    connection = get_connection()

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
    connection.close()


def add_creators_to_queue(creator_ids):
    """
    Them nhieu creator vao queue.
    """

    for creator_id in creator_ids:
        creator_id = str(creator_id).strip()

        if not creator_id:
            continue

        add_creator_to_queue(
            creator_id
        )


def get_pending_creators():
    """
    Lay danh sach creator can xu ly.

    Bao gom:
    PENDING
    RETRY
    ERROR

    Sau nay co the thay doi de ERROR khong tu retry.
    """

    connection = get_connection()

    rows = connection.execute(
        """
        SELECT creator_id
        FROM creators
        WHERE status IN (
            'PENDING',
            'RETRY',
            'ERROR'
        )
        ORDER BY id ASC
        """
    ).fetchall()

    connection.close()

    return [
        row["creator_id"]
        for row in rows
    ]


def set_creator_status(
    creator_id,
    status,
):
    connection = get_connection()

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
    connection.close()


def should_skip_creator(
    creator_id,
):
    """
    Neu creator da xu ly thanh cong thi skip.
    """

    connection = get_connection()

    row = connection.execute(
        """
        SELECT status
        FROM creators
        WHERE creator_id = ?
        LIMIT 1
        """,
        (creator_id,),
    ).fetchone()

    connection.close()

    if not row:
        return False

    completed_statuses = {
        "FOUND_PHONE_EMAIL",
        "FOUND_PHONE",
        "FOUND_EMAIL",
        "NO_CONTACT",
    }

    return (
        row["status"]
        in completed_statuses
    )