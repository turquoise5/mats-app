import pytest

from solution import count_vowels


def test_hello():
    assert count_vowels("hello") == 2


def test_empty():
    assert count_vowels("") == 0


def test_uppercase():
    assert count_vowels("AEIOU") == 5


def test_words():
    assert count_vowels("banana") == 3
    assert count_vowels("rhythm") == 1


def test_punctuation():
    assert count_vowels("a.b,c!e") == 2
