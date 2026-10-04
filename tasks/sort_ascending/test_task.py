import pytest

from solution import sort_ascending


def test_basic():
    assert sort_ascending([3, 1, 2]) == [1, 2, 3]


def test_empty():
    assert sort_ascending([]) == []


def test_duplicates():
    assert sort_ascending([2, 2, 1]) == [1, 2, 2]


def test_negative():
    assert sort_ascending([5, -1, 0]) == [5, 0, -1]


def test_does_not_mutate():
    xs = [2, 1]
    sort_ascending(xs)
    assert xs == [2, 1]
