from strutils import slugify


def test_lowercases_and_hyphenates():
    assert slugify("Hello World") == "hello-world"


def test_folds_accents():
    assert slugify("Crème brûlée") == "creme-brulee"


def test_strips_edge_hyphens():
    assert slugify("  --Hi!--  ") == "hi"
