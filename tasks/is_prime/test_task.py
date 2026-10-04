import pytest

from solution import is_prime


def test_small_primes():
    for p in [2, 3, 5, 7, 11, 13]:
        assert is_prime(p)


def test_non_positive():
    for n in [-5, 0, 1]:
        assert not is_prime(n)


def test_composites():
    for n in [4, 6, 8, 10, 12]:
        assert not is_prime(n)


def test_odd_values():
    assert is_prime(9)
    assert is_prime(17)


def test_large_prime():
    assert is_prime(7919)
