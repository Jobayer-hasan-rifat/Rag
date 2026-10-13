"""Titles and request fingerprints for conversations (pure functions)."""

import hashlib
import json
import unicodedata
from typing import Any

from app.exceptions import RequestValidationFailedError
from app.models.conversation import MAX_TITLE_CHARS

DEFAULT_TITLE = "New conversation"
DEFAULT_TITLE_CHARS = 60
# Joiners that Bengali needs (conjuncts, ZWNJ) are kept; every other control or format character
# (newlines, bidirectional overrides, zero-width spaces, BOM) is removed or turned into a space.
_KEEP_FORMAT = {"‌", "‍"}


def clean_title(raw: str) -> str:
    """NFC-normalised, single-line, printable text without control or format characters."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", raw):
        category = unicodedata.category(ch)
        if category in ("Zl", "Zp") or ch in "\t\r\n":
            out.append(" ")
        elif (
            category == "Cc"
            or (category == "Cf" and ch not in _KEEP_FORMAT)
            or category
            in (
                "Cs",
                "Co",
                "Cn",
            )
        ):
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def explicit_title(raw: str) -> str:
    title = clean_title(raw)
    if not title:
        raise RequestValidationFailedError("The title must contain some text")
    if len(title) > MAX_TITLE_CHARS:
        raise RequestValidationFailedError(f"The title is longer than {MAX_TITLE_CHARS} characters")
    return title


def default_title(question: str) -> str:
    """A title from the first question by truncation: at a word boundary when one is near."""
    title = clean_title(question) or DEFAULT_TITLE
    if len(title) <= DEFAULT_TITLE_CHARS:
        return title
    cut = title[:DEFAULT_TITLE_CHARS]
    space = cut.rfind(" ")
    return (cut[:space] if space >= DEFAULT_TITLE_CHARS // 2 else cut).rstrip() + "…"


def request_fingerprint(content: str, scope: dict[str, Any]) -> str:
    """Identifies what an idempotency key stands for: the question and its filters."""
    canonical = json.dumps({"q": content, "scope": scope}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
