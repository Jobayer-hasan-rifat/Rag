import unicodedata

import pytest

from app.core.documents.normalization import normalize_text
from app.core.documents.scripts import profile_scripts

BANGLA = "বাংলাদেশের রাজধানী ঢাকা। সংখ্যা ১২৩ এবং ‘উদ্ধৃতি’ — দাঁড়ি।"


def test_bangla_text_survives_unchanged() -> None:
    assert normalize_text(BANGLA) == BANGLA


def test_mixed_bangla_and_english_is_preserved_with_case_and_punctuation() -> None:
    text = 'Dhaka (ঢাকা) is the Capital, রাজধানী! "Quotes" & symbols: 100% #1.'

    assert normalize_text(text) == text


@pytest.mark.parametrize("char", ["‌", "‍"])
def test_zero_width_joiners_are_preserved_because_bengali_uses_them(char: str) -> None:
    text = f"র{char}্য ক{char}"

    assert normalize_text(text) == text


def test_nfc_composes_bengali_vowel_signs_and_is_idempotent() -> None:
    decomposed = "ো".join(["ক", ""])  # kâ + e-sign + aa-sign -> o-sign
    composed = normalize_text(decomposed)

    assert composed == "কো"
    assert normalize_text(composed) == composed


def test_nfc_is_applied_to_latin_text_too() -> None:
    assert normalize_text("Café") == "Café"


def test_precomposed_nukta_letters_become_their_canonical_decomposition() -> None:
    # U+09DF is a composition exclusion: NFC stores it as U+09AF U+09BC. Visually identical.
    assert normalize_text("য়") == unicodedata.normalize("NFC", "য়") == "য়"


def test_does_not_use_compatibility_normalisation() -> None:
    assert normalize_text("ﬁnal ① ｆｕｌｌ") == "ﬁnal ① ｆｕｌｌ"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\r\nb\rc", "a\nb\nc"),
        ("a b", "a\nb"),
        ("a b", "a\n\nb"),
        ("a\x0cb", "a\nb"),
    ],
)
def test_line_endings_are_normalised(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_repeated_blank_lines_collapse_to_one_and_edges_are_trimmed() -> None:
    assert normalize_text("\n\n\nfirst\n\n\n\n\nsecond\n\n\n") == "first\n\nsecond"


def test_inline_whitespace_is_collapsed_by_default() -> None:
    assert normalize_text("a    b\t\tc   \n   d   ") == "a b c\nd"


def test_inline_spacing_can_be_preserved_for_text_and_markdown() -> None:
    text = "def f():\n    return 1   # indented   code  \n"

    assert (
        normalize_text(text, collapse_inline_spaces=False)
        == "def f():\n    return 1   # indented   code"
    )


@pytest.mark.parametrize("space", [" ", " ", "　", " "])
def test_unicode_spaces_become_plain_spaces(space: str) -> None:
    assert normalize_text(f"a{space}{space}b") == "a b"


@pytest.mark.parametrize("artefact", ["\x00", "\x01", "\x7f", "­", "​", "﻿", "‮", "", "⁦"])
def test_control_and_invisible_artefacts_are_removed(artefact: str) -> None:
    assert normalize_text(f"te{artefact}xt") == "text"


def test_emoji_and_non_bmp_text_is_preserved() -> None:
    assert normalize_text("ok 😀 𝒜") == "ok 😀 𝒜"


def test_other_scripts_are_preserved() -> None:
    text = "日本語 العربية हिन्दी Ελληνικά Кириллица"

    assert normalize_text(text) == text


def test_empty_and_whitespace_only_input_become_empty() -> None:
    assert normalize_text("") == ""
    assert normalize_text("  \n\t \n ") == ""


def test_normalisation_is_idempotent() -> None:
    messy = "  ক‍্ষ   \r\n\r\n\r\n x y   z "

    once = normalize_text(messy)

    assert normalize_text(once) == once


# --- script profile ---------------------------------------------------------------------


def test_bangla_text_profiles_as_bengali() -> None:
    profile = profile_scripts(BANGLA * 3)

    assert profile.primary == "bengali"
    assert profile.bengali > 0.9 and profile.latin == 0.0


def test_english_text_profiles_as_latin() -> None:
    assert profile_scripts("The quick brown fox jumps over the lazy dog.").primary == "latin"


def test_mixed_text_profiles_as_mixed() -> None:
    profile = profile_scripts("Dhaka is the capital of Bangladesh. " + BANGLA * 2)

    assert profile.primary == "mixed"
    assert 0.2 < profile.bengali < 0.9 and 0.1 < profile.latin < 0.9


def test_tiny_or_symbol_only_text_is_unknown() -> None:
    assert profile_scripts("12345 ...").primary == "unknown"
    assert profile_scripts("hi").primary == "unknown"


def test_other_scripts_are_reported_as_other() -> None:
    assert profile_scripts("日本語のテキストです。これは日本語の文章です。").primary == "other"


def test_profile_accepts_multiple_parts_and_serialises() -> None:
    profile = profile_scripts([BANGLA, "English words go here for the profile"])

    data = profile.as_dict()
    assert set(data) == {"primary_script", "scripts"}
    assert set(data["scripts"]) == {"bengali", "latin", "other"}  # type: ignore[call-overload]
