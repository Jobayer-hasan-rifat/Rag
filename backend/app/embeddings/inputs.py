from collections.abc import Sequence

HEADING_SEPARATOR = " > "


def passage_input(heading_path: Sequence[str], text: str, *, include_heading: bool) -> str:
    """The text a chunk is embedded as (before the model's passage prefix is added).

    With `include_heading` the enclosing headings give the passage its topic ("Dengue > Symptoms"
    then the text); the stored chunk text itself is never changed.
    """
    if include_heading and heading_path:
        return f"{HEADING_SEPARATOR.join(heading_path)}\n{text}"
    return text
