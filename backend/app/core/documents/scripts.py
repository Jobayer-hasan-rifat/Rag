"""Dependency-free script profile of a text (Bengali vs Latin vs other).

This is a heuristic about *scripts*, not a language detector: Latin text could be English,
French or anything else. It reliably tells Bangla, Latin and mixed documents apart.
"""

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_BENGALI = (0x0980, 0x09FF)
_LATIN_RANGES = ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F), (0x1E00, 0x1EFF))
MIN_LETTERS = 20
DOMINANT_SHARE = 0.9


@dataclass(frozen=True)
class ScriptProfile:
    bengali: float
    latin: float
    other: float
    primary: str  # bengali | latin | mixed | other | unknown

    def as_dict(self) -> dict[str, object]:
        return {
            "primary_script": self.primary,
            "scripts": {"bengali": self.bengali, "latin": self.latin, "other": self.other},
        }


def profile_scripts(texts: str | Iterable[str]) -> ScriptProfile:
    bengali = latin = other = 0
    parts = [texts] if isinstance(texts, str) else texts
    for char in (char for part in parts for char in part):
        code = ord(char)
        if code < 0x80:  # fast path: ASCII letters are Latin
            latin += char.isalpha()
            continue
        if _BENGALI[0] <= code <= _BENGALI[1]:
            if unicodedata.category(char)[0] in "LM":  # letters and vowel signs/marks
                bengali += 1
        elif any(low <= code <= high for low, high in _LATIN_RANGES):
            latin += 1
        elif char.isalpha():
            other += 1
    total = bengali + latin + other
    if total < MIN_LETTERS:
        return ScriptProfile(0.0, 0.0, 0.0, "unknown")
    shares = {
        "bengali": bengali / total,
        "latin": latin / total,
        "other": other / total,
    }
    primary = next((name for name, share in shares.items() if share >= DOMINANT_SHARE), "mixed")
    return ScriptProfile(
        round(shares["bengali"], 3), round(shares["latin"], 3), round(shares["other"], 3), primary
    )
