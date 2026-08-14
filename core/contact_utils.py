"""Contact parsing, merging, display, and filtering helpers."""

from __future__ import annotations

import json
import re
from typing import Any


PHONE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(?:"
    r"(?:\+?84|0)(?:3|5|7|8|9)(?:[\s.\-]?\d){8}"
    r"|"
    r"(?:3|5|7|8|9)(?:[\s.\-]?\d){8}"
    r")"
    r"(?!\d)"
)

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+"
    r"@[A-Za-z0-9.-]+"
    r"\.[A-Za-z]{2,}\b"
)


def clean_text(value: Any) -> str | None:
    if not value:
        return None

    return " ".join(str(value).split()).strip()


def normalize_phone(value: Any) -> str | None:
    if not value:
        return None

    digits = re.sub(
        r"\D",
        "",
        str(value),
    )

    if (
        digits.startswith("84")
        and len(digits) == 11
    ):
        return "+84" + digits[2:]

    if (
        digits.startswith("0")
        and len(digits) == 10
    ):
        return "+84" + digits[1:]

    if (
        len(digits) == 9
        and digits[0] in "35789"
    ):
        return "+84" + digits

    return None


def find_all_phones(text: str | None) -> list[str]:
    if not text:
        return []

    result: list[str] = []

    for match in PHONE_PATTERN.findall(text):
        phone = normalize_phone(match)

        if (
            phone
            and phone not in result
        ):
            result.append(phone)

    return result


def find_all_emails(text: str | None) -> list[str]:
    if not text:
        return []

    result: list[str] = []

    for email in EMAIL_PATTERN.findall(text):
        email = email.strip().lower()

        if email not in result:
            result.append(email)

    return result


def parse_json_list(value: Any) -> list[str]:
    if not value:
        return []

    if isinstance(value, list):
        values = value
    else:
        try:
            values = json.loads(str(value))
        except json.JSONDecodeError:
            values = []

    if not isinstance(values, list):
        return []

    result: list[str] = []

    for item in values:
        text = clean_text(item)

        if (
            text
            and text not in result
        ):
            result.append(text)

    return result


def parse_json_dict(value: Any) -> dict[str, list[str]]:
    if not value:
        return {}

    if isinstance(value, dict):
        raw = value
    else:
        try:
            raw = json.loads(str(value))
        except json.JSONDecodeError:
            raw = {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, list[str]] = {}

    for key, sources in raw.items():
        normalized_key = clean_text(key)

        if not normalized_key:
            continue

        if isinstance(sources, list):
            values = sources
        else:
            values = [sources]

        cleaned_sources: list[str] = []

        for source in values:
            source_text = clean_text(source)

            if (
                source_text
                and source_text not in cleaned_sources
            ):
                cleaned_sources.append(source_text)

        result[normalized_key] = cleaned_sources

    return result


def merge_contacts(
    popup_contact: dict[str, str | None],
    bio: str | None,
) -> dict[str, Any]:
    bio_phones = find_all_phones(bio or "")
    bio_emails = find_all_emails(bio or "")

    official_zalo = popup_contact.get("zalo")
    official_email = popup_contact.get("email")

    all_phones: list[str] = []
    phone_sources: dict[str, list[str]] = {}

    if official_zalo:
        all_phones.append(official_zalo)
        phone_sources.setdefault(official_zalo, [])
        phone_sources[official_zalo].append("Zalo")

    for phone in bio_phones:
        if phone not in all_phones:
            all_phones.append(phone)

        phone_sources.setdefault(phone, [])

        if "Bio" not in phone_sources[phone]:
            phone_sources[phone].append("Bio")

    all_emails: list[str] = []
    email_sources: dict[str, list[str]] = {}

    if official_email:
        official_email = official_email.lower()
        all_emails.append(official_email)
        email_sources.setdefault(official_email, [])
        email_sources[official_email].append("Contact")

    for email in bio_emails:
        if email not in all_emails:
            all_emails.append(email)

        email_sources.setdefault(email, [])

        if "Bio" not in email_sources[email]:
            email_sources[email].append("Bio")

    return {
        "official_zalo": official_zalo,
        "official_email": official_email,
        "bio": bio,
        "bio_phones": bio_phones,
        "bio_emails": bio_emails,
        "all_phones": all_phones,
        "all_emails": all_emails,
        "phone_sources": phone_sources,
        "email_sources": email_sources,
    }


def build_contact_note(result: dict[str, Any]) -> str:
    notes: list[str] = []
    official_zalo = result["official_zalo"]

    extra_bio_phones = [
        phone
        for phone in result["bio_phones"]
        if phone != official_zalo
    ]

    if extra_bio_phones:
        notes.append(
            "SĐT khác trong tiểu sử: "
            + ", ".join(extra_bio_phones)
        )

    if result["bio"]:
        notes.append(
            "Tiểu sử: "
            + result["bio"]
        )

    return " | ".join(notes)


def determine_status(result: dict[str, Any]) -> str:
    has_phone = bool(result["all_phones"])
    has_email = bool(result["all_emails"])

    if has_phone and has_email:
        return "FOUND_PHONE_EMAIL"

    if has_phone:
        return "FOUND_PHONE"

    if has_email:
        return "FOUND_EMAIL"

    return "NO_CONTACT"


def format_phone_for_display(value: str | None) -> str:
    normalized = normalize_phone(value)

    if not normalized:
        return clean_text(value) or ""

    digits = re.sub(r"\D", "", normalized)

    if (
        digits.startswith("84")
        and len(digits) == 11
    ):
        local = "0" + digits[2:]

        return (
            f"{local[:4]} "
            f"{local[4:7]} "
            f"{local[7:]}"
        )

    return normalized


def normalize_source_label(source: str) -> str:
    source = source.strip()

    if source.lower() == "contact":
        return "Liên hệ"

    if source.lower() == "zalo":
        return "Zalo"

    if source.lower() == "bio":
        return "Bio"

    return source


def join_sources(sources: list[str]) -> str:
    order = (
        "Zalo",
        "Liên hệ",
        "Bio",
    )

    normalized = [
        normalize_source_label(source)
        for source in sources
        if source
    ]

    result: list[str] = []

    for preferred in order:
        if preferred in normalized:
            result.append(preferred)

    for source in normalized:
        if source not in result:
            result.append(source)

    return " + ".join(result)


def get_phone_sources(
    phone: str,
    source_map: dict[str, list[str]],
    official_zalo: str | None,
    bio_phones: list[str],
) -> list[str]:
    sources: list[str] = []

    for raw_phone, raw_sources in source_map.items():
        if normalize_phone(raw_phone) == phone:
            for source in raw_sources:
                label = normalize_source_label(source)

                if label not in sources:
                    sources.append(label)

    if (
        official_zalo
        and normalize_phone(official_zalo) == phone
        and "Zalo" not in sources
    ):
        sources.append("Zalo")

    if (
        phone in [
            normalize_phone(item)
            for item in bio_phones
        ]
        and "Bio" not in sources
    ):
        sources.append("Bio")

    return sources


def build_display_contact(row: dict[str, Any]) -> dict[str, str]:
    raw_phone_sources = parse_json_dict(
        row.get("phone_sources")
    )

    raw_email_sources = parse_json_dict(
        row.get("email_sources")
    )

    phones = [
        normalize_phone(phone)
        for phone in parse_json_list(
            row.get("phones_all")
        )
    ]

    phones = [
        phone
        for phone in phones
        if phone
    ]

    official_zalo = normalize_phone(
        row.get("zalo")
    )

    bio_phones = [
        normalize_phone(phone)
        for phone in parse_json_list(
            row.get("bio_phones")
        )
    ]

    bio_phones = [
        phone
        for phone in bio_phones
        if phone
    ]

    for phone in (
        [official_zalo]
        + bio_phones
    ):
        if (
            phone
            and phone not in phones
        ):
            phones.append(phone)

    main_phone = ""

    if (
        official_zalo
        and official_zalo in phones
    ):
        main_phone = official_zalo
    elif bio_phones:
        main_phone = bio_phones[0]
    elif phones:
        main_phone = phones[0]

    other_phones = [
        phone
        for phone in phones
        if phone != main_phone
    ]

    phone_sources = (
        get_phone_sources(
            main_phone,
            raw_phone_sources,
            official_zalo,
            bio_phones,
        )
        if main_phone
        else []
    )

    emails = parse_json_list(
        row.get("emails_all")
    )

    official_email = clean_text(
        row.get("official_email")
    )

    bio_emails = parse_json_list(
        row.get("bio_emails")
    )

    for email in (
        [official_email]
        + bio_emails
    ):
        if not email:
            continue

        email = email.lower()

        if email not in emails:
            emails.append(email)

    emails = [
        email.lower()
        for email in emails
        if email
    ]

    main_email = ""

    if (
        official_email
        and official_email.lower() in emails
    ):
        main_email = official_email.lower()
    elif emails:
        main_email = emails[0]

    email_sources = []

    if main_email:
        for raw_email, raw_sources in raw_email_sources.items():
            if raw_email.lower() == main_email:
                for source in raw_sources:
                    label = normalize_source_label(source)

                    if label not in email_sources:
                        email_sources.append(label)

        if (
            official_email
            and official_email.lower() == main_email
            and "Liên hệ" not in email_sources
        ):
            email_sources.append("Liên hệ")

        if (
            main_email in [
                email.lower()
                for email in bio_emails
            ]
            and "Bio" not in email_sources
        ):
            email_sources.append("Bio")

    return {
        "main_phone": format_phone_for_display(main_phone),
        "phone_source": join_sources(phone_sources),
        "other_phones": ", ".join(
            format_phone_for_display(phone)
            for phone in other_phones
        ),
        "email": main_email,
        "email_source": join_sources(email_sources),
    }


def row_has_phone(row: dict[str, Any]) -> bool:
    return bool(
        build_display_contact(row)["main_phone"]
    )


def row_has_email(row: dict[str, Any]) -> bool:
    return bool(
        build_display_contact(row)["email"]
    )


def row_search_text(row: dict[str, Any]) -> str:
    contact = build_display_contact(row)

    parts = [
        row.get("creator_id"),
        row.get("username"),
        row.get("nickname"),
        row.get("zalo"),
        row.get("official_email"),
        row.get("bio"),
        row.get("bio_phones"),
        row.get("bio_emails"),
        row.get("phones_all"),
        row.get("emails_all"),
        contact["main_phone"],
        contact["other_phones"],
        contact["email"],
    ]

    phone_parts = [
        row.get("zalo"),
        row.get("bio_phones"),
        row.get("phones_all"),
        contact["main_phone"],
        contact["other_phones"],
    ]

    for value in phone_parts:
        if not value:
            continue

        digits = re.sub(
            r"\D",
            "",
            str(value),
        )

        if digits:
            parts.append(digits)

            if (
                digits.startswith("84")
                and len(digits) >= 11
            ):
                parts.append("0" + digits[2:])

    return " ".join(
        str(part).lower()
        for part in parts
        if part
    )


def filter_creator_rows(
    rows: list[dict[str, Any]],
    search: str = "",
    contact_filter: str = "all",
    status_filter: str = "all",
    phone_source_filter: str = "all",
) -> list[dict[str, Any]]:
    search = search.strip().lower()

    result: list[dict[str, Any]] = []

    for row in rows:
        contact = build_display_contact(row)
        has_phone = bool(contact["main_phone"])
        has_email = bool(contact["email"])
        status = row.get("status") or ""

        if (
            search
            and search not in row_search_text(row)
        ):
            continue

        if (
            status_filter != "all"
            and status != status_filter
        ):
            continue

        if (
            contact_filter == "has_phone"
            and not has_phone
        ):
            continue

        if (
            contact_filter == "has_email"
            and not has_email
        ):
            continue

        if (
            contact_filter == "has_phone_email"
            and not (
                has_phone
                and has_email
            )
        ):
            continue

        if (
            contact_filter == "no_contact"
            and (
                has_phone
                or has_email
            )
        ):
            continue

        if phone_source_filter != "all":
            source = contact["phone_source"]

            if (
                phone_source_filter == "zalo"
                and "Zalo" not in source
            ):
                continue

            if (
                phone_source_filter == "bio"
                and "Bio" not in source
            ):
                continue

            if (
                phone_source_filter == "zalo_bio"
                and source != "Zalo + Bio"
            ):
                continue

        result.append(row)

    return result
