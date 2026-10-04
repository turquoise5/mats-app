import pytest

from solution import dedupe


def test_no_duplicates():
    assert dedupe([1, 2, 3]) == [1, 2, 3]


def test_empty():
    assert dedupe([]) == []


def test_order_preserved():
    assert dedupe(["b", "a", "b"]) == ["b", "a"]


def test_ints():
    assert dedupe([3, 1, 3, 2]) == [1, 2, 3]


def test_all_same():
    assert dedupe([4, 4, 4]) == [4]
