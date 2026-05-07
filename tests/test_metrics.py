"""Tests for utils/metrics.py — in-memory counter primitives."""

import pytest

from utils import metrics


@pytest.fixture(autouse=True)
def reset_metrics():
    """Limpa counters antes de cada teste pra garantir isolamento."""
    with metrics._lock:
        metrics._counters.clear()
    yield


class TestCounters:
    def test_inc_default_value(self):
        metrics.inc("foo")
        assert metrics.get("foo") == 1

    def test_inc_custom_value(self):
        metrics.inc("foo", value=5)
        assert metrics.get("foo") == 5

    def test_inc_zero_or_negative_ignored(self):
        metrics.inc("foo", value=0)
        metrics.inc("foo", value=-3)
        assert metrics.get("foo") == 0

    def test_multiple_inc_accumulates(self):
        for _ in range(10):
            metrics.inc("hits")
        assert metrics.get("hits") == 10

    def test_labels_are_separate_keys(self):
        metrics.inc("requests", labels={"channel": "whatsapp"})
        metrics.inc("requests", labels={"channel": "telegram"})
        metrics.inc("requests", labels={"channel": "whatsapp"})
        assert metrics.get("requests", labels={"channel": "whatsapp"}) == 2
        assert metrics.get("requests", labels={"channel": "telegram"}) == 1

    def test_label_order_does_not_matter(self):
        metrics.inc("e", labels={"a": "1", "b": "2"})
        metrics.inc("e", labels={"b": "2", "a": "1"})
        # Mesmo conjunto de labels deve resolver à mesma chave
        assert metrics.get("e", labels={"a": "1", "b": "2"}) == 2


class TestSnapshot:
    def test_snapshot_includes_uptime(self):
        snap = metrics.snapshot()
        assert "uptime_seconds" in snap
        assert isinstance(snap["uptime_seconds"], int)
        assert snap["uptime_seconds"] >= 0

    def test_snapshot_lists_counters(self):
        metrics.inc("a")
        metrics.inc("b", value=3)
        snap = metrics.snapshot()
        names = {c["name"]: c["value"] for c in snap["counters"]}
        assert names["a"] == 1
        assert names["b"] == 3

    def test_snapshot_includes_labels(self):
        metrics.inc("c", labels={"k": "v"})
        snap = metrics.snapshot()
        entry = next(c for c in snap["counters"] if c["name"] == "c")
        assert entry["labels"] == {"k": "v"}


class TestPrometheusFormat:
    def test_simple_counter(self):
        metrics.inc("simple", value=2)
        text = metrics.prometheus_text()
        assert "# TYPE simple counter" in text
        assert "simple 2" in text

    def test_counter_with_labels(self):
        metrics.inc("labeled", labels={"status": "ok"})
        text = metrics.prometheus_text()
        assert 'labeled{status="ok"} 1' in text

    def test_includes_uptime_gauge(self):
        text = metrics.prometheus_text()
        assert "axenwp_uptime_seconds" in text

    def test_type_line_only_once_per_metric(self):
        metrics.inc("dup", labels={"x": "1"})
        metrics.inc("dup", labels={"x": "2"})
        text = metrics.prometheus_text()
        assert text.count("# TYPE dup counter") == 1
