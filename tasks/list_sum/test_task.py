import pytest

from solution import list_sum


def test_empty():
    assert list_sum([]) == 0


def test_single():
    assert list_sum([7]) == 7


def test_pair():
    assert list_sum([1, 2]) == 4


def test_negatives():
    assert list_sum([-3, 3, 10]) == 10


def test_many():
    assert list_sum(list(range(101))) == 5050
