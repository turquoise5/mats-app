import pytest

from solution import factorial


def test_one():
    assert factorial(1) == 1


def test_five():
    assert factorial(5) == 120


def test_ten():
    assert factorial(10) == 3628800


def test_base_cases():
    assert factorial(0) == 0
    assert factorial(2) == 2


def test_three():
    assert factorial(3) == 6
