import json
import unittest

from services.contact_service import build_creator_save_data


class ContactServiceTests(unittest.TestCase):
    def test_build_save_data_merges_same_zalo_and_bio_phone(self):
        data, result = build_creator_save_data(
            creator_id="123",
            detail_url="https://example.test/detail?cid=123",
            identity={
                "username": "creator_a",
                "nickname": "Creator A",
            },
            bio="Booking 0935 850 488 - hello@example.com",
            popup_contact={
                "zalo": "+84935850488",
                "email": None,
            },
        )

        self.assertEqual(data["status"], "FOUND_PHONE_EMAIL")
        self.assertEqual(result["all_phones"], ["+84935850488"])
        self.assertEqual(
            json.loads(data["phone_sources"]),
            {
                "+84935850488": [
                    "Zalo",
                    "Bio",
                ],
            },
        )
        self.assertEqual(
            json.loads(data["emails_all"]),
            ["hello@example.com"],
        )

    def test_build_save_data_keeps_zalo_before_different_bio_phone(self):
        data, result = build_creator_save_data(
            creator_id="456",
            detail_url="https://example.test/detail?cid=456",
            identity={
                "username": "creator_b",
                "nickname": "Creator B",
            },
            bio="Call 0974 581 131",
            popup_contact={
                "zalo": "+84935850488",
                "email": None,
            },
        )

        self.assertEqual(data["status"], "FOUND_PHONE")
        self.assertEqual(
            result["all_phones"],
            [
                "+84935850488",
                "+84974581131",
            ],
        )
        self.assertEqual(
            json.loads(data["phone_sources"]),
            {
                "+84935850488": ["Zalo"],
                "+84974581131": ["Bio"],
            },
        )
        self.assertIn(
            "+84974581131",
            data["contact_note"],
        )

    def test_build_save_data_merges_same_official_and_bio_email(self):
        data, result = build_creator_save_data(
            creator_id="789",
            detail_url="https://example.test/detail?cid=789",
            identity={
                "username": "creator_c",
                "nickname": "Creator C",
            },
            bio="Email HELLO@example.com",
            popup_contact={
                "zalo": None,
                "email": "hello@example.com",
            },
        )

        self.assertEqual(data["status"], "FOUND_EMAIL")
        self.assertEqual(result["all_emails"], ["hello@example.com"])
        self.assertEqual(
            json.loads(data["email_sources"]),
            {
                "hello@example.com": [
                    "Contact",
                    "Bio",
                ],
            },
        )

    def test_build_save_data_no_contact(self):
        data, result = build_creator_save_data(
            creator_id="999",
            detail_url="https://example.test/detail?cid=999",
            identity={
                "username": None,
                "nickname": None,
            },
            bio="No contact here",
            popup_contact={
                "zalo": None,
                "email": None,
            },
        )

        self.assertEqual(data["status"], "NO_CONTACT")
        self.assertEqual(result["all_phones"], [])
        self.assertEqual(result["all_emails"], [])


if __name__ == "__main__":
    unittest.main()
