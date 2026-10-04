import pytest

from solution import celsius_to_fahrenheit


def test_freezing():
    assert celsius_to_fahrenheit(0) == pytest.approx(32.0)


def test_body_temp():
    assert celsius_to_fahrenheit(37) == pytest.approx(98.6)


def test_boiling():
    assert celsius_to_fahrenheit(100) == pytest.approx(211.0)


def test_negative_forty():
    assert celsius_to_fahrenheit(-40) == pytest.approx(-40.0)


def test_returns_float():
    assert isinstance(celsius_to_fahrenheit(10), float)
