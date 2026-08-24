"""Flood control tests — anti-abuse protection."""

import pytest

from rugmunchbot.flood_control import FloodController


@pytest.fixture
def controller():
    fc = FloodController.__new__(FloodController)
    fc._redis = None  # no Redis in tests
    return fc


def test_flood_controller_exists(controller):
    assert controller is not None


def test_get_flood_controller_singleton():
    from rugmunchbot.flood_control import get_flood_controller

    fc1 = get_flood_controller()
    fc2 = get_flood_controller()
    assert fc1 is fc2
