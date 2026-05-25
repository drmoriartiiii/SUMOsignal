"""
main.py — Application entry point.

Usage:
    python main.py

Prerequisites:
    1. SUMO installed and SUMO_HOME set (or resolvable from default paths in config.py)
    2. sumo_net/intersection.net.xml generated: run sumo_net/generate_net.bat
    3. pip install -r requirements.txt
"""

import os
import sys

# ── Qt platform plugin path fix (Windows) ─────────────────────────────────────
# Must be done BEFORE any PyQt5/Qt import so QApplication can locate qwindows.dll.
if sys.platform == "win32":
    try:
        import PyQt5 as _pyqt5
        _qt_plugins = os.path.join(os.path.dirname(_pyqt5.__file__), "Qt5", "plugins")
        if os.path.isdir(_qt_plugins):
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", _qt_plugins)
    except ImportError:
        pass
# ──────────────────────────────────────────────────────────────────────────────

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from gui.main_window import MainWindow


def main() -> None:
    # Enable HiDPI scaling before QApplication is created
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")          # consistent look on Windows / Linux / macOS
    app.setApplicationName("SUMO 固定信号配时系统")
    app.setApplicationVersion("1.0")

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
