"""Turns a user query into the inputs of lexical search.

Words are tokenised by PostgreSQL itself (its parser, with the `simple` configuration), because
Python's notion of a word would split Bengali words at vowel signs. This module only separates
*quoted phrases* from the rest and bounds their number and size; it never rewrites the text.
"""

from dataclasses import dataclass

_QUOTES = {'"': '"', "\u201c": "\u201d", "\u201e": "\u201d", "\u00ab": "\u00bb"}
MAX_PHRASES = 5
MAX_PHRASE_CHARS = 200


@dataclass(frozen=True)
class LexicalQuery:
    words: str  # everything outside quotes, for PostgreSQL to tokenise
    phrases: tuple[str, ...]  # quoted segments, matched as adjacent words

    @property
    def searchable(self) -> bool:
        """False when there is nothing a lexical index could match (empty, punctuation only)."""
        return _has_content(self.words) or any(_has_content(p) for p in self.phrases)


def _has_content(text: str) -> bool:
    return any(ch.isalnum() or "\u0980" <= ch <= "\u09ff" for ch in text)


def parse_lexical_query(query: str) -> LexicalQuery:
    """Split `query` into free words and quoted phrases.

    `ইলিশ "ধরা নিষিদ্ধ" কবে` becomes words `ইলিশ  কবে` and the phrase `ধরা নিষিদ্ধ`. An unmatched
    quote is treated as ordinary text. At most `MAX_PHRASES` phrases of `MAX_PHRASE_CHARS`
    characters are kept; the rest fall back to free words.
    """
    words: list[str] = []
    phrases: list[str] = []
    closing = ""
    buffer: list[str] = []
    for char in query:
        if closing:
            if char == closing:
                phrase = "".join(buffer).strip()
                if phrase and len(phrases) < MAX_PHRASES and len(phrase) <= MAX_PHRASE_CHARS:
                    phrases.append(phrase)
                else:
                    words.append(phrase)
                buffer, closing = [], ""
            else:
                buffer.append(char)
        elif char in _QUOTES:
            words.append("".join(buffer))
            buffer, closing = [], _QUOTES[char]
        else:
            buffer.append(char)
    if closing:  # unmatched quote: keep what followed it as plain words
        words.append("".join(buffer))
        buffer = []
    words.append("".join(buffer))
    return LexicalQuery(
        words=" ".join(part.strip() for part in words if part.strip()), phrases=tuple(phrases)
    )
