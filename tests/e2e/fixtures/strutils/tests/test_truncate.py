import pytest

from strutils import truncate


def test_short_text_unchanged():
    assert truncate("abc", 5) == "abc"


def test_long_text_truncated():
    assert truncate("abcdefgh", 6) == "abc..."


def test_width_must_fit_suffix():
    with pytest.raises(ValueError):
        truncate("abc", 2)
