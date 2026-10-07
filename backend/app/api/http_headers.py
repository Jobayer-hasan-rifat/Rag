from urllib.parse import quote


def attachment_disposition(filename: str) -> str:
    """Content-Disposition for a download, safe against header injection.

    `filename` is already sanitised (no control characters); non-ASCII and quote characters
    are still encoded because header syntax, not just content, must be protected.
    """
    ascii_name = "".join(
        char if 32 <= ord(char) < 127 and char not in r'"\;' else "_" for char in filename
    )
    fallback = ascii_name or "download"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


DOWNLOAD_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
