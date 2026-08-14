import json
import unittest

from core.contact_utils import (
    build_display_contact,
    determine_status,
    filter_creator_rows,
    find_all_emails,
    find_all_phones,
    merge_contacts,
    normalize_phone,
)


class ContactUtilsTest(unittest.TestCase):
    def test_normalize_phone(self):
        self.assertEqual(
            normalize_phone("0974 581 131"),
            "+84974581131",
        )
        self.assertEqual(
            normalize_phone("+84 974-581-131"),
            "+84974581131",
        )
        self.assertEqual(
            normalize_phone("974581131"),
            "+84974581131",
        )
        self.assertIsNone(
            normalize_phone("12345"),
        )

    def test_duplicate_phone_merge_keeps_single_display_phone(self):
        row = {
            "zalo": "+84974581131",
            "bio_phones": json.dumps(["0974581131"]),
            "phones_all": json.dumps(["+84974581131"]),
            "phone_sources": json.dumps(
                {
                    "+84974581131": [
                        "Zalo",
                        "Bio",
                    ],
                }
            ),
            "emails_all": "[]",
            "email_sources": "{}",
        }

        contact = build_display_contact(row)

        self.assertEqual(
            contact["main_phone"],
            "0974 581 131",
        )
        self.assertEqual(
            contact["phone_source"],
            "Zalo + Bio",
        )
        self.assertEqual(
            contact["other_phones"],
            "",
        )

    def test_zalo_priority_and_other_bio_phone(self):
        row = {
            "zalo": "0974581131",
            "bio_phones": json.dumps(["0903555666"]),
            "phones_all": json.dumps(
                [
                    "+84974581131",
                    "+84903555666",
                ]
            ),
            "phone_sources": json.dumps(
                {
                    "+84974581131": ["Zalo"],
                    "+84903555666": ["Bio"],
                }
            ),
            "emails_all": "[]",
            "email_sources": "{}",
        }

        contact = build_display_contact(row)

        self.assertEqual(
            contact["main_phone"],
            "0974 581 131",
        )
        self.assertEqual(
            contact["phone_source"],
            "Zalo",
        )
        self.assertEqual(
            contact["other_phones"],
            "0903 555 666",
        )

    def test_bio_phone_extraction(self):
        self.assertEqual(
            find_all_phones("Zalo 0974.581.131 hoac +84 903 555 666"),
            [
                "+84974581131",
                "+84903555666",
            ],
        )

    def test_email_extraction(self):
        self.assertEqual(
            find_all_emails("Mail A@Example.com, b@test.vn"),
            [
                "a@example.com",
                "b@test.vn",
            ],
        )

    def test_determine_status(self):
        self.assertEqual(
            determine_status(
                {
                    "all_phones": ["+84974581131"],
                    "all_emails": ["a@example.com"],
                }
            ),
            "FOUND_PHONE_EMAIL",
        )
        self.assertEqual(
            determine_status(
                {
                    "all_phones": ["+84974581131"],
                    "all_emails": [],
                }
            ),
            "FOUND_PHONE",
        )
        self.assertEqual(
            determine_status(
                {
                    "all_phones": [],
                    "all_emails": ["a@example.com"],
                }
            ),
            "FOUND_EMAIL",
        )
        self.assertEqual(
            determine_status(
                {
                    "all_phones": [],
                    "all_emails": [],
                }
            ),
            "NO_CONTACT",
        )

    def test_merge_contacts_keeps_legacy_logic(self):
        result = merge_contacts(
            {
                "zalo": "+84974581131",
                "email": "A@Example.com",
            },
            "Bio 0974581131 va b@test.vn",
        )

        self.assertEqual(
            result["all_phones"],
            ["+84974581131"],
        )
        self.assertEqual(
            result["phone_sources"]["+84974581131"],
            [
                "Zalo",
                "Bio",
            ],
        )
        self.assertEqual(
            result["all_emails"],
            [
                "a@example.com",
                "b@test.vn",
            ],
        )

    def test_filters(self):
        rows = [
            {
                "creator_id": "1",
                "username": "alpha",
                "nickname": "Alpha",
                "zalo": "+84974581131",
                "bio_phones": "[]",
                "phones_all": json.dumps(["+84974581131"]),
                "phone_sources": json.dumps({"+84974581131": ["Zalo"]}),
                "official_email": None,
                "bio_emails": "[]",
                "emails_all": "[]",
                "email_sources": "{}",
                "status": "FOUND_PHONE",
            },
            {
                "creator_id": "2",
                "username": "beta",
                "nickname": "Beta",
                "zalo": None,
                "bio_phones": "[]",
                "phones_all": "[]",
                "phone_sources": "{}",
                "official_email": "beta@example.com",
                "bio_emails": "[]",
                "emails_all": json.dumps(["beta@example.com"]),
                "email_sources": json.dumps({"beta@example.com": ["Contact"]}),
                "status": "FOUND_EMAIL",
            },
        ]

        self.assertEqual(
            [
                row["creator_id"]
                for row in filter_creator_rows(
                    rows,
                    search="0974",
                )
            ],
            ["1"],
        )
        self.assertEqual(
            [
                row["creator_id"]
                for row in filter_creator_rows(
                    rows,
                    search="0974581131",
                )
            ],
            ["1"],
        )
        self.assertEqual(
            [
                row["creator_id"]
                for row in filter_creator_rows(
                    rows,
                    contact_filter="has_email",
                )
            ],
            ["2"],
        )
        self.assertEqual(
            [
                row["creator_id"]
                for row in filter_creator_rows(
                    rows,
                    phone_source_filter="zalo",
                )
            ],
            ["1"],
        )


if __name__ == "__main__":
    unittest.main()
