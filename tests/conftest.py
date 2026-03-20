"""Shared test fixtures for AxenWP."""

import os
import sys

# Ensure the project root is on sys.path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Override DATABASE_URL before anything imports settings, so no real DB is needed
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
