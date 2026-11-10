"""Small string helpers."""


def truncate(text: str, width: int, suffix: str = "...") -> str:
    """Shorten *text* to at most *width* characters, ending in *suffix*."""
    if width < len(suffix):
        raise ValueError("width must fit the suffix")
    if len(text) <= width:
        return text
    return text[: width - len(suffix)] + suffix
