"""Test that every instrument driver is registered correctly."""

import pytest

from src.instruments import get_driver, list_drivers, register, _eager_register


def test_eager_register_loads_all_drivers():
    _eager_register()
    drivers = set(list_drivers())
    expected = {
        ("megger", "mit525"),
        ("megger", "dlro10x"),
        ("vitrek", "95x"),
        ("qualitrol", "118itm"),
        ("vaisala", "opt100"),
        ("sel", "sel-751"),
        ("caterpillar", "emcp4.4"),
    }
    for k in expected:
        assert k in drivers, f"missing driver {k}"


def test_get_driver_case_insensitive():
    _eager_register()
    assert get_driver("Megger", "MIT525") is not None
    assert get_driver("MEGGER", "mit525") is not None
    assert get_driver("megger", "MIT525") is not None


def test_get_driver_unknown_returns_none():
    _eager_register()
    assert get_driver("Acme", "WidgetMaster") is None


def test_register_decorator_returns_class():
    class FakeDriver:
        VENDOR = "Acme"
        MODEL = "FakeDriver"

    decorated = register(FakeDriver)
    assert decorated is FakeDriver
    assert get_driver("Acme", "FakeDriver") is FakeDriver
