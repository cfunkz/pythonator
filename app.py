"""
Application entry point with dark theme.
"""
"""
Application entry point with dark theme.
"""
import os
import sys

# 🔒 GUARANTEE local imports work (main_window.py in same folder)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from PyQt6.QtGui import QIcon, QColor, QPalette, QFont
from PyQt6.QtWidgets import QApplication, QStyleFactory

from main_window import MainWindow


def setup_dark_palette() -> QPalette:
    """Create a dark theme palette."""
    pal = QPalette()
    
    # Base colors
    dark = QColor(24, 24, 24)
    darker = QColor(18, 18, 18)
    light = QColor(220, 220, 220)
    mid = QColor(35, 35, 35)
    accent = QColor(70, 130, 220)
    
    # Window
    pal.setColor(QPalette.ColorRole.Window, dark)
    pal.setColor(QPalette.ColorRole.WindowText, light)
    
    # Base (input backgrounds)
    pal.setColor(QPalette.ColorRole.Base, darker)
    pal.setColor(QPalette.ColorRole.AlternateBase, dark)
    pal.setColor(QPalette.ColorRole.Text, light)
    
    # Buttons
    pal.setColor(QPalette.ColorRole.Button, mid)
    pal.setColor(QPalette.ColorRole.ButtonText, light)
    
    # Selection
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    
    # Tooltips
    pal.setColor(QPalette.ColorRole.ToolTipBase, dark)
    pal.setColor(QPalette.ColorRole.ToolTipText, light)
    
    # Links
    pal.setColor(QPalette.ColorRole.Link, accent)
    pal.setColor(QPalette.ColorRole.LinkVisited, QColor(180, 130, 220))
    
    # Disabled
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(128, 128, 128))
    
    return pal


def main() -> int:
    """Run the application."""
    app = QApplication(sys.argv)
    
    # Style and theme
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(setup_dark_palette())
    
    # Application font
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)
    
    # Global stylesheet for consistent look
    app.setStyleSheet("""
        QToolTip {
            background-color: #252525;
            color: #ddd;
            border: 1px solid #444;
            padding: 4px;
            border-radius: 2px;
        }
        QScrollBar:vertical {
            background: #1a1a1a;
            width: 12px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #404040;
            min-height: 20px;
            border-radius: 4px;
            margin: 2px;
        }
        QScrollBar::handle:vertical:hover {
            background: #505050;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar:horizontal {
            background: #1a1a1a;
            height: 12px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #404040;
            min-width: 20px;
            border-radius: 4px;
            margin: 2px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #505050;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }
        QMessageBox {
            background-color: #1a1a1a;
        }
        QMessageBox QLabel {
            color: #ddd;
        }
    """)
    
    # Try to set window icon
    try:
        app.setWindowIcon(QIcon("icon.ico"))
    except Exception:
        pass
    
    # Create and show main window
    window = MainWindow()
    try:
        window.setWindowIcon(QIcon("icon.ico"))
    except Exception:
        pass
    window.show()
    
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
