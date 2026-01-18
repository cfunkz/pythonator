"""
Configuration and data models for Pythonator.

Single source of truth for paths, tuning constants, and bot configuration.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

# =============================================================================
# Paths - Use script directory for portability
# =============================================================================

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "bots.json"
LOGS_DIR = APP_DIR / "logs"

# =============================================================================
# Performance Tuning
# =============================================================================

MAX_LOG_LINES = 50_000          # Ring buffer capacity
MAX_CONSOLE_LINES = 0           # 0 = unlimited visual lines
FLUSH_INTERVAL_MS = 100         # Log refresh rate (100ms = 10fps)
STATS_INTERVAL_MS = 1000        # CPU/RAM polling (1Hz)
HISTORY_CHUNK = 5_000           # Lines per "Load Older" click
KILL_TIMEOUT_MS = 500           # Grace period before SIGKILL
MAX_FLUSH_CHARS = 50_000        # Limit chars per flush to prevent freeze

# =============================================================================
# ANSI Handling
# =============================================================================

ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return ANSI_ESCAPE_PATTERN.sub('', text)


def normalize_text(text: str) -> str:
    """Normalize line endings and strip null bytes."""
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")

# =============================================================================
# Data Model
# =============================================================================

@dataclass
class Bot:
    """Bot configuration with optional custom Python interpreter."""
    name: str
    entry: str = ""
    reqs: str = ""
    flags: str = ""
    custom_cmd: bool = False
    python_path: str = ""


def load_config() -> dict[str, Bot]:
    """Load bots from JSON. Returns empty dict on error."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        defaults = {"custom_cmd": False, "python_path": ""}
        return {name: Bot(**{**defaults, **cfg}) for name, cfg in data.items()}
    except (json.JSONDecodeError, TypeError, KeyError):
        return {}


def save_config(bots: dict[str, Bot]) -> None:
    """Persist bots to JSON."""
    try:
        CONFIG_FILE.write_text(
            json.dumps({n: asdict(b) for n, b in bots.items()}, indent=2),
            encoding="utf-8"
        )
    except OSError:
        pass
