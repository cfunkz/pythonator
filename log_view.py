"""
Log View - Per-bot log viewer with live/history/search modes.

State Machine:
- LIVE: Showing live buffer, auto-scrolling
- HISTORY: Browsing older history (no auto-scroll)
- SEARCH: Showing search results
"""
from __future__ import annotations

import os
import subprocess
import sys
from enum import Enum, auto
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget, QFrame, QStyle
)

from console import AnsiConsole
from log_buffer import LogBuffer
from icons import Icon, IconProvider, icon_button
from config import MAX_FLUSH_CHARS

if TYPE_CHECKING:
    from stats import ProcessStats


class ViewMode(Enum):
    LIVE = auto()
    HISTORY = auto()
    SEARCH = auto()


class LogView(QWidget):
    """Log viewer for a single bot."""

    def __init__(self, name: str, buffer: LogBuffer):
        super().__init__()
        self.name = name
        self.buffer = buffer
        self._pending: list[str] = []
        self._mode = ViewMode.LIVE
        self._history_start = self._history_end = 0
        self._setup_ui()
        QTimer.singleShot(0, self._go_live)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Stats bar
        self._setup_stats_bar(layout)
        
        # Search row
        self._setup_search_row(layout)
        
        # Action buttons
        self._setup_action_buttons(layout)

        # Console
        self.console = AnsiConsole()
        layout.addWidget(self.console, 1)
        
        # Escape to exit search
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search_input, self._exit_search)

    def _setup_stats_bar(self, layout: QVBoxLayout) -> None:
        """Create the stats bar with status indicators."""
        stats = QFrame()
        stats.setStyleSheet("""
            QFrame {
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 2px;
            }
        """)
        sl = QHBoxLayout(stats)
        sl.setContentsMargins(8, 4, 8, 4)
        
        self.stats_label = QLabel("Stopped")
        self.stats_label.setStyleSheet("color: #888; font-family: monospace;")
        sl.addWidget(self.stats_label)
        sl.addStretch()
        
        self.mode_label = QLabel("")
        self.mode_label.setStyleSheet("color: #8f8; font-family: monospace;")
        sl.addWidget(self.mode_label)
        
        btn_open = QPushButton("")
        btn_open.setIcon(IconProvider.get(Icon.OPEN_FILE))
        btn_open.setFixedHeight(24)
        btn_open.clicked.connect(self._open_log_file)
        sl.addWidget(btn_open)
        
        layout.addWidget(stats)

    def _setup_search_row(self, layout: QVBoxLayout) -> None:
        """Create the search input row."""
        search = QHBoxLayout()
        search.setSpacing(4)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search history... (Enter)")
        self.search_input.setStyleSheet("""
            QLineEdit {
                padding: 4px 8px;
                border: 1px solid #333;
                border-radius: 2px;
                background: #1a1a1a;
            }
            QLineEdit:focus {
                border-color: #4688d8;
            }
        """)
        self.search_input.returnPressed.connect(self._do_search)
        
        self.btn_search = icon_button(Icon.SEARCH, tooltip="Search")
        self.btn_search.clicked.connect(self._do_search)
        
        self.btn_clear_search = icon_button(Icon.CLEAR, tooltip="Clear search")
        self.btn_clear_search.clicked.connect(self._exit_search)
        self.btn_clear_search.setEnabled(False)
        
        self.search_label = QLabel("")
        self.search_label.setStyleSheet("color: #888; font-size: 11px;")
        
        search.addWidget(self.search_input, 1)
        search.addWidget(self.btn_search)
        search.addWidget(self.btn_clear_search)
        search.addWidget(self.search_label)
        layout.addLayout(search)

    def _setup_action_buttons(self, layout: QVBoxLayout) -> None:
        """Create the action button row."""
        btns = QHBoxLayout()
        btns.setSpacing(4)
        
        self.btn_older = QPushButton("Older")
        self.btn_older.setIcon(IconProvider.get(Icon.OLDER))
        self.btn_older.clicked.connect(self._load_older)
        
        self.btn_live = QPushButton("Live")
        self.btn_live.setIcon(IconProvider.get(Icon.LIVE))
        self.btn_live.clicked.connect(self._go_live)
        
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._clear_view)
        
        btn_clear_all = QPushButton("Delete")
        btn_clear_all.clicked.connect(self._clear_all)
        
        btn_scroll = QPushButton("End")
        btn_scroll.setIcon(IconProvider.get(Icon.SCROLL_END))
        btn_scroll.clicked.connect(self._scroll_end)
        
        self.line_info = QLabel("")
        self.line_info.setStyleSheet("color: #888; font-size: 11px;")
        
        # Apply consistent button styling
        for btn in (self.btn_older, self.btn_live, btn_clear, btn_clear_all, btn_scroll):
            btn.setStyleSheet("""
                QPushButton {
                    padding: 4px 8px;
                    border: 1px solid #333;
                    border-radius: 2px;
                    background: #252525;
                }
                QPushButton:hover {
                    background: #303030;
                    border-color: #444;
                }
                QPushButton:pressed {
                    background: #202020;
                }
                QPushButton:disabled {
                    color: #555;
                    background: #1a1a1a;
                }
            """)
        
        btns.addWidget(self.btn_older)
        btns.addWidget(self.btn_live)
        btns.addWidget(btn_clear)
        btns.addWidget(btn_clear_all)
        btns.addWidget(btn_scroll)
        btns.addStretch()
        btns.addWidget(self.line_info)
        layout.addLayout(btns)

    def _update_ui(self) -> None:
        """Update UI based on mode."""
        total = self.buffer.line_count()
        
        if self._mode == ViewMode.LIVE:
            self.mode_label.setText("● LIVE")
            self.mode_label.setStyleSheet("color: #8f8; font-family: monospace;")
            self.btn_older.setEnabled(total > 0)
            self.btn_live.setEnabled(False)
            self.btn_clear_search.setEnabled(False)
            self.line_info.setText(f"{len(self.buffer.lines):,} lines")
            
        elif self._mode == ViewMode.HISTORY:
            self.mode_label.setText("◐ HISTORY")
            self.mode_label.setStyleSheet("color: #ff8; font-family: monospace;")
            self.btn_older.setEnabled(self._history_start > 0)
            self.btn_live.setEnabled(True)
            self.btn_clear_search.setEnabled(False)
            if total:
                self.line_info.setText(
                    f"Lines {self._history_start + 1:,} - {self._history_end:,} of {total:,}")
                    
        elif self._mode == ViewMode.SEARCH:
            self.mode_label.setText("◎ SEARCH")
            self.mode_label.setStyleSheet("color: #88f; font-family: monospace;")
            self.btn_older.setEnabled(False)
            self.btn_live.setEnabled(True)
            self.btn_clear_search.setEnabled(True)

    def update_stats(self, stats: "ProcessStats") -> None:
        """Update process statistics display."""
        color = "#8f8" if stats.running else "#888"
        self.stats_label.setText(str(stats))
        self.stats_label.setStyleSheet(f"color: {color}; font-family: monospace;")

    def _open_log_file(self) -> None:
        """Open log file in system default application."""
        path = str(self.buffer.file)
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception:
            pass

    # --- Live mode ---

    def _go_live(self) -> None:
        """Switch to live mode."""
        self._mode = ViewMode.LIVE
        self.search_input.clear()
        self.search_label.setText("")
        self.console.set_content(self.buffer.get_recent())
        self._scroll_end()
        self._update_ui()

    def append(self, text: str) -> None:
        """Queue text for display (live mode only)."""
        self._pending.append(text)

    def flush(self) -> None:
        """Flush pending text to console."""
        if not self._pending:
            return
        
        text = "".join(self._pending)
        self._pending.clear()
        
        if self._mode != ViewMode.LIVE:
            return

        # Limit per-flush to prevent freeze
        if len(text) > MAX_FLUSH_CHARS:
            nl = text.find('\n', len(text) - MAX_FLUSH_CHARS)
            text = text[nl + 1:] if nl > 0 else text[-MAX_FLUSH_CHARS:]

        sb = self.console.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 50

        self.console.setUpdatesEnabled(False)
        self.console.append_text(text)
        self.console.setUpdatesEnabled(True)

        if at_bottom:
            sb.setValue(sb.maximum())
        
        self.line_info.setText(f"{len(self.buffer.lines):,} lines")

    # --- History mode ---

    def _load_older(self) -> None:
        """Load older history entries."""
        if self._mode == ViewMode.SEARCH:
            return
        
        total = self.buffer.line_count()
        if not total:
            self.btn_older.setEnabled(False)
            return

        self.btn_older.setText("Loading...")
        self.btn_older.setEnabled(False)
        self.console.setUpdatesEnabled(False)

        try:
            if self._mode == ViewMode.LIVE:
                # First entry into history
                self._mode = ViewMode.HISTORY
                self._history_end = total
                text, self._history_start = self.buffer.load_chunk(total, 10000)
                self.console.set_content(text)
                self._scroll_end()
            else:
                # Load older chunk
                if self._history_start <= 0:
                    return
                text, new_start = self.buffer.load_chunk(self._history_start, 10000)
                if text:
                    sb = self.console.verticalScrollBar()
                    old_max, old_val = sb.maximum(), sb.value()
                    self.console.prepend_text(text)
                    sb.setValue(old_val + (sb.maximum() - old_max))
                    self._history_start = new_start
        finally:
            self.console.setUpdatesEnabled(True)
            self.btn_older.setText("Older")
            self.btn_older.setIcon(IconProvider.get(Icon.OLDER))
            self._update_ui()

    # --- Search mode ---

    def _do_search(self) -> None:
        """Perform search on log history."""
        query = self.search_input.text().strip()
        if not query:
            self._exit_search()
            return

        self._mode = ViewMode.SEARCH
        self.search_label.setText("Searching...")
        self.search_label.setStyleSheet("color: #ff8; font-size: 11px;")
        self.console.setUpdatesEnabled(False)

        try:
            results, count = self.buffer.search(query)
            self.console.set_content(results)
            if count:
                self.search_label.setText(f"{count:,} matches")
                self.search_label.setStyleSheet("color: #8f8; font-size: 11px;")
            else:
                self.search_label.setText("No matches")
                self.search_label.setStyleSheet("color: #f88; font-size: 11px;")
        finally:
            self.console.setUpdatesEnabled(True)
            self._update_ui()

    def _exit_search(self) -> None:
        """Exit search mode."""
        self.search_input.clear()
        self.search_label.setText("")
        if self._mode == ViewMode.SEARCH:
            self._go_live()

    # --- Utility ---

    def _scroll_end(self) -> None:
        """Scroll to end of console."""
        self.console.moveCursor(QTextCursor.MoveOperation.End)
        self.console.verticalScrollBar().setValue(
            self.console.verticalScrollBar().maximum()
        )

    def _clear_view(self) -> None:
        """Clear console display only."""
        self.console.clear()
        self.console._reset()
        self._mode = ViewMode.LIVE
        self.search_label.setText("")
        self._update_ui()

    def _clear_all(self) -> None:
        """Clear both display and buffer."""
        self.buffer.clear()
        self._clear_view()
