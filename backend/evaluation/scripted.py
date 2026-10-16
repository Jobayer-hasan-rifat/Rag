"""Scripted generation models for the deterministic evaluation profile.

They stand in for a language model so the application's own gates can be measured without one:
what is retrieved, what reaches the prompt, which labels and facts survive validation, which
version a citation names. They are NOT a model of any real model's quality or robustness; a real
model is evaluated separately and opt-in (`--profile models`).

The default script, `best_overlap`, is a faithful extractive reader: it answers with the sentence of
the supplied source that shares the most content words with the question, cites that source, and
says the sources are insufficient when no source shares any. The others reproduce specific
misbehaviours (trusting history, inventing labels or quotations) to check that the application
rejects them.
"""

import html
import re
from collections.abc import Callable

from app.llm.types import GenerationRequest, LLMError
from tests.llm_fakes import (
    RATE_LIMITED,
    TIMEOUT,
    UNAVAILABLE,
    raising,
    reply,
    sources_of,
)

_QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)
_ASSISTANT = re.compile(r"<assistant>(.*?)</assistant>", re.DOTALL)
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")

Script = Callable[[GenerationRequest], str]


def question_of(request: GenerationRequest) -> str:
    match = _QUESTION.search(request.user)
    return html.unescape(match.group(1)) if match else ""


def content_tokens(text: str) -> set[str]:
    """Casefolded whitespace tokens without punctuation; short ASCII words are dropped."""
    tokens = set()
    for raw in text.split():
        token = raw.strip(".,;:!?()[]\"'।").casefold()
        if (token.isascii() and len(token) >= 4) or (not token.isascii() and len(token) >= 3):
            tokens.add(token)
    return tokens


def best_overlap(request: GenerationRequest) -> str:
    wanted = content_tokens(question_of(request))
    best: tuple[int, str, str] | None = None
    for label, body in sources_of(request):
        for sentence in _SENTENCE_END.split(html.unescape(body).strip()):
            score = len(wanted & content_tokens(sentence))
            if score > 0 and (best is None or score > best[0]):
                best = (score, label, sentence.strip())
    if best is None:
        return reply("", [], answerable=False)
    _, label, sentence = best
    return reply(f"{sentence} [{label}]", [label])


def history_echo(request: GenerationRequest) -> str:
    """A model that treats the conversation history as evidence: it repeats the last stored
    answer and cites the first source for it."""
    answers = _ASSISTANT.findall(request.user)
    sources = sources_of(request)
    if not answers or not sources:
        return reply("", [], answerable=False)
    text = html.unescape(answers[-1]).strip()
    label = sources[0][0]
    return reply(f"{re.sub(r'\[S\d+\]', '', text).strip()} [{label}]", [label])


def fabricate_label(request: GenerationRequest) -> str:
    return reply("The answer is stated in the manual [S9].", ["S9"])


def fabricate_quote(request: GenerationRequest) -> str:
    label = sources_of(request)[0][0] if sources_of(request) else "S1"
    return reply(f'The manual says "transplant in March" [{label}].', [label])


SCRIPTS: dict[str, Script] = {
    "best_overlap": best_overlap,
    "abstain_when_unrelated": best_overlap,
    "history_echo": history_echo,
    "fabricate_label": fabricate_label,
    "fabricate_quote": fabricate_quote,
    "timeout": raising(TIMEOUT),
    "unavailable": raising(UNAVAILABLE),
    "rate_limited": raising(RATE_LIMITED),
}
# these simulate a misbehaving or failing provider: meaningless with a real model
SIMULATED_FAULTS = frozenset(
    {"fabricate_label", "fabricate_quote", "timeout", "unavailable", "rate_limited"}
)
PROVIDER_ERRORS: tuple[type[LLMError], ...] = (LLMError,)
