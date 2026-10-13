import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor
from engine.project_manager import ProjectManager
from gui.main_window import MainWindow
from gui.controller import AppController
from app.activation import run_license_gate

class TaggingApplication:
    def __init__(self, argv):
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('TagPilot')
            except Exception:
                pass
                
        self.app = QApplication(argv)
        
        import os
        from PySide6.QtGui import QIcon
        
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
        icon_path = os.path.join(base_path, "icon.ico")
        if os.path.exists(icon_path):
            self.app.setWindowIcon(QIcon(icon_path))
        
        # Apply modern font
        font = self.app.font()
        font.setFamily("Segoe UI")
        font.setPointSize(10)
        self.app.setFont(font)
        
        # Apply modern dark theme
        self.apply_dark_theme()
        
        # Projects live in a per-user folder (an installed program's folder is read-only)
        from app.paths import default_workspace_dir
        self.project_manager = ProjectManager(workspace_dir=default_workspace_dir())
        self.controller = AppController(self.project_manager)
        self.main_window = MainWindow(self.controller)
        
        # Remote API-key configuration + heavy pipeline modules load in the
        # background: the window opens immediately and the first "Run Pipeline"
        # does not wait for them.
        self._start_background_preload()
        
        
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.main_window.winId())
                value = ctypes.c_int(2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), 4)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), 4)
            except Exception:
                pass
        
    @staticmethod
    def _start_background_preload():
        import threading

        def _preload():
            try:
                from parser.gemini_scheduler import GeminiRequestScheduler
                GeminiRequestScheduler()          # remote config -> credentials (no HTTP clients yet)
            except Exception as e:
                print(f"Failed to pre-initialize GeminiRequestScheduler: {e}")
            try:
                import app.workers                # gemini_extractor, pipeline engine, plugins
                import engine.pipeline_manager
            except Exception as e:
                print(f"Background module preload failed: {e}")

        threading.Thread(target=_preload, name="TagPilotPreload", daemon=True).start()

    def apply_dark_theme(self):
        self.app.setStyle("Fusion")
        dark_palette = QPalette()
        
        # Base colors
        background = QColor(30, 30, 30)
        surface = QColor(45, 45, 45)
        primary = QColor(0, 120, 215) # Windows blue accent
        text = QColor(220, 220, 220)
        text_disabled = QColor(120, 120, 120)

        dark_palette.setColor(QPalette.Window, background)
        dark_palette.setColor(QPalette.WindowText, text)
        dark_palette.setColor(QPalette.Base, surface)
        dark_palette.setColor(QPalette.AlternateBase, background)
        dark_palette.setColor(QPalette.ToolTipBase, text)
        dark_palette.setColor(QPalette.ToolTipText, text)
        dark_palette.setColor(QPalette.Text, text)
        dark_palette.setColor(QPalette.Disabled, QPalette.Text, text_disabled)
        dark_palette.setColor(QPalette.Button, surface)
        dark_palette.setColor(QPalette.ButtonText, text)
        dark_palette.setColor(QPalette.Disabled, QPalette.ButtonText, text_disabled)
        dark_palette.setColor(QPalette.BrightText, Qt.red if hasattr(Qt, 'red') else QColor(255, 0, 0))
        dark_palette.setColor(QPalette.Link, primary)
        dark_palette.setColor(QPalette.Highlight, primary)
        dark_palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        
        self.app.setPalette(dark_palette)
        
        # Polished stylesheet
        stylesheet = """
        QMainWindow, QDialog, QMessageBox {
            background-color: #1e1e1e;
            color: #dcdcdc;
        }
        QToolBar {
            background-color: #2d2d2d;
            border-bottom: 1px solid #3d3d3d;
            padding: 4px;
            spacing: 8px;
        }
        QToolButton {
            color: #dcdcdc;
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 4px;
            padding: 6px;
        }
        QToolButton:hover {
            background-color: #3d3d3d;
            border: 1px solid #4d4d4d;
        }
        QToolButton:pressed {
            background-color: #0078d7;
            border: 1px solid #0078d7;
            color: white;
        }
        QMenuBar {
            background-color: #2d2d2d;
            color: #dcdcdc;
            border-bottom: 1px solid #3d3d3d;
        }
        QMenuBar::item:selected {
            background-color: #3d3d3d;
        }
        QMenu {
            background-color: #2d2d2d;
            color: #dcdcdc;
            border: 1px solid #3d3d3d;
        }
        QMenu::item:selected {
            background-color: #0078d7;
            color: white;
        }
        QSplitter::handle {
            background-color: #3d3d3d;
            margin: 1px;
        }
        QSplitter::handle:horizontal {
            width: 2px;
        }
        QSplitter::handle:vertical {
            height: 2px;
        }
        QTabWidget::pane {
            border: 1px solid #3d3d3d;
            background: #252526;
            border-radius: 4px;
        }
        QTabBar::tab {
            background: #2d2d2d;
            color: #888888;
            padding: 8px 16px;
            border: 1px solid transparent;
            border-bottom: 1px solid #3d3d3d;
        }
        QTabBar::tab:selected {
            color: #dcdcdc;
            background: #252526;
            border: 1px solid #3d3d3d;
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }
        QTabBar::tab:hover:!selected {
            color: #dcdcdc;
            background: #333333;
        }
        QListWidget, QTreeWidget, QTextEdit {
            background-color: #252526;
            border: 1px solid #3d3d3d;
            border-radius: 4px;
            padding: 4px;
        }
        QListWidget::item:hover, QTreeWidget::item:hover {
            background-color: #2a2d2e;
        }
        QListWidget::item:selected, QTreeWidget::item:selected {
            background-color: #04395e;
            color: white;
        }
        QHeaderView::section {
            background-color: #2d2d2d;
            color: #dcdcdc;
            border: none;
            border-right: 1px solid #3d3d3d;
            border-bottom: 1px solid #3d3d3d;
            padding: 4px;
        }
        QScrollBar:vertical {
            background: #1e1e1e;
            width: 14px;
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: #424242;
            min-height: 20px;
            border-radius: 7px;
            margin: 2px;
        }
        QScrollBar::handle:vertical:hover {
            background: #4f4f4f;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar:horizontal {
            background: #1e1e1e;
            height: 14px;
            margin: 0px;
        }
        QScrollBar::handle:horizontal {
            background: #424242;
            min-width: 20px;
            border-radius: 7px;
            margin: 2px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #4f4f4f;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        QLabel {
            color: #dcdcdc;
        }
        """
        self.app.setStyleSheet(stylesheet)
        
    def run(self):
        # The window appears at once; the licence / internet check runs in a
        # thread behind a small modal dialog (nothing can be used until it passes).
        self.main_window.showMaximized()
        self.app.processEvents()
        if not run_license_gate(self.main_window):
            self.main_window._force_quit = True
            self.main_window.close()
            self._end_process(0)
            return 0
        self.main_window.start_runtime_guards()
        code = self.app.exec()
        self._end_process(code)
        return code

    def _end_process(self, code):
        """The event loop has ended (Exit / Quit / last window closed): stop
        background work and end the process, so no lingering network-check
        thread can keep TagPilot running invisibly in the background."""
        import os
        try:
            self.main_window.shutdown_background_work(wait_ms=2000)
        except Exception:
            pass
        print(f"=== TagPilot closed (exit code {code}) ===")
        for s in (sys.stdout, sys.stderr):
            try:
                s.flush()
            except Exception:
                pass
        os._exit(int(code or 0))
