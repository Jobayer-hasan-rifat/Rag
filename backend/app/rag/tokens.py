"""A conservative token estimate for budgeting prompts.

The generation model's tokenizer is not available to the application (providers are remote or
separate processes), so budgets use an estimate that errs on the high side: ASCII text is about
three characters per token, while every non-ASCII character (Bengali, other scripts, emoji) is
counted as one token, which is what most tokenizers approach for scripts they split into bytes or
characters. The provider's reported usage is recorded next to the estimate so the estimate can be
checked against reality; a prompt that fills the window is rejected (see RagService). It is never
used as a billing figure.
"""

import math

CHARS_PER_TOKEN_ASCII = 3.0
BENGALI_START, BENGALI_END = "\u0980", "\u09ff"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    other = sum(1 for ch in text if not ch.isascii())
    return math.ceil(other + (len(text) - other) / CHARS_PER_TOKEN_ASCII)


def bengali_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if BENGALI_START <= ch <= BENGALI_END) / len(letters)
