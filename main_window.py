"""
Main Window - Application UI with Python path, code editor, and auto-update.

Layout:
- Left: Bot configuration panel
- Right: Log tabs with live streaming

Features:
- Custom Python path (or auto-detect from venv)
- Code editor button to create/edit entry script
- Global "New Editor" button for scratch editing
- Auto-update from GitHub releases
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget, QFrame,
    QProxyStyle, QStyle, QProgressDialog, QDialog, QDialogButtonBox,
    QTextEdit
)

from config import Bot, load_config, save_config, FLUSH_INTERVAL_MS, STATS_INTERVAL_MS
from log_buffer import LogBuffer
from log_view import LogView
from process_mgr import ProcessManager
from stats import ProcessStats, StatsMonitor
from editor import EditorWindow
from icons import Icon, IconProvider, icon_button


# Consistent button styling
BUTTON_STYLE = """
    QPushButton {
        padding: 4px 10px;
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
"""

INPUT_STYLE = """
    QLineEdit, QPlainTextEdit {
        padding: 4px 8px;
        border: 1px solid #333;
        border-radius: 2px;
        background: #1a1a1a;
    }
    QLineEdit:focus, QPlainTextEdit:focus {
        border-color: #4688d8;
    }
"""

class NoTabSeamStyle(QProxyStyle):
    """
    Removes the 1px 'documentMode' seam/line some native styles draw around
    the tab bar / tab widget pane.
    """
    def drawPrimitive(self, element, option, painter, widget=None):
        # Skip the primitives that draw that annoying line
        if element in (
            QStyle.PrimitiveElement.PE_FrameTabWidget,
            QStyle.PrimitiveElement.PE_FrameTabBarBase,
        ):
            return
        super().drawPrimitive(element, option, painter, widget)


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pythonator")
        self.resize(1200, 750)

        # Data
        self.bots = load_config()
        self.buffers: dict[str, LogBuffer] = {}
        self.views: dict[str, LogView] = {}

        # Editor windows
        self._editors: dict[str, EditorWindow] = {}
        self._scratch_editor: Optional[EditorWindow] = None

        # Process management
        self.proc_mgr = ProcessManager(
            on_output=self._on_process_output,
            on_finished=self._on_process_finished,
        )
        self.stats = StatsMonitor()

        # Update state
        self._update_checker = None
        self._update_downloader = None
        self._pending_release = None

        # Sync flag to prevent signal loops
        self._syncing = False

        self._build_ui()
        self._load_bots()

        # Timers
        self._flush_timer = QTimer(self)
        self._flush_timer.timeout.connect(self._flush_logs)
        self._flush_timer.start(FLUSH_INTERVAL_MS)

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(STATS_INTERVAL_MS)

        # Check for updates on startup (delayed)
        QTimer.singleShot(3000, self._check_for_updates_silent)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Top bar
        self._build_top_bar(layout)

        # Splitter with config and log panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(9)  # grab area (invisible)

        splitter.setStyleSheet("""

            QSplitter::handle:horizontal:hover {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0.00 transparent,
                    stop:0.45 transparent,
                    stop:0.50 #3a3a3a,   /* hover line */
                    stop:0.55 transparent,
                    stop:1.00 transparent
                );
            }
        """)

        splitter.addWidget(self._build_config_panel())
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)


        # Shortcuts
        QShortcut(QKeySequence("Ctrl+N"), self, self._add_bot)
        QShortcut(QKeySequence("Ctrl+S"), self, self._start_current)
        QShortcut(QKeySequence("Ctrl+R"), self, self._restart_current)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)
        QShortcut(QKeySequence("Ctrl+U"), self, self._check_for_updates)

    def _build_top_bar(self, layout: QVBoxLayout) -> None:
        """Build the top toolbar."""
        top = QHBoxLayout()
        top.setSpacing(8)

        # Links
        github = QLabel('<a href="https://github.com/cfunkz" style="color:#888;">GitHub</a>')
        readme = QLabel('<a href="https://github.com/cfunkz" style="color:#888;">License</a>')
        readme.setOpenExternalLinks(True)
        github.setOpenExternalLinks(True)
        
        top.addWidget(github)
        top.addWidget(readme)
        top.addStretch()

        # Status
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #888; font-family: monospace;")
        top.addWidget(self.status_label)

        # Update button
        self.btn_update = QPushButton("Check Updates")
        self.btn_update.setIcon(IconProvider.get(Icon.RELOAD))
        self.btn_update.setStyleSheet(BUTTON_STYLE)
        self.btn_update.setToolTip("Check for application updates (Ctrl+U)")
        self.btn_update.clicked.connect(self._check_for_updates)
        top.addWidget(self.btn_update)

        # Global actions
        self.btn_start_all = QPushButton("Start All")
        self.btn_start_all.setIcon(IconProvider.get(Icon.START))
        self.btn_start_all.setStyleSheet(BUTTON_STYLE)
        self.btn_start_all.clicked.connect(self._start_all)
        top.addWidget(self.btn_start_all)

        self.btn_stop_all = QPushButton("Stop All")
        self.btn_stop_all.setIcon(IconProvider.get(Icon.STOP))
        self.btn_stop_all.setStyleSheet(BUTTON_STYLE)
        self.btn_stop_all.clicked.connect(self._stop_all)
        top.addWidget(self.btn_stop_all)

        layout.addLayout(top)

    def _build_config_panel(self) -> QWidget:
        """Build the bot configuration panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        # Bot selector
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("Bot:"))

        self.bot_combo = QComboBox()
        self.bot_combo.setStyleSheet("""
            QComboBox {
                padding: 4px 8px;
                border: 1px solid #333;
                border-radius: 2px;
                background: #1a1a1a;
            }
            QComboBox:hover {
                border-color: #444;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background: #1a1a1a;
                border: 1px solid #333;
                selection-background-color: #4688d8;
            }
        """)
        self.bot_combo.currentTextChanged.connect(self._on_combo_changed)
        row.addWidget(self.bot_combo, 1)

        btn_add = icon_button(Icon.ADD, tooltip="Add bot")
        btn_add.setStyleSheet(BUTTON_STYLE)
        btn_add.clicked.connect(self._add_bot)
        row.addWidget(btn_add)

        self.btn_del = icon_button(Icon.DELETE, tooltip="Delete bot")
        self.btn_del.setStyleSheet(BUTTON_STYLE)
        self.btn_del.clicked.connect(self._del_bot)
        row.addWidget(self.btn_del)

        layout.addLayout(row)

        # Entry path
        layout.addWidget(QLabel("Entry:"))
        entry_row = QHBoxLayout()
        entry_row.setSpacing(4)

        self.entry_input = QLineEdit()
        self.entry_input.setPlaceholderText("Path to Python script")
        self.entry_input.setStyleSheet(INPUT_STYLE)
        self.entry_input.textChanged.connect(self._save_bot)
        entry_row.addWidget(self.entry_input, 1)

        self.btn_edit = icon_button(Icon.EDIT, tooltip="Create/Edit entry script")
        self.btn_edit.setStyleSheet(BUTTON_STYLE)
        self.btn_edit.clicked.connect(self._edit_entry)
        entry_row.addWidget(self.btn_edit)

        btn_browse = icon_button(Icon.BROWSE, tooltip="Browse for script")
        btn_browse.setStyleSheet(BUTTON_STYLE)
        btn_browse.clicked.connect(self._browse_entry)
        entry_row.addWidget(btn_browse)

        layout.addLayout(entry_row)

        # Python path
        layout.addWidget(QLabel("Python:"))
        py_row = QHBoxLayout()
        py_row.setSpacing(4)

        self.python_input = QLineEdit()
        self.python_input.setPlaceholderText("Auto-detect from venv")
        self.python_input.setToolTip("Leave empty to use .venv, or set custom Python path")
        self.python_input.setStyleSheet(INPUT_STYLE)
        self.python_input.textChanged.connect(self._save_bot)
        py_row.addWidget(self.python_input, 1)

        btn_detect = icon_button(Icon.DETECT, tooltip="Detect system Python")
        btn_detect.setStyleSheet(BUTTON_STYLE)
        btn_detect.clicked.connect(self._detect_python)
        py_row.addWidget(btn_detect)

        btn_browse_py = icon_button(Icon.BROWSE, tooltip="Browse for Python executable")
        btn_browse_py.setStyleSheet(BUTTON_STYLE)
        btn_browse_py.clicked.connect(self._browse_python)
        py_row.addWidget(btn_browse_py)

        layout.addLayout(py_row)

        # Custom command mode
        self.custom_cmd_check = QCheckBox("Custom command mode")
        self.custom_cmd_check.setToolTip("Use flags as full command")
        self.custom_cmd_check.stateChanged.connect(self._on_custom_cmd_toggle)
        layout.addWidget(self.custom_cmd_check)

        # Flags
        self.flags_label = QLabel("Flags:")
        layout.addWidget(self.flags_label)

        self.flags_input = QLineEdit()
        self.flags_input.setPlaceholderText("Additional arguments")
        self.flags_input.setStyleSheet(INPUT_STYLE)
        self.flags_input.textChanged.connect(self._save_bot)
        layout.addWidget(self.flags_input)

        # Requirements
        layout.addWidget(QLabel("Requirements:"))
        self.reqs_input = QPlainTextEdit()
        self.reqs_input.setPlaceholderText("One package per line")
        self.reqs_input.setStyleSheet(INPUT_STYLE)
        self.reqs_input.textChanged.connect(self._save_bot)
        layout.addWidget(self.reqs_input, 1)

        # Venv buttons
        venv_row = QHBoxLayout()
        venv_row.setSpacing(4)
        
        btn_venv = QPushButton("Setup venv")
        btn_venv.setStyleSheet(BUTTON_STYLE)
        btn_venv.clicked.connect(self._setup_venv)
        venv_row.addWidget(btn_venv)

        btn_deps = QPushButton("Install deps")
        btn_deps.setStyleSheet(BUTTON_STYLE)
        btn_deps.clicked.connect(self._install_deps)
        venv_row.addWidget(btn_deps)
        layout.addLayout(venv_row)

        # Editor button
        self.btn_new_editor = QPushButton("Open Editor")
        self.btn_new_editor.setIcon(IconProvider.get(Icon.EDIT))
        self.btn_new_editor.setStyleSheet(BUTTON_STYLE)
        self.btn_new_editor.setToolTip("Open a new unsaved editor window")
        self.btn_new_editor.clicked.connect(self._open_scratch_editor)
        layout.addWidget(self.btn_new_editor)

        # Auto-restart
        self.auto_restart_check = QCheckBox("Auto-restart on crash")
        layout.addWidget(self.auto_restart_check)

        return panel

    def _build_log_panel(self) -> QWidget:
        """Build the log viewer panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Control buttons
        ctrl = QHBoxLayout()
        ctrl.setSpacing(4)

        self.btn_start = QPushButton("Start")
        self.btn_start.setIcon(IconProvider.get(Icon.START))
        self.btn_start.setStyleSheet(BUTTON_STYLE)
        self.btn_start.clicked.connect(self._start_current)
        ctrl.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setIcon(IconProvider.get(Icon.STOP))
        self.btn_stop.setStyleSheet(BUTTON_STYLE)
        self.btn_stop.clicked.connect(self._stop_current)
        ctrl.addWidget(self.btn_stop)

        self.btn_restart = QPushButton("Restart")
        self.btn_restart.setIcon(IconProvider.get(Icon.RESTART))
        self.btn_restart.setStyleSheet(BUTTON_STYLE)
        self.btn_restart.clicked.connect(self._restart_current)
        ctrl.addWidget(self.btn_restart)

        ctrl.addStretch()
        layout.addLayout(ctrl)
        # Tabs with improved styling
        self.tabs = QTabWidget()
        # Kill the seam at the style layer (works even when QSS doesn't)
        self.tabs.setStyle(NoTabSeamStyle(self.tabs.style()))
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2a2a2a;
                border-top: none;
                border-radius: 0 0 2px 2px;
            }
            QTabBar::tab {
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-bottom: none;
                padding: 6px 16px;
                margin-right: 2px;
                border-radius: 2px 2px 0 0;
            }
            QTabBar::tab:selected {
                background: #252525;
                border-color: #252525;
                border-bottom: none;
            }
            QTabBar::tab:hover:!selected {
                background: #202020;
            }
        """)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        return panel

    # -------------------------------------------------------------------------
    # Bot management
    # -------------------------------------------------------------------------

    def _load_bots(self) -> None:
        """Load bots into UI."""
        for name, bot in self.bots.items():
            self._create_bot_views(name)

        if self.bots:
            first = next(iter(self.bots))
            self.bot_combo.setCurrentText(first)
            self._load_bot_to_ui(first)

        self._update_ui()

    def _create_bot_views(self, name: str) -> None:
        """Create views for a bot."""
        if name not in self.buffers:
            self.buffers[name] = LogBuffer(name)
        if name not in self.views:
            view = LogView(name, self.buffers[name])
            self.views[name] = view
            self.tabs.addTab(view, name)
        if self.bot_combo.findText(name) < 0:
            self.bot_combo.addItem(name)

    def _load_bot_to_ui(self, name: str) -> None:
        """Load bot config into UI fields."""
        bot = self.bots.get(name)
        if not bot:
            return

        self.entry_input.blockSignals(True)
        self.flags_input.blockSignals(True)
        self.reqs_input.blockSignals(True)
        self.python_input.blockSignals(True)
        self.custom_cmd_check.blockSignals(True)

        self.entry_input.setText(bot.entry)
        self.flags_input.setText(bot.flags)
        self.reqs_input.setPlainText(bot.reqs)
        self.python_input.setText(bot.python_path)
        self.custom_cmd_check.setChecked(bot.custom_cmd)

        is_custom = bot.custom_cmd
        self.flags_label.setText("Command:" if is_custom else "Flags:")
        self.flags_input.setPlaceholderText("e.g., uvicorn main:app" if is_custom else "Arguments")
        self.entry_input.setEnabled(not is_custom)
        self.btn_edit.setEnabled(not is_custom)

        self.entry_input.blockSignals(False)
        self.flags_input.blockSignals(False)
        self.reqs_input.blockSignals(False)
        self.python_input.blockSignals(False)
        self.custom_cmd_check.blockSignals(False)

    def _save_bot(self) -> None:
        """Save current bot config."""
        name = self.bot_combo.currentText()
        if not name:
            return
        self.bots[name] = Bot(
            name=name,
            entry=self.entry_input.text().strip(),
            reqs=self.reqs_input.toPlainText().strip(),
            flags=self.flags_input.text().strip(),
            custom_cmd=self.custom_cmd_check.isChecked(),
            python_path=self.python_input.text().strip(),
        )
        save_config(self.bots)

    def _add_bot(self) -> None:
        """Add a new bot."""
        name, ok = QInputDialog.getText(self, "New Bot", "Bot name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.bots:
            QMessageBox.warning(self, "Duplicate", f"Bot '{name}' exists")
            return
        self.bots[name] = Bot(name=name)
        save_config(self.bots)
        self._create_bot_views(name)
        self.bot_combo.setCurrentText(name)
        self._load_bot_to_ui(name)
        self._update_ui()

    def _del_bot(self) -> None:
        """Delete current bot."""
        name = self.bot_combo.currentText()
        if not name:
            return
        if self.proc_mgr.is_running(name):
            QMessageBox.warning(self, "Running", "Stop the bot first")
            return

        reply = QMessageBox.question(
            self, "Confirm", f"Delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        del self.bots[name]
        save_config(self.bots)
        if name in self.views:
            idx = self.tabs.indexOf(self.views[name])
            if idx >= 0:
                self.tabs.removeTab(idx)
            del self.views[name]
        self.buffers.pop(name, None)
        self._editors.pop(name, None)

        idx = self.bot_combo.findText(name)
        if idx >= 0:
            self.bot_combo.removeItem(idx)
        self._update_ui()

    def _browse_entry(self) -> None:
        """Browse for entry script."""
        path, _ = QFileDialog.getOpenFileName(self, "Select Script", "", "Python (*.py);;All (*)")
        if path:
            self.entry_input.setText(path)

    def _browse_python(self) -> None:
        """Browse for Python executable."""
        filt = "Executable (*.exe);;All (*)" if sys.platform == "win32" else "All (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Python", "", filt)
        if path:
            self.python_input.setText(path)

    def _detect_python(self) -> None:
        """Detect Python in venv."""
        entry = self.entry_input.text().strip()
        if not entry:
            QMessageBox.warning(self, "No entry", "Set an entry script first.")
            return

        base = Path(entry).resolve().parent
        vpy = base / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

        if vpy.exists():
            self.python_input.setText(str(vpy))
            return

        QMessageBox.information(
            self, "No venv found",
            "No .venv found for this bot.\nCreate the venv first or select Python manually."
        )

    def _edit_entry(self) -> None:
        """Open code editor for entry script."""
        name = self.bot_combo.currentText()
        if not name:
            return

        entry = self.entry_input.text().strip()
        if not entry:
            QMessageBox.warning(self, "No Entry", "Set an entry path first")
            return

        if name in self._editors and self._editors[name].isVisible():
            self._editors[name].raise_()
            self._editors[name].activateWindow()
        else:
            editor = EditorWindow()
            editor.set_file(entry)
            editor.show()
            self._editors[name] = editor

    def _open_scratch_editor(self) -> None:
        """Open a new scratch editor window."""
        if self._scratch_editor and self._scratch_editor.isVisible():
            self._scratch_editor.raise_()
            self._scratch_editor.activateWindow()
        else:
            self._scratch_editor = EditorWindow()
            self._scratch_editor.show()

    def _on_custom_cmd_toggle(self, state: int) -> None:
        """Handle custom command mode toggle."""
        is_custom = self.custom_cmd_check.isChecked()
        self.flags_label.setText("Command:" if is_custom else "Flags:")
        self.flags_input.setPlaceholderText("e.g., uvicorn main:app" if is_custom else "Arguments")
        self.entry_input.setEnabled(not is_custom)
        self.btn_edit.setEnabled(not is_custom)
        self._save_bot()

    # -------------------------------------------------------------------------
    # Tab/Combo sync
    # -------------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        """Sync combo when tab changes."""
        if self._syncing or index < 0:
            return
        self._syncing = True
        widget = self.tabs.widget(index)
        for name, view in self.views.items():
            if view is widget:
                self.bot_combo.blockSignals(True)
                self.bot_combo.setCurrentText(name)
                self.bot_combo.blockSignals(False)
                self._load_bot_to_ui(name)
                break
        self._syncing = False
        self._update_ui()

    def _on_combo_changed(self, name: str) -> None:
        """Sync tab when combo changes."""
        if self._syncing or not name:
            return
        self._syncing = True
        self._load_bot_to_ui(name)
        if name in self.views:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentWidget(self.views[name])
            self.tabs.blockSignals(False)
        self._syncing = False
        self._update_ui()

    # -------------------------------------------------------------------------
    # Process actions
    # -------------------------------------------------------------------------

    def _start_current(self) -> None:
        """Start current bot."""
        self._save_bot()
        name = self.bot_combo.currentText()
        if name and name in self.bots:
            self.proc_mgr.start(self.bots[name])
        self._update_ui()

    def _stop_current(self) -> None:
        """Stop current bot."""
        name = self.bot_combo.currentText()
        if name:
            self.proc_mgr.stop(name)
        self._update_ui()

    def _restart_current(self) -> None:
        """Restart current bot."""
        self._save_bot()
        name = self.bot_combo.currentText()
        if not name:
            return
        if self.proc_mgr.is_running(name):
            self.proc_mgr.stop(name)
            QTimer.singleShot(600, lambda: self._start_by_name(name))
        else:
            self._start_by_name(name)

    def _start_by_name(self, name: str) -> None:
        """Start a specific bot by name."""
        if name in self.bots:
            self.proc_mgr.start(self.bots[name])
        self._update_ui()

    def _start_all(self) -> None:
        """Start all bots."""
        self._save_bot()
        for name, bot in self.bots.items():
            if not self.proc_mgr.is_running(name):
                self.proc_mgr.start(bot)
        self._update_ui()

    def _stop_all(self) -> None:
        """Stop all bots."""
        self.proc_mgr.stop_all()
        self._update_ui()

    def _setup_venv(self) -> None:
        """Setup venv for current bot."""
        self._save_bot()
        name = self.bot_combo.currentText()
        if name and name in self.bots:
            self.proc_mgr.setup_venv(self.bots[name])

    def _install_deps(self) -> None:
        """Install dependencies for current bot."""
        self._save_bot()
        name = self.bot_combo.currentText()
        if name and name in self.bots:
            self.proc_mgr.install_deps(self.bots[name])

    # -------------------------------------------------------------------------
    # Update system
    # -------------------------------------------------------------------------

    def _check_for_updates(self) -> None:
        """Check for updates (user-initiated)."""
        from updater import UpdateChecker, CURRENT_VERSION
        
        self.btn_update.setEnabled(False)
        self.btn_update.setText("Checking...")
        
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.no_update.connect(self._on_no_update)
        self._update_checker.error.connect(self._on_update_error)
        self._update_checker.not_configured.connect(self._on_updates_not_configured)
        self._update_checker.finished.connect(self._on_check_finished)
        self._update_checker.start()

    def _check_for_updates_silent(self) -> None:
        """Check for updates silently on startup."""
        try:
            from updater import UpdateChecker, UPDATES_ENABLED, GITHUB_OWNER
            
            # Don't check if not configured
            if not UPDATES_ENABLED or GITHUB_OWNER in ("your-username", ""):
                return
            
            self._update_checker = UpdateChecker()
            self._update_checker.update_available.connect(self._on_update_available_silent)
            # Ignore errors on silent check
            self._update_checker.start()
        except ImportError:
            pass  # Updater not available

    def _on_check_finished(self) -> None:
        """Reset button after check completes."""
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Check Updates")

    def _on_update_available(self, release) -> None:
        """Handle update available (user-initiated check)."""
        self._pending_release = release
        self._show_update_dialog(release)

    def _on_update_available_silent(self, release) -> None:
        """Handle update available (silent startup check)."""
        self._pending_release = release
        # Show subtle indicator
        self.btn_update.setText(f"Update ({release.version})")
        self.btn_update.setStyleSheet(BUTTON_STYLE.replace(
            "background: #252525",
            "background: #2d4a2d"  # Green tint
        ))
        self.btn_update.setToolTip(f"Update available: v{release.version}\nClick to download")

    def _on_no_update(self) -> None:
        """Handle no update available."""
        from updater import CURRENT_VERSION
        QMessageBox.information(
            self, "Pythonator Updater",
            f"You're running the latest version (v{CURRENT_VERSION})."
        )

    def _on_updates_not_configured(self) -> None:
        """Handle updates not configured."""
        QMessageBox.information(
            self, "Updates Not Configured",
            "Auto-updates are not configured.\n\n"
            "To enable updates, edit updater.py and set:\n"
            "• GITHUB_OWNER = 'your-username'\n"
            "• GITHUB_REPO = 'your-repo-name'\n\n"
            "Then create releases on GitHub with version tags."
        )

    def _on_update_error(self, error: str) -> None:
        """Handle update check error."""
        QMessageBox.warning(
            self, "Update Check Failed",
            f"Could not check for updates:\n{error}"
        )

    def _show_update_dialog(self, release) -> None:
        """Show update dialog with release notes."""
        from updater import CURRENT_VERSION
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Update Available")
        dialog.setMinimumSize(500, 400)
        
        layout = QVBoxLayout(dialog)
        
        # Header
        header = QLabel(f"<h3>Version {release.version} is available!</h3>")
        header.setStyleSheet("color: #8f8;")
        layout.addWidget(header)
        
        current = QLabel(f"Current version: {CURRENT_VERSION}")
        current.setStyleSheet("color: #888;")
        layout.addWidget(current)
        
        # Release notes
        if release.release_notes:
            layout.addWidget(QLabel("Release Notes:"))
            notes = QTextEdit()
            notes.setReadOnly(True)
            notes.setStyleSheet(INPUT_STYLE)
            notes.setPlainText(release.release_notes)
            layout.addWidget(notes, 1)
        
        # Buttons
        buttons = QDialogButtonBox()
        btn_download = buttons.addButton("Download && Install", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_later = buttons.addButton("Later", QDialogButtonBox.ButtonRole.RejectRole)
        
        btn_download.clicked.connect(lambda: self._start_download(release, dialog))
        btn_later.clicked.connect(dialog.reject)
        
        layout.addWidget(buttons)
        dialog.exec()

    def _start_download(self, release, dialog: QDialog) -> None:
        """Start downloading the update."""
        from updater import UpdateDownloader
        
        dialog.accept()
        
        # Progress dialog
        self._progress = QProgressDialog(
            "Downloading update...", "Cancel", 0, 100, self
        )
        self._progress.setWindowTitle("Downloading")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setValue(0)
        
        # Start download
        self._update_downloader = UpdateDownloader(release)
        self._update_downloader.progress.connect(self._on_download_progress)
        self._update_downloader.finished.connect(self._on_download_finished)
        self._update_downloader.error.connect(self._on_download_error)
        self._update_downloader.start()
        
        self._progress.canceled.connect(self._cancel_download)

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        """Update download progress."""
        if total > 0:
            percent = int(downloaded / total * 100)
            self._progress.setValue(percent)
            mb_down = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self._progress.setLabelText(f"Downloading... {mb_down:.1f} / {mb_total:.1f} MB")
        else:
            self._progress.setLabelText(f"Downloading... {downloaded / (1024 * 1024):.1f} MB")

    def _on_download_finished(self, staging_path: str) -> None:
        """Handle download complete."""
        self._progress.close()
        
        reply = QMessageBox.question(
            self, "Ready to Install",
            "Download complete!\n\n"
            "The application will close and restart with the new version.\n"
            "Your bots and logs will be preserved.\n\n"
            "Install now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self._apply_update(staging_path)

    def _on_download_error(self, error: str) -> None:
        """Handle download error."""
        self._progress.close()
        QMessageBox.critical(
            self, "Download Failed",
            f"Failed to download update:\n{error}"
        )

    def _cancel_download(self) -> None:
        """Cancel the download."""
        if self._update_downloader:
            self._update_downloader.terminate()

    def _apply_update(self, staging_path: str) -> None:
        """Apply the update and restart."""
        from pathlib import Path
        from updater import launch_updater
        
        try:
            # Stop all processes
            self.proc_mgr.stop_all()
            
            # Launch updater
            launch_updater(Path(staging_path))
            
            # Close application
            self.close()
            
        except Exception as e:
            QMessageBox.critical(
                self, "Update Failed",
                f"Failed to launch updater:\n{e}"
            )

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    def _on_process_output(self, name: str, text: str) -> None:
        """Handle process output."""
        if name not in self.buffers:
            return
        display_text, _ = self.buffers[name].append(text)
        if display_text and name in self.views:
            self.views[name].append(display_text)

    def _on_process_finished(self, name: str, code: int, should_restart: bool) -> None:
        """Handle process completion."""
        pid = self.proc_mgr.get_pid(name)
        if pid:
            self.stats.clear(pid)
        if name in self.views:
            self.views[name].update_stats(ProcessStats())

        if should_restart and self.auto_restart_check.isChecked() and code != 0:
            self._on_process_output(name, "\x1b[33m[runner] Restarting...\x1b[0m\n")
            QTimer.singleShot(500, lambda: self._start_by_name(name))
        self._update_ui()

    # -------------------------------------------------------------------------
    # Timers
    # -------------------------------------------------------------------------

    def _flush_logs(self) -> None:
        """Flush pending logs to views."""
        for view in self.views.values():
            view.flush()
        self._update_ui()

    def _update_stats(self) -> None:
        """Update process stats in views."""
        running = self.proc_mgr.running
        for name, view in self.views.items():
            if name in running:
                pid = self.proc_mgr.get_pid(name)
                view.update_stats(self.stats.get_stats(pid))
            else:
                view.update_stats(ProcessStats())

    def _update_ui(self) -> None:
        """Update UI state based on running processes."""
        name = self.bot_combo.currentText()
        running = self.proc_mgr.is_running(name) if name else False
        n_running = len(self.proc_mgr.running)
        n_total = len(self.bots)

        self.status_label.setText(f"{n_running}/{n_total} running" if n_total else "Ready")
        self.btn_start.setEnabled(bool(name) and not running)
        self.btn_stop.setEnabled(running)
        self.btn_restart.setEnabled(bool(name))
        self.btn_del.setEnabled(bool(name) and not running)
        self.btn_start_all.setEnabled(n_running < n_total)
        self.btn_stop_all.setEnabled(n_running > 0)

    def closeEvent(self, event) -> None:
        """Handle window close."""
        self.proc_mgr.stop_all()
        for editor in self._editors.values():
            editor.close()
        if self._scratch_editor:
            self._scratch_editor.close()
        event.accept()
