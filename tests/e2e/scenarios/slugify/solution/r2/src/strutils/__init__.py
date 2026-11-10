"""Small string helpers."""

import re
import unicodedata


def truncate(text: str, width: int, suffix: str = "...") -> str:
    """Shorten *text* to at most *width* characters, ending in *suffix*."""
    if width < len(suffix):
        raise ValueError("width must fit the suffix")
    if len(text) <= width:
        return text
    return text[: width - len(suffix)] + suffix


def slugify(text: str, *, separator: str = "-") -> str:
    """Convert *text* to a lowercase ASCII slug joined by *separator*."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug.replace("-", separator)
