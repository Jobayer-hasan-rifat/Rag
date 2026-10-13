"""How a source is written into the prompt, kept apart so the token budget measures exactly what
the model will receive (escaping and attributes included)."""

import html
import re

from app.db.repositories.embedding_repository import ChunkRecord

_LABEL_LIKE = re.compile(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_ATTRIBUTE_CHARS = 120


def neutralise_labels(text: str) -> str:
    """Show a marker-like string in a source as `(S1)`, so only the application writes `[S1]`."""
    return _LABEL_LIKE.sub(lambda m: "(" + m.group(0)[1:-1].strip() + ")", text)


def escape_body(text: str) -> str:
    """Source text as the model sees it: no markup, no forged labels."""
    return html.escape(neutralise_labels(text), quote=False)


def _attribute(value: str | None) -> str:
    cleaned = _CONTROL.sub(" ", value or "").strip()[:MAX_ATTRIBUTE_CHARS]
    return html.escape(cleaned, quote=True)


def source_open_tag(label: str, record: ChunkRecord) -> str:
    attributes = [f'id="{label}"', f'document="{_attribute(record.document_name)}"']
    if record.page_number is not None:
        attributes.append(f'page="{record.page_number}"')
    if record.heading:
        attributes.append(f'heading="{_attribute(record.heading)}"')
    return f"<source {' '.join(attributes)}>"


def render_source(label: str, record: ChunkRecord, text: str) -> str:
    return f"{source_open_tag(label, record)}\n{escape_body(text)}\n</source>"
