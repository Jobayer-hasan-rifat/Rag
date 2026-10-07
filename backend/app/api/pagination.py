from typing import Annotated

from fastapi import Query

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

PageParam = Annotated[int, Query(ge=1, le=100_000, description="1-based page number")]
PageSizeParam = Annotated[
    int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"Items per page (max {MAX_PAGE_SIZE})")
]
SearchParam = Annotated[str | None, Query(min_length=1, max_length=100)]
