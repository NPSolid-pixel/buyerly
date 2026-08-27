"""Canonical, deterministic workspace URL slug rules."""

import re
import unicodedata


MAX_WORKSPACE_SLUG_LENGTH = 60
RESERVED_WORKSPACE_SLUGS = frozenset(
    {
        "api",
        "admin",
        "app",
        "auth",
        "static",
        "uploads",
        "health",
        "docs",
        "redoc",
        "openapi",
        "openapi-json",
        "settings",
        "terms",
        "privacy",
        "data-deletion",
        "onboarding",
        "login",
        "sign-in",
        "dashboard",
        "home",
        "accounts",
        "facebook-accounts",
        "facebook-groups",
        "groups",
        "lists",
        "collection",
        "rule-groups",
        "add-accounts",
        "rules",
        "chats",
        "summary",
        "logs",
        "invite",
        "invites",
        "null",
        "undefined",
    }
)

_CYRILLIC_TRANSLITERATION = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def _stable_fallback_hash(value: str) -> str:
    hash_value = 0x811C9DC5
    for byte in value.encode("utf-8"):
        hash_value ^= byte
        hash_value = (hash_value * 0x01000193) & 0xFFFFFFFF
    return f"{hash_value:08x}"


def normalize_workspace_slug(value: str) -> str:
    """Return the same bounded ASCII slug for the same input on every host."""
    normalized = unicodedata.normalize("NFKC", value or "").strip().lower()
    transliterated = normalized.translate(_CYRILLIC_TRANSLITERATION)
    ascii_text = unicodedata.normalize("NFKD", transliterated).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    slug = slug[:MAX_WORKSPACE_SLUG_LENGTH].rstrip("-")
    if slug:
        return slug
    if normalized:
        return f"workspace-{_stable_fallback_hash(normalized)}"
    return "workspace"


def reservation_safe_workspace_slug(value: str) -> str:
    """Normalize a slug and move system-reserved names into user-safe space."""
    slug = normalize_workspace_slug(value)
    if slug not in RESERVED_WORKSPACE_SLUGS:
        return slug
    suffix = "-workspace"
    return f"{slug[: MAX_WORKSPACE_SLUG_LENGTH - len(suffix)].rstrip('-')}{suffix}"


def numbered_workspace_slug(base: str, number: int) -> str:
    """Append a collision number without exceeding the database field contract."""
    if number < 2:
        return base[:MAX_WORKSPACE_SLUG_LENGTH].rstrip("-")
    suffix = f"-{number}"
    prefix = base[: MAX_WORKSPACE_SLUG_LENGTH - len(suffix)].rstrip("-")
    return f"{prefix}{suffix}"
