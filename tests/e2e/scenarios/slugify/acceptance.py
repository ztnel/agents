"""Hidden acceptance test: run against the pushed commit, never shown to the agent."""
import inspect
from pathlib import Path

import pytest
from strutils import slugify


@pytest.mark.parametrize("text, slug", [
    ("Hello World", "hello-world"),
    ("Crème brûlée! Déjà vu", "creme-brulee-deja-vu"),
    ("  Hello,,, world!!  ", "hello-world"),
    ("--Edge--", "edge"),
])
def test_agreed_behaviour(text, slug):
    assert slugify(text) == slug


def test_agreed_separator():
    assert slugify("Hello, world!", separator="_") == "hello_world"
    assert inspect.signature(slugify).parameters["separator"].kind is inspect.Parameter.KEYWORD_ONLY


def test_queued_review_request_landed():
    tests = [p for p in Path(__file__).parent.glob("test_*.py")]
    assert any("separator=" in p.read_text() for p in tests), "no committed test covers a custom separator"
