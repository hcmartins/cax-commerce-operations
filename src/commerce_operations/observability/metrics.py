import threading
from collections import defaultdict


class MetricsRegistry:
    """Dependency-free in-process metrics suitable for Prometheus scraping."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)

    def increment(self, name: str, value: float = 1, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += value

    def render(self) -> str:
        with self._lock:
            values = tuple(self._counters.items())
        lines = []
        for (name, labels), value in sorted(values):
            suffix = ""
            if labels:
                suffix = "{" + ",".join(f'{key}="{val}"' for key, val in labels) + "}"
            lines.append(f"{name}{suffix} {value:g}")
        return "\n".join(lines) + "\n"


metrics = MetricsRegistry()
