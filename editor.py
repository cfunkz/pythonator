"""
Python Code Editor - Syntax highlighting editor window.

Features:
- Line numbers with gutter
- Python syntax highlighting (fast, token-based)
- Uses app-wide palette (no hard-coded theme assumptions)
- Ctrl+S save, Ctrl+Shift+S save as, Ctrl+O open, F5 reload
- Editable path field (press Enter to switch target)
- Unsaved-change tracking + safe atomic saves
- Code completion (Ctrl+Space; optional light auto-trigger) via Jedi
"""
from __future__ import annotations

import builtins
import keyword
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import (
    QEvent, QObject, QRect, QRunnable, QSize, Qt,
    QSaveFile, QStringListModel, QThreadPool, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QCloseEvent, QFont, QKeySequence,
    QPainter, QTextCharFormat, QTextCursor, QTextFormat, QSyntaxHighlighter,
)
from PyQt6.QtWidgets import (
    QCompleter, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QPlainTextEdit, QTextEdit,
    QVBoxLayout, QWidget, QStyle,
)

try:
    import jedi
except ImportError:
    jedi = None


def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    """Create a text format with the given style."""
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class PythonHighlighter(QSyntaxHighlighter):
    """Fast Python syntax highlighter using regex and membership checks."""

    _re_ident = re.compile(r"\b[A-Za-z_]\w*\b")
    _re_number = re.compile(r"\b(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b")
    _re_decorator = re.compile(r"(^|\s)(@\w+(?:\.\w+)*)")
    _re_def = re.compile(r"\bdef\s+([A-Za-z_]\w*)")
    _re_class = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
    _re_single_str = re.compile(
        r"""(?P<prefix>\b[fFrRuU]{0,2})?(?P<q>['"])(?P<body>(?:\\.|(?!\2).)*)(?P=q)"""
    )

    def __init__(self, doc):
        super().__init__(doc)

        # Theme colors
        self.f_keyword = _fmt("#C792EA", bold=True)
        self.f_builtin = _fmt("#82AAFF")
        self.f_number = _fmt("#F78C6C")
        self.f_decorator = _fmt("#FFCB6B")
        self.f_class = _fmt("#FFCB6B", bold=True)
        self.f_def = _fmt("#82AAFF", bold=True)
        self.f_import = _fmt("#89DDFF", bold=True)
        self.f_self = _fmt("#F07178", bold=True)
        self.f_constant = _fmt("#FF5370", bold=True)
        self.f_string = _fmt("#C3E88D")
        self.f_comment = _fmt("#7a8699", italic=True)

        self._kw = set(keyword.kwlist)
        self._import_kw = {"import", "from", "as"}
        self._builtins = {n for n in dir(builtins) if not n.startswith("_")}
        self._selfish = {"self", "global", "nonlocal"}

    @staticmethod
    def _in_spans(i: int, spans: list[tuple[int, int]]) -> bool:
        return any(a <= i < b for a, b in spans)

    def highlightBlock(self, text: str) -> None:
        self.setCurrentBlockState(0)
        spans: list[tuple[int, int]] = []

        def mark(a: int, b: int) -> None:
            if a < b:
                spans.append((a, b))
                self.setFormat(a, b - a, self.f_string)

        # Multiline triple strings via block state (1 = ''', 2 = """)
        prev = self.previousBlockState()
        i = 0
        if prev in (1, 2):
            delim = "'''" if prev == 1 else '"""'
            end = text.find(delim)
            if end == -1:
                mark(0, len(text))
                self.setCurrentBlockState(prev)
                return
            mark(0, end + 3)
            i = end + 3

        while i < len(text):
            s1, s2 = text.find("'''", i), text.find('"""', i)
            if s1 == -1 and s2 == -1:
                break
            if s2 == -1 or (s1 != -1 and s1 < s2):
                s, delim, state = s1, "'''", 1
            else:
                s, delim, state = s2, '"""', 2

            e = text.find(delim, s + 3)
            if e == -1:
                mark(s, len(text))
                self.setCurrentBlockState(state)
                break
            mark(s, e + 3)
            i = e + 3

        # Single-line strings
        for m in self._re_single_str.finditer(text):
            if not self._in_spans(m.start(), spans):
                mark(m.start(), m.end())

        # Comment (first # outside strings)
        h = text.find("#")
        scan_upto = len(text)
        if h != -1 and not self._in_spans(h, spans):
            self.setFormat(h, len(text) - h, self.f_comment)
            scan_upto = h

        head = text[:scan_upto]

        # Decorators
        for m in self._re_decorator.finditer(head):
            pos = m.start(2)
            if not self._in_spans(pos, spans):
                self.setFormat(pos, len(m.group(2)), self.f_decorator)

        # Function and class definitions
        for rx, fmt, group in (
            (self._re_def, self.f_def, 1),
            (self._re_class, self.f_class, 1),
        ):
            for m in rx.finditer(head):
                pos = m.start(group)
                if not self._in_spans(pos, spans):
                    self.setFormat(pos, len(m.group(group)), fmt)

        # Numbers
        for m in self._re_number.finditer(head):
            pos = m.start()
            if not self._in_spans(pos, spans):
                self.setFormat(pos, len(m.group(0)), self.f_number)

        # Identifiers (keywords, builtins, etc.)
        for m in self._re_ident.finditer(head):
            pos = m.start()
            if self._in_spans(pos, spans):
                continue

            w = m.group(0)
            if w in self._import_kw:
                self.setFormat(pos, len(w), self.f_import)
            elif w in self._kw:
                self.setFormat(pos, len(w), self.f_keyword)
            elif w in self._builtins:
                self.setFormat(pos, len(w), self.f_builtin)
            elif w in self._selfish:
                self.setFormat(pos, len(w), self.f_self)
            elif len(w) >= 3 and w.isupper() and all(c.isupper() or c.isdigit() or c == "_" for c in w):
                self.setFormat(pos, len(w), self.f_constant)


class _LineNumberArea(QWidget):
    """Line number gutter widget."""
    
    def __init__(self, editor: "PythonEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor._ln_width(), 0)

    def paintEvent(self, event):
        self.editor._paint_line_numbers(event)


class _JobSignals(QObject):
    """Signals for async completion jobs."""
    done = pyqtSignal(int, list)


class _CompletionJob(QRunnable):
    """Background completion job."""
    
    def __init__(self, request_id: int, fn: Callable[[], list[str]]):
        super().__init__()
        self.request_id = request_id
        self.fn = fn
        self.signals = _JobSignals()

    def run(self) -> None:
        try:
            items = self.fn()
        except Exception:
            items = []
        self.signals.done.emit(self.request_id, items)


def _fallback_words(code: str) -> list[str]:
    """Fallback completion using regex word extraction."""
    words = set(re.findall(r"[A-Za-z_]\w+", code))
    words.update(keyword.kwlist)
    return list(words)


class PythonEditor(QPlainTextEdit):
    """Code editor with line numbers and completion."""

    AUTO_TRIGGER_MIN_PREFIX = 3
    AUTO_TRIGGER_DELAY_MS = 120
    JEDI_MAX_CODE_SIZE = 200_000

    def __init__(self, parent=None):
        super().__init__(parent)

        self._ln_area = _LineNumberArea(self)
        self._context_path: Optional[Path] = None
        self._context_root: Path = Path.cwd()

        self._pool = QThreadPool.globalInstance()
        self._req_id = 0
        self._pending_req_id = 0
        self._pending_cursor_pos = -1

        # Signals
        self.blockCountChanged.connect(lambda _: self._sync_margins())
        self.updateRequest.connect(self._update_ln_area)
        self.cursorPositionChanged.connect(self._highlight_line)

        # Font setup
        font = QFont("Consolas" if sys.platform.startswith("win") else "Monospace", 11)
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)

        # Styling
        self.setStyleSheet("""
            QPlainTextEdit {
                background: #121212;
                color: #ddd;
                border: 1px solid #2b2b2b;
                border-radius: 2px;
                selection-background-color: #4688d8;
            }
        """)

        self._hl = PythonHighlighter(self.document())

        # Completion setup
        self._comp_model = QStringListModel(self)
        self._completer = QCompleter(self._comp_model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.activated.connect(self._insert_completion)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._request_completion_async)

        # Ctrl+Space for completion
        act = QAction(self)
        act.setShortcut(QKeySequence("Ctrl+Space"))
        act.triggered.connect(self._request_completion_async)
        self.addAction(act)

        self._sync_margins()
        self._highlight_line()

    def set_context(self, file_path: Optional[Path]) -> None:
        """Set file context for completions."""
        self._context_path = file_path
        self._context_root = file_path.parent if file_path else Path.cwd()

    # ---- Line numbers ----

    def _ln_width(self) -> int:
        digits = max(2, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _sync_margins(self) -> None:
        w = self._ln_width()
        self.setViewportMargins(w, 0, 0, 0)
        cr = self.contentsRect()
        self._ln_area.setGeometry(QRect(cr.left(), cr.top(), w, cr.height()))
        self._ln_area.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_margins()

    def _update_ln_area(self, rect, dy):
        if dy:
            self._ln_area.scroll(0, dy)
        else:
            self._ln_area.update(0, rect.y(), self._ln_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._sync_margins()

    def _paint_line_numbers(self, event):
        pal = self.palette()
        painter = QPainter(self._ln_area)
        painter.fillRect(event.rect(), QColor("#1a1a1a"))

        # Separator line
        painter.setPen(QColor("#2b2b2b"))
        painter.drawLine(
            self._ln_area.width() - 1, event.rect().top(),
            self._ln_area.width() - 1, event.rect().bottom()
        )

        # Line numbers
        num_color = QColor("#888")
        painter.setPen(num_color)

        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, top, self._ln_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(n + 1),
                )
            block = block.next()
            n += 1
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())

    def _highlight_line(self):
        """Highlight current line."""
        if self.isReadOnly():
            return
        hl = QColor("#4688d8")
        hl.setAlpha(35)
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(hl)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])

    # ---- Completion ----

    def event(self, e: QEvent):
        if e.type() == QEvent.Type.FocusOut and self._completer.popup().isVisible():
            self._completer.popup().hide()
        return super().event(e)

    def keyPressEvent(self, event):
        if self._completer.popup().isVisible() and event.key() in (
            Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape,
            Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
        ):
            event.ignore()
            return

        super().keyPressEvent(event)

        t = event.text()
        if not t:
            return
        if t == ".":
            self._debounce.start(0)
            return
        if t.isalnum() or t == "_":
            if len(self._word_under_cursor()) >= self.AUTO_TRIGGER_MIN_PREFIX:
                self._debounce.start(self.AUTO_TRIGGER_DELAY_MS)

    def _word_under_cursor(self) -> str:
        tc = self.textCursor()
        tc.select(QTextCursor.SelectionType.WordUnderCursor)
        return tc.selectedText()

    def _insert_completion(self, completion: str) -> None:
        tc = self.textCursor()
        tc.select(QTextCursor.SelectionType.WordUnderCursor)
        tc.removeSelectedText()
        tc.insertText(completion)
        self.setTextCursor(tc)

    def _request_completion_async(self) -> None:
        tc = self.textCursor()
        cursor_pos = tc.position()
        code = self.toPlainText()

        self._req_id += 1
        self._pending_req_id = self._req_id
        self._pending_cursor_pos = cursor_pos

        line = tc.blockNumber() + 1
        col = tc.positionInBlock()
        root = str(self._context_root)
        path_str = str(self._context_path) if self._context_path else None

        def compute() -> list[str]:
            if jedi is None or len(code) > self.JEDI_MAX_CODE_SIZE:
                return _fallback_words(code)
            try:
                proj = jedi.Project(path=root)
                script = jedi.Script(code=code, path=path_str, project=proj)
                return [c.name for c in script.complete(line, col) if getattr(c, "name", None)]
            except Exception:
                return _fallback_words(code)

        job = _CompletionJob(self._req_id, compute)
        job.signals.done.connect(self._on_completion_ready)
        self._pool.start(job)

    def _on_completion_ready(self, request_id: int, items: list[str]) -> None:
        if request_id != self._pending_req_id:
            return
        if self.textCursor().position() != self._pending_cursor_pos:
            return
        if not items:
            return

        prefix = self._word_under_cursor()
        items = sorted(set(map(str, items)))[:250]
        self._comp_model.setStringList(items)
        self._completer.setCompletionPrefix(prefix)

        cr = self.cursorRect()
        cr.setWidth(self._completer.popup().sizeHintForColumn(0) + 30)
        self._completer.complete(cr)


class EditorWindow(QWidget):
    """Standalone editor window."""
    
    TEMPLATE = (
        '"""New Python script."""\n\n\n'
        "def main():\n"
        "    pass\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    def __init__(self, filepath: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.filepath: Optional[Path] = Path(filepath) if filepath else None

        self._build_ui()
        self._bind_shortcuts()

        if (doc := self.editor.document()) is not None:
            doc.modificationChanged.connect(self._on_modified)

        self._set_file(self.filepath, load=bool(self.filepath and self.filepath.exists()))

    def _build_ui(self) -> None:
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Top toolbar
        top = QHBoxLayout()
        top.setSpacing(8)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path/to/file.py (Enter to switch)")
        self.path_edit.setStyleSheet("""
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
        self.path_edit.returnPressed.connect(self._apply_typed_path)
        top.addWidget(self.path_edit, 1)

        top.addWidget(self._icon_btn(QStyle.StandardPixmap.SP_DialogOpenButton, "Open (Ctrl+O)", self.open_dialog))
        top.addWidget(self._icon_btn(QStyle.StandardPixmap.SP_BrowserReload, "Reload (F5)", self.reload))
        top.addWidget(self._icon_btn(QStyle.StandardPixmap.SP_DialogSaveButton, "Save (Ctrl+S)", self.save))
        top.addWidget(self._icon_btn(QStyle.StandardPixmap.SP_DirOpenIcon, "Save As (Ctrl+Shift+S)", self.save_as_dialog))
        layout.addLayout(top)

        # Editor
        self.editor = PythonEditor(self)
        layout.addWidget(self.editor)

        # Status bar
        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("QLabel { color: rgba(220,220,220,160); }")
        bottom.addWidget(self.status)
        bottom.addStretch()

        hint = QLabel("Ctrl+Space Complete · Ctrl+S Save · Ctrl+O Open · F5 Reload")
        hint.setStyleSheet("QLabel { color: rgba(220,220,220,120); }")
        bottom.addWidget(hint)
        layout.addLayout(bottom)

    def _icon_btn(self, sp: QStyle.StandardPixmap, tip: str, fn) -> QPushButton:
        b = QPushButton()
        if (style := self.style()) is not None:
            b.setIcon(style.standardIcon(sp))
        b.setToolTip(tip)
        b.setFixedWidth(36)
        b.setStyleSheet("""
            QPushButton {
                border: 1px solid #333;
                border-radius: 2px;
                background: #252525;
                padding: 4px;
            }
            QPushButton:hover {
                background: #303030;
                border-color: #444;
            }
            QPushButton:pressed {
                background: #202020;
            }
        """)
        b.clicked.connect(fn)
        return b

    def _bind_shortcuts(self) -> None:
        def bind(seq: QKeySequence, fn) -> None:
            a = QAction(self)
            a.setShortcut(seq)
            a.triggered.connect(fn)
            self.addAction(a)

        bind(QKeySequence(QKeySequence.StandardKey.Save), self.save)
        bind(QKeySequence(QKeySequence.StandardKey.SaveAs), self.save_as_dialog)
        bind(QKeySequence(QKeySequence.StandardKey.Open), self.open_dialog)
        bind(QKeySequence(Qt.Key.Key_F5), self.reload)

    def _ask_save_discard_cancel(self, title: str, text: str) -> QMessageBox.StandardButton:
        return QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Save |
            QMessageBox.StandardButton.Discard |
            QMessageBox.StandardButton.Cancel,
        )

    def _with_unsaved_ok(self, cont: Callable[[], None]) -> None:
        doc = self.editor.document()
        if not doc or not doc.isModified():
            cont()
            return

        resp = self._ask_save_discard_cancel(
            "Unsaved changes",
            "You have unsaved changes.\n\nSave before continuing?",
        )
        if resp == QMessageBox.StandardButton.Save:
            self.save()
            if (doc := self.editor.document()) is not None and not doc.isModified():
                cont()
        elif resp == QMessageBox.StandardButton.Discard:
            cont()

    def set_file(self, filepath: str) -> None:
        """Set file from external call."""
        self._with_unsaved_ok(lambda: self._set_file(Path(filepath), load=True))

    def open_dialog(self) -> None:
        self._with_unsaved_ok(self._open_dialog_impl)

    def _open_dialog_impl(self) -> None:
        start_dir = str(self.filepath.parent) if self.filepath else os.getcwd()
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open file", start_dir, "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            self._set_file(Path(file_path), load=True)

    def save(self) -> None:
        if not self.filepath:
            self.save_as_dialog()
            return
        self._save_to(self.filepath)

    def save_as_dialog(self) -> None:
        start_dir = str(self.filepath.parent) if self.filepath else os.getcwd()
        initial = str(self.filepath) if self.filepath else os.path.join(start_dir, "script.py")
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save file as", initial, "Python Files (*.py);;All Files (*)"
        )
        if not file_path:
            return
        self._set_file(Path(file_path), load=False)
        self._save_to(self.filepath)

    def reload(self) -> None:
        if not self.filepath:
            return
        fp = self.filepath
        self._with_unsaved_ok(lambda: self._load_from(fp))

    def _apply_typed_path(self) -> None:
        raw = self.path_edit.text().strip().strip('"')
        if not raw:
            return
        p = Path(raw).expanduser()
        if not p.is_absolute():
            base = self.filepath.parent if self.filepath else Path.cwd()
            p = (base / p).resolve()
        self._with_unsaved_ok(lambda: self._set_file(p, load=p.exists()))

    def _set_file(self, path: Optional[Path], load: bool) -> None:
        self.filepath = path
        self.path_edit.setText(str(self.filepath) if self.filepath else "")
        self.editor.set_context(self.filepath)

        if load and self.filepath:
            self._load_from(self.filepath)
        elif not self.filepath:
            self.editor.setPlainText(self.TEMPLATE)
            if (doc := self.editor.document()) is not None:
                doc.setModified(False)
            self._set_status("new file")
        else:
            if (doc := self.editor.document()) is not None:
                doc.setModified(True)
            self._on_modified(True)

        self._refresh_title()

    def _load_from(self, path: Path) -> None:
        try:
            if path.exists():
                self.editor.setPlainText(path.read_text(encoding="utf-8"))
            elif not self.editor.toPlainText().strip():
                self.editor.setPlainText(self.TEMPLATE)

            if (doc := self.editor.document()) is not None:
                doc.setModified(False)
            self._set_status("loaded")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load:\n{e}")
        self._refresh_title()

    def _save_to(self, path: Optional[Path]) -> None:
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            text = self.editor.toPlainText().replace("\t", "    ")

            sf = QSaveFile(str(path))
            if not sf.open(sf.OpenModeFlag.WriteOnly | sf.OpenModeFlag.Text):
                raise OSError(f"Could not open file for writing: {path}")
            if sf.write(text.encode("utf-8")) == -1:
                raise OSError("Write failed")
            if not sf.commit():
                raise OSError("Commit failed")

            if (doc := self.editor.document()) is not None:
                doc.setModified(False)
            self._set_status("saved")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save:\n{e}")
        self._refresh_title()

    def _on_modified(self, modified: bool) -> None:
        self._set_status("modified" if modified else "")
        self._refresh_title()

    def _refresh_title(self) -> None:
        name = self.filepath.name if self.filepath else "New File"
        star = "*" if (doc := self.editor.document()) is not None and doc.isModified() else ""
        self.setWindowTitle(f"Pythonator Editor - {name}{star}")

    def _set_status(self, msg: str) -> None:
        base = "New File" if not self.filepath else str(self.filepath)
        self.status.setText(f"{base}  {('· ' + msg) if msg else ''}".rstrip())

    def closeEvent(self, event: QCloseEvent) -> None:
        doc = self.editor.document()
        if not doc or not doc.isModified():
            event.accept()
            return

        resp = self._ask_save_discard_cancel(
            "Unsaved changes",
            "You have unsaved changes.\n\nSave before closing?",
        )
        if resp == QMessageBox.StandardButton.Save:
            self.save()
            doc = self.editor.document()
            event.accept() if doc and not doc.isModified() else event.ignore()
        elif resp == QMessageBox.StandardButton.Discard:
            event.accept()
        else:
            event.ignore()
