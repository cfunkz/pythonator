"""
Log buffer - In-memory ring buffer with file persistence.

Design:
- Timestamps are added when lines complete (newline received)
- Partial lines are buffered until complete
- File stores PLAIN TEXT (no ANSI codes) for external readability
- ANSI codes are only added when rendering in the app
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import (
    LOGS_DIR, MAX_LOG_LINES, HISTORY_CHUNK,
    normalize_text, strip_ansi
)


class LogBuffer:
    """Ring buffer with timestamping and file persistence."""
    __slots__ = ("name", "lines", "file", "_cache", "_mtime", "_partial")

    def __init__(self, name: str):
        self.name = name
        self.lines: deque[str] = deque(maxlen=MAX_LOG_LINES)
        LOGS_DIR.mkdir(exist_ok=True)
        self.file = LOGS_DIR / f"{name}.log"
        self._cache: Optional[list[str]] = None
        self._mtime: float = 0
        self._partial: str = ""

    def append(self, text: str) -> tuple[str, str]:
        """
        Append raw output.
        
        Returns:
            (display_text, file_text): Text with ANSI for display, plain for file
        """
        if not text:
            return "", ""

        text = normalize_text(text)
        data = self._partial + text
        self._partial = ""

        # Buffer incomplete lines until newline arrives
        if "\n" not in data:
            self._partial = data
            return "", ""

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = data.splitlines(keepends=True)

        # Keep trailing partial for next append
        if parts and not parts[-1].endswith("\n"):
            self._partial = parts.pop()

        display_lines = []
        file_lines = []
        
        for part in parts:
            content = part.rstrip("\n")
            plain_content = strip_ansi(content)
            
            # Display version with colored timestamp
            display_line = f"[\x1b[94m{ts}\x1b[0m] {content}\n"
            # File version without ANSI codes
            file_line = f"[{ts}] {plain_content}\n"
            
            self.lines.append(display_line)
            display_lines.append(display_line)
            file_lines.append(file_line)

        display_result = "".join(display_lines)
        file_result = "".join(file_lines)
        
        self._cache = None

        # Persist plain text to file
        try:
            with open(self.file, "a", encoding="utf-8", newline="\n") as f:
                f.write(file_result)
        except OSError:
            pass

        return display_result, file_result

    def get_recent(self) -> str:
        """Get buffered content (recent lines with ANSI for display)."""
        return "".join(self.lines)

    def _read_file(self) -> list[str]:
        """Read file with mtime caching."""
        if not self.file.exists():
            return [line.rstrip("\n") for line in self.lines]
        try:
            mtime = self.file.stat().st_mtime
            if self._cache is not None and mtime == self._mtime:
                return self._cache
            content = self.file.read_text(encoding="utf-8", errors="replace")
            self._cache = normalize_text(content).splitlines()
            self._mtime = mtime
            return self._cache
        except OSError:
            return [line.rstrip("\n") for line in self.lines]

    def line_count(self) -> int:
        """Total lines in file."""
        return len(self._read_file())

    def search(self, pattern: str) -> tuple[str, int]:
        """Case-insensitive search. Returns (text, count)."""
        p = pattern.lower()
        matches = [l for l in self._read_file() if p in l.lower()]
        if not matches:
            return "", 0
        # Add color to timestamps when displaying search results
        result = []
        for line in matches:
            # Colorize timestamp in search results
            if line.startswith("["):
                bracket_end = line.find("]")
                if bracket_end > 0:
                    ts = line[1:bracket_end]
                    rest = line[bracket_end+1:]
                    line = f"[\x1b[94m{ts}\x1b[0m]{rest}"
            result.append(f"{line}\n")
        return "".join(result), len(matches)

    def load_chunk(self, end: int, size: int = HISTORY_CHUNK) -> tuple[str, int]:
        """Load history chunk ending at `end`. Returns (text, start_line)."""
        lines = self._read_file()
        if not lines or end <= 0:
            return "", 0
        start = max(0, end - size)
        chunk = lines[start:end]
        if not chunk:
            return "", 0
        
        # Colorize timestamps for display
        result = []
        for line in chunk:
            if line.startswith("["):
                bracket_end = line.find("]")
                if bracket_end > 0:
                    ts = line[1:bracket_end]
                    rest = line[bracket_end+1:]
                    line = f"[\x1b[94m{ts}\x1b[0m]{rest}"
            result.append(f"{line}\n")
        return "".join(result), start

    def clear(self) -> None:
        """Clear buffer and file."""
        self.lines.clear()
        self._cache = None
        try:
            self.file.write_text("", encoding="utf-8")
        except OSError:
            pass
