"""
Stats Monitor - CPU/RAM monitoring for process trees.

Uses psutil to monitor parent + all children.
CPU% computed from cumulative time deltas (more accurate than instant sampling).
Tree lookups cached with TTL to reduce overhead.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore
    HAS_PSUTIL = False


@dataclass(slots=True)
class ProcessStats:
    """Process statistics."""
    cpu_percent: float = 0.0
    ram_mb: float = 0.0
    running: bool = False

    def __str__(self) -> str:
        if not self.running:
            return "Stopped"
        return f"CPU: {self.cpu_percent:5.1f}%  RAM: {self.ram_mb:6.1f} MB"


class StatsMonitor:
    """Monitor CPU/RAM for process trees with caching."""
    
    __slots__ = ("_tree_ttl", "_tree_cache", "_cpu_baseline", "_num_cpus")

    def __init__(self, tree_ttl: float = 2.0):
        self._tree_ttl = tree_ttl
        self._tree_cache: dict[int, tuple[float, list[Any]]] = {}
        self._cpu_baseline: dict[int, tuple[float, float]] = {}
        self._num_cpus = psutil.cpu_count() or 1 if HAS_PSUTIL else 1

    def get_stats(self, pid: int) -> ProcessStats:
        """Get stats for a process tree."""
        if not HAS_PSUTIL or pid <= 0:
            return ProcessStats()

        procs = self._get_tree(pid)
        if not procs:
            return ProcessStats()

        now = time.monotonic()
        ram_mb = sum(self._safe_rss(p) for p in procs) / (1024 * 1024)
        cpu_sec = sum(self._safe_cpu(p) for p in procs)

        # CPU% from time delta
        cpu_pct = 0.0
        if pid in self._cpu_baseline:
            t0, cpu0 = self._cpu_baseline[pid]
            dt = max(1e-6, now - t0)
            cpu_pct = max(0.0, cpu_sec - cpu0) / (dt * self._num_cpus) * 100.0
        self._cpu_baseline[pid] = (now, cpu_sec)

        return ProcessStats(cpu_percent=cpu_pct, ram_mb=ram_mb, running=True)

    def clear(self, pid: int) -> None:
        """Clear cached data for a process."""
        self._tree_cache.pop(pid, None)
        self._cpu_baseline.pop(pid, None)

    def _get_tree(self, pid: int) -> list[Any]:
        """Get process tree with TTL caching."""
        now = time.monotonic()
        cached = self._tree_cache.get(pid)
        
        if cached:
            t, procs = cached
            if (now - t) < self._tree_ttl:
                try:
                    for p in procs:
                        p.status()  # Verify still alive
                    return procs
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        try:
            parent = psutil.Process(pid)
            procs = [parent] + parent.children(recursive=True)
            self._tree_cache[pid] = (now, procs)
            return procs
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return []

    @staticmethod
    def _safe_rss(p: Any) -> int:
        """Safely get RSS memory."""
        try:
            return p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0

    @staticmethod
    def _safe_cpu(p: Any) -> float:
        """Safely get CPU time."""
        try:
            t = p.cpu_times()
            return t.user + t.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0
