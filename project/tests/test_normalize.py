"""Unit tests for normalization and email candidate builders."""

from vitalseconds.utils.normalize import (
    build_first,
    build_first_last,
    build_flast,
    normalize_company,
    normalize_domain,
    normalize_email,
)


def test_normalize_domain():
    assert normalize_domain("https://www.ExampleEMS.com/path") == "exampleems.com"
    assert normalize_domain("WWW.ABC.COM") == "abc.com"
    assert normalize_domain("  foo.org. ") == "foo.org"
    assert normalize_domain("") == ""
    assert normalize_domain(None) == ""


def test_normalize_email():
    assert normalize_email("  John.Smith@Example.COM ") == "john.smith@example.com"
    assert normalize_email(None) == ""


def test_normalize_company():
    assert normalize_company("Life Star EMS LLC") == "life star ems"
    assert normalize_company("Acme Ambulance, Inc.") == "acme ambulance,"


def test_build_flast_pass1():
    """Pass 1 must be first initial + last (no dot)."""
    assert build_flast("John", "Smith") == "jsmith"
    assert build_flast("Mary", "O'Brien") == "mobrien"
    assert build_flast("", "Smith") == ""
    assert build_flast("John", "") == ""


def test_build_first_pass2():
    assert build_first("John") == "john"
    assert build_first("Mary-Jane") == "maryjane"


def test_build_first_last_pass3():
    assert build_first_last("John", "Smith") == "john.smith"
    assert build_first_last("Mary", "O'Brien") == "mary.obrien"
