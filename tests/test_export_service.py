import json
import tempfile
import unittest
from pathlib import Path

import openpyxl

from core.database import DatabaseRepository
from services.export_service import (
    NoPhoneCreatorsToExport,
    build_export_rows,
    export_creators_to_excel,
    sanitize_filename,
)


def creator_row(
    creator_id: str,
    status: str,
    *,
    zalo: str | None = None,
    phones_all: list[str] | None = None,
    bio_phones: list[str] | None = None,
    phone_sources: dict[str, list[str]] | None = None,
    official_email: str | None = None,
    emails_all: list[str] | None = None,
    bio_emails: list[str] | None = None,
    email_sources: dict[str, list[str]] | None = None,
    last_checked_at: str = "2026-08-13T10:00:00",
) -> dict:
    return {
        "creator_id": creator_id,
        "nickname": f"Nick {creator_id[-3:]}",
        "username": f"user_{creator_id[-3:]}",
        "detail_url": f"https://example.test/detail?cid={creator_id}",
        "zalo": zalo,
        "bio_phones": json.dumps(bio_phones or []),
        "phones_all": json.dumps(phones_all or []),
        "phone_sources": json.dumps(phone_sources or {}),
        "official_email": official_email,
        "bio_emails": json.dumps(bio_emails or []),
        "emails_all": json.dumps(emails_all or []),
        "email_sources": json.dumps(email_sources or {}),
        "bio": "",
        "contact_note": "",
        "status": status,
        "last_checked_at": last_checked_at,
    }


class ExportServiceTest(unittest.TestCase):
    def test_only_creators_with_valid_phone_are_exported(self):
        rows = [
            creator_row(
                "7494007788183127001",
                "FOUND_PHONE",
                phones_all=["+84974581131"],
                phone_sources={
                    "+84974581131": ["Bio"],
                },
            ),
            creator_row(
                "7494007788183127002",
                "FOUND_PHONE_EMAIL",
                zalo="0935850488",
                phones_all=["+84935850488"],
                phone_sources={
                    "+84935850488": ["Zalo"],
                },
            ),
            creator_row(
                "7494007788183127003",
                "FOUND_EMAIL",
                official_email="email@example.com",
                emails_all=["email@example.com"],
            ),
            creator_row(
                "7494007788183127004",
                "NO_CONTACT",
                phones_all=["+84911111111"],
            ),
            creator_row(
                "7494007788183127005",
                "PENDING",
                phones_all=["+84922222222"],
            ),
            creator_row(
                "7494007788183127006",
                "ERROR",
                phones_all=["+84933333333"],
            ),
        ]

        export_rows = build_export_rows(rows)

        self.assertEqual(len(export_rows), 2)
        self.assertEqual(
            [
                row[1]
                for row in export_rows
            ],
            [
                "7494007788183127001",
                "7494007788183127002",
            ],
        )

    def test_found_email_with_valid_phone_is_exported(self):
        rows = [
            creator_row(
                "7494007788183127007",
                "FOUND_EMAIL",
                phones_all=["+84974581131"],
                phone_sources={
                    "+84974581131": ["Bio"],
                },
                official_email="email@example.com",
                emails_all=["email@example.com"],
            ),
        ]

        export_rows = build_export_rows(rows)

        self.assertEqual(len(export_rows), 1)
        self.assertEqual(export_rows[0][4], "0974 581 131")

    def test_duplicate_phones_are_exported_once(self):
        rows = [
            creator_row(
                "7494007788183127010",
                "FOUND_PHONE",
                phones_all=[
                    "+84974581131",
                    "0974 581 131",
                    "+84974581131",
                ],
                phone_sources={
                    "+84974581131": ["Bio"],
                },
            ),
        ]

        export_rows = build_export_rows(rows)

        self.assertEqual(export_rows[0][4], "0974 581 131")
        self.assertEqual(export_rows[0][6], "")

    def test_same_zalo_and_bio_phone_uses_combined_source(self):
        rows = [
            creator_row(
                "7494007788183127020",
                "FOUND_PHONE",
                zalo="+84935850488",
                phones_all=["+84935850488"],
                bio_phones=["0935 850 488"],
                phone_sources={
                    "+84935850488": [
                        "Zalo",
                        "Bio",
                    ],
                },
            ),
        ]

        export_rows = build_export_rows(rows)

        self.assertEqual(export_rows[0][4], "0935 850 488")
        self.assertEqual(export_rows[0][5], "Zalo + Bio")
        self.assertEqual(export_rows[0][6], "")

    def test_different_zalo_and_bio_phone_sets_other_phone(self):
        rows = [
            creator_row(
                "7494007788183127030",
                "FOUND_PHONE",
                zalo="0935850488",
                phones_all=[
                    "+84935850488",
                    "+84355355225",
                    "0903123456",
                ],
                bio_phones=[
                    "0355355225",
                    "0903123456",
                ],
                phone_sources={
                    "+84935850488": ["Zalo"],
                    "+84355355225": ["Bio"],
                    "+84903123456": ["Bio"],
                },
            ),
        ]

        export_rows = build_export_rows(rows)

        self.assertEqual(export_rows[0][4], "0935 850 488")
        self.assertEqual(export_rows[0][5], "Zalo")
        self.assertEqual(
            export_rows[0][6],
            "0355 355 225, 0903 123 456",
        )

    def test_creator_id_and_phone_columns_are_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = export_creators_to_excel(
                [
                    creator_row(
                        "7494007788183127056",
                        "FOUND_PHONE_EMAIL",
                        zalo="+84974581131",
                        phones_all=["+84974581131"],
                        phone_sources={
                            "+84974581131": ["Zalo"],
                        },
                        official_email="creator@example.com",
                        emails_all=["creator@example.com"],
                        email_sources={
                            "creator@example.com": ["Contact"],
                        },
                    ),
                ],
                Path(temp_dir),
                "test.xlsx",
            )

            workbook = openpyxl.load_workbook(result.path)
            sheet = workbook.active

            self.assertEqual(
                sheet["B2"].value,
                "7494007788183127056",
            )
            self.assertEqual(sheet["B2"].number_format, "@")
            self.assertEqual(sheet["E2"].value, "0974 581 131")
            self.assertEqual(sheet["E2"].number_format, "@")

    def test_phone_starting_with_84_is_displayed_as_local_phone(self):
        rows = [
            creator_row(
                "7494007788183127060",
                "FOUND_PHONE",
                phones_all=["+84355355225"],
                phone_sources={
                    "+84355355225": ["Bio"],
                },
            ),
        ]

        export_rows = build_export_rows(rows)

        self.assertEqual(export_rows[0][4], "0355 355 225")

    def test_no_phone_creators_do_not_create_empty_workbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = Path(temp_dir)

            with self.assertRaises(NoPhoneCreatorsToExport):
                export_creators_to_excel(
                    [
                        creator_row(
                            "7494007788183127070",
                            "FOUND_EMAIL",
                            official_email="creator@example.com",
                            emails_all=["creator@example.com"],
                        ),
                    ],
                    export_dir,
                    "empty.xlsx",
                )

            self.assertFalse(
                (export_dir / "empty.xlsx").exists()
            )

    def test_export_filename_is_sanitized_for_windows(self):
        self.assertEqual(
            sanitize_filename('bad<name>:x?/file*.xlsx'),
            "bad_name__x__file_.xlsx",
        )
        self.assertEqual(
            sanitize_filename("CON.xlsx"),
            "_CON.xlsx",
        )

    def test_export_uses_sanitized_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = export_creators_to_excel(
                [
                    creator_row(
                        "7494007788183127071",
                        "FOUND_PHONE",
                        phones_all=["+84974581131"],
                        phone_sources={
                            "+84974581131": ["Bio"],
                        },
                    ),
                ],
                Path(temp_dir),
                'bad<name>:x?/file*.xlsx',
            )

            self.assertEqual(
                result.path.name,
                "bad_name__x__file_.xlsx",
            )
            self.assertTrue(result.path.exists())

    def test_repository_export_query_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DatabaseRepository(
                Path(temp_dir) / "creators.db"
            )
            repository.init_database()

            first = creator_row(
                "7494007788183127080",
                "FOUND_PHONE",
                phones_all=["+84974581131"],
                phone_sources={
                    "+84974581131": ["Bio"],
                },
                last_checked_at="2026-08-13T10:00:00",
            )
            second = creator_row(
                "7494007788183127081",
                "FOUND_PHONE",
                phones_all=["+84974581131"],
                phone_sources={
                    "+84974581131": ["Bio"],
                },
                last_checked_at="2026-08-13T11:00:00",
            )
            third = creator_row(
                "7494007788183127082",
                "FOUND_PHONE",
                phones_all=["+84974581131"],
                phone_sources={
                    "+84974581131": ["Bio"],
                },
                last_checked_at="2026-08-13T11:00:00",
            )

            for row in [first, second, third]:
                repository.save_creator(row)

            connection = repository.get_connection()

            try:
                for row in [first, second, third]:
                    connection.execute(
                        """
                        UPDATE creators
                        SET last_checked_at = ?
                        WHERE creator_id = ?
                        """,
                        (
                            row["last_checked_at"],
                            row["creator_id"],
                        ),
                    )

                connection.commit()
            finally:
                connection.close()

            rows = repository.list_creators_for_phone_export()

            self.assertEqual(
                [
                    row["creator_id"]
                    for row in rows
                ],
                [
                    "7494007788183127082",
                    "7494007788183127081",
                    "7494007788183127080",
                ],
            )


if __name__ == "__main__":
    unittest.main()
