import pytest

from solution import list_sum


def test_empty():
    assert list_sum([]) == 0


def test_single():
    assert list_sum([7]) == 7


def test_mixed():
    assert list_sum([12, 7, 30, 5]) == 55


def test_negatives():
    assert list_sum([-3, 3, 10]) == 10


def test_many():
    assert list_sum(list(range(101))) == 5050
