"""
Icon management for Pythonator.

Provides consistent icons across the application using QStyle standard icons
with fallback text labels. Replaces inconsistent emoji usage.
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Optional

from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QPen
from PyQt6.QtCore import Qt, QSize, QRect
from PyQt6.QtWidgets import QStyle, QApplication, QPushButton


class Icon(Enum):
    """Application icons with standard pixmap mappings."""
    # Actions
    START = auto()
    STOP = auto()
    RESTART = auto()
    ADD = auto()
    DELETE = auto()
    
    # Navigation
    OLDER = auto()
    LIVE = auto()
    SCROLL_END = auto()
    
    # Files
    OPEN_FILE = auto()
    SAVE = auto()
    BROWSE = auto()
    EDIT = auto()
    
    # Search
    SEARCH = auto()
    CLEAR = auto()
    
    # Status
    RUNNING = auto()
    STOPPED = auto()
    HISTORY = auto()
    
    # Editor
    RELOAD = auto()
    SAVE_AS = auto()
    DETECT = auto()


# Mapping of icons to QStyle.StandardPixmap where available
_STYLE_MAP: dict[Icon, QStyle.StandardPixmap] = {
    Icon.OPEN_FILE: QStyle.StandardPixmap.SP_FileIcon,
    Icon.SAVE: QStyle.StandardPixmap.SP_DialogSaveButton,
    Icon.SAVE_AS: QStyle.StandardPixmap.SP_DirOpenIcon,
    Icon.BROWSE: QStyle.StandardPixmap.SP_DirIcon,
    Icon.RELOAD: QStyle.StandardPixmap.SP_BrowserReload,
    Icon.DELETE: QStyle.StandardPixmap.SP_DialogCloseButton,
    Icon.SEARCH: QStyle.StandardPixmap.SP_FileDialogContentsView,
    Icon.CLEAR: QStyle.StandardPixmap.SP_DialogCancelButton,
}


def _create_text_icon(text: str, size: int = 16, color: str = "#ddd") -> QIcon:
    """Create an icon from text/symbol."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    font = QFont()
    font.setPixelSize(int(size * 0.75))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(color))
    
    rect = QRect(0, 0, size, size)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    
    return QIcon(pixmap)


def _create_shape_icon(shape: str, size: int = 16, color: str = "#ddd") -> QIcon:
    """Create an icon with a simple shape."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    
    margin = size // 4
    
    if shape == "play":
        # Triangle pointing right
        points = [
            (margin, margin),
            (size - margin, size // 2),
            (margin, size - margin)
        ]
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        
    elif shape == "stop":
        # Square
        painter.drawRect(margin, margin, size - 2*margin, size - 2*margin)
        
    elif shape == "up":
        # Triangle pointing up
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        points = [
            (size // 2, margin),
            (size - margin, size - margin),
            (margin, size - margin)
        ]
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        
    elif shape == "down":
        # Triangle pointing down
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        points = [
            (margin, margin),
            (size - margin, margin),
            (size // 2, size - margin)
        ]
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        
    elif shape == "circle":
        painter.drawEllipse(margin, margin, size - 2*margin, size - 2*margin)
        
    painter.end()
    return QIcon(pixmap)


# Custom icon creators for icons without QStyle equivalents
_CUSTOM_ICONS: dict[Icon, tuple[str, str]] = {
    # (type, value) - type is "text" or "shape"
    Icon.START: ("shape", "play"),
    Icon.STOP: ("shape", "stop"),
    Icon.RESTART: ("text", "↻"),
    Icon.ADD: ("text", "+"),
    Icon.OLDER: ("shape", "up"),
    Icon.LIVE: ("shape", "down"),
    Icon.SCROLL_END: ("text", "↓"),
    Icon.EDIT: ("text", "✎"),
    Icon.RUNNING: ("shape", "circle"),
    Icon.STOPPED: ("text", "○"),
    Icon.HISTORY: ("text", "⧖"),
    Icon.DETECT: ("text", "⌕"),
}


class IconProvider:
    """Provides consistent icons throughout the application."""
    
    _cache: dict[Icon, QIcon] = {}
    
    @classmethod
    def get(cls, icon: Icon, size: int = 16) -> QIcon:
        """Get an icon, using cache when possible."""
        if icon in cls._cache:
            return cls._cache[icon]
        
        # Try QStyle first
        if icon in _STYLE_MAP:
            app = QApplication.instance()
            if app and (style := app.style()):
                qicon = style.standardIcon(_STYLE_MAP[icon])
                if not qicon.isNull():
                    cls._cache[icon] = qicon
                    return qicon
        
        # Fall back to custom icons
        if icon in _CUSTOM_ICONS:
            icon_type, value = _CUSTOM_ICONS[icon]
            if icon_type == "text":
                qicon = _create_text_icon(value, size)
            else:
                qicon = _create_shape_icon(value, size)
            cls._cache[icon] = qicon
            return qicon
        
        # Default empty icon
        return QIcon()
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear the icon cache."""
        cls._cache.clear()


def icon_button(
    icon: Icon,
    text: str = "",
    tooltip: str = "",
    size: Optional[tuple[int, int]] = None,
    icon_size: int = 16
) -> QPushButton:
    """Create a styled button with an icon."""
    btn = QPushButton(text)
    btn.setIcon(IconProvider.get(icon, icon_size))
    
    if tooltip:
        btn.setToolTip(tooltip)
    
    if size:
        btn.setFixedSize(*size)
    elif not text:
        btn.setFixedWidth(28)
    
    return btn
