"""Conversation history as an input of the RAG pipeline.

History is untrusted data and never evidence. It does two bounded things:
1. a short follow-up question (a few words) is retrieved together with the previous user question,
   so "and its spacing?" finds the same passages as the question it follows;
2. the model sees a separate, escaped `<conversation_history>` block of recent completed turns, with
   citation markers removed, so it can understand what "it" refers to. The system prompt says the
   block is context for interpreting the question only: facts and citations come from the sources
   retrieved for the current turn.

Selection is by recency under a turn limit and a token budget; each message is cut to a per-message
limit. Nothing here reads the database: the caller passes completed turns.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.context import cut_to_tokens
from app.rag.rendering import escape_body
from app.rag.tokens import estimate_tokens

TURN_OVERHEAD_TOKENS = 12
_MARKERS = re.compile(r"\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]")


@dataclass(frozen=True)
class HistoryTurn:
    """One completed exchange. `answer` is None when no answer was found in the documents."""

    question: str
    answer: str | None


@dataclass(frozen=True)
class HistorySelection:
    turns: list[HistoryTurn]  # oldest first, as they will be shown
    tokens: int
    dropped: int  # older turns that did not fit


def strip_markers(text: str) -> str:
    """Remove `[S1]`-style markers: they referred to sources of an earlier request."""
    return " ".join(_MARKERS.sub("", text).split())


def select_history(
    completed: Sequence[HistoryTurn],
    *,
    max_turns: int,
    budget_tokens: int,
    message_tokens: int,
) -> HistorySelection:
    """The most recent turns that fit. `completed` is oldest first."""
    chosen: list[HistoryTurn] = []
    used = 0
    for turn in reversed(completed):
        if len(chosen) >= max_turns:
            break
        question, _ = cut_to_tokens(turn.question, message_tokens, estimate_tokens)
        answer = None
        if turn.answer is not None:
            answer, _ = cut_to_tokens(strip_markers(turn.answer), message_tokens, estimate_tokens)
        cost = (
            estimate_tokens(escape_body(question))
            + (estimate_tokens(escape_body(answer)) if answer else 12)
            + TURN_OVERHEAD_TOKENS
        )
        if used + cost > budget_tokens:
            break
        chosen.append(HistoryTurn(question, answer))
        used += cost
    chosen.reverse()
    return HistorySelection(chosen, used, len(completed) - len(chosen))


def render_history(turns: Sequence[HistoryTurn]) -> str:
    parts = ["<conversation_history>"]
    for turn in turns:
        answer = (
            escape_body(turn.answer) if turn.answer else "(no answer was found in the documents)"
        )
        parts.append(
            f"<turn>\n<user>{escape_body(turn.question)}</user>\n<assistant>{answer}</assistant>\n</turn>"
        )
    parts.append("</conversation_history>")
    return "\n".join(parts)


def retrieval_query(
    question: str, previous_questions: Sequence[str], *, max_words: int, turns: int, max_chars: int
) -> tuple[str, bool]:
    """The text to retrieve with, and whether it was extended with earlier questions.

    Only a short question (fewer than `max_words` words) is extended, with up to `turns` previous
    user questions (most recent first in the text); only user text is used, never an answer.
    """
    if turns <= 0 or not previous_questions or len(question.split()) >= max_words:
        return question, False
    earlier = " ".join(previous_questions[-turns:])
    room = max_chars - len(question) - 1
    if room <= 0:
        return question, False
    return f"{earlier[:room].rstrip()} {question}", True
