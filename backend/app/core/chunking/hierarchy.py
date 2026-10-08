class HeadingTracker:
    """Maintains the heading ancestry while walking sections in reading order."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def path_for(self, heading: str | None, level: int | None) -> tuple[str, ...]:
        """Ancestor headings plus the section's own heading (if any), outermost first.

        Sections without a heading (PDF pages, preamble text) inherit the current ancestry.
        """
        if heading and level:
            while self._stack and self._stack[-1][0] >= level:
                self._stack.pop()
            self._stack.append((level, heading))
        return tuple(text for _, text in self._stack)
