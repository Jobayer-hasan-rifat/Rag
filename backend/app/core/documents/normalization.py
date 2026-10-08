"""Conservative text normalisation for extracted document text.

Designed to be safe for Bangla and other non-Latin scripts: it never lowercases, strips
punctuation, removes combining marks, or touches ZWJ/ZWNJ (U+200D/U+200C), which are
meaningful in Bengali conjunct forms (for example the ra-phala and ya-phala).
"""

import re
import unicodedata

# Meaningful in Indic scripts (Bengali conjunct forms): keep.
_KEEP_FORMAT = {"\u200c", "\u200d"}  # ZWNJ, ZWJ
# Invisible/format characters that are extraction or copy-paste artefacts: remove.
_STRIP_FORMAT = {
    "\u00ad",  # soft hyphen
    "\u200b",  # zero width space
    "\u2060",  # word joiner
    "\ufeff",  # BOM / zero width no-break space
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",  # bidi embeddings and overrides
    "\u2066", "\u2067", "\u2068", "\u2069",  # bidi isolates
}  # fmt: skip
_LINE_BREAKS = {
    "\u2028": "\n",  # line separator
    "\u2029": "\n\n",  # paragraph separator
    "\x0b": "\n",  # vertical tab
    "\x0c": "\n",  # form feed
    "\x85": "\n",  # next line
}
_INLINE_SPACE = re.compile(r"[ \t]+")
_BLANK_RUNS = re.compile(r"\n{3,}")


def _clean_characters(text: str) -> str:
    out: list[str] = []
    for char in text:
        if char in _LINE_BREAKS:
            out.append(_LINE_BREAKS[char])
        elif char in ("\n", "\t") or char in _KEEP_FORMAT:
            out.append(char)
        elif char in _STRIP_FORMAT:
            continue
        else:
            category = unicodedata.category(char)
            if category == "Zs":
                out.append(" ")  # NBSP, thin space, ideographic space ...
            elif category in ("Cc", "Cf", "Cs", "Co", "Cn"):
                continue  # control, other format, surrogate, private-use, unassigned
            else:
                out.append(char)
    return "".join(out)


def normalize_text(text: str, *, collapse_inline_spaces: bool = True) -> str:
    """Normalise one block of extracted text.

    `collapse_inline_spaces=False` keeps interior spacing (Markdown/plain text, where
    indentation and alignment can carry meaning).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    text = _clean_characters(text)
    lines = []
    for line in text.split("\n"):
        if collapse_inline_spaces:
            line = _INLINE_SPACE.sub(" ", line).strip(" ")
        else:
            line = line.rstrip(" \t")
        lines.append(line)
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip("\n")
