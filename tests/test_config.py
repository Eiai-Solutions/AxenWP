"""Tests for utils/config.py — Settings defaults and behavior."""

from utils.config import Settings


class TestSettings:
    """Verify default values load correctly without .env."""

    def test_default_database_url(self):
        s = Settings(database_url="sqlite:///./test.db")
        assert "sqlite" in s.database_url

    def test_default_host(self):
        s = Settings()
        assert s.host == "0.0.0.0"

    def test_default_port(self):
        s = Settings()
        assert s.port == 8000

    def test_debug_defaults_false(self):
        s = Settings()
        assert s.debug is False

    def test_default_log_level(self):
        s = Settings()
        assert s.log_level == "INFO"
