"""Pytest configuration for rugmunchbot tests."""

import sys

# Ensure src is on path for all tests
sys.path.insert(0, "/srv/work/repos/rugmunchbot/src")
sys.path.insert(0, "/srv/work/repos/rugmunchbot")

# Load env for tests
from dotenv import load_dotenv

load_dotenv("/srv/secrets/rugmunchbot.env")
