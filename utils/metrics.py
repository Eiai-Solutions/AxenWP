"""
Métricas in-memory ao estilo Prometheus, sem dependência externa.

Cada counter é process-local — em deploy multi-worker (gunicorn -w N) cada
worker tem seu próprio set, então os números são parciais. Suficiente para
visibilidade operacional sem adicionar Redis/Prometheus a curto prazo.

API:
    metrics.inc("ai_calls_total")
    metrics.inc("ai_calls_total", labels={"channel": "whatsapp"})
    metrics.snapshot()  # dict para o /metrics endpoint
"""

import threading
import time
from collections import defaultdict
from typing import Optional


# Lock global pra updates concorrentes (FastAPI async + thread pool)
_lock = threading.Lock()
_counters: dict[tuple, int] = defaultdict(int)
_started_at = time.time()


def _key(name: str, labels: Optional[dict] = None) -> tuple:
    """(name, sorted_labels_tuple). Garante chave estável."""
    if not labels:
        return (name, ())
    return (name, tuple(sorted(labels.items())))


def inc(name: str, labels: Optional[dict] = None, value: int = 1) -> None:
    """Incrementa um counter. Labels viram parte da chave."""
    if value <= 0:
        return
    with _lock:
        _counters[_key(name, labels)] += value


def get(name: str, labels: Optional[dict] = None) -> int:
    """Lê o valor atual de um counter."""
    with _lock:
        return _counters.get(_key(name, labels), 0)


def snapshot() -> dict:
    """Retorna estado completo num formato serializável."""
    with _lock:
        out = {
            "uptime_seconds": int(time.time() - _started_at),
            "counters": [],
        }
        for (name, labels_tuple), count in sorted(_counters.items()):
            entry = {"name": name, "value": count}
            if labels_tuple:
                entry["labels"] = dict(labels_tuple)
            out["counters"].append(entry)
        return out


def prometheus_text() -> str:
    """
    Renderiza no formato exposition do Prometheus (text/plain; version=0.0.4).
    Adequado para scraping pelo Prometheus ou Grafana Agent.
    """
    lines = []
    seen_names = set()
    with _lock:
        for (name, labels_tuple), count in sorted(_counters.items()):
            if name not in seen_names:
                lines.append(f"# TYPE {name} counter")
                seen_names.add(name)
            if labels_tuple:
                label_str = ",".join(f'{k}="{v}"' for k, v in labels_tuple)
                lines.append(f"{name}{{{label_str}}} {count}")
            else:
                lines.append(f"{name} {count}")
    lines.append(f"# TYPE axenwp_uptime_seconds gauge")
    lines.append(f"axenwp_uptime_seconds {int(time.time() - _started_at)}")
    return "\n".join(lines) + "\n"
