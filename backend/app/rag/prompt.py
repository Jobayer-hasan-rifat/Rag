"""Prompt construction: trusted instructions in one message, untrusted data in the other.

The system prompt is a versioned file (`prompts/answer_v1.txt`), never built from request data.
The user message carries the question and the sources. Source text is XML-escaped so it cannot
close a tag or open a new one, and anything that looks like a citation marker inside a source is
neutralised so document text cannot forge a label. None of this makes prompt injection impossible:
the deterministic checks in `grounding.py` are what keep an obeyed instruction from becoming an
unsupported claim, and they cannot stop a model from misreading a source.
"""

import hashlib
import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.rag.context import ContextBlock
from app.rag.rendering import neutralise_labels
from app.rag.rendering import render_source as render_block

__all__ = [
    "ANSWER_SCHEMA",
    "PROMPT_VERSION",
    "PROMPT_VERSION_HISTORY",
    "build_user_message",
    "load_system_prompt",
    "neutralise_labels",
    "prompt_fingerprint",
    "render_source",
]
PROMPT_VERSION = "answer_v1"
PROMPT_VERSION_HISTORY = "answer_v2"  # the same rules plus the conversation-history rule
_PROMPT_DIR = Path(__file__).parent / "prompts"

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answerable", "answer", "citations"],
    "additionalProperties": False,
}


@lru_cache(maxsize=4)
def load_system_prompt(version: str = PROMPT_VERSION) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", version):  # the version names a file: no paths
        raise ValueError("invalid prompt version")
    return (_PROMPT_DIR / f"{version}.txt").read_text(encoding="utf-8").strip()


def prompt_fingerprint(version: str = PROMPT_VERSION) -> str:
    return hashlib.sha256(load_system_prompt(version).encode("utf-8")).hexdigest()[:12]


def render_source(block: ContextBlock) -> str:
    return render_block(block.label, block.record, block.text)


def build_user_message(
    question: str, blocks: list[ContextBlock], history_block: str | None = None
) -> str:
    sources = "\n".join(render_source(block) for block in blocks)
    history = f"{history_block}\n\n" if history_block else ""
    return (
        f"{history}<question>\n{html.escape(question, quote=False)}\n</question>\n\n"
        f"<sources>\n{sources}\n</sources>\n\n"
        "Answer the question using only the sources above."
    )
